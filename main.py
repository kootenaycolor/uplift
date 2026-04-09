"""
Drive Uploader
--------------
Upload any file or folder from any mounted drive to Google Drive.

Key features:
  - Picks files/folders from any path including external drives
  - True resumable uploads: session URI saved after every chunk, survives crashes
  - Up to 3 concurrent uploads (Frame.io-inspired parallelism for folder uploads)
  - Exponential backoff retry on transient network errors
  - Sub-chunk progress via ProgressFileWrapper for smooth UI updates
  - Rolling 20s data rate and ETA display
  - ZIP or Keep Structure mode for folder uploads
"""

import os
import queue
import shutil
import socket
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import filedialog

import customtkinter as ctk
from googleapiclient.errors import HttpError

import config
import drive as drivelib
import drive_accounts
from drive import StopRequested
from state import StateManager, UploadEntry

# ── Appearance ────────────────────────────────────────────────────────────────

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG     = "#1c1c1e"
BG2    = "#2c2c2e"
BG3    = "#3a3a3c"
BORDER = "#48484a"
TEXT   = "#f2f2f7"
TEXT2  = "#8e8e93"
ACCENT = "#0a84ff"
GREEN  = "#30d158"
RED    = "#ff453a"
YELLOW = "#ffd60a"

FONT_TITLE = ("SF Pro Display", 20, "bold")
FONT_LABEL = ("SF Pro Text", 11)
FONT_SMALL = ("SF Pro Text", 10)

MAX_CONCURRENT = 1  # serialized — OpenSSL 3.x has a thread-safety bug that
                     # macOS 26's xzone malloc allocator catches as heap corruption
                     # when multiple SSL connections operate concurrently


# ── Helpers ───────────────────────────────────────────────────────────────────

def section_label(parent, text):
    return ctk.CTkLabel(parent, text=text.upper(),
                        font=("SF Pro Text", 10, "bold"), text_color=TEXT2)


def divider(parent):
    return ctk.CTkFrame(parent, height=1, fg_color=BORDER, corner_radius=0)


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_duration(secs: float) -> str:
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs}s"
    elif secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    else:
        h = secs // 3600
        m = (secs % 3600) // 60
        return f"{h}h {m}m"


# ── Drive Folder Picker ───────────────────────────────────────────────────────

class FolderPickerDialog(ctk.CTkToplevel):
    def __init__(self, parent, folders):
        super().__init__(parent)
        self.title("Select Drive Folder")
        self.geometry("520x560")
        self.configure(fg_color=BG)
        self.resizable(True, True)
        self.result_id = None
        self.result_name = None
        self.grab_set()

        self._folders = folders
        self._iid_to_folder = {}   # treeview iid → folder dict (leaf nodes only)
        self._selected_folder = None

        # ── Header ──
        ctk.CTkLabel(self, text="Select Google Drive Folder",
                     font=FONT_TITLE, text_color=TEXT).pack(padx=20, pady=(20, 4))
        ctk.CTkLabel(self, text="Expand drives to browse your folder hierarchy.",
                     font=FONT_LABEL, text_color=TEXT2).pack(padx=20, pady=(0, 10))

        # ── Search box ──
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", self._filter)
        ctk.CTkEntry(self, textvariable=self._search_var,
                     placeholder_text="Search folders…",
                     fg_color=BG3, border_color=BORDER, text_color=TEXT,
                     height=34, corner_radius=8).pack(padx=20, fill="x")

        # ── Treeview style ──
        style = ttk.Style()
        style.configure(
            "FP.Treeview",
            background=BG3,
            foreground=TEXT,
            fieldbackground=BG3,
            borderwidth=0,
            font=("SF Pro Text", 12),
            rowheight=26,
        )
        style.map(
            "FP.Treeview",
            background=[("selected", ACCENT)],
            foreground=[("selected", "#ffffff")],
        )
        style.layout("FP.Treeview", [("FP.Treeview.treearea", {"sticky": "nswe"})])

        # ── Tree container ──
        tree_frame = ctk.CTkFrame(self, fg_color=BG3, corner_radius=8,
                                   border_color=BORDER, border_width=1)
        tree_frame.pack(padx=20, pady=10, fill="both", expand=True)

        self._tree = ttk.Treeview(tree_frame, style="FP.Treeview",
                                   show="tree", selectmode="browse")
        vsb = tk.Scrollbar(tree_frame, orient="vertical",
                           command=self._tree.yview,
                           relief="flat", bd=0, width=10)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        vsb.pack(side="right", fill="y", pady=4, padx=(0, 2))

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-1>", self._on_confirm)
        self.bind("<Return>", self._on_confirm)

        # ── Button row ──
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(padx=20, pady=(0, 16), fill="x")
        ctk.CTkButton(btn_row, text="Cancel", width=100, height=32,
                      fg_color=BG3, hover_color=BORDER, text_color=TEXT,
                      corner_radius=8, command=self.destroy).pack(side="right", padx=(8, 0))
        self._confirm_btn = ctk.CTkButton(btn_row, text="Select", width=100, height=32,
                                           fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
                                           corner_radius=8, state="disabled",
                                           command=self._on_confirm)
        self._confirm_btn.pack(side="right")

        # ── Initial build ──
        self._build_tree(folders)

    # ── Tree population ───────────────────────────────────────────────────────

    def _build_tree(self, folders):
        """Populate the treeview with a proper hierarchical Drive structure."""
        self._tree.delete(*self._tree.get_children())
        self._iid_to_folder = {}

        # Build lookup structures
        children_of = {}  # (drive_id, parent_id) → [folder, ...]
        for f in folders:
            key = (f["drive_id"], f["parent_id"])
            children_of.setdefault(key, []).append(f)

        # Determine drive order (same as list sort: shared drives first, then My Drive)
        seen_drives = {}
        for f in folders:
            if f["drive_id"] not in seen_drives:
                seen_drives[f["drive_id"]] = f["drive_name"]

        drive_iid_map = {}
        for drive_id, drive_name in seen_drives.items():
            iid = self._tree.insert("", "end", text=f"  \U0001f4c2  {drive_name}", open=True)
            drive_iid_map[drive_id] = iid

        # Recursively insert children under each drive
        def insert_children(parent_iid, drive_id, parent_id):
            children = sorted(
                children_of.get((drive_id, parent_id), []),
                key=lambda x: x["name"].lower(),
            )
            for child in children:
                iid = self._tree.insert(parent_iid, "end",
                                        text=f"  \U0001f4c1  {child['name']}", open=False)
                self._iid_to_folder[iid] = child
                insert_children(iid, drive_id, child["id"])

        for drive_id, drive_iid in drive_iid_map.items():
            insert_children(drive_iid, drive_id, drive_id)

        # Orphan sweep: attach any un-inserted folders directly under their drive
        inserted_ids = {f["id"] for f in self._iid_to_folder.values()}
        for f in folders:
            if f["id"] not in inserted_ids:
                drive_iid = drive_iid_map.get(f["drive_id"])
                if drive_iid:
                    iid = self._tree.insert(drive_iid, "end",
                                            text=f"  \U0001f4c1  {f['name']}", open=False)
                    self._iid_to_folder[iid] = f

    def _build_flat(self, folders):
        """Populate the treeview as a flat filtered list (search mode)."""
        self._tree.delete(*self._tree.get_children())
        self._iid_to_folder = {}
        for f in folders:
            iid = self._tree.insert("", "end",
                                    text=f"  \U0001f4c1  {f['drive_name']} / {f['name']}")
            self._iid_to_folder[iid] = f

    # ── Events ────────────────────────────────────────────────────────────────

    def _filter(self, *_):
        q = self._search_var.get().lower().strip()
        self._selected_folder = None
        self._confirm_btn.configure(state="disabled")
        if q:
            filtered = [f for f in self._folders
                        if q in f["name"].lower() or q in f["drive_name"].lower()]
            self._build_flat(filtered)
        else:
            self._build_tree(self._folders)

    def _on_tree_select(self, event=None):
        sel = self._tree.selection()
        if not sel:
            return
        folder = self._iid_to_folder.get(sel[0])
        self._selected_folder = folder
        self._confirm_btn.configure(state="normal" if folder else "disabled")

    def _on_confirm(self, event=None):
        if self._selected_folder is None:
            return
        self.result_id = self._selected_folder["id"]
        self.result_name = f"{self._selected_folder['drive_name']} / {self._selected_folder['name']}"
        self.destroy()


