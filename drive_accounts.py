# ── Shared Drive account manager ─────────────────────────────────────────────
# This file is intentionally kept in sync between export-watcher and drive-uploader.
# When making changes, update both copies.
# ─────────────────────────────────────────────────────────────────────────────
"""Named Google Drive accounts stored in ~/.drive-accounts/.

Each account stores its OAuth token on disk (600 permissions) and its
credentials.json content in the macOS Keychain. Token refresh works
without credentials.json because token files embed client_id/secret.
"""

import json
import keyring
import os
import uuid
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ACCOUNTS_DIR = Path.home() / ".drive-accounts"
INDEX_PATH = ACCOUNTS_DIR / "index.json"
SCOPES = ["https://www.googleapis.com/auth/drive"]
KEYRING_SERVICE = "kootenay-drive-accounts"


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
    return ACCOUNTS_DIR / f"token_{account_id}.json"


def credentials_path(account_id: str) -> Path:
    return ACCOUNTS_DIR / f"credentials_{account_id}.json"


def get_account(account_id: str) -> dict | None:
    """Return the account dict for account_id, or None if not found."""
    for a in list_accounts():
        if a["id"] == account_id:
            return a
    return None


# ── Account management ────────────────────────────────────────────────────────

def add_account(source_credentials_path, display_name: str = "") -> dict:
    """Run OAuth flow, save token + credentials, return account dict.

    source_credentials_path: path to the app's credentials.json (used for OAuth).
    A copy is stored alongside the token so refresh works from any app.
    """
    _ensure()
    flow = InstalledAppFlow.from_client_secrets_file(
        str(source_credentials_path), SCOPES
    )
    creds = flow.run_local_server(port=0)

    # Fetch the signed-in email from the Drive API
    svc = build("drive", "v3", credentials=creds)
    email = (
        svc.about().get(fields="user").execute()
        .get("user", {}).get("emailAddress", "")
    )

    account_id = uuid.uuid4().hex[:8]
    token_path(account_id).write_text(creds.to_json())
    os.chmod(token_path(account_id), 0o600)
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
    """Delete an account, its token file, and its Keychain entry."""
    _save_index([a for a in list_accounts() if a["id"] != account_id])
    token_path(account_id).unlink(missing_ok=True)
    credentials_path(account_id).unlink(missing_ok=True)  # backward compat
    try:
        keyring.delete_password(KEYRING_SERVICE, account_id)
    except Exception:
        pass


def rename_account(account_id: str, new_name: str) -> None:
    accounts = list_accounts()
    for a in accounts:
        if a["id"] == account_id:
            a["name"] = new_name
    _save_index(accounts)


# ── Drive service builders ────────────────────────────────────────────────────

def get_service(account_id: str):
    """Return an authenticated Drive service for a saved account."""
    tp = token_path(account_id)
    creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        tp.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


def build_thread_service(account_id: str):
    """Return a NEW thread-safe Drive service for a saved account.

    Each upload worker thread must call this to get an independent service —
    httplib2 is not thread-safe.
    """
    tp = token_path(account_id)
    creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        tp.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)
