import time
import logging
import re
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime
from pydantic import BaseModel, Field

import pymongo
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError, PyMongoError
from bson import ObjectId

from app.config.settings import settings
from app.db.document_store import get_document_store_config, redact_uri

logger = logging.getLogger(__name__)

# Strictly disallowed operations and commands
PROHIBITED_OPERATIONS = {
    "insert", "insertone", "insertmany",
    "update", "updateone", "updatemany", "replaceone",
    "delete", "deleteone", "deletemany",
    "drop", "dropdatabase", "dropcollection",
    "createindex", "dropindex", "dropindexes",
    "renamecollection", "reindex",
    "eval", "copydb", "clonedatabase",
    "shutdown", "repairdatabase"
}

# Aggregate stages that perform writes or export data
PROHIBITED_AGGREGATE_STAGES = {
    "$out", "$merge", "$indexstats", "$plancachestats"
}

# Allowed read operations
ALLOWED_OPERATIONS = {"find", "countdocuments", "count", "distinct", "aggregate"}

class MongoTestResult(BaseModel):
    status: str = Field(
        ...,
        description="Execution status: 'success', 'unavailable', 'rejected', or 'error'."
    )
    database: str = Field(default="inspectdb", description="Database name.")
    collection: str = Field(default="inspection_reports", description="Collection name.")
    operation: str = Field(default="find", description="Executed read operation.")
    documents_matched: int = Field(default=0, description="Total number of documents matching the query.")
    execution_time_ms: float = Field(default=0.0, description="Query execution duration in milliseconds.")
    sample_results: List[Dict[str, Any]] = Field(default_factory=list, description="Sample of returned documents (capped at MAX_QUERY_RESULTS).")
    total_returned: int = Field(default=0, description="Count of sample documents included in response.")
    max_results_limit: int = Field(default=20, description="Maximum result cap configured.")
    reason: Optional[str] = Field(default=None, description="Detailed explanation if unavailable or rejected.")
    error_message: Optional[str] = Field(default=None, description="Technical error message if execution failed.")