# ── Folder Mode Dialog ────────────────────────────────────────────────────────

class FolderModeDialog(ctk.CTkToplevel):
    def __init__(self, parent, folder_name: str):
        super().__init__(parent)
        self.title("Upload Folder")
        self.geometry("420x230")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.result = None  # "structure" | "zip" | None
        self.grab_set()

        name_display = folder_name if len(folder_name) < 36 else folder_name[:34] + "…"
        ctk.CTkLabel(self, text=f'Upload "{name_display}"',
                     font=FONT_TITLE, text_color=TEXT).pack(padx=20, pady=(22, 4))
        ctk.CTkLabel(self, text="How would you like to upload this folder?",
                     font=FONT_LABEL, text_color=TEXT2).pack(padx=20, pady=(0, 16))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(padx=20, fill="x")
        ctk.CTkButton(btn_row, text="Keep Structure", height=40, corner_radius=8,
                      fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
                      font=("SF Pro Text", 13, "bold"),
                      command=lambda: self._choose("structure")).pack(
                          side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(btn_row, text="Compress to ZIP", height=40, corner_radius=8,
                      fg_color=BG3, hover_color=BORDER, text_color=TEXT,
                      font=("SF Pro Text", 13, "bold"),
                      command=lambda: self._choose("zip")).pack(
                          side="left", expand=True, fill="x")

        ctk.CTkLabel(self,
                     text="Keep Structure preserves subfolders in Drive.\n"
                          "ZIP compresses everything into one file before uploading.",
                     font=FONT_SMALL, text_color=TEXT2, justify="center").pack(padx=20, pady=12)

        ctk.CTkButton(self, text="Cancel", width=80, height=30, corner_radius=8,
                      fg_color="transparent", hover_color=BG3, text_color=TEXT2,
                      command=self.destroy).pack()

    def _choose(self, mode: str):
        self.result = mode
        self.destroy()


# ── Upload Worker ─────────────────────────────────────────────────────────────

class UploadWorker:
    """Runs a single resumable upload in a daemon thread.

    Progress events posted to pq:
      ("progress",   entry_id, bytes_read)      — smooth intra-chunk update
      ("confirmed",  entry_id, bytes_confirmed)  — chunk confirmed by Drive
      ("retrying",   entry_id, attempt)          — retrying after transient error
      ("done",       entry_id, drive_file_id)    — upload complete
      ("error",      entry_id, error_msg)        — terminal failure
      ("cancelled",  entry_id, None)             — stopped by user
    """

    MAX_RETRIES = 5
    RETRY_STATUS_CODES = {429, 500, 502, 503}

    def __init__(self, entry: UploadEntry, state: StateManager,
                 pq: queue.Queue, stop_event: threading.Event,
                 account_id: str = ""):
        self._entry = entry
        self._state = state
        self._pq = pq
        self._stop = stop_event
        self._account_id = account_id

    def run(self):
        entry_id = self._entry.id
        request = None
        wrapper = None

        try:
            # Each worker gets its own Drive service with an independent HTTP
            # connection.  httplib2 is NOT thread-safe — sharing a single
            # service across concurrent workers corrupts OpenSSL buffers,
            # which macOS 26's xzone malloc allocator detects as a crash.
            if self._account_id and drive_accounts.token_path(self._account_id).exists():
                service = drive_accounts.build_thread_service(self._account_id)
            else:
                service = drivelib.build_thread_service()

            if self._entry.resumable_uri:
                # Resume an existing session
                request, wrapper, confirmed = drivelib.restore_upload_request(
                    service,
                    self._entry.local_path,
                    self._entry.folder_id,
                    self._entry.resumable_uri,
                    self._entry.resumable_progress,
                    lambda b: self._pq.put(("progress", entry_id, b)),
                    stop_event=self._stop,
                )
                self._state.update(entry_id, status="in_progress",
                                   resumable_progress=confirmed)
                self._pq.put(("confirmed", entry_id, confirmed))
            else:
                # Start a fresh session
                request, wrapper = drivelib.create_upload_request(
                    service,
                    self._entry.local_path,
                    self._entry.folder_id,
                    lambda b: self._pq.put(("progress", entry_id, b)),
                    stop_event=self._stop,
                )
                now = datetime.now(timezone.utc).isoformat()
                self._state.update(entry_id, status="in_progress",
                                   session_created_at=now)

            response = None
            retry_count = 0

            while response is None:
                try:
                    status, response = request.next_chunk()
                    retry_count = 0  # reset on success

                    if status and request.resumable_uri:
                        confirmed_bytes = request.resumable_progress or 0
                        self._state.update(entry_id,
                                           resumable_uri=request.resumable_uri,
                                           resumable_progress=confirmed_bytes)
                        self._pq.put(("confirmed", entry_id, confirmed_bytes))

                except StopRequested:
                    # User clicked pause — save session and exit cleanly
                    saved_uri = (request.resumable_uri if request else None) or self._entry.resumable_uri
                    saved_progress = (request.resumable_progress if request else None) or 0
                    self._state.update(entry_id, status="queued",
                                       resumable_uri=saved_uri,
                                       resumable_progress=saved_progress)
                    self._pq.put(("cancelled", entry_id, None))
                    return

                except (ConnectionError, TimeoutError, BrokenPipeError,
                        socket.timeout, socket.error) as e:
                    retry_count += 1
                    if retry_count > self.MAX_RETRIES:
                        raise
                    self._pq.put(("retrying", entry_id, retry_count))
                    time.sleep(min(2 ** (retry_count - 1), 16))

                except HttpError as e:
                    if e.resp.status in self.RETRY_STATUS_CODES:
                        retry_count += 1
                        if retry_count > self.MAX_RETRIES:
                            raise
                        self._pq.put(("retrying", entry_id, retry_count))
                        time.sleep(min(2 ** (retry_count - 1), 16))
                    else:
                        raise

            # Upload complete
            drive_file_id = response.get("id", "") if response else ""
            self._state.update(entry_id,
                               status="completed",
                               drive_file_id=drive_file_id,
                               completed_at=datetime.now(timezone.utc).isoformat(),
                               resumable_uri=None,
                               resumable_progress=self._entry.file_size)
            self._pq.put(("done", entry_id, drive_file_id))

        except StopRequested:
            # Stop was set before the first chunk even started
            self._state.update(entry_id, status="queued",
                               resumable_uri=self._entry.resumable_uri,
                               resumable_progress=self._entry.resumable_progress)
            self._pq.put(("cancelled", entry_id, None))

        except OSError as e:
            msg = str(e)
            if "No such file" in msg or "not a file" in msg.lower():
                msg = "File not accessible — is the external drive still connected?"
            # Preserve resumable_uri so user can retry if drive reconnects
            saved_uri = (request.resumable_uri if request else None) or self._entry.resumable_uri
            self._state.update(entry_id, status="failed", error=msg,
                               resumable_uri=saved_uri)
            self._pq.put(("error", entry_id, msg))

        except Exception as e:
            self._state.update(entry_id, status="failed", error=str(e))
            self._pq.put(("error", entry_id, str(e)))

        finally:
            if wrapper and not wrapper.closed:
                wrapper.close()


# ── ZIP Worker ────────────────────────────────────────────────────────────────

class ZipWorker:
    """Compresses a local folder to a temp ZIP, then signals the upload queue.

    Progress events:
      ("zip_progress", entry_id, n_done, n_total)
      ("zip_done",     entry_id, zip_path, zip_size, zip_name)
      ("error",        entry_id, error_msg)
    """

    def __init__(self, folder_path: str, entry_id: str,
                 state: StateManager, pq: queue.Queue):
        self._folder = folder_path
        self._entry_id = entry_id
        self._state = state
        self._pq = pq

    def run(self):
        try:
            folder_name = Path(self._folder).name
            tmp_dir = tempfile.mkdtemp(prefix="drive-uploader-")
            zip_path = os.path.join(tmp_dir, folder_name + ".zip")

            # Collect all files first so we know the total count
            all_files = []
            for root, _, files in os.walk(self._folder):
                for f in files:
                    all_files.append(os.path.join(root, f))

            folder_parent = os.path.dirname(self._folder)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as zf:
                for i, fp in enumerate(all_files):
                    arcname = os.path.relpath(fp, folder_parent)
                    zf.write(fp, arcname)
                    self._pq.put(("zip_progress", self._entry_id, i + 1, len(all_files)))

            zip_size = os.path.getsize(zip_path)
            zip_name = folder_name + ".zip"

            self._state.update(self._entry_id,
                               status="queued",
                               local_path=zip_path,
                               file_name=zip_name,
                               file_size=zip_size,
                               is_temp_zip=True)
            self._pq.put(("zip_done", self._entry_id, zip_path, zip_size, zip_name))

        except Exception as e:
            self._state.update(self._entry_id, status="failed", error=str(e))
            self._pq.put(("error", self._entry_id, str(e)))


# ── Upload Row ────────────────────────────────────────────────────────────────

class UploadRowFrame(ctk.CTkFrame):
    """One row in the upload queue. Shows progress, stats, and a cancel button."""

    def __init__(self, parent, entry: UploadEntry, cancel_callback, resume_callback=None):
        super().__init__(parent, fg_color=BG2, corner_radius=8,
                         border_color=BORDER, border_width=1)
        self._entry_id = entry.id
        self._file_size = entry.file_size
        self._bytes_display = entry.resumable_progress   # for progress bar (smooth)
        self._bytes_confirmed = entry.resumable_progress # for rate/ETA (accurate)
        self._rate_samples: deque = deque()
        self._rate = 0.0
        self._cancel_callback = cancel_callback
        self._resume_callback = resume_callback

        self.pack(fill="x", padx=0, pady=3)

        # ── Top row: filename + size ──
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 3))

        name = entry.file_name
        if len(name) > 44:
            name = name[:42] + "…"
        ctk.CTkLabel(top, text=name, font=("SF Pro Text", 12, "bold"),
                     text_color=TEXT, anchor="w").pack(side="left")
        ctk.CTkLabel(top, text=_fmt_size(entry.file_size),
                     font=FONT_SMALL, text_color=TEXT2).pack(side="right")

        # ── Middle row: progress bar + status badge + cancel ──
        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.pack(fill="x", padx=14, pady=3)

        self._bar = ctk.CTkProgressBar(mid, height=8, corner_radius=4,
                                        fg_color=BG3, progress_color=ACCENT)
        init_val = self._bytes_display / self._file_size if self._file_size else 0
        self._bar.set(init_val)
        self._bar.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self._badge = ctk.CTkLabel(mid, text="Queued", width=96,
                                    font=FONT_SMALL, text_color=TEXT2,
                                    fg_color=BG3, corner_radius=6)
        self._badge.pack(side="left", padx=(0, 6))

        self._cancel_btn = ctk.CTkButton(
            mid, text="✕", width=28, height=26, corner_radius=6,
            fg_color=BG3, hover_color=RED, text_color=TEXT2,
            font=("SF Pro Text", 13),
            command=lambda: self._cancel_callback(self._entry_id),
        )
        self._cancel_btn.pack(side="left")

        # ── Bottom row: stats ──
        self._stats = ctk.CTkLabel(self, text="—",
                                    font=FONT_SMALL, text_color=TEXT2, anchor="w")
        self._stats.pack(fill="x", padx=14, pady=(2, 10))

        # Set initial appearance based on status
        if entry.status == "in_progress":
            self._set_badge("Uploading", ACCENT, TEXT)
        elif entry.status == "compressing":
            self._set_badge("Compressing", YELLOW, "#1c1c1e")
        elif entry.status == "completed":
            self.set_done()
        elif entry.status == "failed":
            self.set_failed(entry.error or "Unknown error")

    # ── Badge / status ──

    def _set_badge(self, text: str, fg: str, text_color: str = TEXT):
        self._badge.configure(text=text, fg_color=fg, text_color=text_color)

    # ── Progress updates ──

    def update_progress(self, bytes_read: int):
        """Smooth update from ProgressFileWrapper reads (intra-chunk)."""
        if self._file_size <= 0:
            return
        self._bytes_display = min(bytes_read, self._file_size)
        self._bar.set(self._bytes_display / self._file_size)
        self._refresh_stats()

    def confirm_progress(self, bytes_confirmed: int):
        """Chunk confirmed by Drive. Updates rolling rate samples."""
        self._bytes_confirmed = bytes_confirmed
        now = time.monotonic()
        self._rate_samples.append((now, bytes_confirmed))
        # Keep last 20 seconds of samples
        cutoff = now - 20.0
        while self._rate_samples and self._rate_samples[0][0] < cutoff:
            self._rate_samples.popleft()
        if len(self._rate_samples) >= 2:
            t0, b0 = self._rate_samples[0]
            t1, b1 = self._rate_samples[-1]
            dt = t1 - t0
            if dt > 0.1:
                self._rate = (b1 - b0) / dt
        self._set_badge("Uploading", ACCENT, TEXT)
        self._refresh_stats()

    def _refresh_stats(self):
        done = _fmt_size(self._bytes_confirmed)
        total = _fmt_size(self._file_size)
        pct = int(self._bytes_display / self._file_size * 100) if self._file_size else 0
        if self._rate > 1024:  # at least 1 KB/s before showing rate
            rate = _fmt_size(self._rate) + "/s"
            remaining = (self._file_size - self._bytes_confirmed) / self._rate
            eta = _fmt_duration(remaining)
            text = f"{done} / {total} ({pct}%) — {rate} — ~{eta}"
        else:
            text = f"{done} / {total} ({pct}%)"
        self._stats.configure(text=text, text_color=TEXT2)

    def set_retrying(self, attempt: int):
        self._set_badge(f"Retry {attempt}/5", YELLOW, "#1c1c1e")
        self._stats.configure(
            text=f"Network error — retrying (attempt {attempt}/5)…", text_color=YELLOW)

    def set_done(self):
        self._bytes_display = self._file_size
        self._bytes_confirmed = self._file_size
        self._bar.set(1.0)
        self._bar.configure(progress_color=GREEN)
        self._set_badge("Done", GREEN, "#1c1c1e")
        self._cancel_btn.configure(text="✓", state="disabled",
                                    fg_color=BG3, hover_color=BG3, text_color=GREEN)
        self._stats.configure(text=f"{_fmt_size(self._file_size)} uploaded successfully",
                              text_color=GREEN)

    def set_paused(self):
        """Upload was stopped by user. Show resume button."""
        self._rate_samples.clear()
        self._rate = 0.0
        self._set_badge("Paused", BG3, TEXT2)
        done = _fmt_size(self._bytes_confirmed)
        total = _fmt_size(self._file_size)
        pct = int(self._bytes_confirmed / self._file_size * 100) if self._file_size else 0
        self._stats.configure(text=f"Paused at {done} / {total} ({pct}%)", text_color=TEXT2)
        # Switch cancel button to a resume button
        self._cancel_btn.configure(
            text="▶", fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
            state="normal",
            command=lambda: self._resume_callback(self._entry_id) if self._resume_callback else None,
        )

    def set_queued(self):
        """Reset to queued state (e.g. after pausing and re-queueing)."""
        self._rate_samples.clear()
        self._rate = 0.0
        self._set_badge("Queued", BG3, TEXT2)
        self._stats.configure(text="Waiting to upload…", text_color=TEXT2)
        self._cancel_btn.configure(
            text="✕", fg_color=BG3, hover_color=RED, text_color=TEXT2,
            state="normal",
            command=lambda: self._cancel_callback(self._entry_id),
        )

    def set_uploading(self):
        """Called when a worker actually starts (or resumes) this entry."""
        self._rate_samples.clear()
        self._rate = 0.0
        self._set_badge("Uploading", ACCENT, TEXT)
        # Restore cancel button (may have been a resume ▶ button)
        self._cancel_btn.configure(
            text="✕", fg_color=BG3, hover_color=RED, text_color=TEXT2,
            state="normal",
            command=lambda: self._cancel_callback(self._entry_id),
        )

    def set_failed(self, msg: str):
        self._set_badge("Failed", RED, TEXT)
        self._cancel_btn.configure(state="disabled")
        short = msg if len(msg) < 65 else msg[:63] + "…"
        self._stats.configure(text=f"Error: {short}", text_color=RED)

    def set_zip_progress(self, done: int, total: int):
        frac = done / total if total else 0
        self._bar.set(frac)
        self._set_badge("Compressing", YELLOW, "#1c1c1e")
        self._stats.configure(text=f"Compressing: {done} / {total} files ({int(frac*100)}%)…",
                              text_color=TEXT2)

    def set_upload_ready(self, zip_name: str, zip_size: int):
        """Transition from compressing → ready to upload."""
        self._file_size = zip_size
        self._bytes_display = 0
        self._bytes_confirmed = 0
        self._rate_samples.clear()
        self._rate = 0.0
        self._bar.set(0.0)
        self._bar.configure(progress_color=ACCENT)
        self._set_badge("Queued", BG3, TEXT2)
        self._stats.configure(text=_fmt_size(zip_size), text_color=TEXT2)


