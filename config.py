"""Persistent configuration for Uplift.

Saved to ~/.uplift-config.json so the last-used Drive folder
is pre-filled on relaunch.
"""

import json
import uuid
from pathlib import Path

CONFIG_PATH = Path.home() / ".uplift-config.json"
_OLD_PATH   = Path.home() / ".drive-uploader-config.json"  # migration


DEFAULTS = {
    "active_drive_account_id": "",
    "last_browse_dir": "/Volumes",
    "quick_drive_account_id": "",
    "quick_drive_folder_id": "",
    "quick_drive_folder_name": "",
    # Per-account email templates: {account_id: {email_subject, email_body}}
    "account_templates": {},
    # Named email templates (list of dicts with name/to/cc/bcc/subject/body)
    "email_templates": [],
    # Per-job list (replaces global watch/email/destination settings)
    "jobs": [],
    # v2: Presets (named drive+email configs) and Watches (global folder monitors)
    # None = not yet migrated; [] = migrated with no items
    "presets": None,
    "watches": None,
    # Email preset keys — must be in DEFAULTS so the whitelist preserves them on save/load
    "email_body_templates": [],
    "email_recipient_presets": [],
    "email_timing_presets_watch": [],
    "email_timing_presets_manual": [],
    "email_timing_presets": [],          # referenced by migration; kept for safety
    "_email_preset_migration_done": False,
    # User-configured zip scratch directories
    "zip_scratch_dirs": [],
    # Legacy keys kept for migration only — not used by current code
    "drive_folder_id": "",
    "drive_folder_name": "",
    "watch_enabled": False,
    "watch_folder": "",
    "watch_batch_mode": False,
    "watch_batch_stable_secs": 15,
    "email_enabled": False,
    "recipient_email": "",
    "recipient_cc": "",
    "recipient_bcc": "",
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


def _migrate():
    """One-time rename of old config file from drive-uploader era."""
    if _OLD_PATH.exists() and not CONFIG_PATH.exists():
        try:
            _OLD_PATH.rename(CONFIG_PATH)
        except OSError:
            pass


def _migrate_to_jobs(cfg: dict) -> None:
    """One-time: convert legacy global watch/email/destination settings to a Job."""
    if cfg.get("jobs"):
        return  # already migrated
    # Always create one default job, seeding from legacy keys if present
    job = {
        "id": str(uuid.uuid4()),
        "name": "Default Job",
        "drive_account_id": cfg.get("active_drive_account_id", ""),
        "drive_folder_id": cfg.get("drive_folder_id", ""),
        "drive_folder_name": cfg.get("drive_folder_name", ""),
        "watch_enabled": cfg.get("watch_enabled", False),
        "watch_folder": cfg.get("watch_folder", ""),
        "watch_batch_mode": cfg.get("watch_batch_mode", False),
        "watch_batch_stable_secs": cfg.get("watch_batch_stable_secs", 15),
        "email_enabled": cfg.get("email_enabled", False),
        "recipient_email": cfg.get("recipient_email", ""),
        "recipient_cc": cfg.get("recipient_cc", ""),
        "recipient_bcc": cfg.get("recipient_bcc", ""),
        "email_trigger": "per_file" if cfg.get("auto_send_email", True) else "manual",
        "active": cfg.get("watch_enabled", False),
    }
    cfg["jobs"] = [job]


def _migrate_jobs_to_presets(cfg: dict) -> None:
    """Convert old cfg['jobs'] (Projects) to cfg['presets'] + cfg['watches']."""
    if cfg.get("presets") is not None:
        return  # already migrated
    cfg["presets"] = []
    cfg["watches"] = []
    for old_job in cfg.get("jobs", []):
        preset_id = str(uuid.uuid4())
        preset = {
            "id": preset_id,
            "name": old_job.get("name", "Default"),
            "drive_account_id": old_job.get("drive_account_id", ""),
            "drive_folder_id": old_job.get("drive_folder_id", ""),
            "drive_folder_name": old_job.get("drive_folder_name", ""),
            "email_to": old_job.get("recipient_email", ""),
            "email_subject": old_job.get("email_subject", ""),
            "email_body": old_job.get("email_body", ""),
        }
        cfg["presets"].append(preset)
        if old_job.get("watch_enabled") and old_job.get("watch_folder"):
            watch = {
                "id": str(uuid.uuid4()),
                "name": Path(old_job["watch_folder"]).name,
                "path": old_job["watch_folder"],
                "preset_id": preset_id,
                "manual_config": None,
                "active": old_job.get("active", False),
                "file_extensions": [],
                "batch_stable_secs": old_job.get("watch_batch_stable_secs", 15),
            }
            cfg["watches"].append(watch)


def load() -> dict:
    """Load config from disk, merging with defaults for any missing keys."""
    _migrate()
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text())
            cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
            # jobs is a list — always take the full saved value
            if "jobs" in saved:
                cfg["jobs"] = saved["jobs"]
        except (json.JSONDecodeError, OSError):
            pass
    _migrate_to_jobs(cfg)
    _migrate_jobs_to_presets(cfg)
    return cfg


def save(cfg: dict) -> None:
    """Persist config to disk."""
    data = {k: cfg[k] for k in DEFAULTS if k in cfg}
    # Always include jobs even if it was a list (not filtered by DEFAULTS check above)
    data["jobs"] = cfg.get("jobs", [])
    data["presets"] = cfg.get("presets") or []
    data["watches"] = cfg.get("watches") or []
    CONFIG_PATH.write_text(json.dumps(data, indent=2))
