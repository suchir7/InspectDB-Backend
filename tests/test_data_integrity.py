import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.repositories.memory_repository import InMemoryInspectionRepository
from app.services.inspection_service import InspectionService, inspection_service
from app.api.deps import get_current_user

class MockUser:
    id = "test-user-data-integrity"
    email = "test@example.com"
    name = "Tester"
    role = "USER"

@pytest.fixture(autouse=True)
def setup_test_auth_and_repo():
    """Ensures each test starts with a fresh repository and authenticated mock user."""
    fresh_repo = InMemoryInspectionRepository()
    inspection_service.repository = fresh_repo
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    yield fresh_repo
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_1_and_2_empty_repository_startup():
    """
    1. New application starts with zero reports.
    2. GET reports returns [] when repository is empty.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/reports")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 0
        assert data["reports"] == []
        assert data["page"] == 1

@pytest.mark.asyncio
async def test_3_4_5_6_create_user_report_data_integrity():
    """
    3. Creating a report adds exactly one user-created document.
    4. Stored fields match the submitted user data.
    5. System metadata (id, created_at, updated_at) is generated.
    6. No fabricated inspection fields are added.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_payload = {
            "title": "Substation High-Voltage Transformer Check",
            "inspector_name": "Harish",
            "location": "Building A, West Wing",
            "inspection_date": "2026-10-03",
            "category": "Electrical",
            "status": "in_review",
            "overall_severity": "high",
            "description": "Exposed wiring on primary panel.",
            "findings": [
                {
                    "finding_id": "FND-01",
                    "category": "Electrical Hazards",
                    "severity": "high",
                    "description": "Exposed wiring behind main junction.",
                    "location_details": "Panel 3B",
                    "issues": [
                        {
                            "issue_id": "ISS-01",
                            "title": "Unshielded live contact",
                            "severity": "high",
                            "code_reference": "NEC 110.14",
                            "status": "open",
                            "notes": "De-energize circuit prior to repair."
                        }
                    ]
                }
            ],
            "custom_fields": [
                {"key": "operating_voltage", "value": 480, "field_type": "number"}
            ],
            "dynamic_attributes": {
                "telemetry": {
                    "phase_a_volts": 480.2,
                    "ambient_temp_c": 26.5
                }
            }
        }

        create_res = await client.post("/api/reports", json=user_payload)
        assert create_res.status_code == 201
        created = create_res.json()

        # 3. Check created document identity & system metadata
        assert created["id"].startswith("RPT-")
        assert "created_at" in created
        assert "updated_at" in created
        assert created["is_sample"] is False

        # 4. Check that stored fields match exactly what the user entered
        assert created["title"] == "Substation High-Voltage Transformer Check"
        assert created["inspector_name"] == "Harish"
        assert created["location"] == "Building A, West Wing"
        assert created["inspection_date"] == "2026-10-03"
        assert created["category"] == "Electrical"
        assert created["status"] == "in_review"
        assert created["overall_severity"] == "high"
        assert created["description"] == "Exposed wiring on primary panel."

        # 6. Verify no fake names or fabricated data were injected
        assert "Marcus Vance" not in str(created)
        assert "Elena Rostova" not in str(created)
        assert "David Sterling" not in str(created)

        # Verify GET /api/reports now has exactly 1 report
        list_res = await client.get("/api/reports")
        assert list_res.status_code == 200
        list_data = list_res.json()
        assert list_data["total"] == 1
        assert len(list_data["reports"]) == 1
        assert list_data["reports"][0]["id"] == created["id"]
        assert list_data["reports"][0]["inspector_name"] == "Harish"

