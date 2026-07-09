from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from astrbot.api import logger

from ..clock import now as life_now
from ..runtime.markers import LOG_PREFIX
from .sample import sight_cache_dir


class SightCleanupMixin:
    async def maintain_sight_cache(self) -> dict[str, int]:
        cache_dir = sight_cache_dir(getattr(self, "data_path", None))
        settings = getattr(getattr(self, "config", None), "sight", None)
        keep_days = max(1, int(getattr(settings, "sight_cache_keep_days", 7) or 7))
        cutoff = life_now().timestamp() - keep_days * 86400
        targets = [
            cache_dir / "frames",
            cache_dir / "media",
            cache_dir / "audio",
            cache_dir / "transcripts",
            cache_dir / "brief",
            cache_dir / "prepare",
        ]

        deleted_files = 0
        deleted_dirs = 0
        for target in targets:
            if not target.is_dir():
                continue
            files, dirs = await asyncio.to_thread(self._prune_cache_tree, target, cutoff)
            deleted_files += files
            deleted_dirs += dirs

        if deleted_files or deleted_dirs:
            logger.info(
                f"{LOG_PREFIX} 视听缓存清理完成：清理文件 {deleted_files} 个，清理目录 {deleted_dirs} 个"
            )
        return {"deleted_files": deleted_files, "deleted_dirs": deleted_dirs}

    @staticmethod
    def _prune_cache_tree(root: Path, cutoff: float) -> tuple[int, int]:
        return (
            SightCleanupMixin._prune_cache_files(root, cutoff),
            SightCleanupMixin._prune_empty_cache_dirs(root),
        )

    @staticmethod
    def _prune_cache_files(root: Path, cutoff: float) -> int:
        deleted = 0
        paths = [path for path in root.rglob("*") if path.is_file()]
        for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            with contextlib.suppress(OSError):
                path.unlink()
                deleted += 1
        return deleted

    @staticmethod
    def _prune_empty_cache_dirs(root: Path) -> int:
        deleted = 0
        dirs = [path for path in root.rglob("*") if path.is_dir()]
        for path in sorted(dirs, key=lambda item: len(item.parts), reverse=True):
            with contextlib.suppress(OSError):
                if path.exists() and not any(path.iterdir()):
                    path.rmdir()
                    deleted += 1
        return deleted
