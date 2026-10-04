from typing import Any, Dict

from fastapi import APIRouter
from app.config.settings import settings
from app.services.inspection_service import inspection_service

router = APIRouter(tags=["Health"])


def get_document_store_status() -> Dict[str, Any]:
    """Describes the inspection document store without exposing connection strings."""
    repo = inspection_service.repository
    if not hasattr(repo, "is_available"):
        return {
            "type": "In-Memory Repository (no database attached)",
            "mode": "memory",
            "status": "in_memory",
            "connected": False,
            "cluster_cost_active": False,
            "message": "Inspection reports are held in server memory and are lost when the server restarts."
        }

    is_documentdb = repo.mode == "documentdb"
    connected = repo.is_available()
    return {
        "type": "Amazon DocumentDB" if is_documentdb else "Local MongoDB",
        "mode": repo.mode,
        "status": "connected" if connected else "unreachable",
        "connected": connected,
        "cluster_cost_active": is_documentdb,
        "database_name": repo.database_name,
        "collection": repo.collection_name,
        "message": (
            f"Connected to {repo.engine_label} ({repo.database_name}.{repo.collection_name})."
            if connected
            else f"Cannot reach {repo.engine_label}. Check the server network path and connection settings."
        )
    }


# Sync handler so the database ping runs in the threadpool instead of blocking the event loop
@router.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "database": get_document_store_status()
    }