# ── Folder Group Header ───────────────────────────────────────────────────────

class FolderGroupRow(ctk.CTkFrame):
    """Summary header shown above a group of files from the same folder upload."""

    def __init__(self, parent, group_name: str, n_total: int):
        super().__init__(parent, fg_color=BG3, corner_radius=8,
                         border_color=BORDER, border_width=1)
        self.pack(fill="x", padx=0, pady=(8, 1))
        self._n_total = n_total

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=8)

        display = group_name if len(group_name) < 40 else group_name[:38] + "…"
        ctk.CTkLabel(row, text=f"Folder: {display}",
                     font=("SF Pro Text", 11, "bold"), text_color=TEXT,
                     anchor="w").pack(side="left")
        self._count = ctk.CTkLabel(row, text=f"0 / {n_total} files",
                                    font=FONT_SMALL, text_color=TEXT2)
        self._count.pack(side="right")

    def update_count(self, n_complete: int):
        self._count.configure(text=f"{n_complete} / {self._n_total} files")


# ── Upload Queue Frame ────────────────────────────────────────────────────────

class UploadQueueFrame(ctk.CTkScrollableFrame):
    """Scrollable container for upload rows and group headers."""

    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent",
                         scrollbar_button_color=BG3,
                         scrollbar_button_hover_color=BORDER)
        self._rows: dict[str, UploadRowFrame] = {}        # entry_id → row
        self._group_headers: dict[str, FolderGroupRow] = {}  # group_id → header

    def add_group_header(self, group_id: str, group_name: str, n_total: int):
        if group_id not in self._group_headers:
            header = FolderGroupRow(self, group_name, n_total)
            self._group_headers[group_id] = header

    def add_row(self, entry: UploadEntry, cancel_callback, resume_callback=None) -> UploadRowFrame:
        row = UploadRowFrame(self, entry, cancel_callback, resume_callback)
        self._rows[entry.id] = row
        return row

    def get_row(self, entry_id: str) -> UploadRowFrame | None:
        return self._rows.get(entry_id)

    def get_group_header(self, group_id: str) -> FolderGroupRow | None:
        return self._group_headers.get(group_id)

    def remove_completed_rows(self):
        for entry_id, row in list(self._rows.items()):
            # Check badge text to identify completed/failed rows
            badge_text = row._badge.cget("text")
            if badge_text in ("Done", "Failed"):
                row.pack_forget()
                row.destroy()
                del self._rows[entry_id]


