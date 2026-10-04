import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from app.config.settings import settings

# pymongo warns on every DocumentDB connection; compatibility is handled by this app
warnings.filterwarnings("ignore", message="You appear to be connected to a DocumentDB cluster")

# Absolute path to backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass
class DocumentStoreConfig:
    """Connection settings for the database that stores inspection documents."""
    mode: str
    engine_label: str
    uri: str
    database: str
    collection: str
    timeout_ms: int
    client_kwargs: Dict[str, Any] = field(default_factory=dict)


def get_storage_mode() -> str:
    return (getattr(settings, "STORAGE_MODE", "memory") or "memory").strip().lower()


def resolve_ca_file(path: str) -> str:
    ca_path = Path(path)
    if not ca_path.is_absolute():
        ca_path = BACKEND_DIR / ca_path
    return str(ca_path)


def get_document_store_config() -> DocumentStoreConfig:
    """
    Builds MongoClient settings for the active STORAGE_MODE.
    'documentdb' targets Amazon DocumentDB; any other mode targets local MongoDB.
    """
    if get_storage_mode() == "documentdb":
        # DocumentDB 3.6/4.0 reject retryable writes (5.0 accepts them); keep them off so every engine version works.
        client_kwargs: Dict[str, Any] = {"retryWrites": False}
        if settings.DOCUMENTDB_TLS:
            client_kwargs["tls"] = True
            client_kwargs["tlsCAFile"] = resolve_ca_file(settings.DOCUMENTDB_TLS_CA_FILE)
        if settings.DOCUMENTDB_USERNAME:
            client_kwargs["username"] = settings.DOCUMENTDB_USERNAME
        if settings.DOCUMENTDB_PASSWORD:
            client_kwargs["password"] = settings.DOCUMENTDB_PASSWORD

        return DocumentStoreConfig(
            mode="documentdb",
            engine_label="Amazon DocumentDB",
            uri=settings.DOCUMENTDB_URI.strip(),
            database=settings.DOCUMENTDB_DATABASE or "inspectdb",
            collection=settings.DOCUMENTDB_COLLECTION or "inspection_reports",
            timeout_ms=settings.DOCUMENTDB_TIMEOUT_MS,
            client_kwargs=client_kwargs,
        )

    return DocumentStoreConfig(
        mode=get_storage_mode(),
        engine_label="local MongoDB",
        uri=settings.MONGODB_URI or "mongodb://localhost:27017",
        database=settings.MONGODB_DATABASE or "inspectdb",
        collection=settings.MONGODB_COLLECTION or "inspection_reports",
        timeout_ms=getattr(settings, "MONGODB_TIMEOUT_MS", 2000),
    )


def redact_uri(uri: str) -> str:
    """Masks the password in a mongodb:// connection string so it is safe to log or return."""
    return re.sub(r"//([^:/@]+):([^@]*)@", r"//\1:****@", uri or "")
