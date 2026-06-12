from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings
import logging

# Import Base and models to register them
from app.schemas.models import Base, Schema, SchemaVersion, ExtractionJob, User, WatchChannel

logger = logging.getLogger(__name__)

# Render provides postgres:// — SQLAlchemy 2.x requires postgresql://
_db_url = settings.database_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    _db_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """Dependency for getting database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise
