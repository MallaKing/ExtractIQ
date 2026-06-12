from celery import Celery
from celery.schedules import crontab
from app.config import settings
import logging

logger = logging.getLogger(__name__)

celery_app = Celery(
    "extractiq",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    # Explicitly tell Celery where to find tasks — required on Windows
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,
    task_soft_time_limit=25 * 60,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    worker_pool="threads",  # Windows-compatible

    beat_schedule={
        "cleanup-historical-jobs": {
            "task": "tasks.cleanup_historical_jobs",
            "schedule": crontab(minute=0),
        },
    },
)

logger.info("Celery app configured")
