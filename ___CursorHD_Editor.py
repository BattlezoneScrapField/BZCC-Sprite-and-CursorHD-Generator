#!/usr/bin/env python3
"""
BZCC Sprite Generator
=====================
Unified tool for cursor sprite-sheets and sprite/colour-map generation.
Export dimensions use fixed presets for predictable output.
All settings (export paths, cursor names, etc.) are saved between sessions.
"""

import sys
import os
import shutil
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QLabel, QPushButton, QFileDialog, QSlider,
    QCheckBox, QSpinBox, QLineEdit, QTextEdit, QMessageBox,
    QComboBox, QScrollArea, QColorDialog, QButtonGroup, QGridLayout
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
# Constants
# ----------------------------------------------------------------------
GRID_SIZE = 8
FRAMES_TOTAL = 64
BASE_CURSOR_SIZE = 256

CURSOR_DIMENSIONS = {
    "base": 256,
    "x1_5": 384,
    "x2_0": 512,
    "x2_5": 640,
    "x3_0": 768,
    "x3_5": 896,
    "x4_0": 1024,
    "x4_5": 1152,
    "x5_0": 1280,
}

SPRITE_VARIANT_DIMENSIONS: List[Tuple[str, int]] = [
    ("x1_0", 256), ("x1_5", 384), ("x2_0", 512), ("x2_5", 640),
    ("x3_0", 768), ("x3_5", 896), ("x4_0", 1024), ("x4_5", 1152), ("x5_0", 1280),
]

EXPORT_FORMATS = ["DDS", "TGA", "PNG"]
DDS_FORMATS = ["DXT5", "BC3_UNORM", "BC3_UNORM_SRGB", "DXT3"]
RESAMPLE_NAMES = ["Nearest", "Bilinear", "Bicubic", "Lanczos"]
RESAMPLE_MAP = {
    "Nearest": RESAMPLE.NEAREST, "Bilinear": RESAMPLE.BILINEAR,
    "Bicubic": RESAMPLE.BICUBIC, "Lanczos": RESAMPLE.LANCZOS,
}

VALID_IMAGE_EXTS = frozenset({".png", ".tga", ".dds", ".jpg", ".jpeg", ".webp", ".bmp"})

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

def sanitize_filename_component(value: str, default: str = "asset") -> str:
    value = (value or "").strip()
    value = value.replace("\\", "_").replace("/", "_").replace(":", "_")
    value = _SAFE_NAME_RE.sub("_", value).strip("._- ")
    return value or default

def _natural_key(path: Path) -> Tuple:
    parts = re.split(r"(\d+)", path.name.lower())
    key = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part)
    return tuple(key)

def _coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default

def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default

def _coerce_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default

def _safe_remove_paths(watcher: QFileSystemWatcher, paths: List[str]):
    if not paths:
        return
    try:
        watcher.removePaths(paths)
    except Exception:
        for p in paths:
            try:
                watcher.removePath(p)
            except Exception:
                pass

def _safe_add_path(watcher: QFileSystemWatcher, path: str):
    if not path:
        return
    try:
        if path not in watcher.files() and path not in watcher.directories():
            watcher.addPath(path)
    except Exception:
        try:
            watcher.addPath(path)
        except Exception:
            pass

# ----------------------------------------------------------------------
# Core Utilities
# ----------------------------------------------------------------------
_CACHED_TEXCONV: Optional[Path] = None

def find_texconv() -> Optional[Path]:
    """Locate texconv executable, with caching and existence validation."""
    global _CACHED_TEXCONV
    if _CACHED_TEXCONV is not None:
        if _CACHED_TEXCONV.exists():
            return _CACHED_TEXCONV
        else:
            _CACHED_TEXCONV = None  # reset if missing

    env = shutil.which("texconv")
    if env:
        _CACHED_TEXCONV = Path(env)
        return _CACHED_TEXCONV

    here = Path(__file__).resolve().parent
    for name in ("texconv.exe", "texconv"):
        p = here / name
        if p.exists():
            _CACHED_TEXCONV = p
            return p
    return None

