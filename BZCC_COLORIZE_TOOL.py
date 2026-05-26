#!/usr/bin/env python3
"""BZCC COLORIZE TOOL

A lightweight texture prep tool for Battlezone Combat Commander.

What it does:
- Loads a colorize source image (PNG/TGA/DDS when supported)
- Shows live preview
- Watches the source file and auto-reloads when it changes on disk
- Applies image tweaks useful for mask-like grayscale textures
- Exports a base 128x128 texture plus size variants
- Saves to a target folder and can overwrite existing files
- Remembers settings between sessions

Recommended dependencies:
    pip install pillow
Optional for robust DDS import/export:
    texconv.exe on PATH or in the same folder as this script

Notes:
- This tool is designed for grayscale/alpha colorize maps.
- RGB channels are preserved only if you disable grayscale conversion.
- DDS export uses texconv if available. If not, the tool falls back to PNG/TGA export.
"""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageTk


try:
    RESAMPLE = Image.Resampling  # Pillow 9+
except AttributeError:  # pragma: no cover
    RESAMPLE = Image


APP_NAME = "BZCC COLORIZE TOOL"
BASE_SIZE = 128
VARIANTS: List[Tuple[str, int]] = [
    ("x1_0", 128),
    ("x1_5", 192),
    ("x2_0", 256),
    ("x2_5", 320),
    ("x3_0", 384),
    ("x3_5", 448),
    ("x4_0", 512),
    ("x4_5", 576),
    ("x5_0", 640),
]

EXPORT_FORMATS = ["DDS", "TGA", "PNG"]
DDS_FORMATS = ["DXT5", "BC3_UNORM", "BC3_UNORM_SRGB", "DXT3"]
RESAMPLE_NAMES = ["Nearest", "Bilinear", "Bicubic", "Lanczos"]
RESAMPLE_MAP = {
    "Nearest": RESAMPLE.NEAREST,
    "Bilinear": RESAMPLE.BILINEAR,
    "Bicubic": RESAMPLE.BICUBIC,
    "Lanczos": RESAMPLE.LANCZOS,
}


def app_data_dir() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "BZCC_COLORIZE_TOOL"
    return Path.home() / ".bzcc_colorize_tool"


def settings_file() -> Path:
    return app_data_dir() / "settings.json"


def find_texconv() -> Optional[Path]:
    candidates: List[Path] = []
    env = shutil.which("texconv")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for name in ("texconv.exe", "texconv"):
        p = here / name
        if p.exists():
            return p
    return None


@dataclass
class Settings:
    source_path: str = ""
    target_dir: str = ""
    export_format: str = "DDS"
    dds_format: str = "DXT5"
    base_name: str = "colorize"
    auto_reload: bool = True
    overwrite: bool = True
    force_grayscale: bool = True
    keep_alpha: bool = True
    invert: bool = False
    normalize_levels: bool = False
    antialias: bool = True
    resample: str = "Lanczos"
    sharpen: float = 0.0
    blur: float = 0.0
    gamma: float = 1.0
    brightness: float = 1.0
    contrast: float = 1.0
    opacity: float = 1.0
    edge_enhance: float = 0.0
    denoise: float = 0.0
    window_geometry: str = "1380x900"
    texconv_path: str = ""
    variant_enabled: Dict[str, bool] = field(default_factory=lambda: {k: True for k, _ in VARIANTS})


