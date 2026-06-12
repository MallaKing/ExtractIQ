from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # App
    app_name: str = "ExtractIQ"
    debug: bool = False
    app_env: str = "development"
    log_level: str = "INFO"

    # Groq API
    groq_api_key: str

    # Database
    database_url: str

    # Redis
    redis_url: str

    # Celery
    celery_broker_url: str
    celery_result_backend: str

    # Google Drive (Service Account — for legacy single-tenant use)
    google_service_account_json: str = ""
    google_drive_webhook_token: str = ""

    # Google OAuth 2.0 (multi-tenant Web App credentials)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    # Ngrok tunnel domain (used as OAuth redirect base + webhook address)
    ngrok_domain: str = "https://perjury-oxidant-visibly.ngrok-free.dev"

    # Job retention
    job_retention_hours: int = 24

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
