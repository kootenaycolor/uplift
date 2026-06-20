#!/usr/bin/env python3
"""Uplift v2 — PyQt6 port of main.py"""

import json
import os
import queue
import shutil
import socket
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from collections import deque, defaultdict
from datetime import datetime, date as _date, timezone
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtSignal, QPointF, QRectF,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPainterPath,
    QPen, QBrush, QLinearGradient, QFontMetrics, QPalette, QCursor,
    QPixmap, QIcon,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QPushButton, QHBoxLayout, QVBoxLayout, QScrollArea,
    QSizePolicy, QSpacerItem, QStackedWidget, QDialog,
    QLineEdit, QPlainTextEdit, QComboBox, QFileDialog,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QInputDialog,
    QGraphicsDropShadowEffect, QSpinBox, QMenu,
    QTextEdit, QListView, QTreeView,
    QListWidget, QListWidgetItem,
)

from googleapiclient.errors import HttpError
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import config
import drive as drivelib
import drive_accounts
import mailer
import sender_profile
from drive import StopRequested
from state import StateManager, UploadEntry

# ── Design tokens ──────────────────────────────────────────────────────────────
BG         = "#F1F0F0"
SURFACE    = "#FFFFFF"
SURFACE2   = "#F7F7F7"
INK        = "#000000"
GRAPHITE   = "#404040"
STONE      = "#6B6B6B"
MIST       = "#D9D9D9"
TEAL       = "#0089a6"
TEAL_DEEP  = "#04657e"
TEAL_MID   = "#3d9eb6"
TEAL_SOFT  = "#77b2c6"
TEAL_PALE  = "#cfe1e7"
TEAL_WASH  = "#eaf2f5"
SIDEBAR_BG = "#eae9e9"
GREEN      = "#197a26"
RED        = "#cc2222"
YELLOW     = "#9b6e00"

# Apple HIG system colours for dark mode — better contrast on dark surfaces
GREEN_DARK  = "#30D158"
RED_DARK    = "#FF453A"
YELLOW_DARK = "#FF9F0A"

# Interactive surface states
SURFACE_HOVER         = "#F0F0F0"
SURFACE_HOVER_DARK    = "#3A3A3C"
SURFACE_PRESSED       = "#E5E5E5"
SURFACE_PRESSED_DARK  = "#323234"

# Disabled control text
INK_DISABLED      = "#B0B0B0"
INK_DISABLED_DARK = "#6B6B6B"

# Focus ring — same value as TEAL_MID, separate token for clarity
FOCUS_RING = "#3d9eb6"

# Toggle switch colours — overridable independently of the shared palette
TOGGLE_ON        = "#3d9eb6"  # on-track (defaults to TEAL_MID)
TOGGLE_OFF       = "#D9D9D9"  # off-track, light mode (defaults to MIST)
TOGGLE_OFF_DARK  = "#48484A"  # off-track, dark mode (defaults to MIST_DARK)
TOGGLE_KNOB      = "#FFFFFF"  # knob/circle colour

# Spacing — overridable via ~/.uplift-theme.json (values are pixels)
SP_PANEL_H  = 20
SP_PANEL_V  = 10
SP_QUEUE_H  = 20
SP_TILE_GAP = 10
SP_ROW_GAP  = 4

# Semi-transparent glass variants (light mode)
BG_GLASS       = "rgba(241, 240, 240, 0.60)"
SURFACE_GLASS  = "rgba(255, 255, 255, 0.55)"
SURFACE2_GLASS = "rgba(247, 247, 247, 0.58)"

# Dark mode base colors
BG_DARK       = "#1C1C1E"
SURFACE_DARK  = "#2C2C2E"
SURFACE2_DARK = "#3A3A3C"
INK_DARK      = "#FFFFFF"
STONE_DARK    = "#98989D"
MIST_DARK     = "#48484A"
GRAPHITE_DARK = "#C0C0C0"

# Dark mode glass variants
BG_GLASS_DARK       = "rgba(28, 28, 30, 0.60)"
SURFACE_GLASS_DARK  = "rgba(44, 44, 46, 0.55)"
SURFACE2_GLASS_DARK = "rgba(58, 58, 60, 0.58)"

# Runtime dark-mode flag — set in App.__init__ before _build_ui()
_IS_DARK: bool = False
# Set True after per-widget glass is applied; panels switch to transparent bg
GLASS_PANELS_ACTIVE: bool = False


def _th_bg()           -> str: return BG_DARK       if _IS_DARK else BG
def _th_surface()      -> str: return SURFACE_DARK   if _IS_DARK else SURFACE
def _th_surface2()     -> str: return SURFACE2_DARK  if _IS_DARK else SURFACE2
def _th_ink()          -> str: return INK_DARK       if _IS_DARK else INK
def _th_stone()        -> str: return STONE_DARK     if _IS_DARK else STONE
def _th_mist()         -> str: return MIST_DARK      if _IS_DARK else MIST
def _th_graphite()     -> str: return GRAPHITE_DARK  if _IS_DARK else GRAPHITE
def _th_bg_glass()     -> str: return BG_GLASS_DARK      if _IS_DARK else BG_GLASS
def _th_surface_glass() -> str: return SURFACE_GLASS_DARK if _IS_DARK else SURFACE_GLASS
def _th_surface2_glass() -> str: return SURFACE2_GLASS_DARK if _IS_DARK else SURFACE2_GLASS
def _th_toggle_on()       -> str: return TOGGLE_ON
def _th_toggle_off()      -> str: return TOGGLE_OFF_DARK    if _IS_DARK else TOGGLE_OFF
def _th_toggle_knob()     -> str: return TOGGLE_KNOB
def _th_green()           -> str: return GREEN_DARK          if _IS_DARK else GREEN
def _th_red()             -> str: return RED_DARK            if _IS_DARK else RED
def _th_yellow()          -> str: return YELLOW_DARK         if _IS_DARK else YELLOW
def _th_surface_hover()   -> str: return SURFACE_HOVER_DARK  if _IS_DARK else SURFACE_HOVER
def _th_surface_pressed() -> str: return SURFACE_PRESSED_DARK if _IS_DARK else SURFACE_PRESSED
def _th_ink_disabled()    -> str: return INK_DISABLED_DARK   if _IS_DARK else INK_DISABLED
def _th_teal_link()       -> str: return TEAL_MID            if _IS_DARK else TEAL_DEEP


def _load_theme_overrides() -> None:
    """Apply user colour/glass overrides from ~/.uplift-theme.json (written by theme_editor.py)."""
    theme_path = Path.home() / ".uplift-theme.json"
    if not theme_path.exists():
        return
    try:
        data = json.loads(theme_path.read_text())
    except Exception:
        return
    g = globals()

    def _rgba(hex_c: str, alpha: float) -> str:
        c = QColor(hex_c)
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.2f})"

    for key in ("BG", "SURFACE", "SURFACE2", "INK", "STONE", "MIST", "GRAPHITE",
                "BG_DARK", "SURFACE_DARK", "SURFACE2_DARK", "INK_DARK",
                "STONE_DARK", "MIST_DARK", "GRAPHITE_DARK",
                "TEAL", "TEAL_DEEP", "TEAL_MID", "TEAL_SOFT", "TEAL_PALE", "TEAL_WASH",
                "GREEN", "RED", "YELLOW",
                "GREEN_DARK", "RED_DARK", "YELLOW_DARK",
                "SURFACE_HOVER", "SURFACE_HOVER_DARK",
                "SURFACE_PRESSED", "SURFACE_PRESSED_DARK",
                "INK_DISABLED", "INK_DISABLED_DARK", "FOCUS_RING",
                "TOGGLE_ON", "TOGGLE_OFF", "TOGGLE_OFF_DARK", "TOGGLE_KNOB"):
        if key in data:
            g[key] = data[key]

    al = float(data.get("GLASS_ALPHA_LIGHT", 0.60))
    ad = float(data.get("GLASS_ALPHA_DARK",  0.60))
    g["BG_GLASS"]            = _rgba(g["BG"],           al)
    g["SURFACE_GLASS"]       = _rgba(g["SURFACE"],      al)
    g["SURFACE2_GLASS"]      = _rgba(g["SURFACE2"],     al)
    g["BG_GLASS_DARK"]       = _rgba(g["BG_DARK"],      ad)
    g["SURFACE_GLASS_DARK"]  = _rgba(g["SURFACE_DARK"], ad)
    g["SURFACE2_GLASS_DARK"] = _rgba(g["SURFACE2_DARK"],ad)

    for sp_key, default in (("SP_PANEL_H", 20), ("SP_PANEL_V", 10),
                             ("SP_QUEUE_H", 20), ("SP_TILE_GAP", 10), ("SP_ROW_GAP", 4)):
        if sp_key in data:
            g[sp_key] = int(data[sp_key])


_load_theme_overrides()   # runs once at import time, before any class is instantiated

EMAIL_SUBJECT_DEFAULT = "Your file is ready: {filename}"
EMAIL_BODY_DEFAULT = "Hi,\n\nYour file is ready to download:\n{link}\n\nBest,\n{sender_name}"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mxf", ".r3d", ".braw", ".mkv",
                    ".avi", ".prores", ".dng"}
LOG_PATH  = Path.home() / ".uplift-log.txt"
JOBS_PATH = Path.home() / ".uplift-jobs.json"
MAX_CONCURRENT = 1

def _c(hex_str: str) -> QColor:
    return QColor(hex_str)

# ── Font helpers ───────────────────────────────────────────────────────────────
_FONT_DIR = Path(__file__).parent / "fonts"

def _load_fonts():
    for fname in ["ProximaNova-Light.ttf", "ProximaNova-Regular.ttf",
                  "ProximaNova-Semibold.ttf", "Lato-Black.ttf"]:
        p = _FONT_DIR / fname
        if p.exists():
            QFontDatabase.addApplicationFont(str(p))

def _f(family: str, size: int, weight=QFont.Weight.Normal, italic=False) -> QFont:
    f = QFont(family, size)
    f.setWeight(weight)
    f.setItalic(italic)
    return f

def F_PROXIMA(size, bold=False):
    return _f("Proxima Nova", size, QFont.Weight.DemiBold if bold else QFont.Weight.Normal)

def F_SEMIBOLD(size):
    return _f("Proxima Nova", size, QFont.Weight.DemiBold)

def F_BODY(size=13):  return _f("Proxima Nova", size)
def F_LABEL(size=10): return _f("Proxima Nova", size, QFont.Weight.Bold)
def F_MONO(size=11):  return _f("SF Mono", size)
def F_WORDMARK():     return _f("Lato", 15, QFont.Weight.Black)

# ── Stylesheet ─────────────────────────────────────────────────────────────────
def _build_app_qss() -> str:
    bg        = _th_bg()
    surface   = _th_surface()
    s2        = _th_surface2()
    ink       = _th_ink()
    stone     = _th_stone()
    mist      = _th_mist()
    graphite  = _th_graphite()
    s_hover   = _th_surface_hover()
    s_pressed = _th_surface_pressed()
    ink_dis   = _th_ink_disabled()
    teal_link = _th_teal_link()
    return f"""
/* ── Global reset ─────────────────────────────────────────── */
QWidget {{
    font-family: "Proxima Nova", "Trebuchet MS", Arial, sans-serif;
    font-size: 13px;
    color: {ink};
}}

/* Main window transparent — NSGlassEffectView shows through */
QMainWindow, QMainWindow > QWidget {{
    background-color: transparent;
    color: {ink};
}}
/* Dialogs stay opaque */
QDialog, QDialog > QWidget {{
    background-color: {surface};
    color: {ink};
}}

/* ── Scroll areas ─────────────────────────────────────────── */
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {mist};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {mist};
    border-radius: 3px;
    min-width: 20px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

/* ── Labels ───────────────────────────────────────────────── */
QLabel {{
    color: {ink};
    background: transparent;
}}

/* ── Buttons ──────────────────────────────────────────────── */
QPushButton {{
    background: {s2};
    color: {ink};
    border: 1px solid {TEAL_PALE};
    border-radius: 4px;
    padding: 5px 12px;
    font-family: "Proxima Nova", "Trebuchet MS", Arial, sans-serif;
    font-size: 12px;
}}
QPushButton:hover    {{ background: {s_hover};   border-color: {TEAL_MID}; color: {ink}; }}
QPushButton:pressed  {{ background: {s_pressed}; border-color: {TEAL};     color: {ink}; }}
QPushButton:disabled {{ color: {ink_dis}; background: {s2}; border-color: {mist}; }}

QPushButton#primary {{
    background: {TEAL};
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: 600;
    font-size: 12px;
}}
QPushButton#primary:hover    {{ background: {TEAL_DEEP}; color: white; }}
QPushButton#primary:pressed  {{ background: {TEAL_DEEP}; color: white; }}
QPushButton#primary:disabled {{ background: {TEAL_SOFT}; color: {stone}; }}

QPushButton#ghost {{
    background: transparent;
    color: {ink};
    border: 1px solid {TEAL_PALE};
    border-radius: 4px;
    padding: 5px 12px;
    font-size: 12px;
}}
QPushButton#ghost:hover  {{ background: {TEAL_WASH}; color: {ink}; border-color: {TEAL_MID}; }}
QPushButton#ghost:pressed {{ background: {TEAL_PALE}; color: {ink}; border-color: {TEAL}; }}

QPushButton#icon-btn {{
    background: {s2};
    color: {ink};
    border: 1px solid {TEAL_PALE};
    border-radius: 2px;
    padding: 2px;
}}
QPushButton#icon-btn:hover  {{ background: {TEAL_WASH}; border-color: {TEAL_MID}; color: {TEAL_DEEP}; }}
QPushButton#icon-btn:pressed {{ background: {TEAL_PALE}; border-color: {TEAL}; }}

QPushButton#link {{
    background: transparent;
    color: {teal_link};
    border: none;
    padding: 0;
    font-size: 11px;
    text-decoration: underline;
}}
QPushButton#link:hover {{ color: {TEAL}; }}

/* ── Inputs ───────────────────────────────────────────────── */
QLineEdit {{
    background: {surface};
    color: {ink};
    border: 1px solid {TEAL_PALE};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 13px;
    selection-background-color: {TEAL_PALE};
    selection-color: {ink};
}}
QLineEdit:focus {{ border: 2px solid {TEAL_MID}; background: {surface}; }}
QLineEdit:disabled {{ color: {stone}; background: {s2}; border-color: {mist}; }}
QLineEdit::placeholder {{ color: {stone}; }}

QPlainTextEdit {{
    background: {surface};
    color: {ink};
    border: 1px solid {TEAL_PALE};
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: {TEAL_PALE};
    selection-color: {ink};
}}
QPlainTextEdit:focus {{ border: 2px solid {TEAL_MID}; }}

QSpinBox {{
    background: {surface};
    color: {ink};
    border: 1px solid {TEAL_PALE};
    border-radius: 4px;
    padding: 2px 6px;
}}
QSpinBox:focus {{ border: 2px solid {TEAL_MID}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 16px;
    border: none;
    background: {TEAL_WASH};
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {TEAL_PALE}; }}

/* ── ComboBox ─────────────────────────────────────────────── */
QComboBox {{
    background: {surface};
    color: {ink};
    border: 1px solid {TEAL_PALE};
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 26px;
}}
QComboBox:focus {{ border: 2px solid {TEAL_MID}; }}
QComboBox:disabled {{ color: {stone}; background: {s2}; border-color: {mist}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {surface};
    color: {ink};
    border: 1px solid {TEAL_PALE};
    border-radius: 4px;
    selection-background-color: {TEAL_WASH};
    selection-color: {ink};
    outline: none;
    padding: 2px;
}}
QComboBox QAbstractItemView::item {{
    color: {ink};
    background: {surface};
    padding: 4px 8px;
    min-height: 24px;
}}
QComboBox QAbstractItemView::item:hover    {{ background: {TEAL_WASH}; color: {ink}; }}
QComboBox QAbstractItemView::item:selected {{ background: {TEAL_WASH}; color: {ink}; }}

/* ── TreeWidget ───────────────────────────────────────────── */
QTreeWidget {{
    background: {s2};
    color: {ink};
    border: 1px solid {TEAL_PALE};
    border-radius: 4px;
    outline: none;
}}
QTreeWidget::item {{
    color: {ink};
    background: transparent;
    padding: 3px 4px;
    min-height: 22px;
}}
QTreeWidget::item:hover    {{ background: {TEAL_WASH}; color: {ink}; }}
QTreeWidget::item:selected {{ background: {TEAL};      color: white; }}
QTreeWidget::item:selected:hover {{ background: {TEAL_DEEP}; color: white; }}

/* ── Context menus ────────────────────────────────────────── */
QMenu {{
    background: {surface};
    color: {ink};
    border: 1px solid {mist};
    border-radius: 4px;
    padding: 4px 0;
}}
QMenu::item {{
    color: {ink};
    background: transparent;
    padding: 6px 20px 6px 12px;
    font-size: 13px;
}}
QMenu::item:selected  {{ background: {TEAL_WASH}; color: {ink}; }}
QMenu::item:disabled  {{ color: {stone}; }}
QMenu::separator      {{ height: 1px; background: {mist}; margin: 4px 0; }}

/* ── Message boxes ────────────────────────────────────────── */
QMessageBox {{ background: {surface}; color: {ink}; }}
QMessageBox QLabel {{ color: {ink}; background: transparent; }}
QMessageBox QPushButton {{ min-width: 72px; min-height: 28px; }}

/* ── Dialogs ──────────────────────────────────────────────── */
QDialog {{ background: {surface}; color: {ink}; }}

/* ── Tooltips ─────────────────────────────────────────────── */
QToolTip {{
    background: {ink};
    color: {surface};
    border: none;
    border-radius: 2px;
    padding: 4px 8px;
    font-size: 11px;
}}

/* ── Section header ───────────────────────────────────────── */
QLabel#section-header {{
    color: {graphite};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 2px;
}}
"""

# ── Helpers ────────────────────────────────────────────────────────────────────
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

# ── Workers (copied verbatim from main.py logic) ───────────────────────────────

