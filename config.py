"""Persistent configuration for Uplift.

Saved to ~/.uplift-config.json so the last-used Drive folder
is pre-filled on relaunch.
"""

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".uplift-config.json"
_OLD_PATH   = Path.home() / ".drive-uploader-config.json"  # migration


DEFAULTS = {
    "drive_folder_id": "",
    "drive_folder_name": "",
    "active_drive_account_id": "",
    "last_browse_dir": "/Volumes",
    # Export Watch
    "watch_enabled": False,
    "watch_folder": "",
    "watch_batch_mode": False,
    "watch_batch_stable_secs": 15,   # seconds folder must be fully static before zipping
    # Email Notification
    "email_enabled": False,
    "recipient_email": "",       # comma-separated To addresses
    "recipient_cc": "",          # comma-separated CC addresses
    "recipient_bcc": "",         # comma-separated BCC addresses
    "email_subject": "Your file is ready: {filename}",
    "email_body": (
        "Hi,\n\n"
        "Your file is ready to download:\n"
        "{link}\n\n"
        "Best,\n"
        "{sender_name}"
    ),
    "auto_send_email": True,
    # Per-account email templates: {account_id: {email_subject, email_body}}
    "account_templates": {},
}


def _migrate():
    """One-time rename of old config file from drive-uploader era."""
    if _OLD_PATH.exists() and not CONFIG_PATH.exists():
        try:
            _OLD_PATH.rename(CONFIG_PATH)
        except OSError:
            pass


def load() -> dict:
    """Load config from disk, merging with defaults for any missing keys."""
    _migrate()
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
