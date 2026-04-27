"""Upload state persistence for drive-uploader.

Saved atomically to ~/.drive-uploader-state.json so uploads survive crashes.
"""

import json
import os
import threading
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

STATE_PATH = Path.home() / ".drive-uploader-state.json"
SESSION_EXPIRY_DAYS = 6  # Google resumable sessions expire after 7 days; clear after 6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class UploadEntry:
    id: str
    status: str          # queued | in_progress | compressing | paused | completed | failed
    local_path: str
    file_name: str
    file_size: int
    folder_id: str
    folder_name: str
    added_at: str
    group_id: Optional[str] = None   # UUID shared by all files in a folder upload group
    group_name: Optional[str] = None # Display name for the folder group
    session_created_at: Optional[str] = None
    resumable_uri: Optional[str] = None
    resumable_progress: int = 0
    drive_file_id: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    is_temp_zip: bool = False  # True if local_path is a temp zip to delete after upload

    @classmethod
    def new(cls, local_path: str, file_size: int, folder_id: str,
            folder_name: str, group_id: Optional[str] = None,
            group_name: Optional[str] = None,
            status: str = "queued") -> "UploadEntry":
        return cls(
            id=str(uuid.uuid4()),
            status=status,
            local_path=local_path,
            file_name=Path(local_path).name,
            file_size=file_size,
            folder_id=folder_id,
            folder_name=folder_name,
            added_at=_now(),
            group_id=group_id,
            group_name=group_name,
        )


class StateManager:
    def __init__(self, path: Path = STATE_PATH):
        self._path = path
        self._entries: List[UploadEntry] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not self._path.exists():
            self._entries = []
            return
        try:
            data = json.loads(self._path.read_text())
            self._entries = []
            for u in data.get("uploads", []):
                # Tolerate missing fields from older versions
                known = {f.name for f in UploadEntry.__dataclass_fields__.values()}
                filtered = {k: v for k, v in u.items() if k in known}
                self._entries.append(UploadEntry(**filtered))
        except Exception:
            self._entries = []

    def _save(self):
        # Must be called with self._lock held
        tmp = self._path.with_suffix(".json.tmp")
        data = {"uploads": [asdict(e) for e in self._entries]}
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self._path)

    def all(self) -> List[UploadEntry]:
        with self._lock:
            return list(self._entries)

    def get(self, entry_id: str) -> Optional[UploadEntry]:
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    return e
        return None

    def add(self, entry: UploadEntry) -> bool:
        """Add entry. Returns False (skips) if same path is already queued/in_progress."""
        with self._lock:
            for e in self._entries:
                if e.local_path == entry.local_path and e.status in ("queued", "in_progress"):
                    return False
            self._entries.append(entry)
            self._save()
        return True

    def update(self, entry_id: str, **kwargs):
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    for k, v in kwargs.items():
                        if hasattr(e, k):
                            setattr(e, k, v)
                    break
            self._save()

    def get_pending(self) -> List[UploadEntry]:
        """Entries that are not yet done — shown in queue on startup."""
        with self._lock:
            return [e for e in self._entries
                    if e.status in ("queued", "in_progress", "compressing", "paused")]

    def get_queued(self) -> List[UploadEntry]:
        """Entries ready to start uploading (not yet started)."""
        with self._lock:
            return [e for e in self._entries if e.status == "queued"]

    def clear_completed(self):
        with self._lock:
            self._entries = [e for e in self._entries
                             if e.status not in ("completed", "failed")]
            self._save()

    def clear_all_pending(self):
        """Used when user declines resume on startup."""
        with self._lock:
            for e in self._entries:
                if e.status in ("queued", "in_progress", "compressing", "paused"):
                    e.status = "failed"
                    e.error = "Cleared by user"
            self._save()

    def expire_old_sessions(self) -> List[str]:
        """Clear resumable session URIs older than SESSION_EXPIRY_DAYS.

        Returns list of filenames whose sessions expired (will restart from 0).
        """
        expired_names = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=SESSION_EXPIRY_DAYS)
        with self._lock:
            changed = False
            for e in self._entries:
                if e.status == "in_progress" and e.session_created_at and e.resumable_uri:
                    try:
                        created = datetime.fromisoformat(e.session_created_at)
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        if created < cutoff:
                            e.resumable_uri = None
                            e.resumable_progress = 0
                            e.session_created_at = None
                            e.status = "queued"
                            expired_names.append(e.file_name)
                            changed = True
                    except ValueError:
                        pass
            if changed:
                self._save()
        return expired_names

    def get_group_progress(self, group_id: str):
        """Returns (bytes_done, bytes_total, n_complete, n_total) for a group."""
        with self._lock:
            entries = [e for e in self._entries if e.group_id == group_id]
            if not entries:
                return 0, 0, 0, 0
            bytes_done = sum(e.resumable_progress for e in entries)
            bytes_total = sum(e.file_size for e in entries)
            n_complete = sum(1 for e in entries if e.status == "completed")
            return bytes_done, bytes_total, n_complete, len(entries)
