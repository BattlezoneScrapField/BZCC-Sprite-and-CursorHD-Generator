# ** THIS CONTENT IS AI GENERATED **

<img width="1434" height="873" alt="2026-05-26_103453" src="https://github.com/user-attachments/assets/68572fc3-0d1f-465b-9029-f853d0307106" />
## BZCC Sprite Generator

[Exe file you find here in "dist" folder.](https://drive.google.com/drive/folders/1TdC_JE8A9ezst-rZ1kxEcvU3EbNn16qA?usp=drive_link)

**BZCC Sprite Generator** is a unified GUI tool for creating cursor sprite sheets and sprite/color-map assets.  
It combines image processing, multi-format export, and automatic cursor configuration generation into a single workflow.

---

## Advantages

- **Two tools in one interface**  
  Cursor Baker and Sprite Generator are integrated into a single application.

- **Global export scaling (×1 to ×5)**  
  All output assets can be uniformly scaled using a global multiplier.

- **Persistent settings**  
  Paths, processing parameters, filenames, and UI states are automatically saved between sessions.

- **Live preview system**  
  Instant visual feedback without requiring manual re-export.

- **Live file monitoring**  
  Automatically detects changes in source files and refreshes previews.

- **Advanced image processing pipeline**  
  Built-in filters allow fast preprocessing before export.

- **Multi-format support**  
  Supports common texture formats including PNG, TGA, and DDS.

- **Compact workflow-focused UI**  
  Designed to reduce clicks and eliminate unnecessary steps.

---

## Core Features

### Cursor Baker
- Load sprite sheets or 64-frame image sequences
- Automatic 8×8 frame slicing (1024×1024 source layout)
- Frame navigation and animation preview
- Cursor configuration:
  - Name assignment
  - Hotspot X/Y control
  - FPS control
  - Anti-aliasing toggle
- Batch export of cursor frames to `.TGA`
- Automatic generation of `bzgame_init_cursor.cfg`
- Dual cursor support:
  - Default Cursor
  - Highlight Cursor

---

### Sprite Generator
- Load single source images (PNG, TGA, DDS, etc.)
- Export to `PNG / TGA / DDS`
- DDS compression modes:
  - DXT5
  - BC3_UNORM
  - BC3_UNORM_SRGB
  - DXT3
- Multi-size export variants:
  - x1.0 → x5.0 scale presets
- Image preprocessing options:
  - Grayscale conversion
  - Invert
  - Normalize levels
  - Keep alpha channel
  - Antialiasing toggle
  - Resampling modes
- Live reload + preview system
- Export logging system

---

### Image Processing Pipeline
- Sharpen
- Blur
- Gamma correction
- Brightness
- Contrast
- Opacity control
- Edge enhancement
- Denoise filtering

---

## What it is useful for

- Cursor animation creation for games
- Sprite sheet generation for UI and VFX
- Batch texture scaling and export
- Image preprocessing before engine import
- Automated cursor configuration generation

---

## Key Highlights

- Unified workflow for cursor + sprite production
- Non-destructive image processing pipeline
- Persistent project state across sessions
- Batch export system with scaling support
- Real-time preview and live reload
- Automation of repetitive texture tasks

- <img width="1434" height="873" alt="2026-05-26_103456" src="https://github.com/user-attachments/assets/dcfb2bfe-5125-451e-866c-58b6ebc2dbe1" />
