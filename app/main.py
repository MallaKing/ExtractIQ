from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import logging
import os

from app.config import settings
from app.database.connection import init_db, get_db
from app.providers.groq_provider import get_groq_provider
from app.providers.redis_client import get_redis_client
from app.api import health, schemas, extraction, evaluation, benchmarking, webhooks, auth

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    logger.info("Starting ExtractIQ application")
    try:
        init_db()
        # Run migrations (idempotent — safe to run on every startup)
        try:
            from patch_db import run as run_migrations
            run_migrations()
        except Exception as e:
            logger.warning(f"Migration warning (non-fatal): {e}")
        groq = get_groq_provider()
        redis = get_redis_client()

        logger.info(f"Groq models available: {groq.get_models()}")
        logger.info(f"Redis health: {redis.health_check()}")

        # Module E: re-enqueue any jobs stuck from a previous crash
        from app.workers.tasks import recover_jobs_after_crash
        recover_jobs_after_crash()
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down ExtractIQ application")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="Universal Information Extraction Platform",
    version="1.0.0",
    lifespan=lifespan,
    debug=settings.debug,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router)
app.include_router(schemas.router)
app.include_router(extraction.router)
app.include_router(evaluation.router)
app.include_router(benchmarking.router)
app.include_router(webhooks.router)
app.include_router(auth.router)

# Serve frontend
_frontend = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(_frontend):
    app.mount("/static", StaticFiles(directory=_frontend), name="static")

@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse(os.path.join(_frontend, "index.html"))





# Root endpoint
@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Welcome to ExtractIQ",
        "service": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "status": "operational"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )
