"""Sender profile for drive-uploader email notifications.

Sender identity (name + email) stored in ~/.drive-uploader-profile.json.
App password stored securely in macOS Keychain via keyring — never written to disk.
"""

import json
from pathlib import Path
import keyring

PROFILE_PATH = Path.home() / ".drive-uploader-profile.json"
KEYRING_SERVICE = "drive-uploader-email"


def load() -> dict | None:
    """Return {sender_name, sender_email, gmail_app_password}, or None if not set up."""
    if not PROFILE_PATH.exists():
        return None
    try:
        data = json.loads(PROFILE_PATH.read_text())
        email = data.get("sender_email", "")
        data["gmail_app_password"] = keyring.get_password(KEYRING_SERVICE, email) or ""
        return data
    except (json.JSONDecodeError, OSError):
        return None


def save(sender_name: str, sender_email: str, app_password: str) -> dict:
    """Write identity to disk and password to Keychain. Returns full profile dict."""
    PROFILE_PATH.write_text(json.dumps(
        {"sender_name": sender_name, "sender_email": sender_email}, indent=2
    ))
    keyring.set_password(KEYRING_SERVICE, sender_email, app_password)
    return {
        "sender_name": sender_name,
        "sender_email": sender_email,
        "gmail_app_password": app_password,
    }


def clear() -> None:
    """Delete profile file and remove password from Keychain."""
    if PROFILE_PATH.exists():
        try:
            email = json.loads(PROFILE_PATH.read_text()).get("sender_email", "")
            if email:
                try:
                    keyring.delete_password(KEYRING_SERVICE, email)
                except Exception:
                    pass
        except Exception:
            pass
        PROFILE_PATH.unlink(missing_ok=True)