class FolderBatchMonitor:
    POLL_INTERVAL = 3

    def __init__(self, folder: str, stable_secs: int,
                 on_stable, on_status=None, skip_paths: set | None = None,
                 delay_secs: int = 0, extensions: set | None = None,
                 recursive: bool = False, ignore_hidden: bool = True):
        self._folder = Path(folder)
        self._stable_secs = stable_secs
        self._on_stable = on_stable
        self._on_status = on_status or (lambda msg, color: None)
        self._skip_paths: set[str] = set(skip_paths or [])
        self._delay_secs = delay_secs
        self._extensions = extensions  # None = accept all files
        self._recursive = recursive
        self._ignore_hidden = ignore_hidden
        self._start_time = time.time()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="FolderBatchMonitor")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _snapshot(self) -> dict[str, int] | None:
        try:
            iterator = self._folder.rglob("*") if self._recursive else self._folder.iterdir()
            result = {}
            for f in iterator:
                if not f.is_file():
                    continue
                if self._ignore_hidden and f.name.startswith("."):
                    continue
                if self._extensions is not None and f.suffix.lower() not in self._extensions:
                    continue
                path_str = str(f)
                if path_str in self._skip_paths:
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                if st.st_mtime < self._start_time:
                    continue  # skip; don't blacklist — mtime may update while file is still writing
                result[path_str] = st.st_size
            return result
        except OSError:
            return None

    def _run(self):
        prev: dict | None = None
        stable_ticks = 0
        needed = max(1, self._stable_secs // self.POLL_INTERVAL)
        while not self._stop_event.wait(self.POLL_INTERVAL):
            snap = self._snapshot()
            if snap is None:
                continue
            n = len(snap)
            if n == 0:
                stable_ticks = 0
                prev = None
                self._on_status("●  Watching — no video files yet", "dim")
                continue
            if snap == prev:
                stable_ticks += 1
                elapsed = stable_ticks * self.POLL_INTERVAL
                if stable_ticks >= needed:
                    if self._delay_secs > 0:
                        self._on_status(
                            f"●  {n} file{'s' if n!=1 else ''} stable — "
                            f"waiting {self._delay_secs}s…", "yellow")
                        if self._stop_event.wait(self._delay_secs):
                            return
                    self._on_status(f"●  {n} file{'s' if n!=1 else ''} stable — zipping…", "green")
                    self._on_stable(list(snap.keys()))
                    return
                self._on_status(
                    f"●  {n} file{'s' if n!=1 else ''} stable "
                    f"{elapsed}s / {self._stable_secs}s…", "yellow")
            else:
                stable_ticks = 0
                prev = snap
                self._on_status(f"●  {n} file{'s' if n!=1 else ''} exporting…", "yellow")


class SubfolderBatchMonitor:
    """Watches a folder for new immediate subfolders. When a subfolder's contents
    stabilize, fires on_subfolder_stable(subfolder_path, files_list) then keeps
    running for subsequent subfolders."""
    POLL_INTERVAL = 4

    def __init__(self, folder: str, stable_secs: int,
                 on_subfolder_stable, on_status=None,
                 skip_subfolders: set | None = None,
                 extensions: set | None = None,
                 ignore_hidden: bool = True):
        self._folder       = Path(folder)
        self._stable_secs  = stable_secs
        self._on_stable    = on_subfolder_stable
        self._on_status    = on_status or (lambda msg, color: None)
        self._skip         = set(skip_subfolders or [])
        self._extensions   = extensions
        self._ignore_hidden = ignore_hidden
        self._stop_event   = threading.Event()
        self._thread       = threading.Thread(target=self._run, daemon=True,
                                              name="SubfolderBatchMonitor")
        # Pre-seed skip with all subfolders that already exist — only NEW ones after
        # this point should be watched (same pattern as ExportHandler.start_scan)
        try:
            for f in self._folder.iterdir():
                if f.is_dir() and not (ignore_hidden and f.name.startswith(".")):
                    self._skip.add(str(f))
        except OSError:
            pass

    def start(self): self._thread.start()
    def stop(self):  self._stop_event.set()

    def _list_subfolders(self) -> list[Path]:
        try:
            return [
                f for f in self._folder.iterdir()
                if f.is_dir()
                and not (self._ignore_hidden and f.name.startswith("."))
                and str(f) not in self._skip
            ]
        except OSError:
            return []

    def _snapshot(self, subfolder: Path) -> dict[str, int] | None:
        try:
            result = {}
            for f in subfolder.rglob("*"):
                if not f.is_file():
                    continue
                if self._ignore_hidden and f.name.startswith("."):
                    continue
                if self._extensions and f.suffix.lower() not in self._extensions:
                    continue
                try:
                    result[str(f)] = f.stat().st_size
                except OSError:
                    pass
            return result
        except OSError:
            return None

    def _run(self):
        needed = max(1, self._stable_secs // self.POLL_INTERVAL)
        watching: dict[str, dict] = {}  # sf_str -> {"prev": snap|None, "ticks": int}
        while not self._stop_event.wait(self.POLL_INTERVAL):
            # Un-skip and reset any previously-processed folder that was deleted —
            # a re-export with the same name should be treated as new.
            for sf_str in list(self._skip):
                if not Path(sf_str).exists():
                    self._skip.discard(sf_str)
            # Also reset watch state for any folder deleted mid-stability-check.
            for sf_str in list(watching.keys()):
                if not Path(sf_str).exists():
                    del watching[sf_str]
            for sf in self._list_subfolders():
                sf_str = str(sf)
                if sf_str not in watching:
                    watching[sf_str] = {"prev": None, "ticks": 0}

            if not watching:
                self._on_status("●  Watching — waiting for new subfolders", "dim")
                continue

            to_fire = []
            for sf_str, state in list(watching.items()):
                snap = self._snapshot(Path(sf_str))
                if snap is None:
                    continue
                if not snap:
                    state["prev"] = snap
                    state["ticks"] = 0
                    continue
                n = len(snap)
                sf_name = Path(sf_str).name
                if snap == state["prev"]:
                    state["ticks"] += 1
                    elapsed = state["ticks"] * self.POLL_INTERVAL
                    if state["ticks"] >= needed:
                        to_fire.append((sf_str, list(snap.keys())))
                    else:
                        self._on_status(
                            f"●  {sf_name}: {n} file(s) stable "
                            f"{elapsed}s / {self._stable_secs}s…", "yellow")
                else:
                    state["ticks"] = 0
                    state["prev"] = snap
                    self._on_status(f"●  {sf_name}: {n} file(s) exporting…", "yellow")

            for sf_str, files in to_fire:
                del watching[sf_str]
                self._skip.add(sf_str)
                self._on_status(f"●  {Path(sf_str).name}: stable — zipping…", "green")
                self._on_stable(sf_str, files)


class ExportHandler(FileSystemEventHandler):
    STABLE_SECS = 10
    POLL_INTERVAL = 2
    SCAN_INTERVAL = 8  # fallback folder scan in case FSEvents miss events

    def __init__(self, on_ready_callback, extensions: set | None = None,
                 recursive: bool = False, ignore_hidden: bool = True):
        super().__init__()
        self._callback = on_ready_callback
        self._seen: set[str] = set()
        self._extensions = extensions  # None = accept all files
        self._recursive = recursive
        self._ignore_hidden = ignore_hidden
        self._start_time = time.time()
        self._stop_event = threading.Event()

    def start_scan(self, folder: str):
        """Pre-seed seen set with existing files, then start polling fallback."""
        root = Path(folder)
        try:
            it = root.rglob("*") if self._recursive else root.iterdir()
            for f in it:
                if (f.is_file()
                        and not (self._ignore_hidden and f.name.startswith("."))
                        and (self._extensions is None or f.suffix.lower() in self._extensions)):
                    self._seen.add(str(f))
        except OSError:
            pass
        threading.Thread(target=self._scan_loop, args=(folder,),
                         daemon=True, name="ExportHandlerScan").start()

    def stop(self):
        self._stop_event.set()

    def _dbg(self, msg: str):
        try:
            with LOG_PATH.open("a") as f:
                f.write(f"[ExportHandler] {msg}\n")
        except Exception:
            pass

    def _scan_loop(self, folder: str):
        root = Path(folder)
        self._dbg(f"scan_loop started: folder={folder} exts={self._extensions} recursive={self._recursive}")
        while not self._stop_event.wait(self.SCAN_INTERVAL):
            try:
                it = root.rglob("*") if self._recursive else root.iterdir()
                found = []
                for f in it:
                    if (f.is_file()
                            and not (self._ignore_hidden and f.name.startswith("."))
                            and (self._extensions is None or f.suffix.lower() in self._extensions)):
                        found.append(str(f))
                        self._try_queue(str(f))
                self._dbg(f"scan tick: found {len(found)} matching files, seen={len(self._seen)}: {found[:5]}")
            except OSError as e:
                self._dbg(f"scan OSError: {e}")

    def on_created(self, event):
        self._dbg(f"on_created: {event.src_path}")
        if not event.is_directory:
            self._try_queue(event.src_path)

    def on_moved(self, event):
        self._dbg(f"on_moved: {event.dest_path}")
        if not event.is_directory:
            self._try_queue(event.dest_path)

    def on_modified(self, event):
        self._dbg(f"on_modified: {event.src_path}")
        if not event.is_directory:
            self._try_queue(event.src_path)

    def _try_queue(self, path: str):
        if path in self._seen:
            return
        p = Path(path)
        if self._ignore_hidden and p.name.startswith("."):
            return
        if self._extensions is not None:
            if p.suffix.lower() not in self._extensions:
                self._dbg(f"skip (ext filter): {path}")
                return
        # Skip files that haven't been modified since watch started
        try:
            if p.stat().st_mtime < self._start_time:
                return  # don't blacklist; mtime updates as file is written
        except OSError:
            return
        self._dbg(f"queuing: {path}")
        self._seen.add(path)
        threading.Thread(target=self._wait_and_queue, args=(path,), daemon=True).start()

    def _wait_and_queue(self, path: str):
        prev_size = -1
        stable_count = 0
        needed = self.STABLE_SECS // self.POLL_INTERVAL
        self._dbg(f"wait_and_queue start: {path} need {needed} stable polls")
        while not self._stop_event.is_set():
            try:
                size = Path(path).stat().st_size
            except OSError as e:
                self._dbg(f"wait_and_queue OSError: {e}")
                return
            if size == prev_size:
                stable_count += 1
                self._dbg(f"wait_and_queue stable {stable_count}/{needed}: {path} size={size}")
                if stable_count >= needed:
                    self._dbg(f"STABLE → firing callback: {path}")
                    self._callback(path)
                    return
            else:
                stable_count = 0
                self._dbg(f"wait_and_queue size change: {path} {prev_size}→{size}")
                prev_size = size
            time.sleep(self.POLL_INTERVAL)


class UploadWorker:
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

    def _countdown_retry(self, entry_id: str, retry_count: int) -> bool:
        wait = min(2 ** (retry_count - 1), 16)
        for remaining in range(wait, 0, -1):
            if self._stop.is_set():
                return False
            self._pq.put(("status", entry_id,
                f"Network error — retrying in {remaining}s  "
                f"(attempt {retry_count}/{self.MAX_RETRIES})"))
            time.sleep(1)
        if self._stop.is_set():
            return False
        self._pq.put(("status", entry_id,
            f"Reconnecting…  (attempt {retry_count}/{self.MAX_RETRIES})"))
        return True

    def run(self):
        entry_id = self._entry.id
        request = None
        wrapper = None
        try:
            self._pq.put(("status", entry_id, "Connecting…"))
            if self._account_id and drive_accounts.token_path(self._account_id).exists():
                service = drive_accounts.build_thread_service(self._account_id)
            else:
                service = drivelib.build_thread_service()

            if self._stop.is_set():
                self._pq.put(("cancelled", entry_id, None))
                return

            if self._entry.resumable_uri:
                self._pq.put(("status", entry_id, "Querying server for progress…"))
                request, wrapper, confirmed = drivelib.restore_upload_request(
                    service, self._entry.local_path, self._entry.folder_id,
                    self._entry.resumable_uri, self._entry.resumable_progress,
                    lambda b: self._pq.put(("progress", entry_id, b)),
                    stop_event=self._stop,
                )
                self._state.update(entry_id, status="in_progress",
                                   resumable_progress=confirmed)
                self._pq.put(("confirmed", entry_id, confirmed))
            else:
                self._pq.put(("status", entry_id, "Starting upload…"))
                request, wrapper = drivelib.create_upload_request(
                    service, self._entry.local_path, self._entry.folder_id,
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
                    retry_count = 0
                    if status and request.resumable_uri:
                        confirmed_bytes = request.resumable_progress or 0
                        self._state.update(entry_id,
                                           resumable_uri=request.resumable_uri,
                                           resumable_progress=confirmed_bytes)
                        self._pq.put(("confirmed", entry_id, confirmed_bytes))
                except StopRequested:
                    saved_uri = (request.resumable_uri if request else None) or self._entry.resumable_uri
                    saved_progress = (request.resumable_progress if request else None) or 0
                    self._state.update(entry_id, status="paused",
                                       resumable_uri=saved_uri,
                                       resumable_progress=saved_progress)
                    self._pq.put(("cancelled", entry_id, None))
                    return
                except (ConnectionError, TimeoutError, BrokenPipeError,
                        socket.timeout, socket.error):
                    retry_count += 1
                    if retry_count > self.MAX_RETRIES:
                        raise
                    self._pq.put(("retrying", entry_id, retry_count))
                    if not self._countdown_retry(entry_id, retry_count):
                        saved_uri = (request.resumable_uri if request else None) or self._entry.resumable_uri
                        saved_progress = (request.resumable_progress if request else None) or 0
                        self._state.update(entry_id, status="paused",
                                           resumable_uri=saved_uri,
                                           resumable_progress=saved_progress)
                        self._pq.put(("cancelled", entry_id, None))
                        return
                except HttpError as e:
                    if e.resp.status in self.RETRY_STATUS_CODES:
                        retry_count += 1
                        if retry_count > self.MAX_RETRIES:
                            raise
                        self._pq.put(("retrying", entry_id, retry_count))
                        if not self._countdown_retry(entry_id, retry_count):
                            saved_uri = (request.resumable_uri if request else None) or self._entry.resumable_uri
                            saved_progress = (request.resumable_progress if request else None) or 0
                            self._state.update(entry_id, status="paused",
                                               resumable_uri=saved_uri,
                                               resumable_progress=saved_progress)
                            self._pq.put(("cancelled", entry_id, None))
                            return
                    else:
                        raise

            drive_file_id = response.get("id", "") if response else ""
            self._state.update(entry_id, status="completed",
                               drive_file_id=drive_file_id,
                               completed_at=datetime.now(timezone.utc).isoformat(),
                               resumable_uri=None,
                               resumable_progress=self._entry.file_size)
            self._pq.put(("done", entry_id, drive_file_id))

        except StopRequested:
            self._state.update(entry_id, status="paused",
                               resumable_uri=self._entry.resumable_uri,
                               resumable_progress=self._entry.resumable_progress)
            self._pq.put(("cancelled", entry_id, None))
        except OSError as e:
            msg = str(e)
            if "No such file" in msg or "not a file" in msg.lower():
                msg = "File not accessible — is the external drive still connected?"
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


class _ZipCancelled(Exception):
    pass


class ZipWorker:
    def __init__(self, folder_path: str, entry_id: str,
                 state: StateManager, pq: queue.Queue,
                 stop_event: threading.Event | None = None,
                 zip_name: str = "", keep_zip: bool = False,
                 output_dir: str | None = None):
        self._folder = folder_path
        self._entry_id = entry_id
        self._state = state
        self._pq = pq
        self._stop = stop_event or threading.Event()
        self._zip_name_override = zip_name  # empty = auto
        self._keep_zip = keep_zip
        self._output_dir = output_dir

    def run(self):
        tmp_dir = None
        try:
            folder_name = Path(self._folder).name
            tmp_dir = tempfile.mkdtemp(prefix="uplift-", dir=self._output_dir)
            custom = self._zip_name_override.strip()
            zip_name = (custom if custom.lower().endswith(".zip") else custom + ".zip") if custom else folder_name + ".zip"
            zip_path = os.path.join(tmp_dir, zip_name)
            all_files = []
            for root, _, files in os.walk(self._folder):
                for f in files:
                    all_files.append(os.path.join(root, f))
            folder_parent = os.path.dirname(self._folder)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
                for i, fp in enumerate(all_files):
                    if self._stop.is_set():
                        raise _ZipCancelled()
                    arcname = os.path.relpath(fp, folder_parent)
                    zf.write(fp, arcname)
                    self._pq.put(("zip_progress", self._entry_id, i + 1, len(all_files)))
            zip_size = os.path.getsize(zip_path)
            self._state.update(self._entry_id, status="queued", local_path=zip_path,
                               file_name=zip_name, file_size=zip_size,
                               is_temp_zip=not self._keep_zip)
            self._pq.put(("zip_done", self._entry_id, zip_path, zip_size, zip_name))
        except _ZipCancelled:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            self._state.update(self._entry_id, status="failed", error="Cancelled")
            self._pq.put(("zip_cancelled", self._entry_id))
        except Exception as e:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            self._state.update(self._entry_id, status="failed", error=str(e))
            self._pq.put(("error", self._entry_id, str(e)))


class ListZipWorker:
    def __init__(self, file_paths: list[str], zip_name: str, entry_id: str,
                 state: StateManager, pq: queue.Queue,
                 stop_event: threading.Event | None = None,
                 keep_zip: bool = False,
                 output_dir: str | None = None):
        self._files = file_paths
        self._zip_name = zip_name
        self._entry_id = entry_id
        self._state = state
        self._pq = pq
        self._stop = stop_event or threading.Event()
        self._keep_zip = keep_zip
        self._output_dir = output_dir

    def run(self):
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="uplift-", dir=self._output_dir)
            zip_path = os.path.join(tmp_dir, self._zip_name)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
                for i, fp in enumerate(self._files):
                    if self._stop.is_set():
                        raise _ZipCancelled()
                    zf.write(fp, Path(fp).name)
                    self._pq.put(("zip_progress", self._entry_id, i + 1, len(self._files)))
            zip_size = os.path.getsize(zip_path)
            self._state.update(self._entry_id, status="queued", local_path=zip_path,
                               file_name=self._zip_name, file_size=zip_size,
                               is_temp_zip=not self._keep_zip)
            self._pq.put(("zip_done", self._entry_id, zip_path, zip_size, self._zip_name))
        except _ZipCancelled:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            self._state.update(self._entry_id, status="failed", error="Cancelled")
            self._pq.put(("zip_cancelled", self._entry_id))
        except Exception as e:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            self._state.update(self._entry_id, status="failed", error=str(e))
            self._pq.put(("error", self._entry_id, str(e)))


class JobWatcher:
    def __init__(self, job: dict, on_file_ready, on_batch_ready, on_status):
        self._job = dict(job)
        self._on_file_ready = on_file_ready
        self._on_batch_ready = on_batch_ready
        self._on_status = on_status
        self._observer: Observer | None = None
        self._export_handler: ExportHandler | None = None
        self._folder_monitor: FolderBatchMonitor | None = None
        self._subfolder_monitor: SubfolderBatchMonitor | None = None
        self._batched_paths: set[str] = set()

    @property
    def job_id(self) -> str:
        return self._job.get("id", "")

    def start(self):
        folder = self._job.get("watch_folder", "").strip()
        if not folder or not Path(folder).is_dir():
            self._on_status("⚠  Invalid watch folder", _th_yellow(), self.job_id)
            return
        jid          = self.job_id
        recursive    = bool(self._job.get("watch_recursive", False))
        extensions   = set(self._job.get("watch_extensions") or []) or None
        ignore_hidden = bool(self._job.get("watch_ignore_hidden", True))
        batch_mode   = bool(self._job.get("watch_batch_mode"))
        name         = Path(folder).name

        if batch_mode and bool(self._job.get("watch_subfolder_zip")):
            # Per-subfolder zip: each new subfolder gets its own zip named after it.
            self._start_subfolder_monitor(folder)
        elif batch_mode:
            # Whole-folder batch zip: wait for all files to stabilize, zip together.
            self._start_folder_monitor(folder)
        else:
            # File-by-file mode: watchdog + polling fallback, upload each file as stable.
            self._export_handler = ExportHandler(
                lambda path: self._on_file_ready(path, jid),
                extensions=extensions, recursive=recursive,
                ignore_hidden=ignore_hidden)
            self._observer = Observer()
            self._export_handler.start_scan(folder)   # pre-seed _seen before observer fires events
            self._observer.schedule(self._export_handler, folder, recursive=recursive)
            self._observer.start()

        self._on_status(f"●  Watching  •  {name}", _th_green(), self.job_id)

    def stop(self):
        if self._export_handler:
            self._export_handler.stop()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
        self._export_handler = None
        if self._folder_monitor:
            self._folder_monitor.stop()
            self._folder_monitor = None
        if self._subfolder_monitor:
            self._subfolder_monitor.stop()
            self._subfolder_monitor = None
        self._batched_paths.clear()
        self._on_status("Stopped", STONE, self.job_id)

    def restart(self, updated_job: dict):
        self.stop()
        self._job = dict(updated_job)
        self.start()

    def _start_folder_monitor(self, folder: str):
        if self._folder_monitor:
            self._folder_monitor.stop()
        stable_secs   = int(self._job.get("watch_batch_stable_secs", 15))
        delay_secs    = int(self._job.get("watch_delay_secs", 0))
        extensions    = set(self._job.get("watch_extensions") or []) or None
        recursive     = bool(self._job.get("watch_recursive", False))
        ignore_hidden = bool(self._job.get("watch_ignore_hidden", True))
        jid = self.job_id
        self._folder_monitor = FolderBatchMonitor(
            folder=folder, stable_secs=stable_secs,
            on_stable=lambda files: self._on_batch_ready(files, jid),
            on_status=lambda msg, color: self._on_status(msg, color, jid),
            skip_paths=set(self._batched_paths),
            delay_secs=delay_secs, extensions=extensions, recursive=recursive,
            ignore_hidden=ignore_hidden)
        self._folder_monitor.start()

    def _start_subfolder_monitor(self, folder: str):
        if self._subfolder_monitor:
            self._subfolder_monitor.stop()
        stable_secs   = int(self._job.get("watch_batch_stable_secs", 15))
        extensions    = set(self._job.get("watch_extensions") or []) or None
        ignore_hidden = bool(self._job.get("watch_ignore_hidden", True))
        jid = self.job_id
        def _on_sf_stable(sf_path, files):
            self._batched_paths.add(sf_path)
            self._on_batch_ready(files, jid, Path(sf_path).name)
        self._subfolder_monitor = SubfolderBatchMonitor(
            folder=folder, stable_secs=stable_secs,
            on_subfolder_stable=_on_sf_stable,
            on_status=lambda msg, color: self._on_status(msg, color, jid),
            skip_subfolders=set(self._batched_paths),
            extensions=extensions, ignore_hidden=ignore_hidden)
        self._subfolder_monitor.start()

    def add_batched_paths(self, paths: list):
        self._batched_paths.update(paths)
        if self._subfolder_monitor:
            return  # SubfolderBatchMonitor is continuous; manages its own skip set
        folder = self._job.get("watch_folder", "")
        if folder and Path(folder).is_dir():
            self._start_folder_monitor(folder)


# ── Visual primitives ──────────────────────────────────────────────────────────

class KToggle(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, parent=None, on: bool = False):
        super().__init__(parent)
        self._on = on
        self._anim_x = 18.0 if on else 2.0
        self._target_x = self._anim_x
        self.setFixedSize(36, 20)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._timer = QTimer(self)
        self._timer.setInterval(12)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        diff = self._target_x - self._anim_x
        self._anim_x += diff * 0.35
        if abs(diff) < 0.5:
            self._anim_x = self._target_x
            self._timer.stop()
        self.update()

    def set(self, on: bool):
        self._on = on
        self._target_x = 18.0 if on else 2.0
        self._timer.start()

    def mousePressEvent(self, e):
        self._on = not self._on
        self._target_x = 18.0 if self._on else 2.0
        self._timer.start()
        self.toggled.emit(self._on)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill_color = _c(_th_toggle_on()) if self._on else _c(_th_toggle_off())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(pill_color))
        p.drawRoundedRect(0, 2, 36, 16, 8, 8)
        p.setBrush(QBrush(_c(_th_toggle_knob())))
        p.setPen(QPen(QColor(0, 0, 0, 40), 0.5))
        x = int(self._anim_x)
        p.drawEllipse(x, 3, 14, 14)
        p.end()


class ElideLabel(QLabel):
    """QLabel that elides with … and shows full text as tooltip."""
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        super().setText(text)

    def setText(self, text: str):
        self._full = text
        self.setToolTip(text)
        self._update_elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_elide()

    def _update_elide(self):
        elided = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(self.width(), 0))
        super().setText(elided)


class PulsingDot(QWidget):
    def __init__(self, size=7, color=TEAL, parent=None):
        super().__init__(parent)
        self._color = _c(color)
        self._alpha = 255
        self._growing = False
        self.setFixedSize(size + 4, size + 4)
        self._size = size
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._pulse)
        self._timer.start()

    def set_color(self, color: str):
        self._color = _c(color)
        self.update()

    def _pulse(self):
        step = 8
        if self._growing:
            self._alpha = min(255, self._alpha + step)
            if self._alpha >= 255: self._growing = False
        else:
            self._alpha = max(80, self._alpha - step)
            if self._alpha <= 80: self._growing = True
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self._color)
        c.setAlpha(self._alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c))
        pad = 2
        p.drawEllipse(pad, pad, self._size, self._size)
        p.end()


def HDivider(color=MIST) -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {color}; border: none;")
    return line


def VDivider(color=MIST) -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFixedWidth(1)
    line.setStyleSheet(f"background: {color}; border: none;")
    return line


class GradientBar(QWidget):
    def __init__(self, pct: float = 0.0, height: int = 4, parent=None):
        super().__init__(parent)
        self._pct = pct
        self.setFixedHeight(height)
        self.setStyleSheet("background: transparent;")

    def set_pct(self, pct: float):
        self._pct = max(0.0, min(1.0, pct))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_c(MIST)))
        p.drawRoundedRect(0, 0, w, h, h / 2, h / 2)
        fill_w = int(w * self._pct)
        if fill_w > 0:
            grad = QLinearGradient(QPointF(0, 0), QPointF(fill_w, 0))
            grad.setColorAt(0, _c(TEAL))
            grad.setColorAt(1, _c(TEAL_SOFT))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(0, 0, fill_w, h, h / 2, h / 2)
        p.end()


class GradientPill(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(QPointF(0, 14), QPointF(28, 14))
        grad.setColorAt(0, _c(TEAL))
        grad.setColorAt(1, _c(TEAL_SOFT))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(0, 0, 28, 28, 14, 14)
        p.setPen(QPen(_c("#FFFFFF"), 1.8, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(QPointF(14, 19), QPointF(14, 10))
        p.drawLine(QPointF(14, 10), QPointF(10, 14))
        p.drawLine(QPointF(14, 10), QPointF(18, 14))
        p.end()


class EnvelopeIcon(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 14)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(_c(TEAL_DEEP), 1.4, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(0.7, 0.7, 14.6, 12.6))
        p.drawLine(QPointF(0.7, 0.7), QPointF(8, 7))
        p.drawLine(QPointF(8, 7), QPointF(15.3, 0.7))
        p.end()


# ── TitleBar ───────────────────────────────────────────────────────────────────

class TitleBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setStyleSheet(f"background: {_th_bg()}; border-bottom: 1px solid {_th_mist()};")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 20, 0)
        lay.setSpacing(0)

        wordmark = QLabel("UPLIFT")
        wordmark.setFont(F_WORDMARK())
        wordmark.setStyleSheet(f"color: {_th_ink()};")
        lay.addWidget(wordmark)
        lay.addStretch()

        self._dot = PulsingDot(size=7, color=STONE)
        lay.addWidget(self._dot)
        lay.addSpacing(6)

        self._status_lbl = QLabel("Connecting…")
        self._status_lbl.setFont(F_BODY(12))
        self._status_lbl.setStyleSheet(f"color: {_th_graphite()};")
        lay.addWidget(self._status_lbl)

        lay.addWidget(VDivider(), 0)
        lay.addSpacing(12)

        self._settings_btn = QPushButton("Settings")
        self._settings_btn.setObjectName("link")
        self._settings_btn.setFont(F_BODY(12))
        self._settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        lay.addWidget(self._settings_btn)

    def update_status(self, text: str, color: str = STONE,
                      dot_color: str = None, pulsing: bool = False):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color};")
        if dot_color:
            self._dot.set_color(dot_color)


# ── FileRow ────────────────────────────────────────────────────────────────────

class FileRow(QFrame):
    def __init__(self, parent, entry: UploadEntry,
                 cancel_cb, resume_cb, retry_cb=None):
        super().__init__(parent)
        self._entry_id   = entry.id
        self._file_size  = entry.file_size
        self._bytes_disp = entry.resumable_progress
        self._bytes_conf = entry.resumable_progress
        self._rate_samples: deque = deque()
        self._rate       = 0.0
        self._status     = "queued"
        self._link_url   = ""
        self._cancel_cb  = cancel_cb
        self._resume_cb  = resume_cb
        self._retry_cb   = retry_cb or resume_cb

        self.setStyleSheet(f"QFrame {{ background: {_th_surface()}; border-bottom: 1px solid {_th_mist()}; }}")

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(10, 6, 10, 2)
        main_lay.setSpacing(0)

        # Top row
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(8)

        fname = entry.file_name
        self._name_lbl = QLabel()
        self._name_lbl.setFont(F_MONO(12))
        self._name_lbl.setStyleSheet(f"color: {_th_ink()};")
        self._name_lbl.setFixedWidth(200)
        metrics = QFontMetrics(self._name_lbl.font())
        self._name_lbl.setText(
            metrics.elidedText(fname, Qt.TextElideMode.ElideMiddle, 200))
        row_lay.addWidget(self._name_lbl)

        self._size_lbl = QLabel(_fmt_size(entry.file_size))
        self._size_lbl.setFont(F_BODY(11))
        self._size_lbl.setStyleSheet(f"color: {_th_stone()};")
        self._size_lbl.setFixedWidth(60)
        self._size_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_lay.addWidget(self._size_lbl)

        self._bar = GradientBar(pct=0.0, height=4)
        row_lay.addWidget(self._bar, 1)

        self._stat_lbl = QLabel("queued")
        self._stat_lbl.setFont(F_SEMIBOLD(11))
        self._stat_lbl.setStyleSheet(f"color: {_th_stone()};")
        self._stat_lbl.setFixedWidth(44)
        self._stat_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_lay.addWidget(self._stat_lbl)

        self._ctrl_widget = QWidget()
        self._ctrl_widget.setStyleSheet("background: transparent;")
        self._ctrl_lay = QHBoxLayout(self._ctrl_widget)
        self._ctrl_lay.setContentsMargins(0, 0, 0, 0)
        self._ctrl_lay.setSpacing(3)
        row_lay.addWidget(self._ctrl_widget)

        main_lay.addWidget(row)

        self._stats_lbl = QLabel("—")
        self._stats_lbl.setFont(F_BODY(11))
        self._stats_lbl.setStyleSheet(f"color: {_th_stone()};")
        main_lay.addWidget(self._stats_lbl)

        # Initial state
        if entry.status == "in_progress":
            self.set_uploading()
        elif entry.status == "compressing":
            self._rebuild_ctrl("compressing")
        elif entry.status == "paused":
            self.set_paused()
        elif entry.status == "completed":
            wl = (f"https://drive.google.com/file/d/{entry.drive_file_id}/view"
                  if entry.drive_file_id else "")
            self._link_url = wl
            self.set_done(web_link=wl)
        elif entry.status == "failed":
            self.set_failed(entry.error or "Unknown error")
        else:
            self._rebuild_ctrl("queued")

    def _make_icon_btn(self, text: str, tooltip: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("icon-btn")
        b.setFixedSize(22, 22)
        b.setFont(F_BODY(11))
        b.setToolTip(tooltip)
        b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        return b

    def _rebuild_ctrl(self, status: str):
        self._status = status
        # Clear
        while self._ctrl_lay.count():
            item = self._ctrl_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        eid = self._entry_id
        if status == "queued":
            b = self._make_icon_btn("✕", "Remove")
            b.clicked.connect(lambda: self._cancel_cb(eid))
            self._ctrl_lay.addWidget(b)
        elif status in ("uploading", "in_progress"):
            b1 = self._make_icon_btn("⏸", "Pause")
            b1.clicked.connect(lambda: self._cancel_cb(eid))
            b2 = self._make_icon_btn("✕", "Remove")
            b2.clicked.connect(lambda: self._cancel_cb(eid))
            self._ctrl_lay.addWidget(b1)
            self._ctrl_lay.addWidget(b2)
        elif status == "paused":
            b1 = self._make_icon_btn("▶", "Resume")
            b1.clicked.connect(lambda: self._resume_cb(eid))
            b2 = self._make_icon_btn("✕", "Remove")
            b2.clicked.connect(lambda: self._cancel_cb(eid))
            self._ctrl_lay.addWidget(b1)
            self._ctrl_lay.addWidget(b2)
        elif status == "failed":
            b1 = self._make_icon_btn("↺", "Retry")
            b1.clicked.connect(lambda: self._retry_cb(eid))
            b2 = self._make_icon_btn("✕", "Remove")
            b2.clicked.connect(lambda: self._cancel_cb(eid))
            self._ctrl_lay.addWidget(b1)
            self._ctrl_lay.addWidget(b2)
        elif status == "done":
            check = QLabel("✓")
            check.setFont(F_SEMIBOLD(12))
            check.setStyleSheet(f"color: {TEAL};")
            check.setFixedSize(22, 22)
            check.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._ctrl_lay.addWidget(check)
            if self._link_url:
                b = self._make_icon_btn("⎘", "Copy link")
                b.clicked.connect(self._copy_link)
                self._ctrl_lay.addWidget(b)
        elif status in ("pausing", "compressing"):
            b = self._make_icon_btn("✕", "Cancel")
            b.clicked.connect(lambda: self._cancel_cb(eid))
            self._ctrl_lay.addWidget(b)

    def _refresh_stats(self):
        done  = _fmt_size(self._bytes_conf)
        total = _fmt_size(self._file_size)
        pct   = int(self._bytes_disp / self._file_size * 100) if self._file_size else 0
        if self._rate > 1024:
            rate = _fmt_size(self._rate) + "/s"
            remaining = (self._file_size - self._bytes_conf) / self._rate
            eta = _fmt_duration(remaining)
            text = f"{done} / {total} ({pct}%) — {rate} — ~{eta}"
        else:
            text = f"{done} / {total} ({pct}%)"
        self._stats_lbl.setText(text)

    def update_progress(self, bytes_read: int):
        if self._file_size <= 0:
            return
        now = time.monotonic()
        self._bytes_disp = min(bytes_read, self._file_size)
        self._rate_samples.append((now, self._bytes_disp))
        cutoff = now - 8.0
        while self._rate_samples and self._rate_samples[0][0] < cutoff:
            self._rate_samples.popleft()
        if len(self._rate_samples) >= 2:
            t0, b0 = self._rate_samples[0]
            t1, b1 = self._rate_samples[-1]
            dt = t1 - t0
            if dt > 0.2:
                self._rate = (b1 - b0) / dt
        self._refresh_stats()
        self._bar.set_pct(self._bytes_disp / self._file_size if self._file_size else 0)

    def confirm_progress(self, bytes_confirmed: int):
        self._bytes_conf = bytes_confirmed
        self._refresh_stats()

    def set_status(self, text: str):
        self._stats_lbl.setText(text)

    def set_retrying(self, attempt: int):
        self._stats_lbl.setText(f"Retrying ({attempt}/5)…")
        self._stats_lbl.setStyleSheet(f"color: {_th_yellow()};")

    def set_uploading(self):
        self._rate_samples.clear()
        self._rate = 0.0
        self._rebuild_ctrl("uploading")
        self._stat_lbl.setText("…")
        self._stat_lbl.setStyleSheet(f"color: {TEAL};")
        self._stats_lbl.setText("Uploading…")
        self._stats_lbl.setStyleSheet(f"color: {_th_stone()};")
        self.setStyleSheet(
            f"QFrame {{ background: {TEAL_WASH}; border-bottom: 1px solid {_th_mist()}; }}")

    def set_pausing(self):
        self._rebuild_ctrl("pausing")
        self._stats_lbl.setText("Pausing…")

    def set_paused(self):
        self._rate_samples.clear()
        self._rate = 0.0
        self._rebuild_ctrl("paused")
        done  = _fmt_size(self._bytes_conf)
        total = _fmt_size(self._file_size)
        pct   = int(self._bytes_conf / self._file_size * 100) if self._file_size else 0
        self._stats_lbl.setText(f"Paused at {done} / {total} ({pct}%)")
        self._stat_lbl.setText("paused")
        self._stat_lbl.setStyleSheet(f"color: {_th_stone()};")
        self.setStyleSheet(
            f"QFrame {{ background: {_th_surface()}; border-bottom: 1px solid {_th_mist()}; }}")

    def set_queued(self):
        self._rate_samples.clear()
        self._rate = 0.0
        self._rebuild_ctrl("queued")
        self._stats_lbl.setText("Waiting to upload…")
        self._stat_lbl.setText("queued")
        self._stat_lbl.setStyleSheet(f"color: {_th_stone()};")
        self.setStyleSheet(
            f"QFrame {{ background: {_th_surface()}; border-bottom: 1px solid {_th_mist()}; }}")

    def set_done(self, web_link: str = ""):
        self._bytes_disp = self._file_size
        self._bytes_conf = self._file_size
        self._link_url   = web_link
        self._rebuild_ctrl("done")
        self._bar.set_pct(1.0)
        self._stats_lbl.setText(f"{_fmt_size(self._file_size)} uploaded successfully")
        self._stats_lbl.setStyleSheet(f"color: {_th_green()};")
        self._stat_lbl.setText("done")
        self._stat_lbl.setStyleSheet(f"color: {TEAL};")
        self.setStyleSheet(
            f"QFrame {{ background: {_th_surface()}; border-bottom: 1px solid {_th_mist()}; }}")

    def set_failed(self, msg: str):
        self._rebuild_ctrl("failed")
        short = msg if len(msg) < 65 else msg[:63] + "…"
        self._stats_lbl.setText(f"Error: {short}")
        self._stats_lbl.setStyleSheet(f"color: {_th_red()};")
        self._stat_lbl.setText("failed")
        self._stat_lbl.setStyleSheet(f"color: {_th_red()};")

    def set_zip_progress(self, done: int, total: int):
        frac = done / total if total else 0
        self._bytes_disp = int(frac * (self._file_size or 1))
        self._rebuild_ctrl("compressing")
        self._bar.set_pct(frac)
        self._stats_lbl.setText(
            f"Compressing: {done}/{total} files ({int(frac*100)}%)…")

    def set_zip_cancelling(self):
        self._rebuild_ctrl("pausing")
        self._stats_lbl.setText("Cancelling…")

    def set_upload_ready(self, zip_name: str, zip_size: int):
        self._file_size  = zip_size
        self._bytes_disp = 0
        self._bytes_conf = 0
        self._rate_samples.clear()
        self._rate = 0.0
        self._rebuild_ctrl("queued")
        self._bar.set_pct(0)
        self._size_lbl.setText(_fmt_size(zip_size))
        self._stats_lbl.setText(_fmt_size(zip_size))

    def _copy_link(self):
        QApplication.clipboard().setText(self._link_url)

    def refresh_theme(self):
        st = getattr(self, "_status", "queued")
        bg = TEAL_WASH if st == "uploading" else _th_surface()
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border-bottom: 1px solid {_th_mist()}; }}")
        self._name_lbl.setStyleSheet(f"color: {_th_ink()};")
        self._size_lbl.setStyleSheet(f"color: {_th_stone()};")
        if st == "done":
            self._stats_lbl.setStyleSheet(f"color: {_th_green()};")
        elif st == "failed":
            self._stats_lbl.setStyleSheet(f"color: {_th_red()};")
            self._stat_lbl.setStyleSheet(f"color: {_th_red()};")
        elif st == "retrying":
            self._stats_lbl.setStyleSheet(f"color: {_th_yellow()};")
        else:
            self._stat_lbl.setStyleSheet(f"color: {_th_stone()};")
            self._stats_lbl.setStyleSheet(f"color: {_th_stone()};")


# ── Dialogs ────────────────────────────────────────────────────────────────────

class FolderPickerDialog(QDialog):
    """Column-browser Drive folder picker (Finder-style)."""

    _COL_W = 220

    def __init__(self, parent, folders):
        super().__init__(parent)
        self.setWindowTitle("Select Drive Folder")
        self.resize(740, 460)
        self.setModal(True)
        self.result_id   = None
        self.result_name = None
        self._folders    = folders
        self._selected: dict | None = None
        self._columns: list[QListWidget] = []
        self._searching  = False

        self._build_lookups()
        self._build_ui()
        self._push_drives_column()

    # ── data ──────────────────────────────────────────────────────────────────

    def _build_lookups(self):
        self._id_to_folder: dict[str, dict] = {f["id"]: f for f in self._folders}
        self._children: dict[str, list] = {}
        seen_drives: dict[str, str] = {}
        for f in self._folders:
            seen_drives.setdefault(f["drive_id"], f["drive_name"])
            pid = f.get("parent_id") or f["drive_id"]
            self._children.setdefault(pid, []).append(f)
        for k in self._children:
            self._children[k].sort(key=lambda x: x["name"].lower())
        self._drives = sorted(seen_drives.items(), key=lambda x: x[1].lower())

    def _has_children(self, folder_id: str) -> bool:
        return bool(self._children.get(folder_id))

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setMinimumSize(620, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(8)

        title = QLabel("Select Google Drive Folder")
        title.setFont(F_SEMIBOLD(15))
        lay.addWidget(title)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search folders…")
        self._search.textChanged.connect(self._on_search)
        lay.addWidget(self._search)

        self._breadcrumb = QLabel(" ")
        self._breadcrumb.setFont(F_BODY(11))
        self._breadcrumb.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(self._breadcrumb)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {TEAL_PALE}; border-radius: 4px;"
            f" background: {_th_surface2()}; }}")
        self._col_container = QWidget()
        self._col_container.setStyleSheet(f"background: {_th_surface2()};")
        self._col_layout = QHBoxLayout(self._col_container)
        self._col_layout.setContentsMargins(0, 0, 0, 0)
        self._col_layout.setSpacing(0)
        self._col_layout.addStretch()
        self._scroll.setWidget(self._col_container)
        lay.addWidget(self._scroll, 1)

        btn_row = QWidget()
        br = QHBoxLayout(btn_row); br.setContentsMargins(0, 4, 0, 0); br.setSpacing(8)
        br.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(self.reject)
        br.addWidget(cancel_btn)
        self._select_btn = QPushButton("Select Folder")
        self._select_btn.setObjectName("primary")
        self._select_btn.setEnabled(False)
        self._select_btn.clicked.connect(self._confirm)
        br.addWidget(self._select_btn)
        lay.addWidget(btn_row)

    def _col_style(self, focused: bool = True) -> str:
        w = f"QListWidget {{ background: {_th_surface2()}; border: none; border-right: 1px solid {_th_mist()}; outline: 0; }}"
        i = f"QListWidget::item {{ padding: 5px 10px; color: {_th_ink()}; }}"
        h = f"QListWidget::item:hover {{ background: {TEAL_WASH}; }}"
        # Dim selection in unfocused columns so active column is obvious
        sel_bg = TEAL if focused else TEAL_PALE
        sel_fg = "white" if focused else _th_ink()
        s = f"QListWidget::item:selected {{ background: {sel_bg}; color: {sel_fg}; }}"
        return "\n".join([w, i, h, s])

    def _make_list(self, width: int) -> QListWidget:
        lw = QListWidget()
        lw.setFixedWidth(width)
        lw.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lw.setStyleSheet(self._col_style())
        lw.installEventFilter(self)
        return lw

    # ── columns ───────────────────────────────────────────────────────────────

    def _push_drives_column(self):
        lw = self._make_list(200)
        fnt = lw.font(); fnt.setBold(True); lw.setFont(fnt)
        for drive_id, drive_name in self._drives:
            item = QListWidgetItem(f"📂  {drive_name}")
            item.setData(Qt.ItemDataRole.UserRole,
                         {"_is_drive": True, "id": drive_id,
                          "drive_id": drive_id, "drive_name": drive_name, "name": drive_name})
            lw.addItem(item)
        ci = len(self._columns)
        lw.currentItemChanged.connect(
            lambda cur, prev, i=ci: self._on_item_changed(i, cur))
        lw.itemDoubleClicked.connect(lambda _: self._confirm())
        self._columns.append(lw)
        self._col_layout.insertWidget(self._col_layout.count() - 1, lw)
        self._set_column_focus(0)
        lw.setFocus()

    def _push_folder_column(self, folders: list, after_col: int):
        # Trim columns after after_col
        while len(self._columns) > after_col + 1:
            old = self._columns.pop()
            self._col_layout.removeWidget(old)
            old.deleteLater()
        if not folders:
            return
        lw = self._make_list(self._COL_W)
        for folder in folders:
            arrow = "  ›" if self._has_children(folder["id"]) else ""
            item = QListWidgetItem(f"📁  {folder['name']}{arrow}")
            item.setData(Qt.ItemDataRole.UserRole, folder)
            lw.addItem(item)
        ci = len(self._columns)
        lw.currentItemChanged.connect(
            lambda cur, prev, i=ci: self._on_item_changed(i, cur))
        lw.itemDoubleClicked.connect(lambda _: self._confirm())
        self._columns.append(lw)
        self._col_layout.insertWidget(self._col_layout.count() - 1, lw)
        # Dim all but the parent (after_col) as the active focus column
        self._set_column_focus(after_col)
        # Scroll to end without capturing lw (avoids crash if lw deleted before timer fires)
        QTimer.singleShot(30, self._scroll_to_end)

    def _scroll_to_end(self):
        sb = self._scroll.horizontalScrollBar()
        sb.setValue(sb.maximum())

    def _on_item_changed(self, col_idx: int, current: QListWidgetItem | None):
        try:
            if current is None:
                return
            folder = current.data(Qt.ItemDataRole.UserRole)
            if not folder:
                return
            if folder.get("_is_drive"):
                self._selected = None
                self._select_btn.setEnabled(False)
                self._breadcrumb.setText(folder.get("drive_name", ""))
                self._push_folder_column(
                    self._children.get(folder["id"], []), col_idx)
            else:
                self._selected = folder
                self._select_btn.setEnabled(True)
                self._update_breadcrumb(folder)
                self._push_folder_column(
                    self._children.get(folder["id"], []), col_idx)
        except Exception as exc:
            import traceback; traceback.print_exc()

    # ── search ────────────────────────────────────────────────────────────────

    def _on_search(self, text: str):
        q = text.lower().strip()

        # Clear all columns
        while self._columns:
            old = self._columns.pop()
            self._col_layout.removeWidget(old)
            old.deleteLater()
        self._selected = None
        self._select_btn.setEnabled(False)

        if not q:
            self._searching = False
            self._breadcrumb.setText(" ")
            self._push_drives_column()
            return

        self._searching = True
        filtered = sorted(
            [f for f in self._folders
             if q in f["name"].lower() or q in f.get("drive_name", "").lower()],
            key=lambda x: x["name"].lower())

        lw = self._make_list(max(400, self.width() - 60))
        for folder in filtered:
            arrow = "  ›" if self._has_children(folder["id"]) else ""
            item = QListWidgetItem(
                f"📁  {folder['drive_name']} / {folder['name']}{arrow}")
            item.setData(Qt.ItemDataRole.UserRole, folder)
            lw.addItem(item)

        lw.currentItemChanged.connect(
            lambda cur, prev: self._on_search_select(cur))
        lw.itemDoubleClicked.connect(lambda _: self._confirm())
        self._columns.append(lw)
        self._col_layout.insertWidget(self._col_layout.count() - 1, lw)

    def _on_search_select(self, current: QListWidgetItem | None):
        try:
            if not current:
                self._selected = None
                self._select_btn.setEnabled(False)
                return
            folder = current.data(Qt.ItemDataRole.UserRole)
            self._selected = folder
            self._select_btn.setEnabled(folder is not None)
            if folder:
                self._update_breadcrumb(folder)
                children = self._children.get(folder["id"], [])
                self._push_folder_column(children, 0)
        except Exception:
            import traceback; traceback.print_exc()

    # ── breadcrumb ────────────────────────────────────────────────────────────

    def _update_breadcrumb(self, folder: dict):
        parts = [folder["name"]]
        cur = folder
        while True:
            pid = cur.get("parent_id")
            if not pid or pid == cur.get("drive_id"):
                break
            parent = self._id_to_folder.get(pid)
            if not parent:
                break
            parts.insert(0, parent["name"])
            cur = parent
        parts.insert(0, folder.get("drive_name", ""))
        self._breadcrumb.setText("  ›  ".join(p for p in parts if p))

    # ── confirm / keys ────────────────────────────────────────────────────────

    def _confirm(self):
        try:
            if not self._selected:
                return
            self.result_id   = self._selected["id"]
            self.result_name = (f"{self._selected.get('drive_name', '')} / "
                                f"{self._selected.get('name', '')}")
            self.accept()
        except Exception:
            import traceback; traceback.print_exc()

    def _set_column_focus(self, focused_idx: int):
        for i, lw in enumerate(self._columns):
            lw.setStyleSheet(self._col_style(focused=i == focused_idx))
        QTimer.singleShot(0, self.repaint)

    def eventFilter(self, obj, event):
        try:
            col_idx = next(
                (i for i, lw in enumerate(self._columns) if lw is obj), -1)
            if col_idx >= 0:
                if event.type() == event.Type.FocusIn:
                    self._set_column_focus(col_idx)
                elif event.type() == event.Type.KeyPress:
                    key = event.key()
                    if key == Qt.Key.Key_Right:
                        if col_idx + 1 < len(self._columns):
                            nxt = self._columns[col_idx + 1]
                            if nxt.count() and nxt.currentRow() < 0:
                                nxt.setCurrentRow(0)
                            nxt.setFocus()
                        return True
                    if key == Qt.Key.Key_Left:
                        if col_idx > 0:
                            prev = self._columns[col_idx - 1]
                            prev.setFocus()
                            self._on_item_changed(col_idx - 1, prev.currentItem())
                        return True
                    if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                        self._confirm()
                        return True
        except Exception:
            import traceback; traceback.print_exc()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


class FolderModeDialog(QDialog):
    def __init__(self, parent, folder_name: str):
        super().__init__(parent)
        self.setWindowTitle("Upload Folder")
        self.setFixedSize(380, 180)
        self.setModal(True)
        self.result = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(10)

        name_display = folder_name if len(folder_name) < 36 else folder_name[:34] + "…"
        title = QLabel(f'Upload "{name_display}"')
        title.setFont(F_SEMIBOLD(15))
        lay.addWidget(title)

        sub = QLabel("How would you like to upload this folder?")
        sub.setFont(F_BODY(12))
        sub.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(sub)

        btn_row = QWidget()
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(8)

        keep_btn = QPushButton("Keep Structure")
        keep_btn.setObjectName("primary")
        keep_btn.setFont(F_SEMIBOLD(12))
        keep_btn.clicked.connect(lambda: self._choose("structure"))
        br_lay.addWidget(keep_btn, 1)

        zip_btn = QPushButton("Compress to ZIP")
        zip_btn.setObjectName("ghost")
        zip_btn.setFont(F_SEMIBOLD(12))
        zip_btn.clicked.connect(lambda: self._choose("zip"))
        br_lay.addWidget(zip_btn, 1)
        lay.addWidget(btn_row)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("link")
        cancel_btn.clicked.connect(self.reject)
        lay.addWidget(cancel_btn, 0, Qt.AlignmentFlag.AlignCenter)

    def _choose(self, mode: str):
        self.result = mode
        self.accept()


