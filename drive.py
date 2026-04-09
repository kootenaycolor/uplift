# ── Shared upload engine ──────────────────────────────────────────────────────
# This file is intentionally kept in sync with drive-uploader/drive.py.
# When making changes, update both copies.
# ─────────────────────────────────────────────────────────────────────────────

"""Google Drive API helpers for export-watcher.

Handles OAuth, folder listing, and the resumable upload engine with:
  - 25 MB chunks (Frame.io-inspired sizing for fewer round-trips)
  - ProgressFileWrapper for sub-chunk progress updates
  - Session restore via resumable_uri (survives process restarts)
  - Server-side progress query to handle mid-chunk crashes correctly
"""

import io
import mimetypes
import os
import socket
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
CHUNK_SIZE = 25 * 1024 * 1024  # 25 MB — must be a multiple of 256 KB

_HERE = Path(__file__).parent
CREDENTIALS_PATH = _HERE / "credentials.json"
TOKEN_PATH = _HERE / "token.json"


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_service(token_path_override=None, credentials_path_override=None):
    """Return an authenticated Drive service. Runs OAuth flow on first use.

    Pass token_path_override / credentials_path_override to use a non-default
    account (e.g. from drive_accounts). Omit both to use the app's own token.json.
    """
    _tp = Path(token_path_override) if token_path_override else TOKEN_PATH
    _cp = Path(credentials_path_override) if credentials_path_override else CREDENTIALS_PATH

    creds = None
    if _tp.exists():
        creds = Credentials.from_authorized_user_file(str(_tp), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not _cp.exists():
                raise FileNotFoundError(
                    f"credentials.json not found at {_cp}.\n"
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(_cp), SCOPES)
            creds = flow.run_local_server(port=0)

        _tp.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def build_thread_service(token_path_override=None):
    """Return a NEW Drive service with its own HTTP connection.

    Each upload worker thread MUST call this to get an independent service.
    httplib2's HTTP object is not thread-safe — sharing a single service across
    concurrent upload threads corrupts OpenSSL's internal buffers, which
    macOS 26's xzone malloc allocator detects as memory corruption.

    Pass token_path_override to use a non-default account.
    """
    _tp = Path(token_path_override) if token_path_override else TOKEN_PATH
    creds = Credentials.from_authorized_user_file(str(_tp), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _tp.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


# ── Folder listing ────────────────────────────────────────────────────────────

def list_folders(service):
    """
    Return [{id, name, path, drive_name, drive_id, parent_id}] for My Drive
    and all Shared Drives the user has access to.

    drive_id:  "my_drive" for My Drive folders, or the Shared Drive's GUID.
    parent_id: the folder's immediate parent ID, normalized so that root-level
               folders (whose parent is not another folder in the list) get
               parent_id == drive_id (the sentinel).
    """
    folders = []

    # ── My Drive folders ──────────────────────────────────────────────────────
    my_drive_raw = []
    page_token = None
    while True:
        resp = service.files().list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces="drive",
            fields="nextPageToken, files(id, name, parents)",
            pageToken=page_token,
            pageSize=1000,
        ).execute()
        my_drive_raw.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Normalize parent_id: if parent is not another My Drive folder → it's root
    my_drive_ids = {f["id"] for f in my_drive_raw}
    for f in my_drive_raw:
        raw_parent = (f.get("parents") or [None])[0]
        parent_id = raw_parent if raw_parent in my_drive_ids else "my_drive"
        folders.append({
            "id": f["id"],
            "name": f["name"],
            "path": f["name"],
            "drive_name": "My Drive",
            "drive_id": "my_drive",
            "parent_id": parent_id,
        })

    # ── Shared Drives ─────────────────────────────────────────────────────────
    try:
        sd_resp = service.drives().list(pageSize=50).execute()
        for drive in sd_resp.get("drives", []):
            drive_id = drive["id"]
            drive_name = drive["name"]
            drive_raw = []
            page_token = None
            while True:
                resp = service.files().list(
                    q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                    spaces="drive",
                    corpora="drive",
                    driveId=drive_id,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    fields="nextPageToken, files(id, name, parents)",
                    pageToken=page_token,
                    pageSize=1000,
                ).execute()
                drive_raw.extend(resp.get("files", []))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            # Normalize parent_id for this shared drive
            drive_folder_ids = {f["id"] for f in drive_raw}
            for f in drive_raw:
                raw_parent = (f.get("parents") or [None])[0]
                parent_id = raw_parent if raw_parent in drive_folder_ids else drive_id
                folders.append({
                    "id": f["id"],
                    "name": f["name"],
                    "path": f["name"],
                    "drive_name": drive_name,
                    "drive_id": drive_id,
                    "parent_id": parent_id,
                })
    except Exception:
        pass  # No Shared Drive access — fine

    folders.sort(key=lambda x: (x["drive_name"] == "My Drive", x["drive_name"], x["name"].lower()))
    return folders


# ── Progress-tracking file wrapper ────────────────────────────────────────────

class StopRequested(Exception):
    """Raised from inside ProgressFileWrapper when a stop event is set.

    Propagates out of next_chunk() immediately, interrupting a mid-chunk upload
    without waiting for the full 25 MB chunk to finish sending.
    """


class ProgressFileWrapper(io.RawIOBase):
    """Wraps a file, intercepting read() calls to report sub-chunk progress.

    This decouples UI update frequency from chunk size: even with 25 MB chunks,
    the progress bar moves as bytes are read from disk (which closely tracks
    bytes sent over the network).

    If stop_event is provided, raises StopRequested as soon as it is set,
    which propagates out of next_chunk() and allows immediate cancellation.
    """

    def __init__(self, file_path: str, progress_callback, stop_event=None):
        super().__init__()
        self._file = open(file_path, "rb")
        self._bytes_read = 0
        self._callback = progress_callback
        self._stop_event = stop_event

    # Cap each read so the stop-event check fires at least every 256 KB,
    # giving sub-second cancellation response even on fast local storage.
    _READ_CAP = 256 * 1024

    def _check_stop(self):
        if self._stop_event and self._stop_event.is_set():
            raise StopRequested()

    def read(self, n=-1):
        self._check_stop()
        if n < 0 or n > self._READ_CAP:
            n = self._READ_CAP
        data = self._file.read(n)
        if data:
            self._bytes_read += len(data)
            self._callback(self._bytes_read)
        return data

    def readinto(self, b):
        self._check_stop()
        # Limit how much we fill per call
        view = memoryview(b)[:self._READ_CAP]
        n = self._file.readinto(view)
        if n:
            self._bytes_read += n
            self._callback(self._bytes_read)
        return n

    def seek(self, pos, whence=0):
        result = self._file.seek(pos, whence)
        self._bytes_read = self._file.tell()
        return result

    def tell(self):
        return self._file.tell()

    def readable(self):
        return True

    def seekable(self):
        return True

    def close(self):
        try:
            self._file.close()
        except Exception:
            pass
        super().close()


# ── Upload session management ─────────────────────────────────────────────────

def create_upload_request(service, file_path: str, folder_id: str,
                          progress_callback, stop_event=None):
    """Create a new resumable upload session. Does not start uploading.

    Returns (request, wrapper). Caller drives the upload by calling
    request.next_chunk() in a loop.
    """
    file_name = Path(file_path).name
    mime, _ = mimetypes.guess_type(file_path)
    mime = mime or "application/octet-stream"

    wrapper = ProgressFileWrapper(file_path, progress_callback, stop_event=stop_event)
    media = MediaIoBaseUpload(wrapper, mimetype=mime, chunksize=CHUNK_SIZE, resumable=True)

    request = service.files().create(
        body={"name": file_name, "parents": [folder_id]},
        media_body=media,
        supportsAllDrives=True,
        fields="id",
    )
    return request, wrapper


def restore_upload_request(service, file_path: str, folder_id: str,
                           resumable_uri: str, saved_progress: int,
                           progress_callback, stop_event=None):
    """Restore a previously interrupted resumable upload session.

    Queries the Drive server for the actual confirmed byte count (handles
    mid-chunk crashes where saved_progress may be ahead of what Drive received).

    Returns (request, wrapper, confirmed_bytes).
    """
    mime, _ = mimetypes.guess_type(file_path)
    mime = mime or "application/octet-stream"
    file_size = os.path.getsize(file_path)

    wrapper = ProgressFileWrapper(file_path, progress_callback, stop_event=stop_event)
    media = MediaIoBaseUpload(wrapper, mimetype=mime, chunksize=CHUNK_SIZE, resumable=True)

    # Build a new request object with the same parameters
    request = service.files().create(
        body={"name": Path(file_path).name, "parents": [folder_id]},
        media_body=media,
        supportsAllDrives=True,
        fields="id",
    )

    # Restore the session URI — next_chunk() skips initiation when this is set
    request.resumable_uri = resumable_uri

    # Ask Drive how many bytes it actually has (server is authoritative)
    confirmed = _query_server_progress(resumable_uri, service._http, file_size)
    if confirmed == 0 and saved_progress > 0:
        # Server returned nothing (possibly a 308 with no Range header meaning 0 bytes)
        # Fall back to saved_progress as a conservative estimate
        confirmed = 0  # safer to restart from 0 than to corrupt

    request.resumable_progress = confirmed

    # Seek the file wrapper to the confirmed position so the next read starts there
    wrapper.seek(confirmed)

    return request, wrapper, confirmed


def _query_server_progress(resumable_uri: str, http, file_size: int) -> int:
    """Ask Drive how many bytes it has confirmed for a resumable session.

    Uses the standard resumable upload protocol: PUT with Content-Range: bytes */N.
    Returns the confirmed byte count, or 0 if the session has expired/errored.
    """
    try:
        headers = {
            "Content-Range": f"bytes */{file_size}",
            "Content-Length": "0",
        }
        resp, _ = http.request(resumable_uri, "PUT", headers=headers)
        status = int(resp.status)
        if status == 308:  # Resume Incomplete
            range_header = resp.get("range", "")
            if range_header and "-" in range_header:
                return int(range_header.split("-")[1]) + 1
            return 0
        elif status in (200, 201):  # Already complete
            return file_size
    except (socket.error, OSError, Exception):
        pass
    return 0


# ── Drive folder creation ─────────────────────────────────────────────────────

def create_drive_folder(service, name: str, parent_id: str) -> str:
    """Create a folder in Drive under parent_id. Returns the new folder's ID."""
    result = service.files().create(
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        },
        supportsAllDrives=True,
        fields="id",
    ).execute()
    return result["id"]


# ── Sharing ───────────────────────────────────────────────────────────────────

def make_shareable(service, file_id: str) -> str:
    """Set file to 'anyone with the link can view' and return the shareable URL."""
    service.permissions().create(
        fileId=file_id,
        supportsAllDrives=True,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    file_meta = service.files().get(
        fileId=file_id,
        supportsAllDrives=True,
        fields="webViewLink",
    ).execute()

    return file_meta.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
