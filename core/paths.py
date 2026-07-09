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