# ── Drive Accounts Dialog ────────────────────────────────────────────────────

class DriveAccountsDialog(ctk.CTkToplevel):
    """Manage saved Google Drive accounts and choose which one is active."""

    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.title("Google Drive Accounts")
        self.geometry("500x400")
        self.configure(fg_color=BG)
        self.resizable(False, True)
        self.grab_set()
        self._cfg = cfg
        self.account_changed = False

        ctk.CTkLabel(self, text="Google Drive Accounts",
                     font=FONT_TITLE, text_color=TEXT).pack(padx=20, pady=(20, 4))
        ctk.CTkLabel(self, text="Manage which account this app uploads to.",
                     font=FONT_LABEL, text_color=TEXT2).pack(padx=20, pady=(0, 12))

        self._list_frame = ctk.CTkScrollableFrame(self, fg_color=BG2, corner_radius=10,
                                                   border_color=BORDER, border_width=1)
        self._list_frame.pack(fill="both", expand=True, padx=20)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(10, 16))
        ctk.CTkButton(btn_row, text="+ Add Account", height=34, corner_radius=8,
                      fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
                      command=self._add_account).pack(side="left")
        ctk.CTkButton(btn_row, text="Done", width=80, height=34, corner_radius=8,
                      fg_color=BG3, hover_color=BORDER, text_color=TEXT,
                      command=self.destroy).pack(side="right")

        self._rebuild_list()

    def _rebuild_list(self):
        for w in self._list_frame.winfo_children():
            w.destroy()
        accounts = drive_accounts.list_accounts()
        active_id = self._cfg.get("active_drive_account_id", "")
        if not accounts:
            ctk.CTkLabel(self._list_frame,
                         text="No accounts saved yet. Click + Add Account.",
                         font=FONT_LABEL, text_color=TEXT2).pack(padx=16, pady=20)
            return
        for acct in accounts:
            row = ctk.CTkFrame(self._list_frame, fg_color=BG3, corner_radius=8)
            row.pack(fill="x", padx=8, pady=(4, 0))
            row.columnconfigure(1, weight=1)

            is_active = acct["id"] == active_id
            dot = "●" if is_active else "○"
            dot_color = ACCENT if is_active else TEXT2
            ctk.CTkLabel(row, text=dot, font=FONT_LABEL,
                         text_color=dot_color, width=20).grid(row=0, column=0, padx=(10, 6), pady=10)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.grid(row=0, column=1, sticky="ew", pady=6)
            ctk.CTkLabel(info, text=acct["name"], font=("SF Pro Text", 12, "bold"),
                         text_color=TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(info, text=acct["email"], font=FONT_SMALL,
                         text_color=TEXT2, anchor="w").pack(anchor="w")

            btns = ctk.CTkFrame(row, fg_color="transparent")
            btns.grid(row=0, column=2, padx=(0, 10), pady=10)
            if is_active:
                ctk.CTkLabel(btns, text="Active", font=FONT_SMALL,
                             text_color=ACCENT).pack(side="left", padx=(0, 8))
            else:
                acct_id = acct["id"]
                ctk.CTkButton(btns, text="Use", width=52, height=28, corner_radius=6,
                              fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
                              command=lambda aid=acct_id: self._set_active(aid)
                              ).pack(side="left", padx=(0, 6))
            acct_id = acct["id"]
            ctk.CTkButton(btns, text="✕", width=28, height=28, corner_radius=6,
                          fg_color=BG2, hover_color=RED, text_color=TEXT2,
                          command=lambda aid=acct_id: self._remove(aid)
                          ).pack(side="left")

    def _set_active(self, account_id: str):
        self._cfg["active_drive_account_id"] = account_id
        self.account_changed = True
        self._rebuild_list()

    def _remove(self, account_id: str):
        drive_accounts.remove_account(account_id)
        if self._cfg.get("active_drive_account_id") == account_id:
            self._cfg["active_drive_account_id"] = ""
            self.account_changed = True
        self._rebuild_list()

    def _add_account(self):
        dlg = AddAccountDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            self._cfg["active_drive_account_id"] = dlg.result["id"]
            self.account_changed = True
            self._rebuild_list()


class AddAccountDialog(ctk.CTkToplevel):
    """Prompt for an optional account name, credentials file, then run the OAuth flow."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Add Google Account")
        self.geometry("400x265")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.grab_set()
        self.result = None
        self._creds_path: str = ""

        ctk.CTkLabel(self, text="Add Google Drive Account",
                     font=("SF Pro Text", 14, "bold"), text_color=TEXT).pack(padx=20, pady=(20, 4))
        ctk.CTkLabel(self, text="A browser window will open for Google sign-in.",
                     font=FONT_LABEL, text_color=TEXT2).pack(padx=20, pady=(0, 12))

        form = ctk.CTkFrame(self, fg_color=BG2, corner_radius=10,
                            border_color=BORDER, border_width=1)
        form.pack(fill="x", padx=20)
        form.columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Nickname", font=FONT_LABEL,
                     text_color=TEXT2, anchor="w").grid(row=0, column=0, padx=(14, 8), pady=(12, 6), sticky="w")
        self._name_var = ctk.StringVar()
        ctk.CTkEntry(form, textvariable=self._name_var, placeholder_text="e.g. Personal, Work (optional)",
                     fg_color=BG3, border_color=BORDER, text_color=TEXT,
                     height=32, corner_radius=6).grid(row=0, column=1, columnspan=2, padx=(0, 14), pady=(12, 6), sticky="ew")

        ctk.CTkLabel(form, text="Credentials File", font=FONT_LABEL,
                     text_color=TEXT2, anchor="w").grid(row=1, column=0, padx=(14, 8), pady=(6, 12), sticky="w")
        self._creds_label = ctk.CTkLabel(form, text="— not selected —",
                                          font=FONT_SMALL, text_color=TEXT2, anchor="w")
        self._creds_label.grid(row=1, column=1, padx=(0, 6), pady=(6, 12), sticky="ew")
        ctk.CTkButton(form, text="Browse…", width=70, height=28, corner_radius=6,
                      fg_color=BG3, hover_color=BORDER, text_color=TEXT,
                      command=self._browse_credentials).grid(row=1, column=2, padx=(0, 14), pady=(6, 12))

        self._status = ctk.CTkLabel(self, text="", font=FONT_SMALL, text_color=TEXT2)
        self._status.pack(pady=(8, 0))

        self._connect_btn = ctk.CTkButton(self, text="Connect Google Account",
                                           height=36, corner_radius=8,
                                           fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
                                           font=("SF Pro Text", 12, "bold"),
                                           state="disabled",
                                           command=self._connect)
        self._connect_btn.pack(padx=20, pady=(8, 16), fill="x")

    def _browse_credentials(self):
        path = filedialog.askopenfilename(
            title="Select credentials.json",
            filetypes=[("JSON", "*.json"), ("All files", "*")],
        )
        if path:
            self._creds_path = path
            self._creds_label.configure(text=Path(path).name, text_color=TEXT)
            self._connect_btn.configure(state="normal")

    def _connect(self):
        self._connect_btn.configure(state="disabled", text="Connecting…")
        self._status.configure(text="Browser opening for Google sign-in…")
        name = self._name_var.get().strip()
        threading.Thread(target=self._do_oauth, args=(name,), daemon=True).start()

    def _do_oauth(self, name: str):
        try:
            acct = drive_accounts.add_account(self._creds_path, display_name=name)
            self.result = acct
            self.after(0, self.destroy)
        except Exception as e:
            self.after(0, lambda: self._connect_btn.configure(state="normal", text="Connect Google Account"))
            self.after(0, lambda: self._status.configure(text=f"Error: {e}", text_color=RED))


# ── Main App ──────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Drive Uploader")
        self.geometry("720x740")
        self.minsize(600, 500)
        self.configure(fg_color=BG)

        self._cfg = config.load()
        self._state = StateManager()
        self._drive_service = None
        self._folders = []
        self._progress_queue: queue.Queue = queue.Queue()
        self._active_workers: dict[str, tuple[threading.Thread, threading.Event]] = {}

        self._build_ui()
        self._update_account_label()
        self._handle_startup_state()
        self._poll_progress()

        # Connect to Drive in background
        threading.Thread(target=self._init_drive, daemon=True).start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI Construction ───────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 0))
        ctk.CTkLabel(hdr, text="Drive Uploader",
                     font=FONT_TITLE, text_color=TEXT).pack(side="left")
        self._status_dot = ctk.CTkLabel(hdr, text="● Connecting",
                                         font=FONT_SMALL, text_color=TEXT2)
        self._status_dot.pack(side="right")

        divider(self).pack(fill="x", padx=24, pady=(12, 0))

        # Drive folder config
        section_label(self, "Destination").pack(anchor="w", padx=24, pady=(12, 4))
        card = ctk.CTkFrame(self, fg_color=BG2, corner_radius=10,
                            border_color=BORDER, border_width=1)
        card.pack(fill="x", padx=24)
        card.columnconfigure(1, weight=1)

        # Google Account row
        ctk.CTkLabel(card, text="Google Account", font=FONT_LABEL,
                     text_color=TEXT2, anchor="w").grid(
                         row=0, column=0, padx=(16, 8), pady=(14, 6), sticky="w")
        self._account_label = ctk.CTkLabel(card, text="— none selected —",
                                            font=FONT_LABEL, text_color=TEXT2, anchor="w")
        self._account_label.grid(row=0, column=1, padx=(0, 8), pady=(14, 6), sticky="ew")
        ctk.CTkButton(card, text="Manage", width=80, height=30, corner_radius=6,
                      fg_color=BG3, hover_color=BORDER, text_color=TEXT,
                      command=self._manage_accounts).grid(row=0, column=2, padx=(0, 16), pady=(14, 6))

        # Drive Folder row
        ctk.CTkLabel(card, text="Drive Folder", font=FONT_LABEL,
                     text_color=TEXT2, anchor="w").grid(
                         row=1, column=0, padx=(16, 8), pady=(6, 14), sticky="w")
        self._folder_var = ctk.StringVar(value="— loading… —")
        ctk.CTkLabel(card, textvariable=self._folder_var, font=FONT_LABEL,
                     text_color=TEXT, anchor="w").grid(
                         row=1, column=1, padx=(0, 8), pady=(6, 14), sticky="ew")

        btn_col = ctk.CTkFrame(card, fg_color="transparent")
        btn_col.grid(row=1, column=2, padx=(0, 16), pady=(6, 14))
        ctk.CTkButton(btn_col, text="Pick", width=54, height=30, corner_radius=6,
                      fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
                      command=self._pick_drive_folder).pack(side="left", padx=(0, 4))
        ctk.CTkButton(btn_col, text="↺", width=30, height=30, corner_radius=6,
                      fg_color=BG3, hover_color=BORDER, text_color=TEXT,
                      command=lambda: threading.Thread(
                          target=self._init_drive, daemon=True).start()
                      ).pack(side="left")

        divider(self).pack(fill="x", padx=24, pady=12)

        # Action buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=24)
        ctk.CTkButton(btn_row, text="+ Add Files…", height=36, corner_radius=8,
                      fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
                      font=("SF Pro Text", 12, "bold"),
                      command=self._add_files).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Add Folder…", height=36, corner_radius=8,
                      fg_color=BG3, hover_color=BORDER, text_color=TEXT,
                      font=("SF Pro Text", 12, "bold"),
                      command=self._add_folder).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Clear Completed", height=36, corner_radius=8,
                      fg_color=BG3, hover_color=BORDER, text_color=TEXT2,
                      command=self._clear_completed).pack(side="right")

        divider(self).pack(fill="x", padx=24, pady=12)

        # Queue
        section_label(self, "Upload Queue").pack(anchor="w", padx=24, pady=(0, 6))
        self._queue_frame = UploadQueueFrame(self)
        self._queue_frame.pack(fill="both", expand=True, padx=24, pady=(0, 20))

    # ── Startup resume ────────────────────────────────────────────────────

    def _handle_startup_state(self):
        expired = self._state.expire_old_sessions()
        if expired:
            names = ", ".join(expired[:3])
            suffix = f" (+{len(expired)-3} more)" if len(expired) > 3 else ""
            self._show_notice(f"Session expired for: {names}{suffix}\n"
                              "These will restart from the beginning.")

        pending = self._state.get_pending()
        if not pending:
            return

        # Rebuild queue rows for all pending entries
        group_ids_seen = set()
        for entry in pending:
            if entry.group_id and entry.group_id not in group_ids_seen:
                group_ids_seen.add(entry.group_id)
                group_entries = [e for e in pending if e.group_id == entry.group_id]
                self._queue_frame.add_group_header(
                    entry.group_id, entry.group_name or "Folder",
                    len(group_entries))
            self._queue_frame.add_row(entry, self._cancel_upload, self._resume_upload)

        # Ask user whether to resume
        self._show_resume_dialog(len(pending))

    def _show_resume_dialog(self, n: int):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Resume Uploads")
        dlg.geometry("360x180")
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        s = "s" if n != 1 else ""
        ctk.CTkLabel(dlg, text=f"Resume {n} incomplete upload{s}?",
                     font=("SF Pro Text", 14, "bold"), text_color=TEXT).pack(
                         padx=20, pady=(24, 8))
        ctk.CTkLabel(dlg,
                     text="Uploads will continue from where they left off.",
                     font=FONT_LABEL, text_color=TEXT2).pack(padx=20)

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(padx=20, pady=20, fill="x")

        def do_resume():
            dlg.destroy()
            # Workers start after Drive connects

        def do_clear():
            self._state.clear_all_pending()
            # Remove all rows from queue
            for entry_id in list(self._queue_frame._rows.keys()):
                row = self._queue_frame._rows.pop(entry_id)
                row.pack_forget()
                row.destroy()
            self._queue_frame._group_headers.clear()
            dlg.destroy()

        ctk.CTkButton(btn_row, text="Resume", height=36, corner_radius=8,
                      fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
                      font=("SF Pro Text", 12, "bold"),
                      command=do_resume).pack(side="left", expand=True, fill="x", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Clear All", height=36, corner_radius=8,
                      fg_color=RED, hover_color="#cc2a20", text_color=TEXT,
                      command=do_clear).pack(side="left", expand=True, fill="x")

    def _show_notice(self, msg: str):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Notice")
        dlg.geometry("380x160")
        dlg.configure(fg_color=BG)
        dlg.resizable(False, False)
        dlg.grab_set()
        ctk.CTkLabel(dlg, text=msg, font=FONT_LABEL, text_color=TEXT2,
                     wraplength=340, justify="left").pack(padx=20, pady=(20, 12))
        ctk.CTkButton(dlg, text="OK", width=80, height=32, corner_radius=8,
                      fg_color=ACCENT, hover_color="#0060df", text_color=TEXT,
                      command=dlg.destroy).pack(pady=(0, 16))

    # ── Drive connection ──────────────────────────────────────────────────

    def _init_drive(self):
        self.after(0, lambda: self._status_dot.configure(text="● Connecting", text_color=YELLOW))
        try:
            account_id = self._cfg.get("active_drive_account_id", "")
            if account_id and drive_accounts.token_path(account_id).exists():
                self._drive_service = drive_accounts.get_service(account_id)
            else:
                self._drive_service = drivelib.get_service()
            self._folders = drivelib.list_folders(self._drive_service)
            self.after(0, lambda: self._status_dot.configure(
                text=f"● Online ({len(self._folders)} folders)", text_color=GREEN))
            saved_name = self._cfg.get("drive_folder_name", "")
            if saved_name:
                self.after(0, lambda: self._folder_var.set(saved_name))
            else:
                self.after(0, lambda: self._folder_var.set("— click Pick to choose —"))
            # Now that we're connected, start any pending uploads
            self.after(0, self._start_next_uploads)
        except FileNotFoundError as e:
            self.after(0, lambda: self._status_dot.configure(
                text="● No credentials", text_color=RED))
            self.after(0, lambda: self._folder_var.set("⚠ credentials.json missing"))
        except Exception as e:
            self.after(0, lambda: self._status_dot.configure(
                text="● Connection failed", text_color=RED))
            self.after(0, lambda: self._folder_var.set("⚠ connection failed"))

    # ── Drive account management ──────────────────────────────────────────

    def _update_account_label(self):
        account_id = self._cfg.get("active_drive_account_id", "")
        if account_id:
            acct = drive_accounts.get_account(account_id)
            if acct:
                self._account_label.configure(
                    text=f"{acct['name']}  ·  {acct['email']}", text_color=TEXT)
                return
        self._account_label.configure(text="— none selected —", text_color=TEXT2)

    def _manage_accounts(self):
        dlg = DriveAccountsDialog(self, self._cfg)
        self.wait_window(dlg)
        if dlg.account_changed:
            config.save(self._cfg)
            self._update_account_label()
            threading.Thread(target=self._init_drive, daemon=True).start()

    # ── Folder picker ─────────────────────────────────────────────────────

    def _pick_drive_folder(self):
        if not self._folders:
            self._show_notice("Drive folders not loaded yet.\nClick ↺ to reconnect first.")
            return
        dlg = FolderPickerDialog(self, self._folders)
        self.wait_window(dlg)
        if dlg.result_id:
            self._cfg["drive_folder_id"] = dlg.result_id
            self._cfg["drive_folder_name"] = dlg.result_name
            self._folder_var.set(dlg.result_name)
            config.save(self._cfg)

    # ── Add files / folders ───────────────────────────────────────────────

    def _add_files(self):
        if not self._cfg.get("drive_folder_id"):
            self._show_notice("Please select a Drive folder first.")
            return
        paths = filedialog.askopenfilenames(
            title="Select files to upload",
            initialdir="/Volumes",
        )
        if not paths:
            return
        folder_id = self._cfg["drive_folder_id"]
        folder_name = self._cfg["drive_folder_name"]
        added = 0
        for path in paths:
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            entry = UploadEntry.new(path, size, folder_id, folder_name)
            if self._state.add(entry):
                self._queue_frame.add_row(entry, self._cancel_upload, self._resume_upload)
                added += 1
        if added:
            self._start_next_uploads()

    def _add_folder(self):
        if not self._cfg.get("drive_folder_id"):
            self._show_notice("Please select a Drive folder first.")
            return
        folder_path = filedialog.askdirectory(
            title="Select folder to upload",
            initialdir="/Volumes",
        )
        if not folder_path:
            return

        folder_name = Path(folder_path).name
        dlg = FolderModeDialog(self, folder_name)
        self.wait_window(dlg)
        if not dlg.result:
            return

        if dlg.result == "zip":
            self._add_folder_as_zip(folder_path)
        else:
            self._add_folder_as_structure(folder_path)

    def _add_folder_as_zip(self, folder_path: str):
        folder_name = Path(folder_path).name
        folder_id = self._cfg["drive_folder_id"]
        folder_dest = self._cfg["drive_folder_name"]

        # Placeholder entry — real size unknown until compression finishes
        entry = UploadEntry.new(
            local_path=folder_path,
            file_size=0,
            folder_id=folder_id,
            folder_name=folder_dest,
            status="compressing",
        )
        entry.file_name = folder_name + ".zip"
        self._state.add(entry)
        row = self._queue_frame.add_row(entry, self._cancel_upload, self._resume_upload)
        row.set_zip_progress(0, 1)  # show initial compressing state

        worker = ZipWorker(folder_path, entry.id, self._state, self._progress_queue)
        t = threading.Thread(target=worker.run, daemon=True)
        t.start()

    def _add_folder_as_structure(self, folder_path: str):
        if not self._drive_service:
            self._show_notice("Drive not connected yet. Please wait and try again.")
            return
        # Create Drive folder hierarchy in a background thread
        threading.Thread(
            target=self._prepare_folder_structure,
            args=(folder_path,),
            daemon=True,
        ).start()

    def _prepare_folder_structure(self, folder_path: str):
        """Walk folder, create Drive subfolders, add file entries to queue."""
        try:
            folder_name = Path(folder_path).name
            parent_id = self._cfg["drive_folder_id"]
            parent_display = self._cfg["drive_folder_name"]

            # Create root folder in Drive
            root_drive_id = drivelib.create_drive_folder(
                self._drive_service, folder_name, parent_id)
            root_display = f"{parent_display} / {folder_name}"

            folder_map = {folder_path: root_drive_id}
            group_id = str(uuid.uuid4())
            all_file_paths = []

            for dirpath, dirnames, filenames in os.walk(folder_path):
                parent_local = str(Path(dirpath).parent)
                if dirpath != folder_path and parent_local in folder_map:
                    sub_id = drivelib.create_drive_folder(
                        self._drive_service, Path(dirpath).name, folder_map[parent_local])
                    folder_map[dirpath] = sub_id
                for fn in filenames:
                    all_file_paths.append(os.path.join(dirpath, fn))

            entries = []
            for fp in all_file_paths:
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    continue
                dir_id = folder_map.get(str(Path(fp).parent), root_drive_id)
                entry = UploadEntry.new(
                    local_path=fp,
                    file_size=size,
                    folder_id=dir_id,
                    folder_name=root_display,
                    group_id=group_id,
                    group_name=folder_name,
                )
                entries.append(entry)

            def _add_to_ui():
                self._queue_frame.add_group_header(group_id, folder_name, len(entries))
                for entry in entries:
                    self._state.add(entry)
                    self._queue_frame.add_row(entry, self._cancel_upload, self._resume_upload)
                self._start_next_uploads()

            self.after(0, _add_to_ui)

        except Exception as e:
            self.after(0, lambda: self._show_notice(
                f"Failed to create folder structure in Drive:\n{e}"))

    # ── Upload worker management ──────────────────────────────────────────

    def _start_next_uploads(self):
        """Start upload workers for queued entries up to MAX_CONCURRENT."""
        if not self._drive_service:
            return
        available = MAX_CONCURRENT - len(self._active_workers)
        if available <= 0:
            return
        queued = self._state.get_queued()
        for entry in queued[:available]:
            if entry.id in self._active_workers:
                continue
            stop_event = threading.Event()
            worker = UploadWorker(
                entry, self._state,
                self._progress_queue, stop_event,
                account_id=self._cfg.get("active_drive_account_id", ""),
            )
            t = threading.Thread(target=worker.run, daemon=True)
            self._active_workers[entry.id] = (t, stop_event)
            row = self._queue_frame.get_row(entry.id)
            if row:
                self._set_badge_uploading(row)
            t.start()

    def _set_badge_uploading(self, row: UploadRowFrame):
        row.set_uploading()

    def _cancel_upload(self, entry_id: str):
        if entry_id in self._active_workers:
            _, stop_event = self._active_workers[entry_id]
            stop_event.set()
            # Worker catches StopRequested, saves session, posts "cancelled"
        else:
            # Queued but not started — mark as failed (nothing to resume)
            self._state.update(entry_id, status="failed", error="Cancelled by user")
            row = self._queue_frame.get_row(entry_id)
            if row:
                row.set_failed("Cancelled by user")

    def _resume_upload(self, entry_id: str):
        """Resume a paused upload in the current session."""
        if entry_id in self._active_workers:
            return  # already running
        row = self._queue_frame.get_row(entry_id)
        if row:
            # Immediately switch button back to ✕ so user sees the response
            row.set_uploading()
        # State is already "queued" with resumable_uri saved — kick the scheduler
        self._start_next_uploads()

    # ── Progress polling ──────────────────────────────────────────────────

    def _poll_progress(self):
        try:
            while True:
                msg = self._progress_queue.get_nowait()
                kind = msg[0]
                entry_id = msg[1]
                row = self._queue_frame.get_row(entry_id)

                if kind == "progress":
                    if row:
                        row.update_progress(msg[2])

                elif kind == "confirmed":
                    if row:
                        row.confirm_progress(msg[2])
                    # Update group header if applicable
                    entry = self._state.get(entry_id)
                    if entry and entry.group_id:
                        hdr = self._queue_frame.get_group_header(entry.group_id)
                        if hdr:
                            _, _, n_done, n_total = self._state.get_group_progress(
                                entry.group_id)
                            hdr.update_count(n_done)

                elif kind == "retrying":
                    if row:
                        row.set_retrying(msg[2])

                elif kind == "done":
                    self._on_upload_done(entry_id, msg[2])

                elif kind == "error":
                    self._on_upload_error(entry_id, msg[2])

                elif kind == "cancelled":
                    self._active_workers.pop(entry_id, None)
                    row = self._queue_frame.get_row(entry_id)
                    if row:
                        row.set_paused()
                    self._start_next_uploads()

                elif kind == "zip_progress":
                    if row:
                        row.set_zip_progress(msg[2], msg[3])

                elif kind == "zip_done":
                    zip_path, zip_size, zip_name = msg[2], msg[3], msg[4]
                    if row:
                        row.set_upload_ready(zip_name, zip_size)
                    self._start_next_uploads()

        except queue.Empty:
            pass
        self.after(200, self._poll_progress)

    def _on_upload_done(self, entry_id: str, drive_file_id: str):
        self._active_workers.pop(entry_id, None)
        row = self._queue_frame.get_row(entry_id)
        if row:
            row.set_done()

        # Clean up temp ZIP if applicable
        entry = self._state.get(entry_id)
        if entry and entry.is_temp_zip and entry.local_path:
            try:
                tmp_dir = str(Path(entry.local_path).parent)
                if tempfile.gettempdir() in tmp_dir or "drive-uploader-" in tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

        # Update group header
        if entry and entry.group_id:
            hdr = self._queue_frame.get_group_header(entry.group_id)
            if hdr:
                _, _, n_done, n_total = self._state.get_group_progress(entry.group_id)
                hdr.update_count(n_done)

        self._start_next_uploads()

    def _on_upload_error(self, entry_id: str, error_msg: str):
        self._active_workers.pop(entry_id, None)
        row = self._queue_frame.get_row(entry_id)
        if row:
            row.set_failed(error_msg)
        self._start_next_uploads()

    # ── UI actions ────────────────────────────────────────────────────────

    def _clear_completed(self):
        self._state.clear_completed()
        self._queue_frame.remove_completed_rows()

    # ── Window close ──────────────────────────────────────────────────────

    def _on_close(self):
        # Signal all active workers to stop (they save state before exiting)
        for entry_id, (thread, stop_event) in list(self._active_workers.items()):
            stop_event.set()
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
