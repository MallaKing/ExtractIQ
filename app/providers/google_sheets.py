"""
Google Sheets provider — multi-tenant, uses per-user OAuth credentials.

Two operations:
  get_or_create_sheet()  — State A: find or create the ExtractIQ_Log sheet in a folder
  append_row()           — State B: append one extracted record as a new row
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SHEET_NAME = "ExtractIQ_Log"


def _get_sheets_service(user):
    """Build a Sheets API client using the user's stored OAuth credentials."""
    from googleapiclient.discovery import build
    from app.providers.google_drive import _credentials_from_user
    creds = _credentials_from_user(user)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _get_drive_service_for_user(user):
    from app.providers.google_drive import get_user_drive_service
    return get_user_drive_service(user)


def get_or_create_sheet(
    user,
    parent_folder_id: str,
    field_names: List[str],
    db,
) -> str:
    """
    State A: Find an existing ExtractIQ_Log sheet in the folder, or create one.
    Returns the spreadsheet_id.

    On creation:
    - Creates the Google Sheet inside parent_folder_id
    - Writes bold header row from field_names + a 'Source File' + 'Processed At' column
    """
    drive = _get_drive_service_for_user(user)

    # Search for existing sheet in this folder
    query = (
        f"'{parent_folder_id}' in parents "
        f"and name='{_SHEET_NAME}' "
        f"and mimeType='application/vnd.google-apps.spreadsheet' "
        f"and trashed=false"
    )
    results = drive.files().list(q=query, fields="files(id,name)").execute()
    files = results.get("files", [])

    if files:
        sheet_id = files[0]["id"]
        logger.info(f"[Sheets] Found existing sheet {sheet_id} in folder {parent_folder_id}")
        return sheet_id

    # Create new sheet via Drive API (so it lands in the right folder)
    file_meta = {
        "name": _SHEET_NAME,
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [parent_folder_id],
    }
    created = drive.files().create(body=file_meta, fields="id").execute()
    sheet_id = created["id"]
    logger.info(f"[Sheets] Created new sheet {sheet_id} in folder {parent_folder_id}")

    # Write bold header row
    headers = field_names + ["Source File", "Processed At"]
    sheets = _get_sheets_service(user)
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()

    # Bold the header row
    _bold_header_row(sheets, sheet_id)

    # Persist sheet_id in Redis so we don't search Drive on every document
    try:
        from app.providers.redis_client import get_redis_client
        redis = get_redis_client()
        redis.set(f"sheets:id:{user.id}:{parent_folder_id}", sheet_id, ex=86400)
    except Exception:
        pass

    return sheet_id


def get_cached_sheet_id(user_id: int, parent_folder_id: str) -> Optional[str]:
    """Return cached sheet_id from Redis to avoid repeated Drive searches."""
    try:
        from app.providers.redis_client import get_redis_client
        return get_redis_client().get(f"sheets:id:{user_id}:{parent_folder_id}")
    except Exception:
        return None


def append_row(
    user,
    sheet_id: str,
    extracted_data: Dict[str, Any],
    field_names: List[str],
    source_file_name: str,
    db,
):
    """
    State B: Append one extracted record as a new row.
    Values are ordered to match the header row created in get_or_create_sheet().
    """
    from datetime import datetime

    row_values = [str(extracted_data.get(f, "")) for f in field_names]
    row_values.append(source_file_name)
    row_values.append(datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))

    sheets = _get_sheets_service(user)
    sheets.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range="Sheet1!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row_values]},
    ).execute()
    logger.info(f"[Sheets] Appended row to sheet {sheet_id} for file '{source_file_name}'")


def _bold_header_row(sheets_service, spreadsheet_id: str):
    """Apply bold formatting to row 1."""
    try:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [{
                    "repeatCell": {
                        "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                }]
            },
        ).execute()
    except Exception as e:
        logger.warning(f"[Sheets] Could not bold header: {e}")
