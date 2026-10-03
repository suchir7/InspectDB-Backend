import copy
import pytest
from app.repositories.memory_repository import InMemoryInspectionRepository
from app.services.inspection_service import InspectionService
from app.services.query_validator import query_validator
from app.schemas.report import QueryRequest, QueryCondition
from tests.fixtures.sample_reports import SAMPLE_REPORTS

@pytest.fixture
def repo():
    r = InMemoryInspectionRepository()
    for report in SAMPLE_REPORTS:
        r._reports[report["id"]] = copy.deepcopy(report)
    return r

@pytest.fixture
def service(repo):
    return InspectionService(repository=repo)

def test_schema_discovery(repo):
    overview = repo.get_schema_overview()
    assert overview.total_documents == 6
    assert overview.nested_fields_count > 10
    assert overview.arrays_count >= 2
    
    # Check that findings, issues, and dynamic telemetry are discovered
    paths = [f.path for f in overview.fields]
    assert "findings" in paths
    assert "findings.severity" in paths
    assert "findings.issues.status" in paths
    assert "dynamic_attributes.electrical_telemetry.phases.phase_a.voltage_kv" in paths

    # Check variable schema flags
    findings_field = next(f for f in overview.fields if f.path == "findings")
    assert findings_field.occurrence_count == 6
    assert findings_field.is_variable_schema is False

    telemetry_field = next(f for f in overview.fields if "electrical_telemetry" in f.path)
    assert telemetry_field.occurrence_count < 6
    assert telemetry_field.is_variable_schema is True

@pytest.mark.asyncio
async def test_nested_array_elem_match(service):
    # Multiple conditions on the findings array: severity == high AND category == Thermal Anomaly
    req = QueryRequest(
        match_type="and",
        conditions=[
            QueryCondition(field="findings.severity", operator="equals", value="high"),
            QueryCondition(field="findings.category", operator="equals", value="Thermal Anomaly")
        ]
    )
    reports, ast, mongo_query, exec_time = await service.execute_query(req)
    assert len(reports) >= 1
    assert "RPT-2026-0891" in [r["id"] for r in reports]
    assert "$elemMatch" in str(mongo_query)

@pytest.mark.asyncio
async def test_nested_telemetry_numeric_comparison(service):
    # Voltage > 13.8 kV
    req = QueryRequest(
        match_type="and",
        conditions=[
            QueryCondition(
                field="dynamic_attributes.electrical_telemetry.phases.phase_a.voltage_kv",
                operator="greater_than",
                value=13.8,
                value_type="number"
            )
        ]
    )
    reports, ast, mongo_query, exec_time = await service.execute_query(req)
    assert len(reports) == 1
    assert reports[0]["id"] == "RPT-2026-0891"

@pytest.mark.asyncio
async def test_raw_query_execution_and_safety(service):
    # Safe read query
    raw_query = {
        "status": "action_required",
        "findings": {
            "$elemMatch": {
                "severity": {"$in": ["high", "critical"]}
            }
        }
    }
    is_valid, errors, warnings = query_validator.validate_query(raw_query)
    assert is_valid is True
    assert len(errors) == 0

    results, exec_time = await service.execute_raw_query(raw_query)
    assert len(results) >= 1

    # Unsafe query with mutation operator
    unsafe_query = {
        "$set": {"status": "passed"}
    }
    is_valid_unsafe, errors_unsafe, warnings_unsafe = query_validator.validate_query(unsafe_query)
    assert is_valid_unsafe is False
    assert any("forbidden" in e.lower() for e in errors_unsafe)

@pytest.mark.asyncio
async def test_zero_result_query(service):
    req = QueryRequest(
        match_type="and",
        conditions=[
            QueryCondition(field="location", operator="equals", value="NonExistentFacilityXYZ")
        ]
    )
    reports, ast, mongo_query, exec_time = await service.execute_query(req)
    assert len(reports) == 0

@pytest.mark.asyncio
async def test_query_explanation(service):
    query = {"findings": {"$elemMatch": {"severity": "high", "status": "open"}}}
    explanation = await service.explain_query(query)
    assert explanation.uses_elem_match is True
    assert "findings" in explanation.nested_paths[0]
    assert len(explanation.index_recommendations) >= 1
