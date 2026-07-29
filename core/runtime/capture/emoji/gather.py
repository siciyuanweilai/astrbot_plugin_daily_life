import asyncio
import datetime
import inspect
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ....clock import now as life_now
from ....models import EmojiAssetRecord
from ...markers import LOG_PREFIX


class EmojiGatherMixin:
    _PREPARED_VISUAL_MEDIA_ATTR = "_daily_life_prepared_visual_media"
    _VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS = 5.0

    @staticmethod
    def _visual_media_sources(payload: dict[str, str]) -> list[str]:
        sources: list[str] = []
        for key in ("path", "file", "url", "image"):
            value = str(payload.get(key) or "").strip()
            if value and value not in sources:
                sources.append(value)
        return sources

    async def _first_existing_visual_media_path(
        self, sources: list[str]
    ) -> str:
        for source in sources:
            value = str(source or "").strip()
            if not value or value.startswith(
                ("http://", "https://", "data:image/", "base64://")
            ):
                continue
            if value.startswith("file://"):
                value = self._local_path_from_file_uri(value)
            path = Path(value).expanduser()
            if await asyncio.to_thread(path.is_file):
                return str(path)
        return ""

    @staticmethod
    def _visual_media_has_durable_source(sources: list[str]) -> bool:
        return any(
            str(source or "").strip().startswith(
                ("http://", "https://", "data:image/", "base64://")
            )
            for source in sources
        )

    async def _build_prepared_visual_media(self, event: Any) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for item in self._event_message_items(event):
            if not (
                self._emoji_asset_is_image_item(item)
                or self._emoji_asset_source_kind(item)
            ):
                continue
            payload = dict(self._message_media_payload(item))
            sources = self._visual_media_sources(payload)
            local_source = await self._first_existing_visual_media_path(sources)
            if not local_source and not self._visual_media_has_durable_source(sources):
                payload = await self._media_payload_from_item_async(item)
                sources = self._visual_media_sources(payload)
                local_source = await self._first_existing_visual_media_path(sources)

            fingerprint = self._media_fingerprint(payload)
            if inspect.isawaitable(fingerprint):
                fingerprint = await fingerprint
            fingerprint = str(fingerprint or "").strip()
            if not fingerprint:
                continue

            stable_path = ""
            state = "deferred" if self._visual_media_has_durable_source(sources) else "failed"
            if local_source:
                cached_path = await self._cache_emoji_asset_path(
                    {"path": local_source},
                    fingerprint,
                    log_failure=False,
                )
                if cached_path:
                    stable_path = str(cached_path)
                    payload["path"] = stable_path
                    state = "ready"
                    media_type = cached_path.suffix.lstrip(".").upper() or "图片"
                    logger.debug(
                        f"{LOG_PREFIX} 图片缓存准备完成：来源=临时文件；类型={media_type}"
                    )
                else:
                    state = (
                        "transient"
                        if self._emoji_asset_cache_dir(create=False) is None
                        else "failed"
                    )
            entries.append(
                {
                    "item": item,
                    "payload": payload,
                    "fingerprint": fingerprint,
                    "path": stable_path,
                    "cache_sources": self._emoji_asset_cache_candidates(
                        stable_path, sources
                    ),
                    "state": state,
                }
            )
        return entries

    async def prepare_visual_media_from_event(self, event: Any) -> bool:
        """在框架清理事件临时文件前，将本地图片固化到插件缓存。"""

        if event is None or self.event_was_recalled(event, log_skip=True):
            return False
        if not any(
            self._emoji_asset_is_image_item(item)
            or self._emoji_asset_source_kind(item)
            for item in self._event_message_items(event)
        ):
            setattr(event, self._PREPARED_VISUAL_MEDIA_ATTR, [])
            return False
        started_at = time.monotonic()
        try:
            entries = await asyncio.wait_for(
                self._build_prepared_visual_media(event),
                timeout=self._VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            entries = []
            logger.debug(
                f"{LOG_PREFIX} 图片前置固化超时，已跳过；"
                f"上限={self._VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS:.0f}秒"
            )
        setattr(event, self._PREPARED_VISUAL_MEDIA_ATTR, entries)
        elapsed = time.monotonic() - started_at
        if elapsed >= 0.1:
            logger.debug(f"{LOG_PREFIX} 图片前置固化耗时 {elapsed:.2f} 秒")
        return any(str(entry.get("path") or "") for entry in entries)

    async def _prepared_visual_media_from_event(
        self, event: Any
    ) -> list[dict[str, Any]]:
        prepared = getattr(event, self._PREPARED_VISUAL_MEDIA_ATTR, None)
        if isinstance(prepared, list):
            return prepared
        prepared = await self._build_prepared_visual_media(event)
        setattr(event, self._PREPARED_VISUAL_MEDIA_ATTR, prepared)
        return prepared

    async def _cache_and_describe_emoji_asset(
        self,
        asset: EmojiAssetRecord,
        payload: dict[str, str],
        *,
        context_scope: str = "",
        context_message_key: str = "",
    ) -> None:
        cached_path = await self._cache_emoji_asset_file(payload, asset.file_hash)
        if cached_path and cached_path != asset.file_path:
            refreshed = await self.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    id=asset.id,
                    file_hash=asset.file_hash,
                    file_path=cached_path,
                    label=asset.label,
                    description=asset.description,
                    emotions=asset.emotions,
                    source_scope=asset.source_scope,
                    source_message_id=asset.source_message_id,
                    source_url=asset.source_url,
                    source_kind=asset.source_kind,
                    asset_type=asset.asset_type,
                    confidence=asset.confidence,
                    sendable=asset.sendable,
                    rejected_reason=asset.rejected_reason,
                    status=asset.status,
                    used_count=asset.used_count,
                    last_used_at=asset.last_used_at,
                    created_at=asset.created_at,
                    updated_at=asset.updated_at,
                )
            )
            if refreshed:
                asset = refreshed
        await self._describe_emoji_asset_with_vision(
            asset,
            context_scope=context_scope,
            context_message_key=context_message_key,
        )

    async def maybe_collect_emoji_assets_from_event(
        self,
        event: Any,
        now: datetime.datetime | None = None,
        sender_name: str = "",
    ) -> None:
        if not self._emoji_auto_collect_enabled():
            return
        if self._event_has_command_handler(event) or self.event_was_recalled(
            event, log_skip=True
        ):
            return
        now = now or life_now()
        sender_name = sender_name or await self.contact_resolver.resolve_event_sender(
            event
        )
        if self.event_was_recalled(event, log_skip=True):
            return
        meta = await self._event_context_meta(event, sender_name, now)
        scope = meta.get("group_id") or meta.get("session_id") or ""
        context_scope = meta.get("session_id") or ""
        context_message_key = meta.get("message_id") or f"event:{id(event)}"
        saved: list[EmojiAssetRecord] = []
        payloads: dict[str, dict[str, str]] = {}
        for entry in await self._prepared_visual_media_from_event(event):
            item = entry.get("item")
            source_kind = self._emoji_asset_source_kind(item)
            if source_kind not in {"trusted", "review"}:
                continue
            if entry.get("state") == "failed":
                continue
            payload = dict(entry.get("payload") or {})
            fingerprint = str(entry.get("fingerprint") or "")
            if not fingerprint:
                continue
            if self.event_was_recalled(event, log_skip=True):
                return
            existing = await self.archive.get_emoji_asset_by_hash(fingerprint)
            if (
                existing
                and str(existing.status or "") in self.EMOJI_ASSET_REJECTED_STATUSES
            ):
                continue
            if existing and str(existing.status or "") == "ready":
                cached_summary = self._visual_context_summary_cache().get(
                    fingerprint, ""
                )
                if cached_summary:
                    self._apply_visual_context_summary_text(
                        context_scope,
                        context_message_key,
                        cached_summary,
                    )
                    continue
                saved.append(existing)
                payloads[str(existing.id)] = payload
                continue
            if not await self._emoji_can_accept_ready_asset(exclude_hash=fingerprint):
                continue
            path_text = (
                payload.get("path")
                or payload.get("file")
                or payload.get("url")
                or payload.get("image")
                or ""
            )
            status = "pending" if source_kind == "trusted" else "reviewing"
            asset = await self.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    file_hash=fingerprint,
                    file_path=path_text,
                    source_scope=scope,
                    source_message_id=meta.get("message_id", ""),
                    source_url=path_text
                    if self._emoji_asset_is_remote(path_text)
                    else "",
                    source_kind=source_kind,
                    asset_type=self._emoji_asset_type_from_item(item),
                    sendable=False,
                    status=status,
                )
            )
            if self.event_was_recalled(event, log_skip=True):
                return
            if asset:
                saved.append(asset)
                payloads[str(asset.id)] = payload
        if self.event_was_recalled(event, log_skip=True):
            return
        for asset in saved[: self._emoji_review_batch_size()]:
            self._schedule_background_task(
                self._cache_and_describe_emoji_asset(
                    asset,
                    payloads.get(str(asset.id), {}),
                    context_scope=context_scope,
                    context_message_key=context_message_key,
                ),
                label="表情素材缓存与识别",
                key=f"emoji_asset_vision:{asset.id}",
            )

    def schedule_visual_context_from_event(self, event: Any) -> bool:
        if (
            event is None
            or self._event_has_command_handler(event)
            or self.event_was_recalled(event, log_skip=True)
        ):
            return False
        if not any(
            self._emoji_asset_is_image_item(item)
            for item in self._event_message_items(event)
        ):
            return False
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event)
        if not scope:
            return False
        key = f"visual_context:{scope}:{message_id or id(event)}"
        return self._schedule_background_task(
            self._collect_visual_context_background(event),
            label="图片上下文识别",
            key=key,
        )

    async def _collect_visual_context_background(self, event: Any) -> None:
        if self.event_was_recalled(event, log_skip=True):
            return
        scope = self._event_session_id(event)
        message_key = self._event_message_id(event) or f"event:{id(event)}"
        if not scope or not message_key:
            return

        provider = await self._get_vision_provider()
        if not provider:
            logger.debug(f"{LOG_PREFIX} 图片上下文识别跳过：未配置可用视觉模型")
            return
        if self.event_was_recalled(event, log_skip=True):
            return
        if not any(
            callable(getattr(provider, name, None))
            for name in ("text_chat", "image_chat", "vision_chat")
        ):
            logger.debug(f"{LOG_PREFIX} 图片上下文识别跳过：视觉模型不支持图片输入")
            return

        for entry in await self._prepared_visual_media_from_event(event):
            if self.event_was_recalled(event, log_skip=True):
                return
            fingerprint = str(entry.get("fingerprint") or "")
            if not fingerprint:
                continue

            cached_summary = self._visual_context_summary_cache().get(fingerprint, "")
            if cached_summary:
                self._apply_visual_context_summary_text(
                    scope, message_key, cached_summary
                )
                continue

            path = str(entry.get("path") or "")
            cache_sources = list(entry.get("cache_sources") or [])
            if not path and entry.get("state") in {"deferred", "transient"}:
                path, cache_sources = await self._emoji_asset_prepare_vision_source(
                    cache_sources, fingerprint
                )
            if not path:
                logger.debug(
                    f"{LOG_PREFIX} 图片上下文识别跳过：图片临时文件已失效，且无可用备用来源"
                )
                continue
            await self._describe_visual_context_with_vision(
                provider,
                path,
                scope,
                message_key,
                fingerprint,
                cache_sources=cache_sources,
            )
