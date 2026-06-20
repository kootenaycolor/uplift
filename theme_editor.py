#!/usr/bin/env python3
"""
Uplift Theme Editor
====================
Every colour, glass level and spacing value — with labels showing
exactly which part of the app each setting controls.

Run with:   python3 theme_editor.py
"""
from __future__ import annotations
import sys, json, subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QFrame, QScrollArea, QColorDialog,
    QSplitter, QMessageBox, QTabWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QLinearGradient, QBrush, QPen

THEME_PATH = Path.home() / ".uplift-theme.json"
UPLIFT_APP = Path("/Applications/Uplift.app")

# ── Defaults (must mirror main_qt.py constants exactly) ───────────────────────
DEFAULTS: dict = {
    # ── Light mode
    "BG":       "#F1F0F0",
    "SURFACE":  "#FFFFFF",
    "SURFACE2": "#F7F7F7",
    "INK":      "#000000",
    "GRAPHITE": "#404040",
    "STONE":    "#6B6B6B",
    "MIST":     "#D9D9D9",
    # ── Dark mode
    "BG_DARK":       "#1C1C1E",
    "SURFACE_DARK":  "#2C2C2E",
    "SURFACE2_DARK": "#3A3A3C",
    "INK_DARK":      "#FFFFFF",
    "GRAPHITE_DARK": "#C0C0C0",
    "STONE_DARK":    "#98989D",
    "MIST_DARK":     "#48484A",
    # ── Brand / accent palette
    "TEAL":      "#0089a6",
    "TEAL_DEEP": "#04657e",
    "TEAL_MID":  "#3d9eb6",
    "TEAL_SOFT": "#77b2c6",
    "TEAL_PALE": "#cfe1e7",
    "TEAL_WASH": "#eaf2f5",
    # ── Status
    "GREEN":  "#197a26",
    "RED":    "#cc2222",
    "YELLOW": "#9b6e00",
    # ── Toggle switch (independent of shared palette)
    "TOGGLE_ON":       "#3d9eb6",
    "TOGGLE_OFF":      "#D9D9D9",
    "TOGGLE_OFF_DARK": "#48484A",
    "TOGGLE_KNOB":     "#FFFFFF",
    # ── Glass
    "GLASS_ALPHA_LIGHT": 0.60,
    "GLASS_ALPHA_DARK":  0.60,
    # ── Spacing (pixels)
    "SP_PANEL_H":  20,
    "SP_PANEL_V":  10,
    "SP_QUEUE_H":  20,
    "SP_TILE_GAP": 10,
    "SP_ROW_GAP":  4,
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _blend(fg: str, alpha: float, bg: str) -> str:
    f, b = QColor(fg), QColor(bg)
    r = int(f.red()   * alpha + b.red()   * (1 - alpha))
    g = int(f.green() * alpha + b.green() * (1 - alpha))
    bl = int(f.blue()  * alpha + b.blue()  * (1 - alpha))
    return QColor(r, g, bl).name()

def _contrast(c: str) -> str:
    q = QColor(c)
    lum = 0.2126 * q.redF() + 0.7152 * q.greenF() + 0.0722 * q.blueF()
    return "#000000" if lum > 0.45 else "#FFFFFF"

def _slider_qss() -> str:
    return ("QSlider::groove:horizontal{height:4px;background:#D0D0D0;border-radius:2px}"
            "QSlider::handle:horizontal{background:#0089a6;border:none;"
            "width:14px;height:14px;margin:-5px 0;border-radius:7px}"
            "QSlider::sub-page:horizontal{background:#0089a6;border-radius:2px}")


# ══════════════════════════════════════════════════════════════════════════════
# Primitive widgets
# ══════════════════════════════════════════════════════════════════════════════

class ColorSwatch(QPushButton):
    changed = pyqtSignal(str)
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._color = color; self._repaint()
        self.clicked.connect(self._pick)
    def set_color(self, c: str): self._color = c; self._repaint()
    def color(self) -> str: return self._color
    def _repaint(self):
        tc = _contrast(self._color)
        self.setStyleSheet(
            f"QPushButton{{background:{self._color};border:1px solid #999;"
            f"border-radius:4px;color:{tc}}}"
            f"QPushButton:hover{{border:2px solid #0089a6}}")
    def _pick(self):
        d = QColorDialog(QColor(self._color), self)
        d.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        if d.exec():
            self._color = d.currentColor().name(); self._repaint()
            self.changed.emit(self._color)


class SectionHeader(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet("color:#333;font-size:10px;font-weight:bold;"
                           "letter-spacing:2px;padding-top:16px;padding-bottom:4px;")


class SubHeader(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setStyleSheet("color:#888;font-size:10px;padding-bottom:2px;")
        self.setWordWrap(True)


class HDivider(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(1)
        self.setStyleSheet("background:#EBEBEB;margin-top:10px;margin-bottom:2px;")


# ── Color row ─────────────────────────────────────────────────────────────────

class ColorRow(QWidget):
    changed = pyqtSignal()
    def __init__(self, label: str, sublabel: str, key: str, values: dict, parent=None):
        super().__init__(parent)
        self._key = key
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 2, 0, 2); lay.setSpacing(8)
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0,0,0,0); ll.setSpacing(0)
        lbl = QLabel(label); lbl.setStyleSheet("color:#111;font-size:12px;")
        ll.addWidget(lbl)
        if sublabel:
            sl = QLabel(sublabel); sl.setStyleSheet("color:#aaa;font-size:9px;")
            ll.addWidget(sl)
        left.setMinimumWidth(220)
        lay.addWidget(left, 1)
        self._swatch = ColorSwatch(values.get(key, "#FFFFFF"))
        self._swatch.changed.connect(lambda c: (values.__setitem__(key, c), self.changed.emit()))
        lay.addWidget(self._swatch)

    def refresh(self, values: dict):
        self._swatch.set_color(values.get(self._key, "#FFFFFF"))


# ── Slider row ────────────────────────────────────────────────────────────────

class SliderRow(QWidget):
    changed = pyqtSignal()
    def __init__(self, label: str, sublabel: str, key: str, values: dict,
                 lo: int, hi: int, scale: float = 1.0, suffix: str = "", parent=None):
        super().__init__(parent)
        self._key = key; self._values = values; self._scale = scale; self._suffix = suffix
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 3, 0, 3); lay.setSpacing(8)
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0,0,0,0); ll.setSpacing(0)
        lbl = QLabel(label); lbl.setStyleSheet("color:#111;font-size:12px;")
        ll.addWidget(lbl)
        if sublabel:
            sl = QLabel(sublabel); sl.setStyleSheet("color:#aaa;font-size:9px;")
            ll.addWidget(sl)
        left.setMinimumWidth(220)
        lay.addWidget(left)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(lo, hi)
        self._slider.setValue(int(round(float(values.get(key, lo)) / scale)))
        self._slider.setFixedWidth(120)
        self._slider.setStyleSheet(_slider_qss())
        lay.addWidget(self._slider)
        self._val = QLabel(self._fmt())
        self._val.setFixedWidth(44)
        self._val.setStyleSheet("color:#555;font-size:11px;")
        lay.addWidget(self._val)
        self._slider.valueChanged.connect(self._on)

    def _fmt(self) -> str:
        v = self._slider.value() * self._scale
        return f"{int(v * 100)}%" if self._scale < 1 else f"{int(v)}{self._suffix}"

    def _on(self, v: int):
        self._val.setText(self._fmt())
        self._values[self._key] = v * self._scale
        self.changed.emit()

    def refresh(self, values: dict):
        self._slider.blockSignals(True)
        self._slider.setValue(int(round(float(values.get(self._key, self._slider.minimum())) / self._scale)))
        self._slider.blockSignals(False)
        self._val.setText(self._fmt())


