"""SQLAlchemy models: schemas, extraction jobs, and multi-tenant users."""
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, JSON, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class User(Base):
    """
    Multi-tenant user — one row per Google account that has completed OAuth.
    google_tokens stores the serialized OAuth2 credentials (credentials.to_json()).
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    google_tokens = Column(JSON, nullable=False)        # serialized OAuth2 credentials
    created_at = Column(DateTime, default=datetime.utcnow)

    jobs = relationship("ExtractionJob", back_populates="user", cascade="all, delete-orphan")


class Schema(Base):
    """Schema storage model — scoped per user."""
    __tablename__ = "schemas"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    version = Column(String(50), default="v1")
    schema_json = Column(JSON, nullable=False)
    schema_hash = Column(String(64), nullable=False, index=True)  # no longer globally unique
    description = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # None = global/legacy
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    versions = relationship("SchemaVersion", back_populates="schema", cascade="all, delete-orphan")


class SchemaVersion(Base):
    """Schema version history."""
    __tablename__ = "schema_versions"

    id = Column(Integer, primary_key=True, index=True)
    schema_id = Column(Integer, ForeignKey("schemas.id"), nullable=False)
    version = Column(String(50), nullable=False)
    schema_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    schema = relationship("Schema", back_populates="versions")


class WatchChannel(Base):
    """
    Tracks active Google Drive push notification channels per user per folder.
    Allows listing and stopping watches from the UI.
    """
    __tablename__ = "watch_channels"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    folder_id = Column(String(255), nullable=False)
    folder_name = Column(String(255), nullable=True)
    channel_id = Column(String(36), unique=True, nullable=False)    # UUID sent to Google
    resource_id = Column(String(255), nullable=True)                # Google's resource ID (needed to stop)
    expiration_ms = Column(String(20), nullable=True)               # epoch ms from Google
    created_at = Column(DateTime, default=datetime.utcnow)


class ExtractionJob(Base):
    """
    Tracks async extraction jobs — both REST batch and Google Drive webhook.
    user_id links every Drive-triggered job back to the owning tenant.
    """
    __tablename__ = "extraction_jobs"

    id = Column(String(36), primary_key=True)           # UUID
    status = Column(String(20), nullable=False, default="pending")
    # pending → started → completed | failed | partial

    total = Column(Integer, nullable=False, default=0)
    processed = Column(Integer, nullable=False, default=0)
    succeeded = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)

    schema_json = Column(JSON, nullable=False)
    model = Column(String(100), nullable=True)
    method = Column(String(20), nullable=True, default="llm")

    # Google Drive fields
    file_id = Column(String(255), nullable=True)        # Drive file ID (crash recovery)
    source = Column(String(50), nullable=True, default="api")   # "api" | "google_drive"

    # Multi-tenant ownership — nullable for REST batch jobs (no user login required)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user = relationship("User", back_populates="jobs")

    results = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