class MongoExecutor:
    """
    Safe read-only execution engine for testing MongoDB queries against
    the local MongoDB development database, or Amazon DocumentDB when STORAGE_MODE=documentdb.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        database: Optional[str] = None,
        collection: Optional[str] = None,
        max_results: Optional[int] = None,
        timeout_ms: Optional[int] = None
    ):
        cfg = get_document_store_config()
        self.engine_label = cfg.engine_label
        self.uri = uri or cfg.uri
        self.database_name = database or cfg.database
        self.collection_name = collection or cfg.collection
        self.max_results = max_results or getattr(settings, "MAX_QUERY_RESULTS", 20)
        self.timeout_ms = timeout_ms or cfg.timeout_ms
        self._client_kwargs = dict(cfg.client_kwargs)
        self._client: Optional[MongoClient] = None

    def _get_client(self) -> MongoClient:
        if self._client is None:
            self._client = MongoClient(
                self.uri,
                serverSelectionTimeoutMS=self.timeout_ms,
                connectTimeoutMS=self.timeout_ms,
                **self._client_kwargs
            )
        return self._client

    def is_available(self) -> bool:
        """Lightweight connectivity probe for local MongoDB server."""
        try:
            client = self._get_client()
            client.admin.command("ping")
            return True
        except Exception:
            return False

    def validate_safety(
        self,
        query: Union[Dict[str, Any], List[Dict[str, Any]]],
        operation: str = "find"
    ) -> Tuple[bool, Optional[str]]:
        """
        Enforces strict read-only safety rules on the operation and query AST.
        """
        op_norm = operation.strip().lower()

        if op_norm in PROHIBITED_OPERATIONS:
            return False, f"Prohibited write/admin operation '{operation}'. Only read-only operations are permitted."

        if op_norm not in ALLOWED_OPERATIONS:
            return False, f"Unsupported operation '{operation}'. Allowed read operations: {', '.join(sorted(ALLOWED_OPERATIONS))}."

        # AST Inspection for JavaScript execution operators
        def _check_dangerous_operators(node: Any) -> Optional[str]:
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ["$where", "$accumulator", "$function"]:
                        return f"Forbidden arbitrary code execution operator '{k}'."
                    err = _check_dangerous_operators(v)
                    if err:
                        return err
            elif isinstance(node, list):
                for item in node:
                    err = _check_dangerous_operators(item)
                    if err:
                        return err
            return None

        danger_err = _check_dangerous_operators(query)
        if danger_err:
            return False, danger_err

        # If aggregation, verify all stages are read-only
        if op_norm == "aggregate":
            if not isinstance(query, list):
                return False, "Aggregation pipeline must be a list of stage objects."
            for stage in query:
                if isinstance(stage, dict):
                    for stage_name in stage.keys():
                        if stage_name.lower() in PROHIBITED_AGGREGATE_STAGES:
                            return False, f"Prohibited write aggregation stage '{stage_name}'."

        return True, None

    def _clean_doc(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Converts ObjectId and datetime instances to clean JSON-serializable structures."""
        def _clean_item(item: Any) -> Any:
            if isinstance(item, dict):
                clean_d = {}
                for k, v in item.items():
                    if k == "_id":
                        if "id" not in item:
                            clean_d["id"] = str(v)
                    else:
                        clean_d[k] = _clean_item(v)
                return clean_d
            elif isinstance(item, list):
                return [_clean_item(x) for x in item]
            elif isinstance(item, ObjectId):
                return str(item)
            elif isinstance(item, datetime):
                return item.isoformat() + "Z"
            return item

        return _clean_item(doc)

    async def execute_test(
        self,
        query: Union[Dict[str, Any], List[Dict[str, Any]]],
        operation: str = "find",
        collection_name: Optional[str] = None,
        field_name: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> MongoTestResult:
        """
        Executes a validated read-only query against the local MongoDB instance.
        Enforces user ownership scoping so queries cannot access other users' documents.
        If MongoDB is unavailable, returns a graceful unavailable result.
        """
        coll_name = collection_name or self.collection_name
        op_norm = operation.strip().lower()

        # 1. Safety check
        is_safe, reject_reason = self.validate_safety(query, operation=op_norm)
        if not is_safe:
            return MongoTestResult(
                status="rejected",
                database=self.database_name,
                collection=coll_name,
                operation=operation,
                reason=reject_reason,
                max_results_limit=self.max_results
            )

        # 2. Check local MongoDB connectivity
        try:
            client = self._get_client()
            client.admin.command("ping")
        except (ConnectionFailure, ServerSelectionTimeoutError, PyMongoError, Exception) as conn_err:
            logger.debug(f"Local MongoDB connection test skipped (server offline): {conn_err}")
            return MongoTestResult(
                status="unavailable",
                database=self.database_name,
                collection=coll_name,
                operation=operation,
                reason=(
                    f"Could not connect to {self.engine_label} at '{redact_uri(self.uri)}'. "
                    + ("Check the DocumentDB cluster status, security group, and DOCUMENTDB_* settings."
                       if self.engine_label == "Amazon DocumentDB"
                       else "Ensure mongod service is running locally for live query testing.")
                ),
                max_results_limit=self.max_results
            )

        # 3. Execute read operation with user_id scoping
        try:
            db = client[self.database_name]
            coll = db[coll_name]

            start_time = time.perf_counter()
            sample_docs: List[Dict[str, Any]] = []
            matched_count: int = 0

            if op_norm == "find":
                filter_dict = query if isinstance(query, dict) else {}
                if user_id is not None:
                    filter_dict = {"$and": [{"user_id": user_id}, filter_dict]} if filter_dict else {"user_id": user_id}
                matched_count = coll.count_documents(filter_dict)
                cursor = coll.find(filter_dict).limit(self.max_results)
                sample_docs = [self._clean_doc(d) for d in cursor]

            elif op_norm in ["countdocuments", "count"]:
                filter_dict = query if isinstance(query, dict) else {}
                if user_id is not None:
                    filter_dict = {"$and": [{"user_id": user_id}, filter_dict]} if filter_dict else {"user_id": user_id}
                matched_count = coll.count_documents(filter_dict)
                sample_docs = [{"matched_count": matched_count}]

            elif op_norm == "distinct":
                filter_dict = query if isinstance(query, dict) else {}
                if user_id is not None:
                    filter_dict = {"$and": [{"user_id": user_id}, filter_dict]} if filter_dict else {"user_id": user_id}
                target_field = field_name or "category"
                distinct_vals = coll.distinct(target_field, filter=filter_dict)
                matched_count = len(distinct_vals)
                sample_docs = [{"field": target_field, "distinct_values": distinct_vals[:self.max_results]}]

            elif op_norm == "aggregate":
                pipeline = query if isinstance(query, list) else []
                scoped_pipeline = []
                if user_id is not None:
                    scoped_pipeline.append({"$match": {"user_id": user_id}})
                scoped_pipeline.extend(pipeline)
                scoped_pipeline.append({"$limit": self.max_results})
                cursor = coll.aggregate(scoped_pipeline)
                sample_docs = [self._clean_doc(d) for d in cursor]
                matched_count = len(sample_docs)

            exec_time_ms = round((time.perf_counter() - start_time) * 1000, 2)

            return MongoTestResult(
                status="success",
                database=self.database_name,
                collection=coll_name,
                operation=operation,
                documents_matched=matched_count,
                execution_time_ms=exec_time_ms,
                sample_results=sample_docs,
                total_returned=len(sample_docs),
                max_results_limit=self.max_results
            )

        except PyMongoError as pe:
            logger.warning(f"MongoDB query execution failed: {pe}")
            return MongoTestResult(
                status="error",
                database=self.database_name,
                collection=coll_name,
                operation=operation,
                error_message=str(pe),
                reason=f"MongoDB returned an execution error: {str(pe)}",
                max_results_limit=self.max_results
            )
        except Exception as e:
            logger.error(f"Unexpected error during MongoDB execution test: {e}")
            return MongoTestResult(
                status="error",
                database=self.database_name,
                collection=coll_name,
                operation=operation,
                error_message=str(e),
                reason=f"Execution error: {str(e)}",
                max_results_limit=self.max_results
            )

# Global singleton instance
mongo_executor = MongoExecutor()
