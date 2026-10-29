from __future__ import annotations

import asyncio
import datetime
import inspect
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.agent.message import TextPart

from ....clock import now as life_now
from ....models import EmojiAssetRecord
from ....sight.animated import is_animated_gif
from ...markers import LOG_PREFIX
from ...foreground import foreground_budget

from typing import Protocol


class EmojiGatherComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    EMOJI_ASSET_REJECTED_STATUSES: Any
    _GIF_CONTEXT_FUTURE_ATTR: Any
    _GIF_VISION_BRIDGE_TIMEOUT_SECONDS: Any
    _PREPARED_VISUAL_MEDIA_ATTR: Any
    _VISUAL_CONTEXT_FUTURE_ATTR: Any
    _VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS: Any
    _append_gif_visual_summary: Any
    _apply_visual_context_summary_text: Any
    _cache_emoji_asset_file: Any
    _cache_emoji_asset_path: Any
    _classify_plain_image_emoji_candidate: Any
    _describe_emoji_asset_with_vision: Any
    _describe_visual_context_with_vision: Any
    _emoji_asset_cache_candidates: Any
    _emoji_asset_cache_dir: Any
    _emoji_asset_is_image_item: Any
    _emoji_asset_is_remote: Any
    _emoji_asset_prepare_vision_source: Any
    _emoji_asset_source_kind: Any
    _emoji_asset_type_from_item: Any
    _emoji_auto_collect_enabled: Any
    _emoji_can_accept_ready_asset: Any
    _emoji_review_batch_size: Any
    _event_context_meta: Any
    _event_has_command_handler: Any
    _event_message_id: Any
    _event_message_items: Any
    _event_session_id: Any
    _local_path_from_file_uri: Any
    _media_fingerprint: Any
    _media_payload_from_item_async: Any
    _message_media_payload: Any
    _plain_image_emoji_review_cached: Any
    _schedule_background_task: Any
    _visual_context_cache_key: Any
    _visual_context_summary_cache: Any
    _visual_media_has_durable_source: Any
    _visual_media_sources: Any
    archive: Any
    contact_resolver: Any
    config: Any
    event_was_recalled: Any


