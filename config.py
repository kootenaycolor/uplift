"""Persistent configuration for drive-uploader.

Saved to ~/.drive-uploader-config.json so the last-used Drive folder
is pre-filled on relaunch.
"""

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".drive-uploader-config.json"

DEFAULTS = {
    "drive_folder_id": "",
    "drive_folder_name": "",
    "active_drive_account_id": "",
    # Export Watch
    "watch_enabled": False,
    "watch_folder": "",
    # Email Notification
    "email_enabled": False,
    "recipient_email": "",
    "email_subject": "Your file is ready: {filename}",
    "email_body": (
        "Hi,\n\n"
        "Your file is ready to download:\n"
        "{link}\n\n"
        "Best,\n"
        "{sender_name}"
    ),
    "auto_send_email": True,
}


def load() -> dict:
    """Load config from disk, merging with defaults for any missing keys."""
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
            cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save(cfg: dict) -> None:
    """Persist config to disk."""
    data = {k: cfg[k] for k in DEFAULTS if k in cfg}
    CONFIG_PATH.write_text(json.dumps(data, indent=2))
