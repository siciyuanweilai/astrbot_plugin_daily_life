import asyncio
import shutil
from pathlib import Path

from astrbot.api import logger

from ....clock import now as life_now
from ...markers import LOG_PREFIX


class EmojiCacheMixin:

    async def _cache_emoji_asset_file(self, payload: dict[str, str], fingerprint: str) -> str:
        cache_dir = self._emoji_asset_cache_dir()
        source = self._emoji_asset_source(payload)
        if not cache_dir or not source:
            return source
        if source.startswith(("http://", "https://")):
            cached = await self._download_emoji_asset(source, fingerprint, cache_dir)
            return str(cached) if cached else source
        cached = await self._copy_emoji_asset(source, fingerprint, cache_dir)
        return str(cached) if cached else source

    async def cleanup_emoji_asset_cache(self) -> int:
        cache_dir = self._emoji_asset_cache_dir(create=False)
        if not cache_dir or not cache_dir.is_dir():
            return 0

        referenced_paths = set()
        for asset in await self.archive.get_emoji_assets(limit=0):
            path_text = str(getattr(asset, "file_path", "") or "").strip()
            resolved = self._resolve_cached_emoji_path(path_text, cache_dir)
            if resolved:
                referenced_paths.add(resolved)

        deleted = 0
        now_ts = life_now().timestamp()
        for path in cache_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in self.EMOJI_ASSET_SUFFIXES:
                continue
            try:
                resolved = path.resolve()
                file_stat = path.stat()
            except (OSError, RuntimeError):
                continue
            if resolved in referenced_paths:
                continue
            if now_ts - file_stat.st_mtime < self.EMOJI_ASSET_ORPHAN_GRACE_SECONDS:
                continue
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.debug(f"{LOG_PREFIX} 表情素材缓存清理跳过：{exc}")
        if deleted:
            logger.info(f"{LOG_PREFIX} 已清理未引用表情素材缓存 {deleted} 个")
        return deleted

    async def _copy_emoji_asset(self, source: str, fingerprint: str, cache_dir: Path) -> Path | None:
        try:
            source_path = Path(source).expanduser().resolve()
            if not source_path.is_file():
                return None
            suffix = self._emoji_asset_suffix(source, content_type="")
            target_path = cache_dir / self._emoji_asset_filename(fingerprint, suffix)
            if source_path == target_path.resolve():
                return target_path
            if not target_path.exists():
                await asyncio.to_thread(shutil.copy2, source_path, target_path)
            return target_path
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 表情素材本地缓存跳过：{exc}")
            return None

    async def _download_emoji_asset(self, url: str, fingerprint: str, cache_dir: Path) -> Path | None:
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    content_type = str(response.headers.get("Content-Type", "") or "")
                    if content_type and not content_type.lower().startswith("image/"):
                        return None
                    data = await response.content.read(self.EMOJI_ASSET_MAX_BYTES + 1)
            if not data or len(data) > self.EMOJI_ASSET_MAX_BYTES:
                return None
            suffix = self._emoji_asset_suffix(url, data=data, content_type=content_type)
            target_path = cache_dir / self._emoji_asset_filename(fingerprint, suffix)
            if not target_path.exists():
                await asyncio.to_thread(target_path.write_bytes, data)
            return target_path
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 表情素材联网缓存跳过：{exc}")
            return None
