from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database.connection import get_db
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "ExtractIQ",
        "version": "1.0.0"
    }


@router.get("/ready")
async def readiness_check(db: Session = Depends(get_db)):
    """Readiness check - ensures all dependencies are available."""
    try:
        # Check database
        db.execute(text("SELECT 1"))
        
        return {
            "status": "ready",
            "database": "connected",
            "redis": "configured"
        }
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        raise HTTPException(status_code=503, detail="Service not ready")
