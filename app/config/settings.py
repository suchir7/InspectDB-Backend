import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings

# Absolute path to backend/.env
ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"

class Settings(BaseSettings):
    PROJECT_NAME: str = "InspectDB - Inspection Report Management System"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"
    ENVIRONMENT: str = "development"
    
    # Neon PostgreSQL Relational Database Configuration (users & authentication)
    DATABASE_URL: str = ""
    
    # JWT Authentication Configuration
    JWT_SECRET: str = "inspectdb-neon-jwt-secret-key-development-2026"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 Hours
    
    # Gemini AI Configuration
    GEMINI_API_KEY: str = ""
    GEMINI_BACKUP_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "*"
    ]
    
    # Storage Mode for inspection documents:
    #   "memory"     - in-process repository (no database; data is lost on restart)
    #   "mongodb"    - local MongoDB for development (MONGODB_* settings)
    #   "documentdb" - Amazon DocumentDB cluster for production (DOCUMENTDB_* settings)
    STORAGE_MODE: str = "memory"
    
    # Local MongoDB Configuration (For Local Development & Query Execution Testing)
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DATABASE: str = "inspectdb"
    MONGODB_COLLECTION: str = "inspection_reports"
    MAX_QUERY_RESULTS: int = 20
    MONGODB_TIMEOUT_MS: int = 2000
    
    # Amazon DocumentDB Configuration (used when STORAGE_MODE=documentdb)
    # e.g. mongodb://my-cluster.cluster-abc123.us-east-1.docdb.amazonaws.com:27017/?tls=true&replicaSet=rs0&retryWrites=false
    DOCUMENTDB_URI: str = ""
    # Optional credentials; when set they override any user:password in DOCUMENTDB_URI (no URL-encoding needed)
    DOCUMENTDB_USERNAME: str = ""
    DOCUMENTDB_PASSWORD: str = ""
    DOCUMENTDB_DATABASE: str = "inspectdb"
    DOCUMENTDB_COLLECTION: str = "inspection_reports"
    DOCUMENTDB_TLS: bool = True
    # Amazon RDS/DocumentDB CA bundle; relative paths resolve against the backend directory
    DOCUMENTDB_TLS_CA_FILE: str = "global-bundle.pem"
    DOCUMENTDB_TIMEOUT_MS: int = 5000
    DOCUMENTDB_TARGET_VERSION: str = "5.0"  # Supported: "3.6", "4.0", "5.0", "8.0"

    # Live AWS cost & usage data for the cost pages (read-only; needs an IAM role on the API server)
    AWS_INSIGHTS_ENABLED: bool = False
    AWS_REGION: str = "us-east-1"
    DOCUMENTDB_CLUSTER_ID: str = ""
    # Shared cache directory for AWS responses (empty = system temp dir)
    INSIGHTS_CACHE_DIR: str = ""
    # Comma-separated emails allowed to view account cost data (empty = every signed-in user)
    INSIGHTS_ALLOWED_EMAILS: str = ""
    
    class Config:
        env_file = str(ENV_PATH)
        extra = "ignore"

    def get_gemini_api_keys(self) -> List[str]:
        keys = []
        if self.GEMINI_API_KEY and self.GEMINI_API_KEY.strip():
            keys.append(self.GEMINI_API_KEY.strip())
        if self.GEMINI_BACKUP_API_KEY and self.GEMINI_BACKUP_API_KEY.strip():
            keys.append(self.GEMINI_BACKUP_API_KEY.strip())
        return keys

settings = Settings()
