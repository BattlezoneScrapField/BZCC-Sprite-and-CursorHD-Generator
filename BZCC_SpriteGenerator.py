#!/usr/bin/env python3
"""
BZCC Sprite Generator
=====================
Unified tool for cursor sprite‑sheets and sprite/colour-map generation.
Global export multiplier (×1 … ×5) scales all output textures.
All settings (export paths, cursor names, etc.) are saved between sessions.
"""

import sys
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QLabel, QPushButton, QFileDialog, QSlider,
    QCheckBox, QSpinBox, QLineEdit, QTextEdit, QMessageBox,
    QComboBox, QScrollArea, QColorDialog, QButtonGroup
)
from PyQt5.QtCore import Qt, QTimer, QFileSystemWatcher, QSettings
from PyQt5.QtGui import QPixmap, QImage, QColor, QPainter

from PIL import Image, ImageFilter, ImageEnhance, ImageOps

# ----------------------------------------------------------------------
# Pillow resampling compatibility
# ----------------------------------------------------------------------
try:
    RESAMPLE = Image.Resampling
except AttributeError:
    class RESAMPLE:
        NEAREST = Image.NEAREST
        BILINEAR = Image.BILINEAR
        BICUBIC = Image.BICUBIC
        LANCZOS = Image.LANCZOS

# ----------------------------------------------------------------------
# Global multiplier presets
# ----------------------------------------------------------------------
MULTIPLIER_PRESETS = [1, 2, 3, 4, 5]
DEFAULT_MULTIPLIER = 1

# ----------------------------------------------------------------------
# PIL → QPixmap (no channel swapping – accurate preview)
# ----------------------------------------------------------------------
def pil2pixmap(pil_img: Image.Image) -> QPixmap:
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qim = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qim)

# ----------------------------------------------------------------------
# Cursor constants
# ----------------------------------------------------------------------
GRID_SIZE = 8
CELL_SIZE = 128
FRAMES_TOTAL = 64
SOURCE_DIM = 1024

EXPORT_SIZES_CURSOR = {
    "base": 256, "x1_5": 384, "x2_0": 512, "x2_5": 640,
    "x3_0": 768, "x3_5": 896, "x4_0": 1024, "x4_5": 1152, "x5_0": 1280,
}

# ----------------------------------------------------------------------
# Sprite generator constants
# ----------------------------------------------------------------------
BASE_SIZE_SPRITE = 128
VARIANTS_SPRITE: List[Tuple[str, int]] = [
    ("x1_0", 128), ("x1_5", 192), ("x2_0", 256), ("x2_5", 320),
    ("x3_0", 384), ("x3_5", 448), ("x4_0", 512), ("x4_5", 576), ("x5_0", 640),
]

EXPORT_FORMATS = ["DDS", "TGA", "PNG"]
DDS_FORMATS = ["DXT5", "BC3_UNORM", "BC3_UNORM_SRGB", "DXT3"]
RESAMPLE_NAMES = ["Nearest", "Bilinear", "Bicubic", "Lanczos"]
RESAMPLE_MAP = {
    "Nearest": RESAMPLE.NEAREST, "Bilinear": RESAMPLE.BILINEAR,
    "Bicubic": RESAMPLE.BICUBIC, "Lanczos": RESAMPLE.LANCZOS,
}

# ----------------------------------------------------------------------
# texconv finder
# ----------------------------------------------------------------------
def find_texconv() -> Optional[Path]:
    env = shutil.which("texconv")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for name in ("texconv.exe", "texconv"):
        p = here / name
        if p.exists():
            return p
    return None

# ======================================================================
# Shared processing panel (sliders + reset)
# ======================================================================
class ImageProcessingPanel(QGroupBox):
    def __init__(self, title="Processing", parent=None):
        super().__init__(title, parent)
        layout = QFormLayout()
        layout.setVerticalSpacing(3)

        self.sharpen_slider = self._add_slider("Sharpen", 0.0, 3.0, 0.0, layout)
        self.blur_slider = self._add_slider("Blur", 0.0, 3.0, 0.0, layout)
        self.gamma_slider = self._add_slider("Gamma", 0.2, 3.0, 1.0, layout)
        self.brightness_slider = self._add_slider("Brightness", 0.2, 2.0, 1.0, layout)
        self.contrast_slider = self._add_slider("Contrast", 0.2, 2.0, 1.0, layout)
        self.opacity_slider = self._add_slider("Opacity", 0.0, 1.0, 1.0, layout)
        self.edge_slider = self._add_slider("Edge enhance", 0.0, 2.0, 0.0, layout)
        self.denoise_slider = self._add_slider("Denoise", 0.0, 3.0, 0.0, layout)

        self.reset_btn = QPushButton("Reset Values")
        self.reset_btn.clicked.connect(self._reset_defaults)
        layout.addRow(self.reset_btn)

        self.setLayout(layout)

    def _add_slider(self, name, min_val, max_val, default, layout):
        slider = QSlider(Qt.Horizontal)
        slider.setRange(int(min_val * 100), int(max_val * 100))
        slider.setValue(int(default * 100))
        layout.addRow(name + ":", slider)
        return slider

    def get_values(self):
        def val(s):
            return s.value() / 100.0
        return {
            "sharpen": val(self.sharpen_slider),
            "blur": val(self.blur_slider),
            "gamma": val(self.gamma_slider),
            "brightness": val(self.brightness_slider),
            "contrast": val(self.contrast_slider),
            "opacity": val(self.opacity_slider),
            "edge_enhance": val(self.edge_slider),
            "denoise": val(self.denoise_slider),
        }

    def set_values(self, vals: dict):
        def set_slider(slider, v):
            slider.setValue(int(v * 100))
        set_slider(self.sharpen_slider, vals.get("sharpen", 0.0))
        set_slider(self.blur_slider, vals.get("blur", 0.0))
        set_slider(self.gamma_slider, vals.get("gamma", 1.0))
        set_slider(self.brightness_slider, vals.get("brightness", 1.0))
        set_slider(self.contrast_slider, vals.get("contrast", 1.0))
        set_slider(self.opacity_slider, vals.get("opacity", 1.0))
        set_slider(self.edge_slider, vals.get("edge_enhance", 0.0))
        set_slider(self.denoise_slider, vals.get("denoise", 0.0))

    def _reset_defaults(self):
        self.set_values({
            "sharpen": 0.0, "blur": 0.0, "gamma": 1.0, "brightness": 1.0,
            "contrast": 1.0, "opacity": 1.0, "edge_enhance": 0.0, "denoise": 0.0,
        })

