"""Dependances facultatives : importees une fois, `None` si absentes (`MISSING` liste les paquets a installer)."""
from __future__ import annotations

MISSING: list[str] = []


def _optional_import(module: str, package: str):
    try:
        return __import__(module, fromlist=["_"])
    except Exception:  # ImportError, ou OSError si PortAudio manque pour sounddevice
        MISSING.append(package)
        return None


np = _optional_import("numpy", "numpy")
glfw = _optional_import("glfw", "glfw")
moderngl = _optional_import("moderngl", "moderngl")
sd = _optional_import("sounddevice", "sounddevice")
PIL_Image = _optional_import("PIL.Image", "Pillow")
PIL_ImageDraw = _optional_import("PIL.ImageDraw", "Pillow")
PIL_ImageFont = _optional_import("PIL.ImageFont", "Pillow")
PIL_ImageOps = _optional_import("PIL.ImageOps", "Pillow")
