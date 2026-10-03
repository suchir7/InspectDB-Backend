import logging
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config.settings import settings
from app.db.base import Base
# Import all models to ensure they are registered with Base metadata before create_all
import app.models  # noqa: F401

logger = logging.getLogger("inspectdb.db")

DATABASE_URL = settings.DATABASE_URL.strip() if settings.DATABASE_URL else ""

# If DATABASE_URL is not set or empty, fallback to local sqlite for safety/dev
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./inspectdb_dev.db"
    logger.warning("DATABASE_URL not set. Falling back to local SQLite database.")

# Ensure proper arguments based on database dialect
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a database session and closes it on exit.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """
    Initialize database tables defined in SQLAlchemy models.
    """
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Neon PostgreSQL / Relational Database schema initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing relational database tables: {e}")
        # We don't crash app on startup so other endpoints can still operate if offline