# ══════════════════════════════════════════════════════════════════════════════
# Preview canvas
# ══════════════════════════════════════════════════════════════════════════════

class PreviewCanvas(QWidget):
    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        self._v = values; self._dark = False
        self.setMinimumHeight(420)

    def set_dark(self, d: bool): self._dark = d; self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        v = self._v; dark = self._dark

        bg      = v["BG_DARK"]      if dark else v["BG"]
        surface = v["SURFACE_DARK"] if dark else v["SURFACE"]
        s2      = v["SURFACE2_DARK"]if dark else v["SURFACE2"]
        ink     = v["INK_DARK"]     if dark else v["INK"]
        stone   = v["STONE_DARK"]   if dark else v["STONE"]
        mist    = v["MIST_DARK"]    if dark else v["MIST"]
        teal    = v["TEAL"]; td = v["TEAL_DEEP"]; tm = v["TEAL_MID"]
        tp = v["TEAL_PALE"]; tw = v["TEAL_WASH"]
        green = v["GREEN"]; red = v["RED"]; yellow = v["YELLOW"]
        tog_on  = v["TOGGLE_ON"]
        tog_off = v["TOGGLE_OFF_DARK"] if dark else v["TOGGLE_OFF"]
        tog_knob = v["TOGGLE_KNOB"]
        al    = v["GLASS_ALPHA_DARK"] if dark else v["GLASS_ALPHA_LIGHT"]
        pad   = int(v.get("SP_PANEL_H", 20))
        tgap  = int(v.get("SP_TILE_GAP", 10))

        dt = "#1a4a6b" if dark else "#b5d4e8"
        db = "#0d2a40" if dark else "#7aaec8"
        gr = QLinearGradient(0, 0, 0, self.height())
        gr.setColorAt(0, QColor(dt)); gr.setColorAt(1, QColor(db))
        p.fillRect(self.rect(), QBrush(gr))

        W, H = self.width(), self.height()
        m = 12; pw = W - m * 2

        # ── Top panel ─────────────────────────────────────────────────────────
        top_h = 170; top_y = m + 18
        pb = _blend(surface, al, dt)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(pb))
        p.drawRoundedRect(m, top_y, pw, top_h, 8, 8)

        # titlebar
        tb_h = 22; tbb = _blend(bg, al * 0.7, dt)
        p.setBrush(QColor(tbb)); p.drawRoundedRect(m, top_y, pw, tb_h, 8, 8)
        p.fillRect(m, top_y + 11, pw, 11, QColor(tbb))
        for col, ox in [("#ff5f57",13),("#febc2e",25),("#28c840",37)]:
            p.setBrush(QColor(col)); p.drawEllipse(m+ox, top_y+6, 10, 10)

        # tab row
        ty = top_y + tb_h + 6
        # active tab
        p.setBrush(QColor(teal)); p.drawRoundedRect(m+pad, ty, 82, 22, 4, 4)
        self._txt(p, m+pad+5, ty+15, "Upload Files", "#fff", 9, bold=True)
        # inactive tab
        p.setPen(QPen(QColor(tp), 1)); p.setBrush(QColor(s2))
        p.drawRoundedRect(m+pad+86, ty, 82, 22, 4, 4)
        self._txt(p, m+pad+91, ty+15, "Watch Folder", ink, 9)

        # status dot + text + divider + links (top right)
        dot_x = W - m - 140
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(green))
        p.drawEllipse(dot_x, ty+7, 8, 8)
        self._txt(p, dot_x+11, ty+15, "1 account · ready", stone, 8)
        p.setPen(QPen(QColor(mist), 1))
        p.drawLine(W-m-80, ty+2, W-m-80, ty+20)
        self._txt(p, W-m-76, ty+15, "Settings", td, 8)
        self._txt(p, W-m-34, ty+15, "Accounts", td, 8)

        # drive field
        fy = ty + 30
        p.setPen(QPen(QColor(tp), 1)); p.setBrush(QColor(surface))
        p.drawRoundedRect(m+pad, fy, pw-pad*2, 20, 3, 3)
        self._txt(p, m+pad+7, fy+14, "📁  Pick Drive folder…", stone, 9)

        # acct field
        ay = fy+25
        p.setPen(QPen(QColor(tp), 1)); p.setBrush(QColor(surface))
        p.drawRoundedRect(m+pad, ay, pw-pad*2, 20, 3, 3)
        self._txt(p, m+pad+7, ay+14, "Kootenay Color", ink, 9)

        # toggles row
        row_y = ay + 28
        # toggle OFF
        tx, ty2 = m+pad, row_y+4
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(tog_off))
        p.drawRoundedRect(tx, ty2, 28, 12, 6, 6)
        p.setBrush(QColor(tog_knob)); p.drawEllipse(tx+1, ty2+1, 10, 10)
        self._txt(p, tx+32, ty2+10, "Zip", ink, 9)
        # toggle ON
        tx2 = tx + 65
        p.setBrush(QColor(tog_on)); p.drawRoundedRect(tx2, ty2, 28, 12, 6, 6)
        p.setBrush(QColor(tog_knob)); p.drawEllipse(tx2+15, ty2+1, 10, 10)
        self._txt(p, tx2+32, ty2+10, "Email", ink, 9)

        # divider
        p.setPen(QPen(QColor(mist), 1))
        p.drawLine(m, top_y+top_h, m+pw, top_y+top_h)

        # ── Queue ─────────────────────────────────────────────────────────────
        qy = top_y + top_h + 1; qh = H - qy - m
        qbg = _blend(bg, al, db)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(qbg)); p.drawRect(m, qy, pw, qh)

        # queue toolbar
        hh = 30; hbg = _blend(bg, al*0.9, db)
        p.setBrush(QColor(hbg)); p.drawRect(m, qy, pw, hh)
        p.setPen(QPen(QColor(mist), 1)); p.drawLine(m, qy+hh, m+pw, qy+hh)
        self._txt(p, m+12, qy+20, "Queue", ink, 10, bold=True)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(s2))
        p.drawRoundedRect(W-m-62, qy+6, 56, 18, 4, 4)
        p.setPen(QPen(QColor(tp), 1)); p.drawRoundedRect(W-m-62, qy+6, 56, 18, 4, 4)
        self._txt(p, W-m-55, qy+19, "Clear Done", stone, 8)

        # tile 1: uploading
        tile_y = qy + hh + 6
        tile_h = 58
        for (label, badge, badge_fg, badge_bg, status, st_col, prog) in [
            ("project-files.zip", "UPLOAD", stone, s2,  "Uploading 45%", teal,  0.45),
            ("WATCH · footage",   "WATCH",  "#fff", teal, "● Watching",    green, None),
        ]:
            if tile_y + tile_h > H - m: break
            tbg = _blend(surface, al, db)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(tbg))
            p.drawRoundedRect(m+4, tile_y, pw-8, tile_h, 4, 4)
            # accent stripe
            stripe = teal if badge == "WATCH" else stone
            p.setBrush(QColor(stripe))
            p.drawRoundedRect(m+4, tile_y, 4, tile_h, 2, 2)
            p.fillRect(m+4, tile_y+4, 4, tile_h-8, QColor(stripe))
            # badge
            p.setBrush(QColor(badge_bg))
            p.drawRoundedRect(m+14, tile_y+8, 48, 16, 3, 3)
            self._txt(p, m+16, tile_y+19, badge, badge_fg, 8, bold=True)
            # filename
            self._txt(p, m+68, tile_y+19, label, ink, 9, bold=True)
            # status
            self._txt(p, m+68, tile_y+33, status, st_col, 9)
            # progress bar
            if prog is not None:
                p.setBrush(QColor(tp))
                p.drawRoundedRect(m+4, tile_y+tile_h-5, pw-8, 4, 2, 2)
                p.setBrush(QColor(teal))
                p.drawRoundedRect(m+4, tile_y+tile_h-5, int((pw-8)*prog), 4, 2, 2)
            # drop zone (show in first tile gap)
            tile_y += tile_h + tgap

        # Drop zone hint inside queue area if space
        dz_y = tile_y + 4
        if dz_y + 36 < H - m:
            p.setPen(QPen(QColor(tm), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(_blend(bg, al*0.5, db)))
            p.drawRoundedRect(m+4, dz_y, pw-8, 32, 6, 6)
            self._txt(p, m+pw//2-35, dz_y+20, "Drop files here", td, 9)

        p.end()

    def _txt(self, p, x, y, text, col, sz, bold=False):
        f = QFont("Helvetica Neue", sz); f.setBold(bold)
        p.setFont(f); p.setPen(QPen(QColor(col))); p.drawText(x, y, text)


class PreviewPane(QWidget):
    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        self._v = values; self._dark = False
        outer = QVBoxLayout(self); outer.setContentsMargins(0,0,0,6); outer.setSpacing(4)
        tog = QHBoxLayout(); tog.setSpacing(0)
        self._lb = QPushButton("☀  Light"); self._db = QPushButton("☾  Dark")
        for b in (self._lb, self._db): b.setFixedHeight(26); b.setCheckable(True)
        self._lb.setChecked(True)
        self._lb.clicked.connect(lambda: self._sw(False))
        self._db.clicked.connect(lambda: self._sw(True))
        tog.addWidget(self._lb); tog.addWidget(self._db); tog.addStretch()
        outer.addLayout(tog)
        self._canvas = PreviewCanvas(values)
        outer.addWidget(self._canvas, 1)
        n = QLabel("Simulated preview — glass panels also blur desktop behind them.")
        n.setStyleSheet("color:#aaa;font-size:9px;"); n.setWordWrap(True)
        outer.addWidget(n)
        self._style()

    def _sw(self, dark: bool):
        self._dark = dark; self._lb.setChecked(not dark); self._db.setChecked(dark)
        self._canvas.set_dark(dark); self._style()

    def _style(self):
        a = "background:#0089a6;color:white;border:none;border-radius:4px;padding:3px 12px"
        i = "background:#E0E0E0;color:#333;border:none;border-radius:4px;padding:3px 12px"
        self._lb.setStyleSheet(f"QPushButton{{{i if self._dark else a}}}")
        self._db.setStyleSheet(f"QPushButton{{{a if self._dark else i}}}")

    def refresh(self): self._canvas.update()


# ══════════════════════════════════════════════════════════════════════════════
# Control panels
# ══════════════════════════════════════════════════════════════════════════════

def _scroll_widget() -> tuple[QScrollArea, QVBoxLayout]:
    scroll = QScrollArea(); scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea,QWidget{background:white}")
    inner = QWidget(); inner.setStyleSheet("background:white")
    lay = QVBoxLayout(inner); lay.setContentsMargins(16, 6, 16, 16); lay.setSpacing(0)
    scroll.setWidget(inner)
    return scroll, lay


class ThemeEditor(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Uplift — Theme Editor")
        self.resize(960, 700); self.setMinimumSize(800, 540)
        self._values: dict = dict(DEFAULTS)
        if THEME_PATH.exists():
            try: self._values.update(json.loads(THEME_PATH.read_text()))
            except Exception: pass
        self._rows: list = []
        self._build_ui()

    # ── Shell ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)

        hdr = QFrame(); hdr.setStyleSheet("background:#0089a6"); hdr.setFixedHeight(50)
        hl = QHBoxLayout(hdr); hl.setContentsMargins(18,0,18,0)
        t = QLabel("Uplift Theme Editor"); t.setStyleSheet("color:white;font-size:15px;font-weight:bold")
        hl.addWidget(t); hl.addStretch()
        n = QLabel("Changes apply on Uplift restart"); n.setStyleSheet("color:rgba(255,255,255,.7);font-size:11px")
        hl.addWidget(n)
        outer.addWidget(hdr)

        spl = QSplitter(Qt.Orientation.Horizontal)
        spl.setHandleWidth(1); spl.setStyleSheet("QSplitter::handle{background:#DDD}")
        outer.addWidget(spl, 1)
        spl.addWidget(self._build_tabs())
        self._preview = PreviewPane(self._values); spl.addWidget(self._preview)
        spl.setSizes([530, 380])

        bot = QFrame(); bot.setStyleSheet("background:#F5F5F5;border-top:1px solid #CCC")
        bot.setFixedHeight(54)
        bl = QHBoxLayout(bot); bl.setContentsMargins(18,0,18,0); bl.setSpacing(10)
        rb = QPushButton("↩  Reset to Defaults"); rb.setFixedHeight(34)
        rb.setStyleSheet("QPushButton{background:white;color:#333;border:1px solid #CCC;"
                         "border-radius:5px;padding:0 14px;font-size:12px}"
                         "QPushButton:hover{background:#EEE}")
        rb.clicked.connect(self._reset); bl.addWidget(rb); bl.addStretch()
        self._st = QLabel(""); self._st.setStyleSheet("color:#555;font-size:11px")
        bl.addWidget(self._st)
        ab = QPushButton("✓  Apply & Restart Uplift"); ab.setFixedHeight(34)
        ab.setStyleSheet("QPushButton{background:#0089a6;color:white;border:none;"
                         "border-radius:5px;padding:0 18px;font-size:13px;font-weight:bold}"
                         "QPushButton:hover{background:#04657e}")
        ab.clicked.connect(self._apply); bl.addWidget(ab)
        outer.addWidget(bot)

    def _build_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane{border:none;background:white}
            QTabBar::tab{padding:7px 15px;font-size:12px;background:#EFEFEF;
                         border:1px solid #D0D0D0;border-bottom:none;margin-right:2px}
            QTabBar::tab:selected{background:white;color:#0089a6;font-weight:bold}
        """)
        tabs.addTab(self._tab_backgrounds(),   "Backgrounds")
        tabs.addTab(self._tab_text(),           "Text")
        tabs.addTab(self._tab_accent(),         "Accent Palette")
        tabs.addTab(self._tab_status(),         "Status & Icons")
        tabs.addTab(self._tab_glass_spacing(),  "Glass & Spacing")
        return tabs

    # ── helpers ───────────────────────────────────────────────────────────────

    def _crow(self, lay, label: str, sub: str, key: str):
        row = ColorRow(label, sub, key, self._values)
        row.changed.connect(self._changed); self._rows.append(row); lay.addWidget(row)

    def _srow(self, lay, label: str, sub: str, key: str,
              lo: int, hi: int, scale: float = 1.0, suffix: str = ""):
        row = SliderRow(label, sub, key, self._values, lo, hi, scale, suffix)
        row.changed.connect(self._changed); self._rows.append(row); lay.addWidget(row)

    def _sec(self, lay, title: str, sub: str = ""):
        lay.addWidget(SectionHeader(title))
        if sub: lay.addWidget(SubHeader(sub))

    # ── Tab: Backgrounds ──────────────────────────────────────────────────────

    def _tab_backgrounds(self) -> QWidget:
        sc, lay = _scroll_widget()
        self._sec(lay, "LIGHT MODE BACKGROUNDS",
                  "Used when your Mac is in Light appearance.")
        self._crow(lay, "Window background",            "Behind all panels; queue area fill", "BG")
        self._crow(lay, "Panels & input fields",        "Form panel, job tiles, input boxes", "SURFACE")
        self._crow(lay, "Secondary panels / row tints", "Inactive tab bg, badge bg, file rows", "SURFACE2")
        self._crow(lay, "Dividers & borders",           "Horizontal lines, toggle off-track", "MIST")
        lay.addWidget(HDivider())
        self._sec(lay, "DARK MODE BACKGROUNDS",
                  "Used when your Mac is in Dark appearance.")
        self._crow(lay, "Window background",            "Behind all panels; queue area fill", "BG_DARK")
        self._crow(lay, "Panels & input fields",        "Form panel, job tiles, input boxes", "SURFACE_DARK")
        self._crow(lay, "Secondary panels / row tints", "Inactive tab bg, badge bg, file rows", "SURFACE2_DARK")
        self._crow(lay, "Dividers & borders",           "Horizontal lines, toggle off-track", "MIST_DARK")
        lay.addStretch(); return sc

    # ── Tab: Text ─────────────────────────────────────────────────────────────

    def _tab_text(self) -> QWidget:
        sc, lay = _scroll_widget()
        self._sec(lay, "LIGHT MODE TEXT")
        self._crow(lay, "Primary text",    "File names, labels, input text", "INK")
        self._crow(lay, "Medium text",     "Section headers, secondary labels", "GRAPHITE")
        self._crow(lay, "Subtle text",     "Status text, 'Settings'/'Accounts' links (initial), "
                                           "placeholder text, 'UPLOAD' badge text", "STONE")
        lay.addWidget(HDivider())
        self._sec(lay, "DARK MODE TEXT")
        self._crow(lay, "Primary text",    "File names, labels, input text", "INK_DARK")
        self._crow(lay, "Medium text",     "Section headers, secondary labels", "GRAPHITE_DARK")
        self._crow(lay, "Subtle text",     "Status text, placeholder, 'UPLOAD' badge text", "STONE_DARK")
        lay.addStretch(); return sc

    # ── Tab: Accent Palette ───────────────────────────────────────────────────

    def _tab_accent(self) -> QWidget:
        sc, lay = _scroll_widget()
        self._sec(lay, "PRIMARY ACCENT",
                  "The main brand colour — applies to all six tones below.")
        self._crow(lay, "Primary accent  (TEAL)",
                   "Active tab bg · 'Add to Queue' / 'Start Watching' buttons · "
                   "WATCH badge background · toggle ON track · progress bar fill · "
                   "job tile WATCH accent stripe · focused input border",
                   "TEAL")
        self._crow(lay, "Dark accent  (TEAL_DEEP)",
                   "Button hover · 'Settings' / 'Accounts' link colour · "
                   "'WATCH' badge text · envelope icon · drop zone label text",
                   "TEAL_DEEP")
        self._crow(lay, "Mid accent  (TEAL_MID)",
                   "Drop zone border (idle) · hover border on inputs and buttons",
                   "TEAL_MID")
        self._crow(lay, "Soft accent  (TEAL_SOFT)",
                   "Disabled primary button · progress bar gradient end",
                   "TEAL_SOFT")
        self._crow(lay, "Pale accent  (TEAL_PALE)",
                   "Input/button borders · job tile border · tree widget border · "
                   "progress bar track",
                   "TEAL_PALE")
        self._crow(lay, "Wash accent  (TEAL_WASH)",
                   "Button/input hover background · uploading file row tint · "
                   "drop zone idle background",
                   "TEAL_WASH")
        lay.addWidget(HDivider())
        self._sec(lay, "TOGGLE SWITCHES",
                  "Zip and Email toggles in the upload/watch form. "
                  "These are independent of the palette above so you can tune them separately.")
        self._crow(lay, "Toggle — ON track",
                   "Pill background when toggle is switched on",
                   "TOGGLE_ON")
        self._crow(lay, "Toggle — OFF track (light mode)",
                   "Pill background when toggle is off, light appearance",
                   "TOGGLE_OFF")
        self._crow(lay, "Toggle — OFF track (dark mode)",
                   "Pill background when toggle is off, dark appearance",
                   "TOGGLE_OFF_DARK")
        self._crow(lay, "Toggle — knob / circle",
                   "The sliding circle on top of the pill (usually white)",
                   "TOGGLE_KNOB")
        lay.addStretch(); return sc

    # ── Tab: Status & Icons ───────────────────────────────────────────────────

    def _tab_status(self) -> QWidget:
        sc, lay = _scroll_widget()
        self._sec(lay, "STATUS COLOURS",
                  "Shown in job tiles, the status dot, and file row indicators.")
        self._crow(lay, "Success / Ready  (GREEN)",
                   "Status dot when connected · 'Done' job text · "
                   "'Watching' active indicator · email sent",
                   "GREEN")
        self._crow(lay, "Error / Failed  (RED)",
                   "Failed upload text · error dialogs · "
                   "email failed · retry prompt",
                   "RED")
        self._crow(lay, "Warning / Pending  (YELLOW)",
                   "Retry status in file rows · pending indicators",
                   "YELLOW")
        lay.addStretch(); return sc

    # ── Tab: Glass & Spacing ──────────────────────────────────────────────────

    def _tab_glass_spacing(self) -> QWidget:
        sc, lay = _scroll_widget()
        self._sec(lay, "GLASS TRANSPARENCY",
                  "How see-through the panels are over the Liquid Glass background. "
                  "Lower = more desktop shows through. Higher = more solid.")
        self._srow(lay, "Light mode transparency", "", "GLASS_ALPHA_LIGHT", 10, 100, 0.01)
        self._srow(lay, "Dark mode transparency",  "", "GLASS_ALPHA_DARK",  10, 100, 0.01)
        lay.addWidget(HDivider())
        self._sec(lay, "SPACING",
                  "Padding and gaps. Changes take effect on Uplift restart.")
        specs = {
            "SP_PANEL_H":  ("Panel left/right padding",
                            "Space inside the top form panel — left & right", 4, 48),
            "SP_PANEL_V":  ("Panel top/bottom padding",
                            "Space inside the top form panel — top & bottom", 4, 32),
            "SP_QUEUE_H":  ("Queue left/right padding",
                            "Margin inside the queue/job list area", 4, 48),
            "SP_TILE_GAP": ("Gap between job tiles",
                            "Vertical space between each job card", 0, 32),
            "SP_ROW_GAP":  ("Gap between form rows",
                            "Space between rows within the upload/watch form", 0, 20),
        }
        for key, (label, sub, lo, hi) in specs.items():
            self._srow(lay, label, sub, key, lo, hi, 1.0, " px")
        lay.addStretch(); return sc

    # ── State ─────────────────────────────────────────────────────────────────

    def _changed(self):
        self._preview.refresh()
        self._st.setText("Unsaved changes")
        self._st.setStyleSheet("color:#c0392b;font-size:11px")

    def _reset(self):
        if QMessageBox.question(
            self, "Reset", "Reset everything to original Uplift defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        self._values.clear(); self._values.update(DEFAULTS)
        for r in self._rows: r.refresh(self._values)
        self._preview.refresh()
        self._st.setText("Reset — not yet saved")
        self._st.setStyleSheet("color:#e67e22;font-size:11px")

    def _apply(self):
        tmp = THEME_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._values, indent=2)); tmp.replace(THEME_PATH)
        self._st.setText("Saved!"); self._st.setStyleSheet("color:#197a26;font-size:11px")
        if UPLIFT_APP.exists():
            subprocess.run(["pkill", "-x", "Uplift"], capture_output=True)
            QTimer.singleShot(1000, lambda: subprocess.Popen(["open", str(UPLIFT_APP)]))
            QTimer.singleShot(2500, lambda: self._st.setText("✓ Applied — Uplift restarted"))
        else:
            QMessageBox.information(self, "Saved",
                f"Saved to {THEME_PATH}.\nUplift not found at {UPLIFT_APP} — restart manually.")


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Uplift Theme Editor")
    app.setStyleSheet("QWidget{font-family:'Helvetica Neue',Arial,sans-serif}")
    win = ThemeEditor(); win.show()
    sys.exit(app.exec())
