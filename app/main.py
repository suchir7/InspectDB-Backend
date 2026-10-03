import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import ConnectionFailure
import uvicorn

from app.config.settings import settings, Settings
from app.db.session import init_db
from app.db.document_store import get_storage_mode, resolve_ca_file
from app.services.inspection_service import inspection_service
from app.api.routes.health import router as health_router
from app.api.routes.reports import router as reports_router
from app.api.routes.query import router as query_router
from app.api.routes.stats import router as stats_router
from app.api.routes.ai import router as ai_router
from app.api.routes.cost import router as cost_router
from app.api.routes.auth import router as auth_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("inspectdb")

def init_document_store():
    """Validates DocumentDB settings and creates indexes for MongoDB-compatible stores."""
    if get_storage_mode() == "documentdb":
        if not settings.DOCUMENTDB_URI.strip():
            raise RuntimeError("STORAGE_MODE=documentdb requires DOCUMENTDB_URI to be set.")
        if settings.DOCUMENTDB_TLS:
            ca_file = resolve_ca_file(settings.DOCUMENTDB_TLS_CA_FILE)
            if not Path(ca_file).is_file():
                raise RuntimeError(
                    f"DocumentDB TLS CA bundle not found at '{ca_file}'. Download it from "
                    "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"
                )

    repo = inspection_service.repository
    if hasattr(repo, "ensure_indexes"):
        try:
            repo.ensure_indexes()
            logger.info(f"Document store ready: {repo.engine_label} ({repo.database_name}.{repo.collection_name})")
        except Exception as e:
            # Keep serving so /api/health can report the problem; requests retry the connection.
            logger.error(f"Could not initialize {repo.engine_label} indexes: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize relational database schemas (Neon PostgreSQL)
    init_db()
    init_document_store()
    if settings.ENVIRONMENT == "production" and settings.JWT_SECRET == Settings.model_fields["JWT_SECRET"].default:
        logger.warning("JWT_SECRET is using the built-in development default. Set a unique JWT_SECRET in production.")
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="InspectDB: Amazon DocumentDB Document-Oriented Inspection Report Management System API with Neon PostgreSQL Auth & AI Advisor",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan
)

# Configure CORS for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(health_router, prefix=settings.API_V1_STR)
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(reports_router, prefix=settings.API_V1_STR)
app.include_router(query_router, prefix=settings.API_V1_STR)
app.include_router(stats_router, prefix=settings.API_V1_STR)
app.include_router(ai_router, prefix=settings.API_V1_STR)
app.include_router(cost_router, prefix=settings.API_V1_STR)

@app.get("/")
async def root():
    return {
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "online",
        "docs_url": "/api/docs",
        "health_check": f"{settings.API_V1_STR}/health",
        "auth_endpoints": {
            "register": f"{settings.API_V1_STR}/auth/register",
            "login": f"{settings.API_V1_STR}/auth/login",
            "me": f"{settings.API_V1_STR}/auth/me"
        },
        "ai_assistant": f"{settings.API_V1_STR}/ai/generate-query",
        "cost_optimizer": f"{settings.API_V1_STR}/cost/estimate",
        "storage": {
            "application_auth_db": "Neon PostgreSQL (Online)",
            "inspection_documents_db": getattr(
                inspection_service.repository, "engine_label", "In-Memory Repository (Ready for Amazon DocumentDB)"
            )
        }
    }

@app.exception_handler(ConnectionFailure)
async def document_store_unavailable_handler(request: Request, exc: ConnectionFailure):
    # Raised when DocumentDB is stopped by its schedule or otherwise unreachable
    logger.warning(f"Document store unavailable for {request.url.path}: {type(exc).__name__}")
    return JSONResponse(
        status_code=503,
        content={
            "error": "DocumentStoreUnavailable",
            "detail": (
                "The inspection database is paused or unreachable right now. "
                "It may be outside its scheduled running hours; please try again later."
            ),
            "path": request.url.path
        }
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": "InternalServerError",
            "message": str(exc),
            "path": request.url.path
        }
    )

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