class DriveAccountsDialog(QDialog):
    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.setWindowTitle("Google Drive Accounts")
        self.resize(500, 400)
        self.setModal(True)
        self._cfg = cfg
        self.account_changed = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(8)

        title = QLabel("Google Drive Accounts")
        title.setFont(F_SEMIBOLD(16))
        lay.addWidget(title)

        sub = QLabel("Manage which account this app uploads to.")
        sub.setFont(F_BODY(12))
        sub.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_widget = QWidget()
        self._list_widget.setStyleSheet(f"background: {_th_surface2()};")
        self._list_lay = QVBoxLayout(self._list_widget)
        self._list_lay.setContentsMargins(8, 8, 8, 8)
        self._list_lay.setSpacing(4)
        scroll.setWidget(self._list_widget)
        lay.addWidget(scroll, 1)

        btn_row = QWidget()
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)

        add_btn = QPushButton("+ Add Account")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_account)
        br_lay.addWidget(add_btn)
        br_lay.addStretch()

        done_btn = QPushButton("Done")
        done_btn.setObjectName("ghost")
        done_btn.clicked.connect(self.accept)
        br_lay.addWidget(done_btn)
        lay.addWidget(btn_row)

        self._rebuild_list()

    def _rebuild_list(self):
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        accounts = drive_accounts.list_accounts()
        active_id = self._cfg.get("active_drive_account_id", "")

        if not accounts:
            lbl = QLabel("No accounts saved yet. Click + Add Account.")
            lbl.setFont(F_BODY(12))
            lbl.setStyleSheet(f"color: {_th_stone()};")
            self._list_lay.addWidget(lbl)
            return

        for acct in accounts:
            row = QFrame()
            row.setStyleSheet(f"QFrame {{ background: {_th_surface()}; border-radius: 6px; }}")
            r_lay = QHBoxLayout(row)
            r_lay.setContentsMargins(10, 8, 10, 8)
            r_lay.setSpacing(8)

            is_active = acct["id"] == active_id
            dot_lbl = QLabel("●" if is_active else "○")
            dot_lbl.setStyleSheet(f"color: {TEAL if is_active else STONE};")
            r_lay.addWidget(dot_lbl)

            info = QWidget()
            info.setStyleSheet("background: transparent;")
            i_lay = QVBoxLayout(info)
            i_lay.setContentsMargins(0, 0, 0, 0)
            i_lay.setSpacing(1)
            n_lbl = QLabel(acct["name"])
            n_lbl.setFont(F_SEMIBOLD(13))
            i_lay.addWidget(n_lbl)
            e_lbl = QLabel(acct.get("email", ""))
            e_lbl.setFont(F_BODY(11))
            e_lbl.setStyleSheet(f"color: {_th_stone()};")
            i_lay.addWidget(e_lbl)
            r_lay.addWidget(info, 1)

            acct_id = acct["id"]
            email_btn = QPushButton("Email…")
            email_btn.setObjectName("ghost")
            email_btn.setFixedHeight(28)
            acct_name = acct.get("name", "")
            email_btn.clicked.connect(
                lambda _, aid=acct_id, an=acct_name: self._setup_email(aid, an))
            r_lay.addWidget(email_btn)

            rm_btn = QPushButton("✕  Remove")
            rm_btn.setFixedHeight(28)
            rm_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            rm_btn.setStyleSheet(
                "QPushButton { background: transparent; color: #cc2222; border: 1px solid #cc2222;"
                " border-radius: 4px; padding: 0 8px; font-size: 11px; }"
                "QPushButton:hover { background: #fff0f0; }"
                "QPushButton:pressed { background: #ffe0e0; }")
            rm_btn.clicked.connect(lambda _, aid=acct_id, an=acct.get("name",""): self._remove(aid, an))
            r_lay.addWidget(rm_btn)

            self._list_lay.addWidget(row)

        self._list_lay.addStretch()

    def _set_active(self, account_id: str):
        self._cfg["active_drive_account_id"] = account_id
        self.account_changed = True
        self._rebuild_list()

    def _remove(self, account_id: str, account_name: str = ""):
        label = f'"{account_name}"' if account_name else "this account"
        msg = QMessageBox(self)
        msg.setWindowTitle("Remove Account")
        msg.setText(f"Remove {label}?\n\nThis deletes the saved credentials. You'll need to re-authorize to use this account again.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
        msg.button(QMessageBox.StandardButton.Yes).setText("Remove")
        if msg.exec() != QMessageBox.StandardButton.Yes:
            return
        drive_accounts.remove_account(account_id)
        if self._cfg.get("active_drive_account_id") == account_id:
            self._cfg["active_drive_account_id"] = ""
            self.account_changed = True
        self._rebuild_list()

    def _setup_email(self, account_id: str, account_name: str):
        dlg = EmailSetupDialog(self, account_id, account_name)
        dlg.exec()

    def _add_account(self):
        dlg = AddAccountDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result:
            self._cfg["active_drive_account_id"] = dlg.result["id"]
            self.account_changed = True
            self._rebuild_list()


class AddAccountDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Add Google Account")
        self.setFixedSize(420, 460)
        self.setModal(True)
        self.result = None
        self._creds_path = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(8)

        title = QLabel("Add Google Drive Account")
        title.setFont(F_SEMIBOLD(14))
        lay.addWidget(title)

        sub = QLabel("A browser window will open for Google sign-in.")
        sub.setFont(F_BODY(12))
        sub.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(sub)

        # Instructions box
        info = QFrame()
        info.setStyleSheet(
            f"QFrame {{ background: #EAF4F7; border: 1px solid #B0D8E4; "
            f"border-radius: 6px; }}")
        info_lay = QVBoxLayout(info)
        info_lay.setContentsMargins(12, 10, 12, 10)
        info_lay.setSpacing(4)

        info_title = QLabel("How to get credentials.json")
        info_title.setFont(F_SEMIBOLD(11))
        info_title.setStyleSheet("background: transparent; color: #005E7A; border: none;")
        info_lay.addWidget(info_title)

        # Step 1 — URL with copy button
        url = "console.cloud.google.com"
        step1_row = QWidget(); step1_row.setStyleSheet("background: transparent;")
        s1_lay = QHBoxLayout(step1_row)
        s1_lay.setContentsMargins(0, 0, 0, 0); s1_lay.setSpacing(4)
        s1_lbl = QLabel(f"1.  Go to <b>{url}</b>")
        s1_lbl.setFont(F_BODY(11))
        s1_lbl.setStyleSheet("background: transparent; color: #1C1C1E; border: none;")
        s1_lay.addWidget(s1_lbl, 1)
        copy_btn = QPushButton("Copy URL")
        copy_btn.setObjectName("ghost")
        copy_btn.setFont(F_BODY(10))
        copy_btn.setFixedHeight(20)
        copy_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        copy_btn.clicked.connect(lambda: (
            QApplication.clipboard().setText(f"https://{url}"),
            copy_btn.setText("Copied!"),
            QTimer.singleShot(1500, lambda: copy_btn.setText("Copy URL"))
        ))
        s1_lay.addWidget(copy_btn)
        info_lay.addWidget(step1_row)

        plain_steps = [
            "2.  Create a project (or select one)",
            "3.  APIs & Services → Enable APIs → enable Google Drive API",
            "4.  APIs & Services → OAuth consent screen → configure it\n"
            "     (External ok) → add your email under Test users",
            "5.  APIs & Services → Credentials → + Create Credentials\n"
            "     → OAuth client ID → Desktop app",
            "6.  Download the JSON — that's your credentials.json",
        ]
        for step in plain_steps:
            lbl = QLabel(step)
            lbl.setFont(F_BODY(11))
            lbl.setStyleSheet("background: transparent; color: #1C1C1E; border: none;")
            lbl.setWordWrap(True)
            info_lay.addWidget(lbl)

        lay.addWidget(info)

        form = QFrame()
        form.setStyleSheet(
            f"QFrame {{ background: {_th_surface2()}; border: 1px solid {_th_mist()}; border-radius: 6px; }}")
        f_lay = QVBoxLayout(form)
        f_lay.setContentsMargins(14, 10, 14, 10)
        f_lay.setSpacing(6)

        nick_row = QWidget()
        nick_row.setStyleSheet("background: transparent;")
        nr_lay = QHBoxLayout(nick_row)
        nr_lay.setContentsMargins(0, 0, 0, 0)
        nr_lay.setSpacing(8)
        nr_lay.addWidget(QLabel("Nickname"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Personal, Work (optional)")
        nr_lay.addWidget(self._name_edit, 1)
        f_lay.addWidget(nick_row)

        creds_row = QWidget()
        creds_row.setStyleSheet("background: transparent;")
        cr_lay = QHBoxLayout(creds_row)
        cr_lay.setContentsMargins(0, 0, 0, 0)
        cr_lay.setSpacing(8)
        cr_lay.addWidget(QLabel("Credentials"))
        self._creds_lbl = QLabel("— not selected —")
        self._creds_lbl.setFont(F_BODY(11))
        self._creds_lbl.setStyleSheet(f"color: {_th_stone()};")
        cr_lay.addWidget(self._creds_lbl, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("ghost")
        browse_btn.clicked.connect(self._browse)
        cr_lay.addWidget(browse_btn)
        f_lay.addWidget(creds_row)
        lay.addWidget(form)

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(F_BODY(11))
        self._status_lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(self._status_lbl)

        self._connect_btn = QPushButton("Connect Google Account")
        self._connect_btn.setObjectName("primary")
        self._connect_btn.setFixedHeight(36)
        self._connect_btn.setEnabled(False)
        self._connect_btn.clicked.connect(self._connect)
        lay.addWidget(self._connect_btn)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select credentials.json", "",
            "JSON (*.json);;All files (*)")
        if path:
            self._creds_path = path
            self._creds_lbl.setText(Path(path).name)
            self._creds_lbl.setStyleSheet(f"color: {_th_ink()};")
            self._connect_btn.setEnabled(True)

    def _connect(self):
        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("Connecting…")
        self._set_status("Browser opening — complete sign-in within 2 minutes…", STONE)
        name = self._name_edit.text().strip()
        threading.Thread(target=self._do_oauth, args=(name,), daemon=True).start()

    def _set_status(self, msg: str, color: str):
        self._status_lbl.setText(msg)
        self._status_lbl.setStyleSheet(f"color: {color};")

    def _do_oauth(self, name: str):
        try:
            acct = drive_accounts.add_account(self._creds_path, display_name=name)
            self.result = acct
            QTimer.singleShot(0, self.accept)
        except Exception as e:
            err = str(e)
            def _show_err():
                self._connect_btn.setEnabled(True)
                self._connect_btn.setText("Try Again")
                self._set_status(f"Failed: {err[:80]}", _th_red())
                QMessageBox.critical(
                    self, "Connection Failed",
                    f"{err}\n\n"
                    "Common causes:\n"
                    "• credentials.json must be for a Desktop app, not Web application\n"
                    "• OAuth consent screen must be configured in Google Cloud Console\n"
                    "• If app is in Testing mode, add your email as a Test User under\n"
                    "  APIs & Services → OAuth consent screen → Test users\n"
                    "• Complete browser sign-in within 2 minutes of clicking Connect"
                )
            QTimer.singleShot(0, _show_err)


class EmailSetupDialog(QDialog):
    def __init__(self, parent, account_id: str = "", account_name: str = ""):
        super().__init__(parent)
        self._account_id = account_id
        title = f"Email Setup — {account_name}" if account_name else "Email Setup"
        self.setWindowTitle(title)
        self.setFixedSize(420, 310)
        self.setModal(True)

        existing = sender_profile.load(account_id) or {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(8)

        heading = f"Email Sender — {account_name}" if account_name else "Email Sender Setup"
        lbl = QLabel(heading)
        lbl.setFont(F_SEMIBOLD(15))
        lay.addWidget(lbl)

        sub = QLabel("Uses Gmail SMTP with an App Password\n"
                     "(requires 2FA — generate at myaccount.google.com/apppasswords).")
        sub.setFont(F_BODY(11))
        sub.setStyleSheet(f"color: {_th_stone()};")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)

        form = QFrame()
        form.setStyleSheet(
            f"QFrame {{ background: {_th_surface2()}; border: 1px solid {_th_mist()}; border-radius: 6px; }}")
        f_lay = QVBoxLayout(form)
        f_lay.setContentsMargins(14, 10, 14, 10)
        f_lay.setSpacing(6)

        self._name_edit = QLineEdit(existing.get("sender_name", ""))
        self._name_edit.setPlaceholderText("Your Name")
        self._email_edit = QLineEdit(existing.get("sender_email", ""))
        self._email_edit.setPlaceholderText("you@gmail.com")
        self._pw_edit = QLineEdit(existing.get("gmail_app_password", ""))
        self._pw_edit.setPlaceholderText("xxxx xxxx xxxx xxxx")
        self._pw_edit.setEchoMode(QLineEdit.EchoMode.Password)

        for lbl_text, widget in [("Name", self._name_edit),
                                  ("Gmail", self._email_edit),
                                  ("App Password", self._pw_edit)]:
            r = QWidget()
            r.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(r)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            l = QLabel(lbl_text)
            l.setFixedWidth(90)
            l.setFont(F_LABEL(10))
            l.setStyleSheet(f"color: {_th_stone()};")
            rl.addWidget(l)
            rl.addWidget(widget, 1)
            f_lay.addWidget(r)
        lay.addWidget(form)

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(F_BODY(11))
        self._status_lbl.setStyleSheet(f"color: {RED};")
        lay.addWidget(self._status_lbl)

        btn_row = QWidget()
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        if existing:
            clr_btn = QPushButton("Clear")
            clr_btn.setObjectName("ghost")
            clr_btn.setStyleSheet(f"color: {RED}; border-color: {RED};")
            clr_btn.clicked.connect(self._clear)
            br_lay.addWidget(clr_btn)
        br_lay.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(self.reject)
        br_lay.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        br_lay.addWidget(save_btn)
        lay.addWidget(btn_row)

    def _save(self):
        name  = self._name_edit.text().strip()
        email = self._email_edit.text().strip()
        pw    = self._pw_edit.text().strip()
        if not name or not email or not pw:
            self._status_lbl.setText("All fields are required.")
            return
        sender_profile.save(self._account_id, name, email, pw)
        self.accept()

    def _clear(self):
        sender_profile.clear(self._account_id)
        self.accept()


class EmailTemplateDialog(QDialog):
    DEFAULT_SUBJECT = "Your file is ready: {filename}"
    DEFAULT_BODY = ("Hi,\n\nYour file is ready to download:\n{link}\n\n"
                    "Best,\n{sender_name}")

    def __init__(self, parent, cfg: dict, account_id: str = "", account_name: str = ""):
        super().__init__(parent)
        self._cfg = cfg
        self._account_id = account_id
        label = account_name or (account_id[:24] if account_id else "Default")
        self.setWindowTitle(f"Email Template — {label}")
        self.resize(500, 440)
        self.setModal(True)

        acct_tmpl = cfg.get("account_templates", {}).get(account_id, {})
        init_subj = acct_tmpl.get("email_subject", self.DEFAULT_SUBJECT)
        init_body = acct_tmpl.get("email_body", self.DEFAULT_BODY)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(8)

        title_lbl = QLabel(f"Email Template — {label}")
        title_lbl.setFont(F_SEMIBOLD(15))
        lay.addWidget(title_lbl)

        vars_lbl = QLabel("Variables:  {filename}  {link}  {date}  {sender_name}")
        vars_lbl.setFont(F_BODY(11))
        vars_lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(vars_lbl)

        subj_lbl = QLabel("Subject")
        subj_lbl.setFont(F_LABEL(10))
        subj_lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(subj_lbl)

        self._subject_edit = QLineEdit(init_subj)
        lay.addWidget(self._subject_edit)

        body_lbl = QLabel("Body")
        body_lbl.setFont(F_LABEL(10))
        body_lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(body_lbl)

        self._body_edit = QPlainTextEdit()
        self._body_edit.setPlainText(init_body)
        lay.addWidget(self._body_edit, 1)

        btn_row = QWidget()
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        reset_btn = QPushButton("Reset to Default")
        reset_btn.setObjectName("ghost")
        reset_btn.clicked.connect(self._reset)
        br_lay.addWidget(reset_btn)
        br_lay.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(self.reject)
        br_lay.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        br_lay.addWidget(save_btn)
        lay.addWidget(btn_row)

    def _reset(self):
        self._subject_edit.setText(self.DEFAULT_SUBJECT)
        self._body_edit.setPlainText(self.DEFAULT_BODY)

    def _save(self):
        subject = self._subject_edit.text().strip()
        body    = self._body_edit.toPlainText().rstrip("\n")
        templates = self._cfg.setdefault("account_templates", {})
        templates[self._account_id] = {"email_subject": subject, "email_body": body}
        config.save(self._cfg)
        self.accept()


class ComposeEmailDialog(QDialog):
    def __init__(self, parent, subject: str, body: str, on_send):
        super().__init__(parent)
        self.setWindowTitle("Compose Email")
        self.resize(520, 480)
        self.setModal(True)
        self._orig_subject = subject
        self._orig_body    = body
        self._on_send      = on_send

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(8)

        title = QLabel("Compose Email")
        title.setFont(F_SEMIBOLD(15))
        lay.addWidget(title)

        hint = QLabel("{link} will be replaced with the Drive share URL when sent.")
        hint.setFont(F_BODY(11))
        hint.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(hint)

        subj_lbl = QLabel("Subject")
        subj_lbl.setFont(F_LABEL(10))
        subj_lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(subj_lbl)

        self._subject_edit = QLineEdit(subject)
        lay.addWidget(self._subject_edit)

        body_lbl = QLabel("Body")
        body_lbl.setFont(F_LABEL(10))
        body_lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(body_lbl)

        self._body_edit = QPlainTextEdit()
        self._body_edit.setPlainText(body)
        lay.addWidget(self._body_edit, 1)

        btn_row = QWidget()
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        revert_btn = QPushButton("← Revert to Template")
        revert_btn.setObjectName("ghost")
        revert_btn.clicked.connect(self._revert)
        br_lay.addWidget(revert_btn)
        br_lay.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(self.reject)
        br_lay.addWidget(cancel_btn)
        send_btn = QPushButton("Send")
        send_btn.setObjectName("primary")
        send_btn.clicked.connect(self._send)
        br_lay.addWidget(send_btn)
        lay.addWidget(btn_row)

    def _revert(self):
        self._subject_edit.setText(self._orig_subject)
        self._body_edit.setPlainText(self._orig_body)

    def _send(self):
        subject = self._subject_edit.text().strip()
        body    = self._body_edit.toPlainText().rstrip("\n")
        self.accept()
        self._on_send(subject, body)


class EmailDraftDialog(QDialog):
    """Capture email To/CC/BCC/Subject/Body at job-creation time (no send)."""
    DEFAULT_SUBJECT = EmailTemplateDialog.DEFAULT_SUBJECT
    DEFAULT_BODY    = EmailTemplateDialog.DEFAULT_BODY

    def __init__(self, parent, draft: dict | None = None, cfg: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Email Settings")
        self.resize(500, 480)
        self.setModal(True)
        d = draft or {}
        self._cfg = cfg
        self.result_draft: dict | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 16)
        lay.setSpacing(8)

        title = QLabel("Email Settings")
        title.setFont(F_SEMIBOLD(15))
        lay.addWidget(title)

        # ── Template bar ──────────────────────────────────────────────────────
        if cfg is not None:
            tmpl_bar = QWidget(); tmpl_bar.setStyleSheet("background: transparent;")
            tb_lay = QHBoxLayout(tmpl_bar)
            tb_lay.setContentsMargins(0, 0, 0, 0); tb_lay.setSpacing(6)
            tmpl_lbl = QLabel("Template:")
            tmpl_lbl.setFont(F_LABEL(10))
            tmpl_lbl.setStyleSheet(f"color: {_th_stone()};")
            tb_lay.addWidget(tmpl_lbl)
            self._tmpl_combo = QComboBox()
            self._tmpl_combo.setFont(F_BODY(11))
            self._refresh_tmpl_combo()
            tb_lay.addWidget(self._tmpl_combo, 1)
            load_btn = QPushButton("Load")
            load_btn.setObjectName("ghost"); load_btn.setFixedHeight(24)
            load_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            load_btn.clicked.connect(self._load_template)
            tb_lay.addWidget(load_btn)
            save_tmpl_btn = QPushButton("Save as Template…")
            save_tmpl_btn.setObjectName("ghost"); save_tmpl_btn.setFixedHeight(24)
            save_tmpl_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            save_tmpl_btn.clicked.connect(self._save_as_template)
            tb_lay.addWidget(save_tmpl_btn)
            del_tmpl_btn = QPushButton("Delete")
            del_tmpl_btn.setObjectName("ghost")
            del_tmpl_btn.setFixedHeight(24)
            del_tmpl_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            del_tmpl_btn.clicked.connect(self._delete_template)
            tb_lay.addWidget(del_tmpl_btn)
            lay.addWidget(tmpl_bar)
        else:
            self._tmpl_combo = None

        hint = QLabel("{link} and {filename} are replaced when the email is sent.")
        hint.setFont(F_BODY(11))
        hint.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(hint)

        def field(label_text, widget):
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            lbl = QLabel(label_text)
            lbl.setFont(F_LABEL(10))
            lbl.setStyleSheet(f"color: {_th_stone()};")
            lbl.setFixedWidth(56)
            rl.addWidget(lbl)
            rl.addWidget(widget, 1)
            lay.addWidget(row)

        self._to_edit  = QLineEdit(d.get("to", ""))
        self._to_edit.setPlaceholderText("one@example.com, two@example.com")
        field("TO", self._to_edit)

        self._cc_edit  = QLineEdit(d.get("cc", ""))
        self._cc_edit.setPlaceholderText("optional")
        field("CC", self._cc_edit)

        self._bcc_edit = QLineEdit(d.get("bcc", ""))
        self._bcc_edit.setPlaceholderText("optional")
        field("BCC", self._bcc_edit)

        self._subj_edit = QLineEdit(d.get("subject", self.DEFAULT_SUBJECT))
        field("SUBJECT", self._subj_edit)

        body_lbl = QLabel("BODY")
        body_lbl.setFont(F_LABEL(10))
        body_lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(body_lbl)

        self._body_edit = QPlainTextEdit()
        self._body_edit.setPlainText(d.get("body", self.DEFAULT_BODY))
        lay.addWidget(self._body_edit, 1)

        btn_row = QWidget()
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        reset_btn = QPushButton("Reset to Default")
        reset_btn.setObjectName("ghost")
        reset_btn.clicked.connect(self._reset)
        br_lay.addWidget(reset_btn)
        br_lay.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(self.reject)
        br_lay.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        br_lay.addWidget(save_btn)
        lay.addWidget(btn_row)

    def _refresh_tmpl_combo(self):
        if self._tmpl_combo is None or self._cfg is None:
            return
        self._tmpl_combo.blockSignals(True)
        self._tmpl_combo.clear()
        self._tmpl_combo.addItem("— load template —")
        for t in self._cfg.get("email_templates", []):
            self._tmpl_combo.addItem(t["name"])
        self._tmpl_combo.blockSignals(False)

    def _load_template(self):
        if self._tmpl_combo is None or self._cfg is None:
            return
        idx = self._tmpl_combo.currentIndex()
        if idx == 0:
            return
        templates = self._cfg.get("email_templates", [])
        if idx - 1 >= len(templates):
            return
        t = templates[idx - 1]
        if t.get("to"):
            self._to_edit.setText(t["to"])
        if t.get("cc"):
            self._cc_edit.setText(t["cc"])
        if t.get("bcc"):
            self._bcc_edit.setText(t["bcc"])
        self._subj_edit.setText(t.get("subject", ""))
        self._body_edit.setPlainText(t.get("body", ""))

    def _save_as_template(self):
        if self._cfg is None:
            return
        name, ok = QInputDialog.getText(self, "Save Template", "Template name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        t = {
            "name":    name,
            "to":      self._to_edit.text().strip(),
            "cc":      self._cc_edit.text().strip(),
            "bcc":     self._bcc_edit.text().strip(),
            "subject": self._subj_edit.text().strip(),
            "body":    self._body_edit.toPlainText().rstrip("\n"),
        }
        templates = self._cfg.setdefault("email_templates", [])
        for i, existing in enumerate(templates):
            if existing["name"] == name:
                templates[i] = t
                break
        else:
            templates.append(t)
        config.save(self._cfg)
        self._refresh_tmpl_combo()
        # Select the just-saved template
        idx = next((i + 1 for i, tmpl in enumerate(templates) if tmpl["name"] == name), 0)
        self._tmpl_combo.setCurrentIndex(idx)

    def _delete_template(self):
        if self._tmpl_combo is None or self._cfg is None:
            return
        idx = self._tmpl_combo.currentIndex()
        if idx == 0:
            QMessageBox.information(self, "No Template Selected",
                                    "Select a template from the dropdown first.")
            return
        templates = self._cfg.get("email_templates", [])
        tmpl = templates[idx - 1]
        msg = QMessageBox(self)
        msg.setWindowTitle("Delete Template")
        msg.setText(f'Delete template "{tmpl["name"]}"?')
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
        msg.button(QMessageBox.StandardButton.Yes).setText("Delete")
        if msg.exec() != QMessageBox.StandardButton.Yes:
            return
        templates.pop(idx - 1)
        config.save(self._cfg)
        self._refresh_tmpl_combo()
        self._tmpl_combo.setCurrentIndex(0)

    def _reset(self):
        self._subj_edit.setText(self.DEFAULT_SUBJECT)
        self._body_edit.setPlainText(self.DEFAULT_BODY)

    def _save(self):
        to = self._to_edit.text().strip()
        if not to:
            self._to_edit.setFocus()
            self._to_edit.setStyleSheet(f"border: 1px solid {RED};")
            return
        self.result_draft = {
            "to":      to,
            "cc":      self._cc_edit.text().strip(),
            "bcc":     self._bcc_edit.text().strip(),
            "subject": self._subj_edit.text().strip() or self.DEFAULT_SUBJECT,
            "body":    self._body_edit.toPlainText().rstrip("\n") or self.DEFAULT_BODY,
            "status":  "pending",
            "held":    False,
        }
        self.accept()


# ── _DropZone ──────────────────────────────────────────────────────────────────

class _DropZone(QFrame):
    def __init__(self, on_drop, parent=None):
        super().__init__(parent)
        self._on_drop = on_drop
        self._hover   = False
        self.setAcceptDrops(True)
        self._update_style(False)

    def _update_style(self, hover: bool):
        border_color = TEAL if hover else TEAL_MID
        bg = _th_surface2() if hover else _th_bg_glass()
        self.setStyleSheet(f"""
            QFrame {{
                background: {bg};
                border: 2px dashed {border_color};
                border-radius: 6px;
            }}
        """)

    def refresh_theme(self):
        self._update_style(self._hover)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._hover = True
            self._update_style(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._hover = False
        self._update_style(False)

    def dropEvent(self, event):
        self._hover = False
        self._update_style(False)
        paths = [url.toLocalFile() for url in event.mimeData().urls()
                 if url.toLocalFile() and Path(url.toLocalFile()).exists()]
        if paths:
            self._on_drop(paths)
        event.acceptProposedAction()


# ── JobTile ────────────────────────────────────────────────────────────────────

class JobTile(QFrame):
    def __init__(self, job: dict, app, parent=None):
        super().__init__(parent)
        self._job    = job
        self._app    = app
        self._rows: dict[str, FileRow] = {}
        self._expanded = True

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 15))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

        source       = self._job.get("source", "manual")
        accent_color = TEAL if source == "watch" else STONE

        # Initial style — refresh_theme() called after glass activates
        tile_bg = "transparent" if GLASS_PANELS_ACTIVE else _th_surface()
        self.setStyleSheet(f"""
            JobTile {{
                background: {tile_bg};
                border: 1px solid {TEAL_PALE};
                border-radius: 2px;
            }}
        """)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        accent = QFrame()
        accent.setFixedWidth(3)
        accent.setStyleSheet(f"background: {accent_color}; border: none;")
        outer.addWidget(accent)

        self._inner = inner = QWidget()
        inner.setStyleSheet(f"background: {_th_surface()};")
        self._inner_lay = QVBoxLayout(inner)
        self._inner_lay.setContentsMargins(0, 0, 0, 0)
        self._inner_lay.setSpacing(0)

        self._inner_lay.addWidget(self._build_header())

        self._prog_bar = GradientBar(pct=0.0, height=3)
        self._inner_lay.addWidget(self._prog_bar)

        self._strip = self._build_strip()
        self._inner_lay.addWidget(self._strip)

        self._watch_status_row: QWidget | None = None
        if source == "watch":
            self._watch_status_row = self._build_watch_status_row()
            self._inner_lay.addWidget(self._watch_status_row)

        self._files_widget = QWidget()
        self._files_widget.setStyleSheet(f"background: {_th_surface()};")
        self._files_lay = QVBoxLayout(self._files_widget)
        self._files_lay.setContentsMargins(0, 0, 0, 0)
        self._files_lay.setSpacing(0)
        self._inner_lay.addWidget(self._files_widget)

        outer.addWidget(inner, 1)

    def _build_header(self) -> QWidget:
        self._hdr = hdr = QWidget()
        hdr.setStyleSheet(f"background: {_th_surface()};")
        hdr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(10, 8, 10, 6)
        lay.setSpacing(8)

        self._tri = QLabel("▾")
        self._tri.setFont(F_BODY(10))
        self._tri.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(self._tri, 0, Qt.AlignmentFlag.AlignVCenter)

        source     = self._job.get("source", "manual")
        badge_bg   = TEAL_WASH if source == "watch" else SURFACE2
        badge_fg   = TEAL_DEEP if source == "watch" else STONE
        badge_text = "WATCH" if source == "watch" else "UPLOAD"
        self._badge = QLabel(badge_text)
        self._badge.setFont(F_LABEL(10))
        self._badge.setStyleSheet(
            f"color: {badge_fg}; background: {badge_bg}; "
            f"border-radius: 2px; padding: 2px 6px; letter-spacing: 1px;")
        lay.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self._name_lbl = QLabel(self._job.get("name", "Job"))
        self._name_lbl.setFont(F_SEMIBOLD(13))
        self._name_lbl.setStyleSheet(f"color: {_th_ink()};")
        lay.addWidget(self._name_lbl, 0)

        # Inline watch status (dot + label) — only for watch jobs
        self._watch_dot: PulsingDot | None = None
        self._watch_lbl: QLabel | None = None
        if self._job.get("source") == "watch":
            sep = QLabel("·")
            sep.setFont(F_BODY(11))
            sep.setStyleSheet(f"color: {_th_mist()};")
            lay.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)
            self._watch_dot = PulsingDot(size=7, color=TEAL)
            lay.addWidget(self._watch_dot, 0, Qt.AlignmentFlag.AlignVCenter)
            self._watch_lbl = ElideLabel("Watching…")
            self._watch_lbl.setFont(F_BODY(11))
            self._watch_lbl.setStyleSheet(f"color: {TEAL};")
            lay.addWidget(self._watch_lbl, 1)
        else:
            lay.addStretch(1)

        self._count_lbl = QLabel("")
        self._count_lbl.setFont(F_BODY(11))
        self._count_lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(self._count_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self._pause_btn = QPushButton("⏸")
        self._pause_btn.setToolTip("Pause all")
        self._pause_btn.setObjectName("ghost")
        self._pause_btn.setFixedSize(28, 22)
        self._pause_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._pause_btn.clicked.connect(self._pause_all)
        self._pause_btn.hide()
        lay.addWidget(self._pause_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        source = self._job.get("source", "manual")
        self._stop_watch_btn = None
        self._remove_btn = None
        self._watching = True
        if source == "watch":
            jid = self._job["id"]
            self._edit_watch_btn = QPushButton("Edit")
            self._edit_watch_btn.setToolTip("Edit watch folder settings")
            self._edit_watch_btn.setObjectName("ghost")
            self._edit_watch_btn.setFixedHeight(22)
            self._edit_watch_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self._edit_watch_btn.clicked.connect(
                lambda: self._app._edit_watch_job(jid))
            lay.addWidget(self._edit_watch_btn, 0, Qt.AlignmentFlag.AlignVCenter)

            self._stop_watch_btn = QPushButton("Stop")
            self._stop_watch_btn.setToolTip("Stop watching this folder")
            self._stop_watch_btn.setObjectName("ghost")
            self._stop_watch_btn.setFixedHeight(22)
            self._stop_watch_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self._stop_watch_btn.clicked.connect(self._toggle_watch)
            lay.addWidget(self._stop_watch_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setToolTip("Remove this job from the queue")
        self._remove_btn.setObjectName("ghost")
        self._remove_btn.setFixedHeight(22)
        self._remove_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._remove_btn.clicked.connect(
            lambda: self._app._remove_job(self._job["id"]))
        lay.addWidget(self._remove_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        hdr.mousePressEvent = lambda e: (
            None if e.button() == Qt.MouseButton.LeftButton
            and any(child.underMouse() for child in hdr.findChildren(QPushButton))
            else self._toggle_expand()
        )
        return hdr

    def _build_strip(self) -> QWidget:
        strip = QWidget()
        strip.setStyleSheet(f"background: {_th_surface2()};")
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(12, 3, 10, 3)
        lay.setSpacing(8)

        folder_name = self._job.get("drive_folder_name", "")
        if len(folder_name) > 40:
            folder_name = "…" + folder_name[-38:]
        self._folder_lbl = QLabel(f"📁  {folder_name}" if folder_name else "📁  —")
        self._folder_lbl.setFont(F_MONO(10))
        self._folder_lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(self._folder_lbl, 1)

        if self._job.get("zip"):
            self._zip_lbl = QLabel("🗜")
            self._zip_lbl.setFont(F_BODY(12))
            self._zip_lbl.setStyleSheet(f"color: {_th_stone()};")
            self._zip_lbl.setToolTip("Zip")
            lay.addWidget(self._zip_lbl)
        else:
            self._zip_lbl = None

        email_cfg = self._job.get("email_cfg")
        jid = self._job["id"]
        self._email_lbl = QPushButton("✉")
        self._email_lbl.setFixedSize(22, 22)
        self._email_lbl.setFont(F_BODY(12))
        self._email_lbl.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._email_lbl.clicked.connect(lambda: self._app._edit_job_email(jid))
        lay.addWidget(self._email_lbl)
        self._refresh_email_btn_style(email_cfg)

        return strip

    def _refresh_email_btn_style(self, email_cfg: dict | None):
        if email_cfg and email_cfg.get("to", "").strip():
            self._email_lbl.setStyleSheet(
                f"QPushButton {{ color: {TEAL}; background: transparent; "
                f"border: none; padding: 0; }}"
                f"QPushButton:hover {{ color: {TEAL_DEEP}; }}")
            self._email_lbl.setToolTip(f"Edit email → {email_cfg.get('to', '')}")
        else:
            self._email_lbl.setStyleSheet(
                f"QPushButton {{ color: {_th_stone()}; background: transparent; "
                f"border: none; padding: 0; }}"
                f"QPushButton:hover {{ color: {_th_graphite()}; }}")
            self._email_lbl.setToolTip("Add email notification…")

    def _build_watch_status_row(self) -> QWidget:
        row = QWidget()
        row.setStyleSheet(f"background: {_th_surface2()};")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(8)
        lay.addStretch(1)
        for label, tip, slot in (
            ("Pause uploads",  "Pause all active and queued uploads in this job", self._pause_all_uploads),
            ("Resume uploads", "Resume all paused uploads in this job",            self._resume_all_uploads),
            ("Cancel uploads", "Cancel all uploads in this job",                   self._cancel_all_uploads),
        ):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setObjectName("ghost")
            btn.setFixedHeight(22)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(slot)
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def set_watch_status(self, msg: str, color: str = ""):
        if not self._watch_lbl:
            return
        self._watch_lbl.setText(msg)
        color_map = {"green": _th_green(), "yellow": _th_yellow(), "red": _th_red(),
                     "dim": _th_stone(), "teal": TEAL}
        c = color_map.get(color, color or TEAL)
        self._watch_lbl.setStyleSheet(f"color: {c};")
        self._watch_dot.set_color(c)

    def _toggle_watch(self):
        if self._watching:
            self._app._stop_watch_job(self._job["id"])
        else:
            self._app._resume_watch_job(self._job["id"])

    def set_watch_stopped(self):
        self._watching = False
        if self._stop_watch_btn:
            self._stop_watch_btn.setText("Resume")
            self._stop_watch_btn.setToolTip("Resume watching this folder")

    def set_watch_resumed(self):
        self._watching = True
        if self._stop_watch_btn:
            self._stop_watch_btn.setText("Stop")
            self._stop_watch_btn.setToolTip("Stop watching this folder")

    def hide_watch_controls(self):
        pass  # kept for compat; controls stay visible, just toggle state

    def _pause_all_uploads(self):
        for eid, row in list(self._rows.items()):
            if getattr(row, "_status", "") in ("uploading", "in_progress", "queued", "compressing"):
                self._app._pause_upload(eid)

    def _resume_all_uploads(self):
        for eid, row in list(self._rows.items()):
            if getattr(row, "_status", "") == "paused":
                self._app._resume_upload(eid)

    def _cancel_all_uploads(self):
        for eid, row in list(self._rows.items()):
            if getattr(row, "_status", "") not in ("done", "failed"):
                self._app._cancel_upload(eid)

    def _toggle_expand(self):
        self._expanded = not self._expanded
        self._tri.setText("▾" if self._expanded else "▸")
        self._files_widget.setVisible(self._expanded)

    def _pause_all(self):
        for entry_id, row in list(self._rows.items()):
            if getattr(row, "_status", "") in ("uploading", "in_progress"):
                self._app._cancel_upload(entry_id)

    def _update_header(self):
        n    = len(self._rows)
        done = sum(1 for r in self._rows.values()
                   if getattr(r, "_status", "") == "done")
        self._count_lbl.setText(f"{done}/{n}" if n else "")
        uploading = any(
            getattr(r, "_status", "") in ("uploading", "in_progress")
            for r in self._rows.values()
        )
        self._pause_btn.setVisible(uploading)

    def _update_progress_bar(self):
        if not self._rows:
            self._prog_bar.set_pct(0.0)
            return
        total_size = sum(r._file_size for r in self._rows.values())
        total_disp = sum(r._bytes_disp for r in self._rows.values())
        self._prog_bar.set_pct(total_disp / total_size if total_size else 0.0)

    def refresh_theme(self):
        source = self._job.get("source", "manual")
        if GLASS_PANELS_ACTIVE:
            # Tile header floats on the queue panel glass — same surface as Drive/Folder controls
            self.setStyleSheet(f"""
                JobTile {{
                    background: transparent;
                    border: 1px solid {TEAL_PALE};
                    border-radius: 2px;
                }}
            """)
            self._inner.setStyleSheet("QWidget { background: transparent; }")
            self._hdr.setStyleSheet("QFrame { background: transparent; }")
            # STONE-on-glass rule: use GRAPHITE in the transparent header
            self._tri.setStyleSheet(f"color: {_th_graphite()};")
            self._count_lbl.setStyleSheet(f"color: {_th_graphite()};")
            # Re-apply badge — parent transparent cascade wipes it otherwise
            if source == "watch":
                bbg = ("rgba(0,137,166,0.85)" if _IS_DARK else TEAL_WASH)
                bfg = ("#FFFFFF" if _IS_DARK else TEAL_DEEP)
            else:
                bbg = (SURFACE2_DARK if _IS_DARK else SURFACE2)
                bfg = _th_graphite()
        else:
            self.setStyleSheet(f"""
                JobTile {{
                    background: {_th_surface()};
                    border: 1px solid {TEAL_PALE};
                    border-radius: 2px;
                }}
            """)
            self._inner.setStyleSheet(f"background: {_th_surface()};")
            self._hdr.setStyleSheet(f"background: {_th_surface()};")
            self._tri.setStyleSheet(f"color: {_th_stone()};")
            self._count_lbl.setStyleSheet(f"color: {_th_stone()};")
            if source == "watch":
                bbg, bfg = TEAL_WASH, TEAL_DEEP
            else:
                bbg, bfg = _th_surface2(), _th_stone()
        self._badge.setStyleSheet(
            f"color: {bfg}; background: {bbg}; "
            f"border-radius: 2px; padding: 2px 6px; letter-spacing: 1px;")
        # Strip and file rows stay solid — STONE text lives here
        self._strip.setStyleSheet(f"background: {_th_surface2()};")
        self._files_widget.setStyleSheet(f"background: {_th_surface()};")
        self._name_lbl.setStyleSheet(f"color: {_th_ink()};")
        self._folder_lbl.setStyleSheet(f"color: {_th_stone()};")
        if self._watch_status_row is not None:
            self._watch_status_row.setStyleSheet(f"background: {_th_surface2()};")
        for row in self._rows.values():
            row.refresh_theme()


    def add_file(self, entry):
        row = FileRow(self._files_widget, entry,
                      cancel_cb=self._app._cancel_upload,
                      resume_cb=self._app._resume_upload,
                      retry_cb=self._app._resume_upload)
        self._rows[entry.id] = row
        self._files_lay.addWidget(row)
        self._update_header()
        if not self._expanded:
            self._toggle_expand()

    def get_file_row(self, entry_id: str):
        return self._rows.get(entry_id)

    def update_counts(self):
        self._update_header()
        self._update_progress_bar()

    def remove_completed_rows(self):
        for eid, row in list(self._rows.items()):
            if getattr(row, "_status", "") in ("done", "failed"):
                row.setParent(None)
                row.deleteLater()
                del self._rows[eid]
        self._update_header()

    def set_email_status(self, status: str, held: bool = False):
        if not self._email_lbl:
            return
        color_map = {"sent": GREEN, "failed": RED, "sending": TEAL_MID}
        c = color_map.get(status, TEAL)
        self._email_lbl.setStyleSheet(
            f"QPushButton {{ color: {c}; background: transparent; border: none; padding: 0; }}"
            f"QPushButton:hover {{ color: {c}; }}")
        self._email_lbl.setToolTip(f"Email {status}")


# ── QueuePanel ─────────────────────────────────────────────────────────────────

class QueuePanel(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app   = app
        self._tiles: dict[str, JobTile] = {}
        self.setStyleSheet(f"background: {_th_bg_glass()};")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._toolbar = QFrame()
        toolbar = self._toolbar
        toolbar.setFixedHeight(40)
        toolbar.setStyleSheet(
            f"QFrame {{ background: {_th_bg_glass()}; border-bottom: 1px solid {_th_mist()}; }}")
        t_lay = QHBoxLayout(toolbar)
        t_lay.setContentsMargins(SP_QUEUE_H, 0, 16, 0)
        t_lay.setSpacing(10)

        self._queue_lbl = QLabel("Queue")
        self._queue_lbl.setFont(F_SEMIBOLD(12))
        self._queue_lbl.setStyleSheet(f"color: {_th_ink()};")
        t_lay.addWidget(self._queue_lbl)
        t_lay.addStretch()

        clear_btn = QPushButton("Clear Done")
        clear_btn.setObjectName("ghost")
        clear_btn.setFont(F_BODY(11))
        clear_btn.setFixedHeight(26)
        clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        clear_btn.clicked.connect(lambda: self._app._clear_completed())
        t_lay.addWidget(clear_btn)
        lay.addWidget(toolbar)

        self._scroll = QScrollArea()
        scroll = self._scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")

        self._content = QWidget()
        self._content.setStyleSheet(f"background: {_th_bg_glass()};")
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(SP_QUEUE_H, 12, SP_QUEUE_H, SP_QUEUE_H)
        self._content_lay.setSpacing(SP_TILE_GAP)

        self._empty_lbl = QLabel("No jobs yet. Use the form above to add one.")
        self._empty_lbl.setFont(F_BODY(12))
        self._empty_lbl.setStyleSheet(f"color: {_th_stone()};")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._content_lay.addWidget(self._empty_lbl)
        self._content_lay.addStretch()

        scroll.setWidget(self._content)
        lay.addWidget(scroll, 1)

    def refresh_theme(self):
        if GLASS_PANELS_ACTIVE:
            self.setStyleSheet("background: transparent;")
            self._toolbar.setStyleSheet(
                f"QFrame {{ background: transparent; border-bottom: 1px solid {_th_mist()}; }}")
            self._content.setStyleSheet("background: transparent;")
            self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            self._scroll.viewport().setStyleSheet("background: transparent;")
        else:
            self.setStyleSheet(f"background: {_th_bg_glass()};")
            self._toolbar.setStyleSheet(
                f"QFrame {{ background: {_th_bg_glass()}; border-bottom: 1px solid {_th_mist()}; }}")
            self._content.setStyleSheet(f"background: {_th_bg_glass()};")
        self._queue_lbl.setStyleSheet(f"color: {_th_ink()};")
        self._empty_lbl.setStyleSheet(f"color: {_th_stone()};")

    def add_tile(self, job_id: str, tile: JobTile):
        self._tiles[job_id] = tile
        count = self._content_lay.count()
        self._content_lay.insertWidget(count - 1, tile)
        self._refresh_empty()

    def remove_tiles(self, job_ids: list):
        for jid in job_ids:
            tile = self._tiles.pop(jid, None)
            if tile:
                tile.setParent(None)
                tile.deleteLater()
        self._refresh_empty()

    def remove_tile(self, job_id: str):
        tile = self._tiles.pop(job_id, None)
        if tile:
            tile.setParent(None)
            tile.deleteLater()
        self._refresh_empty()

    def _refresh_empty(self):
        self._empty_lbl.setVisible(len(self._tiles) == 0)

    def update_active_count(self, n: int):
        self._queue_lbl.setText(f"Queue — {n} active" if n else "Queue")


# ── JobCreationPanel ───────────────────────────────────────────────────────────

class JobCreationPanel(QFrame):
    job_requested    = pyqtSignal(dict)
    _folders_ready   = pyqtSignal(list)   # emitted from bg thread → main thread
    _folders_failed  = pyqtSignal(str)    # emitted from bg thread → main thread

    def __init__(self, cfg: dict, app, parent=None):
        super().__init__(parent)
        self._cfg  = cfg
        self._app  = app
        self._mode = "upload"
        self._pending_files: list[str]   = []
        self._pending_folders: list[str] = []
        self._folder_id   = ""
        self._folder_name = ""
        self._available_folders: list = []
        self._acct_ids: list[str]     = []
        self._watch_path  = ""
        self._email_draft_u: dict | None = None
        self._email_draft_w: dict | None = None
        self._folder_load_pending: str = ""  # which picker to show after preload

        self._folders_ready.connect(self._on_folders_ready)
        self._folders_failed.connect(self._on_folders_failed)

        self.setStyleSheet(
            f"JobCreationPanel {{ background: {_th_surface_glass()}; border-bottom: 1px solid {_th_mist()}; }}")
        self.setFixedHeight(280)

        root = QVBoxLayout(self)
        root.setContentsMargins(SP_PANEL_H, SP_PANEL_V, SP_PANEL_H, SP_PANEL_V)
        root.setSpacing(6)

        # Type tabs + status/actions row
        tab_row = QWidget()
        tab_row.setStyleSheet("background: transparent;")
        tab_lay = QHBoxLayout(tab_row)
        tab_lay.setContentsMargins(0, 0, 0, 0)
        tab_lay.setSpacing(4)

        self._upload_tab = QPushButton("Upload Files")
        self._upload_tab.setCheckable(True)
        self._upload_tab.setChecked(True)
        self._upload_tab.setFont(F_SEMIBOLD(12))
        self._upload_tab.setFixedHeight(28)
        self._upload_tab.setStyleSheet(self._tab_style(True))
        self._upload_tab.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._upload_tab.clicked.connect(lambda: self._switch_mode("upload"))

        self._watch_tab = QPushButton("Watch Folder")
        self._watch_tab.setCheckable(True)
        self._watch_tab.setChecked(False)
        self._watch_tab.setFont(F_SEMIBOLD(12))
        self._watch_tab.setFixedHeight(28)
        self._watch_tab.setStyleSheet(self._tab_style(False))
        self._watch_tab.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._watch_tab.clicked.connect(lambda: self._switch_mode("watch"))

        tab_lay.addWidget(self._upload_tab)
        tab_lay.addWidget(self._watch_tab)
        tab_lay.addStretch()

        # Status indicator (right-aligned in same row as tabs)
        self._status_dot = PulsingDot(size=7, color=STONE)
        tab_lay.addWidget(self._status_dot)
        tab_lay.addSpacing(6)

        self._status_lbl = QLabel("Connecting…")
        self._status_lbl.setFont(F_BODY(12))
        self._status_lbl.setStyleSheet(f"color: {_th_graphite()};")
        tab_lay.addWidget(self._status_lbl)

        tab_lay.addWidget(VDivider(), 0)
        tab_lay.addSpacing(8)

        self._settings_btn = QPushButton("Settings")
        self._settings_btn.setObjectName("link")
        self._settings_btn.setFont(F_BODY(12))
        self._settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        tab_lay.addWidget(self._settings_btn)

        tab_lay.addSpacing(8)

        self._log_btn = QPushButton("Log")
        self._log_btn.setObjectName("link")
        self._log_btn.setFont(F_BODY(12))
        self._log_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        tab_lay.addWidget(self._log_btn)

        root.addWidget(tab_row)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        self._stack.addWidget(self._build_upload_page())
        self._stack.addWidget(self._build_watch_page())
        root.addWidget(self._stack, 1)

    @staticmethod
    def _tab_style(active: bool) -> str:
        if active:
            return (f"QPushButton {{ background: {TEAL}; color: white; border: none; "
                    f"border-radius: 4px; padding: 4px 14px; }}"
                    f"QPushButton:hover {{ background: {TEAL_DEEP}; color: white; }}")
        return (f"QPushButton {{ background: {_th_surface2()}; color: {_th_ink()}; "
                f"border: 1px solid {TEAL_PALE}; border-radius: 4px; padding: 4px 14px; }}"
                f"QPushButton:hover {{ background: {TEAL_WASH}; border-color: {TEAL_MID}; color: {_th_ink()}; }}")

    def _switch_mode(self, mode: str):
        self._mode = mode
        self._upload_tab.setChecked(mode == "upload")
        self._watch_tab.setChecked(mode == "watch")
        self._upload_tab.setStyleSheet(self._tab_style(mode == "upload"))
        self._watch_tab.setStyleSheet(self._tab_style(mode == "watch"))
        self._stack.setCurrentIndex(0 if mode == "upload" else 1)

    def update_status(self, text: str, color: str = None,
                      dot_color: str = None, pulsing: bool = False):
        self._status_lbl.setText(text)
        # Status lbl is on glass — use GRAPHITE (glass-safe) as default, not STONE
        self._status_lbl.setStyleSheet(f"color: {color or _th_graphite()};")
        if dot_color:
            self._status_dot.set_color(dot_color)

    def _dest_btn_style(self) -> str:
        return (
            f"QPushButton {{ background: {_th_surface2()}; color: {_th_ink()}; "
            f"border: 1px solid {TEAL_PALE}; border-radius: 4px; text-align: left; padding: 0 8px; }}"
            f"QPushButton:hover {{ background: {TEAL_WASH}; border-color: {TEAL_MID}; color: {_th_ink()}; }}"
        )

    def _acct_combo_style(self) -> str:
        return (
            f"QComboBox {{ background: {_th_surface2()}; color: {_th_ink()}; "
            f"border: 1px solid {TEAL_PALE}; border-radius: 4px; padding: 0 8px; }}"
            f"QComboBox:hover {{ background: {TEAL_WASH}; border-color: {TEAL_MID}; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox QAbstractItemView {{ background: {_th_surface2()}; color: {_th_ink()}; "
            f"border: 1px solid {TEAL_PALE}; selection-background-color: {TEAL_WASH}; }}"
        )

    def refresh_theme(self):
        if GLASS_PANELS_ACTIVE:
            self.setStyleSheet("JobCreationPanel { background: transparent; border: none; }")
        else:
            self.setStyleSheet(
                f"JobCreationPanel {{ background: {_th_surface_glass()}; border-bottom: 1px solid {_th_mist()}; }}")
        self._upload_tab.setStyleSheet(self._tab_style(self._mode == "upload"))
        self._watch_tab.setStyleSheet(self._tab_style(self._mode == "watch"))
        self._status_lbl.setStyleSheet(f"color: {_th_graphite()};")
        self._file_count_lbl.setStyleSheet(f"color: {_th_graphite()};")
        self._drop_zone.refresh_theme()
        s = self._dest_btn_style()
        if hasattr(self, "_dest_lbl_u"):
            self._dest_lbl_u.setStyleSheet(s)
        if hasattr(self, "_dest_lbl_w"):
            self._dest_lbl_w.setStyleSheet(s)
        cs = self._acct_combo_style()
        if hasattr(self, "_acct_combo_u"):
            self._acct_combo_u.setStyleSheet(cs)
        if hasattr(self, "_acct_combo_w"):
            self._acct_combo_w.setStyleSheet(cs)


    # ── Upload page ───────────────────────────────────────────────────────────

    def _build_upload_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # Left: drop zone + browse
        left = QWidget()
        left.setStyleSheet("background: transparent;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        self._drop_zone = _DropZone(self._on_drop)
        self._drop_zone.setFixedHeight(80)
        dz_inner = QVBoxLayout(self._drop_zone)
        dz_inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dz_lbl = QLabel("Drop files or folders here")
        dz_lbl.setFont(F_BODY(12))
        dz_lbl.setStyleSheet(f"color: {_th_teal_link()}; background: transparent; border: none;")
        dz_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dz_inner.addWidget(dz_lbl)
        ll.addWidget(self._drop_zone)

        browse_row = QWidget()
        browse_row.setStyleSheet("background: transparent;")
        br_lay = QHBoxLayout(browse_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(6)
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("ghost")
        browse_btn.setFont(F_BODY(11))
        browse_btn.setFixedHeight(26)
        browse_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        browse_btn.clicked.connect(self._browse_upload_folder)
        br_lay.addWidget(browse_btn)
        self._file_count_lbl = QLabel("No files selected")
        self._file_count_lbl.setFont(F_BODY(11))
        self._file_count_lbl.setStyleSheet(f"color: {_th_graphite()};")
        br_lay.addWidget(self._file_count_lbl)
        br_lay.addStretch()
        ll.addWidget(browse_row)
        ll.addStretch()
        lay.addWidget(left, 2)

        # Right: settings
        right = QWidget()
        right.setStyleSheet("background: transparent;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        rl.addWidget(self._make_dest_row("u"))
        rl.addWidget(self._make_acct_row("u"))
        rl.addWidget(self._make_toggle_row("u"))

        self._zip_name_row_u = self._make_zip_name_row("u")
        self._zip_name_row_u.hide()
        rl.addWidget(self._zip_name_row_u)

        self._keep_zip_row_u = self._make_keep_zip_row("u")
        self._keep_zip_row_u.hide()
        rl.addWidget(self._keep_zip_row_u)

        self._email_row_u = self._make_email_compose_row("u")
        self._email_row_u.hide()
        rl.addWidget(self._email_row_u)
        rl.addStretch()

        self._add_btn_u = QPushButton("Add to Queue  ▶")
        self._add_btn_u.setObjectName("primary")
        self._add_btn_u.setFont(F_SEMIBOLD(12))
        self._add_btn_u.setFixedHeight(32)
        self._add_btn_u.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._add_btn_u.setStyleSheet(
            f"QPushButton {{ background: {TEAL}; color: white; border: none; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {TEAL_DEEP}; }}"
            f"QPushButton:pressed {{ background: {TEAL_DEEP}; }}")
        self._add_btn_u.clicked.connect(self._add_upload_job)
        rl.addWidget(self._add_btn_u)
        lay.addWidget(right, 3)
        return page

    # ── Watch page ────────────────────────────────────────────────────────────

    def _build_watch_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        left = QWidget()
        left.setStyleSheet("background: transparent;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        wf_lbl = QLabel("Watch Folder")
        wf_lbl.setFont(F_LABEL(10))
        wf_lbl.setStyleSheet(f"color: {_th_stone()};")
        ll.addWidget(wf_lbl)

        wfr = QWidget()
        wfr.setStyleSheet("background: transparent;")
        wfr_lay = QHBoxLayout(wfr)
        wfr_lay.setContentsMargins(0, 0, 0, 0)
        wfr_lay.setSpacing(6)
        self._watch_path_lbl = QLabel("— not selected —")
        self._watch_path_lbl.setFont(F_MONO(10))
        self._watch_path_lbl.setStyleSheet(f"color: {_th_stone()};")
        wfr_lay.addWidget(self._watch_path_lbl, 1)
        wf_browse = QPushButton("Browse…")
        wf_browse.setObjectName("ghost")
        wf_browse.setFont(F_BODY(11))
        wf_browse.setFixedHeight(26)
        wf_browse.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        wf_browse.clicked.connect(self._browse_watch_folder)
        wfr_lay.addWidget(wf_browse)
        ll.addWidget(wfr)

        # Advanced params
        adv_lbl = QLabel("Advanced")
        adv_lbl.setFont(F_LABEL(10))
        adv_lbl.setStyleSheet(f"color: {_th_stone()};")
        ll.addWidget(adv_lbl)

        timing_row = QWidget()
        timing_row.setStyleSheet("background: transparent;")
        tr_lay = QHBoxLayout(timing_row)
        tr_lay.setContentsMargins(0, 0, 0, 0)
        tr_lay.setSpacing(6)
        stable_lbl = QLabel("Stable:")
        stable_lbl.setFont(F_BODY(11))
        stable_lbl.setStyleSheet(f"color: {_th_stone()};")
        tr_lay.addWidget(stable_lbl)
        self._watch_stable_spin = QSpinBox()
        self._watch_stable_spin.setRange(5, 600)
        self._watch_stable_spin.setValue(15)
        self._watch_stable_spin.setSuffix(" s")
        self._watch_stable_spin.setFixedSize(70, 24)
        self._watch_stable_spin.setFont(F_BODY(11))
        self._watch_stable_spin.setToolTip(
            "Stable: how long files must be unchanged before they're\n"
            "considered done arriving (detects end of a copy/transfer)")
        stable_lbl.setToolTip(self._watch_stable_spin.toolTip())
        tr_lay.addWidget(self._watch_stable_spin)
        tr_lay.addSpacing(10)
        delay_lbl = QLabel("Delay:")
        delay_lbl.setFont(F_BODY(11))
        delay_lbl.setStyleSheet(f"color: {_th_stone()};")
        tr_lay.addWidget(delay_lbl)
        self._watch_delay_spin = QSpinBox()
        self._watch_delay_spin.setRange(0, 300)
        self._watch_delay_spin.setValue(0)
        self._watch_delay_spin.setSuffix(" s")
        self._watch_delay_spin.setFixedSize(70, 24)
        self._watch_delay_spin.setFont(F_BODY(11))
        self._watch_delay_spin.setToolTip(
            "Delay: after files are stable, wait this many extra seconds\n"
            "before zipping — grace period for metadata writes or late adds")
        delay_lbl.setToolTip(self._watch_delay_spin.toolTip())
        tr_lay.addWidget(self._watch_delay_spin)
        tr_lay.addStretch()
        ll.addWidget(timing_row)

        ext_row = QWidget()
        ext_row.setStyleSheet("background: transparent;")
        er_lay = QHBoxLayout(ext_row)
        er_lay.setContentsMargins(0, 0, 0, 0)
        er_lay.setSpacing(6)
        ext_lbl = QLabel("Types:")
        ext_lbl.setFont(F_BODY(11))
        ext_lbl.setStyleSheet(f"color: {_th_stone()};")
        er_lay.addWidget(ext_lbl)
        self._watch_exts_edit = QLineEdit()
        self._watch_exts_edit.setPlaceholderText(".mp4 .mov .mxf … (blank = all files)")
        self._watch_exts_edit.setFont(F_BODY(11))
        self._watch_exts_edit.setFixedHeight(24)
        er_lay.addWidget(self._watch_exts_edit, 1)
        ll.addWidget(ext_row)

        rec_row = QWidget()
        rec_row.setStyleSheet("background: transparent;")
        rec_lay = QHBoxLayout(rec_row)
        rec_lay.setContentsMargins(0, 0, 0, 0)
        rec_lay.setSpacing(6)
        self._watch_recursive_toggle = KToggle(on=False)
        self._watch_recursive_toggle.setToolTip(
            "Also watch all subfolders — new files in any nested folder will be picked up")
        rec_lbl = QLabel("Subfolders")
        rec_lbl.setFont(F_BODY(11))
        rec_lbl.setToolTip(self._watch_recursive_toggle.toolTip())
        rec_lay.addWidget(self._watch_recursive_toggle)
        rec_lay.addWidget(rec_lbl)
        rec_lay.addSpacing(16)
        self._watch_ignore_hidden_toggle = KToggle(on=True)
        self._watch_ignore_hidden_toggle.setToolTip(
            'Skip files whose names start with "." (hidden files)')
        hidden_lbl = QLabel("Ignore Hidden Files")
        hidden_lbl.setFont(F_BODY(11))
        hidden_lbl.setToolTip(self._watch_ignore_hidden_toggle.toolTip())
        rec_lay.addWidget(self._watch_ignore_hidden_toggle)
        rec_lay.addWidget(hidden_lbl)
        rec_lay.addStretch()
        ll.addWidget(rec_row)

        ll.addStretch()
        lay.addWidget(left, 2)

        right = QWidget()
        right.setStyleSheet("background: transparent;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        rl.addWidget(self._make_dest_row("w"))
        rl.addWidget(self._make_acct_row("w"))
        rl.addWidget(self._make_toggle_row("w"))

        self._zip_name_row_w = self._make_zip_name_row("w")
        self._zip_name_row_w.hide()
        rl.addWidget(self._zip_name_row_w)

        self._keep_zip_row_w = self._make_keep_zip_row("w")
        self._keep_zip_row_w.hide()
        rl.addWidget(self._keep_zip_row_w)

        self._subfolder_zip_row_w = self._make_subfolder_zip_row()
        self._subfolder_zip_row_w.hide()
        rl.addWidget(self._subfolder_zip_row_w)

        self._email_row_w = self._make_email_compose_row("w")
        self._email_row_w.hide()
        rl.addWidget(self._email_row_w)
        rl.addStretch()

        self._add_btn_w = QPushButton("Start Watching  ▶")
        self._add_btn_w.setObjectName("primary")
        self._add_btn_w.setFont(F_SEMIBOLD(12))
        self._add_btn_w.setFixedHeight(32)
        self._add_btn_w.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._add_btn_w.setStyleSheet(
            f"QPushButton {{ background: {TEAL}; color: white; border: none; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {TEAL_DEEP}; }}"
            f"QPushButton:pressed {{ background: {TEAL_DEEP}; }}")
        self._add_btn_w.clicked.connect(self._add_watch_job)
        rl.addWidget(self._add_btn_w)
        lay.addWidget(right, 3)
        return page

    # ── Row builders ──────────────────────────────────────────────────────────

    def _make_dest_row(self, which: str) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lbl = QLabel("Drive:")
        lbl.setFont(F_LABEL(10))
        lbl.setStyleSheet(f"color: {_th_graphite()};")
        lbl.setFixedWidth(36)
        lay.addWidget(lbl)
        # Full-width button — shows folder name once picked, placeholder until then
        dest_btn = QPushButton("📁  Pick Drive folder…")
        dest_btn.setFont(F_BODY(11))
        dest_btn.setFixedHeight(26)
        dest_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        dest_btn.setStyleSheet(self._dest_btn_style())
        dest_btn.clicked.connect(lambda: self._pick_folder(which))
        lay.addWidget(dest_btn, 1)
        if which == "u":
            self._dest_lbl_u = dest_btn
        else:
            self._dest_lbl_w = dest_btn
        return row

    def _make_acct_row(self, which: str) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 2)
        lay.setSpacing(6)
        lbl = QLabel("Acct:")
        lbl.setFont(F_LABEL(10))
        lbl.setStyleSheet(f"color: {_th_graphite()};")
        lbl.setFixedWidth(36)
        lay.addWidget(lbl)
        combo = QComboBox()
        combo.setFont(F_BODY(11))
        combo.setFixedHeight(26)
        combo.setStyleSheet(self._acct_combo_style())
        self._populate_accounts(combo)
        lay.addWidget(combo, 1)
        if which == "u":
            self._acct_combo_u = combo
        else:
            self._acct_combo_w = combo
        return row

    def _make_toggle_row(self, which: str) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        zip_toggle = KToggle(on=False)
        zip_lbl = QLabel("Zip")
        zip_lbl.setFont(F_BODY(11))
        lay.addWidget(zip_toggle)
        lay.addWidget(zip_lbl)

        email_toggle = KToggle(on=False)
        email_lbl = QLabel("Email")
        email_lbl.setFont(F_BODY(11))
        lay.addWidget(email_toggle)
        lay.addWidget(email_lbl)
        lay.addStretch()

        if which == "u":
            self._zip_toggle_u   = zip_toggle
            self._email_toggle_u = email_toggle
            zip_toggle.toggled.connect(self._on_zip_toggle_u)
            email_toggle.toggled.connect(self._on_email_toggle_u)
        else:
            self._zip_toggle_w   = zip_toggle
            self._email_toggle_w = email_toggle
            zip_toggle.toggled.connect(self._on_zip_toggle_w)
            email_toggle.toggled.connect(self._on_email_toggle_w)
        return row

    def _make_email_compose_row(self, which: str) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lbl = QLabel("No email configured")
        lbl.setFont(F_MONO(10))
        lbl.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(lbl, 1)
        if which == "u":
            self._email_summary_lbl_u = lbl
        else:
            self._email_summary_lbl_w = lbl
        compose_btn = QPushButton("Compose…")
        compose_btn.setObjectName("ghost")
        compose_btn.setFont(F_BODY(11))
        compose_btn.setFixedHeight(24)
        compose_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        compose_btn.clicked.connect(lambda: self._compose_email(which))
        lay.addWidget(compose_btn)
        return row

    def _make_zip_name_row(self, which: str) -> QWidget:
        row = QWidget(); row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        lbl = QLabel("Zip name:"); lbl.setFont(F_LABEL(10))
        lbl.setStyleSheet(f"color: {_th_stone()};"); lbl.setFixedWidth(60)
        lay.addWidget(lbl)
        edit = QLineEdit(); edit.setFont(F_BODY(11)); edit.setFixedHeight(24)
        edit.setPlaceholderText("auto-generated if blank")
        lay.addWidget(edit, 1)
        if which == "u":
            self._zip_name_edit_u = edit
        else:
            self._zip_name_edit_w = edit
        return row

    def _make_subfolder_zip_row(self) -> QWidget:
        row = QWidget(); row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        self._subfolder_zip_toggle_w = KToggle(on=False)
        lbl = QLabel("Zip per subfolder"); lbl.setFont(F_BODY(11))
        tip = "Zip each new subfolder individually, named after the subfolder"
        lbl.setToolTip(tip)
        self._subfolder_zip_toggle_w.setToolTip(tip)
        lay.addWidget(self._subfolder_zip_toggle_w)
        lay.addWidget(lbl)
        lay.addStretch()
        return row

    def _make_keep_zip_row(self, which: str) -> QWidget:
        row = QWidget(); row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        toggle = KToggle(on=False)
        lbl = QLabel("Keep zip after upload"); lbl.setFont(F_BODY(11))
        lbl.setToolTip("Keep the zip file locally after uploading (default: delete)")
        toggle.setToolTip(lbl.toolTip())
        lay.addWidget(toggle)
        lay.addWidget(lbl)
        lay.addStretch()
        if which == "u":
            self._keep_zip_toggle_u = toggle
        else:
            self._keep_zip_toggle_w = toggle
        return row

    def _on_zip_toggle_u(self, on: bool):
        self._zip_name_row_u.setVisible(on)
        self._keep_zip_row_u.setVisible(on)
        if not on:
            self._zip_name_edit_u.clear()
            self._keep_zip_toggle_u.set(False)

    def _on_zip_toggle_w(self, on: bool):
        self._zip_name_row_w.setVisible(on)
        self._keep_zip_row_w.setVisible(on)
        self._subfolder_zip_row_w.setVisible(on)
        if not on:
            self._zip_name_edit_w.clear()
            self._keep_zip_toggle_w.set(False)
            self._subfolder_zip_toggle_w.set(False)

    def _on_email_toggle_u(self, on: bool):
        self._email_row_u.setVisible(on)

    def _on_email_toggle_w(self, on: bool):
        self._email_row_w.setVisible(on)

    def _compose_email(self, which: str):
        draft = self._email_draft_u if which == "u" else self._email_draft_w
        dlg = EmailDraftDialog(self, draft, cfg=self._cfg)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_draft:
            if which == "u":
                self._email_draft_u = dlg.result_draft
                self._email_summary_lbl_u.setText(f"→ {dlg.result_draft['to']}")
                self._email_summary_lbl_u.setStyleSheet(f"color: {_th_ink()};")
            else:
                self._email_draft_w = dlg.result_draft
                self._email_summary_lbl_w.setText(f"→ {dlg.result_draft['to']}")
                self._email_summary_lbl_w.setStyleSheet(f"color: {_th_ink()};")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _populate_accounts(self, combo: QComboBox):
        combo.clear()
        accounts       = drive_accounts.list_accounts()
        self._acct_ids = [a["id"] for a in accounts]
        for a in accounts:
            combo.addItem(a.get("name") or a.get("email", a["id"][:12]))
        active_id = self._cfg.get("active_drive_account_id", "")
        if active_id in self._acct_ids:
            combo.setCurrentIndex(self._acct_ids.index(active_id))

    def _get_acct_id(self, combo: QComboBox) -> str:
        idx = combo.currentIndex()
        return self._acct_ids[idx] if 0 <= idx < len(self._acct_ids) else ""

    def _on_drop(self, paths: list):
        for p in paths:
            if Path(p).is_dir():
                if p not in self._pending_folders:
                    self._pending_folders.append(p)
            elif Path(p).is_file():
                if p not in self._pending_files:
                    self._pending_files.append(p)
        self._update_selection_label()

    def _update_selection_label(self):
        parts = []
        if self._pending_folders:
            n = len(self._pending_folders)
            parts.append(f"{n} folder{'s' if n != 1 else ''}")
        if self._pending_files:
            n = len(self._pending_files)
            parts.append(f"{n} file{'s' if n != 1 else ''}")
        if parts:
            self._file_count_lbl.setText(" + ".join(parts) + " selected")
            self._file_count_lbl.setStyleSheet(f"color: {_th_ink()};")
        else:
            self._file_count_lbl.setText("No files selected")
            self._file_count_lbl.setStyleSheet(f"color: {_th_graphite()};")

    def _browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Files", self._cfg.get("last_browse_dir", "/Volumes"))
        if paths:
            self._cfg["last_browse_dir"] = str(Path(paths[0]).parent)
            self._on_drop(paths)

    def _browse_upload_folder(self):
        try:
            from AppKit import NSOpenPanel, NSOKButton
            from Foundation import NSURL
            panel = NSOpenPanel.openPanel()
            panel.setTitle_("Select Files and Folders")
            panel.setCanChooseFiles_(True)
            panel.setCanChooseDirectories_(True)
            panel.setAllowsMultipleSelection_(True)
            panel.setResolvesAliases_(True)
            start = self._cfg.get("last_browse_dir", "/Volumes")
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(start))
            if panel.runModal() != NSOKButton:
                return
            paths = [url.path() for url in panel.URLs()]
            if not paths:
                return
            self._cfg["last_browse_dir"] = str(Path(paths[0]).parent)
            self._on_drop(paths)
        except Exception:
            # Fallback: single-folder QFileDialog
            start = self._cfg.get("last_browse_dir", "/Volumes")
            folder = QFileDialog.getExistingDirectory(self, "Select Folder", start)
            if folder:
                self._cfg["last_browse_dir"] = str(Path(folder).parent)
                self._on_drop([folder])

    def _browse_watch_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Watch Folder", self._cfg.get("last_browse_dir", "/Volumes"))
        if folder:
            self._cfg["last_browse_dir"] = str(Path(folder).parent)
            self._watch_path = folder
            display = folder if len(folder) < 44 else "…" + folder[-42:]
            self._watch_path_lbl.setText(display)
            self._watch_path_lbl.setStyleSheet(f"color: {_th_ink()};")

    def preload_folders(self, acct_id: str):
        """Called by App after accounts ready. Loads folders silently in background."""
        if self._available_folders or not acct_id:
            return
        for btn in (self._dest_lbl_u, self._dest_lbl_w):
            btn.setText("📁  Loading folders…")
            btn.setEnabled(False)
        def _load():
            try:
                svc     = drive_accounts.get_service(acct_id)
                folders = drivelib.list_folders(svc)
                self._folders_ready.emit(folders)
            except Exception as e:
                self._folders_failed.emit(str(e))
        threading.Thread(target=_load, daemon=True).start()

    def _on_folders_ready(self, folders: list):
        self._available_folders = folders
        for btn in (self._dest_lbl_u, self._dest_lbl_w):
            btn.setText("📁  Pick Drive folder…")
            btn.setEnabled(True)
        if self._folder_load_pending:
            which, self._folder_load_pending = self._folder_load_pending, ""
            self._show_folder_picker(which)

    def _on_folders_failed(self, err: str):
        for btn in (self._dest_lbl_u, self._dest_lbl_w):
            btn.setText("📁  Pick Drive folder…")
            btn.setEnabled(True)
        self._folder_load_pending = ""
        QMessageBox.warning(self, "Drive Error", f"Could not load folders:\n{err}")

    def _pick_folder(self, which: str):
        try:
            combo   = self._acct_combo_u if which == "u" else self._acct_combo_w
            acct_id = self._get_acct_id(combo)
            if not acct_id:
                QMessageBox.warning(
                    self, "No Account",
                    "Add a Google Drive account first using the Accounts button "
                    "in the title bar.")
                return
            if self._available_folders:
                self._show_folder_picker(which)
                return
            # Folders still loading — store intent, show picker when they arrive
            self._folder_load_pending = which
            if not any(not btn.isEnabled() for btn in (self._dest_lbl_u, self._dest_lbl_w)):
                # Preload not already running — start it now
                self.preload_folders(acct_id)
        except Exception:
            import traceback; traceback.print_exc()

    def _show_folder_picker(self, which: str):
        try:
            dlg = FolderPickerDialog(self, self._available_folders)
        except Exception:
            import traceback; traceback.print_exc()
            return
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_id:
            self._folder_id   = dlg.result_id
            self._folder_name = dlg.result_name
            dest_btn = self._dest_lbl_u if which == "u" else self._dest_lbl_w
            disp = self._folder_name
            if len(disp) > 38:
                disp = "…" + disp[-36:]
            dest_btn.setText(f"📁  {disp}")
            dest_btn.setStyleSheet(
                f"QPushButton {{ background: {TEAL_WASH}; color: {_th_ink()}; "
                f"border: 1px solid {TEAL_MID}; border-radius: 4px; text-align: left; padding: 0 8px; }}"
                f"QPushButton:hover {{ background: {TEAL_PALE}; border-color: {TEAL}; color: {_th_ink()}; }}"
            )

    def _build_email_cfg(self, toggle: KToggle, which: str) -> dict | None:
        if not toggle._on:
            return None
        draft = self._email_draft_u if which == "u" else self._email_draft_w
        if not draft or not draft.get("to"):
            return None
        return draft

    def _add_upload_job(self):
        if not self._pending_files and not self._pending_folders:
            QMessageBox.warning(self, "No Files", "Select files or folders to upload.")
            return
        if not self._folder_id:
            QMessageBox.warning(self, "No Destination", "Pick a Drive folder first.")
            return
        acct_id   = self._get_acct_id(self._acct_combo_u)
        zip_on    = self._zip_toggle_u._on
        email_cfg = self._build_email_cfg(self._email_toggle_u, "u")
        keep_zip  = self._keep_zip_toggle_u._on if zip_on else False
        custom_zip_name = self._zip_name_edit_u.text().strip() if zip_on else ""
        base = {
            "source":           "manual",
            "drive_account_id": acct_id,
            "drive_folder_id":  self._folder_id,
            "drive_folder_name": self._folder_name,
            "email_cfg":        email_cfg,
            "zip":              zip_on,
            "keep_zip":         keep_zip,
        }
        ts = datetime.now().strftime("%H:%M")
        # One job per folder — preserves structure
        for folder in self._pending_folders:
            folder_name = Path(folder).name
            spec = {**base,
                    "name":       f"Upload · {ts} · {folder_name}",
                    "zip_name":   custom_zip_name or (folder_name if zip_on else ""),
                    "folder_src": folder,
                    "files":      []}
            self.job_requested.emit(spec)
        # One job for loose files
        if self._pending_files:
            n = len(self._pending_files)
            spec = {**base,
                    "name":    f"Upload · {ts} · {n} file{'s' if n != 1 else ''}",
                    "zip_name": custom_zip_name,
                    "files":    list(self._pending_files)}
            self.job_requested.emit(spec)
        self._pending_files   = []
        self._pending_folders = []
        self._update_selection_label()

    def _add_watch_job(self):
        if not self._watch_path or not Path(self._watch_path).is_dir():
            QMessageBox.warning(self, "No Folder", "Select a local folder to watch.")
            return
        if not self._folder_id:
            QMessageBox.warning(self, "No Destination", "Pick a Drive folder first.")
            return
        acct_id  = self._get_acct_id(self._acct_combo_w)
        zip_on   = self._zip_toggle_w._on
        email_cfg = self._build_email_cfg(self._email_toggle_w, "w")
        raw_exts = self._watch_exts_edit.text().strip()
        exts = [e.strip() if e.strip().startswith(".") else f".{e.strip()}"
                for e in raw_exts.split() if e.strip()]
        spec = {
            "name":               f"Watch · {Path(self._watch_path).name}",
            "source":             "watch",
            "watch_folder":       self._watch_path,
            "drive_account_id":   acct_id,
            "drive_folder_id":    self._folder_id,
            "drive_folder_name":  self._folder_name,
            "email_cfg":          email_cfg,
            "zip":                zip_on,
            "zip_name":           self._zip_name_edit_w.text().strip() if zip_on else "",
            "keep_zip":           self._keep_zip_toggle_w._on if zip_on else False,
            "watch_stable_secs":  self._watch_stable_spin.value(),
            "watch_delay_secs":   self._watch_delay_spin.value(),
            "watch_extensions":   exts,
            "watch_recursive":    self._watch_recursive_toggle._on,
            "watch_ignore_hidden": self._watch_ignore_hidden_toggle._on,
            "watch_subfolder_zip": zip_on and self._subfolder_zip_toggle_w._on,
        }
        self.job_requested.emit(spec)
        self._watch_path = ""
        self._watch_path_lbl.setText("— not selected —")
        self._watch_path_lbl.setStyleSheet(f"color: {_th_stone()};")

    def refresh_accounts(self):
        self._available_folders = []
        self._populate_accounts(self._acct_combo_u)
        self._populate_accounts(self._acct_combo_w)


# ── WatchEditDialog ────────────────────────────────────────────────────────────

class WatchEditDialog(QDialog):
    """Edit watch-folder parameters for an existing queue tile."""
    _folders_ready = pyqtSignal(list)

    def __init__(self, job: dict, app, parent=None):
        super().__init__(parent)
        self._app = app
        self._job = job
        self._drive_folder_id   = job.get("drive_folder_id", "")
        self._drive_folder_name = job.get("drive_folder_name", "")
        self._watch_folder      = job.get("watch_folder", "")
        self._available_folders: list = []

        self._folders_ready.connect(self._on_folders_ready)

        self.setWindowTitle("Edit Watch Settings")
        self.setMinimumWidth(460)
        self.setStyleSheet(f"QDialog {{ background: {_th_surface()}; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        title = QLabel(f"Watch · {Path(self._watch_folder).name or 'folder'}")
        title.setFont(F_SEMIBOLD(13))
        title.setStyleSheet(f"color: {_th_ink()};")
        lay.addWidget(title)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {_th_mist()};"); lay.addWidget(sep)

        def lbl_row(label_text, *widgets):
            r = QWidget(); r.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(r); rl.setContentsMargins(0,0,0,0); rl.setSpacing(8)
            lbl = QLabel(label_text); lbl.setFont(F_BODY(11))
            lbl.setStyleSheet(f"color: {_th_stone()};"); lbl.setFixedWidth(100)
            rl.addWidget(lbl)
            for w in widgets: rl.addWidget(w) if not isinstance(w, int) else rl.addStretch()
            return r

        # ── Local watch folder ────────────────────────────────────────────────
        self._local_lbl = QPushButton(Path(self._watch_folder).name or "Pick folder…")
        self._local_lbl.setFont(F_BODY(11))
        self._local_lbl.setFixedHeight(26)
        self._local_lbl.setStyleSheet(
            f"QPushButton {{ background: {_th_surface2()}; color: {_th_ink()}; border: 1px solid {TEAL_PALE};"
            f" border-radius: 4px; text-align: left; padding: 0 8px; }}"
            f"QPushButton:hover {{ background: {TEAL_WASH}; border-color: {TEAL_MID}; }}")
        self._local_lbl.clicked.connect(self._pick_local)
        lay.addWidget(lbl_row("Watch folder:", self._local_lbl))

        # ── Drive destination ─────────────────────────────────────────────────
        self._drive_btn = QPushButton(self._drive_folder_name or "Pick Drive folder…")
        self._drive_btn.setFont(F_BODY(11))
        self._drive_btn.setFixedHeight(26)
        self._drive_btn.setStyleSheet(
            f"QPushButton {{ background: {_th_surface2()}; color: {_th_ink()}; border: 1px solid {TEAL_PALE};"
            f" border-radius: 4px; text-align: left; padding: 0 8px; }}"
            f"QPushButton:hover {{ background: {TEAL_WASH}; border-color: {TEAL_MID}; }}")
        self._drive_btn.clicked.connect(self._pick_drive)
        lay.addWidget(lbl_row("Drive dest:", self._drive_btn))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {_th_mist()};"); lay.addWidget(sep2)

        # ── Timing params ─────────────────────────────────────────────────────
        self._stable_spin = QSpinBox()
        self._stable_spin.setRange(5, 600); self._stable_spin.setSuffix(" s")
        self._stable_spin.setValue(int(job.get("watch_stable_secs", 15)))
        self._stable_spin.setToolTip(
            "How long files must be unchanged before considered done arriving")
        lay.addWidget(lbl_row("Stable buffer:", self._stable_spin))

        self._delay_spin = QSpinBox()
        self._delay_spin.setRange(0, 300); self._delay_spin.setSuffix(" s")
        self._delay_spin.setValue(int(job.get("watch_delay_secs", 0)))
        self._delay_spin.setToolTip("Extra wait after files are stable before zipping")
        lay.addWidget(lbl_row("Pre-zip delay:", self._delay_spin))

        self._exts_edit = QLineEdit()
        self._exts_edit.setFont(F_BODY(11))
        self._exts_edit.setPlaceholderText(".mp4 .mov .mxf … (blank = all files)")
        self._exts_edit.setText(" ".join(job.get("watch_extensions", [])))
        lay.addWidget(lbl_row("File types:", self._exts_edit))

        rec_row = QWidget(); rec_row.setStyleSheet("background: transparent;")
        rec_lay = QHBoxLayout(rec_row); rec_lay.setContentsMargins(0,0,0,0); rec_lay.setSpacing(8)
        rec_lbl = QLabel("Subfolders:"); rec_lbl.setFont(F_BODY(11))
        rec_lbl.setStyleSheet(f"color: {_th_stone()};"); rec_lbl.setFixedWidth(100)
        self._recursive_toggle = KToggle(on=bool(job.get("watch_recursive", False)))
        rec_sub = QLabel("Watch all nested subfolders"); rec_sub.setFont(F_BODY(11))
        rec_lay.addWidget(rec_lbl); rec_lay.addWidget(self._recursive_toggle)
        rec_lay.addWidget(rec_sub); rec_lay.addStretch()
        lay.addWidget(rec_row)

        hidden_row = QWidget(); hidden_row.setStyleSheet("background: transparent;")
        hidden_lay = QHBoxLayout(hidden_row); hidden_lay.setContentsMargins(0,0,0,0); hidden_lay.setSpacing(8)
        hidden_lbl = QLabel("Hidden files:"); hidden_lbl.setFont(F_BODY(11))
        hidden_lbl.setStyleSheet(f"color: {_th_stone()};"); hidden_lbl.setFixedWidth(100)
        self._ignore_hidden_toggle = KToggle(on=bool(job.get("watch_ignore_hidden", True)))
        hidden_sub = QLabel('Ignore hidden files (starting with ".")'); hidden_sub.setFont(F_BODY(11))
        hidden_lay.addWidget(hidden_lbl); hidden_lay.addWidget(self._ignore_hidden_toggle)
        hidden_lay.addWidget(hidden_sub); hidden_lay.addStretch()
        lay.addWidget(hidden_row)

        lay.addSpacing(6)
        btn_row = QWidget(); btn_row.setStyleSheet("background: transparent;")
        br = QHBoxLayout(btn_row); br.setContentsMargins(0,0,0,0); br.setSpacing(8)
        br.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost"); cancel_btn.setFixedHeight(30)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save & Restart Watch")
        save_btn.setFixedHeight(30)
        save_btn.setStyleSheet(
            f"QPushButton {{ background: {TEAL}; color: white; border: none; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {TEAL_DEEP}; }}")
        save_btn.clicked.connect(self.accept)
        br.addWidget(cancel_btn); br.addWidget(save_btn)
        lay.addWidget(btn_row)

    def _pick_local(self):
        start = (self._watch_folder
                 or self._app._cfg.get("last_browse_dir", "")
                 or str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Select Watch Folder", start)
        if folder:
            self._watch_folder = folder
            self._app._cfg["last_browse_dir"] = str(Path(folder).parent)
            self._local_lbl.setText(Path(folder).name)

    def _pick_drive(self):
        self._drive_btn.setText("Loading folders…")
        self._drive_btn.setEnabled(False)
        acct_id = self._job.get("drive_account_id", "")
        def _load():
            try:
                svc = drive_accounts.get_service(acct_id) if acct_id else drivelib.build_thread_service()
                folders = drivelib.list_folders(svc)
                self._folders_ready.emit(folders)
            except Exception as e:
                self._folders_ready.emit([])
        threading.Thread(target=_load, daemon=True).start()

    def _on_folders_ready(self, folders: list):
        self._available_folders = folders
        self._drive_btn.setText(self._drive_folder_name or "Pick Drive folder…")
        self._drive_btn.setEnabled(True)
        dlg = FolderPickerDialog(self, folders)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_id:
            self._drive_folder_id   = dlg.result_id
            self._drive_folder_name = dlg.result_name
            self._drive_btn.setText(dlg.result_name)

    def result_params(self) -> dict:
        raw = self._exts_edit.text().strip()
        exts = [e.strip() if e.strip().startswith(".") else f".{e.strip()}"
                for e in raw.split() if e.strip()]
        return {
            "watch_folder":      self._watch_folder,
            "drive_folder_id":   self._drive_folder_id,
            "drive_folder_name": self._drive_folder_name,
            "watch_stable_secs": self._stable_spin.value(),
            "watch_delay_secs":  self._delay_spin.value(),
            "watch_extensions":  exts,
            "watch_recursive":    self._recursive_toggle._on,
            "watch_ignore_hidden": self._ignore_hidden_toggle._on,
        }


# ── SettingsDialog ─────────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app
        self.setWindowTitle("Settings")
        self.resize(400, 220)
        self.setModal(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        title = QLabel("Settings")
        title.setFont(F_SEMIBOLD(16))
        lay.addWidget(title)

        # Accounts section
        acct_frame = QFrame()
        acct_frame.setStyleSheet(
            f"QFrame {{ background: {_th_surface2()}; border-radius: 6px; border: 1px solid {_th_mist()}; }}")
        af_lay = QVBoxLayout(acct_frame)
        af_lay.setContentsMargins(14, 10, 14, 10)
        af_lay.setSpacing(6)
        acct_hdr = QLabel("ACCOUNTS")
        acct_hdr.setObjectName("section-header")
        af_lay.addWidget(acct_hdr)
        accounts = drive_accounts.list_accounts()
        for a in accounts[:4]:
            row_lbl = QLabel(
                f"{a.get('name', '')}  —  {a.get('email', a['id'][:12])}")
            row_lbl.setFont(F_BODY(11))
            row_lbl.setStyleSheet(f"color: {_th_stone()}; background: transparent; border: none;")
            af_lay.addWidget(row_lbl)
        if not accounts:
            empty_lbl = QLabel("No accounts connected")
            empty_lbl.setFont(F_BODY(11))
            empty_lbl.setStyleSheet(f"color: {_th_stone()}; background: transparent; border: none;")
            af_lay.addWidget(empty_lbl)
        manage_btn = QPushButton("Manage Accounts…")
        manage_btn.setObjectName("ghost")
        manage_btn.setFixedHeight(26)
        manage_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        manage_btn.clicked.connect(self._open_accounts)
        af_lay.addWidget(manage_btn)
        lay.addWidget(acct_frame)

        lay.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setObjectName("primary")
        close_btn.setFixedHeight(32)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)

    def _open_accounts(self):
        self.accept()
        self._app._manage_accounts()


# ── App ────────────────────────────────────────────────────────────────────────

class App(QMainWindow):
    # Thread-safe signals: emitted from bg threads, handled on main thread
    _watch_file_signal   = pyqtSignal(str, str)        # path, job_id
    _watch_batch_signal  = pyqtSignal(list, str, str)  # files, job_id, zip_name_hint
    _watch_status_signal = pyqtSignal(str, str, str)   # msg, color, jid

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Uplift")
        self.resize(980, 740)
        self.setMinimumSize(820, 560)
        # Prevent QMainWindow from painting its own gray background;
        # the panels provide all visible surfaces via glass or solid fills.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("QMainWindow { background: transparent; }")

        _load_fonts()
        global _IS_DARK
        _IS_DARK = (QApplication.instance().styleHints().colorScheme()
                    == Qt.ColorScheme.Dark)
        QApplication.instance().setStyleSheet(_build_app_qss())
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            self._on_scheme_changed)

        self._cfg   = config.load()
        self._state = StateManager()

        self._drive_service = None
        self._progress_queue: queue.Queue = queue.Queue()
        self._active_workers: dict[str, tuple]     = {}
        self._active_zip_workers: dict[str, tuple] = {}
        self._upload_account: dict[str, str]        = {}

        self._jobs: dict[str, dict]       = {}
        self._job_tiles: dict[str, JobTile] = {}

        self._watch_watchers: dict[str, JobWatcher] = {}
        self._email_batch: dict[str, list]           = {}
        self._email_batch_timers: dict[str, QTimer]  = {}

        self._watch_file_signal.connect(self._on_watch_file_ready_main)
        self._watch_batch_signal.connect(self._on_watch_batch_ready_main)
        self._watch_status_signal.connect(self._on_watch_status_main)

        self._build_ui()
        self._handle_startup_state()
        pass  # tray removed

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll_progress)
        self._poll_timer.start()

        QTimer.singleShot(0, self._init_drive)

    def _build_ui(self):
        central = QWidget()
        central.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self._creation_panel = JobCreationPanel(self._cfg, app=self)
        self._creation_panel.job_requested.connect(self._on_job_requested)
        self._creation_panel._settings_btn.clicked.connect(self._open_settings)
        self._creation_panel._log_btn.clicked.connect(self._show_log_viewer)
        root.addWidget(self._creation_panel)

        self._queue_panel = QueuePanel(app=self)
        root.addWidget(self._queue_panel, 1)

    def prepare_glass_panels(self):
        """Call BEFORE show(). Marks panel widgets as transparent so native glass shows through."""
        try:
            import pyqt_liquidglass as lg
            lg.prepare_widget_for_glass(self._creation_panel)
            lg.prepare_widget_for_glass(self._queue_panel)
        except Exception:
            pass

    def apply_glass_panels(self):
        """Call AFTER show() and after first layout pass. Inserts native glass behind each panel."""
        global GLASS_PANELS_ACTIVE
        try:
            import pyqt_liquidglass as lg
            opts = lg.GlassOptions(corner_radius=12)
            lg.apply_glass_to_widget(self._creation_panel, options=opts)
            lg.apply_glass_to_widget(self._queue_panel, options=opts)
            GLASS_PANELS_ACTIVE = True
            self._creation_panel.setStyleSheet("JobCreationPanel { background: transparent; border: none; }")
            self._queue_panel.setStyleSheet("background: transparent;")
            for tile in self._job_tiles.values():
                tile.refresh_theme()
        except Exception:
            pass

    # ── Job creation ───────────────────────────────────────────────────────────

    def _on_job_requested(self, spec: dict):
        source    = spec["source"]
        email_cfg = spec.get("email_cfg")
        zip_on    = spec.get("zip", False)

        job = self._create_job(
            name=spec["name"],
            source=source,
            drive_account_id=spec.get("drive_account_id", ""),
            drive_folder_id=spec.get("drive_folder_id", ""),
            drive_folder_name=spec.get("drive_folder_name", ""),
            email_cfg=email_cfg,
            zip_on=zip_on,
        )

        job["zip_name"] = spec.get("zip_name", "")
        job["keep_zip"] = spec.get("keep_zip", False)

        if source == "manual":
            folder_src = spec.get("folder_src")
            if folder_src:
                if zip_on:
                    self._add_folder_as_zip(folder_src, job)
                else:
                    self._add_folder_as_structure(folder_src, job)
            else:
                files = spec.get("files", [])
                if zip_on and files:
                    self._add_files_as_zip(files, job)
                else:
                    self._add_files_for_job(files, job)
        elif source == "watch":
            job["watch_folder"]        = spec.get("watch_folder", "")
            job["watch_stable_secs"]   = spec.get("watch_stable_secs", 15)
            job["watch_delay_secs"]    = spec.get("watch_delay_secs", 0)
            job["watch_extensions"]    = spec.get("watch_extensions", [])
            job["watch_recursive"]     = spec.get("watch_recursive", False)
            job["watch_ignore_hidden"] = spec.get("watch_ignore_hidden", True)
            job["watch_subfolder_zip"] = spec.get("watch_subfolder_zip", False)
            self._start_job_watcher(job)

    def _create_job(self, name: str, source: str,
                    drive_account_id: str, drive_folder_id: str,
                    drive_folder_name: str,
                    email_cfg: dict | None = None,
                    zip_on: bool = False) -> dict:
        job_id = str(uuid.uuid4())
        job = {
            "id":               job_id,
            "name":             name,
            "source":           source,
            "drive_account_id": drive_account_id,
            "drive_folder_id":  drive_folder_id,
            "drive_folder_name": drive_folder_name,
            "email_cfg":        email_cfg,
            "zip":              zip_on,
            "status":           "active",
            "created_at":       datetime.now().isoformat(),
            "date":             datetime.now().strftime("%Y-%m-%d"),
        }
        self._jobs[job_id] = job
        tile = JobTile(job, app=self)
        self._job_tiles[job_id] = tile
        self._queue_panel.add_tile(job_id, tile)
        self._save_jobs()
        return job

    def _save_jobs(self):
        tmp = JOBS_PATH.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps({"jobs": list(self._jobs.values())}, indent=2))
            os.replace(tmp, JOBS_PATH)
        except Exception:
            pass

    def _restore_jobs(self):
        if not JOBS_PATH.exists():
            return
        try:
            data = json.loads(JOBS_PATH.read_text())
            jobs = data.get("jobs", [])
        except Exception:
            return

        # Index all upload entries by job_id for fast lookup
        entries_by_job: dict[str, list] = {}
        for entry in self._state.all():
            entries_by_job.setdefault(entry.job_id, []).append(entry)

        for job in jobs:
            job_id = job.get("id")
            if not job_id:
                continue
            # Re-register job and build tile
            self._jobs[job_id] = job
            tile = JobTile(job, app=self)
            self._job_tiles[job_id] = tile
            self._queue_panel.add_tile(job_id, tile)

            # Restore upload entries for this job
            for entry in entries_by_job.get(job_id, []):
                if entry.status in ("in_progress", "compressing"):
                    self._state.update(entry.id, status="queued")
                    entry.status = "queued"
                tile.add_file(entry)

            # Restart watch watcher
            if job.get("source") == "watch":
                self._start_job_watcher(job)

        # Kick off any queued uploads
        self._start_next_uploads()

    def _add_files_for_job(self, paths: list, job: dict):
        folder_id   = job.get("drive_folder_id", "")
        folder_name = job.get("drive_folder_name", "")
        job_id      = job.get("id", "")
        tile        = self._job_tiles.get(job_id)
        added = 0
        for path in paths:
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            entry = UploadEntry.new(path, size, folder_id, folder_name)
            entry.job_id = job_id
            if self._state.add(entry):
                if tile:
                    tile.add_file(entry)
                self._log(f"Queued: {Path(path).name}  ({_fmt_size(size)})")
                added += 1
        if added:
            self._start_next_uploads()

    def _add_files_as_zip(self, files: list, job: dict):
        job_id      = job["id"]
        folder_id   = job.get("drive_folder_id", "")
        folder_name = job.get("drive_folder_name", "")
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        custom   = job.get("zip_name", "").strip()
        zip_name = (custom if custom.lower().endswith(".zip") else custom + ".zip") if custom else f"upload_{ts}.zip"
        entry = UploadEntry.new(local_path="", file_size=0,
                                folder_id=folder_id, folder_name=folder_name,
                                status="compressing")
        entry.file_name = zip_name
        entry.job_id    = job_id
        self._state.add(entry)
        tile = self._job_tiles.get(job_id)
        if tile:
            tile.add_file(entry)
        stop_event = threading.Event()
        worker = ListZipWorker(files, zip_name, entry.id, self._state,
                               self._progress_queue, stop_event=stop_event,
                               keep_zip=job.get("keep_zip", False))
        t = threading.Thread(target=worker.run, daemon=True)
        self._active_zip_workers[entry.id] = (t, stop_event)
        t.start()

    def _add_folder_for_job(self, folder_path: str, job: dict):
        folder_name = Path(folder_path).name
        from PyQt6.QtWidgets import QMessageBox as _MB
        reply = _MB.question(self, "Upload Folder",
                             f'How to upload "{folder_name}"?',
                             "Compress to ZIP", "Keep Structure", None, 0, 1)
        if reply == 0:
            self._add_folder_as_zip(folder_path, job)
        else:
            self._add_folder_as_structure(folder_path, job)

    def _add_folder_as_zip(self, folder_path: str, job: dict):
        folder_name = Path(folder_path).name
        folder_id   = job.get("drive_folder_id", "")
        folder_dest = job.get("drive_folder_name", "")
        job_id      = job.get("id", "")
        custom      = job.get("zip_name", "").strip()
        zip_name    = (custom if custom.lower().endswith(".zip") else custom + ".zip") if custom else folder_name + ".zip"
        entry = UploadEntry.new(local_path=folder_path, file_size=0,
                                folder_id=folder_id, folder_name=folder_dest,
                                status="compressing")
        entry.file_name = zip_name
        entry.job_id    = job_id
        self._state.add(entry)
        tile = self._job_tiles.get(job_id)
        if tile:
            tile.add_file(entry)
        stop_event = threading.Event()
        worker = ZipWorker(folder_path, entry.id, self._state,
                           self._progress_queue, stop_event=stop_event, zip_name=custom,
                           keep_zip=job.get("keep_zip", False))
        t = threading.Thread(target=worker.run, daemon=True)
        self._active_zip_workers[entry.id] = (t, stop_event)
        t.start()

    def _add_folder_as_structure(self, folder_path: str, job: dict):
        if not self._drive_service:
            self._show_notice("Drive not connected yet. Please wait and try again.")
            return
        threading.Thread(target=self._prepare_folder_structure,
                         args=(folder_path, job), daemon=True).start()

    def _prepare_folder_structure(self, folder_path: str, job: dict):
        try:
            folder_name    = Path(folder_path).name
            parent_id      = job.get("drive_folder_id", "")
            parent_display = job.get("drive_folder_name", "")
            job_id         = job.get("id", "")
            job_account    = job.get("drive_account_id", "")
            if not job_account or not drive_accounts.token_path(job_account).exists():
                self._progress_queue.put(("file_error", job_id,
                                          "No Drive account configured for this job"))
                return
            svc = drive_accounts.build_thread_service(job_account)
            root_drive_id = drivelib.create_drive_folder(svc, folder_name, parent_id)
            root_display  = f"{parent_display} / {folder_name}"
            folder_map = {folder_path: root_drive_id}
            for dirpath, dirnames, _ in os.walk(folder_path):
                parent_local = str(Path(dirpath).parent)
                if dirpath != folder_path and parent_local in folder_map:
                    sub_id = drivelib.create_drive_folder(
                        svc, Path(dirpath).name, folder_map[parent_local])
                    folder_map[dirpath] = sub_id
            entries = []
            for dirpath, _, filenames in os.walk(folder_path):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    try:
                        size = os.path.getsize(fp)
                    except OSError:
                        continue
                    dir_id = folder_map.get(str(Path(fp).parent), root_drive_id)
                    entry = UploadEntry.new(fp, size, dir_id, root_display)
                    entry.job_id = job_id
                    entries.append(entry)

            def _add_to_ui():
                tile = self._job_tiles.get(job_id)
                for entry in entries:
                    self._state.add(entry)
                    if tile:
                        tile.add_file(entry)
                self._start_next_uploads()

            QTimer.singleShot(0, _add_to_ui)
        except Exception as e:
            QTimer.singleShot(0, lambda: self._show_notice(
                f"Failed to create folder structure in Drive:\n{e}"))

    # ── Watch jobs ─────────────────────────────────────────────────────────────

    def _start_job_watcher(self, job: dict):
        job_id = job["id"]
        zip_on = job.get("zip", False)
        job_dict = {
            "id":                      job_id,
            "watch_folder":            job.get("watch_folder", ""),
            "watch_batch_mode":        zip_on,
            "watch_subfolder_zip":     zip_on and bool(job.get("watch_subfolder_zip", False)),
            "watch_batch_stable_secs": job.get("watch_stable_secs", 15),
            "watch_delay_secs":        job.get("watch_delay_secs", 0),
            "watch_extensions":        job.get("watch_extensions", []),
            "watch_recursive":         job.get("watch_recursive", False),
            "watch_ignore_hidden":     job.get("watch_ignore_hidden", True),
        }

        def on_file_ready(path, jid):
            self._watch_file_signal.emit(path, jid)

        def on_batch_ready(files, jid, zip_name=""):
            self._watch_batch_signal.emit(files, jid, zip_name)

        def on_status(msg, color, jid):
            self._watch_status_signal.emit(msg, color, jid)

        watcher = JobWatcher(job_dict, on_file_ready, on_batch_ready, on_status)
        self._watch_watchers[job_id] = watcher
        watcher.start()
        folder_name = job.get("watch_folder", "")
        self._log(f"Watch started: {Path(folder_name).name if folder_name else job.get('name','?')}  →  {job.get('drive_folder_name','')}")

    def _stop_job_watcher(self, job_id: str):
        watcher = self._watch_watchers.pop(job_id, None)
        if watcher:
            watcher.stop()

    def _on_watch_file_ready_main(self, path: str, job_id: str):
        job = self._jobs.get(job_id)
        if not job:
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        entry = UploadEntry.new(path, size,
                                job["drive_folder_id"],
                                job["drive_folder_name"])
        entry.job_id = job_id
        if self._state.add(entry):
            self._log(f"Watch detected: {Path(path).name}  ({_fmt_size(size)})")
            tile = self._job_tiles.get(job_id)
            if tile:
                tile.add_file(entry)
            self._start_next_uploads()

    def _on_watch_status_main(self, msg: str, color: str, jid: str):
        tile = self._job_tiles.get(jid)
        if tile:
            tile.set_watch_status(msg, color)

    def _on_watch_batch_ready_main(self, files: list, job_id: str, zip_name_hint: str = ""):
        job = self._jobs.get(job_id)
        if not job:
            return
        # Mark these files done and restart the monitor immediately for next batch
        watcher = self._watch_watchers.get(job_id)
        if watcher:
            watcher.add_batched_paths(files)
        folder_id   = job.get("drive_folder_id", "")
        folder_name = job.get("drive_folder_name", "")
        watch_folder = job.get("watch_folder", "")
        folder_display = Path(watch_folder).name if watch_folder else "batch"
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        # zip_name_hint is the subfolder name when in subfolder-zip mode; overrides zip_name field
        custom   = zip_name_hint.strip() or job.get("zip_name", "").strip()
        zip_name = (custom if custom.lower().endswith(".zip") else custom + ".zip") if custom else f"{folder_display}_{ts}.zip"
        entry = UploadEntry.new(local_path="", file_size=0,
                                folder_id=folder_id, folder_name=folder_name,
                                status="compressing")
        entry.file_name = zip_name
        entry.job_id    = job_id
        self._state.add(entry)
        tile = self._job_tiles.get(job_id)
        if tile:
            tile.add_file(entry)
            tile.set_watch_status(f"●  Compressing {zip_name}…", "yellow")
        stop_event = threading.Event()
        out_dir = str(Path(watch_folder).parent) if watch_folder and Path(watch_folder).parent.exists() else None
        worker = ListZipWorker(files, zip_name, entry.id, self._state,
                               self._progress_queue, stop_event=stop_event,
                               keep_zip=job.get("keep_zip", False),
                               output_dir=out_dir)
        t = threading.Thread(target=worker.run, daemon=True)
        self._active_zip_workers[entry.id] = (t, stop_event)
        t.start()

    # ── Drive ──────────────────────────────────────────────────────────────────

    def _on_scheme_changed(self, scheme) -> None:
        global _IS_DARK
        _IS_DARK = (scheme == Qt.ColorScheme.Dark)
        QApplication.instance().setStyleSheet(_build_app_qss())
        self._creation_panel.refresh_theme()
        self._queue_panel.refresh_theme()
        for tile in self._job_tiles.values():
            tile.refresh_theme()

    def _init_drive(self):
        accounts   = drive_accounts.list_accounts()
        account_id = self._cfg.get("active_drive_account_id", "")
        if account_id and drive_accounts.token_path(account_id).exists():
            active_id = account_id
        else:
            active_id = next(
                (a["id"] for a in accounts
                 if drive_accounts.token_path(a["id"]).exists()), None)

        if not active_id:
            self._creation_panel.update_status(
                "No account — click Accounts to connect", STONE, MIST)
            return

        self._drive_service = True
        n = len(accounts)
        self._creation_panel.update_status(
            f"{n} account{'s' if n != 1 else ''} · ready", TEAL_DEEP, TEAL)
        self._creation_panel.preload_folders(active_id)
        self._start_next_uploads()

    def _manage_accounts(self):
        dlg = DriveAccountsDialog(self, self._cfg)
        dlg.exec()
        if dlg.account_changed:
            config.save(self._cfg)
            self._creation_panel.refresh_accounts()
            self._init_drive()

    def _open_settings(self):
        dlg = SettingsDialog(self, parent=self)
        dlg.exec()

    def _manage_email_templates(self):
        active_id = self._cfg.get("active_drive_account_id", "")
        accounts  = drive_accounts.list_accounts()
        acct_name = next(
            (a.get("name", "") for a in accounts if a["id"] == active_id), "")
        dlg = EmailTemplateDialog(self, self._cfg,
                                  account_id=active_id, account_name=acct_name)
        dlg.exec()

    def _edit_watch_job(self, job_id: str):
        job = self._jobs.get(job_id)
        if not job:
            return
        dlg = WatchEditDialog(job, app=self, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        params = dlg.result_params()
        job.update(params)
        # Update job name to reflect new watch folder
        job["name"] = f"Watch · {Path(job['watch_folder']).name}"
        self._save_jobs()
        # Restart watcher with new params
        watcher = self._watch_watchers.pop(job_id, None)
        if watcher:
            watcher.stop()
        self._start_job_watcher(job)
        tile = self._job_tiles.get(job_id)
        if tile:
            tile._name_lbl.setText(job["name"])
            tile.set_watch_resumed()
            tile.set_watch_status("Watching…", TEAL)

    def _stop_watch_job(self, job_id: str):
        watcher = self._watch_watchers.pop(job_id, None)
        if watcher:
            watcher.stop()
        tile = self._job_tiles.get(job_id)
        if tile:
            tile.set_watch_stopped()
            tile.set_watch_status("Watching stopped", STONE)

    def _resume_watch_job(self, job_id: str):
        if job_id in self._watch_watchers:
            return  # already running
        job = self._jobs.get(job_id)
        if not job:
            return
        self._start_job_watcher(job)
        tile = self._job_tiles.get(job_id)
        if tile:
            tile.set_watch_resumed()
            tile.set_watch_status("Watching…", TEAL)

    def _remove_job(self, job_id: str):
        watcher = self._watch_watchers.pop(job_id, None)
        if watcher:
            watcher.stop()
        for entry in self._state.all():
            if getattr(entry, "job_id", None) != job_id:
                continue
            t_zip = self._active_zip_workers.pop(entry.id, None)
            if t_zip:
                t_zip[1].set()
            t_up = self._active_workers.pop(entry.id, None)
            if t_up:
                t_up[1].set()
            self._upload_account.pop(entry.id, None)
        self._queue_panel.remove_tile(job_id)
        self._job_tiles.pop(job_id, None)
        self._jobs.pop(job_id, None)
        self._save_jobs()

    def _edit_job_email(self, job_id: str):
        job = self._jobs.get(job_id)
        if not job:
            return
        dlg = EmailDraftDialog(self, job.get("email_cfg"), cfg=self._cfg)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_draft:
            job["email_cfg"] = dlg.result_draft
            tile = self._job_tiles.get(job_id)
            if tile:
                tile._refresh_email_btn_style(job["email_cfg"])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_size_grip"):
            sz = self._size_grip.sizeHint()
            self._size_grip.setGeometry(
                self.width() - sz.width(),
                self.height() - sz.height(),
                sz.width(), sz.height())

    # ── Upload workers ─────────────────────────────────────────────────────────

    def _start_next_uploads(self):
        if not self._drive_service:
            return
        available = MAX_CONCURRENT - len(self._active_workers)
        if available <= 0:
            return
        queued = self._state.get_queued()
        for entry in queued[:available]:
            if entry.id in self._active_workers:
                continue
            if entry.job_id in self._jobs:
                account_id = self._jobs[entry.job_id].get("drive_account_id", "")
            else:
                account_id = self._cfg.get("active_drive_account_id", "")
            if not account_id:
                account_id = self._cfg.get("active_drive_account_id", "")

            stop_event = threading.Event()
            worker = UploadWorker(entry, self._state, self._progress_queue,
                                  stop_event, account_id=account_id)
            t = threading.Thread(target=worker.run, daemon=True)
            self._active_workers[entry.id]    = (t, stop_event)
            self._upload_account[entry.id]    = account_id
            tile = self._job_tiles.get(entry.job_id)
            if tile:
                row = tile.get_file_row(entry.id)
                if row:
                    row.set_uploading()
            t.start()

    def _cancel_upload(self, entry_id: str):
        if entry_id in self._active_zip_workers:
            _, stop_event = self._active_zip_workers[entry_id]
            stop_event.set()
            self._call_file_row(entry_id, "set_zip_cancelling")
        elif entry_id in self._active_workers:
            _, stop_event = self._active_workers[entry_id]
            stop_event.set()
            self._call_file_row(entry_id, "set_pausing")
        else:
            self._state.update(entry_id, status="failed", error="Cancelled by user")
            self._call_file_row(entry_id, "set_failed", "Cancelled by user")

    def _pause_upload(self, entry_id: str):
        if entry_id in self._active_zip_workers:
            _, stop_event = self._active_zip_workers[entry_id]
            stop_event.set()
            self._call_file_row(entry_id, "set_zip_cancelling")
        elif entry_id in self._active_workers:
            _, stop_event = self._active_workers[entry_id]
            stop_event.set()
            self._call_file_row(entry_id, "set_pausing")
        else:
            self._state.update(entry_id, status="paused")
            self._call_file_row(entry_id, "set_paused")

    def _resume_upload(self, entry_id: str):
        if entry_id in self._active_workers:
            return
        self._state.update(entry_id, status="queued")
        self._call_file_row(entry_id, "set_queued")
        self._start_next_uploads()

    def _call_file_row(self, entry_id: str, method: str, *args):
        entry = self._state.get(entry_id)
        if not entry:
            return
        tile = self._job_tiles.get(entry.job_id)
        if not tile:
            return
        row = tile.get_file_row(entry_id)
        if row and hasattr(row, method):
            getattr(row, method)(*args)
        if method in ("set_done", "set_failed", "set_uploading",
                      "set_paused", "set_queued", "set_zip_progress"):
            tile.update_counts()

    # ── Progress polling ───────────────────────────────────────────────────────

    def _poll_progress(self):
        try:
            while True:
                msg      = self._progress_queue.get_nowait()
                kind     = msg[0]
                entry_id = msg[1]

                if kind == "progress":
                    self._call_file_row(entry_id, "update_progress", msg[2])
                elif kind == "confirmed":
                    self._call_file_row(entry_id, "confirm_progress", msg[2])
                elif kind == "retrying":
                    self._call_file_row(entry_id, "set_retrying", msg[2])
                elif kind == "status":
                    self._call_file_row(entry_id, "set_status", msg[2])
                elif kind == "done":
                    self._on_upload_done(entry_id, msg[2])
                elif kind == "error":
                    self._on_upload_error(entry_id, msg[2])
                elif kind == "cancelled":
                    self._active_workers.pop(entry_id, None)
                    self._call_file_row(entry_id, "set_paused")
                    self._start_next_uploads()
                elif kind == "zip_progress":
                    self._call_file_row(entry_id, "set_zip_progress", msg[2], msg[3])
                elif kind == "zip_done":
                    self._active_zip_workers.pop(entry_id, None)
                    _, zip_size, zip_name = msg[2], msg[3], msg[4]
                    self._call_file_row(entry_id, "set_upload_ready", zip_name, zip_size)
                    self._log(f"Zip complete: {zip_name}  ({_fmt_size(zip_size)})")
                    entry = self._state.get(entry_id)
                    if entry and entry.job_id:
                        job = self._jobs.get(entry.job_id)
                        if job and job.get("source") == "watch":
                            tile = self._job_tiles.get(entry.job_id)
                            if tile:
                                tile.set_watch_status(f"●  Uploading {zip_name}…", "teal")
                    self._start_next_uploads()
                elif kind == "zip_cancelled":
                    self._active_zip_workers.pop(entry_id, None)
                    self._call_file_row(entry_id, "set_failed", "Cancelled")
                elif kind == "email_sent":
                    tile = self._job_tiles.get(msg[1])
                    if tile:
                        tile.set_email_status("sent")
                elif kind == "email_failed":
                    tile = self._job_tiles.get(msg[1])
                    if tile:
                        tile.set_email_status("failed")
        except queue.Empty:
            pass
        n_uploading = len(self._active_workers)
        self._queue_panel.update_active_count(n_uploading)

    def _on_upload_done(self, entry_id: str, drive_file_id: str):
        self._active_workers.pop(entry_id, None)
        upload_account = self._upload_account.pop(entry_id, None)
        web_link = (f"https://drive.google.com/file/d/{drive_file_id}/view"
                    if drive_file_id else "")
        entry = self._state.get(entry_id)
        self._call_file_row(entry_id, "set_done", web_link)
        if entry and entry.local_path:
            try:
                zip_path = Path(entry.local_path)
                tmp_dir = zip_path.parent
                if entry.is_temp_zip:
                    if "uplift-" in str(tmp_dir):
                        shutil.rmtree(str(tmp_dir), ignore_errors=True)
                else:
                    # keep_zip=True — move zip out of uplift- subdir into its parent
                    # (for watch jobs the subdir is already in watch_folder/.., so zip lands there)
                    keep_dir = tmp_dir.parent
                    dest = keep_dir / zip_path.name
                    if dest.exists():
                        stem, suffix = zip_path.stem, zip_path.suffix
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        dest = keep_dir / f"{stem}_{ts}{suffix}"
                    shutil.move(str(zip_path), str(dest))
                    shutil.rmtree(str(tmp_dir), ignore_errors=True)
                    self._log(f"Zip saved: {dest}")
            except Exception:
                pass
        if entry:
            self._log(f"Upload complete: {entry.file_name}")
        if entry and entry.job_id:
            job = self._jobs.get(entry.job_id)
            if job and job.get("source") == "watch":
                watch_folder = job.get("watch_folder", "")
                folder_name = Path(watch_folder).name if watch_folder else "folder"
                tile = self._job_tiles.get(entry.job_id)
                if tile:
                    tile.set_watch_status(f"●  Watching  •  {folder_name}", "green")
        if drive_file_id and entry and entry.job_id:
            job = self._jobs.get(entry.job_id)
            if job and job.get("email_cfg"):
                email_cfg = job["email_cfg"]
                if not email_cfg.get("held"):
                    threading.Thread(
                        target=self._send_job_email_background,
                        args=(entry.job_id, entry, drive_file_id, upload_account),
                        daemon=True).start()
        self._start_next_uploads()

    def _on_upload_error(self, entry_id: str, error_msg: str):
        self._active_workers.pop(entry_id, None)
        self._upload_account.pop(entry_id, None)
        entry = self._state.get(entry_id)
        self._call_file_row(entry_id, "set_failed", error_msg)
        self._log(f"Upload error: {entry.file_name if entry else entry_id} — {error_msg}")
        self._start_next_uploads()

    # ── Email ──────────────────────────────────────────────────────────────────

    def _send_job_email_background(self, job_id: str, entry, drive_file_id: str,
                                   upload_account_id: str | None):
        job = self._jobs.get(job_id)
        if not job or not job.get("email_cfg"):
            return
        email_cfg  = job["email_cfg"]
        account_id = (upload_account_id
                      or job.get("drive_account_id", "")
                      or self._cfg.get("active_drive_account_id", ""))
        to = email_cfg.get("to", "").strip()
        if not to:
            return
        try:
            svc = drive_accounts.build_thread_service(account_id)
            svc.permissions().create(
                fileId=drive_file_id,
                body={"role": "reader", "type": "anyone"},
                fields="id", supportsAllDrives=True).execute()
            result = svc.files().get(fileId=drive_file_id,
                                     fields="webViewLink",
                                     supportsAllDrives=True).execute()
            link = result.get("webViewLink", "")
        except Exception as e:
            self._progress_queue.put(("email_failed", job_id, str(e)))
            return
        prof = sender_profile.load(account_id)
        if not prof or not prof.get("gmail_app_password"):
            self._progress_queue.put(("email_failed", job_id, "Sender not configured"))
            return
        safe = defaultdict(lambda: "?", {
            "filename":    entry.file_name,
            "link":        link,
            "date":        _date.today().strftime("%B %d, %Y"),
            "sender_name": prof.get("sender_name", ""),
        })
        subject = email_cfg.get("subject", EmailTemplateDialog.DEFAULT_SUBJECT
                                ).format_map(safe)
        body    = email_cfg.get("body", EmailTemplateDialog.DEFAULT_BODY
                                ).replace("{link}", link).format_map(safe)
        try:
            mailer.send(
                sender_email=prof["sender_email"],
                app_password=prof["gmail_app_password"],
                recipient=to, subject=subject, body=body,
                cc=email_cfg.get("cc", ""),
                bcc=email_cfg.get("bcc", ""),
            )
            self._log(f"Email sent: {entry.file_name} → {to}")
            self._progress_queue.put(("email_sent", job_id))
        except Exception as e:
            self._log(f"Email failed: {entry.file_name} — {e}")
            self._progress_queue.put(("email_failed", job_id, str(e)))

    # ── Clear completed ────────────────────────────────────────────────────────

    def _clear_completed(self):
        self._state.clear_completed()
        to_remove = []
        for job_id, tile in list(self._job_tiles.items()):
            tile.remove_completed_rows()
            if not tile._rows:
                to_remove.append(job_id)
        self._queue_panel.remove_tiles(to_remove)
        for jid in to_remove:
            self._job_tiles.pop(jid, None)
            self._jobs.pop(jid, None)

    # ── Startup state ──────────────────────────────────────────────────────────

    def _handle_startup_state(self):
        expired = self._state.expire_old_sessions()
        if expired:
            names  = ", ".join(expired[:3])
            suffix = f" (+{len(expired)-3} more)" if len(expired) > 3 else ""
            self._show_notice(f"Session expired for: {names}{suffix}\n"
                              "These will restart from the beginning.")
        self._restore_jobs()
        # Handle any orphaned entries (no matching saved job)
        orphaned = [e for e in self._state.get_pending()
                    if not e.job_id or e.job_id not in self._jobs]
        if orphaned:
            self._show_resume_dialog(orphaned)

    def _show_resume_dialog(self, pending: list):
        n   = len(pending)
        dlg = QDialog(self)
        dlg.setWindowTitle("Resume Uploads")
        dlg.setFixedSize(360, 180)
        dlg.setModal(True)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 24, 20, 16)
        lay.setSpacing(8)

        s = "s" if n != 1 else ""
        lbl = QLabel(f"Resume {n} incomplete upload{s}?")
        lbl.setFont(F_SEMIBOLD(14))
        lay.addWidget(lbl)

        sub = QLabel("Uploads will continue from where they left off.")
        sub.setFont(F_BODY(12))
        sub.setStyleSheet(f"color: {_th_stone()};")
        lay.addWidget(sub)

        btn_row = QWidget()
        b_lay   = QHBoxLayout(btn_row)
        b_lay.setContentsMargins(0, 0, 0, 0)
        b_lay.setSpacing(8)

        def do_resume():
            dlg.accept()
            for entry in pending:
                if entry.status in ("in_progress", "paused"):
                    self._state.update(entry.id, status="queued")
            if pending:
                job = self._create_job(
                    name=f"Resumed · {datetime.now().strftime('%Y-%m-%d')}",
                    source="manual",
                    drive_account_id=self._cfg.get("active_drive_account_id", ""),
                    drive_folder_id=pending[0].folder_id,
                    drive_folder_name=pending[0].folder_name,
                )
                tile = self._job_tiles.get(job["id"])
                for entry in pending:
                    if tile:
                        tile.add_file(entry)
                    if not entry.job_id or entry.job_id not in self._jobs:
                        entry.job_id = job["id"]

        def do_clear():
            self._state.clear_all_pending()
            dlg.reject()

        resume_btn = QPushButton("Resume")
        resume_btn.setObjectName("primary")
        resume_btn.setFixedHeight(36)
        resume_btn.clicked.connect(do_resume)
        b_lay.addWidget(resume_btn, 1)

        clear_btn = QPushButton("Clear All")
        clear_btn.setObjectName("ghost")
        clear_btn.setStyleSheet(f"color: {RED}; border-color: {RED};")
        clear_btn.setFixedHeight(36)
        clear_btn.clicked.connect(do_clear)
        b_lay.addWidget(clear_btn, 1)

        lay.addWidget(btn_row)
        dlg.exec()

    # ── Misc ───────────────────────────────────────────────────────────────────

    def _show_notice(self, msg: str):
        QMessageBox.information(self, "Notice", msg)

    # ── Menu bar tray ──────────────────────────────────────────────────────────

    def _bring_to_front(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _clear_log(self):
        if QMessageBox.question(
            self, "Clear Log", "Delete all activity log entries?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) == QMessageBox.StandardButton.Yes:
            try:
                LOG_PATH.write_text("", encoding="utf-8")
            except OSError:
                pass

    def _show_log_viewer(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Uplift — Activity Log")
        dlg.resize(720, 480)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        title = QLabel("Activity Log")
        title.setStyleSheet(f"font-size:14px;font-weight:bold;color:{_th_ink()};")
        hdr.addWidget(title)
        hdr.addStretch()
        path_lbl = QLabel(str(LOG_PATH))
        path_lbl.setStyleSheet(f"font-size:10px;color:{_th_stone()};")
        hdr.addWidget(path_lbl)
        lay.addLayout(hdr)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("Menlo", 11))
        txt.setStyleSheet(
            f"QTextEdit {{ background:{_th_surface2()}; color:{_th_ink()}; "
            f"border:1px solid {_th_mist()}; border-radius:4px; }}")
        try:
            content = LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else ""
        except OSError:
            content = ""
        txt.setPlainText(content)
        # Scroll to bottom (most recent)
        txt.moveCursor(txt.textCursor().MoveOperation.End)
        lay.addWidget(txt, 1)

        btns = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(lambda: (
            txt.setPlainText(LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else ""),
            txt.moveCursor(txt.textCursor().MoveOperation.End)
        ))
        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(lambda: (
            self._clear_log() or
            txt.setPlainText("")
        ))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        close_btn.setStyleSheet(
            f"QPushButton {{ background:{TEAL}; color:white; border:none; "
            f"border-radius:4px; padding:4px 16px; }}"
            f"QPushButton:hover {{ background:{TEAL_DEEP}; }}")
        btns.addWidget(refresh_btn)
        btns.addWidget(clear_btn)
        btns.addStretch()
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        dlg.exec()

    # ── Persistent log ─────────────────────────────────────────────────────────

    def _log(self, message: str):
        ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}]  {message}\n"
        try:
            with LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    def closeEvent(self, event):
        active = len(self._active_workers) + len(self._active_zip_workers)
        if active:
            msg = QMessageBox(self)
            msg.setWindowTitle("Quit Uplift?")
            msg.setText(
                f"{active} upload{'s' if active != 1 else ''} in progress.\n"
                "Quitting now will cancel them.")
            msg.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            msg.setDefaultButton(QMessageBox.StandardButton.Cancel)
            msg.button(QMessageBox.StandardButton.Yes).setText("Quit Anyway")
            if msg.exec() != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._save_jobs()
        for job_id in list(self._watch_watchers):
            self._stop_job_watcher(job_id)
        for _, (thread, stop_event) in list(self._active_workers.items()):
            stop_event.set()
        for _, (thread, stop_event) in list(self._active_zip_workers.items()):
            stop_event.set()
        event.accept()


# ── macOS menu bar name fix ────────────────────────────────────────────────────

def _fix_macos_app_name(name: str) -> None:
    try:
        import ctypes, ctypes.util
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype    = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p

        def msg(obj, sel, *args):
            send.argtypes = ([ctypes.c_void_p, ctypes.c_void_p]
                             + [type(a) for a in args])
            return send(obj, objc.sel_registerName(sel.encode()), *args)

        ns_str = msg(objc.objc_getClass(b"NSString"),
                     "stringWithUTF8String:",
                     ctypes.c_char_p(name.encode()))
        proc   = msg(objc.objc_getClass(b"NSProcessInfo"), "processInfo")
        msg(proc, "setProcessName:", ctypes.c_void_p(ns_str))
    except Exception:
        pass


def _force_light_palette(app: QApplication) -> None:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(BG))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(INK))
    p.setColor(QPalette.ColorRole.Base,            QColor(SURFACE))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(SURFACE2))
    p.setColor(QPalette.ColorRole.Text,            QColor(INK))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(STONE))
    p.setColor(QPalette.ColorRole.Button,          QColor(SURFACE2))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(INK))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(TEAL_WASH))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(INK))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(INK))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(SURFACE))
    p.setColor(QPalette.ColorRole.Mid,             QColor(MIST))
    p.setColor(QPalette.ColorRole.Midlight,        QColor(SURFACE2))
    p.setColor(QPalette.ColorRole.Dark,            QColor(STONE))
    p.setColor(QPalette.ColorRole.Shadow,          QColor(GRAPHITE))
    p.setColor(QPalette.ColorRole.Light,           QColor(SURFACE))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(SURFACE))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(STONE))
    app.setPalette(p)


if __name__ == "__main__":
    _fix_macos_app_name("Uplift")
    import sys
    app = QApplication(sys.argv)
    app.setApplicationName("Uplift")
    app.setApplicationDisplayName("Uplift")
    app.setQuitOnLastWindowClosed(False)  # keep tray alive when window closed
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyRegular
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyRegular)
    except Exception:
        pass
    window = App()

    try:
        import pyqt_liquidglass as lg
        window.prepare_glass_panels()
        lg.prepare_window_for_glass(window)
        QTimer.singleShot(100, window.apply_glass_panels)
        lg.setup_traffic_lights_inset(window, x_offset=20, y_offset=10)
    except Exception:
        window.show()

    sys.exit(app.exec())
