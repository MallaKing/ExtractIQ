"""
Google Drive provider — OAuth2 only, fully multi-tenant.
Every API call uses the specific user's stored OAuth credentials.
No service account anywhere.
"""
import io
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-user credential builder
# ---------------------------------------------------------------------------

def _credentials_from_user(user) -> Any:
    """
    Reconstruct OAuth2 credentials from the User row's stored google_tokens.
    Auto-refreshes the access token if expired and persists the new token.
    Caller must db.commit() after this to save the refreshed token.
    """
    import google.oauth2.credentials as g_creds
    from google.auth.transport.requests import Request as GRequest
    from app.config import settings

    token_data = user.google_tokens or {}
    creds = g_creds.Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id") or settings.google_oauth_client_id,
        client_secret=token_data.get("client_secret") or settings.google_oauth_client_secret,
        scopes=token_data.get("scopes") or [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ],
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(GRequest())
        try:
            user.google_tokens = json.loads(creds.to_json())
        except Exception as e:
            logger.warning(f"Could not persist refreshed token: {e}")
    return creds


def get_user_drive_service(user):
    """Build a Drive v3 client from a user's OAuth credentials."""
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_credentials_from_user(user), cache_discovery=False)


# ---------------------------------------------------------------------------
# Webhook registration
# ---------------------------------------------------------------------------

def watch_user_folder(user_id: int, folder_id: str, db, folder_name: str = "") -> Dict[str, Any]:
    """
    Register a Drive changes.watch().
    Persists the channel in DB (watch_channels) and Redis.
    """
    import uuid
    from app.schemas.models import User, WatchChannel
    from app.config import settings
    from app.providers.redis_client import get_redis_client

    user = db.get(User, user_id)
    if not user:
        raise ValueError(f"User {user_id} not found")

    drive = get_user_drive_service(user)
    db.commit()

    token_resp = drive.changes().getStartPageToken().execute()
    start_page_token = token_resp.get("startPageToken")

    channel_id = str(uuid.uuid4())
    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": f"{settings.ngrok_domain}/api/v1/webhooks/google-drive",
        "token": f"user_id={user_id}",
    }

    response = drive.changes().watch(
        pageToken=start_page_token,
        body=body,
        includeItemsFromAllDrives=False,
        supportsAllDrives=False,
    ).execute()

    redis = get_redis_client()
    redis.set(f"drive:page_token:{channel_id}", start_page_token)
    redis.set(f"drive:folder:{channel_id}", folder_id)

    # Persist channel record
    ch = WatchChannel(
        user_id=user_id,
        folder_id=folder_id,
        folder_name=folder_name,
        channel_id=channel_id,
        resource_id=response.get("resourceId"),
        expiration_ms=str(response.get("expiration", "")),
    )
    db.add(ch)
    db.commit()

    logger.info(f"[Drive] Watch registered user={user_id} folder={folder_id} channel={channel_id}")
    return response


def stop_watch(channel_id: str, resource_id: str, user, db) -> bool:
    """Stop a Drive channel. Calls Google API, cleans Redis and DB."""
    from app.schemas.models import WatchChannel
    from app.providers.redis_client import get_redis_client

    try:
        drive = get_user_drive_service(user)
        drive.channels().stop(body={"id": channel_id, "resourceId": resource_id}).execute()
        logger.info(f"[Drive] Stopped channel {channel_id}")
    except Exception as e:
        logger.warning(f"[Drive] Google stop failed (may be expired): {e}")

    redis = get_redis_client()
    redis.delete(f"drive:page_token:{channel_id}")
    redis.delete(f"drive:folder:{channel_id}")

    ch = db.query(WatchChannel).filter(WatchChannel.channel_id == channel_id).first()
    if ch:
        db.delete(ch)
        db.commit()
    return True


# ---------------------------------------------------------------------------
# File operations — all require a drive client (user OAuth)
# ---------------------------------------------------------------------------

def download_file_to_memory(file_id: str, drive) -> bytes:
    """Stream a Drive file into RAM using the user's Drive client. No disk writes."""
    from googleapiclient.http import MediaIoBaseDownload
    request = drive.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buffer.seek(0)
    return buffer.read()


def get_file_metadata(file_id: str, drive) -> Dict[str, Any]:
    """Fetch file name, parents, mimeType using the user's Drive client."""
    return drive.files().get(
        fileId=file_id,
        fields="id,name,parents,mimeType",
    ).execute()


def get_folder_name(folder_id: str, drive) -> str:
    """Fetch a folder's display name using the user's Drive client."""
    meta = drive.files().get(fileId=folder_id, fields="name").execute()
    return meta.get("name", "")


def save_json_to_drive(parent_folder_id: str, filename: str, payload: dict, drive) -> str:
    """Create a JSON file in Drive using the user's Drive client. Returns new file ID."""
    from googleapiclient.http import MediaIoBaseUpload
    content = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    buffer = io.BytesIO(content)
    file_metadata = {
        "name": filename,
        "parents": [parent_folder_id],
        "mimeType": "application/json",
    }
    media = MediaIoBaseUpload(buffer, mimetype="application/json", resumable=False)
    created = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
    ).execute()
    return created.get("id", "")
