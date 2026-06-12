"""
OAuth 2.0 Authentication Router — Multi-Tenant Google Drive
GET /api/v1/auth/google/login    → redirect to Google consent screen
GET /api/v1/auth/google/callback → exchange code, upsert User row
"""
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import get_db
from app.schemas.models import User

logger = logging.getLogger(__name__)

# Allow OAuth over non-https for local dev behind ngrok
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

# Add this line right beneath it:
# Tells oauthlib to accept variations or additions in scopes returned by Google
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/userinfo.email",
]
_REDIRECT_URI = f"{settings.ngrok_domain}/api/v1/auth/google/callback"

# Path to OAuth client secret JSON — used in local dev.
# On Render, falls back to building config from env vars.
_CLIENT_SECRET_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "client_secret_81407877676-pn2alfvp8hdlmbkfhfkm2kqnekqmho6r.apps.googleusercontent.com.json",
)


def _build_flow(state: str = None):
    """Build OAuth flow. Works both locally (JSON file) and on Render (env vars)."""
    from google_auth_oauthlib.flow import Flow

    if os.path.exists(_CLIENT_SECRET_FILE):
        # Local dev — use the downloaded JSON file
        return Flow.from_client_secrets_file(
            _CLIENT_SECRET_FILE,
            scopes=_SCOPES,
            redirect_uri=_REDIRECT_URI,
            state=state,
        )
    else:
        # Production (Render) — build config from env vars
        if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
            raise ValueError("GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set")
        client_config = {
            "web": {
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [_REDIRECT_URI],
            }
        }
        return Flow.from_client_config(
            client_config,
            scopes=_SCOPES,
            redirect_uri=_REDIRECT_URI,
            state=state,
        )


@router.get("/google/login", summary="Start Google OAuth flow")
async def google_login():
    from app.providers.redis_client import get_redis_client

    flow = _build_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account consent",
    )

    # PKCE: persist code_verifier keyed by state (10 min TTL).
    # The callback creates a fresh Flow so the verifier must survive across requests.
    verifier = flow.code_verifier
    if verifier:
        redis = get_redis_client()
        redis.set(f"oauth:pkce:{state}", verifier, ex=600)
        logger.debug(f"[auth] Stored PKCE verifier for state={state[:8]}")

    return RedirectResponse(url=auth_url)


@router.get("/google/callback", summary="Google OAuth callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code from Google")

    try:
        from app.providers.redis_client import get_redis_client

        flow = _build_flow(state=state)

        # Restore PKCE verifier — without this fetch_token panics with missing verifier
        if state:
            redis = get_redis_client()
            verifier = redis.get(f"oauth:pkce:{state}")
            if verifier:
                flow.code_verifier = verifier
                redis.delete(f"oauth:pkce:{state}")
                logger.debug(f"[auth] Restored PKCE verifier for state={state[:8]}")
            else:
                logger.warning(f"[auth] No PKCE verifier in Redis for state={state[:8]}")

        # ngrok terminates TLS so request.url arrives as http:// — force https
        callback_url = str(request.url).replace("http://", "https://", 1)
        flow.fetch_token(authorization_response=callback_url)
        credentials = flow.credentials

    except Exception as e:
        logger.error(f"[auth] Token exchange failed: {e}")
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {e}")

    # Fetch email from Google
    try:
        from googleapiclient.discovery import build as g_build
        user_info = g_build("oauth2", "v2", credentials=credentials).userinfo().get().execute()
        email = user_info.get("email")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not retrieve user email: {e}")

    if not email:
        raise HTTPException(status_code=400, detail="Google did not return an email address")

    # Upsert User row
    try:
        token_json = json.loads(credentials.to_json())
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.google_tokens = token_json
        else:
            user = User(email=email, google_tokens=token_json)
            db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"[auth] Authenticated user: {email} id={user.id}")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save user credentials")

    return RedirectResponse(url=f"/dashboard?user_id={user.id}&email={email}&status=connected")


@router.post("/groq-key", summary="Save user's Groq API key")
async def save_groq_key(
    user_id: int,
    groq_api_key: str,
    db: Session = Depends(get_db),
):
    """Store the user's personal Groq API key in their User row."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    tokens = dict(user.google_tokens or {})
    tokens["groq_api_key"] = groq_api_key
    user.google_tokens = tokens
    db.commit()
    return {"status": "saved", "user_id": user_id}


@router.get("/folders", summary="List user's Google Drive folders")
async def list_folders(user_id: int, db: Session = Depends(get_db)):
    """Return the user's Drive folders so the frontend can show a picker."""
    from app.providers.google_drive import get_user_drive_service
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        drive = get_user_drive_service(user)
        db.commit()
        result = drive.files().list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id,name)",
            orderBy="name",
            pageSize=50,
        ).execute()
        return {"folders": result.get("files", [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jobs", summary="List recent jobs for a user")
async def list_jobs(user_id: int, db: Session = Depends(get_db)):
    from app.schemas.models import ExtractionJob
    jobs = (
        db.query(ExtractionJob)
        .filter(ExtractionJob.user_id == user_id)
        .order_by(ExtractionJob.created_at.desc())
        .limit(20)
        .all()
    )
    return {"jobs": [
        {
            "id": j.id,
            "status": j.status,
            "file_id": j.file_id,
            "confidence": (j.results[0].get("confidence") if j.results else None),
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "error": j.error,
        }
        for j in jobs
    ]}


@router.post("/watch", summary="Register a Drive folder watch for a user")
async def register_watch(user_id: int, folder_id: str, folder_name: str = "", db: Session = Depends(get_db)):
    from app.providers.google_drive import watch_user_folder
    try:
        result = watch_user_folder(user_id=user_id, folder_id=folder_id, db=db, folder_name=folder_name)
        return {
            "status": "watching",
            "user_id": user_id,
            "folder_id": folder_id,
            "channel_id": result.get("id"),
            "resource_id": result.get("resourceId"),
            "expiration_ms": result.get("expiration"),
        }
    except Exception as e:
        logger.error(f"[auth] Watch failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/watches", summary="List active folder watches for a user")
async def list_watches(user_id: int, db: Session = Depends(get_db)):
    from app.schemas.models import WatchChannel
    channels = (
        db.query(WatchChannel)
        .filter(WatchChannel.user_id == user_id)
        .order_by(WatchChannel.created_at.desc())
        .all()
    )
    return {"watches": [
        {
            "channel_id": c.channel_id,
            "folder_id": c.folder_id,
            "folder_name": c.folder_name or c.folder_id[:12] + "…",
            "resource_id": c.resource_id,
            "expiration_ms": c.expiration_ms,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in channels
    ]}


@router.delete("/watches/{channel_id}", summary="Stop a folder watch")
async def stop_watch(channel_id: str, user_id: int, db: Session = Depends(get_db)):
    from app.providers.google_drive import stop_watch as _stop
    from app.schemas.models import WatchChannel
    ch = db.query(WatchChannel).filter(
        WatchChannel.channel_id == channel_id,
        WatchChannel.user_id == user_id,
    ).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Watch not found")
    user = db.get(User, user_id)
    _stop(channel_id=channel_id, resource_id=ch.resource_id or "", user=user, db=db)
    return {"status": "stopped", "channel_id": channel_id}
