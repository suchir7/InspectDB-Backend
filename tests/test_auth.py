import pytest
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.base import Base
from app.db.session import get_db
from app.core.security import create_access_token

# Setup in-memory SQLite database for isolated unit testing
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
def setup_database():
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)

@pytest.fixture
def client():
    return TestClient(app)

def test_successful_registration(client):
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Jane Doe",
            "email": "Jane.Doe@EXAMPLE.com",
            "password": "SecurePass123!"
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["name"] == "Jane Doe"
    assert data["user"]["email"] == "jane.doe@example.com"
    assert data["user"]["role"] == "USER"
    assert "password" not in data["user"]
    assert "password_hash" not in data["user"]

def test_duplicate_email_registration(client):
    payload = {
        "name": "First User",
        "email": "duplicate@example.com",
        "password": "Password123!"
    }
    res1 = client.post("/api/auth/register", json=payload)
    assert res1.status_code == 201

    # Attempt to register identical email with different casing
    res2 = client.post(
        "/api/auth/register",
        json={
            "name": "Second User",
            "email": "DUPLICATE@example.com",
            "password": "AnotherPassword123!"
        }
    )
    assert res2.status_code == 400
    assert "already exists" in res2.json()["detail"].lower()

def test_invalid_email_format(client):
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Invalid Email",
            "email": "not-an-email",
            "password": "Password123!"
        }
    )
    assert response.status_code == 422

def test_weak_password_rejection(client):
    # Too short
    res_short = client.post(
        "/api/auth/register",
        json={
            "name": "Short Pass",
            "email": "short@example.com",
            "password": "p1"
        }
    )
    assert res_short.status_code == 422

    # No digits
    res_no_digits = client.post(
        "/api/auth/register",
        json={
            "name": "No Digits",
            "email": "nodigits@example.com",
            "password": "onlylettershere"
        }
    )
    assert res_no_digits.status_code == 422

def test_successful_login(client):
    # Register first
    client.post(
        "/api/auth/register",
        json={
            "name": "Login User",
            "email": "login@example.com",
            "password": "ValidPassword123!"
        }
    )

    # Login
    response = client.post(
        "/api/auth/login",
        json={
            "email": "LOGIN@example.com",
            "password": "ValidPassword123!"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "login@example.com"

def test_wrong_password_login(client):
    client.post(
        "/api/auth/register",
        json={
            "name": "User",
            "email": "test@example.com",
            "password": "ValidPassword123!"
        }
    )

    response = client.post(
        "/api/auth/login",
        json={
            "email": "test@example.com",
            "password": "WrongPassword999!"
        }
    )
    assert response.status_code == 401
    assert "invalid email or password" in response.json()["detail"].lower()

def test_nonexistent_user_login(client):
    response = client.post(
        "/api/auth/login",
        json={
            "email": "ghost@example.com",
            "password": "ValidPassword123!"
        }
    )
    assert response.status_code == 401

def test_get_current_user_me(client):
    reg = client.post(
        "/api/auth/register",
        json={
            "name": "Profile User",
            "email": "profile@example.com",
            "password": "ValidPassword123!"
        }
    ).json()
    token = reg["access_token"]

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    profile = response.json()
    assert profile["id"] == reg["user"]["id"]
    assert profile["email"] == "profile@example.com"
    assert profile["role"] == "USER"
    assert "password_hash" not in profile

def test_missing_jwt(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401

def test_invalid_jwt(client):
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer invalid.token.string"}
    )
    assert response.status_code == 401

def test_expired_jwt(client):
    reg = client.post(
        "/api/auth/register",
        json={
            "name": "Expire User",
            "email": "expire@example.com",
            "password": "ValidPassword123!"
        }
    ).json()
    user_id = reg["user"]["id"]

    # Generate an already expired token
    expired_token = create_access_token(
        subject=user_id,
        expires_delta=timedelta(seconds=-60)
    )

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401

def test_logout_endpoint(client):
    reg = client.post(
        "/api/auth/register",
        json={
            "name": "Logout User",
            "email": "logout@example.com",
            "password": "ValidPassword123!"
        }
    ).json()
    token = reg["access_token"]

    response = client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert "invalidated" in response.json()["message"].lower()
