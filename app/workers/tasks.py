"""
Celery tasks for ExtractIQ.

Module B: process_google_webhook_file  — core extraction worker
Module C: save_output_to_drive         — offloaded Drive upload
Module D: cleanup_historical_jobs      — Celery Beat data retention cleaner
           (also contains extract_batch for the REST batch API)
Module E: recover_jobs_after_crash     — called from FastAPI lifespan
"""
import io
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db():
    """Open a raw SQLAlchemy session (for use inside Celery workers)."""
    from app.database.connection import SessionLocal
    return SessionLocal()


# ---------------------------------------------------------------------------
# Module B: Core Extraction Worker
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=3, name="tasks.process_google_webhook_file")
def process_google_webhook_file(self, job_id: str, file_id: str, user_id: int):
    """
    Processes a single Google Drive file through the full extraction pipeline.
    Uses the owning user's personal OAuth credentials for all Drive API calls.

    Steps:
      1. Load user + build their personal Drive client
      2. Fetch file metadata + parent folder name
         -> GUARD: Exit early if file is a native Google Sheet to prevent binary download crashes.
         -> GUARD: Exit early if folder layout is unmonitored ('My Drive') to handle ghost channels.
      3. Parse schema_id from folder name  ("Invoices_sch_99" → 99)
      4. Stream file bytes into RAM — no disk writes
      5. Run extraction + grounding pipeline
      6. Commit results to ExtractionJob
      7. Fire-and-forget: offload JSON upload back to Drive
    """
    import re
    from app.schemas.models import ExtractionJob, Schema as SchemaModel, User
    from app.providers.google_drive import (
        get_user_drive_service,
        get_file_metadata,
        get_folder_name,
        download_file_to_memory,
    )
    from app.extraction.structured_output import StructuredOutputEngine
    from app.parser.document_parser import parse_document

    db = _get_db()
    try:
        # ── 1. Mark job started ─────────────────────────────────────────────
        job = db.get(ExtractionJob, job_id)
        if not job:
            logger.error(f"[B] Job {job_id} not found — skipping")
            return
        job.status = "started"
        db.commit()

        # ── 2. Load user + build Drive client ───────────────────────────────
        user = db.get(User, user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")
        drive = get_user_drive_service(user)
        db.commit()  # persist any refreshed token

        # ── 3. Resolve file metadata + parent folder name ───────────────────
        file_meta = get_file_metadata(file_id, drive=drive)
        file_name = file_meta.get("name", "unknown")
        incoming_mime = file_meta.get("mimeType", "")
        parents = file_meta.get("parents", [])
        parent_folder_id = parents[0] if parents else None

        # ── GUARD CLAUSE: Ignore log sheets & spreadsheet exports ───────────
        if incoming_mime == "application/vnd.google-apps.spreadsheet" or "ExtractIQ_Log" in file_name:
            logger.info(f"[Webhook Guard] job={job_id} skipping native log sheet file: '{file_name}'")
            job.status = "skipped"
            job.error = "Ignored native Google Sheet file notification"
            job.completed_at = datetime.utcnow()
            db.commit()
            return {"status": "skipped", "reason": "native_google_sheet"}
        # ────────────────────────────────────────────────────────────────────

        folder_name = get_folder_name(parent_folder_id, drive=drive) if parent_folder_id else ""
        logger.info(f"[B] job={job_id} user={user_id} file={file_name} folder={folder_name}")

        # ── NEW GUARD CLAUSE: Silently ignore unmonitored folder scopes ───
        if not folder_name or folder_name == "My Drive" or "_sch_" not in folder_name:
            logger.info(f"[Webhook Folder Guard] job={job_id} skipping file from unmonitored location: '{folder_name}'")
            job.status = "skipped"
            job.error = f"Ignored file transaction processed from unmonitored layout: {folder_name}"
            job.completed_at = datetime.utcnow()
            db.commit()
            return {"status": "skipped", "reason": f"unmonitored_folder: {folder_name}"}
        # ────────────────────────────────────────────────────────────────────

        # ── 4. Parse schema_id from folder name ─────────────────────────────
        match = re.search(r"sch_(\d+)$", folder_name)
        if not match:
            raise ValueError(
                f"Folder '{folder_name}' has no schema ID suffix (expected '_sch_<N>')"
            )
        schema_id = int(match.group(1))
        schema_row = db.get(SchemaModel, schema_id)
        if not schema_row:
            raise ValueError(f"Schema {schema_id} not found in database")
        schema_dict = schema_row.schema_json

        # ── 5. Stream file into RAM ─────────────────────────────────────────
        file_bytes = download_file_to_memory(file_id, drive=drive)
        file_ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "txt"
        document_text = parse_document(file_bytes, file_ext)

        # ── 6. Run extraction pipeline (BYOK — use user's Groq key if set) ──
        user_groq_key = (user.google_tokens or {}).get("groq_api_key")
        engine = StructuredOutputEngine(groq_api_key=user_groq_key)
        result = engine.extract(
            document_text=document_text,
            schema=schema_dict,
            model=job.model or "llama-3.3-70b-versatile",
        )
        extracted_data = result.get("data") or {}

        # ── 7. Commit results ───────────────────────────────────────────────
        job.processed = 1
        job.results = [result]
        job.schema_json = schema_dict
        if result.get("success"):
            job.status = "completed"
            job.succeeded = 1
        else:
            job.status = "failed"
            job.failed = 1
            job.error = result.get("error")
        job.completed_at = datetime.utcnow()
        db.commit()
        logger.info(f"[B] job={job_id} status={job.status}")

        # ── 8. Fire-and-forget: append row to Google Sheet ─────────────────
        if parent_folder_id and extracted_data:
            append_to_sheet.delay(
                parent_folder_id=parent_folder_id,
                file_name=file_name,
                extracted_data=extracted_data,
                field_names=list(schema_dict.get("fields", {}).keys()),
                user_id=user_id,
            )

    except Exception as e:
        logger.error(f"[B] job={job_id} error: {e}")
        try:
            job = db.get(ExtractionJob, job_id)
            if job:
                job.status = "failed"
                job.error = str(e)
                job.completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Module C: Offloaded Google Sheets Writer
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=3, name="tasks.append_to_sheet")
def append_to_sheet(
    self,
    parent_folder_id: str,
    file_name: str,
    extracted_data: dict,
    field_names: list,
    user_id: int,
):
    """
    State A: Find or create ExtractIQ_Log sheet in the folder.
    State B: Append extracted data as a new row.
    Runs completely independently of the extraction queue.
    """
    from app.schemas.models import User
    from app.providers.google_sheets import get_or_create_sheet, get_cached_sheet_id, append_row

    db = _get_db()
    try:
        user = db.get(User, user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        # Check Redis cache first to avoid Drive search on every document
        sheet_id = get_cached_sheet_id(user_id, parent_folder_id)
        if not sheet_id:
            sheet_id = get_or_create_sheet(
                user=user,
                parent_folder_id=parent_folder_id,
                field_names=field_names,
                db=db,
            )
            db.commit()  # persist any refreshed token

        append_row(
            user=user,
            sheet_id=sheet_id,
            extracted_data=extracted_data,
            field_names=field_names,
            source_file_name=file_name,
            db=db,
        )
        logger.info(f"[C] Row appended to sheet {sheet_id} for '{file_name}' user={user_id}")
        return {"sheet_id": sheet_id, "file": file_name}

    except Exception as e:
        logger.error(f"[C] Sheet append failed for '{file_name}': {e}")
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Backward-compat stub — handles any save_output_to_drive messages still in
# Redis from before the rename. Logs and discards gracefully.
# ---------------------------------------------------------------------------

@celery_app.task(name="tasks.save_output_to_drive")
def save_output_to_drive(**kwargs):
    logger.warning("[legacy] Received deprecated save_output_to_drive task — discarding.")


# ---------------------------------------------------------------------------
# Module D: Celery Beat Data Retention Cleaner
# ---------------------------------------------------------------------------

@celery_app.task(name="tasks.cleanup_historical_jobs")
def cleanup_historical_jobs():
    """
    Scheduled task (Celery Beat) — runs every hour.
    Deletes completed/failed/partial ExtractionJob rows older than
    JOB_RETENTION_HOURS (default 24h) to prevent Postgres table bloat.
    """
    from app.schemas.models import ExtractionJob
    from app.config import settings

    db = _get_db()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=settings.job_retention_hours)
        expired_statuses = ["completed", "failed", "partial"]

        deleted = (
            db.query(ExtractionJob)
            .filter(
                ExtractionJob.status.in_(expired_statuses),
                ExtractionJob.created_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        logger.info(f"[D] Cleanup: deleted {deleted} expired jobs (cutoff={cutoff.isoformat()})")
        return {"deleted": deleted}
    except Exception as e:
        db.rollback()
        logger.error(f"[D] Cleanup failed: {e}")
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# REST Batch Extraction Task (existing — unchanged)
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=3, name="tasks.extract_batch")
def extract_batch(self, job_id: str, documents: List[str], schema_json: Dict[str, Any], model: str, method: str):
    """
    Process a REST-submitted batch extraction job.
    """
    from app.schemas.models import ExtractionJob
    from app.extraction.structured_output import StructuredOutputEngine

    db = _get_db()
    engine = StructuredOutputEngine()

    try:
        job = db.get(ExtractionJob, job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        job.status = "started"
        db.commit()

        results: List[Dict[str, Any]] = []
        succeeded = 0
        failed = 0

        for i, document_text in enumerate(documents):
            try:
                if method == "regex":
                    result = engine.extract_regex(document_text, schema_json)
                else:
                    result = engine.extract(document_text, schema_json, model)

                results.append({
                    "index": i,
                    "success": result["success"],
                    "data": result.get("data"),
                    "confidence": result.get("confidence"),
                    "error": result.get("error"),
                })
                if result["success"]:
                    succeeded += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Document {i} in job {job_id} failed: {e}")
                results.append({"index": i, "success": False, "error": str(e)})
                failed += 1

            job.processed = i + 1
            job.succeeded = succeeded
            job.failed = failed
            job.results = results
            db.commit()

        job.status = "completed" if failed == 0 else ("failed" if succeeded == 0 else "partial")
        job.completed_at = datetime.utcnow()
        db.commit()
        return {"job_id": job_id, "status": job.status}

    except Exception as e:
        try:
            job = db.get(ExtractionJob, job_id)
            if job:
                job.status = "failed"
                job.error = str(e)
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Module E: Crash Recovery (called from FastAPI lifespan — not a Celery task)
# ---------------------------------------------------------------------------

def recover_jobs_after_crash():
    """
    Re-enqueues any jobs stuck in 'pending' or 'started' state.
    Called once at application startup inside the FastAPI lifespan.

    Handles two job sources:
    - Google Drive jobs (have file_id) → re-dispatched to process_google_webhook_file
    - REST batch jobs  (no file_id)    → logged only (documents no longer in memory)
    """
    from app.schemas.models import ExtractionJob

    db = _get_db()
    try:
        stuck = (
            db.query(ExtractionJob)
            .filter(ExtractionJob.status.in_(["pending", "started"]))
            .all()
        )

        if not stuck:
            logger.info("[E] Crash recovery: no stuck jobs found")
            return

        logger.warning(f"[E] Crash recovery: found {len(stuck)} stuck jobs — re-enqueuing")

        for job in stuck:
            job.status = "pending"
            db.commit()

            if job.source == "google_drive" and job.file_id:
                process_google_webhook_file.delay(
                    job_id=str(job.id),
                    file_id=job.file_id,
                    user_id=job.user_id,
                )
                logger.info(f"[E] Re-enqueued Drive job {job.id} file={job.file_id} user={job.user_id}")
            else:
                # REST batch jobs can't be recovered without the original documents
                job.status = "failed"
                job.error = "Job interrupted during server restart — documents not recoverable"
                db.commit()
                logger.warning(f"[E] REST batch job {job.id} marked failed — cannot recover documents")

    except Exception as e:
        logger.error(f"[E] Crash recovery error: {e}")
    finally:
        db.close()