# ======================================================================
# Cursor panel (one per cursor)
# ======================================================================
class CursorPanel(QGroupBox):
    def __init__(self, title: str, settings_prefix: str, parent=None):
        super().__init__(title, parent)
        self.settings_prefix = settings_prefix
        self.image_path = ""
        self.source_image: Optional[Image.Image] = None
        self.frames: List[QPixmap] = []
        self.current_frame = 0
        self._is_sequence = False
        self._bg_mode = "checker"
        self._bg_color = QColor(64, 64, 64)
        self._anim_running = False

        self._process_timer = QTimer(self)
        self._process_timer.setSingleShot(True)
        self._process_timer.timeout.connect(self._do_process_source)

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self.on_file_changed)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._advance_frame)

        self.processing = ImageProcessingPanel("Cursor FX")
        self._init_ui()
        self.load_settings()

    def _init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setSpacing(3)

        # preview
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedSize(CELL_SIZE, CELL_SIZE)
        self.preview_label.setStyleSheet("background-color: #222; border: 1px solid #555;")
        main_layout.addWidget(self.preview_label, alignment=Qt.AlignCenter)

        # annotations
        info = QLabel("💡 1024×1024 sheet or 64 frames 128×128 (sequence)")
        info.setWordWrap(True); info.setStyleSheet("color: #aaa; font-size: 8pt;")
        main_layout.addWidget(info)

        self.size_warning = QLabel("")
        self.size_warning.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        self.size_warning.setWordWrap(True)
        main_layout.addWidget(self.size_warning)

        # background
        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel("BG:"))
        self.bg_combo = QComboBox()
        self.bg_combo.addItems(["Checker", "Solid"])
        self.bg_combo.currentIndexChanged.connect(self._on_bg_changed)
        bg_row.addWidget(self.bg_combo)
        self.bg_color_btn = QPushButton("Color...")
        self.bg_color_btn.clicked.connect(self._pick_bg_color)
        bg_row.addWidget(self.bg_color_btn)
        bg_row.addStretch()
        main_layout.addLayout(bg_row)

        # navigation (added first/last)
        nav_layout = QHBoxLayout()
        self.btn_first = QPushButton("⏮"); self.btn_first.setFixedWidth(28)
        self.btn_first.clicked.connect(self._first_frame); nav_layout.addWidget(self.btn_first)
        self.btn_play = QPushButton("▶"); self.btn_play.setFixedWidth(28)
        self.btn_play.clicked.connect(self._toggle_anim); nav_layout.addWidget(self.btn_play)
        self.btn_prev = QPushButton("◀"); self.btn_prev.setFixedWidth(28)
        self.btn_prev.clicked.connect(self._prev_frame); nav_layout.addWidget(self.btn_prev)
        self.btn_next = QPushButton("▶"); self.btn_next.setFixedWidth(28)
        self.btn_next.clicked.connect(self._next_frame); nav_layout.addWidget(self.btn_next)
        self.btn_last = QPushButton("⏭"); self.btn_last.setFixedWidth(28)
        self.btn_last.clicked.connect(self._last_frame); nav_layout.addWidget(self.btn_last)

        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setRange(0, FRAMES_TOTAL-1); self.frame_slider.setValue(0)
        self.frame_slider.valueChanged.connect(self._on_slider_changed)
        nav_layout.addWidget(self.frame_slider, 1)

        self.frame_label = QLabel("0 / 63"); self.frame_label.setFixedWidth(50)
        nav_layout.addWidget(self.frame_label)
        main_layout.addLayout(nav_layout)

        # load/clear
        load_row = QHBoxLayout()
        self.btn_load_sheet = QPushButton("Load sheet"); self.btn_load_sheet.clicked.connect(self.load_sheet_dialog)
        self.btn_load_seq = QPushButton("Load sequence"); self.btn_load_seq.clicked.connect(self.load_sequence_dialog)
        self.btn_clear = QPushButton("Clear"); self.btn_clear.clicked.connect(self.clear_image)
        load_row.addWidget(self.btn_load_sheet); load_row.addWidget(self.btn_load_seq)
        load_row.addWidget(self.btn_clear)
        main_layout.addLayout(load_row)

        # settings (compact)
        set_row = QHBoxLayout()
        set_row.addWidget(QLabel("Name:"))
        self.base_name_input = QLineEdit("cursorHD"); self.base_name_input.setFixedWidth(70)
        set_row.addWidget(self.base_name_input)
        set_row.addWidget(QLabel("Hotspot X:"))
        self.hotspot_x = QSpinBox(); self.hotspot_x.setRange(0, 128); self.hotspot_x.setValue(5)
        self.hotspot_x.setFixedWidth(45); set_row.addWidget(self.hotspot_x)
        set_row.addWidget(QLabel("Y:"))
        self.hotspot_y = QSpinBox(); self.hotspot_y.setRange(0, 128); self.hotspot_y.setValue(10)
        self.hotspot_y.setFixedWidth(45); set_row.addWidget(self.hotspot_y)
        set_row.addWidget(QLabel("FPS:"))
        self.fps_spin = QSpinBox(); self.fps_spin.setRange(1, 144); self.fps_spin.setValue(60)
        self.fps_spin.setFixedWidth(45); set_row.addWidget(self.fps_spin)
        self.aa_check = QCheckBox("AA"); self.aa_check.setChecked(True)
        set_row.addWidget(self.aa_check)
        set_row.addStretch()
        main_layout.addLayout(set_row)

        # processing sliders
        for slider in [self.processing.sharpen_slider, self.processing.blur_slider,
                       self.processing.gamma_slider, self.processing.brightness_slider,
                       self.processing.contrast_slider, self.processing.opacity_slider,
                       self.processing.edge_slider, self.processing.denoise_slider]:
            slider.valueChanged.connect(self._schedule_process)
        main_layout.addWidget(self.processing)

        self.setLayout(main_layout)

    # ----------------------------------------------------------------
    # Background
    # ----------------------------------------------------------------
    def _on_bg_changed(self, idx):
        self._bg_mode = "checker" if idx == 0 else "solid"
        self._update_preview_with_bg()

    def _pick_bg_color(self):
        color = QColorDialog.getColor(self._bg_color, self, "Choose preview background")
        if color.isValid():
            self._bg_color = color
            if self._bg_mode == "solid":
                self._update_preview_with_bg()

    def _make_preview_pixmap(self, frame_pix: QPixmap) -> QPixmap:
        size = CELL_SIZE
        if self._bg_mode == "checker":
            checker = QPixmap(size, size)
            checker.fill(QColor(48,48,48))
            painter = QPainter(checker)
            tile = 16; light = QColor(72,72,72)
            for y in range(0, size, tile):
                for x in range(0, size, tile):
                    if ((x//tile)+(y//tile))%2 == 0:
                        painter.fillRect(x, y, tile, tile, light)
            painter.end()
            result = QPixmap(size, size)
            result.fill(Qt.transparent)
            p2 = QPainter(result)
            p2.drawPixmap(0,0,checker)
            p2.drawPixmap(0,0,frame_pix)
            p2.end()
            return result
        else:
            result = QPixmap(size, size)
            result.fill(self._bg_color)
            painter = QPainter(result)
            painter.drawPixmap(0,0,frame_pix)
            painter.end()
            return result

    def _update_preview_with_bg(self):
        if not self.frames:
            self.preview_label.clear()
            return
        pix = self.frames[self.current_frame]
        self.preview_label.setPixmap(self._make_preview_pixmap(pix))

    # ----------------------------------------------------------------
    # Frame navigation
    # ----------------------------------------------------------------
    def _toggle_anim(self):
        if self._anim_running:
            self.anim_timer.stop()
            self.btn_play.setText("▶")
            self._anim_running = False
        else:
            if self.frames:
                self.anim_timer.start(int(1000 / self.fps_spin.value()))
                self.btn_play.setText("⏸")
                self._anim_running = True

    def _advance_frame(self):
        if not self.frames: return
        self.current_frame = (self.current_frame + 1) % FRAMES_TOTAL
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(self.current_frame)
        self.frame_slider.blockSignals(False)
        self.frame_label.setText(f"{self.current_frame} / 63")
        self._update_preview_with_bg()

    def _prev_frame(self):
        if not self.frames: return
        self.current_frame = (self.current_frame - 1) % FRAMES_TOTAL
        self._jump_to(self.current_frame)

    def _next_frame(self):
        if not self.frames: return
        self.current_frame = (self.current_frame + 1) % FRAMES_TOTAL
        self._jump_to(self.current_frame)

    def _first_frame(self):
        if not self.frames: return
        self._jump_to(0)

    def _last_frame(self):
        if not self.frames: return
        self._jump_to(FRAMES_TOTAL - 1)

    def _jump_to(self, idx):
        self.current_frame = idx
        self.frame_slider.setValue(idx)
        self.frame_label.setText(f"{idx} / 63")
        self._update_preview_with_bg()

    def _on_slider_changed(self, val):
        if not self.frames: return
        self.current_frame = val
        self.frame_label.setText(f"{val} / 63")
        self._update_preview_with_bg()

    # ----------------------------------------------------------------
    # Load / Clear
    # ----------------------------------------------------------------
    def clear_image(self):
        self.source_image = None; self.frames.clear()
        self.current_frame = 0; self.image_path = ""
        self._is_sequence = False
        self.preview_label.clear()
        self.size_warning.setText("")
        self.frame_slider.setValue(0); self.frame_label.setText("0 / 63")
        if self.anim_timer.isActive():
            self.anim_timer.stop(); self.btn_play.setText("▶"); self._anim_running = False
        if self.watcher.files(): self.watcher.removePaths(self.watcher.files())
        self.save_settings()

    def load_sheet_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open sprite sheet", "",
            "Images (*.png *.tga *.dds *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self._is_sequence = False
            self.load_image(path)

    def load_sequence_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder with 64 frames")
        if not folder: return
        try:
            img = self.build_sprite_sheet_from_sequence(folder)
            self.source_image = img
            self._is_sequence = True
            self.image_path = folder
            self.watcher.addPath(folder)
            self._schedule_process()
            self.save_settings()
        except Exception as e:
            QMessageBox.critical(self, "Sequence Error", str(e))

    def load_image(self, path: str):
        if not os.path.exists(path): return
        if self.image_path: self.watcher.removePath(self.image_path)
        self.image_path = path
        self.watcher.addPath(self.image_path)
        self._schedule_process()
        self.save_settings()

    def build_sprite_sheet_from_sequence(self, folder: str) -> Image.Image:
        folder_path = Path(folder)
        exts = {".png", ".tga", ".dds", ".jpg", ".jpeg", ".webp", ".bmp"}
        files = sorted([f for f in folder_path.iterdir() if f.suffix.lower() in exts])
        if len(files) != FRAMES_TOTAL:
            raise ValueError(f"Expected {FRAMES_TOTAL} frames, found {len(files)}.")
        sheet = Image.new("RGBA", (SOURCE_DIM, SOURCE_DIM))
        for idx, fpath in enumerate(files):
            try: frame_img = Image.open(fpath).convert("RGBA")
            except Exception as e: raise ValueError(f"Error loading {fpath.name}: {e}")
            if frame_img.size != (CELL_SIZE, CELL_SIZE):
                frame_img = frame_img.resize((CELL_SIZE, CELL_SIZE), RESAMPLE.LANCZOS)
            row = idx // GRID_SIZE; col = idx % GRID_SIZE
            x = col * CELL_SIZE; y = row * CELL_SIZE
            sheet.paste(frame_img, (x, y))
        return sheet

    def on_file_changed(self, path: str):
        QTimer.singleShot(500, self._schedule_process)

    # ----------------------------------------------------------------
    # Processing
    # ----------------------------------------------------------------
    def _schedule_process(self):
        self._process_timer.start(200)

    def _do_process_source(self):
        if not self.image_path or not os.path.exists(self.image_path): return
        try:
            if self._is_sequence:
                img = self.build_sprite_sheet_from_sequence(self.image_path)
            else:
                img = Image.open(self.image_path).convert("RGBA")
            img = self.apply_processing(img)
            if img.size != (SOURCE_DIM, SOURCE_DIM):
                self.size_warning.setText(f"⚠ Size: {img.size[0]}×{img.size[1]} (not 1024×1024)")
            else:
                self.size_warning.setText("")
            self.source_image = img
            self.extract_frames()
            self._update_preview_with_bg()
            self.frame_slider.setValue(0); self.frame_label.setText("0 / 63")
        except Exception as e:
            self.size_warning.setText(f"❌ Error: {e}")

    def apply_processing(self, img: Image.Image) -> Image.Image:
        vals = self.processing.get_values()
        base = img.convert("RGBA")
        if vals["gamma"] != 1.0: base = self._apply_gamma(base, vals["gamma"])
        if vals["brightness"] != 1.0: base = ImageEnhance.Brightness(base).enhance(vals["brightness"])
        if vals["contrast"] != 1.0: base = ImageEnhance.Contrast(base).enhance(vals["contrast"])
        if vals["opacity"] < 1.0: base = self._apply_opacity(base, vals["opacity"])
        if vals["blur"] > 0: base = base.filter(ImageFilter.GaussianBlur(radius=float(vals["blur"])))
        if vals["edge_enhance"] > 0:
            enhanced = base.filter(ImageFilter.EDGE_ENHANCE_MORE)
            base = Image.blend(base, enhanced, min(1.0, float(vals["edge_enhance"])/2.0))
        if vals["denoise"] > 0: base = base.filter(ImageFilter.MedianFilter(size=max(3, int(1+round(float(vals["denoise"]))*2))))
        if vals["sharpen"] > 0:
            amount = float(vals["sharpen"])
            sharpened = base.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(70+amount*120), threshold=2))
            base = Image.blend(base, sharpened, min(1.0, amount/3.0))
        return base

    @staticmethod
    def _apply_gamma(img, gamma):
        if gamma <= 0: return img
        inv = 1.0/gamma
        lut = [min(255, max(0, int((i/255.0)**inv*255.0+0.5))) for i in range(256)]
        r, g, b, a = img.split()
        return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))

    @staticmethod
    def _apply_opacity(img, opacity):
        opacity = max(0.0, min(1.0, opacity))
        r, g, b, a = img.split()
        a = a.point(lambda p: int(p*opacity))
        return Image.merge("RGBA", (r, g, b, a))

    def extract_frames(self):
        if self.source_image is None: return
        self.frames.clear()
        for idx in range(FRAMES_TOTAL):
            row = idx // GRID_SIZE; col = idx % GRID_SIZE
            x = col * CELL_SIZE; y = row * CELL_SIZE
            frame = self.source_image.crop((x, y, x+CELL_SIZE, y+CELL_SIZE))
            self.frames.append(pil2pixmap(frame))
        self.current_frame = 0

    # ----------------------------------------------------------------
    # Persistence (cursor‑specific)
    # ----------------------------------------------------------------
    def save_settings(self):
        s = QSettings("BZCC_Modding", "CursorTool")
        s.setValue(f"{self.settings_prefix}_path", self.image_path)
        s.setValue(f"{self.settings_prefix}_is_sequence", self._is_sequence)
        s.setValue(f"{self.settings_prefix}_fps", self.fps_spin.value())
        s.setValue(f"{self.settings_prefix}_hx", self.hotspot_x.value())
        s.setValue(f"{self.settings_prefix}_hy", self.hotspot_y.value())
        s.setValue(f"{self.settings_prefix}_aa", self.aa_check.isChecked())
        s.setValue(f"{self.settings_prefix}_basename", self.base_name_input.text())
        s.setValue(f"{self.settings_prefix}_bg_mode", self._bg_mode)
        s.setValue(f"{self.settings_prefix}_bg_color", self._bg_color.name())
        vals = self.processing.get_values()
        for k, v in vals.items():
            s.setValue(f"{self.settings_prefix}_{k}", v)

    def load_settings(self):
        s = QSettings("BZCC_Modding", "CursorTool")
        path = s.value(f"{self.settings_prefix}_path", "")
        self._is_sequence = s.value(f"{self.settings_prefix}_is_sequence", False) in ("true", True)
        if path and os.path.exists(path):
            if self._is_sequence:
                try:
                    self.source_image = self.build_sprite_sheet_from_sequence(path)
                    self.image_path = path
                    self.watcher.addPath(path)
                    self._schedule_process()
                except Exception: pass
            else:
                self.load_image(path)
        self.fps_spin.setValue(int(s.value(f"{self.settings_prefix}_fps", 60)))
        self.hotspot_x.setValue(int(s.value(f"{self.settings_prefix}_hx", 5)))
        self.hotspot_y.setValue(int(s.value(f"{self.settings_prefix}_hy", 10)))
        aa = s.value(f"{self.settings_prefix}_aa", True)
        self.aa_check.setChecked(aa in ("true", True))
        self.base_name_input.setText(s.value(f"{self.settings_prefix}_basename", "cursorHD"))
        bg_mode = s.value(f"{self.settings_prefix}_bg_mode", "checker")
        self._bg_mode = bg_mode
        self.bg_combo.setCurrentIndex(0 if bg_mode == "checker" else 1)
        color_name = s.value(f"{self.settings_prefix}_bg_color", "#404040")
        self._bg_color = QColor(color_name)
        vals = {}
        for key in ["sharpen","blur","gamma","brightness","contrast","opacity","edge_enhance","denoise"]:
            default = 0.0
            if key in ("gamma","brightness","contrast","opacity"): default = 1.0
            vals[key] = float(s.value(f"{self.settings_prefix}_{key}", default))
        self.processing.set_values(vals)

