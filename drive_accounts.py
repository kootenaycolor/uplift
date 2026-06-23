# ── Shared Drive account manager ─────────────────────────────────────────────
# This file is intentionally kept in sync between export-watcher and drive-uploader.
# When making changes, update both copies.
# ─────────────────────────────────────────────────────────────────────────────
"""Named Google Drive accounts.

Storage layout:
  ~/.drive-accounts/index.json          — account list (id, name, email) — not sensitive
  ~/.drive-accounts/.token_cache.json   — token cache (chmod 600), refreshed from Keychain weekly
  macOS Keychain "kootenay-drive-accounts"  key=account_id  — credentials.json content
  macOS Keychain "kootenay-drive-tokens"    key=account_id  — OAuth token JSON (source of truth)

No OAuth tokens or client secrets are written to disk except the cache file,
which is chmod 600 and refreshed from Keychain at most once per week.
On first use, any legacy token files found in ~/.drive-accounts/ are automatically
migrated to Keychain and deleted.
"""

import json
import keyring
import os
import threading
import time
import uuid
from pathlib import Path

import requests as _requests
from google.auth.transport.requests import Request, AuthorizedSession as _AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Re-use the same requests-based HTTP adapter defined in drive.py
# to avoid importing it from there (circular imports). Copy is minimal.
class _FakeResponse(dict):
    fromcache = False; version = 11; previous = None
    def __init__(self, r):
        super().__init__({k.lower(): v for k, v in r.headers.items()})
        self.status = r.status_code; self.reason = r.reason

_HTTP_TIMEOUT = 30  # seconds for all Drive API calls

class _RequestsHttp:
    def __init__(self, creds):
        self._session = _AuthorizedSession(creds)
    def request(self, uri, method="GET", body=None, headers=None,
                redirections=10, connection_type=None):
        resp = self._session.request(method=method, url=uri, data=body,
                                     headers=headers or {},
                                     allow_redirects=(redirections > 0),
                                     timeout=_HTTP_TIMEOUT)
        return _FakeResponse(resp), resp.content
    def close(self):
        self._session.close()

def _build_service(creds):
    return build("drive", "v3", http=_RequestsHttp(creds))

ACCOUNTS_DIR = Path.home() / ".drive-accounts"
INDEX_PATH   = ACCOUNTS_DIR / "index.json"
SCOPES = ["https://www.googleapis.com/auth/drive"]
KEYRING_SERVICE       = "kootenay-drive-accounts"   # credentials.json
TOKEN_KEYRING_SERVICE = "kootenay-drive-tokens"     # OAuth token JSON

DISK_CACHE_PATH    = ACCOUNTS_DIR / ".token_cache.json"
DISK_CACHE_MAX_AGE = 7 * 24 * 3600   # re-read Keychain after 7 days

# In-memory token JSON cache — avoids repeated Keychain reads within a session.
_token_cache: dict[str, str] = {}   # account_id -> token JSON string
_token_cache_lock = threading.Lock()


# ── Storage helpers ───────────────────────────────────────────────────────────

def _ensure():
    ACCOUNTS_DIR.mkdir(exist_ok=True)
    os.chmod(ACCOUNTS_DIR, 0o700)