def export_dds_file(img: Image.Image, out_path: Path, dds_fmt: str, overwrite: bool):
    """Export image as DDS using texconv (or native PIL if available)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not overwrite:
        raise FileExistsError(f"File already exists: {out_path}")

    texconv = find_texconv()
    if texconv is None:
        try:
            img.save(out_path, format="DDS")
            return
        except Exception as e:
            raise RuntimeError("texconv not found and PIL cannot write DDS natively.") from e

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        src_png = td_path / f"{out_path.stem}_temp.png"
        img.save(src_png, format="PNG")
        cmd = [str(texconv), "-y", "-nologo", "-ft", "dds", "-f", dds_fmt, "-o", str(td_path), str(src_png)]

        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45)
            if proc.returncode != 0:
                raise RuntimeError(f"texconv failed:\n{proc.stdout}\n{proc.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("texconv process timed out after 45 seconds.")
        except Exception as ex:
            raise RuntimeError(f"Unexpected error executing texconv: {ex}")

        produced = list(td_path.glob("*.dds"))
        if not produced:
            raise RuntimeError("texconv produced no DDS output.")
        shutil.copy2(produced[0], out_path)

def get_last_dir(key: str) -> str:
    return QSettings("BZCC_Modding", "Paths").value(key, "")

def set_last_dir(key: str, path: str):
    if path:
        if os.path.isfile(path):
            path = os.path.dirname(path)
        QSettings("BZCC_Modding", "Paths").setValue(key, path)

def safe_load_image(path: Path, retries: int = 3, delay: float = 0.15) -> Image.Image:
    """
    Load image from disk with retry logic.
    For DDS files, fall back to texconv if PIL cannot open the format.
    """
    last_err = None
    for attempt in range(retries):
        try:
            suffix = path.suffix.lower()
            if suffix == ".dds":
                try:
                    with Image.open(path) as img:
                        img.load()
                        return img.convert("RGBA")
                except Exception:
                    texconv = find_texconv()
                    if not texconv:
                        raise RuntimeError("texconv is required for this DDS format.")
                    with tempfile.TemporaryDirectory() as td:
                        out_dir = Path(td)
                        try:
                            subprocess.run(
                                [str(texconv), "-ft", "png", "-o", str(out_dir), str(path)],
                                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=30
                            )
                        except subprocess.CalledProcessError as e:
                            raise RuntimeError(f"texconv execution failed: {e.stderr.decode('utf-8', 'ignore')}")
                        except subprocess.TimeoutExpired:
                            raise RuntimeError("texconv timed out extracting DDS.")

                        pngs = list(out_dir.glob("*.png"))
                        if not pngs:
                            raise RuntimeError("texconv export yielded empty sequence.")
                        with Image.open(pngs[0]) as img:
                            img.load()
                            return img.convert("RGBA")
            else:
                with Image.open(path) as img:
                    img.load()
                    return img.convert("RGBA")
        except Exception as e:
            last_err = e
            time.sleep(delay)
    raise RuntimeError(f"Failed to read image after {retries} retries: {last_err}")

# ----------------------------------------------------------------------
# Shared Image FX Logic
# ----------------------------------------------------------------------
def apply_gamma_to_image(img: Image.Image, gamma: float) -> Image.Image:
    if gamma <= 0 or gamma == 1.0:
        return img
    inv = 1.0 / gamma
    lut = [min(255, max(0, int((i / 255.0) ** inv * 255.0 + 0.5))) for i in range(256)]
    r, g, b, a = img.split()
    return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))

def apply_opacity_to_image(img: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1.0:
        return img
    opacity = max(0.0, opacity)
    r, g, b, a = img.split()
    a = a.point(lambda p: int(p * opacity))
    return Image.merge("RGBA", (r, g, b, a))

def normalize_image_levels(img: Image.Image, keep_alpha: bool) -> Image.Image:
    r, g, b, a = img.split()
    rgb = Image.merge("RGB", (r, g, b))
    if rgb.getbbox() is None:
        return img
    gray = ImageOps.autocontrast(ImageOps.grayscale(rgb))
    if keep_alpha:
        return Image.merge("RGBA", (gray, gray, gray, a))
    return Image.merge("RGBA", (gray, gray, gray, Image.new("L", gray.size, 255)))

def pil2pixmap(pil_img: Image.Image) -> QPixmap:
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qim = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGBA8888).copy()
    return QPixmap.fromImage(qim)

# ======================================================================
# Shared processing panel
# ======================================================================
class ImageProcessingPanel(QGroupBox):
    def __init__(self, title="Processing", parent=None):
        super().__init__(title, parent)
        layout = QFormLayout()
        layout.setVerticalSpacing(4)
        layout.setContentsMargins(8, 14, 8, 8)

        self.sharpen_slider = self._add_slider("Sharpen", 0.0, 3.0, 0.0, layout)
        self.blur_slider = self._add_slider("Blur", 0.0, 3.0, 0.0, layout)
        self.gamma_slider = self._add_slider("Gamma", 0.2, 3.0, 1.0, layout)
        self.brightness_slider = self._add_slider("Brightness", 0.2, 2.0, 1.0, layout)
        self.contrast_slider = self._add_slider("Contrast", 0.2, 2.0, 1.0, layout)
        self.opacity_slider = self._add_slider("Opacity", 0.0, 1.0, 1.0, layout)
        self.edge_slider = self._add_slider("Edge enhance", 0.0, 2.0, 0.0, layout)
        self.denoise_slider = self._add_slider("Denoise", 0.0, 3.0, 0.0, layout)

        self.reset_btn = QPushButton("Reset Values")
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.clicked.connect(self._reset_defaults)
        layout.addRow("", self.reset_btn)

        self.setLayout(layout)

    def _add_slider(self, name, min_val, max_val, default, layout):
        slider = QSlider(Qt.Horizontal)
        slider.setRange(int(min_val * 100), int(max_val * 100))
        slider.setValue(int(default * 100))
        slider.setCursor(Qt.PointingHandCursor)
        layout.addRow(QLabel(name), slider)
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
# Cursor panel
# ======================================================================
class CursorPanel(QGroupBox):
    def __init__(self, title: str, settings_prefix: str, parent=None):
        super().__init__(title, parent)
        self.settings_prefix = settings_prefix
        self.image_path = ""
        self.source_image: Optional[Image.Image] = None
        self.frames: List[QPixmap] = []
        self.current_frame = 0
        self.cell_size = 128
        self._is_sequence = False
        self._bg_mode = "checker"
        self._bg_color = QColor(39, 39, 42)
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
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(8, 14, 8, 8)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedSize(128, 128)
        self.preview_label.setStyleSheet("background-color: #09090b; border: 1px solid #27272a; border-radius: 6px;")
        main_layout.addWidget(self.preview_label, alignment=Qt.AlignCenter)

        info = QLabel("💡 1024, 2048, 4096 sheets or 64 frames")
        info.setWordWrap(True)
        info.setStyleSheet("color: #71717a; font-size: 8pt;")
        info.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(info)

        self.size_warning = QLabel("")
        self.size_warning.setAlignment(Qt.AlignCenter)
        self.size_warning.setWordWrap(True)
        main_layout.addWidget(self.size_warning)

        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel("BG Preview:"))
        self.bg_combo = QComboBox()
        self.bg_combo.addItems(["Checker", "Solid"])
        self.bg_combo.currentIndexChanged.connect(self._on_bg_changed)
        bg_row.addWidget(self.bg_combo)
        self.bg_color_btn = QPushButton("Color...")
        self.bg_color_btn.clicked.connect(self._pick_bg_color)
        bg_row.addWidget(self.bg_color_btn)
        bg_row.addStretch()
        main_layout.addLayout(bg_row)

        nav_layout = QHBoxLayout()
        nav_layout.setSpacing(4)
        self.btn_first = QPushButton("⏮"); self.btn_first.setFixedWidth(34)
        self.btn_first.clicked.connect(self._first_frame); nav_layout.addWidget(self.btn_first)
        self.btn_prev = QPushButton("◀"); self.btn_prev.setFixedWidth(34)
        self.btn_prev.clicked.connect(self._prev_frame); nav_layout.addWidget(self.btn_prev)
        self.btn_play = QPushButton("▶"); self.btn_play.setFixedWidth(34)
        self.btn_play.clicked.connect(self._toggle_anim); nav_layout.addWidget(self.btn_play)
        self.btn_next = QPushButton("▶"); self.btn_next.setFixedWidth(34)
        self.btn_next.clicked.connect(self._next_frame); nav_layout.addWidget(self.btn_next)
        self.btn_last = QPushButton("⏭"); self.btn_last.setFixedWidth(34)
        self.btn_last.clicked.connect(self._last_frame); nav_layout.addWidget(self.btn_last)

        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setRange(0, FRAMES_TOTAL - 1)
        self.frame_slider.setValue(0)
        self.frame_slider.valueChanged.connect(self._on_slider_changed)
        nav_layout.addWidget(self.frame_slider, 1)

        self.frame_label = QLabel("0 / 63")
        self.frame_label.setMinimumWidth(52)
        self.frame_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        nav_layout.addWidget(self.frame_label)
        main_layout.addLayout(nav_layout)

        load_row = QHBoxLayout()
        load_row.setSpacing(6)
        self.btn_load_sheet = QPushButton("Load Sheet"); self.btn_load_sheet.clicked.connect(self.load_sheet_dialog)
        self.btn_load_seq = QPushButton("Load Sequence"); self.btn_load_seq.clicked.connect(self.load_sequence_dialog)
        self.btn_clear = QPushButton("Clear"); self.btn_clear.clicked.connect(self.clear_image)
        load_row.addWidget(self.btn_load_sheet); load_row.addWidget(self.btn_load_seq)
        load_row.addWidget(self.btn_clear)
        main_layout.addLayout(load_row)

        set_row = QHBoxLayout()
        set_row.setSpacing(4)
        set_row.addWidget(QLabel("Name:"))
        self.base_name_input = QLineEdit("cursorHD")
        self.base_name_input.setMinimumWidth(100)
        set_row.addWidget(self.base_name_input)
        set_row.addWidget(QLabel("HX:"))
        self.hotspot_x = QSpinBox(); self.hotspot_x.setRange(0, 512); self.hotspot_x.setValue(5)
        self.hotspot_x.setMinimumWidth(64); set_row.addWidget(self.hotspot_x)
        set_row.addWidget(QLabel("HY:"))
        self.hotspot_y = QSpinBox(); self.hotspot_y.setRange(0, 512); self.hotspot_y.setValue(10)
        self.hotspot_y.setMinimumWidth(64); set_row.addWidget(self.hotspot_y)
        set_row.addWidget(QLabel("FPS:"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 144)
        self.fps_spin.setValue(60)
        self.fps_spin.setMinimumWidth(64)
        self.fps_spin.valueChanged.connect(self._refresh_animation_timing)
        set_row.addWidget(self.fps_spin)
        self.aa_check = QCheckBox("AA"); self.aa_check.setChecked(True)
        set_row.addWidget(self.aa_check)
        set_row.addStretch()
        main_layout.addLayout(set_row)

        for slider in [self.processing.sharpen_slider, self.processing.blur_slider,
                       self.processing.gamma_slider, self.processing.brightness_slider,
                       self.processing.contrast_slider, self.processing.opacity_slider,
                       self.processing.edge_slider, self.processing.denoise_slider]:
            slider.valueChanged.connect(self._schedule_process)
        main_layout.addWidget(self.processing)

        self.setLayout(main_layout)

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
        visual_size = 128
        scaled_pix = frame_pix.scaled(visual_size, visual_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        if self._bg_mode == "checker":
            checker = QPixmap(visual_size, visual_size)
            checker.fill(QColor(24, 24, 27))
            painter = QPainter(checker)
            tile = 16
            light = QColor(39, 39, 42)
            for y in range(0, visual_size, tile):
                for x in range(0, visual_size, tile):
                    if ((x // tile) + (y // tile)) % 2 == 0:
                        painter.fillRect(x, y, tile, tile, light)
            painter.end()

            result = QPixmap(visual_size, visual_size)
            result.fill(Qt.transparent)
            p2 = QPainter(result)
            p2.drawPixmap(0, 0, checker)
            p2.drawPixmap((visual_size - scaled_pix.width()) // 2, (visual_size - scaled_pix.height()) // 2, scaled_pix)
            p2.end()
            return result

        result = QPixmap(visual_size, visual_size)
        result.fill(self._bg_color)
        painter = QPainter(result)
        painter.drawPixmap((visual_size - scaled_pix.width()) // 2, (visual_size - scaled_pix.height()) // 2, scaled_pix)
        painter.end()
        return result

    def _update_preview_with_bg(self):
        if not self.frames:
            self.preview_label.clear()
            return
        pix = self.frames[self.current_frame]
        self.preview_label.setPixmap(self._make_preview_pixmap(pix))

    def _refresh_animation_timing(self):
        if self._anim_running and self.anim_timer.isActive():
            self.anim_timer.start(self._current_animation_interval())

    def _current_animation_interval(self) -> int:
        return max(1, int(round(1000 / max(1, self.fps_spin.value()))))

    def _toggle_anim(self):
        if self._anim_running:
            self.anim_timer.stop()
            self.btn_play.setText("▶")
            self._anim_running = False
            return

        if not self.frames:
            return

        self.anim_timer.start(self._current_animation_interval())
        self.btn_play.setText("⏸")
        self._anim_running = True

    def _advance_frame(self):
        if not self.frames:
            return
        self.current_frame = (self.current_frame + 1) % FRAMES_TOTAL
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(self.current_frame)
        self.frame_slider.blockSignals(False)
        self.frame_label.setText(f"{self.current_frame} / 63")
        self._update_preview_with_bg()

    def _prev_frame(self):
        if not self.frames:
            return
        self._jump_to(self.current_frame - 1)

    def _next_frame(self):
        if not self.frames:
            return
        self._jump_to(self.current_frame + 1)

    def _first_frame(self):
        if not self.frames:
            return
        self._jump_to(0)

    def _last_frame(self):
        if not self.frames:
            return
        self._jump_to(FRAMES_TOTAL - 1)

    def _jump_to(self, idx):
        if not self.frames:
            return
        idx = idx % FRAMES_TOTAL
        self.current_frame = idx
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(idx)
        self.frame_slider.blockSignals(False)
        self.frame_label.setText(f"{idx} / 63")
        self._update_preview_with_bg()

    def _on_slider_changed(self, val):
        if not self.frames:
            return
        self.current_frame = val
        self.frame_label.setText(f"{val} / 63")
        self._update_preview_with_bg()

    def _set_watched_path(self, path: str):
        _safe_remove_paths(self.watcher, self.watcher.files())
        _safe_remove_paths(self.watcher, self.watcher.directories())
        _safe_add_path(self.watcher, path)

    def clear_image(self):
        self._process_timer.stop()
        self.source_image = None
        self.frames.clear()
        self.current_frame = 0
        self.image_path = ""
        self._is_sequence = False
        self.preview_label.clear()
        self.size_warning.setText("")
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)
        self.frame_label.setText("0 / 63")
        if self.anim_timer.isActive():
            self.anim_timer.stop()
        self.btn_play.setText("▶")
        self._anim_running = False
        _safe_remove_paths(self.watcher, self.watcher.files())
        _safe_remove_paths(self.watcher, self.watcher.directories())
        self.save_settings()

    def load_sheet_dialog(self):
        start_dir = get_last_dir("cursor_sheet_dir")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Sprite Sheet", start_dir,
            "Images (*.png *.tga *.dds *.jpg *.jpeg *.webp *.bmp)")
        if path:
            set_last_dir("cursor_sheet_dir", path)
            self._is_sequence = False
            self.load_image(path)

    def load_sequence_dialog(self):
        start_dir = get_last_dir("cursor_seq_dir")
        folder = QFileDialog.getExistingDirectory(self, "Select Folder with 64 Frames", start_dir)
        if not folder:
            return
        set_last_dir("cursor_seq_dir", folder)
        try:
            img = self.build_sprite_sheet_from_sequence(folder)
            self.source_image = img
            self._is_sequence = True
            self.image_path = folder
            self._set_watched_path(folder)
            self._schedule_process()
            self.save_settings()
        except Exception as e:
            QMessageBox.critical(self, "Sequence Error", str(e))

    def load_image(self, path: str):
        if not path or not os.path.exists(path):
            return
        if self.image_path:
            _safe_remove_paths(self.watcher, [self.image_path])
        self.image_path = path
        self._set_watched_path(self.image_path)
        self._schedule_process()
        self.save_settings()

    def build_sprite_sheet_from_sequence(self, folder: str) -> Image.Image:
        folder_path = Path(folder)
        files = sorted([f for f in folder_path.iterdir() if f.suffix.lower() in VALID_IMAGE_EXTS], key=_natural_key)
        if len(files) != FRAMES_TOTAL:
            raise ValueError(f"Expected {FRAMES_TOTAL} frames, found {len(files)}.")

        first_img = safe_load_image(files[0])
        cw, ch = first_img.size

        if cw <= 0 or ch <= 0:
            raise ValueError("Sequence frame has invalid dimensions.")
        if cw != ch:
            raise ValueError("Sequence frames must be square.")

        self.cell_size = cw
        sheet_dim = cw * GRID_SIZE
        sheet = Image.new("RGBA", (sheet_dim, sheet_dim))
        sheet.paste(first_img, (0, 0))

        for idx, fpath in enumerate(files[1:], start=1):
            try:
                frame_img = safe_load_image(fpath)
            except Exception as e:
                raise ValueError(f"Error loading {fpath.name}: {e}")

            if frame_img.size != (self.cell_size, self.cell_size):
                frame_img = frame_img.resize((self.cell_size, self.cell_size), RESAMPLE.LANCZOS)
            row = idx // GRID_SIZE
            col = idx % GRID_SIZE
            x = col * self.cell_size
            y = row * self.cell_size
            sheet.paste(frame_img, (x, y))
            QApplication.processEvents()

        return sheet

    def on_file_changed(self, path: str):
        self._process_timer.start(500)

    def _schedule_process(self):
        self._process_timer.start(200)

    def _do_process_source(self):
        if not self.image_path or not os.path.exists(self.image_path):
            return
        try:
            if self._is_sequence:
                img = self.build_sprite_sheet_from_sequence(self.image_path)
            else:
                img = safe_load_image(Path(self.image_path))

            if img.width <= 0 or img.height <= 0:
                raise ValueError("Image has invalid dimensions.")

            self.cell_size = max(1, img.width // GRID_SIZE)
            img = self.apply_processing(img)

            if img.width != img.height:
                self.size_warning.setText(f"Warning: Not square ({img.width}x{img.height})")
                self.size_warning.setStyleSheet("color: #ef4444;")
            else:
                self.size_warning.setText(f"Size OK: {img.width}x{img.height}")
                self.size_warning.setStyleSheet("color: #10b981;")

            self.source_image = img
            self.extract_frames()
            self._update_preview_with_bg()
            self.frame_slider.blockSignals(True)
            self.frame_slider.setValue(0)
            self.frame_slider.blockSignals(False)
            self.frame_label.setText("0 / 63")
        except Exception as e:
            self.size_warning.setText(f"Error: {e}")
            self.size_warning.setStyleSheet("color: #ef4444;")

    def apply_processing(self, img: Image.Image) -> Image.Image:
        vals = self.processing.get_values()
        base = img.convert("RGBA")
        base = apply_gamma_to_image(base, vals["gamma"])

        if vals["brightness"] != 1.0:
            base = ImageEnhance.Brightness(base).enhance(vals["brightness"])
        if vals["contrast"] != 1.0:
            base = ImageEnhance.Contrast(base).enhance(vals["contrast"])

        base = apply_opacity_to_image(base, vals["opacity"])

        if vals["blur"] > 0:
            base = base.filter(ImageFilter.GaussianBlur(radius=float(vals["blur"])))
        if vals["edge_enhance"] > 0:
            enhanced = base.filter(ImageFilter.EDGE_ENHANCE_MORE)
            base = Image.blend(base, enhanced, min(1.0, float(vals["edge_enhance"]) / 2.0))
        if vals["denoise"] > 0:
            base = base.filter(ImageFilter.MedianFilter(size=max(3, int(1 + round(float(vals["denoise"])) * 2))))
        if vals["sharpen"] > 0:
            amount = float(vals["sharpen"])
            sharpened = base.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(70 + amount * 120), threshold=2))
            base = Image.blend(base, sharpened, min(1.0, amount / 3.0))
        return base

    def extract_frames(self):
        if self.source_image is None:
            return
        self.frames.clear()
        self.cell_size = max(1, self.source_image.width // GRID_SIZE)
        for idx in range(FRAMES_TOTAL):
            row = idx // GRID_SIZE
            col = idx % GRID_SIZE
            x = col * self.cell_size
            y = row * self.cell_size
            frame = self.source_image.crop((x, y, x + self.cell_size, y + self.cell_size))
            self.frames.append(pil2pixmap(frame))
        self.current_frame = 0

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
        path = str(s.value(f"{self.settings_prefix}_path", ""))
        self._is_sequence = _coerce_bool(s.value(f"{self.settings_prefix}_is_sequence", False), False)
        if path and os.path.exists(path):
            if self._is_sequence:
                try:
                    self.source_image = self.build_sprite_sheet_from_sequence(path)
                    self.image_path = path
                    self._set_watched_path(path)
                    self._schedule_process()
                except Exception:
                    pass
            else:
                self.load_image(path)
        self.fps_spin.setValue(_coerce_int(s.value(f"{self.settings_prefix}_fps", 60), 60))
        self.hotspot_x.setValue(_coerce_int(s.value(f"{self.settings_prefix}_hx", 5), 5))
        self.hotspot_y.setValue(_coerce_int(s.value(f"{self.settings_prefix}_hy", 10), 10))
        self.aa_check.setChecked(_coerce_bool(s.value(f"{self.settings_prefix}_aa", True), True))
        self.base_name_input.setText(str(s.value(f"{self.settings_prefix}_basename", "cursorHD")))
        bg_mode = str(s.value(f"{self.settings_prefix}_bg_mode", "checker"))
        self._bg_mode = bg_mode if bg_mode in ("checker", "solid") else "checker"
        self.bg_combo.blockSignals(True)
        self.bg_combo.setCurrentIndex(0 if self._bg_mode == "checker" else 1)
        self.bg_combo.blockSignals(False)
        color_name = str(s.value(f"{self.settings_prefix}_bg_color", "#27272a"))
        color = QColor(color_name)
        self._bg_color = color if color.isValid() else QColor(39, 39, 42)

        vals = {}
        for key in ["sharpen", "blur", "gamma", "brightness", "contrast", "opacity", "edge_enhance", "denoise"]:
            default = 0.0
            if key in ("gamma", "brightness", "contrast", "opacity"):
                default = 1.0
            vals[key] = _coerce_float(s.value(f"{self.settings_prefix}_{key}", default), default)
        self.processing.set_values(vals)

# ======================================================================
# Cursor Baker tab
# ======================================================================
class CursorBakerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        panels = QHBoxLayout()
        panels.setSpacing(8)
        self.panel_default = CursorPanel("Default Cursor", "def")
        self.panel_highlight = CursorPanel("Highlight Cursor", "hl")
        panels.addWidget(self.panel_default)
        panels.addWidget(self.panel_highlight)
        layout.addLayout(panels)

        export_group = QGroupBox("Export Settings")
        exp_layout = QVBoxLayout()
        exp_layout.setSpacing(6)
        exp_layout.setContentsMargins(8, 14, 8, 8)

        fmt_layout = QHBoxLayout()
        fmt_layout.setSpacing(8)
        fmt_layout.addWidget(QLabel("Output Format:"))
        self.fmt_cb = QComboBox()
        self.fmt_cb.addItems(["TGA", "DDS"])
        fmt_layout.addWidget(self.fmt_cb)
        self.dds_fmt_cb = QComboBox()
        self.dds_fmt_cb.addItems(DDS_FORMATS)
        fmt_layout.addWidget(self.dds_fmt_cb)
        fmt_layout.addStretch()
        exp_layout.addLayout(fmt_layout)

        target_line = QHBoxLayout()
        target_line.setSpacing(8)
        self.target_dir = QLineEdit()
        self.target_dir.setPlaceholderText("Select export destination folder...")
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self.browse_target)
        target_line.addWidget(self.target_dir)
        target_line.addWidget(self.btn_browse)
        exp_layout.addLayout(target_line)

        self.btn_export = QPushButton("Bake Cursors & Generate Config")
        self.btn_export.setObjectName("primaryAction")
        self.btn_export.clicked.connect(self.export_all)
        exp_layout.addWidget(self.btn_export)

        export_group.setLayout(exp_layout)
        layout.addWidget(export_group)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_settings)
        self.target_dir.textChanged.connect(lambda: self._save_timer.start(500))
        self.fmt_cb.currentTextChanged.connect(self.save_settings)
        self.dds_fmt_cb.currentTextChanged.connect(self.save_settings)

        self.load_settings()
        self.setLayout(layout)

    def browse_target(self):
        start_dir = get_last_dir("cursor_export_dir")
        path = QFileDialog.getExistingDirectory(self, "Choose Export Folder", start_dir)
        if path:
            set_last_dir("cursor_export_dir", path)
            self.target_dir.setText(path)

    def export_all(self):
        out_dir = self.target_dir.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "Export Error", "Pick a valid damn export directory first.")
            return

        if self.panel_default.source_image is None and self.panel_highlight.source_image is None:
            QMessageBox.warning(self, "Export Error", "You haven't loaded any images. Put something in before you bake.")
            return

        out_dir_path = Path(out_dir).resolve()
        try:
            out_dir_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to punch a hole for the export directory:\n{e}")
            return

        mult = self.window().get_export_multiplier()
        self.btn_export.setText("Baking Assets...")
        self.btn_export.setEnabled(False)
        QApplication.processEvents()

        try:
            success_def = self._bake_panel(self.panel_default, str(out_dir_path), mult)
            success_hl = self._bake_panel(self.panel_highlight, str(out_dir_path), mult)
            if success_def or success_hl:
                self._generate_config(str(out_dir_path))
                QMessageBox.information(self, "Baking Complete", "Cursor assets and config exported successfully.")
        finally:
            self.btn_export.setText("Bake Cursors & Generate Config")
            self.btn_export.setEnabled(True)

    def _bake_panel(self, panel: CursorPanel, out_dir: str, mult: int) -> bool:
        if panel.source_image is None:
            return False
        base = sanitize_filename_component(panel.base_name_input.text(), "cursorHD")
        if not base:
            return False

        use_aa = panel.aa_check.isChecked()
        resample = Image.LANCZOS if use_aa else Image.NEAREST

        fmt = self.fmt_cb.currentText()
        dds_fmt = self.dds_fmt_cb.currentText()
        ext = fmt.lower()

        for suffix, dim in CURSOR_DIMENSIONS.items():
            target_w = round(dim * mult)
            target_h = round(dim * mult)

            filename = f"{base}.{ext}" if suffix == "base" else f"{base}_{suffix}.{ext}"
            out_path = Path(out_dir) / filename
            try:
                resized = panel.source_image.resize((target_w, target_h), resample=resample)
                if fmt == "TGA":
                    resized.save(out_path, format="TGA")
                elif fmt == "DDS":
                    export_dds_file(resized, out_path, dds_fmt, True)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to save {filename}:\n{e}")
                return False
            QApplication.processEvents()
        return True

    def _generate_config(self, out_dir: str):
        cfg_path = os.path.join(out_dir, "bzgame_init_cursor.cfg")
        ext = self.fmt_cb.currentText().lower()
        def_base = f"{sanitize_filename_component(self.panel_default.base_name_input.text(), 'cursorHD')}.{ext}"
        hl_base = f"{sanitize_filename_component(self.panel_highlight.base_name_input.text(), 'cursorHD')}.{ext}"
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

    def save_settings(self):
        s = QSettings("BZCC_Modding", "CursorTool")
        s.setValue("export_dir", self.target_dir.text())
        s.setValue("cursor_export_fmt", self.fmt_cb.currentText())
        s.setValue("cursor_dds_fmt", self.dds_fmt_cb.currentText())

    def load_settings(self):
        s = QSettings("BZCC_Modding", "CursorTool")
        self.target_dir.setText(s.value("export_dir", ""))
        fmt = s.value("cursor_export_fmt", "TGA")
        if fmt in ["TGA", "DDS"]:
            self.fmt_cb.setCurrentText(fmt)
        dds = s.value("cursor_dds_fmt", "DXT5")
        if dds in DDS_FORMATS:
            self.dds_fmt_cb.setCurrentText(dds)

# ======================================================================
# Sprite Generator tab
# ======================================================================
class SpriteGeneratorTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.source_image: Optional[Image.Image] = None
        self.source_mtime: Optional[float] = None

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.refresh_preview)

        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._poll_source)
        self._watch_timer.start(1000)

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_watcher_triggered)
        self.processing = ImageProcessingPanel("Sprite FX")
        self._init_ui()
        self.load_settings()

    def _init_ui(self):
        main = QVBoxLayout()
        main.setSpacing(8)
        main.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.source_path_edit = QLineEdit()
        self.source_path_edit.setPlaceholderText("Select source image path...")
        self.btn_browse_source = QPushButton("Browse Source")
        self.btn_browse_source.clicked.connect(self._browse_source)
        top.addWidget(QLabel("Source File:"))
        top.addWidget(self.source_path_edit, 1)
        top.addWidget(self.btn_browse_source)

        self.target_dir_edit = QLineEdit()
        self.target_dir_edit.setPlaceholderText("Select target output folder...")
        self.btn_browse_target = QPushButton("Browse Target")
        self.btn_browse_target.clicked.connect(self._browse_target)
        top.addWidget(QLabel("Target Folder:"))
        top.addWidget(self.target_dir_edit, 1)
        top.addWidget(self.btn_browse_target)
        main.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(8)
        left = QVBoxLayout()
        left.setSpacing(8)

        controls = QGroupBox("Export Configuration")
        form = QFormLayout()
        form.setContentsMargins(8, 14, 8, 8)
        form.setSpacing(6)

        self.export_format_cb = QComboBox()
        self.export_format_cb.addItems(EXPORT_FORMATS)
        form.addRow("File Format:", self.export_format_cb)
        self.dds_format_cb = QComboBox()
        self.dds_format_cb.addItems(DDS_FORMATS)
        form.addRow("DDS Compression:", self.dds_format_cb)
        self.base_name_edit = QLineEdit("colorize")
        form.addRow("Base Name:", self.base_name_edit)

        checks_layout = QVBoxLayout()
        checks_layout.setSpacing(4)
        self.auto_reload_cb = QCheckBox("Auto-reload source file changes")
        self.auto_reload_cb.setChecked(True)
        checks_layout.addWidget(self.auto_reload_cb)
        self.overwrite_cb = QCheckBox("Overwrite existing files")
        self.overwrite_cb.setChecked(True)
        checks_layout.addWidget(self.overwrite_cb)
        self.force_256_cb = QCheckBox("Force 256x256 Base Scaling (Engine Standard)")
        self.force_256_cb.setChecked(True)
        checks_layout.addWidget(self.force_256_cb)
        form.addRow("", checks_layout)

        fx_checks = QVBoxLayout()
        fx_checks.setSpacing(4)
        self.force_gray_cb = QCheckBox("Force Grayscale")
        self.force_gray_cb.setChecked(True)
        self.force_gray_cb.stateChanged.connect(self._schedule_preview)
        fx_checks.addWidget(self.force_gray_cb)
        self.keep_alpha_cb = QCheckBox("Preserve Alpha Channel")
        self.keep_alpha_cb.setChecked(True)
        self.keep_alpha_cb.stateChanged.connect(self._schedule_preview)
        fx_checks.addWidget(self.keep_alpha_cb)
        self.invert_cb = QCheckBox("Invert Colors")
        self.invert_cb.stateChanged.connect(self._schedule_preview)
        fx_checks.addWidget(self.invert_cb)
        self.normalize_cb = QCheckBox("Normalize Levels")
        self.normalize_cb.stateChanged.connect(self._schedule_preview)
        fx_checks.addWidget(self.normalize_cb)
        self.antialias_cb = QCheckBox("Enable Antialiasing")
        self.antialias_cb.setChecked(True)
        self.antialias_cb.stateChanged.connect(self._schedule_preview)
        fx_checks.addWidget(self.antialias_cb)
        form.addRow("Image FX:", fx_checks)

        self.resample_cb = QComboBox()
        self.resample_cb.addItems(RESAMPLE_NAMES)
        self.resample_cb.setCurrentText("Lanczos")
        self.resample_cb.currentTextChanged.connect(self._schedule_preview)
        form.addRow("Resampling Algorithm:", self.resample_cb)
        controls.setLayout(form)

        for slider in [self.processing.sharpen_slider, self.processing.blur_slider,
                       self.processing.gamma_slider, self.processing.brightness_slider,
                       self.processing.contrast_slider, self.processing.opacity_slider,
                       self.processing.edge_slider, self.processing.denoise_slider]:
            slider.valueChanged.connect(self._schedule_preview)

        variants_group = QGroupBox("Required Size Variants")
        var_layout = QGridLayout()
        var_layout.setContentsMargins(8, 14, 8, 8)
        var_layout.setSpacing(6)
        self.variant_checks: Dict[str, QCheckBox] = {}
        row, col = 0, 0
        for key, dim in SPRITE_VARIANT_DIMENSIONS:
            cb = QCheckBox(f"{key} ({dim}px)")
            cb.setChecked(True)
            self.variant_checks[key] = cb
            var_layout.addWidget(cb, row, col)
            col += 1
            if col > 2:
                col = 0
                row += 1
        variants_group.setLayout(var_layout)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.NoFrame)
        left_container = QWidget()
        left_container.setLayout(QVBoxLayout())
        left_container.layout().setContentsMargins(0, 0, 0, 0)
        left_container.layout().setSpacing(8)
        left_container.layout().addWidget(controls)
        left_container.layout().addWidget(self.processing)
        left_container.layout().addWidget(variants_group)
        left_scroll.setWidget(left_container)
        left.addWidget(left_scroll)

        right = QVBoxLayout()
        right.setSpacing(8)

        preview_group = QGroupBox("Live Preview")
        prev_layout = QVBoxLayout()
        prev_layout.setContentsMargins(8, 14, 8, 8)
        self.result_preview = QLabel()
        self.result_preview.setMinimumSize(300, 300)
        self.result_preview.setAlignment(Qt.AlignCenter)
        self.result_preview.setStyleSheet("background-color: #09090b; border: 1px solid #27272a; border-radius: 8px;")
        prev_layout.addWidget(self.result_preview, alignment=Qt.AlignCenter)
        preview_group.setLayout(prev_layout)
        right.addWidget(preview_group)

        log_group = QGroupBox("Activity Log")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(8, 14, 8, 8)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: monospace; font-size: 8pt; background: #09090b;")
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        right.addWidget(log_group)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_open_source = QPushButton("Open Source")
        self.btn_open_source.clicked.connect(self._open_source)
        self.btn_open_target = QPushButton("Open Target")
        self.btn_open_target.clicked.connect(self._open_target)
        self.btn_reload = QPushButton("Force Reload")
        self.btn_reload.clicked.connect(self.reload_source)

        self.btn_export = QPushButton("Export Sprite Variants")
        self.btn_export.setObjectName("primaryAction")
        self.btn_export.clicked.connect(self.export_all)

        btn_row.addWidget(self.btn_open_source)
        btn_row.addWidget(self.btn_open_target)
        btn_row.addWidget(self.btn_reload)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_export)
        right.addLayout(btn_row)

        body.addLayout(left, 1)
        body.addLayout(right, 1)
        main.addLayout(body)
        self.setLayout(main)

    def _schedule_preview(self):
        self._preview_timer.start(150)

    def _browse_source(self):
        start_dir = get_last_dir("sprite_source_dir")
        path, _ = QFileDialog.getOpenFileName(self, "Choose Source Image", start_dir,
                                              "Images (*.png *.tga *.dds *.bmp *.jpg *.jpeg *.webp)")
        if path:
            set_last_dir("sprite_source_dir", path)
            self.source_path_edit.setText(path)
            self.reload_source()

    def _browse_target(self):
        start_dir = get_last_dir("sprite_target_dir")
        path = QFileDialog.getExistingDirectory(self, "Choose Target Folder", start_dir)
        if path:
            set_last_dir("sprite_target_dir", path)
            self.target_dir_edit.setText(path)

    def _open_target(self):
        folder = self.target_dir_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(self, "Notice", "Target folder is not set or valid.")
            return
        self._open_in_os(folder)

    def _open_source(self):
        path = self.source_path_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "Notice", "Source file not found.")
            return
        self._open_in_os(path)

    def _open_in_os(self, path: str):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Cannot open OS window: {e}")

    def _poll_source(self):
        if not self.auto_reload_cb.isChecked():
            return
        path = self.source_path_edit.text().strip()
        if not path or not os.path.isfile(path):
            return
        try:
            mtime = os.path.getmtime(path)
            if self.source_mtime is None:
                self.source_mtime = mtime
            elif mtime != self.source_mtime:
                self.source_mtime = mtime
                self._log("Source updated, reloading...")
                self.reload_source()
        except Exception:
            pass

    def _on_watcher_triggered(self, path):
        if not self.auto_reload_cb.isChecked():
            return
        QTimer.singleShot(300, self.reload_source)

    def _watch_source_path(self, path: str):
        _safe_remove_paths(self.watcher, self.watcher.files())
        _safe_add_path(self.watcher, path)

    def reload_source(self):
        path = self.source_path_edit.text().strip()
        if not path or not os.path.isfile(path):
            self._log("No valid source file to load.")
            return
        try:
            self.source_image = safe_load_image(Path(path))
            self.source_mtime = os.path.getmtime(path)
            self._log(f"Loaded: {os.path.basename(path)} ({self.source_image.width}x{self.source_image.height})")
            self.refresh_preview()
            self._watch_source_path(path)
        except Exception as e:
            self._log(f"File Load Error: {e}")

    def _invert_rgba(self, base: Image.Image, keep_alpha: bool) -> Image.Image:
        r, g, b, a = base.split()
        rgb = Image.merge("RGB", (r, g, b))
        inverted = ImageOps.invert(rgb)
        if keep_alpha:
            return Image.merge("RGBA", (*inverted.split(), a))
        return Image.merge("RGBA", (*inverted.split(), Image.new("L", base.size, 255)))

    def process_image(self, img: Image.Image, target_w: Optional[int] = None, target_h: Optional[int] = None) -> Image.Image:
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
            base = self._invert_rgba(base, s.keep_alpha)

        if s.normalize_levels:
            base = normalize_image_levels(base, s.keep_alpha)

        vals = self.processing.get_values()
        base = apply_gamma_to_image(base, vals["gamma"])

        if vals["brightness"] != 1.0:
            base = ImageEnhance.Brightness(base).enhance(vals["brightness"])
        if vals["contrast"] != 1.0:
            base = ImageEnhance.Contrast(base).enhance(vals["contrast"])

        base = apply_opacity_to_image(base, vals["opacity"])

        if vals["blur"] > 0:
            base = base.filter(ImageFilter.GaussianBlur(radius=float(vals["blur"])))
        if vals["edge_enhance"] > 0:
            enhanced = base.filter(ImageFilter.EDGE_ENHANCE_MORE)
            base = Image.blend(base, enhanced, min(1.0, float(vals["edge_enhance"]) / 2.0))
        if vals["denoise"] > 0:
            base = base.filter(ImageFilter.MedianFilter(size=max(3, int(1 + round(float(vals["denoise"])) * 2))))
        if vals["sharpen"] > 0:
            amount = float(vals["sharpen"])
            sharpened = base.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(70 + amount * 120), threshold=2))
            base = Image.blend(base, sharpened, min(1.0, amount / 3.0))

        if target_w is not None and target_h is not None:
            target_w = max(1, int(target_w))
            target_h = max(1, int(target_h))
            if base.width != target_w or base.height != target_h:
                resample = RESAMPLE_MAP.get(s.resample, RESAMPLE.LANCZOS) if s.antialias else RESAMPLE.NEAREST
                base = base.resize((target_w, target_h), resample=resample)

        return base

    def _current_settings(self):
        class S:
            pass
        s = S()
        s.force_grayscale = self.force_gray_cb.isChecked()
        s.keep_alpha = self.keep_alpha_cb.isChecked()
        s.invert = self.invert_cb.isChecked()
        s.normalize_levels = self.normalize_cb.isChecked()
        s.antialias = self.antialias_cb.isChecked()
        s.resample = self.resample_cb.currentText()
        return s

    def refresh_preview(self):
        if self.source_image is None:
            self.result_preview.clear()
            return
        try:
            result = self.process_image(self.source_image.copy())
            thumb = self._thumbnail_with_checker(result, 400)
            self.result_preview.setPixmap(pil2pixmap(thumb))
        except Exception as e:
            self._log(f"Preview calculation failed: {e}")

    def _thumbnail_with_checker(self, img: Image.Image, max_size: int) -> Image.Image:
        thumb = img.copy()
        thumb.thumbnail((max_size, max_size), RESAMPLE.LANCZOS)
        bg = Image.new("RGBA", thumb.size, (24, 24, 27, 255))
        tile = 12
        for y in range(0, thumb.height, tile):
            for x in range(0, thumb.width, tile):
                if ((x // tile) + (y // tile)) % 2 == 0:
                    box = (x, y, min(x + tile, thumb.width), min(y + tile, thumb.height))
                    bg.paste((39, 39, 42, 255), box)
        bg.paste(thumb, (0, 0), thumb)
        return bg

    def export_all(self):
        if self.source_image is None:
            QMessageBox.information(self, "Export Blocked", "Please load a source image before exporting.")
            return
        out_dir = self.target_dir_edit.text().strip()
        if not out_dir:
            QMessageBox.information(self, "Export Blocked", "Please choose a valid target folder.")
            return

        out_dir_p = Path(out_dir).resolve()
        try:
            out_dir_p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "System Error", f"Failed to initialize directory structure:\n{e}")
            return

        base_name = sanitize_filename_component(self.base_name_edit.text(), "colorize")
        export_format = self.export_format_cb.currentText().upper()
        dds_fmt = self.dds_format_cb.currentText().strip() or "DXT5"
        overwrite = self.overwrite_cb.isChecked()
        force_256 = self.force_256_cb.isChecked()
        window = self.window()
        mult = window.get_export_multiplier() if hasattr(window, "get_export_multiplier") else DEFAULT_MULTIPLIER

        self.btn_export.setText("Processing Export Tasks...")
        self.btn_export.setEnabled(False)
        QApplication.processEvents()

        try:
            written = []

            src_w = 256 if force_256 else self.source_image.width
            src_h = 256 if force_256 else self.source_image.height

            base_w = max(1, round(src_w * mult))
            base_h = max(1, round(src_h * mult))

            processed = self.process_image(self.source_image.copy(), target_w=base_w, target_h=base_h)
            self._write_one(processed, str(out_dir_p), base_name, export_format, dds_fmt, overwrite)
            written.append(f"{base_name}.{export_format.lower()} ({base_w}x{base_h})")

            for key, dim in SPRITE_VARIANT_DIMENSIONS:
                if not self.variant_checks[key].isChecked():
                    continue

                if force_256:
                    out_w = max(1, round(dim * mult))
                    out_h = max(1, round(dim * mult))
                else:
                    factor = dim / 256.0
                    out_w = max(1, round(self.source_image.width * factor * mult))
                    out_h = max(1, round(self.source_image.height * factor * mult))

                img = self.process_image(self.source_image.copy(), target_w=out_w, target_h=out_h)
                name = f"{base_name}_{key}"
                self._write_one(img, str(out_dir_p), name, export_format, dds_fmt, overwrite)
                written.append(f"{name}.{export_format.lower()} ({out_w}x{out_h})")

                QApplication.processEvents()

            self._log("Export Pipeline Complete:")
            for w in written:
                self._log(f"  → {w}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))
        finally:
            self.btn_export.setText("Export Sprite Variants")
            self.btn_export.setEnabled(True)

    def _write_one(self, img, out_dir, name, fmt, dds_fmt, overwrite):
        out_dir_p = Path(out_dir)
        fmt = fmt.upper()
        out_dir_p.mkdir(parents=True, exist_ok=True)
        if fmt == "DDS":
            out_path = out_dir_p / f"{name}.dds"
            export_dds_file(img, out_path, dds_fmt, overwrite)
        elif fmt == "TGA":
            out_path = out_dir_p / f"{name}.tga"
            if out_path.exists() and not overwrite:
                raise FileExistsError(str(out_path))
            img.save(out_path, format="TGA")
        else:
            out_path = out_dir_p / f"{name}.png"
            if out_path.exists() and not overwrite:
                raise FileExistsError(str(out_path))
            img.save(out_path, format="PNG")

    def _log(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {msg}")

    def save_settings(self):
        s = QSettings("BZCC_Modding", "SpriteGen")
        s.setValue("source_path", self.source_path_edit.text())
        s.setValue("target_dir", self.target_dir_edit.text())
        s.setValue("export_format", self.export_format_cb.currentText())
        s.setValue("dds_format", self.dds_format_cb.currentText())
        s.setValue("base_name", self.base_name_edit.text())
        s.setValue("auto_reload", self.auto_reload_cb.isChecked())
        s.setValue("overwrite", self.overwrite_cb.isChecked())
        s.setValue("force_256_base", self.force_256_cb.isChecked())
        s.setValue("force_gray", self.force_gray_cb.isChecked())
        s.setValue("keep_alpha", self.keep_alpha_cb.isChecked())
        s.setValue("invert", self.invert_cb.isChecked())
        s.setValue("normalize", self.normalize_cb.isChecked())
        s.setValue("antialias", self.antialias_cb.isChecked())
        s.setValue("resample", self.resample_cb.currentText())
        vals = self.processing.get_values()
        for k, v in vals.items():
            s.setValue(k, v)
        for key, cb in self.variant_checks.items():
            s.setValue(f"variant_{key}", cb.isChecked())

    def load_settings(self):
        s = QSettings("BZCC_Modding", "SpriteGen")
        self.source_path_edit.setText(str(s.value("source_path", "")))
        self.target_dir_edit.setText(str(s.value("target_dir", "")))
        fmt = str(s.value("export_format", "DDS"))
        if fmt in EXPORT_FORMATS:
            self.export_format_cb.setCurrentText(fmt)
        dds = str(s.value("dds_format", "DXT5"))
        if dds in DDS_FORMATS:
            self.dds_format_cb.setCurrentText(dds)
        self.base_name_edit.setText(str(s.value("base_name", "colorize")))
        self.auto_reload_cb.setChecked(_coerce_bool(s.value("auto_reload", True), True))
        self.overwrite_cb.setChecked(_coerce_bool(s.value("overwrite", True), True))
        self.force_256_cb.setChecked(_coerce_bool(s.value("force_256_base", True), True))
        self.force_gray_cb.setChecked(_coerce_bool(s.value("force_gray", True), True))
        self.keep_alpha_cb.setChecked(_coerce_bool(s.value("keep_alpha", True), True))
        self.invert_cb.setChecked(_coerce_bool(s.value("invert", False), False))
        self.normalize_cb.setChecked(_coerce_bool(s.value("normalize", False), False))
        self.antialias_cb.setChecked(_coerce_bool(s.value("antialias", True), True))
        res = str(s.value("resample", "Lanczos"))
        self.resample_cb.setCurrentText(res if res in RESAMPLE_NAMES else "Lanczos")
        vals = {}
        for key in ["sharpen", "blur", "gamma", "brightness", "contrast", "opacity", "edge_enhance", "denoise"]:
            default = 0.0
            if key in ("gamma", "brightness", "contrast", "opacity"):
                default = 1.0
            vals[key] = _coerce_float(s.value(key, default), default)
        self.processing.set_values(vals)
        for key, cb in self.variant_checks.items():
            val = _coerce_bool(s.value(f"variant_{key}", True), True)
            cb.setChecked(val)
        if self.source_path_edit.text():
            self.reload_source()

# ======================================================================
# Main window entry
# ======================================================================
THEMES = {
    "Classic 95": {
        "bg": "#C0C0C0",
        "text": "#000000",
        "light": "#FFFFFF",
        "dark": "#404040",
        "border_subtle": "#808080",
        "hover": "#E6E6E6",
        "accent": "#008080",
        "accent_text": "#FFFFFF",
        "accent_hover": "#006666",
        "accent_pressed": "#004C4C",
    },
    "Rainy Day": {
        "bg": "#9999CC",
        "text": "#000000",
        "light": "#CCCCFF",
        "dark": "#333366",
        "border_subtle": "#666699",
        "hover": "#B3B3E6",
        "accent": "#4A5A6A",
        "accent_text": "#FFFFFF",
        "accent_hover": "#3A4A5A",
        "accent_pressed": "#2A3A4A",
    },
    "High Contrast Dark": {
        "bg": "#000000",
        "text": "#FFFFFF",
        "light": "#C0C0C0",
        "dark": "#404040",
        "border_subtle": "#808080",
        "hover": "#202020",
        "accent": "#808000",
        "accent_text": "#000000",
        "accent_hover": "#A0A000",
        "accent_pressed": "#606000",
    }
}

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CursorHD Editor")
        self.resize(1000, 700)
        self.setMinimumSize(800, 600)
        self.export_multiplier = 1

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        # Theme selection
        top_bar = QHBoxLayout()
        top_bar.addStretch()
        theme_label = QLabel("Theme:")
        theme_label.setStyleSheet("background: transparent;")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        self.theme_combo.setFocusPolicy(Qt.NoFocus)
        self.theme_combo.currentTextChanged.connect(self._apply_theme)
        top_bar.addWidget(theme_label)
        top_bar.addWidget(self.theme_combo)
        layout.addLayout(top_bar)

        self.tabs = QTabWidget()
        self.tabs.setCursor(Qt.ArrowCursor)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self.tabs.setTabBarAutoHide(False)
        tab_bar = self.tabs.tabBar()
        tab_bar.setExpanding(True)
        tab_bar.setUsesScrollButtons(False)
        self.cursor_tab = CursorBakerTab()
        self.sprite_tab = SpriteGeneratorTab()
        self.tabs.addTab(self.cursor_tab, "Cursor Editing")
        self.tabs.addTab(self.sprite_tab, "Colorize Editing")
        layout.addWidget(self.tabs)

        central.setLayout(layout)

        # Load saved theme
        s = QSettings("VacCompany", "CursorHDEditor")
        saved_theme = s.value("ui_theme", "Classic 95")
        if saved_theme in THEMES:
            self.theme_combo.setCurrentText(saved_theme)
        else:
            self.theme_combo.setCurrentText("Classic 95")
        
        self._apply_theme(self.theme_combo.currentText())

    def _apply_theme(self, theme_name):
        s = QSettings("VacCompany", "CursorHDEditor")
        s.setValue("ui_theme", theme_name)

        t = THEMES.get(theme_name, THEMES["Classic 95"])
        
        input_bg = "#FFFFFF" if theme_name != "High Contrast Dark" else "#000000"
        input_text = "#000000" if theme_name != "High Contrast Dark" else "#FFFFFF"
        checkbox_bg = input_bg

        style = f"""
            * {{
                font-family: "MS Sans Serif", "Tahoma", "Courier New", sans-serif;
                font-size: 12px;
            }}
            QMainWindow, QWidget {{
                background-color: {t['bg']};
                color: {t['text']};
            }}
            QLabel {{
                background: transparent;
                color: {t['text']};
            }}
            QGroupBox {{
                font-weight: 700;
                border: 2px solid;
                border-top-color: {t['light']};
                border-left-color: {t['light']};
                border-right-color: {t['border_subtle']};
                border-bottom-color: {t['border_subtle']};
                margin-top: 18px;
                padding-top: 18px;
                background-color: {t['bg']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 4px;
                color: {t['text']};
                top: 0px;
                left: 8px;
                font-size: 12px;
            }}
            QPushButton {{
                background-color: {t['bg']};
                border: 2px solid;
                border-top-color: {t['light']};
                border-left-color: {t['light']};
                border-right-color: {t['dark']};
                border-bottom-color: {t['dark']};
                padding: 4px 8px;
                color: {t['text']};
                font-weight: 400;
                min-height: 24px;
            }}
            QPushButton:hover {{
                background-color: {t['hover']};
            }}
            QPushButton:focus {{
                border: 1px dotted {t['text']};
            }}
            QPushButton:pressed {{
                background-color: {t['bg']};
                border-top-color: {t['dark']};
                border-left-color: {t['dark']};
                border-right-color: {t['light']};
                border-bottom-color: {t['light']};
                padding-top: 6px;
                padding-left: 10px;
                padding-right: 6px;
                padding-bottom: 2px;
            }}
            QPushButton:checked {{
                background-color: {t['accent']};
                color: {t['accent_text']};
                border-top-color: {t['dark']};
                border-left-color: {t['dark']};
                border-right-color: {t['light']};
                border-bottom-color: {t['light']};
            }}
            QPushButton#primaryAction {{
                background-color: {t['accent']};
                color: {t['accent_text']};
                border: 2px solid;
                border-top-color: {t['light']};
                border-left-color: {t['light']};
                border-right-color: {t['dark']};
                border-bottom-color: {t['dark']};
                font-weight: 700;
                padding: 6px 10px;
                min-height: 28px;
            }}
            QPushButton#primaryAction:hover {{
                background-color: {t['accent_hover']};
            }}
            QPushButton#primaryAction:pressed {{
                background-color: {t['accent_pressed']};
                border-top-color: {t['dark']};
                border-left-color: {t['dark']};
                border-right-color: {t['light']};
                border-bottom-color: {t['light']};
            }}
            QPushButton#primaryAction:focus {{
                outline: 2px solid {t['text']};
            }}
            QPushButton#primaryAction:disabled {{
                background-color: {t['border_subtle']};
                color: {t['bg']};
            }}
            QLineEdit, QSpinBox, QTextEdit, QComboBox {{
                background-color: {input_bg};
                border: 2px solid;
                border-top-color: {t['dark']};
                border-left-color: {t['dark']};
                border-right-color: {t['light']};
                border-bottom-color: {t['light']};
                color: {input_text};
                padding: 2px 4px;
                min-height: 20px;
            }}
            QLineEdit:focus, QSpinBox:focus, QTextEdit:focus, QComboBox:focus {{
                background-color: {input_bg};
            }}
            QComboBox::drop-down {{
                border: 2px solid;
                border-top-color: {t['light']};
                border-left-color: {t['light']};
                border-right-color: {t['dark']};
                border-bottom-color: {t['dark']};
                background-color: {t['bg']};
                width: 20px;
            }}
            QTabWidget::pane {{
                border: 2px solid;
                border-top-color: {t['light']};
                border-left-color: {t['light']};
                border-right-color: {t['border_subtle']};
                border-bottom-color: {t['border_subtle']};
                background: {t['bg']};
                top: 0px;
            }}
            QTabBar {{
                qproperty-drawBase: 0;
            }}
            QTabBar::tab {{
                background: {t['bg']};
                color: {t['text']};
                padding: 4px 12px;
                border: 2px solid;
                border-top-color: {t['light']};
                border-left-color: {t['light']};
                border-right-color: {t['dark']};
                border-bottom-color: {t['dark']};
                font-weight: 400;
                min-width: 120px;
                min-height: 24px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                font-weight: 700;
                background: {t['bg']};
                border-bottom-color: {t['bg']};
            }}
            QTabBar::tab:hover:!selected {{
                background: {t['hover']};
            }}
            QSlider::groove:horizontal {{
                background: {t['border_subtle']};
                border: 1px solid {t['dark']};
                height: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {t['bg']};
                width: 10px;
                margin: -6px 0;
                border: 2px solid;
                border-top-color: {t['light']};
                border-left-color: {t['light']};
                border-right-color: {t['dark']};
                border-bottom-color: {t['dark']};
            }}
            QSlider::handle:horizontal:hover {{
                background: {t['hover']};
            }}
            QCheckBox {{
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 13px;
                height: 13px;
                border: 2px solid;
                border-top-color: {t['dark']};
                border-left-color: {t['dark']};
                border-right-color: {t['light']};
                border-bottom-color: {t['light']};
                background: {checkbox_bg};
            }}
            QCheckBox::indicator:checked {{
                background: {checkbox_bg};
                image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='11' height='11' viewBox='0 0 24 24' fill='none' stroke='{input_text.replace('#', '%23')}' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'><polyline points='20 6 9 17 4 12'></polyline></svg>");
            }}
            QScrollArea {{
                border: 2px solid;
                border-top-color: {t['border_subtle']};
                border-left-color: {t['border_subtle']};
                border-right-color: {t['light']};
                border-bottom-color: {t['light']};
                background-color: {t['bg']};
            }}
        """
        self.setStyleSheet(style)

    def _set_multiplier(self, val):
        # Kept for compatibility with older saved settings and external callers.
        self.export_multiplier = 1

    def get_export_multiplier(self) -> int:
        return 1

    def closeEvent(self, event):
        self.cursor_tab.panel_default.save_settings()
        self.cursor_tab.panel_highlight.save_settings()
        self.cursor_tab.save_settings()
        self.sprite_tab.save_settings()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())