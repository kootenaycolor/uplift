"""Per-account sender profile for Uplift email notifications.

Each Drive account stores its own sender identity (name + email) in
~/.uplift-profile.json under an "accounts" key.  App password is stored
securely in macOS Keychain via keyring — never written to disk.

JSON format:
  {
    "accounts": {
      "<account_id>": {"sender_name": "…", "sender_email": "…"},
      ...
    }
  }

Keyring key: ("uplift-email", sender_email) — password is per email
address, so two accounts sharing the same Gmail share one Keychain entry.

Migration: old flat-format file (no "accounts" key) is silently ignored.
User re-runs Setup once per account.
"""

import json
from pathlib import Path
import keyring

PROFILE_PATH      = Path.home() / ".uplift-profile.json"
KEYRING_SERVICE   = "uplift-email"

_OLD_PROFILE_PATH    = Path.home() / ".drive-uploader-profile.json"
_OLD_KEYRING_SERVICE = "drive-uploader-email"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _read_all() -> dict:
    """Return the full profile dict, or {} on any error / missing file."""
    if not PROFILE_PATH.exists():
        return {}
    try:
        data = json.loads(PROFILE_PATH.read_text())
        if not isinstance(data, dict):
            return {}
        # Old flat format has no "accounts" key — treat as empty
        if "accounts" not in data:
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _write_all(data: dict) -> None:
    PROFILE_PATH.write_text(json.dumps(data, indent=2))


def _migrate_old_keyring(email: str) -> None:
    """One-time: copy password from old service to new, then delete old."""
    try:
        old_pw = keyring.get_password(_OLD_KEYRING_SERVICE, email)
        if old_pw:
            if not keyring.get_password(KEYRING_SERVICE, email):
                keyring.set_password(KEYRING_SERVICE, email, old_pw)
            try:
                keyring.delete_password(_OLD_KEYRING_SERVICE, email)
            except Exception:
                pass
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def load(account_id: str) -> dict | None:
    """Return {sender_name, sender_email, gmail_app_password} for account, or None."""
    data = _read_all()
    acct = data.get("accounts", {}).get(account_id)
    if not acct:
        return None
    email = acct.get("sender_email", "")
    pw = keyring.get_password(KEYRING_SERVICE, email) or ""
    return {
        "sender_name":       acct.get("sender_name", ""),
        "sender_email":      email,
        "gmail_app_password": pw,
    }


def save(account_id: str, sender_name: str, sender_email: str, app_password: str) -> dict:
    """Write sender identity for account to disk, password to Keychain."""
    data = _read_all()
    if "accounts" not in data:
        data["accounts"] = {}
    data["accounts"][account_id] = {
        "sender_name":  sender_name,
        "sender_email": sender_email,
    }
    _write_all(data)
    keyring.set_password(KEYRING_SERVICE, sender_email, app_password)
    return {
        "sender_name":       sender_name,
        "sender_email":      sender_email,
        "gmail_app_password": app_password,
    }


def clear(account_id: str) -> None:
    """Remove sender profile for account from disk (and Keychain if no other account uses that email)."""
    data = _read_all()
    accounts = data.get("accounts", {})
    removed_email = accounts.pop(account_id, {}).get("sender_email", "")
    data["accounts"] = accounts
    _write_all(data)

    # Only delete the Keychain entry if no other account uses that email
    if removed_email:
        still_used = any(
            a.get("sender_email") == removed_email
            for a in accounts.values()
        )
        if not still_used:
            try:
                keyring.delete_password(KEYRING_SERVICE, removed_email)
            except Exception:
                pass
