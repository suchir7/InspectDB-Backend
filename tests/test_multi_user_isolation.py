import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.base import Base
from app.db.session import get_db
from app.repositories.memory_repository import InMemoryInspectionRepository
from app.services.inspection_service import inspection_service

# Isolated in-memory SQLite database for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(autouse=True)
def setup_test_environment():
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    fresh_repo = InMemoryInspectionRepository()
    inspection_service.repository = fresh_repo
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)

@pytest.mark.asyncio
async def test_full_multi_user_isolation_lifecycle():
    """
    Comprehensive verification of multi-user isolation across 14 security scenarios:
    1. User A registers and creates Report A.
    2. User B registers and creates Report B.
    3. User A lists reports -> returns ONLY Report A.
    4. User B lists reports -> returns ONLY Report B.
    5. User A requests Report B by ID -> 404 Not Found (no leak).
    6. User B requests Report A by ID -> 404 Not Found (no leak).
    7. User A attempts to update Report B -> 404 Not Found / rejected.
    8. User B attempts to delete Report A -> 404 Not Found / rejected.
    9. User A uses Nested Query Engine -> matches ONLY Report A.
    10. User B uses Nested Query Engine -> matches ONLY Report B.
    11. User A checks dashboard stats -> computed ONLY from Report A.
    12. User B checks dashboard stats -> computed ONLY from Report B.
    13. Attempt to inject another user's user_id in payload -> backend binds authenticated user's ID.
    14. Unauthenticated requests are rejected with 401 Unauthorized.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register User A
        res_a = await client.post("/api/auth/register", json={
            "name": "Inspector Alice",
            "email": "alice@inspectdb.internal",
            "password": "Password123!"
        })
        assert res_a.status_code == 201
        token_a = res_a.json()["access_token"]
        user_a_id = res_a.json()["user"]["id"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # 2. Register User B
        res_b = await client.post("/api/auth/register", json={
            "name": "Inspector Bob",
            "email": "bob@inspectdb.internal",
            "password": "Password123!"
        })
        assert res_b.status_code == 201
        token_b = res_b.json()["access_token"]
        user_b_id = res_b.json()["user"]["id"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        assert user_a_id != user_b_id

        # 14. Verify unauthenticated requests are rejected
        unauth_list = await client.get("/api/reports")
        assert unauth_list.status_code == 401
        unauth_stats = await client.get("/api/stats")
        assert unauth_stats.status_code == 401

        # 1. User A creates Report A (also tests requirement 13: ignore spoofed user_id in body)
        create_a = await client.post(
            "/api/reports",
            headers=headers_a,
            json={
                "title": "Alice High-Voltage Switchgear Inspection",
                "inspector_name": "Inspector Alice",
                "location": "Facility Alpha - Bay 1",
                "inspection_date": "2026-10-01",
                "category": "Electrical",
                "status": "action_required",
                "overall_severity": "high",
                "description": "Loose busbar clamp.",
                "user_id": user_b_id  # Spoofed user_id attempt!
            }
        )
        assert create_a.status_code == 201
        report_a = create_a.json()
        report_a_id = report_a["id"]
        # Requirement 1 & 13: user_id must be authoritative from JWT (Alice), not spoofed (Bob)
        assert report_a["user_id"] == user_a_id
        assert report_a["user_id"] != user_b_id

        # 2. User B creates Report B
        create_b = await client.post(
            "/api/reports",
            headers=headers_b,
            json={
                "title": "Bob HVAC Chiller Efficiency Audit",
                "inspector_name": "Inspector Bob",
                "location": "Facility Beta - Rooftop",
                "inspection_date": "2026-10-02",
                "category": "HVAC",
                "status": "passed",
                "overall_severity": "none",
                "description": "Nominal refrigerant pressures."
            }
        )
        assert create_b.status_code == 201
        report_b = create_b.json()
        report_b_id = report_b["id"]
        assert report_b["user_id"] == user_b_id

        # 3. User A lists reports -> must ONLY see Report A
        list_a = await client.get("/api/reports", headers=headers_a)
        assert list_a.status_code == 200
        data_a = list_a.json()
        assert data_a["total"] == 1
        assert len(data_a["reports"]) == 1
        assert data_a["reports"][0]["id"] == report_a_id
        assert data_a["reports"][0]["title"] == "Alice High-Voltage Switchgear Inspection"

        # 4. User B lists reports -> must ONLY see Report B
        list_b = await client.get("/api/reports", headers=headers_b)
        assert list_b.status_code == 200
        data_b = list_b.json()
        assert data_b["total"] == 1
        assert len(data_b["reports"]) == 1
        assert data_b["reports"][0]["id"] == report_b_id
        assert data_b["reports"][0]["title"] == "Bob HVAC Chiller Efficiency Audit"

        # 5. User A requests Report B by ID -> 404 Not Found
        get_b_by_a = await client.get(f"/api/reports/{report_b_id}", headers=headers_a)
        assert get_b_by_a.status_code == 404

        # 6. User B requests Report A by ID -> 404 Not Found
        get_a_by_b = await client.get(f"/api/reports/{report_a_id}", headers=headers_b)
        assert get_a_by_b.status_code == 404

        # 7. User A attempts to update Report B -> 404 Not Found
        update_b_by_a = await client.put(
            f"/api/reports/{report_b_id}",
            headers=headers_a,
            json={"title": "Hacked Title by Alice"}
        )
        assert update_b_by_a.status_code == 404

        # 8. User B attempts to delete Report A -> 404 Not Found
        delete_a_by_b = await client.delete(f"/api/reports/{report_a_id}", headers=headers_b)
        assert delete_a_by_b.status_code == 404

        # 9. User A executes visual nested query -> matches ONLY Report A
        query_a = await client.post(
            "/api/query",
            headers=headers_a,
            json={
                "match_type": "or",
                "conditions": [
                    {"field": "category", "operator": "equals", "value": "Electrical"},
                    {"field": "category", "operator": "equals", "value": "HVAC"}
                ]
            }
        )
        assert query_a.status_code == 200
        query_a_data = query_a.json()
        assert query_a_data["total_matches"] == 1
        assert query_a_data["matched_reports"][0]["id"] == report_a_id

        # 10. User B executes visual nested query -> matches ONLY Report B
        query_b = await client.post(
            "/api/query",
            headers=headers_b,
            json={
                "match_type": "or",
                "conditions": [
                    {"field": "category", "operator": "equals", "value": "Electrical"},
                    {"field": "category", "operator": "equals", "value": "HVAC"}
                ]
            }
        )
        assert query_b.status_code == 200
        query_b_data = query_b.json()
        assert query_b_data["total_matches"] == 1
        assert query_b_data["matched_reports"][0]["id"] == report_b_id

        # 11. User A checks dashboard statistics -> strictly User A's data
        stats_a = await client.get("/api/stats", headers=headers_a)
        assert stats_a.status_code == 200
        s_a = stats_a.json()
        assert s_a["total_reports"] == 1
        assert s_a["category_distribution"].get("Electrical") == 1
        assert "HVAC" not in s_a["category_distribution"]

        # 12. User B checks dashboard statistics -> strictly User B's data
        stats_b = await client.get("/api/stats", headers=headers_b)
        assert stats_b.status_code == 200
        s_b = stats_b.json()
        assert s_b["total_reports"] == 1
        assert s_b["category_distribution"].get("HVAC") == 1
        assert "Electrical" not in s_b["category_distribution"]

        # User A deletes Report A
        del_a = await client.delete(f"/api/reports/{report_a_id}", headers=headers_a)
        assert del_a.status_code == 200

        # Verify User A now has 0 reports
        list_a_after = await client.get("/api/reports", headers=headers_a)
        assert list_a_after.json()["total"] == 0

        # Verify User B STILL has Report B intact
        list_b_after = await client.get("/api/reports", headers=headers_b)
        assert list_b_after.json()["total"] == 1
        assert list_b_after.json()["reports"][0]["id"] == report_b_id
