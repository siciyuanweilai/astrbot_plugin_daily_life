from __future__ import annotations

import filecmp
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


FALLBACK_DATA_DIR = Path(tempfile.gettempdir()) / "astrbot_plugin_daily_life"
STYLE_CATALOG_DIR_NAME = "stylecatalog"
_LEGACY_STYLE_CATALOG_DIR_NAME = "style_catalog"


def runtime_data_path(value: Any = None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return (FALLBACK_DATA_DIR / "daily_life.db").resolve()


def runtime_data_root(value: Any = None) -> Path:
    path = runtime_data_path(value)
    return path.parent if path.suffix else path


def migrate_style_catalog_storage(value: Any = None) -> None:
    """将旧视觉衣橱目录和数据库图片路径迁移到单词目录名。"""
    root = runtime_data_root(value)
    legacy = root / _LEGACY_STYLE_CATALOG_DIR_NAME
    current = root / STYLE_CATALOG_DIR_NAME

    if legacy.is_dir():
        if not current.exists():
            legacy.rename(current)
        else:
            current.mkdir(parents=True, exist_ok=True)
            for source in legacy.iterdir():
                target = current / source.name
                if target.exists():
                    if not source.is_file() or not target.is_file() or not filecmp.cmp(
                        source, target, shallow=False
                    ):
                        raise OSError(f"视觉衣橱资源迁移冲突：{source.name}")
                    source.unlink()
                else:
                    shutil.move(str(source), str(target))
            legacy.rmdir()

    database = runtime_data_path(value)
    if not database.is_file():
        return
    old_prefix = str(legacy)
    new_prefix = str(current)
    if not old_prefix or old_prefix == new_prefix:
        return
    try:
        connection = sqlite3.connect(str(database), timeout=30.0)
        try:
            connection.execute(
                "UPDATE style_catalog_items "
                "SET image_path = replace(image_path, ?, ?) "
                "WHERE image_path LIKE ?",
                (old_prefix, new_prefix, f"{old_prefix}/%"),
            )
            connection.commit()
        finally:
            connection.close()
    except sqlite3.OperationalError as exc:
        # 新数据库尚未建表时，后续 schema 初始化会创建衣橱表。
        if "no such table" not in str(exc).lower():
            raise


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
