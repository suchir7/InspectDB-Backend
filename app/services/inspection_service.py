import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from app.repositories.base import BaseInspectionRepository
from app.repositories.memory_repository import InMemoryInspectionRepository
from app.schemas.report import (
    ReportCreate,
    ReportUpdate,
    QueryRequest,
    SchemaOverviewResponse,
    ExplainQueryResponse
)
from app.services.query_validator import query_validator
from app.services.ai_service import ai_query_service
from app.config.settings import settings
from app.db.document_store import get_storage_mode

logger = logging.getLogger(__name__)

def get_default_repository() -> BaseInspectionRepository:
    """
    Selects the active inspection repository based on settings.STORAGE_MODE.
    'documentdb' always returns the Amazon DocumentDB repository: there is no silent fallback,
    because writes to an in-memory store would be lost on restart.
    If 'mongodb' is configured and reachable, returns MongoDBInspectionRepository.
    Gracefully falls back to InMemoryInspectionRepository if MongoDB is offline or if 'memory' mode is active.
    """
    mode = get_storage_mode()
    if mode == "documentdb":
        from app.repositories.mongodb_repository import MongoDBInspectionRepository
        repo = MongoDBInspectionRepository()
        logger.info(f"Using Amazon DocumentDB repository: {repo.database_name}.{repo.collection_name}")
        return repo
    if mode == "mongodb":
        try:
            from app.repositories.mongodb_repository import MongoDBInspectionRepository
            repo = MongoDBInspectionRepository()
            if repo.is_available():
                logger.info(f"Connected to local MongoDB: {settings.MONGODB_DATABASE}.{settings.MONGODB_COLLECTION}")
                return repo
            else:
                logger.warning("STORAGE_MODE is set to 'mongodb' but server is unreachable. Falling back safely to InMemoryInspectionRepository.")
                return InMemoryInspectionRepository()
        except Exception as e:
            logger.warning(f"Error initializing MongoDB repository ({e}). Using InMemoryInspectionRepository.")
            return InMemoryInspectionRepository()
    return InMemoryInspectionRepository()

class InspectionService:
    def __init__(self, repository: Optional[BaseInspectionRepository] = None):
        self.repository = repository or get_default_repository()

    async def list_reports(
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
        return await self.repository.get_all(
            user_id=user_id,
            search=search,
            category=category,
            status=status,
            severity=severity,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            limit=limit
        )

    async def get_report_by_id(self, report_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return await self.repository.get_by_id(report_id, user_id=user_id)

    async def create_report(self, report_in: ReportCreate, user_id: Optional[str] = None) -> Dict[str, Any]:
        data = report_in.model_dump()
        return await self.repository.create(data, user_id=user_id)

    async def update_report(self, report_id: str, report_in: ReportUpdate, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        update_data = report_in.model_dump(exclude_unset=True)
        return await self.repository.update(report_id, update_data, user_id=user_id)

    async def delete_report(self, report_id: str, user_id: Optional[str] = None) -> bool:
        return await self.repository.delete(report_id, user_id=user_id)

    async def execute_query(self, query_request: QueryRequest, user_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], float]:
        conditions = [c.model_dump() for c in query_request.conditions]
        return await self.repository.query_nested(
            conditions=conditions,
            match_type=query_request.match_type,
            limit=query_request.limit,
            user_id=user_id
        )

    async def execute_raw_query(self, raw_filter: Dict[str, Any], limit: int = 20, user_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], float]:
        return await self.repository.query_raw(raw_filter, limit=limit, user_id=user_id)

    def get_schema_overview(self, user_id: Optional[str] = None) -> SchemaOverviewResponse:
        return self.repository.get_schema_overview(user_id=user_id)

    async def get_dashboard_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        return await self.repository.get_stats(user_id=user_id)

    async def explain_query(self, query: Dict[str, Any], collection: str = "inspection_reports") -> ExplainQueryResponse:
        # Determine query attributes
        query_str = json.dumps(query)
        uses_elem_match = "$elemMatch" in query_str
        
        # Extract nested paths
        nested_paths = []
        def _find_paths(node: Any, prefix: str = ""):
            if isinstance(node, dict):
                for k, v in node.items():
                    if not k.startswith("$"):
                        p = f"{prefix}.{k}" if prefix else k
                        if p not in nested_paths:
                            nested_paths.append(p)
                        _find_paths(v, p)
                    else:
                        _find_paths(v, prefix)
            elif isinstance(node, list):
                for item in node:
                    _find_paths(item, prefix)

        _find_paths(query)

        # Complexity determination
        depth = max((len(p.split(".")) for p in nested_paths), default=1)
        if uses_elem_match or depth >= 3 or "$or" in query or "$nor" in query:
            complexity = "Complex" if (uses_elem_match and depth >= 3) else "Moderate"
        else:
            complexity = "Simple" if len(nested_paths) <= 1 else "Moderate"

        index_recs = []
        for p in nested_paths:
            if p.startswith("findings."):
                index_recs.append(f"db.inspection_reports.createIndex({{\"{p}\": 1}})")
            elif p.startswith("dynamic_attributes."):
                index_recs.append(f"db.inspection_reports.createIndex({{\"{p}\": 1}})")

        if not index_recs:
            index_recs = ["db.inspection_reports.createIndex({\"category\": 1, \"inspection_date\": -1})"]

        # Call Gemini if configured, otherwise produce comprehensive deterministic explanation
        if ai_query_service.is_configured():
            try:
                keys = ai_query_service.get_api_keys()
                for key in keys:
                    try:
                        from google import genai
                        from google.genai import types

                        prompt = f"""Explain this MongoDB/Amazon DocumentDB query filter for the inspection_reports collection in 2-3 concise sentences:
Query: {json.dumps(query, indent=2)}
Explain how it navigates nested paths and whether $elemMatch is used to isolate subdocuments."""

                        client = genai.Client(api_key=key)
                        response = client.models.generate_content(
                            model=settings.GEMINI_MODEL or "gemini-2.5-flash",
                            contents=prompt
                        )
                        if response.text:
                            return ExplainQueryResponse(
                                summary=response.text.strip().split("\n")[0],
                                nested_paths=nested_paths,
                                uses_elem_match=uses_elem_match,
                                complexity=complexity,
                                explanation=response.text.strip(),
                                index_recommendations=index_recs[:3]
                            )
                    except Exception as e:
                        logger.warning(f"Gemini explain query failed: {e}")
            except Exception as e:
                logger.error(f"Error during Gemini query explanation: {e}")

        # Deterministic Explanation Fallback
        explanation_parts = []
        if uses_elem_match:
            explanation_parts.append("This query uses the '$elemMatch' array operator to ensure that all specified conditions apply to the same individual finding or issue element, preventing false positive matches across disparate subdocuments.")
        else:
            explanation_parts.append(f"This query filters the collection across {len(nested_paths)} nested path(s) using DocumentDB standard dot notation traversal.")

        if any(p.startswith("dynamic_attributes.") for p in nested_paths):
            explanation_parts.append("It queries variable-schema dynamic attributes, returning only polymorphic documents containing the matching telemetry fields.")

        summary = f"Query searches {collection} for documents matching conditions across {', '.join(nested_paths[:3])}."

        return ExplainQueryResponse(
            summary=summary,
            nested_paths=nested_paths,
            uses_elem_match=uses_elem_match,
            complexity=complexity,
            explanation=" ".join(explanation_parts),
            index_recommendations=index_recs[:3]
        )

# Global service instance singleton
inspection_service = InspectionService()
