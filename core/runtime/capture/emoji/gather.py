import datetime
import inspect
from typing import Any

from ....clock import now as life_now
from ....models import EmojiAssetRecord


class EmojiGatherMixin:

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
        if self._event_has_command_handler(event) or self.event_was_recalled(event, log_skip=True):
            return
        now = now or life_now()
        sender_name = sender_name or await self.contact_resolver.resolve_event_sender(event)
        if self.event_was_recalled(event, log_skip=True):
            return
        meta = await self._event_context_meta(event, sender_name, now)
        scope = meta.get("group_id") or meta.get("session_id") or ""
        context_scope = meta.get("session_id") or ""
        context_message_key = meta.get("message_id") or f"event:{id(event)}"
        saved: list[EmojiAssetRecord] = []
        payloads: dict[str, dict[str, str]] = {}
        for item in self._event_message_items(event):
            if not self._emoji_asset_is_image_item(item):
                continue
            payload = self._message_media_payload(item)
            fingerprint = self._media_fingerprint(payload)
            if inspect.isawaitable(fingerprint):
                fingerprint = await fingerprint
            if not fingerprint:
                continue
            if self.event_was_recalled(event, log_skip=True):
                return
            existing = await self.archive.get_emoji_asset_by_hash(fingerprint)
            if existing and str(existing.status or "") in {"failed", "disabled"}:
                continue
            if existing and str(existing.status or "") == "ready":
                cached_summary = self._visual_context_summary_cache().get(fingerprint, "")
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
            path_text = payload.get("path") or payload.get("file") or payload.get("url") or payload.get("image") or ""
            asset = await self.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    file_hash=fingerprint,
                    file_path=path_text,
                    source_scope=scope,
                    source_message_id=meta.get("message_id", ""),
                    source_url=path_text if self._emoji_asset_is_remote(path_text) else "",
                    status="pending",
                )
            )
            if self.event_was_recalled(event, log_skip=True):
                return
            if asset:
                saved.append(asset)
                payloads[str(asset.id)] = payload
        if self.event_was_recalled(event, log_skip=True):
            return
        for asset in saved[:3]:
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
        if event is None or self._event_has_command_handler(event) or self.event_was_recalled(event, log_skip=True):
            return False
        if not any(
            self._emoji_asset_is_image_item(item) and self._message_media_payload(item)
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
            return
        if self.event_was_recalled(event, log_skip=True):
            return
        vision_call = getattr(provider, "image_chat", None) or getattr(provider, "vision_chat", None)
        if not callable(vision_call):
            return

        for item in self._event_message_items(event):
            if self.event_was_recalled(event, log_skip=True):
                return
            if not self._emoji_asset_is_image_item(item):
                continue
            payload = self._message_media_payload(item)
            fingerprint = self._media_fingerprint(payload)
            if inspect.isawaitable(fingerprint):
                fingerprint = await fingerprint
            if not fingerprint:
                continue

            cached_summary = self._visual_context_summary_cache().get(fingerprint, "")
            if cached_summary:
                self._apply_visual_context_summary_text(scope, message_key, cached_summary)
                continue

            path = self._emoji_asset_source(payload)
            if not path:
                continue
            await self._describe_visual_context_with_vision(
                vision_call,
                path,
                scope,
                message_key,
                fingerprint,
            )
