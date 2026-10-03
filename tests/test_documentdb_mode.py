import asyncio
import unittest.mock as mock

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.db.document_store import get_document_store_config, redact_uri
from app.repositories.mongodb_repository import MongoDBInspectionRepository
from app.services.inspection_service import get_default_repository
from app.services.mongodb_executor import MongoExecutor

DOCDB_SETTINGS = {
    "STORAGE_MODE": "documentdb",
    "DOCUMENTDB_URI": "mongodb://docdb.cluster-x.us-east-1.docdb.amazonaws.com:27017/?replicaSet=rs0",
    "DOCUMENTDB_USERNAME": "inspectdbadmin",
    "DOCUMENTDB_PASSWORD": "S3cretPassw0rd",
    "DOCUMENTDB_TLS": True,
    "DOCUMENTDB_TLS_CA_FILE": "global-bundle.pem",
}


def patch_settings(**overrides):
    return mock.patch.multiple("app.config.settings.settings", **overrides)


def test_documentdb_config_sets_required_client_options():
    with patch_settings(**DOCDB_SETTINGS):
        cfg = get_document_store_config()
    assert cfg.mode == "documentdb"
    assert cfg.engine_label == "Amazon DocumentDB"
    assert cfg.client_kwargs["retryWrites"] is False
    assert cfg.client_kwargs["tls"] is True
    assert cfg.client_kwargs["tlsCAFile"].endswith("global-bundle.pem")
    assert cfg.client_kwargs["username"] == "inspectdbadmin"
    assert cfg.client_kwargs["password"] == "S3cretPassw0rd"


def test_documentdb_config_can_disable_tls():
    with patch_settings(**{**DOCDB_SETTINGS, "DOCUMENTDB_TLS": False}):
        cfg = get_document_store_config()
    assert "tls" not in cfg.client_kwargs
    assert cfg.client_kwargs["retryWrites"] is False


def test_local_modes_use_mongodb_settings():
    with patch_settings(STORAGE_MODE="mongodb", MONGODB_URI="mongodb://localhost:27017"):
        cfg = get_document_store_config()
    assert cfg.engine_label == "local MongoDB"
    assert cfg.uri == "mongodb://localhost:27017"
    assert cfg.client_kwargs == {}


def test_redact_uri_masks_password():
    assert redact_uri("mongodb://admin:hunter2@host:27017/?tls=true") == "mongodb://admin:****@host:27017/?tls=true"
    assert redact_uri("mongodb://host:27017") == "mongodb://host:27017"


def test_documentdb_mode_never_falls_back_to_memory():
    with patch_settings(**DOCDB_SETTINGS):
        with mock.patch("app.repositories.mongodb_repository.MongoClient") as mock_client_cls:
            mock_client_cls.return_value.admin.command.side_effect = Exception("cluster offline")
            repo = get_default_repository()
    assert isinstance(repo, MongoDBInspectionRepository)
    assert repo.mode == "documentdb"


def test_repository_passes_documentdb_options_to_client():
    with patch_settings(**DOCDB_SETTINGS):
        with mock.patch("app.repositories.mongodb_repository.MongoClient") as mock_client_cls:
            MongoDBInspectionRepository()._get_client()
    kwargs = mock_client_cls.call_args.kwargs
    assert kwargs["retryWrites"] is False and kwargs["tls"] is True


def test_executor_unavailable_reason_does_not_leak_password():
    with patch_settings(**{**DOCDB_SETTINGS, "DOCUMENTDB_URI": "mongodb://admin:Leaky123@docdb.example:27017"}):
        with mock.patch("app.services.mongodb_executor.MongoClient") as mock_client_cls:
            mock_client_cls.return_value.admin.command.side_effect = Exception("timeout")
            result = asyncio.run(MongoExecutor().execute_test({"category": "Electrical"}))
    assert result.status == "unavailable"
    assert "Amazon DocumentDB" in result.reason
    assert "Leaky123" not in result.reason


def _mock_repo_with_counter(seq_values):
    """Builds a repository whose counters collection returns the given sequence numbers."""
    reports = mock.MagicMock()
    counters = mock.MagicMock()
    counters.find_one_and_update.side_effect = [{"seq": s} for s in seq_values]
    database = mock.MagicMock()
    database.__getitem__.side_effect = lambda name: counters if name == "counters" else reports
    client = mock.MagicMock()
    client.__getitem__.return_value = database
    repo = MongoDBInspectionRepository(uri="mongodb://localhost:27017")
    repo._client = client
    return repo, reports


@pytest.mark.asyncio
async def test_report_ids_come_from_global_counter():
    repo, reports = _mock_repo_with_counter([7])
    reports.insert_one.return_value = mock.MagicMock(inserted_id=ObjectId())
    created = await repo.create({"title": "Boiler Audit"}, user_id="user-b")
    assert created["id"].endswith("-0007")
    assert created["user_id"] == "user-b"


@pytest.mark.asyncio
async def test_report_id_allocation_skips_taken_ids():
    repo, reports = _mock_repo_with_counter([891, 892])
    reports.insert_one.side_effect = [DuplicateKeyError("id taken"), mock.MagicMock(inserted_id=ObjectId())]
    created = await repo.create({"title": "Chiller Audit"}, user_id="user-a")
    assert created["id"].endswith("-0892")
    assert reports.insert_one.call_count == 2


def test_paused_document_store_returns_friendly_503():
    from fastapi.testclient import TestClient
    from pymongo.errors import ServerSelectionTimeoutError
    from app.main import app
    from app.api.deps import get_current_user
    from app.services.inspection_service import inspection_service

    app.dependency_overrides[get_current_user] = lambda: mock.MagicMock(id="user-a")
    try:
        with mock.patch.object(inspection_service.repository, "get_all",
                               side_effect=ServerSelectionTimeoutError("docdb.cluster-x:27017: timed out")):
            res = TestClient(app).get("/api/reports")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert res.status_code == 503
    body = res.json()
    assert body["error"] == "DocumentStoreUnavailable"
    assert "paused" in body["detail"]
    assert "docdb.cluster-x" not in res.text