class BZCCColorizeTool(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.minsize(1240, 780)

        self.settings = self.load_settings()
        self.preview_max = 470
        self.source_image: Optional[Image.Image] = None
        self.source_mtime: Optional[float] = None
        self.preview_source_tk: Optional[ImageTk.PhotoImage] = None
        self.preview_result_tk: Optional[ImageTk.PhotoImage] = None
        self._watch_timer: Optional[str] = None
        self._preview_timer: Optional[str] = None
        self._save_timer: Optional[str] = None
        self._busy = False
        self._status_queue: "queue.Queue[str]" = queue.Queue()
        self._last_source_scan = 0.0

        self._build_ui()
        self._sync_settings_to_vars()
        self.geometry(self.settings.window_geometry)
        self.after(150, self._initial_load)
        self.after(1000, self._watch_source_file)
        self.after(250, self._drain_status_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------- persistence -------------------------
    def load_settings(self) -> Settings:
        path = settings_file()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                s = Settings()
                for k, v in raw.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
                if not isinstance(s.variant_enabled, dict):
                    s.variant_enabled = {k: True for k, _ in VARIANTS}
                for key, _ in VARIANTS:
                    s.variant_enabled.setdefault(key, True)
                return s
            except Exception:
                pass
        return Settings()

    def save_settings(self) -> None:
        self.settings.window_geometry = self.geometry()
        path = settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self.settings), ensure_ascii=False, indent=2), encoding="utf-8")

    def _schedule_save_settings(self) -> None:
        if self._save_timer is not None:
            self.after_cancel(self._save_timer)
        self._save_timer = self.after(250, self._save_settings_now)

    def _save_settings_now(self) -> None:
        self._save_timer = None
        self._sync_vars_to_settings()
        self.save_settings()

    # ------------------------- UI -------------------------
    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        for col in range(6):
            top.columnconfigure(col, weight=1 if col in (1, 4) else 0)

        ttk.Label(top, text="Source:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.var_source = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_source).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(top, text="Browse", command=self._browse_source).grid(row=0, column=2, padx=(0, 14))

        ttk.Label(top, text="Target folder:").grid(row=0, column=3, sticky="w", padx=(0, 6))
        self.var_target = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_target).grid(row=0, column=4, sticky="ew", padx=(0, 8))
        ttk.Button(top, text="Browse", command=self._browse_target).grid(row=0, column=5)

        mid = ttk.Frame(self, padding=(10, 0, 10, 10))
        mid.grid(row=1, column=0, sticky="nsew")
        mid.columnconfigure(0, weight=2)
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)

        left = ttk.Frame(mid)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        controls = ttk.LabelFrame(left, text="Processing", padding=10)
        controls.grid(row=0, column=0, sticky="ew")
        for c in range(6):
            controls.columnconfigure(c, weight=1 if c in (1, 3, 5) else 0)

        self.var_export_format = tk.StringVar()
        self.var_dds_format = tk.StringVar()
        self.var_base_name = tk.StringVar()
        self.var_auto_reload = tk.BooleanVar()
        self.var_overwrite = tk.BooleanVar()
        self.var_force_gray = tk.BooleanVar()
        self.var_keep_alpha = tk.BooleanVar()
        self.var_invert = tk.BooleanVar()
        self.var_normalize = tk.BooleanVar()
        self.var_antialias = tk.BooleanVar()
        self.var_resample = tk.StringVar()
        self.var_sharpen = tk.DoubleVar()
        self.var_blur = tk.DoubleVar()
        self.var_gamma = tk.DoubleVar()
        self.var_brightness = tk.DoubleVar()
        self.var_contrast = tk.DoubleVar()
        self.var_opacity = tk.DoubleVar()
        self.var_edge = tk.DoubleVar()
        self.var_denoise = tk.DoubleVar()

        r = 0
        ttk.Label(controls, text="Export:").grid(row=r, column=0, sticky="w")
        ttk.Combobox(controls, textvariable=self.var_export_format, values=EXPORT_FORMATS, state="readonly", width=10).grid(row=r, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(controls, text="DDS mode:").grid(row=r, column=2, sticky="w")
        ttk.Combobox(controls, textvariable=self.var_dds_format, values=DDS_FORMATS, state="readonly", width=14).grid(row=r, column=3, sticky="ew", padx=(0, 8))
        ttk.Label(controls, text="Base name:").grid(row=r, column=4, sticky="w")
        ttk.Entry(controls, textvariable=self.var_base_name).grid(row=r, column=5, sticky="ew")

        r += 1
        ttk.Checkbutton(controls, text="Auto-reload source", variable=self.var_auto_reload, command=self._schedule_save_settings).grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(controls, text="Overwrite existing", variable=self.var_overwrite, command=self._schedule_save_settings).grid(row=r, column=2, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(controls, text="Force grayscale", variable=self.var_force_gray, command=self._schedule_preview).grid(row=r, column=4, columnspan=2, sticky="w", pady=(8, 0))

        r += 1
        ttk.Checkbutton(controls, text="Keep alpha", variable=self.var_keep_alpha, command=self._schedule_preview).grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(controls, text="Invert", variable=self.var_invert, command=self._schedule_preview).grid(row=r, column=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(controls, text="Normalize levels", variable=self.var_normalize, command=self._schedule_preview).grid(row=r, column=3, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(controls, text="Antialias", variable=self.var_antialias, command=self._schedule_preview).grid(row=r, column=5, sticky="w", pady=(8, 0))

        r += 1
        ttk.Label(controls, text="Resample:").grid(row=r, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(controls, textvariable=self.var_resample, values=RESAMPLE_NAMES, state="readonly", width=12).grid(row=r, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        ttk.Label(controls, text="Sharpen:").grid(row=r, column=2, sticky="w", pady=(8, 0))
        ttk.Scale(controls, from_=0.0, to=3.0, variable=self.var_sharpen, orient="horizontal", command=lambda _=None: self._schedule_preview()).grid(row=r, column=3, sticky="ew", padx=(0, 8), pady=(8, 0))
        ttk.Label(controls, text="Blur:").grid(row=r, column=4, sticky="w", pady=(8, 0))
        ttk.Scale(controls, from_=0.0, to=3.0, variable=self.var_blur, orient="horizontal", command=lambda _=None: self._schedule_preview()).grid(row=r, column=5, sticky="ew", pady=(8, 0))

        r += 1
        ttk.Label(controls, text="Gamma:").grid(row=r, column=0, sticky="w", pady=(8, 0))
        ttk.Scale(controls, from_=0.2, to=3.0, variable=self.var_gamma, orient="horizontal", command=lambda _=None: self._schedule_preview()).grid(row=r, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        ttk.Label(controls, text="Brightness:").grid(row=r, column=2, sticky="w", pady=(8, 0))
        ttk.Scale(controls, from_=0.2, to=2.0, variable=self.var_brightness, orient="horizontal", command=lambda _=None: self._schedule_preview()).grid(row=r, column=3, sticky="ew", padx=(0, 8), pady=(8, 0))
        ttk.Label(controls, text="Contrast:").grid(row=r, column=4, sticky="w", pady=(8, 0))
        ttk.Scale(controls, from_=0.2, to=2.0, variable=self.var_contrast, orient="horizontal", command=lambda _=None: self._schedule_preview()).grid(row=r, column=5, sticky="ew", pady=(8, 0))

        r += 1
        ttk.Label(controls, text="Opacity:").grid(row=r, column=0, sticky="w", pady=(8, 0))
        ttk.Scale(controls, from_=0.0, to=1.0, variable=self.var_opacity, orient="horizontal", command=lambda _=None: self._schedule_preview()).grid(row=r, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        ttk.Label(controls, text="Edge enhance:").grid(row=r, column=2, sticky="w", pady=(8, 0))
        ttk.Scale(controls, from_=0.0, to=2.0, variable=self.var_edge, orient="horizontal", command=lambda _=None: self._schedule_preview()).grid(row=r, column=3, sticky="ew", padx=(0, 8), pady=(8, 0))
        ttk.Label(controls, text="Denoise:").grid(row=r, column=4, sticky="w", pady=(8, 0))
        ttk.Scale(controls, from_=0.0, to=3.0, variable=self.var_denoise, orient="horizontal", command=lambda _=None: self._schedule_preview()).grid(row=r, column=5, sticky="ew", pady=(8, 0))

        r += 1
        ttk.Button(controls, text="Reload now", command=self.reload_source).grid(row=r, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(controls, text="Export now", command=self.export_all).grid(row=r, column=1, sticky="ew", pady=(10, 0))
        ttk.Button(controls, text="Open target", command=self.open_target_folder).grid(row=r, column=2, sticky="ew", pady=(10, 0))
        ttk.Button(controls, text="Open source", command=self.open_source_in_editor).grid(row=r, column=3, sticky="ew", pady=(10, 0))
        ttk.Button(controls, text="Restore defaults", command=self.restore_defaults).grid(row=r, column=4, sticky="ew", pady=(10, 0))
        ttk.Button(controls, text="Save settings", command=self._save_settings_now).grid(row=r, column=5, sticky="ew", pady=(10, 0))

        variants = ttk.LabelFrame(left, text="Export sizes", padding=10)
        variants.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        variants.columnconfigure(0, weight=1)
        variants.columnconfigure(1, weight=1)
        self.variant_vars: Dict[str, tk.BooleanVar] = {}
        for i, (key, size) in enumerate(VARIANTS):
            var = tk.BooleanVar()
            self.variant_vars[key] = var
            ttk.Checkbutton(variants, text=f"{key}  {size}x{size}", variable=var, command=self._schedule_save_settings).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 14), pady=2)

        right = ttk.Frame(mid)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        info = ttk.LabelFrame(right, text="Live preview", padding=10)
        info.grid(row=0, column=0, sticky="ew")
        info.columnconfigure(0, weight=1)
        self.preview_info = tk.StringVar(value="Load a source file to begin.")
        ttk.Label(info, textvariable=self.preview_info, anchor="w").grid(row=0, column=0, sticky="ew")

        preview_box = ttk.Frame(right)
        preview_box.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        preview_box.columnconfigure(0, weight=1)
        preview_box.columnconfigure(1, weight=1)
        preview_box.rowconfigure(1, weight=1)

        ttk.Label(preview_box, text="Source").grid(row=0, column=0, sticky="w")
        ttk.Label(preview_box, text="Processed").grid(row=0, column=1, sticky="w")

        self.canvas_source = tk.Label(preview_box, relief="sunken", anchor="center", bg="#222")
        self.canvas_source.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.canvas_result = tk.Label(preview_box, relief="sunken", anchor="center", bg="#222")
        self.canvas_result.grid(row=1, column=1, sticky="nsew")

        log_frame = ttk.LabelFrame(right, text="Log", padding=10)
        log_frame.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=10, wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(bottom, textvariable=self.status).grid(row=0, column=0, sticky="w")

        self._bind_setting_traces()

    def _bind_setting_traces(self) -> None:
        vars_to_watch = [
            self.var_source,
            self.var_target,
            self.var_export_format,
            self.var_dds_format,
            self.var_base_name,
            self.var_auto_reload,
            self.var_overwrite,
            self.var_force_gray,
            self.var_keep_alpha,
            self.var_invert,
            self.var_normalize,
            self.var_antialias,
            self.var_resample,
            self.var_sharpen,
            self.var_blur,
            self.var_gamma,
            self.var_brightness,
            self.var_contrast,
            self.var_opacity,
            self.var_edge,
            self.var_denoise,
        ]
        for v in vars_to_watch:
            v.trace_add("write", lambda *_: self._schedule_save_settings())
        for v in [self.var_source, self.var_export_format, self.var_dds_format, self.var_base_name, self.var_force_gray, self.var_keep_alpha, self.var_invert, self.var_normalize, self.var_antialias, self.var_resample, self.var_sharpen, self.var_blur, self.var_gamma, self.var_brightness, self.var_contrast, self.var_opacity, self.var_edge, self.var_denoise]:
            v.trace_add("write", lambda *_: self._schedule_preview())
        self.var_source.trace_add("write", lambda *_: self._maybe_reset_watch_timer())

    def _sync_settings_to_vars(self) -> None:
        s = self.settings
        self.var_source.set(s.source_path)
        self.var_target.set(s.target_dir)
        self.var_export_format.set(s.export_format)
        self.var_dds_format.set(s.dds_format)
        self.var_base_name.set(s.base_name)
        self.var_auto_reload.set(s.auto_reload)
        self.var_overwrite.set(s.overwrite)
        self.var_force_gray.set(s.force_grayscale)
        self.var_keep_alpha.set(s.keep_alpha)
        self.var_invert.set(s.invert)
        self.var_normalize.set(s.normalize_levels)
        self.var_antialias.set(s.antialias)
        self.var_resample.set(s.resample if s.resample in RESAMPLE_NAMES else "Lanczos")
        self.var_sharpen.set(s.sharpen)
        self.var_blur.set(s.blur)
        self.var_gamma.set(s.gamma)
        self.var_brightness.set(s.brightness)
        self.var_contrast.set(s.contrast)
        self.var_opacity.set(s.opacity)
        self.var_edge.set(s.edge_enhance)
        self.var_denoise.set(s.denoise)
        for key, var in self.variant_vars.items():
            var.set(bool(s.variant_enabled.get(key, True)))

    def _sync_vars_to_settings(self) -> None:
        s = self.settings
        s.source_path = self.var_source.get().strip()
        s.target_dir = self.var_target.get().strip()
        s.export_format = self.var_export_format.get().strip() or "DDS"
        s.dds_format = self.var_dds_format.get().strip() or "DXT5"
        s.base_name = self.var_base_name.get().strip() or "colorize"
        s.auto_reload = bool(self.var_auto_reload.get())
        s.overwrite = bool(self.var_overwrite.get())
        s.force_grayscale = bool(self.var_force_gray.get())
        s.keep_alpha = bool(self.var_keep_alpha.get())
        s.invert = bool(self.var_invert.get())
        s.normalize_levels = bool(self.var_normalize.get())
        s.antialias = bool(self.var_antialias.get())
        s.resample = self.var_resample.get().strip() or "Lanczos"
        s.sharpen = float(self.var_sharpen.get())
        s.blur = float(self.var_blur.get())
        s.gamma = float(self.var_gamma.get())
        s.brightness = float(self.var_brightness.get())
        s.contrast = float(self.var_contrast.get())
        s.opacity = float(self.var_opacity.get())
        s.edge_enhance = float(self.var_edge.get())
        s.denoise = float(self.var_denoise.get())
        s.variant_enabled = {k: bool(v.get()) for k, v in self.variant_vars.items()}
        texconv = find_texconv()
        s.texconv_path = str(texconv) if texconv else ""

    def restore_defaults(self) -> None:
        self.settings = Settings()
        self._sync_settings_to_vars()
        self._schedule_preview()
        self._schedule_save_settings()
        self._log("Defaults restored.")

    # ------------------------- utility -------------------------
    def _log(self, msg: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{timestamp}] {msg}\n")
        self.log.see("end")

    def _set_status(self, msg: str) -> None:
        self.status.set(msg)

    def _drain_status_queue(self) -> None:
        try:
            while True:
                msg = self._status_queue.get_nowait()
                self._set_status(msg)
        except queue.Empty:
            pass
        self.after(250, self._drain_status_queue)

    def _queue_status(self, msg: str) -> None:
        self._status_queue.put(msg)

    def _maybe_reset_watch_timer(self) -> None:
        if self._watch_timer is not None:
            self.after_cancel(self._watch_timer)
            self._watch_timer = None
        self._watch_timer = self.after(500, self._watch_source_file)

    def _schedule_preview(self) -> None:
        if self._preview_timer is not None:
            self.after_cancel(self._preview_timer)
        self._preview_timer = self.after(120, self.refresh_preview)

    def _schedule_save(self) -> None:
        self._schedule_save_settings()

    def _source_path(self) -> Optional[Path]:
        p = self.var_source.get().strip()
        return Path(p) if p else None

    # ------------------------- file handling -------------------------
    def _browse_source(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose colorize source",
            filetypes=[
                ("Image files", "*.png *.tga *.dds *.bmp *.jpg *.jpeg *.webp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.var_source.set(path)
            self._maybe_reset_watch_timer()
            self.reload_source()

    def _browse_target(self) -> None:
        path = filedialog.askdirectory(title="Choose target folder")
        if path:
            self.var_target.set(path)

    def open_target_folder(self) -> None:
        target = self.var_target.get().strip()
        if not target:
            messagebox.showinfo(APP_NAME, "Target folder is empty.")
            return
        path = Path(target)
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Failed to open target folder:\n{e}")

    def open_source_in_editor(self) -> None:
        p = self._source_path()
        if not p or not p.exists():
            messagebox.showinfo(APP_NAME, "Source file not found.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Failed to open source file:\n{e}")

    def _watch_source_file(self) -> None:
        self._watch_timer = None
        if not self.var_auto_reload.get():
            self._watch_timer = self.after(1000, self._watch_source_file)
            return

        p = self._source_path()
        if p and p.exists():
            try:
                mtime = p.stat().st_mtime
                if self.source_mtime is None:
                    self.source_mtime = mtime
                elif mtime != self.source_mtime:
                    self.source_mtime = mtime
                    self._queue_status("Source changed on disk, reloading...")
                    self.reload_source()
            except Exception:
                pass
        self._watch_timer = self.after(1000, self._watch_source_file)

    def _initial_load(self) -> None:
        if self.var_source.get().strip():
            self.reload_source()

    def reload_source(self) -> None:
        p = self._source_path()
        if not p:
            self._queue_status("Choose a source file first.")
            return
        if not p.exists():
            self._queue_status("Source file not found.")
            return
        try:
            self.source_image = self.load_image(p)
            self.source_mtime = p.stat().st_mtime
            self._log(f"Loaded source: {p.name}")
            self.refresh_preview()
        except Exception as e:
            self._queue_status("Load failed.")
            messagebox.showerror(APP_NAME, f"Failed to load source image:\n{e}")

    def load_image(self, path: Path) -> Image.Image:
        suffix = path.suffix.lower()
        if suffix == ".dds":
            try:
                return Image.open(path).convert("RGBA")
            except Exception:
                texconv = find_texconv()
                if not texconv:
                    raise RuntimeError("DDS import failed. Install Pillow DDS support or put texconv.exe next to the script.")
                with tempfile.TemporaryDirectory() as td:
                    out_dir = Path(td)
                    cmd = [str(texconv), "-ft", "png", "-o", str(out_dir), str(path)]
                    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    pngs = list(out_dir.glob("*.png"))
                    if not pngs:
                        raise RuntimeError("texconv did not produce a PNG preview.")
                    return Image.open(pngs[0]).convert("RGBA")
        return Image.open(path).convert("RGBA")

    # ------------------------- image processing -------------------------
    def _effective_resample(self) -> int:
        name = self.var_resample.get().strip() or "Lanczos"
        return RESAMPLE_MAP.get(name, RESAMPLE.LANCZOS)

    def process_image(self, img: Image.Image, size: Optional[int] = None) -> Image.Image:
        s = self.settings_from_vars()
        base = img.convert("RGBA")

        if s.force_grayscale:
            # Keep alpha if requested; RGB becomes a gray mask.
            if s.keep_alpha:
                r, g, b, a = base.split()
                gray = ImageOps.grayscale(Image.merge("RGB", (r, g, b))).convert("L")
                base = Image.merge("RGBA", (gray, gray, gray, a))
            else:
                gray = ImageOps.grayscale(base).convert("L")
                base = Image.merge("RGBA", (gray, gray, gray, Image.new("L", gray.size, 255)))

        if s.invert:
            if s.keep_alpha:
                r, g, b, a = base.split()
                gray = ImageOps.invert(r)
                base = Image.merge("RGBA", (gray, gray, gray, a))
            else:
                r, g, b, a = base.split()
                gray = ImageOps.invert(r)
                base = Image.merge("RGBA", (gray, gray, gray, a))

        if s.normalize_levels:
            base = self.normalize_levels(base, s.keep_alpha)

        if abs(s.gamma - 1.0) > 1e-3:
            base = self.apply_gamma(base, s.gamma)

        if abs(s.brightness - 1.0) > 1e-3:
            base = self.apply_brightness(base, s.brightness)

        if abs(s.contrast - 1.0) > 1e-3:
            base = self.apply_contrast(base, s.contrast)

        if abs(s.opacity - 1.0) > 1e-3:
            base = self.apply_opacity(base, s.opacity)

        if s.blur > 0:
            base = base.filter(ImageFilter.GaussianBlur(radius=float(s.blur)))

        if s.edge_enhance > 0:
            # Blend in an edge-enhanced version for a little punch.
            enhanced = base.filter(ImageFilter.EDGE_ENHANCE_MORE)
            base = Image.blend(base, enhanced, min(1.0, float(s.edge_enhance) / 2.0))

        if s.denoise > 0:
            base = base.filter(ImageFilter.MedianFilter(size=max(3, int(1 + round(float(s.denoise)) * 2))))

        if s.sharpen > 0:
            amount = float(s.sharpen)
            sharpened = base.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(70 + amount * 120), threshold=2))
            base = Image.blend(base, sharpened, min(1.0, amount / 3.0))

        if size is not None and base.size != (size, size):
            resample = self._effective_resample() if s.antialias else RESAMPLE.NEAREST
            base = self.resize_square(base, size, resample)

        return base

    @staticmethod
    def resize_square(img: Image.Image, size: int, resample: int) -> Image.Image:
        # Center-crop to square before resizing so the texture does not stretch oddly.
        if img.width != img.height:
            side = min(img.width, img.height)
            left = (img.width - side) // 2
            top = (img.height - side) // 2
            img = img.crop((left, top, left + side, top + side))
        return img.resize((size, size), resample=resample)

    @staticmethod
    def normalize_levels(img: Image.Image, keep_alpha: bool) -> Image.Image:
        r, g, b, a = img.split()
        gray = r
        bbox = gray.getbbox()
        if bbox is None:
            return img
        # Build min/max from histogram without slow pixel loops.
        hist = gray.histogram()
        lo = next((i for i, c in enumerate(hist) if c), 0)
        hi = next((255 - i for i, c in enumerate(reversed(hist)) if c), 255)
        if hi <= lo:
            return img
        gray = ImageOps.autocontrast(gray)
        if keep_alpha:
            return Image.merge("RGBA", (gray, gray, gray, a))
        return Image.merge("RGBA", (gray, gray, gray, Image.new("L", gray.size, 255)))

    @staticmethod
    def apply_gamma(img: Image.Image, gamma: float) -> Image.Image:
        if gamma <= 0:
            gamma = 1.0
        inv = 1.0 / gamma
        lut = [min(255, max(0, int((i / 255.0) ** inv * 255.0 + 0.5))) for i in range(256)]
        r, g, b, a = img.split()
        r = r.point(lut)
        g = g.point(lut)
        b = b.point(lut)
        return Image.merge("RGBA", (r, g, b, a))

    @staticmethod
    def apply_brightness(img: Image.Image, factor: float) -> Image.Image:
        return ImageEnhance.Brightness(img).enhance(factor)

    @staticmethod
    def apply_contrast(img: Image.Image, factor: float) -> Image.Image:
        return ImageEnhance.Contrast(img).enhance(factor)

    @staticmethod
    def apply_opacity(img: Image.Image, opacity: float) -> Image.Image:
        opacity = max(0.0, min(1.0, opacity))
        r, g, b, a = img.split()
        a = a.point(lambda p: int(p * opacity))
        return Image.merge("RGBA", (r, g, b, a))

    def settings_from_vars(self) -> Settings:
        self._sync_vars_to_settings()
        return self.settings

    # ------------------------- preview -------------------------
    def refresh_preview(self) -> None:
        if self._preview_timer is not None:
            self.after_cancel(self._preview_timer)
            self._preview_timer = None

        if self._busy:
            return
        if self.source_image is None:
            self.preview_info.set("No source image loaded.")
            self.canvas_source.configure(image="")
            self.canvas_result.configure(image="")
            return

        self._busy = True
        try:
            src = self.source_image.copy()
            result = self.process_image(src, size=BASE_SIZE)

            src_preview = self.prepare_preview_image(src)
            res_preview = self.prepare_preview_image(result)

            self.preview_source_tk = ImageTk.PhotoImage(src_preview)
            self.preview_result_tk = ImageTk.PhotoImage(res_preview)
            self.canvas_source.configure(image=self.preview_source_tk)
            self.canvas_result.configure(image=self.preview_result_tk)

            info = f"Source: {src.width}x{src.height} | Output: {result.width}x{result.height} | Export: {self.var_export_format.get()}"
            if self.var_export_format.get() == "DDS":
                texconv = find_texconv()
                if texconv:
                    info += " | texconv: found"
                else:
                    info += " | texconv: not found"
            self.preview_info.set(info)
            self._queue_status("Preview updated.")
        finally:
            self._busy = False

    def prepare_preview_image(self, img: Image.Image) -> Image.Image:
        preview = img.copy()
        if preview.width != preview.height:
            side = min(preview.width, preview.height)
            left = (preview.width - side) // 2
            top = (preview.height - side) // 2
            preview = preview.crop((left, top, left + side, top + side))
        preview.thumbnail((self.preview_max, self.preview_max), resample=RESAMPLE.LANCZOS)

        # Checkerboard behind transparency so alpha is visible.
        if preview.mode != "RGBA":
            preview = preview.convert("RGBA")
        bg = Image.new("RGBA", preview.size, (48, 48, 48, 255))
        checker = Image.new("RGBA", preview.size, (0, 0, 0, 0))
        tile = 12
        for y in range(0, preview.height, tile):
            for x in range(0, preview.width, tile):
                if ((x // tile) + (y // tile)) % 2 == 0:
                    Image.Image.paste(checker, Image.new("RGBA", (min(tile, preview.width - x), min(tile, preview.height - y)), (72, 72, 72, 255)), (x, y))
        bg = Image.alpha_composite(checker, preview)
        return bg

    # ------------------------- export -------------------------
    def export_all(self) -> None:
        if self.source_image is None:
            messagebox.showinfo(APP_NAME, "Load a source image first.")
            return

        target = self.var_target.get().strip()
        if not target:
            messagebox.showinfo(APP_NAME, "Choose a target folder first.")
            return

        out_dir = Path(target)
        out_dir.mkdir(parents=True, exist_ok=True)
        base = self.var_base_name.get().strip() or "colorize"
        export_format = self.var_export_format.get().strip().upper() or "DDS"
        overwrite = bool(self.var_overwrite.get())
        processed = self.process_image(self.source_image.copy(), size=BASE_SIZE)

        try:
            written = []
            self._write_one(processed, out_dir, base, "", export_format, overwrite)
            written.append(f"{base}.{export_format.lower()}")

            for key, size in VARIANTS:
                if not self.variant_vars[key].get():
                    continue
                img = self.process_image(self.source_image.copy(), size=size)
                suffix = f"_{key}"
                name = f"{base}{suffix}"
                self._write_one(img, out_dir, name, "", export_format, overwrite)
                written.append(f"{name}.{export_format.lower()}")

            self._log("Export done:")
            for item in written:
                self._log(f"  {item}")
            self._queue_status(f"Exported {len(written)} file(s).")
            self._schedule_save_settings()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Export failed:\n{e}")
            self._queue_status("Export failed.")

    def _write_one(
        self,
        img: Image.Image,
        out_dir: Path,
        name: str,
        _unused_suffix: str,
        export_format: str,
        overwrite: bool,
    ) -> None:
        export_format = export_format.upper()
        out_dir.mkdir(parents=True, exist_ok=True)

        if export_format == "DDS":
            self._export_dds(img, out_dir, name, overwrite)
        elif export_format == "TGA":
            self._export_simple(img, out_dir, name, ".tga", overwrite, mode="TGA")
        else:
            self._export_simple(img, out_dir, name, ".png", overwrite, mode="PNG")

    def _export_simple(self, img: Image.Image, out_dir: Path, name: str, ext: str, overwrite: bool, mode: str) -> None:
        out_path = out_dir / f"{name}{ext}"
        if out_path.exists() and not overwrite:
            raise FileExistsError(f"File exists: {out_path}")
        if mode == "TGA":
            img.save(out_path, format="TGA")
        else:
            img.save(out_path, format="PNG")

    def _export_dds(self, img: Image.Image, out_dir: Path, name: str, overwrite: bool) -> None:
        out_path = out_dir / f"{name}.dds"
        if out_path.exists() and not overwrite:
            raise FileExistsError(f"File exists: {out_path}")

        texconv = find_texconv()
        if texconv is None:
            # Fallback: save PNG with .dds name only if Pillow cannot do DDS.
            # Better than crashing, but not ideal. Humans do enjoy relying on miracles.
            try:
                img.save(out_path, format="DDS")
                return
            except Exception as e:
                raise RuntimeError(
                    "DDS export requires texconv.exe or Pillow DDS support. Put texconv.exe next to this script or on PATH."
                ) from e

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            src_png = td_path / f"{name}.png"
            img.save(src_png, format="PNG")
            cmd = [
                str(texconv),
                "-y",
                "-nologo",
                "-ft",
                "dds",
                "-f",
                self.var_dds_format.get().strip() or "DXT5",
                "-o",
                str(td_path),
                str(src_png),
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"texconv failed:\n{proc.stdout}\n{proc.stderr}")
            produced = list(td_path.glob(f"{src_png.stem}*.dds"))
            if not produced:
                produced = list(td_path.glob("*.dds"))
            if not produced:
                raise RuntimeError("texconv did not produce a DDS file.")
            shutil.copy2(produced[0], out_path)

    # ------------------------- helpers -------------------------
    def _on_close(self) -> None:
        try:
            self._sync_vars_to_settings()
            self.save_settings()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    app = BZCCColorizeTool()
    app.mainloop()


if __name__ == "__main__":
    main()
