from fastapi import APIRouter, HTTPException, Depends
from app.services.inspection_service import inspection_service
from app.services.query_validator import query_validator
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.report import (
    QueryRequest,
    QueryResponse,
    ReportResponse,
    SchemaOverviewResponse,
    RawQueryRequest,
    RawQueryResponse,
    ExplainQueryRequest,
    ExplainQueryResponse
)

router = APIRouter(prefix="/query", tags=["Nested Query Engine"])

@router.get("/schema", response_model=SchemaOverviewResponse)
async def get_document_schema(
    current_user: User = Depends(get_current_user)
):
    """Returns dynamic schema discovery overview strictly for the authenticated user's documents."""
    return inspection_service.get_schema_overview(user_id=current_user.id)

@router.post("", response_model=QueryResponse)
async def execute_nested_query(
    query_req: QueryRequest,
    current_user: User = Depends(get_current_user)
):
    """Executes structured visual query scoped strictly to the authenticated user's documents."""
    matched_reports, query_ast, mongo_query, exec_time = await inspection_service.execute_query(
        query_req,
        user_id=current_user.id
    )
    
    # Calculate depth and complexity
    depth = 1
    for c in query_req.conditions:
        depth = max(depth, len(c.field.split(".")))
    
    uses_elem = "$elemMatch" in str(mongo_query)
    complexity = "Complex" if (uses_elem or depth >= 3 or query_req.match_type == "or") else ("Moderate" if depth >= 2 else "Simple")

    explanation = (
        f"Evaluated {len(query_req.conditions)} condition(s) using '{query_req.match_type.upper()}' logic. "
        f"Found {len(matched_reports)} matching document(s) in {exec_time:.2f}ms."
    )
    
    return QueryResponse(
        total_matches=len(matched_reports),
        execution_time_ms=exec_time,
        query_ast=query_ast,
        mongo_equivalent_query=mongo_query,
        matched_reports=[ReportResponse(**r) for r in matched_reports],
        explanation=explanation,
        complexity=complexity,
        nested_depth=depth
    )

@router.post("/raw", response_model=RawQueryResponse)
async def execute_raw_query(
    req: RawQueryRequest,
    current_user: User = Depends(get_current_user)
):
    """Safely validates and executes a raw MongoDB-compatible read-only filter dictionary scoped to user."""
    is_valid, errors, warnings = query_validator.validate_query(req.query, collection="inspection_reports")
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Query validation failed: {'; '.join(errors)}"
        )

    matched_reports, exec_time = await inspection_service.execute_raw_query(
        req.query,
        limit=req.limit,
        user_id=current_user.id
    )
    
    # Estimate depth and complexity
    query_str = str(req.query)
    uses_elem = "$elemMatch" in query_str
    complexity = "Complex" if uses_elem or "$or" in query_str else "Moderate"

    return RawQueryResponse(
        total_matches=len(matched_reports),
        execution_time_ms=exec_time,
        query=req.query,
        matched_reports=[ReportResponse(**r) for r in matched_reports],
        is_valid=True,
        warnings=warnings,
        complexity=complexity,
        nested_depth=3 if uses_elem else 2
    )

@router.post("/explain", response_model=ExplainQueryResponse)
async def explain_query(
    req: ExplainQueryRequest,
    current_user: User = Depends(get_current_user)
):
    """Provides structured Gemini AI explanation and DocumentDB indexing advice for a query filter."""
    return await inspection_service.explain_query(req.query, collection=req.collection)
