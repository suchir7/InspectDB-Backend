import copy
import logging
import time
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set

import pymongo
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError, PyMongoError, DuplicateKeyError, OperationFailure
from bson import ObjectId

from app.repositories.base import BaseInspectionRepository
from app.schemas.report import SchemaFieldInfo, SchemaOverviewResponse
from app.db.document_store import DocumentStoreConfig, get_document_store_config

logger = logging.getLogger(__name__)

# Holds atomic sequence documents used to allocate human-readable report IDs
COUNTERS_COLLECTION = "counters"
REPORT_ID_COUNTER = "inspection_report_id"
MAX_ID_ALLOCATION_ATTEMPTS = 5

# DocumentDB builds one index per collection at a time (40333); concurrent workers can also
# collide on the same unique index build (11000). Both clear once the other build finishes.
RETRYABLE_INDEX_ERROR_CODES = {40333, 11000}
MAX_INDEX_BUILD_ATTEMPTS = 10
INDEX_BUILD_RETRY_SECONDS = 1.0

class MongoDBInspectionRepository(BaseInspectionRepository):
    """
    MongoDB repository implementing the BaseInspectionRepository interface.
    Connects to local MongoDB or an Amazon DocumentDB cluster (STORAGE_MODE=documentdb),
    providing full CRUD, dynamic schema discovery, nested document query execution,
    and safe ObjectId conversion.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        database_name: Optional[str] = None,
        collection_name: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        config: Optional[DocumentStoreConfig] = None
    ):
        cfg = config or get_document_store_config()
        self.mode = cfg.mode
        self.engine_label = cfg.engine_label
        self.uri = uri or cfg.uri
        self.database_name = database_name or cfg.database
        self.collection_name = collection_name or cfg.collection
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

    def _get_collection(self):
        client = self._get_client()
        return client[self.database_name][self.collection_name]

    def is_available(self) -> bool:
        """Probes the MongoDB server with a lightweight ping command."""
        try:
            client = self._get_client()
            client.admin.command("ping")
            return True
        except Exception as e:
            logger.debug(f"MongoDB health check probe failed: {e}")
            return False

    def ensure_indexes(self) -> None:
        """Creates the indexes the application relies on. Safe to call on every startup."""
        collection = self._get_collection()
        index_specs = [
            ([("id", pymongo.ASCENDING)], {"unique": True}),
            ([("user_id", pymongo.ASCENDING), ("inspection_date", pymongo.DESCENDING)], {}),
            ([("category", pymongo.ASCENDING), ("inspection_date", pymongo.DESCENDING)], {}),
            ([("status", pymongo.ASCENDING)], {}),
            ([("overall_severity", pymongo.ASCENDING)], {}),
            ([("findings.severity", pymongo.ASCENDING)], {}),
        ]
        for keys, options in index_specs:
            for attempt in range(MAX_INDEX_BUILD_ATTEMPTS):
                try:
                    collection.create_index(keys, **options)
                    break
                except ConnectionFailure:
                    raise
                except OperationFailure as e:
                    if e.code in RETRYABLE_INDEX_ERROR_CODES and attempt < MAX_INDEX_BUILD_ATTEMPTS - 1:
                        time.sleep(INDEX_BUILD_RETRY_SECONDS)
                        continue
                    logger.warning(f"Could not create index {keys} on {self.collection_name}: {e}")
                    break
                except PyMongoError as e:
                    logger.warning(f"Could not create index {keys} on {self.collection_name}: {e}")
                    break

    def _next_report_id(self) -> str:
        """Allocates a unique, human-readable report ID from an atomic counter document."""
        counters = self._get_client()[self.database_name][COUNTERS_COLLECTION]
        counter = counters.find_one_and_update(
            {"_id": REPORT_ID_COUNTER},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=pymongo.ReturnDocument.AFTER
        )
        return f"RPT-{datetime.utcnow().year}-{int(counter['seq']):04d}"

    def _clean_doc(self, doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Converts ObjectId fields to string and formats response for JSON serialization."""
        if doc is None:
            return None
        cleaned = dict(doc)
        if "_id" in cleaned:
            # If id is missing, use string ObjectId
            if "id" not in cleaned or not cleaned["id"]:
                cleaned["id"] = str(cleaned["_id"])
            del cleaned["_id"]
        
        # Deep clean any nested ObjectIds or datetime objects
        def _clean_nested(item: Any) -> Any:
            if isinstance(item, dict):
                return {k: _clean_nested(v) for k, v in item.items() if k != "_id"}
            elif isinstance(item, list):
                return [_clean_nested(v) for v in item]
            elif isinstance(item, ObjectId):
                return str(item)
            elif isinstance(item, datetime):
                return item.isoformat() + "Z"
            return item

        return _clean_nested(cleaned)

    async def get_all(
        self,
        user_id: Optional[str] = None,
        search: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        sort_by: str = "inspection_date",
        sort_order: str = "desc",
        page: int = 1,
        limit: int = 10
    ) -> Tuple[List[Dict[str, Any]], int]:
        collection = self._get_collection()
        query_filter: Dict[str, Any] = {}

        if user_id is not None:
            query_filter["user_id"] = user_id

        if search:
            s_regex = {"$regex": re.escape(search.strip()), "$options": "i"}
            search_clause = [
                {"id": s_regex},
                {"title": s_regex},
                {"inspector_name": s_regex},
                {"location": s_regex},
                {"description": s_regex}
            ]
            if query_filter:
                query_filter = {"$and": [dict(query_filter), {"$or": search_clause}]}
            else:
                query_filter["$or"] = search_clause

        if category and category.lower() != "all":
            query_filter["category"] = {"$regex": f"^{re.escape(category)}$", "$options": "i"}

        if status and status.lower() != "all":
            query_filter["status"] = status.lower()

        if severity and severity.lower() != "all":
            query_filter["overall_severity"] = severity.lower()

        total = collection.count_documents(query_filter)
        direction = pymongo.DESCENDING if sort_order.lower() == "desc" else pymongo.ASCENDING

        skip = max(0, (page - 1) * limit)
        cursor = collection.find(query_filter).sort(sort_by, direction).skip(skip).limit(limit)

        results = [self._clean_doc(doc) for doc in cursor]
        return results, total

    async def get_by_id(self, report_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        collection = self._get_collection()
        doc_filter: Dict[str, Any] = {"id": report_id}
        if user_id is not None:
            doc_filter["user_id"] = user_id
        
        doc = collection.find_one(doc_filter)
        if not doc and ObjectId.is_valid(report_id):
            alt_filter: Dict[str, Any] = {"_id": ObjectId(report_id)}
            if user_id is not None:
                alt_filter["user_id"] = user_id
            doc = collection.find_one(alt_filter)
        return self._clean_doc(doc)

    async def create(self, report_data: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
        collection = self._get_collection()
        report = copy.deepcopy(report_data)
        if user_id is not None:
            report["user_id"] = user_id

        now = datetime.utcnow().isoformat() + "Z"
        report["created_at"] = now
        report["updated_at"] = now
        report["is_sample"] = False

        # IDs come from a global counter so they never collide across users or after deletes.
        # Retry if an allocated ID is already taken (e.g. by seeded sample documents).
        auto_id = not report.get("id")
        for attempt in range(MAX_ID_ALLOCATION_ATTEMPTS):
            if auto_id:
                report["id"] = self._next_report_id()
            try:
                result = collection.insert_one(report)
                break
            except DuplicateKeyError:
                report.pop("_id", None)
                if not auto_id or attempt == MAX_ID_ALLOCATION_ATTEMPTS - 1:
                    raise

        report["_id"] = str(result.inserted_id)
        return self._clean_doc(report)

    async def update(self, report_id: str, update_data: Dict[str, Any], user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        collection = self._get_collection()
        clean_update = {k: v for k, v in update_data.items() if v is not None and k not in ["_id", "id", "user_id", "created_at"]}
        clean_update["updated_at"] = datetime.utcnow().isoformat() + "Z"

        filter_dict: Dict[str, Any] = {"id": report_id}
        if user_id is not None:
            filter_dict["user_id"] = user_id

        result = collection.find_one_and_update(
            filter_dict,
            {"$set": clean_update},
            return_document=pymongo.ReturnDocument.AFTER
        )
        return self._clean_doc(result)

    async def delete(self, report_id: str, user_id: Optional[str] = None) -> bool:
        collection = self._get_collection()
        filter_dict: Dict[str, Any] = {"id": report_id}
        if user_id is not None:
            filter_dict["user_id"] = user_id
        res = collection.delete_one(filter_dict)
        return res.deleted_count > 0

    async def query_nested(
        self,
        conditions: List[Dict[str, Any]],
        match_type: str = "and",
        limit: int = 20,
        user_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], float]:
        """Translates condition list into MongoDB query filter and executes it with user ownership scoping."""
        start_time = time.perf_counter()
        mongo_filter = self._build_mongo_filter(conditions, match_type)
        
        final_filter = mongo_filter
        if user_id is not None:
            if mongo_filter:
                final_filter = {"$and": [{"user_id": user_id}, mongo_filter]}
            else:
                final_filter = {"user_id": user_id}

        collection = self._get_collection()
        cursor = collection.find(final_filter).limit(limit)
        results = [self._clean_doc(d) for d in cursor]
        
        exec_ms = round((time.perf_counter() - start_time) * 1000, 2)
        ast_obj = {"match_type": match_type, "conditions": conditions}
        return results, mongo_filter, ast_obj, exec_ms

    async def query_raw(
        self,
        raw_filter: Dict[str, Any],
        limit: int = 20,
        user_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], float]:
        start_time = time.perf_counter()
        collection = self._get_collection()
        
        final_filter = raw_filter
        if user_id is not None:
            if raw_filter:
                final_filter = {"$and": [{"user_id": user_id}, raw_filter]}
            else:
                final_filter = {"user_id": user_id}

        cursor = collection.find(final_filter).limit(limit)
        results = [self._clean_doc(d) for d in cursor]
        exec_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return results, exec_ms

    def _build_mongo_filter(self, conditions: List[Dict[str, Any]], match_type: str = "and") -> Dict[str, Any]:
        if not conditions:
            return {}

        elem_match_groups: Dict[str, List[Dict[str, Any]]] = {}
        standard_filters: List[Dict[str, Any]] = []

        for c in conditions:
            field = c.get("field", "")
            op = c.get("operator", "equals")
            val = c.get("value")
            val_type = c.get("value_type", "string")

            # Check if targeting nested array
            if field.startswith("findings.") and len(field.split(".")) > 1:
                subfield = ".".join(field.split(".")[1:])
                elem_match_groups.setdefault("findings", []).append({
                    "subfield": subfield,
                    "operator": op,
                    "value": val,
                    "value_type": val_type
                })
            else:
                standard_filters.append(self._condition_to_mongo(field, op, val, val_type))

        all_clauses = []
        for array_field, sub_conds in elem_match_groups.items():
            if len(sub_conds) == 1:
                sub = sub_conds[0]
                cond_dict = self._condition_to_mongo(sub["subfield"], sub["operator"], sub["value"], sub["value_type"])
                all_clauses.append({array_field: {"$elemMatch": cond_dict}})
            else:
                combined_elem = {}
                for sub in sub_conds:
                    cd = self._condition_to_mongo(sub["subfield"], sub["operator"], sub["value"], sub["value_type"])
                    combined_elem.update(cd)
                all_clauses.append({array_field: {"$elemMatch": combined_elem}})

        all_clauses.extend(standard_filters)

        if not all_clauses:
            return {}

        if match_type == "or":
            return {"$or": all_clauses} if len(all_clauses) > 1 else all_clauses[0]
        elif match_type == "nor":
            return {"$nor": all_clauses}
        else:
            return {"$and": all_clauses} if len(all_clauses) > 1 else all_clauses[0]

    def _condition_to_mongo(self, field: str, op: str, val: Any, val_type: str) -> Dict[str, Any]:
        if val_type == "number" and val is not None:
            try:
                val = float(val) if "." in str(val) else int(val)
            except (ValueError, TypeError):
                pass
        elif val_type == "boolean" and val is not None:
            val = str(val).lower() in ["true", "1", "yes"]

        if op == "equals":
            return {field: val}
        elif op == "not_equals":
            return {field: {"$ne": val}}
        elif op == "greater_than":
            return {field: {"$gt": val}}
        elif op == "greater_than_or_equal":
            return {field: {"$gte": val}}
        elif op == "less_than":
            return {field: {"$lt": val}}
        elif op == "less_than_or_equal":
            return {field: {"$lte": val}}
        elif op in ["contains", "starts_with", "ends_with"]:
            regex_str = str(val)
            if op == "starts_with":
                regex_str = f"^{re.escape(str(val))}"
            elif op == "ends_with":
                regex_str = f"{re.escape(str(val))}$"
            return {field: {"$regex": regex_str, "$options": "i"}}
        elif op == "regex":
            return {field: {"$regex": str(val), "$options": "i"}}
        elif op == "in":
            items = val if isinstance(val, list) else [v.strip() for v in str(val).split(",") if v.strip()]
            return {field: {"$in": items}}
        elif op == "not_in":
            items = val if isinstance(val, list) else [v.strip() for v in str(val).split(",") if v.strip()]
            return {field: {"$nin": items}}
        elif op == "exists":
            return {field: {"$exists": bool(val)}}
        elif op == "is_true":
            return {field: True}
        elif op == "is_false":
            return {field: False}
        elif op == "array_size":
            try:
                return {field: {"$size": int(val)}}
            except (ValueError, TypeError):
                return {field: {"$size": 1}}

        return {field: val}

    def get_schema_overview(self, user_id: Optional[str] = None) -> SchemaOverviewResponse:
        """Scans documents in the MongoDB collection owned by user_id to discover schema paths and distribution."""
        collection = self._get_collection()
        doc_filter = {"user_id": user_id} if user_id is not None else {}
        reports = [self._clean_doc(d) for d in collection.find(doc_filter).limit(100)]
        total_docs = collection.count_documents(doc_filter)

        discovered: Dict[str, Dict[str, Any]] = {}

        def _traverse(obj: Any, current_path: str, is_inside_array: bool = False):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    sub_path = f"{current_path}.{k}" if current_path else k
                    is_arr = isinstance(v, list)
                    is_nest = isinstance(v, dict) or is_arr
                    
                    if sub_path not in discovered:
                        discovered[sub_path] = {
                            "types": set(),
                            "count": 0,
                            "is_array": is_arr,
                            "is_nested": is_nest or is_inside_array,
                            "example": v if not is_nest else None
                        }
                    
                    t_str = self._infer_val_type(v)
                    discovered[sub_path]["types"].add(t_str)
                    discovered[sub_path]["count"] += 1
                    if not is_nest and discovered[sub_path]["example"] is None:
                        discovered[sub_path]["example"] = v

                    if isinstance(v, dict):
                        _traverse(v, sub_path, is_inside_array)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                _traverse(item, sub_path, is_inside_array=True)
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict):
                        _traverse(item, current_path, is_inside_array=True)

        for r in reports:
            _traverse(r, "")

        fields: List[SchemaFieldInfo] = []
        nested_count = 0
        array_count = 0
        var_groups = set()

        for path, info in sorted(discovered.items()):
            is_arr = info["is_array"] or "[]" in path
            is_nest = info["is_nested"] or "." in path
            is_var = info["count"] < total_docs

            if is_nest:
                nested_count += 1
            if is_arr:
                array_count += 1

            if path.startswith("dynamic_attributes."):
                parts = path.split(".")
                if len(parts) > 1:
                    var_groups.add(parts[1])

            inferred_type = next(iter(info["types"])) if info["types"] else "string"
            if path in ["status", "overall_severity", "category", "findings.severity", "findings.category", "findings.issues.status", "findings.issues.severity"]:
                inferred_type = "categorical"

            display = path
            if is_arr and not display.endswith("[]"):
                display += "[]"

            fields.append(SchemaFieldInfo(
                path=path,
                display_name=display,
                field_type=inferred_type,
                is_array=is_arr,
                is_nested=is_nest,
                is_variable_schema=is_var,
                occurrence_count=info["count"],
                total_documents=total_docs,
                example_value=info["example"] if not isinstance(info["example"], (dict, list)) else None
            ))

        return SchemaOverviewResponse(
            total_documents=total_docs,
            nested_fields_count=nested_count,
            arrays_count=array_count,
            fields=fields,
            variable_schema_groups=sorted(list(var_groups))
        )

    def _infer_val_type(self, val: Any) -> str:
        if isinstance(val, bool):
            return "boolean"
        elif isinstance(val, (int, float)):
            return "number"
        elif isinstance(val, list):
            return "array"
        elif isinstance(val, dict):
            return "object"
        elif isinstance(val, str):
            if re.match(r"^\d{4}-\d{2}-\d{2}", val):
                return "date"
            return "string"
        return "string"

    async def get_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        collection = self._get_collection()
        doc_filter = {"user_id": user_id} if user_id is not None else {}
        total = collection.count_documents(doc_filter)
        reports = [self._clean_doc(d) for d in collection.find(doc_filter)]

        high_severity_findings = 0
        status_dist: Dict[str, int] = {}
        category_dist: Dict[str, int] = {}
        severity_dist: Dict[str, int] = {}
        requiring_attention = 0
        completed = 0

        for r in reports:
            st = r.get("status", "draft")
            status_dist[st] = status_dist.get(st, 0) + 1
            if st in ["action_required", "failed", "in_review"]:
                requiring_attention += 1
            if st == "passed":
                completed += 1

            cat = r.get("category", "General")
            category_dist[cat] = category_dist.get(cat, 0) + 1

            sev = r.get("overall_severity", "low")
            severity_dist[sev] = severity_dist.get(sev, 0) + 1

            for f in r.get("findings", []):
                if f.get("severity") in ["high", "critical"]:
                    high_severity_findings += 1

        schema_overview = self.get_schema_overview(user_id=user_id)

        return {
            "total_reports": total,
            "high_severity_findings": high_severity_findings,
            "reports_requiring_attention": requiring_attention,
            "completed_inspections": completed,
            "status_distribution": status_dist,
            "category_distribution": category_dist,
            "severity_distribution": severity_dist,
            "is_demonstration": False,
            "storage_mode": "Amazon DocumentDB Cluster" if self.mode == "documentdb" else "Local MongoDB Development Database",
            "aws_connected": self.mode == "documentdb",
            "schema_fields_count": len(schema_overview.fields),
            "nested_fields_count": schema_overview.nested_fields_count,
            "array_fields_count": schema_overview.arrays_count,
            "data_source": f"{'Amazon DocumentDB' if self.mode == 'documentdb' else 'MongoDB'} ({self.database_name}.{self.collection_name})"
        }