# ======================================================================
# Cursor Baker tab (two panels + export + persistence)
# ======================================================================
class CursorBakerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(); layout.setSpacing(4)
        panels = QHBoxLayout()
        self.panel_default = CursorPanel("Default Cursor", "def")
        self.panel_highlight = CursorPanel("Highlight Cursor", "hl")
        panels.addWidget(self.panel_default); panels.addWidget(self.panel_highlight)
        layout.addLayout(panels)

        # Export area
        export_group = QGroupBox("Export")
        exp_layout = QVBoxLayout()
        target_line = QHBoxLayout()
        self.target_dir = QLineEdit()
        self.target_dir.setPlaceholderText("Export folder...")
        self.btn_browse = QPushButton("Browse"); self.btn_browse.clicked.connect(self.browse_target)
        target_line.addWidget(self.target_dir); target_line.addWidget(self.btn_browse)
        exp_layout.addLayout(target_line)
        self.btn_export = QPushButton("BAKE CURSORS + CFG")
        self.btn_export.setStyleSheet("background-color: #5a2a2a; font-weight: bold; padding: 8px;")
        self.btn_export.clicked.connect(self.export_all)
        exp_layout.addWidget(self.btn_export)
        export_group.setLayout(exp_layout)
        layout.addWidget(export_group)

        # auto‑save export path on change (debounced)
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_settings)
        self.target_dir.textChanged.connect(lambda: self._save_timer.start(500))

        self.load_settings()
        self.setLayout(layout)

    def browse_target(self):
        path = QFileDialog.getExistingDirectory(self, "Choose export folder")
        if path: self.target_dir.setText(path)

    def export_all(self):
        out_dir = self.target_dir.text().strip()
        if not out_dir or not os.path.isdir(out_dir):
            QMessageBox.warning(self, "Error", "Select a valid export directory.")
            return
        mult = self.window().get_export_multiplier()
        success_def = self._bake_panel(self.panel_default, out_dir, mult)
        success_hl = self._bake_panel(self.panel_highlight, out_dir, mult)
        if success_def or success_hl:
            self._generate_config(out_dir)
            QMessageBox.information(self, "Done", "Cursor assets and config exported.")

    def _bake_panel(self, panel: CursorPanel, out_dir: str, mult: int) -> bool:
        if panel.source_image is None: return False
        base = panel.base_name_input.text().strip()
        if not base: return False
        use_aa = panel.aa_check.isChecked()
        resample = Image.LANCZOS if use_aa else Image.NEAREST
        for suffix, orig_size in EXPORT_SIZES_CURSOR.items():
            size = round(orig_size * mult)
            filename = f"{base}.tga" if suffix == "base" else f"{base}_{suffix}.tga"
            out_path = os.path.join(out_dir, filename)
            try:
                resized = panel.source_image.resize((size, size), resample=resample)
                resized.save(out_path, format="TGA")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save {filename}:\n{e}")
                return False
        return True

    def _generate_config(self, out_dir: str):
        cfg_path = os.path.join(out_dir, "bzgame_init_cursor.cfg")
        def_base = f"{self.panel_default.base_name_input.text().strip()}.tga"
        hl_base = f"{self.panel_highlight.base_name_input.text().strip()}.tga"
        content = f"""// ================================
// BATTLEZONE EDITOR INITIALIZATION
// ================================
//
// CONFIGURE CURSORS
//
ConfigureCursors()
{{
    CreateCursor("Default")
    {{
        Size(32, 32);
        Hotspot({self.panel_default.hotspot_x.value()}, {self.panel_default.hotspot_y.value()});
        Image("{def_base}");
        Frames(0, 63);
        FrameRate({self.panel_default.fps_spin.value()});
    }}

    CreateCursor("Highlight")
    {{
        Size(32, 32);
        Hotspot({self.panel_highlight.hotspot_x.value()}, {self.panel_highlight.hotspot_y.value()});
        Image("{hl_base}");
        Frames(0, 63);
        FrameRate({self.panel_highlight.fps_spin.value()});
    }}

    StandardCursors()
    {{
        Default("Default");
        IBeam("Default");
        Wait("Default");
        No("Default");
    }}
}}
"""
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Config not written:\n{e}")

    # ---------- export path persistence ----------
    def save_settings(self):
        s = QSettings("BZCC_Modding", "CursorTool")
        s.setValue("export_dir", self.target_dir.text())

    def load_settings(self):
        s = QSettings("BZCC_Modding", "CursorTool")
        self.target_dir.setText(s.value("export_dir", ""))

