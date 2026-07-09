from __future__ import annotations

import importlib
import shutil
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def ffmpeg_executable() -> str | None:
    system = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if system:
        return system
    return _imageio_ffmpeg()


@lru_cache(maxsize=1)
def ffprobe_executable() -> str | None:
    system = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if system:
        return system
    ffmpeg = _imageio_ffmpeg()
    if not ffmpeg:
        return None
    sibling = Path(ffmpeg).with_name("ffprobe.exe" if Path(ffmpeg).suffix.lower() == ".exe" else "ffprobe")
    return str(sibling) if sibling.is_file() else None


@lru_cache(maxsize=1)
def ytdlp_ffmpeg_location() -> str | None:
    if shutil.which("ffmpeg") or shutil.which("ffmpeg.exe"):
        return None
    return _imageio_ffmpeg()


def _imageio_ffmpeg() -> str | None:
    try:
        imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
        return str(imageio_ffmpeg.get_ffmpeg_exe() or "").strip() or None
    except Exception:
        return None