def list_accounts() -> list:
    """Return [{id, name, email}] for all saved accounts."""
    _ensure()
    if not INDEX_PATH.exists():
        return []
    try:
        return json.loads(INDEX_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(accounts: list) -> None:
    INDEX_PATH.write_text(json.dumps(accounts, indent=2))


def token_path(account_id: str) -> Path:
    """Legacy disk path — only used for migration detection."""
    return ACCOUNTS_DIR / f"token_{account_id}.json"


def has_token(account_id: str) -> bool:
    """Return True if a token exists for this account."""
    with _token_cache_lock:
        if account_id in _token_cache:
            return True
    # Check disk cache before touching Keychain
    disk = _read_disk_cache()
    if account_id in disk:
        return True
    return bool(
        keyring.get_password(TOKEN_KEYRING_SERVICE, account_id)
        or token_path(account_id).exists()
    )


def credentials_path(account_id: str) -> Path:
    return ACCOUNTS_DIR / f"credentials_{account_id}.json"


def get_account(account_id: str) -> dict | None:
    """Return the account dict for account_id, or None if not found."""
    for a in list_accounts():
        if a["id"] == account_id:
            return a
    return None


# ── Disk token cache (chmod 600, refreshed from Keychain weekly) ──────────────

def _read_disk_cache() -> dict:
    """Return {account_id: {token_json, cached_at}} or {}."""
    try:
        return json.loads(DISK_CACHE_PATH.read_text())
    except Exception:
        return {}


def _write_disk_cache(cache: dict) -> None:
    try:
        _ensure()
        DISK_CACHE_PATH.write_text(json.dumps(cache))
        os.chmod(DISK_CACHE_PATH, 0o600)
    except Exception:
        pass


# ── Token Keychain helpers ────────────────────────────────────────────────────

def _save_token(account_id: str, creds: Credentials) -> None:
    """Persist OAuth token to Keychain, in-memory cache, and disk cache."""
    token_json = creds.to_json()
    keyring.set_password(TOKEN_KEYRING_SERVICE, account_id, token_json)
    with _token_cache_lock:
        _token_cache[account_id] = token_json
    disk = _read_disk_cache()
    disk[account_id] = {"token_json": token_json, "cached_at": time.time()}
    _write_disk_cache(disk)


def _load_token(account_id: str) -> Credentials | None:
    """Load OAuth token: in-memory → disk cache → Keychain (at most once per week)."""
    # 1. In-memory (fastest, no I/O)
    with _token_cache_lock:
        token_json = _token_cache.get(account_id)
    if token_json:
        return Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)

    # 2. Disk cache (survives restarts, avoids Keychain if fresh)
    disk = _read_disk_cache()
    entry = disk.get(account_id)
    if entry and time.time() - entry.get("cached_at", 0) < DISK_CACHE_MAX_AGE:
        token_json = entry["token_json"]
        with _token_cache_lock:
            _token_cache[account_id] = token_json
        return Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)

    # 3. Keychain (once per week per account)
    token_json = keyring.get_password(TOKEN_KEYRING_SERVICE, account_id)
    if not token_json:
        # One-time migration: move old disk token into Keychain then delete the file.
        tp = token_path(account_id)
        if tp.exists():
            token_json = tp.read_text()
            keyring.set_password(TOKEN_KEYRING_SERVICE, account_id, token_json)
            tp.unlink()
        else:
            return None

    # Populate both caches so subsequent calls skip Keychain
    with _token_cache_lock:
        _token_cache[account_id] = token_json
    disk[account_id] = {"token_json": token_json, "cached_at": time.time()}
    _write_disk_cache(disk)

    return Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)


def _evict_token_cache(account_id: str) -> None:
    with _token_cache_lock:
        _token_cache.pop(account_id, None)
    disk = _read_disk_cache()
    disk.pop(account_id, None)
    _write_disk_cache(disk)


# ── Account management ────────────────────────────────────────────────────────

def add_account(source_credentials_path, display_name: str = "") -> dict:
    """Run OAuth flow, save token to Keychain, return account dict."""
    _ensure()
    flow = InstalledAppFlow.from_client_secrets_file(
        str(source_credentials_path), SCOPES
    )
    creds = flow.run_local_server(port=0, timeout_seconds=120)

    # Fetch the signed-in email from the Drive API
    svc = _build_service(creds)
    email = (
        svc.about().get(fields="user").execute()
        .get("user", {}).get("emailAddress", "")
    )

    account_id = uuid.uuid4().hex[:8]
    _save_token(account_id, creds)
    keyring.set_password(KEYRING_SERVICE, account_id, Path(source_credentials_path).read_text())

    existing = list_accounts()
    acct = {
        "id": account_id,
        "name": display_name or email or f"Account {len(existing) + 1}",
        "email": email,
    }
    existing.append(acct)
    _save_index(existing)
    return acct


def remove_account(account_id: str) -> None:
    """Delete an account and all its Keychain entries."""
    _save_index([a for a in list_accounts() if a["id"] != account_id])
    _evict_token_cache(account_id)
    try:
        keyring.delete_password(TOKEN_KEYRING_SERVICE, account_id)
    except Exception:
        pass
    try:
        keyring.delete_password(KEYRING_SERVICE, account_id)
    except Exception:
        pass
    token_path(account_id).unlink(missing_ok=True)
    credentials_path(account_id).unlink(missing_ok=True)


def rename_account(account_id: str, new_name: str) -> None:
    accounts = list_accounts()
    for a in accounts:
        if a["id"] == account_id:
            a["name"] = new_name
    _save_index(accounts)


# ── Drive service builders ────────────────────────────────────────────────────

class _TimeoutSession(_requests.Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", _HTTP_TIMEOUT)
        return super().request(*args, **kwargs)


def _refresh_creds(creds: Credentials, account_id: str) -> None:
    """Refresh expired credentials and persist updated token to Keychain and caches."""
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request(session=_TimeoutSession()))
        _save_token(account_id, creds)


def get_service(account_id: str):
    """Return an authenticated Drive service for a saved account."""
    creds = _load_token(account_id)
    _refresh_creds(creds, account_id)
    return _build_service(creds)


def build_thread_service(account_id: str):
    """Return a NEW Drive service for a saved account (requests transport)."""
    creds = _load_token(account_id)
    _refresh_creds(creds, account_id)
    return _build_service(creds)
