import pytest
import unittest.mock as mock
from typing import Dict, Any, List
from bson import ObjectId

from app.config.settings import settings
from app.repositories.mongodb_repository import MongoDBInspectionRepository
from app.repositories.memory_repository import InMemoryInspectionRepository
from app.services.inspection_service import get_default_repository, InspectionService
from app.services.mongodb_executor import MongoExecutor, mongo_executor, MongoTestResult
from app.services.documentdb_compatibility import compatibility_analyzer
from app.services.ai_service import ai_query_service

# =====================================================================
# 1. MongoDB Connection & Graceful Detection Tests
# =====================================================================

def test_1_mongodb_connection_success():
    with mock.patch("app.services.mongodb_executor.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.admin.command.return_value = {"ok": 1}
        mock_client_cls.return_value = mock_instance

        executor = MongoExecutor(uri="mongodb://localhost:27017")
        assert executor.is_available() is True


def test_2_mongodb_unavailable_graceful_handling():
    with mock.patch("app.services.mongodb_executor.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.admin.command.side_effect = Exception("Connection refused on port 27017")
        mock_client_cls.return_value = mock_instance

        executor = MongoExecutor(uri="mongodb://localhost:27017")
        assert executor.is_available() is False

        # Execute test should not raise exception, but return status 'unavailable'
        import asyncio
        res = asyncio.run(executor.execute_test({"category": "Electrical"}))
        assert res.status == "unavailable"
        assert "Could not connect to local MongoDB" in res.reason


# =====================================================================
# 2. Flexible Document CRUD Tests
# =====================================================================

@pytest.mark.asyncio
async def test_3_insert_flexible_document():
    with mock.patch("app.repositories.mongodb_repository.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_coll = mock.MagicMock()
        mock_coll.count_documents.return_value = 5
        inserted_id = ObjectId()
        mock_coll.insert_one.return_value = mock.MagicMock(inserted_id=inserted_id)
        mock_instance.__getitem__.return_value.__getitem__.return_value = mock_coll
        mock_client_cls.return_value = mock_instance

        repo = MongoDBInspectionRepository()
        flexible_doc = {
            "title": "Solar Array Diagnostics",
            "inspector_name": "Elena Rostova",
            "category": "Solar Energy",
            "custom_telemetry": {"inverter_efficiency": 98.4, "panel_temp_c": 45.2},
            "polymorphic_metadata": ["tag_a", "tag_b"]
        }

        created = await repo.create(flexible_doc)
        assert created["title"] == "Solar Array Diagnostics"
        assert "id" in created
        assert "_id" not in created  # Safe ObjectId conversion


@pytest.mark.asyncio
async def test_4_retrieve_document_by_id():
    with mock.patch("app.repositories.mongodb_repository.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_coll = mock.MagicMock()
        mock_coll.find_one.return_value = {
            "_id": ObjectId(),
            "id": "RPT-2026-0099",
            "title": "HVAC Chiller Audit",
            "status": "passed"
        }
        mock_instance.__getitem__.return_value.__getitem__.return_value = mock_coll
        mock_client_cls.return_value = mock_instance

        repo = MongoDBInspectionRepository()
        doc = await repo.get_by_id("RPT-2026-0099")
        assert doc is not None
        assert doc["id"] == "RPT-2026-0099"
        assert "_id" not in doc


# =====================================================================
# 3. Nested & Array Document Query Tests
# =====================================================================

@pytest.mark.asyncio
async def test_5_nested_document_query():
    with mock.patch("app.repositories.mongodb_repository.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_coll = mock.MagicMock()
        mock_coll.find.return_value.limit.return_value = [
            {"id": "RPT-2026-0001", "dynamic_attributes": {"electrical_telemetry": {"phase_delta_t_c": 38.4}}}
        ]
        mock_instance.__getitem__.return_value.__getitem__.return_value = mock_coll
        mock_client_cls.return_value = mock_instance

        repo = MongoDBInspectionRepository()
        conditions = [
            {
                "field": "dynamic_attributes.electrical_telemetry.phase_delta_t_c",
                "operator": "greater_than",
                "value": 30.0,
                "value_type": "number"
            }
        ]
        results, mongo_filter, ast_obj, exec_ms = await repo.query_nested(conditions, match_type="and")
        assert len(results) == 1
        assert "dynamic_attributes.electrical_telemetry.phase_delta_t_c" in mongo_filter


@pytest.mark.asyncio
async def test_6_array_document_query():
    with mock.patch("app.repositories.mongodb_repository.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_coll = mock.MagicMock()
        mock_coll.find.return_value.limit.return_value = [
            {"id": "RPT-2026-0001", "findings": [{"severity": "high"}]}
        ]
        mock_instance.__getitem__.return_value.__getitem__.return_value = mock_coll
        mock_client_cls.return_value = mock_instance

        repo = MongoDBInspectionRepository()
        conditions = [
            {
                "field": "findings.severity",
                "operator": "equals",
                "value": "high",
                "value_type": "string"
            }
        ]
        results, mongo_filter, _, _ = await repo.query_nested(conditions)
        assert "findings" in mongo_filter
        assert "$elemMatch" in mongo_filter["findings"]


@pytest.mark.asyncio
async def test_7_nested_array_elem_match():
    with mock.patch("app.repositories.mongodb_repository.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_coll = mock.MagicMock()
        mock_coll.find.return_value.limit.return_value = [
            {"id": "RPT-2026-0001", "findings": [{"issues": [{"status": "open", "severity": "critical"}]}]}
        ]
        mock_instance.__getitem__.return_value.__getitem__.return_value = mock_coll
        mock_client_cls.return_value = mock_instance

        repo = MongoDBInspectionRepository()
        conditions = [
            {"field": "findings.issues.status", "operator": "equals", "value": "open", "value_type": "string"},
            {"field": "findings.issues.severity", "operator": "equals", "value": "critical", "value_type": "string"}
        ]
        results, mongo_filter, _, _ = await repo.query_nested(conditions)
        assert "findings" in mongo_filter


# =====================================================================
# 4. Safe Read-Only Execution & AST Rejection Tests
# =====================================================================

@pytest.mark.asyncio
async def test_8_read_only_find_query_execution():
    with mock.patch("app.services.mongodb_executor.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.admin.command.return_value = {"ok": 1}
        mock_coll = mock.MagicMock()
        mock_coll.count_documents.return_value = 3
        mock_coll.find.return_value.limit.return_value = [
            {"_id": ObjectId(), "id": "RPT-001", "status": "failed"},
            {"_id": ObjectId(), "id": "RPT-002", "status": "failed"},
            {"_id": ObjectId(), "id": "RPT-003", "status": "failed"}
        ]
        mock_instance.__getitem__.return_value.__getitem__.return_value = mock_coll
        mock_client_cls.return_value = mock_instance

        executor = MongoExecutor()
        res = await executor.execute_test({"status": "failed"}, operation="find")
        assert res.status == "success"
        assert res.documents_matched == 3
        assert len(res.sample_results) == 3


@pytest.mark.asyncio
async def test_9_read_only_aggregate_query_execution():
    with mock.patch("app.services.mongodb_executor.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.admin.command.return_value = {"ok": 1}
        mock_coll = mock.MagicMock()
        mock_coll.aggregate.return_value = [
            {"_id": "Electrical", "count": 4},
            {"_id": "HVAC", "count": 2}
        ]
        mock_instance.__getitem__.return_value.__getitem__.return_value = mock_coll
        mock_client_cls.return_value = mock_instance

        executor = MongoExecutor()
        pipeline = [
            {"$match": {"status": "action_required"}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}}
        ]
        res = await executor.execute_test(pipeline, operation="aggregate")
        assert res.status == "success"
        assert len(res.sample_results) == 2


@pytest.mark.asyncio
async def test_10_write_operation_rejected():
    executor = MongoExecutor()
    res = await executor.execute_test({"$set": {"status": "passed"}}, operation="updateOne")
    assert res.status == "rejected"
    assert "Prohibited write/admin operation" in res.reason


@pytest.mark.asyncio
async def test_11_delete_operation_rejected():
    executor = MongoExecutor()
    res = await executor.execute_test({"id": "RPT-001"}, operation="deleteMany")
    assert res.status == "rejected"
    assert "Prohibited write/admin operation" in res.reason


@pytest.mark.asyncio
async def test_12_result_limit_enforced():
    with mock.patch("app.services.mongodb_executor.MongoClient") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.admin.command.return_value = {"ok": 1}
        mock_coll = mock.MagicMock()
        mock_coll.count_documents.return_value = 100
        mock_coll.find.return_value.limit.return_value = [{"id": f"RPT-{i}"} for i in range(20)]
        mock_instance.__getitem__.return_value.__getitem__.return_value = mock_coll
        mock_client_cls.return_value = mock_instance

        executor = MongoExecutor(max_results=20)
        res = await executor.execute_test({}, operation="find")
        assert res.status == "success"
        assert res.documents_matched == 100
        assert len(res.sample_results) == 20
        assert res.max_results_limit == 20


# =====================================================================
# 5. Compatibility Engine Scenarios
# =====================================================================

def test_13_mongodb_compatible_query():
    query = {"category": "Electrical", "status": "action_required"}
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "COMPATIBLE"
    assert report.mongodb_supported is True
    assert report.documentdb_supported is True


def test_14_documentdb_incompatible_query():
    query = {"$where": "this.findings.length > 2"}
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "INCOMPATIBLE"
    assert report.documentdb_supported is False
    assert any(i.feature == "$where" for i in report.issues)


def test_15_documentdb_behavioral_difference():
    query = {"inspector_name": {"$regex": "marcus", "$options": "i"}}
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "BEHAVIOR_DIFFERENCE"
    assert len(report.behavioral_differences) > 0


def test_16_unknown_compatibility_feature():
    query = {"custom_field": {"$customUnknownOp": 123}}
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "UNKNOWN"


def test_17_elemmatch_inside_all_with_validated_alternative():
    query = {
        "findings": {
            "$all": [
                {"$elemMatch": {"severity": "critical"}},
                {"$elemMatch": {"remediation_status": "open"}}
            ]
        }
    }
    report = compatibility_analyzer.analyze_query(query, target_version="5.0")
    assert report.status == "INCOMPATIBLE"
    assert report.alternative_query is not None
    assert "$and" in report.alternative_query
    assert report.alternative_status == "COMPATIBLE"


def test_18_gemini_alternative_query_validation():
    bad_alt = {"$where": "function() { return true; }"}
    alt_report = compatibility_analyzer.analyze_query(bad_alt, target_version="5.0")
    assert alt_report.status == "INCOMPATIBLE"


# =====================================================================
# 6. AI Assistant Integration & Repository Fallback Tests
# =====================================================================

@pytest.mark.asyncio
async def test_19_ai_service_integration_with_mongo_test_result():
    res = await ai_query_service.generate_query("Find all reports with high-severity findings.")
    assert res.query is not None
    assert res.is_validated is True
    assert res.compatibility is not None
    assert res.mongo_test_result is not None


def test_20_repository_fallback_when_mongodb_unavailable():
    with mock.patch("app.config.settings.settings.STORAGE_MODE", "mongodb"):
        with mock.patch("app.repositories.mongodb_repository.MongoClient") as mock_client_cls:
            mock_instance = mock.MagicMock()
            mock_instance.admin.command.side_effect = Exception("Connection offline")
            mock_client_cls.return_value = mock_instance

            repo = get_default_repository()
            assert isinstance(repo, InMemoryInspectionRepository)
