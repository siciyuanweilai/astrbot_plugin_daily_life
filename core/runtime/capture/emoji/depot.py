import asyncio
import base64
import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from astrbot.api import logger

from ....clock import now as life_now
from ...markers import LOG_PREFIX


class EmojiCacheMixin:
    async def _cache_emoji_asset_file(
        self, payload: dict[str, str], fingerprint: str
    ) -> str:
        source = self._emoji_asset_source(payload)
        cached = await self._cache_emoji_asset_path(payload, fingerprint)
        return str(cached) if cached else source

    async def _cache_emoji_asset_path(
        self,
        payload: dict[str, str],
        fingerprint: str,
        *,
        log_failure: bool = True,
    ) -> Path | None:
        cache_dir = self._emoji_asset_cache_dir()
        source = self._emoji_asset_source(payload)
        if not cache_dir or not source:
            return None
        if source.startswith("data:image/") or source.startswith("base64://"):
            return await self._decode_emoji_asset_data(
                source, fingerprint, cache_dir, log_failure=log_failure
            )
        if source.startswith("file://"):
            source = self._local_path_from_file_uri(source)
        if source.startswith(("http://", "https://")):
            return await self._download_emoji_asset(
                source, fingerprint, cache_dir, log_failure=log_failure
            )
        return await self._copy_emoji_asset(
            source, fingerprint, cache_dir, log_failure=log_failure
        )

    async def cleanup_emoji_asset_cache(self) -> int:
        cache_dir = self._emoji_asset_cache_dir(create=False)
        if not cache_dir or not await asyncio.to_thread(cache_dir.is_dir):
            return 0

        referenced_paths = set()
        for asset in await self.archive.get_emoji_assets(limit=0):
            path_text = str(getattr(asset, "file_path", "") or "").strip()
            resolved = self._resolve_cached_emoji_path(path_text, cache_dir)
            if resolved:
                referenced_paths.add(resolved)

        deleted = 0
        now_ts = life_now().timestamp()
        paths = await asyncio.to_thread(lambda: list(cache_dir.iterdir()))
        for path in paths:
            if (
                not await asyncio.to_thread(path.is_file)
                or path.suffix.lower() not in self.EMOJI_ASSET_SUFFIXES
            ):
                continue
            try:
                resolved, file_stat = await asyncio.to_thread(
                    lambda: (path.resolve(), path.stat())
                )
            except (OSError, RuntimeError):
                continue
            if resolved in referenced_paths:
                continue
            if now_ts - file_stat.st_mtime < self._emoji_orphan_grace_seconds():
                continue
            try:
                await asyncio.to_thread(path.unlink)
                deleted += 1
            except OSError as exc:
                logger.debug(f"{LOG_PREFIX} 表情素材缓存清理跳过：{exc}")
        if deleted:
            logger.info(f"{LOG_PREFIX} 已清理未引用表情素材缓存 {deleted} 个")
        return deleted

    async def _copy_emoji_asset(
        self,
        source: str,
        fingerprint: str,
        cache_dir: Path,
        *,
        log_failure: bool = True,
    ) -> Path | None:
        temporary_path: Path | None = None
        try:
            source_path = await asyncio.to_thread(
                lambda: Path(source).expanduser().resolve()
            )
            if not await asyncio.to_thread(source_path.is_file):
                return None
            size = await asyncio.to_thread(lambda: source_path.stat().st_size)
            if not self._emoji_asset_size_allowed(size):
                return None
            suffix = self._emoji_asset_suffix(source, content_type="")
            target_path = cache_dir / self._emoji_asset_filename(fingerprint, suffix)
            if source_path == await asyncio.to_thread(target_path.resolve):
                return target_path
            if not await asyncio.to_thread(target_path.exists):
                temporary_path = self._emoji_asset_temporary_path(target_path)
                await asyncio.to_thread(shutil.copy2, source_path, temporary_path)
                await asyncio.to_thread(os.replace, temporary_path, target_path)
            return target_path
        except FileNotFoundError:
            return None
        except Exception as exc:
            if log_failure:
                logger.debug(f"{LOG_PREFIX} 表情素材本地缓存跳过：{exc}")
            return None
        finally:
            await self._remove_emoji_asset_temporary_path(temporary_path)

    @staticmethod
    def _emoji_asset_temporary_path(target_path: Path) -> Path:
        return target_path.with_name(
            f".{target_path.name}.{uuid.uuid4().hex}.tmp"
        )

    @staticmethod
    async def _remove_emoji_asset_temporary_path(path: Path | None) -> None:
        if path is None:
            return
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            pass

    @classmethod
    def _write_emoji_asset_bytes(cls, target_path: Path, data: bytes) -> None:
        temporary_path = cls._emoji_asset_temporary_path(target_path)
        try:
            temporary_path.write_bytes(data)
            os.replace(temporary_path, target_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _local_path_from_file_uri(source: str) -> str:
        parsed = urlparse(str(source or ""))
        if parsed.scheme != "file":
            return source
        path = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc.lower() not in {"localhost", ""}:
            path = f"//{parsed.netloc}{path}"
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path

    async def _decode_emoji_asset_data(
        self,
        source: str,
        fingerprint: str,
        cache_dir: Path,
        *,
        log_failure: bool = True,
    ) -> Path | None:
        try:
            text = str(source or "").strip()
            content_type = ""
            encoded = ""
            if text.startswith("base64://"):
                encoded = text[len("base64://") :]
                content_type = "image/png"
            else:
                header, encoded = text.split(",", 1)
                content_type = header[5:].split(";", 1)[0]
            data = base64.b64decode(encoded, validate=False)
            if not data or not self._emoji_asset_size_allowed(len(data)):
                return None
            suffix = self._emoji_asset_suffix("", data=data, content_type=content_type)
            target_path = cache_dir / self._emoji_asset_filename(fingerprint, suffix)
            if not await asyncio.to_thread(target_path.exists):
                await asyncio.to_thread(
                    self._write_emoji_asset_bytes, target_path, data
                )
            return target_path
        except Exception as exc:
            if log_failure:
                logger.debug(f"{LOG_PREFIX} 表情素材内联缓存跳过：{exc}")
            return None

    async def _download_emoji_asset(
        self,
        url: str,
        fingerprint: str,
        cache_dir: Path,
        *,
        log_failure: bool = True,
    ) -> Path | None:
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
                    max_bytes = self._emoji_max_bytes()
                    data = await response.content.read(
                        max_bytes + 1 if max_bytes > 0 else -1
                    )
            if not data or not self._emoji_asset_size_allowed(len(data)):
                return None
            suffix = self._emoji_asset_suffix(url, data=data, content_type=content_type)
            target_path = cache_dir / self._emoji_asset_filename(fingerprint, suffix)
            if not await asyncio.to_thread(target_path.exists):
                await asyncio.to_thread(
                    self._write_emoji_asset_bytes, target_path, data
                )
            return target_path
        except Exception as exc:
            if log_failure:
                logger.debug(f"{LOG_PREFIX} 表情素材联网缓存跳过：{exc}")
            return None
