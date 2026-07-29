from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any


FALLBACK_DATA_DIR = Path(tempfile.gettempdir()) / "astrbot_plugin_daily_life"


def runtime_data_path(value: Any = None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return (FALLBACK_DATA_DIR / "daily_life.db").resolve()


def runtime_data_root(value: Any = None) -> Path:
    path = runtime_data_path(value)
    return path.parent if path.suffix else path


def expand_path(value: Any, *, resolve: bool = False) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if resolve else path


def path_exists(value: Any) -> bool:
    try:
        return expand_path(value).exists()
    except OSError:
        return False


def path_is_file(value: Any) -> bool:
    try:
        return expand_path(value).is_file()
    except OSError:
        return False


def path_size(value: Any) -> int:
    return expand_path(value).stat().st_size
