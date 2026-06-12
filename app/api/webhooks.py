"""
Multi-Tenant Async Webhook Receiver
POST /api/v1/webhooks/google-drive
"""
import logging
import re
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.schemas.models import ExtractionJob
from app.providers.redis_client import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

_PAGE_TOKEN_KEY = "drive:page_token:{channel_id}"
_FOLDER_KEY     = "drive:folder:{channel_id}"


def _parse_user_id(token: Optional[str]) -> Optional[int]:
    if not token:
        return None
    match = re.search(r"user_id=(\d+)", token)
    return int(match.group(1)) if match else None


async def _process_drive_changes(
    user_id: int,
    channel_id: str,
    db: Session,
) -> None:
    """
    Runs AFTER 200 OK is sent to Google.

    1. Look up the watched folder_id from Redis (stored at watch registration).
    2. Fetch the changes feed delta using the stored page token.
    3. Filter: only files inside the watched folder, not trashed, not Workspace native, not JSON.
    4. Dedup via Redis to absorb Google's multiple-notification-per-upload behaviour.
    5. Create ExtractionJob + dispatch Celery task.
    6. Save new page token.
    """
    from app.workers.tasks import process_google_webhook_file
    from app.schemas.models import User
    from app.providers.google_drive import get_user_drive_service

    redis = get_redis_client()

    # Retrieve the folder_id this channel is watching
    folder_id = redis.get(_FOLDER_KEY.format(channel_id=channel_id))
    if not folder_id:
        logger.warning(f"[webhook] No folder_id found in Redis for channel={channel_id} — skipping")
        return

    page_token = redis.get(_PAGE_TOKEN_KEY.format(channel_id=channel_id)) or "1"

    # Build per-user Drive client
    try:
        user = db.get(User, user_id)
        if not user:
            logger.error(f"[webhook] user_id={user_id} not found")
            return
        drive = get_user_drive_service(user)
        db.commit()  # persist any refreshed token
    except Exception as e:
        logger.error(f"[webhook] Could not build Drive client for user {user_id}: {e}")
        return

    # Fetch changes since last page token
    try:
        response = drive.changes().list(
            pageToken=page_token,
            fields="newStartPageToken,changes(fileId,file(name,mimeType,parents,trashed))",
            includeItemsFromAllDrives=False,
            supportsAllDrives=False,
        ).execute()
    except Exception as e:
        logger.error(f"[webhook] Changes fetch failed channel={channel_id}: {e}")
        return

    changes   = response.get("changes", [])
    new_token = response.get("newStartPageToken")
    spawned   = 0

    for change in changes:
        file_info = change.get("file") or {}
        file_id   = change.get("fileId")

        if not file_id or file_info.get("trashed"):
            continue

        # Only process files inside the watched folder
        parents = file_info.get("parents") or []
        if folder_id not in parents:
            continue

        mime_type = file_info.get("mimeType", "")
        file_name = file_info.get("name", "")

        # Skip Google Workspace native docs (no binary stream to extract from)
        if mime_type.startswith("application/vnd.google-apps."):
            logger.debug(f"[webhook] Skipping Workspace file: {file_name} ({mime_type})")
            continue

        # Skip our own JSON output files
        if file_name.endswith(".json"):
            logger.debug(f"[webhook] Skipping JSON output: {file_name}")
            continue

        # Deduplication — Google sends multiple notifications per upload
        dedup_key = f"drive:processed:{user_id}:{file_id}"
        if redis.exists(dedup_key):
            logger.info(f"[webhook] Duplicate notification skipped file={file_id}")
            continue
        redis.set(dedup_key, "1", ex=300)

        # Create job row and dispatch
        job_id = str(uuid.uuid4())
        try:
            job = ExtractionJob(
                id=job_id,
                status="pending",
                total=1,
                processed=0,
                succeeded=0,
                failed=0,
                schema_json={},
                file_id=file_id,
                source="google_drive",
                user_id=user_id,
                model="llama-3.3-70b-versatile",
                method="llm",
            )
            db.add(job)
            db.commit()

            process_google_webhook_file.delay(
                job_id=job_id,
                file_id=file_id,
                user_id=user_id,
            )
            spawned += 1
            logger.info(f"[webhook] user={user_id} job={job_id} file={file_name}")

        except Exception as e:
            db.rollback()
            logger.error(f"[webhook] Failed to create job for file {file_id}: {e}")

    # Advance page token so next notification picks up from here
    if new_token:
        redis.set(_PAGE_TOKEN_KEY.format(channel_id=channel_id), new_token)

    logger.info(f"[webhook] channel={channel_id} changes={len(changes)} spawned={spawned}")


@router.post(
    "/google-drive",
    status_code=200,
    summary="Google Drive push notification receiver (multi-tenant)",
)
async def google_drive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_goog_channel_id: Optional[str]    = Header(None),
    x_goog_resource_id: Optional[str]   = Header(None),
    x_goog_channel_token: Optional[str] = Header(None),
    x_goog_resource_state: Optional[str]= Header(None),
):
    # Initial handshake — Google fires this once when the watch is registered
    if x_goog_resource_state == "sync":
        logger.info(f"[webhook] Sync handshake channel={x_goog_channel_id}")
        return {"status": "acknowledged", "state": "sync"}

    if not x_goog_channel_id:
        raise HTTPException(status_code=400, detail="Missing X-Goog-Channel-ID")

    user_id = _parse_user_id(x_goog_channel_token)
    if user_id is None:
        logger.warning(f"[webhook] Unroutable token='{x_goog_channel_token}' channel={x_goog_channel_id}")
        return {"status": "acknowledged", "warning": "unidentified channel token"}

    # Return 200 immediately — all work happens after
    background_tasks.add_task(
        _process_drive_changes,
        user_id=user_id,
        channel_id=x_goog_channel_id,
        db=db,
    )

    return {"status": "acknowledged"}