# ======================================================================
# Sprite Generator tab (single preview)
# ======================================================================
class SpriteGeneratorTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.source_image: Optional[Image.Image] = None
        self.source_mtime: Optional[float] = None
        self._watch_timer = QTimer(self); self._watch_timer.timeout.connect(self._poll_source)
        self._watch_timer.start(1000)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_watcher_triggered)
        self.processing = ImageProcessingPanel("Sprite FX")
        self._init_ui()
        self.load_settings()

    def _init_ui(self):
        main = QVBoxLayout(); main.setSpacing(4)

        # top bar
        top = QHBoxLayout()
        self.source_path_edit = QLineEdit(); self.source_path_edit.setPlaceholderText("Source image...")
        self.btn_browse_source = QPushButton("Browse"); self.btn_browse_source.clicked.connect(self._browse_source)
        top.addWidget(QLabel("Source:")); top.addWidget(self.source_path_edit, 1); top.addWidget(self.btn_browse_source)
        self.target_dir_edit = QLineEdit(); self.target_dir_edit.setPlaceholderText("Target folder...")
        self.btn_browse_target = QPushButton("Browse"); self.btn_browse_target.clicked.connect(self._browse_target)
        top.addWidget(QLabel("Target:")); top.addWidget(self.target_dir_edit, 1); top.addWidget(self.btn_browse_target)
        main.addLayout(top)

        # body
        body = QHBoxLayout()
        # left controls
        left = QVBoxLayout()
        controls = QGroupBox("Settings")
        form = QFormLayout()
        self.export_format_cb = QComboBox(); self.export_format_cb.addItems(EXPORT_FORMATS)
        form.addRow("Export:", self.export_format_cb)
        self.dds_format_cb = QComboBox(); self.dds_format_cb.addItems(DDS_FORMATS)
        form.addRow("DDS mode:", self.dds_format_cb)
        self.base_name_edit = QLineEdit("colorize"); form.addRow("Base name:", self.base_name_edit)
        self.auto_reload_cb = QCheckBox("Auto-reload"); self.auto_reload_cb.setChecked(True); form.addRow(self.auto_reload_cb)
        self.overwrite_cb = QCheckBox("Overwrite"); self.overwrite_cb.setChecked(True); form.addRow(self.overwrite_cb)
        self.force_gray_cb = QCheckBox("Force grayscale"); self.force_gray_cb.setChecked(True); self.force_gray_cb.stateChanged.connect(self._schedule_preview); form.addRow(self.force_gray_cb)
        self.keep_alpha_cb = QCheckBox("Keep alpha"); self.keep_alpha_cb.setChecked(True); self.keep_alpha_cb.stateChanged.connect(self._schedule_preview); form.addRow(self.keep_alpha_cb)
        self.invert_cb = QCheckBox("Invert"); self.invert_cb.stateChanged.connect(self._schedule_preview); form.addRow(self.invert_cb)
        self.normalize_cb = QCheckBox("Normalize"); self.normalize_cb.stateChanged.connect(self._schedule_preview); form.addRow(self.normalize_cb)
        self.antialias_cb = QCheckBox("Antialias"); self.antialias_cb.setChecked(True); self.antialias_cb.stateChanged.connect(self._schedule_preview); form.addRow(self.antialias_cb)
        self.resample_cb = QComboBox(); self.resample_cb.addItems(RESAMPLE_NAMES); self.resample_cb.setCurrentText("Lanczos"); self.resample_cb.currentTextChanged.connect(self._schedule_preview)
        form.addRow("Resample:", self.resample_cb)
        controls.setLayout(form)

        # processing sliders
        for slider in [self.processing.sharpen_slider, self.processing.blur_slider,
                       self.processing.gamma_slider, self.processing.brightness_slider,
                       self.processing.contrast_slider, self.processing.opacity_slider,
                       self.processing.edge_slider, self.processing.denoise_slider]:
            slider.valueChanged.connect(self._schedule_preview)

        # variants
        variants_group = QGroupBox("Export sizes")
        var_layout = QVBoxLayout()
        self.variant_checks: Dict[str, QCheckBox] = {}
        row_w = QWidget(); row_l = QHBoxLayout()
        for i, (key, size) in enumerate(VARIANTS_SPRITE):
            cb = QCheckBox(f"{key} ({size}×{size})"); cb.setChecked(True)
            self.variant_checks[key] = cb
            row_l.addWidget(cb)
            if (i+1)%2 == 0:
                var_layout.addWidget(row_w)
                row_w = QWidget(); row_l = QHBoxLayout()
        if len(VARIANTS_SPRITE)%2 != 0: var_layout.addWidget(row_w)
        variants_group.setLayout(var_layout)

        left_scroll = QScrollArea(); left_scroll.setWidgetResizable(True)
        left_container = QWidget(); left_container.setLayout(QVBoxLayout())
        left_container.layout().addWidget(controls)
        left_container.layout().addWidget(self.processing)
        left_container.layout().addWidget(variants_group)
        left_scroll.setWidget(left_container)
        left.addWidget(left_scroll)

        # right: one preview + log
        right = QVBoxLayout()
        self.result_preview = QLabel()
        self.result_preview.setFixedSize(400, 400)
        self.result_preview.setStyleSheet("background-color: #222; border: 1px solid #555;")
        right.addWidget(self.result_preview, alignment=Qt.AlignCenter)

        self.log_text = QTextEdit(); self.log_text.setReadOnly(True)
        right.addWidget(QLabel("Log:")); right.addWidget(self.log_text)

        btn_row = QHBoxLayout()
        self.btn_reload = QPushButton("Reload"); self.btn_reload.clicked.connect(self.reload_source)
        self.btn_export = QPushButton("EXPORT"); self.btn_export.setStyleSheet("font-weight: bold; background-color: #5a2a2a;")
        self.btn_export.clicked.connect(self.export_all)
        self.btn_open_target = QPushButton("Open target"); self.btn_open_target.clicked.connect(self._open_target)
        self.btn_open_source = QPushButton("Open source"); self.btn_open_source.clicked.connect(self._open_source)
        btn_row.addWidget(self.btn_reload); btn_row.addWidget(self.btn_export)
        btn_row.addWidget(self.btn_open_target); btn_row.addWidget(self.btn_open_source)
        right.addLayout(btn_row)

        body.addLayout(left, 1); body.addLayout(right, 2)
        main.addLayout(body)
        self.setLayout(main)

    def _schedule_preview(self):
        if not hasattr(self, "_preview_timer"):
            self._preview_timer = QTimer(self); self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self.refresh_preview)
        self._preview_timer.start(150)

    def _browse_source(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose source", "",
                                              "Images (*.png *.tga *.dds *.bmp *.jpg *.jpeg *.webp)")
        if path: self.source_path_edit.setText(path); self.reload_source()

    def _browse_target(self):
        path = QFileDialog.getExistingDirectory(self, "Choose target folder")
        if path: self.target_dir_edit.setText(path)

    def _open_target(self):
        folder = self.target_dir_edit.text().strip()
        if not folder or not os.path.isdir(folder): QMessageBox.information(self, "Info", "No target folder set."); return
        self._open_in_os(folder)

    def _open_source(self):
        path = self.source_path_edit.text().strip()
        if not path or not os.path.exists(path): QMessageBox.information(self, "Info", "Source file not found."); return
        self._open_in_os(path)

    @staticmethod
    def _open_in_os(path: str):
        try:
            if sys.platform.startswith("win"): os.startfile(path)
            elif sys.platform == "darwin": subprocess.Popen(["open", path])
            else: subprocess.Popen(["xdg-open", path])
        except Exception as e: QMessageBox.warning(None, "Error", f"Cannot open: {e}")

    def _poll_source(self):
        if not self.auto_reload_cb.isChecked(): return
        path = self.source_path_edit.text().strip()
        if not path or not os.path.isfile(path): return
        try:
            mtime = os.path.getmtime(path)
            if self.source_mtime is None: self.source_mtime = mtime
            elif mtime != self.source_mtime:
                self.source_mtime = mtime; self._log("Source changed, reloading..."); self.reload_source()
        except Exception: pass

    def _on_watcher_triggered(self, path):
        QTimer.singleShot(300, self.reload_source)

    def reload_source(self):
        path = self.source_path_edit.text().strip()
        if not path or not os.path.isfile(path): self._log("No valid source file."); return
        try:
            self.source_image = self._load_image(Path(path))
            self.source_mtime = os.path.getmtime(path)
            self._log(f"Loaded: {os.path.basename(path)}")
            self.refresh_preview()
            if self.watcher.files(): self.watcher.removePaths(self.watcher.files())
            self.watcher.addPath(path)
        except Exception as e: self._log(f"Load error: {e}")

    def _load_image(self, p: Path) -> Image.Image:
        suffix = p.suffix.lower()
        if suffix == ".dds":
            try: return Image.open(p).convert("RGBA")
            except Exception:
                texconv = find_texconv()
                if not texconv: raise RuntimeError("texconv not found for DDS import.")
                with tempfile.TemporaryDirectory() as td:
                    out_dir = Path(td)
                    subprocess.run([str(texconv), "-ft", "png", "-o", str(out_dir), str(p)],
                                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    pngs = list(out_dir.glob("*.png"))
                    if not pngs: raise RuntimeError("texconv produced no PNG.")
                    return Image.open(pngs[0]).convert("RGBA")
        return Image.open(p).convert("RGBA")

    def process_image(self, img: Image.Image, size: Optional[int] = None) -> Image.Image:
        s = self._current_settings()
        base = img.convert("RGBA")
        if s.force_grayscale:
            if s.keep_alpha:
                r, g, b, a = base.split()
                gray = ImageOps.grayscale(Image.merge("RGB", (r, g, b))).convert("L")
                base = Image.merge("RGBA", (gray, gray, gray, a))
            else:
                gray = ImageOps.grayscale(base).convert("L")
                base = Image.merge("RGBA", (gray, gray, gray, Image.new("L", gray.size, 255)))
        if s.invert:
            if s.keep_alpha:
                r, g, b, a = base.split(); gray = ImageOps.invert(r)
                base = Image.merge("RGBA", (gray, gray, gray, a))
            else:
                r, g, b, a = base.split(); gray = ImageOps.invert(r)
                base = Image.merge("RGBA", (gray, gray, gray, a))
        if s.normalize_levels: base = self._normalize_levels(base, s.keep_alpha)
        vals = self.processing.get_values()
        if vals["gamma"] != 1.0: base = self._apply_gamma(base, vals["gamma"])
        if vals["brightness"] != 1.0: base = ImageEnhance.Brightness(base).enhance(vals["brightness"])
        if vals["contrast"] != 1.0: base = ImageEnhance.Contrast(base).enhance(vals["contrast"])
        if vals["opacity"] < 1.0: base = self._apply_opacity(base, vals["opacity"])
        if vals["blur"] > 0: base = base.filter(ImageFilter.GaussianBlur(radius=float(vals["blur"])))
        if vals["edge_enhance"] > 0:
            enhanced = base.filter(ImageFilter.EDGE_ENHANCE_MORE)
            base = Image.blend(base, enhanced, min(1.0, float(vals["edge_enhance"])/2.0))
        if vals["denoise"] > 0: base = base.filter(ImageFilter.MedianFilter(size=max(3, int(1+round(float(vals["denoise"]))*2))))
        if vals["sharpen"] > 0:
            amount = float(vals["sharpen"])
            sharpened = base.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(70+amount*120), threshold=2))
            base = Image.blend(base, sharpened, min(1.0, amount/3.0))
        if size is not None and base.size != (size, size):
            resample = RESAMPLE_MAP.get(s.resample, RESAMPLE.LANCZOS) if s.antialias else RESAMPLE.NEAREST
            base = self._resize_square(base, size, resample)
        return base

    def _current_settings(self):
        class S: pass
        s = S()
        s.force_grayscale = self.force_gray_cb.isChecked()
        s.keep_alpha = self.keep_alpha_cb.isChecked()
        s.invert = self.invert_cb.isChecked()
        s.normalize_levels = self.normalize_cb.isChecked()
        s.antialias = self.antialias_cb.isChecked()
        s.resample = self.resample_cb.currentText()
        return s

    @staticmethod
    def _resize_square(img, size, resample):
        if img.width != img.height:
            side = min(img.width, img.height)
            left = (img.width - side)//2; top = (img.height - side)//2
            img = img.crop((left, top, left+side, top+side))
        return img.resize((size, size), resample=resample)

    @staticmethod
    def _normalize_levels(img, keep_alpha):
        r, g, b, a = img.split()
        gray = r
        if gray.getbbox() is None: return img
        gray = ImageOps.autocontrast(gray)
        if keep_alpha: return Image.merge("RGBA", (gray, gray, gray, a))
        return Image.merge("RGBA", (gray, gray, gray, Image.new("L", gray.size, 255)))

    @staticmethod
    def _apply_gamma(img, gamma):
        if gamma <= 0: return img
        inv = 1.0/gamma
        lut = [min(255, max(0, int((i/255.0)**inv*255.0+0.5))) for i in range(256)]
        r, g, b, a = img.split()
        return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))

    @staticmethod
    def _apply_opacity(img, opacity):
        opacity = max(0.0, min(1.0, opacity))
        r, g, b, a = img.split()
        a = a.point(lambda p: int(p*opacity))
        return Image.merge("RGBA", (r, g, b, a))

    def refresh_preview(self):
        if self.source_image is None:
            self.result_preview.clear(); return
        try:
            result = self.process_image(self.source_image.copy(), size=BASE_SIZE_SPRITE)
            thumb = self._thumbnail_with_checker(result, 400)
            self.result_preview.setPixmap(pil2pixmap(thumb))
        except Exception as e: self._log(f"Preview error: {e}")

    def _thumbnail_with_checker(self, img: Image.Image, max_size: int) -> Image.Image:
        thumb = img.copy()
        if thumb.width != thumb.height:
            side = min(thumb.width, thumb.height)
            left = (thumb.width - side)//2; top = (thumb.height - side)//2
            thumb = thumb.crop((left, top, left+side, top+side))
        thumb.thumbnail((max_size, max_size), RESAMPLE.LANCZOS)
        bg = Image.new("RGBA", thumb.size, (48,48,48,255))
        checker = Image.new("RGBA", thumb.size, (0,0,0,0))
        tile = 12
        for y in range(0, thumb.height, tile):
            for x in range(0, thumb.width, tile):
                if ((x//tile)+(y//tile))%2 == 0:
                    Image.Image.paste(checker, Image.new("RGBA", (min(tile, thumb.width-x), min(tile, thumb.height-y)), (72,72,72,255)), (x, y))
        bg = Image.alpha_composite(checker, thumb)
        return bg

    def export_all(self):
        if self.source_image is None: QMessageBox.information(self, "Info", "Load a source image first."); return
        out_dir = self.target_dir_edit.text().strip()
        if not out_dir: QMessageBox.information(self, "Info", "Choose a target folder."); return
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        base_name = self.base_name_edit.text().strip() or "colorize"
        export_format = self.export_format_cb.currentText().upper()
        overwrite = self.overwrite_cb.isChecked()
        mult = self.window().get_export_multiplier()
        try:
            written = []
            base_size = round(BASE_SIZE_SPRITE * mult)
            processed = self.process_image(self.source_image.copy(), size=base_size)
            self._write_one(processed, out_dir, base_name, export_format, overwrite)
            written.append(f"{base_name}.{export_format.lower()}")
            for key, orig_size in VARIANTS_SPRITE:
                if not self.variant_checks[key].isChecked(): continue
                out_size = round(orig_size * mult)
                img = self.process_image(self.source_image.copy(), size=out_size)
                name = f"{base_name}_{key}"
                self._write_one(img, out_dir, name, export_format, overwrite)
                written.append(f"{name}.{export_format.lower()}")
            self._log("Export done:")
            for w in written: self._log(f"  {w}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def _write_one(self, img, out_dir, name, fmt, overwrite):
        out_dir_p = Path(out_dir)
        fmt = fmt.upper()
        if fmt == "DDS": self._export_dds(img, out_dir_p, name, overwrite)
        elif fmt == "TGA":
            out_path = out_dir_p / f"{name}.tga"
            if out_path.exists() and not overwrite: raise FileExistsError(str(out_path))
            img.save(out_path, format="TGA")
        else:
            out_path = out_dir_p / f"{name}.png"
            if out_path.exists() and not overwrite: raise FileExistsError(str(out_path))
            img.save(out_path, format="PNG")

    def _export_dds(self, img, out_dir, name, overwrite):
        out_path = out_dir / f"{name}.dds"
        if out_path.exists() and not overwrite: raise FileExistsError(str(out_path))
        texconv = find_texconv()
        if texconv is None:
            try: img.save(out_path, format="DDS"); return
            except Exception as e: raise RuntimeError("texconv not found and PIL cannot write DDS.") from e
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            src_png = td_path / f"{name}.png"
            img.save(src_png, format="PNG")
            dds_fmt = self.dds_format_cb.currentText().strip() or "DXT5"
            cmd = [str(texconv), "-y", "-nologo", "-ft", "dds", "-f", dds_fmt, "-o", str(td_path), str(src_png)]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode != 0: raise RuntimeError(f"texconv failed:\n{proc.stdout}\n{proc.stderr}")
            produced = list(td_path.glob("*.dds"))
            if not produced: raise RuntimeError("texconv produced no DDS.")
            shutil.copy2(produced[0], out_path)

    def _log(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {msg}")

    # ---------- persistence ----------
    def save_settings(self):
        s = QSettings("BZCC_Modding", "SpriteGen")
        s.setValue("source_path", self.source_path_edit.text())
        s.setValue("target_dir", self.target_dir_edit.text())
        s.setValue("export_format", self.export_format_cb.currentText())
        s.setValue("dds_format", self.dds_format_cb.currentText())
        s.setValue("base_name", self.base_name_edit.text())
        s.setValue("auto_reload", self.auto_reload_cb.isChecked())
        s.setValue("overwrite", self.overwrite_cb.isChecked())
        s.setValue("force_gray", self.force_gray_cb.isChecked())
        s.setValue("keep_alpha", self.keep_alpha_cb.isChecked())
        s.setValue("invert", self.invert_cb.isChecked())
        s.setValue("normalize", self.normalize_cb.isChecked())
        s.setValue("antialias", self.antialias_cb.isChecked())
        s.setValue("resample", self.resample_cb.currentText())
        vals = self.processing.get_values()
        for k, v in vals.items(): s.setValue(k, v)
        for key, cb in self.variant_checks.items():
            s.setValue(f"variant_{key}", cb.isChecked())

    def load_settings(self):
        s = QSettings("BZCC_Modding", "SpriteGen")
        self.source_path_edit.setText(s.value("source_path", ""))
        self.target_dir_edit.setText(s.value("target_dir", ""))
        fmt = s.value("export_format", "DDS")
        if fmt in EXPORT_FORMATS: self.export_format_cb.setCurrentText(fmt)
        dds = s.value("dds_format", "DXT5")
        if dds in DDS_FORMATS: self.dds_format_cb.setCurrentText(dds)
        self.base_name_edit.setText(s.value("base_name", "colorize"))
        self.auto_reload_cb.setChecked(s.value("auto_reload", True) in ("true", True))
        self.overwrite_cb.setChecked(s.value("overwrite", True) in ("true", True))
        self.force_gray_cb.setChecked(s.value("force_gray", True) in ("true", True))
        self.keep_alpha_cb.setChecked(s.value("keep_alpha", True) in ("true", True))
        self.invert_cb.setChecked(s.value("invert", False) in ("true", True))
        self.normalize_cb.setChecked(s.value("normalize", False) in ("true", True))
        self.antialias_cb.setChecked(s.value("antialias", True) in ("true", True))
        res = s.value("resample", "Lanczos")
        self.resample_cb.setCurrentText(res if res in RESAMPLE_NAMES else "Lanczos")
        vals = {}
        for key in ["sharpen","blur","gamma","brightness","contrast","opacity","edge_enhance","denoise"]:
            default = 0.0
            if key in ("gamma","brightness","contrast","opacity"): default = 1.0
            vals[key] = float(s.value(key, default))
        self.processing.set_values(vals)
        for key, cb in self.variant_checks.items():
            val = s.value(f"variant_{key}", True)
            cb.setChecked(val in ("true", True))
        if self.source_path_edit.text(): self.reload_source()

# ======================================================================
# Main window
# ======================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BZCC Sprite Generator")
        self.resize(1600, 900)
        self.export_multiplier = DEFAULT_MULTIPLIER

        self._setup_dark_theme()
        central = QWidget(); self.setCentralWidget(central)
        layout = QVBoxLayout(); layout.setSpacing(4)

        # Multiplier buttons
        mult_group = QGroupBox("Export Size Multiplier")
        mult_layout = QHBoxLayout()
        mult_layout.addWidget(QLabel("Scale all outputs by:"))
        self.mult_buttons = QButtonGroup(self)
        for val in MULTIPLIER_PRESETS:
            btn = QPushButton(f"×{val}"); btn.setCheckable(True); btn.setFixedWidth(40)
            if val == DEFAULT_MULTIPLIER: btn.setChecked(True)
            self.mult_buttons.addButton(btn, val)
            mult_layout.addWidget(btn)
        mult_layout.addStretch()
        self.mult_buttons.buttonClicked[int].connect(self._set_multiplier)
        mult_group.setLayout(mult_layout)
        layout.addWidget(mult_group)

        # Tabs
        self.tabs = QTabWidget()
        self.cursor_tab = CursorBakerTab()
        self.sprite_tab = SpriteGeneratorTab()
        self.tabs.addTab(self.cursor_tab, "Cursor Baker")
        self.tabs.addTab(self.sprite_tab, "Sprite Generator")
        layout.addWidget(self.tabs)

        central.setLayout(layout)

        s = QSettings("BZCC_Modding", "UnifiedTool")
        saved_mult = int(s.value("global_multiplier", DEFAULT_MULTIPLIER))
        if saved_mult in MULTIPLIER_PRESETS:
            self._set_multiplier(saved_mult)
            btn = self.mult_buttons.button(saved_mult)
            if btn: btn.setChecked(True)

    def _setup_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #1e1e1e; color: #d4d4d4; }
            QGroupBox { font-weight: bold; border: 1px solid #444; border-radius: 5px; margin-top: 10px; padding-top: 15px; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 5px; }
            QPushButton { background-color: #3a3d41; border: 1px solid #555; padding: 5px; border-radius: 3px; }
            QPushButton:checked { background-color: #5a5d61; border: 2px solid #aaa; }
            QPushButton:hover { background-color: #4a4d51; }
            QLineEdit, QSpinBox, QTextEdit, QComboBox { background-color: #2d2d30; border: 1px solid #555; color: #fff; padding: 3px; }
            QToolTip { background-color: #2d2d30; color: #fff; border: 1px solid #555; }
            QSlider::groove:horizontal { background: #444; height: 6px; }
            QSlider::handle:horizontal { background: #888; width: 14px; margin: -4px 0; border-radius: 7px; }
            QTabWidget::pane { border: 1px solid #444; background: #1e1e1e; }
            QTabBar::tab { background: #2d2d30; color: #d4d4d4; padding: 8px 16px; border: 1px solid #555; }
            QTabBar::tab:selected { background: #3a3d41; border-bottom-color: #1e1e1e; }
            QTabBar::tab:hover { background: #4a4d51; }
        """)

    def _set_multiplier(self, val):
        self.export_multiplier = val
        QSettings("BZCC_Modding", "UnifiedTool").setValue("global_multiplier", val)

    def get_export_multiplier(self) -> int:
        return self.export_multiplier

    def closeEvent(self, event):
        # Save all sub‑tab settings
        self.cursor_tab.panel_default.save_settings()
        self.cursor_tab.panel_highlight.save_settings()
        self.cursor_tab.save_settings()          # export path
        self.sprite_tab.save_settings()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())