class EmojiGatherComponent:
    def __init__(self, context: EmojiGatherComponentContext) -> None:
        self.context = context

    _PREPARED_VISUAL_MEDIA_ATTR = "_daily_life_prepared_visual_media"
    _VISUAL_CONTEXT_FUTURE_ATTR = "_daily_life_visual_context_future"
    _GIF_CONTEXT_FUTURE_ATTR = "_daily_life_gif_context_future"
    _VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS = 5.0
    _GIF_VISION_BRIDGE_TIMEOUT_SECONDS = 8.0

    @staticmethod
    def _visual_media_sources(payload: dict[str, str]) -> list[str]:
        sources: list[str] = []
        for key in ("path", "file", "url", "image"):
            value = str(payload.get(key) or "").strip()
            if value and value not in sources:
                sources.append(value)
        return sources

    async def _first_existing_visual_media_path(self, sources: list[str]) -> str:
        for source in sources:
            value = str(source or "").strip()
            if not value or value.startswith(
                ("http://", "https://", "data:image/", "base64://")
            ):
                continue
            if value.startswith("file://"):
                value = self.context._local_path_from_file_uri(value)
            path = Path(value).expanduser()
            if await asyncio.to_thread(path.is_file):
                return str(path)
        return ""

    @staticmethod
    def _visual_media_has_durable_source(sources: list[str]) -> bool:
        return any(
            str(source or "")
            .strip()
            .startswith(("http://", "https://", "data:image/", "base64://"))
            for source in sources
        )

    async def _build_prepared_visual_media(self, event: Any) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for item in self.context._event_message_items(event):
            if not (
                self.context._emoji_asset_is_image_item(item)
                or self.context._emoji_asset_source_kind(item)
            ):
                continue
            payload = dict(self.context._message_media_payload(item))
            sources = self.context._visual_media_sources(payload)
            local_source = await self._first_existing_visual_media_path(sources)
            if not local_source and not self.context._visual_media_has_durable_source(sources):
                payload = await self.context._media_payload_from_item_async(item)
                sources = self.context._visual_media_sources(payload)
                local_source = await self._first_existing_visual_media_path(sources)

            fingerprint = self.context._media_fingerprint(payload)
            if inspect.isawaitable(fingerprint):
                fingerprint = await fingerprint
            fingerprint = str(fingerprint or "").strip()
            if not fingerprint:
                continue

            stable_path = ""
            state = (
                "deferred"
                if self.context._visual_media_has_durable_source(sources)
                else "failed"
            )
            if local_source:
                cached_path = await self.context._cache_emoji_asset_path(
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
                        if self.context._emoji_asset_cache_dir(create=False) is None
                        else "failed"
                    )
            entries.append(
                {
                    "item": item,
                    "payload": payload,
                    "fingerprint": fingerprint,
                    "path": stable_path,
                    "cache_sources": self.context._emoji_asset_cache_candidates(
                        stable_path, sources
                    ),
                    "state": state,
                }
            )
        return entries

    async def prepare_visual_media_from_event(self, event: Any) -> bool:
        """在框架清理事件临时文件前，将本地图片固化到插件缓存。"""

        if event is None or self.context.event_was_recalled(event, log_skip=True):
            return False
        if not any(
            self.context._emoji_asset_is_image_item(item) or self.context._emoji_asset_source_kind(item)
            for item in self.context._event_message_items(event)
        ):
            setattr(event, self.context._PREPARED_VISUAL_MEDIA_ATTR, [])
            return False
        started_at = time.monotonic()
        try:
            entries = await asyncio.wait_for(
                self._build_prepared_visual_media(event),
                timeout=self.context._VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            entries = []
            logger.debug(
                f"{LOG_PREFIX} 图片前置固化超时，已跳过；"
                f"上限={self.context._VISUAL_MEDIA_PREPARE_TIMEOUT_SECONDS:.0f}秒"
            )
        setattr(event, self.context._PREPARED_VISUAL_MEDIA_ATTR, entries)
        elapsed = time.monotonic() - started_at
        if elapsed >= 0.1:
            logger.debug(f"{LOG_PREFIX} 图片前置固化耗时 {elapsed:.2f} 秒")
        return any(str(entry.get("path") or "") for entry in entries)

    async def _prepared_visual_media_from_event(
        self, event: Any
    ) -> list[dict[str, Any]]:
        prepared = getattr(event, self.context._PREPARED_VISUAL_MEDIA_ATTR, None)
        if isinstance(prepared, list):
            return prepared
        prepared = await self._build_prepared_visual_media(event)
        setattr(event, self.context._PREPARED_VISUAL_MEDIA_ATTR, prepared)
        return prepared

    async def _prepared_visual_media_is_gif(self, entry: dict[str, Any]) -> bool:
        payload = dict(entry.get("payload") or {})
        sources = [
            str(entry.get("path") or ""),
            *(str(item or "") for item in list(entry.get("cache_sources") or [])),
            *(str(payload.get(key) or "") for key in ("path", "file", "url", "image")),
        ]
        for source in dict.fromkeys(sources):
            if await self._visual_media_source_is_gif(source):
                return True
        return False

    async def _visual_media_source_is_gif(self, source: str) -> bool:
        """只有已确认的多帧 GIF 才走动图桥接，未知来源保留原图输入。"""
        value = str(source or "").strip()
        if not value or value.startswith(("http://", "https://")):
            return False
        if value.startswith("file://"):
            value = self.context._local_path_from_file_uri(value)
        try:
            return await asyncio.to_thread(is_animated_gif, value)
        except (OSError, ValueError):
            return False

    async def _run_visual_context_task(
        self,
        event: Any,
        completion: asyncio.Future,
    ) -> None:
        try:
            await self._collect_visual_context_background(event)
        except asyncio.CancelledError:
            if not completion.done():
                completion.set_result(False)
            raise
        except Exception:
            if not completion.done():
                completion.set_result(False)
            raise
        else:
            if not completion.done():
                completion.set_result(True)
        finally:
            gif_completion = getattr(event, self.context._GIF_CONTEXT_FUTURE_ATTR, None)
            if isinstance(gif_completion, asyncio.Future) and not gif_completion.done():
                gif_completion.set_result(False)

    async def _remove_gif_inputs_from_provider_request(
        self,
        req: Any,
        *,
        converted_sources: frozenset[str] = frozenset(),
    ) -> int:
        image_urls = list(getattr(req, "image_urls", []) or [])
        removed_sources: list[str] = []
        kept_sources: list[str] = []
        for source in image_urls:
            if str(source) in converted_sources or await self._visual_media_source_is_gif(
                str(source or "")
            ):
                removed_sources.append(str(source or ""))
            else:
                kept_sources.append(source)
        setattr(req, "image_urls", kept_sources)

        parts = list(getattr(req, "extra_user_content_parts", []) or [])
        kept_parts: list[Any] = []
        for part in parts:
            part_type = (
                str(
                    getattr(part, "type", "")
                    or (part.get("type", "") if isinstance(part, dict) else "")
                )
                .strip()
                .lower()
            )
            if part_type == "image_url":
                image_url = getattr(part, "image_url", None)
                direct_image_url = (
                    part.get("image_url", "") if isinstance(part, dict) else ""
                )
                source = getattr(image_url, "url", "") or (
                    image_url.get("url", "") if isinstance(image_url, dict) else ""
                )
                if not source and isinstance(direct_image_url, dict):
                    source = direct_image_url.get("url", "")
                elif not source:
                    source = direct_image_url
                if str(source) in converted_sources or await self._visual_media_source_is_gif(
                    str(source or "")
                ):
                    continue
            text = str(
                getattr(part, "text", "")
                or (part.get("text", "") if isinstance(part, dict) else "")
            )
            if removed_sources and any(
                source and source in text for source in removed_sources
            ):
                continue
            kept_parts.append(part)
        setattr(req, "extra_user_content_parts", kept_parts)
        return len(removed_sources)

    @staticmethod
    def _append_gif_visual_summary(req: Any, summaries: list[str]) -> None:
        parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(parts, list):
            parts = []
            setattr(req, "extra_user_content_parts", parts)
        unique = list(
            dict.fromkeys(
                str(item or "").strip() for item in summaries if str(item or "").strip()
            )
        )
        if unique:
            body = "；".join(unique)
            unknown = sum(not str(item or "").strip() for item in summaries)
            if unknown:
                body += f"；另有 {unknown} 张动图内容尚未确认，不猜测其画面"
            text = (
                "<animated_image_caption>本轮用户发送了 GIF 动图。"
                f"视觉模型识别结果：{body}。"
                "请把这段描述作为本轮视觉事实回应，不要补充描述之外的画面细节。"
                "</animated_image_caption>"
            )
        else:
            text = (
                "<animated_image_caption>本轮用户发送了 GIF 动图，"
                "但视觉模型未能确认其内容。请自然说明暂时看不清动图内容，"
                "不要猜测画面。</animated_image_caption>"
            )
        part = TextPart(text=text)
        marker = getattr(part, "mark_as_temp", None)
        parts.append(marker() if callable(marker) else part)

    async def _wait_for_gif_visual_context(self, event: Any) -> None:
        """只等待本轮 GIF；用完前台增强额度后继续文字对话。"""
        completion = getattr(event, self.context._GIF_CONTEXT_FUTURE_ATTR, None)
        if not isinstance(completion, asyncio.Future):
            completion = getattr(event, self.context._VISUAL_CONTEXT_FUTURE_ATTR, None)
        try:
            async with foreground_budget(
                event, maximum=self.context._GIF_VISION_BRIDGE_TIMEOUT_SECONDS
            ):
                if isinstance(completion, asyncio.Future):
                    await asyncio.shield(completion)
                else:
                    await self._collect_visual_context_background(
                        event, gif_only=True
                    )
        except TimeoutError:
            logger.debug(f"{LOG_PREFIX} GIF 视觉转写超过前台预算，继续文字对话")
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} GIF 视觉转写未完成：{type(exc).__name__}")

    async def bridge_animated_visual_for_llm_request(
        self,
        event: Any,
        req: Any,
    ) -> bool:
        """将 GIF 先交给视觉模型识别，再以文字摘要交给主对话模型。"""

        if not self.context.config.vision.enabled:
            return False
        entries = await self._prepared_visual_media_from_event(event)
        gif_entries = [
            entry
            for entry in entries
            if await self._prepared_visual_media_is_gif(entry)
        ]
        if not gif_entries:
            return False

        cache = self.context._visual_context_summary_cache()
        user_text = str(getattr(event, "message_str", "") or "")
        cache_keys = [
            await self.context._visual_context_cache_key(
                str(entry.get("fingerprint") or ""), user_text
            )
            for entry in gif_entries
        ]
        if not all(
            str(cache.get(key, "") or "").strip() for key in cache_keys
        ):
            await self._wait_for_gif_visual_context(event)
        summaries = [
            str(cache.get(key, "") or "").strip() for key in cache_keys
        ]
        # AstrBot 先追加本轮图片，再追加引用图片。只关联本轮对应槽位，
        # 即使 GIF 已转换为 PNG，也不能清空引用或其他附加图片。
        image_urls = list(getattr(req, "image_urls", []) or [])
        current_images = [
            item for item in self.context._event_message_items(event)
            if self.context._emoji_asset_is_image_item(item)
        ]
        entries_by_item = {id(entry.get("item")): entry for entry in entries}
        # 框架会跳过下载失败的图片。出现其附件状态标记、或前置缓存不完整时，
        # 不按位置猜测转换后的路径，避免把后面的引用图片当作 GIF 移除。
        unavailable = any(
            str(part.get("text", "") if isinstance(part, dict) else getattr(part, "text", ""))
            == "[Image unavailable]"
            for part in (getattr(req, "extra_user_content_parts", []) or [])
        )
        mapping_complete = not unavailable and all(
            id(image) in entries_by_item for image in current_images
        )
        converted_sources = frozenset(
            str(image_urls[index])
            for index, item in enumerate(current_images)
            if mapping_complete
            and any(entries_by_item[id(item)] is gif for gif in gif_entries)
            and index < len(image_urls)
        )
        removed = await self._remove_gif_inputs_from_provider_request(
            req, converted_sources=converted_sources,
        )
        self.context._append_gif_visual_summary(req, summaries)
        logger.debug(
            f"{LOG_PREFIX} GIF 已转为视觉文字上下文："
            f"动图={len(gif_entries)}；移除原始输入={removed}；"
            f"识别={'成功' if any(summaries) else '未完成'}"
        )
        return True

    async def _cache_and_describe_emoji_asset(
        self,
        asset: EmojiAssetRecord,
        payload: dict[str, str],
    ) -> None:
        cached_path = await self.context._cache_emoji_asset_file(payload, asset.file_hash)
        if cached_path and cached_path != asset.file_path:
            refreshed = await self.context.archive.upsert_emoji_asset(
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
        await self.context._describe_emoji_asset_with_vision(
            asset,
        )

    async def maybe_collect_emoji_assets_from_event(
        self,
        event: Any,
        now: datetime.datetime | None = None,
        sender_name: str = "",
    ) -> None:
        if not self.context._emoji_auto_collect_enabled():
            return
        if self.context._event_has_command_handler(event) or self.context.event_was_recalled(
            event, log_skip=True
        ):
            return
        entries = [
            entry for entry in await self._prepared_visual_media_from_event(event)
            if entry.get("state") != "failed"
            and self.context._emoji_asset_source_kind(entry.get("item"))
            in {"trusted", "review", "plain_image"}
        ]
        if not entries:
            return
        now = now or life_now()
        sender_name = sender_name or await self.context.contact_resolver.resolve_event_sender(
            event
        )
        if self.context.event_was_recalled(event, log_skip=True):
            return
        meta = await self.context._event_context_meta(event, sender_name, now)
        scope = meta.get("group_id") or meta.get("session_id") or ""
        saved: list[EmojiAssetRecord] = []
        payloads: dict[str, dict[str, str]] = {}
        plain_scheduled = 0
        review_limit = self.context._emoji_review_batch_size()
        seen: set[str] = set()
        for entry in entries:
            item = entry.get("item")
            source_kind = self.context._emoji_asset_source_kind(item)
            if source_kind not in {"trusted", "review", "plain_image"}:
                continue
            if entry.get("state") == "failed":
                continue
            payload = dict(entry.get("payload") or {})
            fingerprint = str(entry.get("fingerprint") or "")
            if not fingerprint or fingerprint in seen:
                continue
            seen.add(fingerprint)
            if self.context.event_was_recalled(event, log_skip=True):
                return
            if source_kind == "plain_image":
                if await self.context._plain_image_emoji_review_cached(fingerprint):
                    continue
                if plain_scheduled + len(saved) < review_limit:
                    scheduled = self.context._schedule_background_task(
                        self.context._classify_plain_image_emoji_candidate(
                            str(entry.get("path") or ""),
                            self.context._event_session_id(event),
                            self.context._event_message_id(event) or f"event:{id(event)}",
                            fingerprint, cache_sources=list(entry.get("cache_sources") or []),
                        ),
                        label="图片表情素材识别",
                        key=f"emoji_asset_vision:{fingerprint}",
                    )
                    plain_scheduled += int(scheduled)
                continue
            existing = await self.context.archive.get_emoji_asset_by_hash(fingerprint)
            if (
                existing
                and str(existing.status or "") in self.context.EMOJI_ASSET_REJECTED_STATUSES
            ):
                continue
            if existing and str(existing.status or "") == "ready":
                continue
            if not await self.context._emoji_can_accept_ready_asset(exclude_hash=fingerprint):
                continue
            path_text = (
                payload.get("path")
                or payload.get("file")
                or payload.get("url")
                or payload.get("image")
                or ""
            )
            status = "pending" if source_kind == "trusted" else "reviewing"
            asset = await self.context.archive.upsert_emoji_asset(
                EmojiAssetRecord(
                    file_hash=fingerprint,
                    file_path=path_text,
                    source_scope=scope,
                    source_message_id=meta.get("message_id", ""),
                    source_url=path_text
                    if self.context._emoji_asset_is_remote(path_text)
                    else "",
                    source_kind=source_kind,
                    asset_type=self.context._emoji_asset_type_from_item(item),
                    sendable=False,
                    status=status,
                )
            )
            if self.context.event_was_recalled(event, log_skip=True):
                return
            if asset:
                saved.append(asset)
                payloads[str(asset.id)] = payload
        if self.context.event_was_recalled(event, log_skip=True):
            return
        for asset in saved[: review_limit - plain_scheduled]:
            self.context._schedule_background_task(
                self._cache_and_describe_emoji_asset(
                    asset,
                    payloads.get(str(asset.id), {}),
                ),
                label="表情素材缓存与识别",
                key=f"emoji_asset_vision:{asset.file_hash}",
            )

    def schedule_visual_context_from_event(self, event: Any) -> bool:
        if (
            event is None
            or not self.context.config.vision.enabled
            or self.context._event_has_command_handler(event)
            or self.context.event_was_recalled(event, log_skip=True)
        ):
            return False
        if not any(
            self.context._emoji_asset_is_image_item(item)
            or self.context._emoji_asset_source_kind(item) in {"trusted", "review"}
            for item in self.context._event_message_items(event)
        ):
            return False
        scope = self.context._event_session_id(event)
        message_id = self.context._event_message_id(event)
        if not scope:
            return False
        key = f"visual_context:{scope}:{message_id or id(event)}"
        completion = asyncio.get_running_loop().create_future()
        gif_completion = asyncio.get_running_loop().create_future()
        setattr(event, self.context._VISUAL_CONTEXT_FUTURE_ATTR, completion)
        setattr(event, self.context._GIF_CONTEXT_FUTURE_ATTR, gif_completion)
        scheduled = self.context._schedule_background_task(
            self._run_visual_context_task(event, completion),
            label="图片上下文识别",
            key=key,
        )
        if not scheduled and not completion.done():
            completion.set_result(False)
            gif_completion.set_result(False)
        return scheduled

    async def _collect_visual_context_background(
        self, event: Any, *, gif_only: bool = False
    ) -> None:
        if not self.context.config.vision.enabled or self.context.event_was_recalled(
            event, log_skip=True
        ):
            return
        scope = self.context._event_session_id(event)
        message_key = self.context._event_message_id(event) or f"event:{id(event)}"
        if not scope or not message_key:
            return

        if self.context.event_was_recalled(event, log_skip=True):
            return

        entries = await self._prepared_visual_media_from_event(event)
        # 先按附件顺序限制数量，再优先处理其中的 GIF，避免动图桥接绕过上限。
        selected: dict[str, dict[str, Any]] = {}
        for entry in entries:
            fingerprint = str(entry.get("fingerprint") or "")
            if fingerprint and fingerprint not in selected:
                selected[fingerprint] = entry
            if len(selected) >= self.context.config.vision.max_images_per_message:
                break
        user_text = str(getattr(event, "message_str", "") or "")
        classified = [
            (await self._prepared_visual_media_is_gif(entry), entry)
            for entry in selected.values()
        ]
        if gif_only:
            classified = [item for item in classified if item[0]]
        gif_completion = getattr(event, self.context._GIF_CONTEXT_FUTURE_ATTR, None)
        gif_remaining = sum(is_gif for is_gif, _ in classified)
        if (
            gif_remaining == 0
            and isinstance(gif_completion, asyncio.Future)
            and not gif_completion.done()
        ):
            gif_completion.set_result(True)
        for is_gif, entry in sorted(classified, key=lambda item: not item[0]):
            if self.context.event_was_recalled(event, log_skip=True):
                return
            try:
                await self._collect_one_visual_context(
                    entry, scope, message_key, user_text=user_text
                )
            finally:
                if is_gif:
                    gif_remaining -= 1
                    if (
                        gif_remaining == 0
                        and isinstance(gif_completion, asyncio.Future)
                        and not gif_completion.done()
                    ):
                        gif_completion.set_result(True)

    async def _collect_one_visual_context(
        self, entry: dict[str, Any], scope: str, message_key: str, *, user_text: str = ""
    ) -> None:
        fingerprint = str(entry.get("fingerprint") or "")
        if not fingerprint or not self.context.config.vision.enabled:
            return
        cache_key = await self.context._visual_context_cache_key(fingerprint, user_text)
        cached_summary = self.context._visual_context_summary_cache().get(cache_key, "")
        if cached_summary:
            self.context._apply_visual_context_summary_text(scope, message_key, cached_summary)
            return
        path = str(entry.get("path") or "")
        cache_sources = list(entry.get("cache_sources") or [])
        if not path and entry.get("state") in {"deferred", "transient"}:
            path, cache_sources = await self.context._emoji_asset_prepare_vision_source(
                cache_sources, fingerprint
            )
        if not path:
            logger.debug(
                f"{LOG_PREFIX} 图片上下文识别跳过：图片临时文件已失效，且无可用备用来源"
            )
            return
        await self.context._describe_visual_context_with_vision(
            path, scope, message_key, fingerprint, user_text=user_text
        )
