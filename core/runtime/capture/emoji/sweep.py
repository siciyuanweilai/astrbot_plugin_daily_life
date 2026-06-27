from pathlib import Path

from astrbot.api import logger

from ....models import EmojiAssetRecord
from ...markers import LOG_PREFIX


class EmojiSweepMixin:

    async def maintain_emoji_assets(self) -> dict[str, int]:
        assets = await self.archive.get_emoji_assets(limit=0)
        updated = 0
        repaired = 0

        for asset in assets:
            status = str(getattr(asset, "status", "") or "pending")
            path_text = str(getattr(asset, "file_path", "") or "").strip()
            if status in {"disabled", "failed"}:
                continue
            if not path_text or self._emoji_asset_is_remote(path_text) or Path(path_text).exists():
                continue

            source = self._remote_source_from_asset(asset)
            if source:
                cache_dir = self._emoji_asset_cache_dir()
                cached_path = await self._download_emoji_asset(source, asset.file_hash, cache_dir) if cache_dir else None
                if cached_path:
                    await self.archive.upsert_emoji_asset(
                        EmojiAssetRecord(
                            id=asset.id,
                            file_hash=asset.file_hash,
                            file_path=str(cached_path),
                            label=asset.label,
                            description=asset.description,
                            emotions=asset.emotions,
                            source_scope=asset.source_scope,
                            source_message_id=asset.source_message_id,
                            source_url=asset.source_url,
                            status=status,
                            used_count=asset.used_count,
                            last_used_at=asset.last_used_at,
                        )
                    )
                    repaired += 1
                    continue

            await self.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    id=asset.id,
                    file_hash=asset.file_hash,
                    file_path=asset.file_path,
                    label=asset.label,
                    description=asset.description,
                    emotions=asset.emotions,
                    source_scope=asset.source_scope,
                    source_message_id=asset.source_message_id,
                    source_url=asset.source_url,
                    status="missing",
                    used_count=asset.used_count,
                    last_used_at=asset.last_used_at,
                )
            )
            updated += 1

        deleted_records = await self._prune_extra_ready_emoji_assets()
        deleted_files = await self.cleanup_emoji_asset_cache()
        result = {
            "missing_marked": updated,
            "repaired": repaired,
            "deleted_records": deleted_records,
            "deleted_files": deleted_files,
        }
        if any(result.values()):
            logger.info(
                f"{LOG_PREFIX} 表情素材维护完成：标记缺失 {updated} 个，修复 {repaired} 个，"
                f"裁剪记录 {deleted_records} 条，清理缓存 {deleted_files} 个"
            )
        return result

    def _remote_source_from_asset(self, asset: EmojiAssetRecord) -> str:
        source = str(getattr(asset, "source_url", "") or "").strip()
        return source if self._emoji_asset_is_remote(source) else ""

    async def _prune_extra_ready_emoji_assets(self) -> int:
        ready_assets = await self.archive.get_emoji_assets(limit=0, status="ready")
        overflow = len(ready_assets) - self.EMOJI_ASSET_MAX_READY
        if overflow <= 0:
            return 0

        def rank(asset: EmojiAssetRecord) -> tuple[int, str, int]:
            return (
                int(getattr(asset, "used_count", 0) or 0),
                str(getattr(asset, "last_used_at", "") or getattr(asset, "updated_at", "") or ""),
                int(getattr(asset, "id", 0) or 0),
            )

        to_delete = sorted(ready_assets, key=rank)[:overflow]
        return await self.archive.delete_emoji_assets([item.id for item in to_delete])