@pytest.mark.asyncio
async def test_7_8_9_variable_schema_nested_objects_and_arrays():
    """
    7. Different document schemas are accepted without rigid schema enforcement.
    8. Nested objects are preserved.
    9. Nested arrays are preserved.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Document 1: Electrical schema
        doc_electrical = {
            "title": "Substation Grounding Survey",
            "inspector_name": "Alice Developer",
            "location": "Substation Alpha",
            "inspection_date": "2026-10-01",
            "category": "Electrical",
            "status": "passed",
            "overall_severity": "low",
            "findings": [],
            "dynamic_attributes": {
                "voltage_rating_kv": 13.8,
                "resistance_measurements": [0.42, 0.45, 0.41],
                "substation_telemetry": {
                    "grid_frequency_hz": 60.01,
                    "power_factor": 0.98
                }
            }
        }

        # Document 2: HVAC schema with different dynamic fields
        doc_hvac = {
            "title": "Rooftop Chiller Performance Test",
            "inspector_name": "Bob Technician",
            "location": "Facility Tower 2",
            "inspection_date": "2026-10-02",
            "category": "HVAC",
            "status": "action_required",
            "overall_severity": "medium",
            "findings": [
                {
                    "finding_id": "FND-H1",
                    "category": "Refrigerant",
                    "severity": "medium",
                    "description": "Minor pressure drop in Loop A",
                    "issues": []
                }
            ],
            "dynamic_attributes": {
                "chiller_pressure_bar": 1.45,
                "temperatures": {"inlet_c": 12.0, "outlet_c": 7.0},
                "equipment": {
                    "model": "Carrier 30XA",
                    "refrigerant_type": "R-134a"
                }
            }
        }

        res1 = await client.post("/api/reports", json=doc_electrical)
        res2 = await client.post("/api/reports", json=doc_hvac)
        assert res1.status_code == 201
        assert res2.status_code == 201

        body1 = res1.json()
        body2 = res2.json()

        # Check preservation of nested objects and arrays
        assert body1["dynamic_attributes"]["voltage_rating_kv"] == 13.8
        assert body1["dynamic_attributes"]["resistance_measurements"] == [0.42, 0.45, 0.41]
        assert body1["dynamic_attributes"]["substation_telemetry"]["power_factor"] == 0.98

        assert body2["dynamic_attributes"]["chiller_pressure_bar"] == 1.45
        assert body2["dynamic_attributes"]["temperatures"]["outlet_c"] == 7.0
        assert body2["dynamic_attributes"]["equipment"]["model"] == "Carrier 30XA"

@pytest.mark.asyncio
async def test_10_update_report():
    """
    10. Updating a report changes only the submitted values.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_res = await client.post("/api/reports", json={
            "title": "Structural Beam Inspection",
            "inspector_name": "Carol Engineer",
            "location": "North Bridge",
            "inspection_date": "2026-10-01",
            "category": "Structural",
            "status": "draft",
            "overall_severity": "low"
        })
        rep_id = create_res.json()["id"]

        # Update status and overall_severity only
        update_res = await client.put(f"/api/reports/{rep_id}", json={
            "status": "passed",
            "overall_severity": "none"
        })
        assert update_res.status_code == 200
        updated = update_res.json()
        assert updated["id"] == rep_id
        assert updated["status"] == "passed"
        assert updated["overall_severity"] == "none"
        assert updated["title"] == "Structural Beam Inspection"
        assert updated["inspector_name"] == "Carol Engineer"

@pytest.mark.asyncio
async def test_11_delete_report():
    """
    11. Deleting a report removes it completely.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_res = await client.post("/api/reports", json={
            "title": "Temporary Boiler Test",
            "inspector_name": "Dan",
            "location": "Plant B",
            "inspection_date": "2026-10-01",
            "category": "Equipment",
            "status": "draft",
            "overall_severity": "low"
        })
        rep_id = create_res.json()["id"]

        # Verify exists
        get_res = await client.get(f"/api/reports/{rep_id}")
        assert get_res.status_code == 200

        # Delete
        del_res = await client.delete(f"/api/reports/{rep_id}")
        assert del_res.status_code == 200

        # Verify 404
        get_after = await client.get(f"/api/reports/{rep_id}")
        assert get_after.status_code == 404

        # Verify collection is empty
        list_res = await client.get("/api/reports")
        assert list_res.json()["total"] == 0
        assert list_res.json()["reports"] == []

@pytest.mark.asyncio
async def test_12_dashboard_statistics_zero_when_empty():
    """
    12. Dashboard statistics are zero when no reports exist.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        stats_res = await client.get("/api/stats")
        assert stats_res.status_code == 200
        stats = stats_res.json()

        assert stats["total_reports"] == 0
        assert stats["high_severity_findings"] == 0
        assert stats["reports_requiring_attention"] == 0
        assert stats["completed_inspections"] == 0
        assert stats["schema_fields_count"] == 0
        assert stats["nested_fields_count"] == 0
        assert stats["array_fields_count"] == 0

@pytest.mark.asyncio
async def test_13_query_engine_returns_zero_when_empty():
    """
    13. Nested Query Engine does not fabricate report results when repository is empty.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        query_res = await client.post("/api/query", json={
            "match_type": "and",
            "conditions": [
                {"field": "status", "operator": "equals", "value": "passed"}
            ]
        })
        assert query_res.status_code == 200
        data = query_res.json()
        assert data["total_matches"] == 0
        assert data["matched_reports"] == []
