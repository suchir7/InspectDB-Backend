from fastapi import APIRouter, HTTPException, Depends, status
from app.schemas.ai import (
    GenerateQueryRequest,
    GenerateQueryResponse,
    AiServiceStatus,
    CheckCompatibilityRequest,
    TestLocalQueryRequest
)
from app.services.ai_service import ai_query_service
from app.services.documentdb_compatibility import compatibility_analyzer, CompatibilityReport
from app.services.mongodb_executor import mongo_executor, MongoTestResult
from app.services.query_validator import query_validator
from app.api.deps import get_current_user, get_optional_current_user
from app.models.user import User
from app.config.settings import settings

router = APIRouter(prefix="/ai", tags=["AI Query Assistant"])

@router.post("/generate-query", response_model=GenerateQueryResponse)
async def generate_query(
    request: GenerateQueryRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Translates a natural language question into a verified Amazon DocumentDB /
    MongoDB-compatible query filter with plain-English explanation, local MongoDB execution test,
    and deterministic DocumentDB compatibility analysis.
    Executes test strictly scoped to the authenticated user's documents.
    """
    if not request.question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The 'question' field must contain a non-empty string."
        )

    response = await ai_query_service.generate_query(
        question=request.question,
        context=request.context,
        target_version=request.target_version,
        execute_local_test=request.execute_local_test,
        user_id=current_user.id
    )
    return response

@router.post("/test-local-query")
async def test_local_query(
    request: TestLocalQueryRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Safely executes an arbitrary read-only query against the local MongoDB development database
    strictly scoped to the authenticated user's documents and evaluates DocumentDB compatibility.
    """
    is_safe, safety_err = mongo_executor.validate_safety(request.query, operation=request.operation)
    if not is_safe:
        return {
            "mongo_test_result": MongoTestResult(
                status="rejected",
                database=mongo_executor.database_name,
                collection=request.collection,
                operation=request.operation,
                reason=safety_err,
                max_results_limit=getattr(settings, "MAX_QUERY_RESULTS", 20)
            ),
            "compatibility": compatibility_analyzer.analyze_query(
                query=request.query,
                target_version=request.target_version or "5.0"
            )
        }

    mongo_res = await mongo_executor.execute_test(
        query=request.query,
        operation=request.operation,
        collection_name=request.collection,
        user_id=current_user.id
    )
    compat_res = compatibility_analyzer.analyze_query(
        query=request.query,
        target_version=request.target_version or "5.0"
    )

    return {
        "mongo_test_result": mongo_res,
        "compatibility": compat_res
    }

@router.post("/compatibility", response_model=CompatibilityReport)
async def check_query_compatibility(request: CheckCompatibilityRequest):
    """
    Evaluates a MongoDB query dictionary against Amazon DocumentDB compatibility rules
    and version-specific features (3.6, 4.0, 5.0, 8.0).
    """
    return compatibility_analyzer.analyze_query(
        query=request.query,
        target_version=request.target_version
    )

@router.get("/status", response_model=AiServiceStatus)
async def get_ai_status():
    """
    Returns AI Assistant status and configuration without exposing secret keys.
    """
    is_conf = ai_query_service.is_configured()
    is_mongo_online = mongo_executor.is_available()
    return AiServiceStatus(
        status="ready" if is_conf else "unconfigured_key",
        gemini_configured=is_conf,
        model=settings.GEMINI_MODEL,
        supported_operations=["find", "countDocuments", "distinct", "aggregate"],
        target_collection=settings.DOCUMENTDB_COLLECTION,
        target_engine="Amazon DocumentDB",
        documentdb_target_version=getattr(settings, "DOCUMENTDB_TARGET_VERSION", "5.0"),
        supported_documentdb_versions=["3.6", "4.0", "5.0", "8.0"],
        local_mongodb_available=is_mongo_online,
        storage_mode=getattr(settings, "STORAGE_MODE", "memory"),
        max_query_results=getattr(settings, "MAX_QUERY_RESULTS", 20)
    )
