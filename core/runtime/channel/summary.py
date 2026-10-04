from __future__ import annotations

import asyncio
import copy
import datetime
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger
from astrbot.api.message_components import Image

from ...paths import expand_path, path_is_file, path_size
from ...prompts import CORE_MEDIA_REPLY_RULES, cache_friendly_prompt
from ...sources.platforms import event_platform_names
from ..delivery import BackgroundTextMode
from ..markers import LOG_PREFIX


class RuntimeMediaCommonMixin:
    _MEDIA_CADENCE_TTL_SECONDS = 6 * 60 * 60

    def _background_tool_request(self, event: Any, tool_name: str) -> dict | None:
        for source in self._event_sources(event):
            requests = getattr(source, "_daily_life_background_tool_requests", {})
            marker = requests.get(tool_name)
            if isinstance(marker, dict):
                return marker
        return None

    def _background_tool_existing_result(
        self, event: Any, tool_name: str
    ) -> str | None:
        marker = self._background_tool_request(event, tool_name)
        if marker and marker.get("status") in {"pending", "sent"}:
            return json.dumps(
                {
                    "status": marker["status"],
                    "media": marker["media"],
                    "deduplicated": True,
                },
                ensure_ascii=False,
            )
        return None

    def _submit_background_tool(
        self,
        event: Any,
        source_event: Any,
        work: Callable[[], Awaitable],
        *,
        tool_name: str,
        media: str,
        label: str,
        category: str,
        failure_text: str,
    ) -> str:
        existing = self._background_tool_existing_result(source_event, tool_name)
        if existing:
            return existing
        scope = self._event_session_id(event)
        source_id = self._event_message_id(source_event) or f"event:{id(source_event)}"
        marker = {"status": "pending", "media": media}

        def cancel_request() -> None:
            if marker["status"] != "sent":
                marker["status"] = "cancelled"
                self.cancel_tool_reaction(event, tool_name)

        if not self._schedule_background_task(
            self._deliver_background_tool(event, work, marker, tool_name, failure_text),
            label=label,
            key=f"background_tool:{tool_name}:{scope}:{source_id}",
            category=category,
            on_cancel=cancel_request,
        ):
            return json.dumps(
                {
                    "status": "failed",
                    "media": media,
                    "reason": "任务队列暂时繁忙，请稍后重试",
                },
                ensure_ascii=False,
            )
        for source in [event, *self._event_sources(source_event)]:
            requests = dict(
                getattr(source, "_daily_life_background_tool_requests", {}) or {}
            )
            requests[tool_name] = marker
            source._daily_life_background_tool_requests = requests
        self._release_media_chat_turn(source_event)
        return json.dumps(
            {
                "status": "pending",
                "media": media,
                "response_timing": "after_delivery",
                "response_stance": "已受理，尚未完成；结束本次工具轮次，不要等待、重复调用或提前声称结果已完成。后续消息正常聊天，实际结果会独立送达。",
            },
            ensure_ascii=False,
        )

    async def _deliver_background_tool(
        self,
        event: Any,
        work: Callable[[], Awaitable],
        marker: dict,
        tool_name: str,
        failure_text: str,
    ) -> None:
        try:
            if not self.can_send_for_source(
                self._event_session_id(event), source_event=event
            ):
                marker["status"] = "cancelled"
                self.cancel_tool_reaction(event, tool_name)
                return
            result = await work()
            status = str(getattr(result, "status", "") or "")
            if marker["status"] == "sent" or status == "sent":
                marker["status"] = "sent"
            elif status == "cancelled" or not self.can_send_for_source(
                self._event_session_id(event), source_event=event
            ):
                marker["status"] = "cancelled"
                self.cancel_tool_reaction(event, tool_name)
                return
            elif status == "ok":
                text = str(result or "").strip()
                sent = bool(text) and await self.send_background_text(
                    self._event_session_id(event),
                    text,
                    mode=BackgroundTextMode.DIRECT,
                    source_event=event,
                    source=f"{marker['media']}_result",
                )
                marker["status"] = "sent" if sent else "failed"
                if sent:
                    await self._append_assistant_history(
                        self._event_session_id(event), text
                    )
            else:
                marker["status"] = "failed"
                if self.media_request_is_current_turn(event):
                    await self.send_background_text(
                        self._event_session_id(event),
                        str(result or failure_text),
                        mode=BackgroundTextMode.DIRECT,
                        source_event=event,
                        source="background_tool_failure",
                    )
            await self.finish_tool_reaction(
                event, tool_name, success=marker["status"] == "sent"
            )
        except asyncio.CancelledError:
            if marker["status"] != "sent":
                marker["status"] = "cancelled"
                self.cancel_tool_reaction(event, tool_name)
            raise
        except Exception as exc:
            logger.warning(
                f"{LOG_PREFIX} {tool_name} 后台处理失败：{self._media_error_summary(exc)}"
            )
            if marker["status"] == "sent":
                await self.finish_tool_reaction(event, tool_name, success=True)
                return
            marker["status"] = "failed"
            await self.finish_tool_reaction(event, tool_name, success=False)
            if self.media_request_is_current_turn(event):
                await self.send_background_text(
                    self._event_session_id(event),
                    failure_text,
                    mode=BackgroundTextMode.DIRECT,
                    source_event=event,
                    source="background_tool_failure",
                )

    def hold_background_tool_final_text(self, event: Any) -> bool:
        if not any(
            getattr(source, "_daily_life_background_tool_requests", {})
            for source in self._event_sources(event)
        ):
            return False
        result = getattr(event, "get_result", lambda: None)()
        if not self._is_llm_result_object(result):
            return False
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        return True

    def _release_media_chat_turn(self, event: Any) -> None:
        self.complete_continuous_turn(event)
        sources = self._event_sources(event)
        original = sources[-1] if sources else event
        runner = self._active_agent_runner(original)
        follow_up = self._follow_up_module()
        unregister = getattr(follow_up, "unregister_active_runner", None)
        if runner is None or not callable(unregister):
            return
        # Existing follow-ups resume as independent turns; later messages must
        # not be consumed by the media request's final confirmation response.
        release = getattr(runner, "_resolve_unconsumed_follow_ups", None)
        if callable(release):
            release()
        unregister(self._event_session_id(original), runner)

    def _snapshot_media_event(self, event: Any) -> Any:
        sources = self._event_sources(event)
        original = sources[-1] if sources else event
        snapshot = copy.copy(original)
        snapshot._extras = dict(getattr(original, "_extras", {}) or {})
        # WebChat's original request stream can close before background delivery.
        snapshot._daily_life_media_scope_delivery = (
            "webchat" in event_platform_names(original)
        )
        items = self._event_message_items(original)
        copied_items = copy.deepcopy(items)
        prepared = getattr(original, self._PREPARED_VISUAL_MEDIA_ATTR, [])
        snapshot._daily_life_cached_image_references = {}
        for index, item in enumerate(items):
            cached = next(
                (
                    entry.get("path")
                    for entry in prepared
                    if entry.get("item") is item and entry.get("path")
                ),
                "",
            )
            if cached:
                copied_items[index] = Image.fromFileSystem(str(cached))
                payload = self._message_media_payload(item)
                for key in ("path", "file", "url", "image"):
                    if payload.get(key):
                        snapshot._daily_life_cached_image_references[payload[key]] = (
                            str(cached)
                        )
        message_obj = getattr(original, "message_obj", None)
        if message_obj is not None:
            snapshot.message_obj = copy.copy(message_obj)
            snapshot.message_obj.message = copied_items
        if hasattr(original, "message_items"):
            snapshot.message_items = copied_items
        setattr(
            snapshot,
            self._PREPARED_VISUAL_MEDIA_ATTR,
            [
                {**entry, "item": copied_items[index]}
                for entry in prepared
                for index, item in enumerate(items)
                if entry.get("item") is item
            ],
        )
        snapshot._daily_life_locked_image_prompt_text = self._event_image_prompt_text(
            event
        )
        return snapshot

    @staticmethod
    def _parse_delivered_media_reply(value: Any) -> str:
        text = str(value or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            fence = lines[0].strip().lower() if lines else ""
            if len(lines) < 3 or fence not in {"```", "```json"}:
                return ""
            if lines[-1].strip() != "```":
                return ""
            text = "\n".join(lines[1:-1]).strip()
        if not text.startswith("{") or not text.endswith("}"):
            return ""
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return ""
        if not isinstance(payload, dict) or set(payload) != {"reply_text"}:
            return ""
        reply_text = payload.get("reply_text")
        return reply_text.strip() if isinstance(reply_text, str) else ""

    async def _generate_delivered_media_reply(
        self,
        scope: str,
        *,
        media_name: str,
        request_text: str,
        delivery_text: str,
        guidance: str = "",
    ) -> str:
        get_provider = getattr(self, "get_text_provider", None)
        call_llm = getattr(self, "call_text_model", None)
        if not callable(get_provider) or not callable(call_llm):
            return ""
        try:
            provider = await get_provider("")
            if provider is None:
                return ""
            persona = ""
            persona_getter = getattr(self, "get_persona_text", None)
            if callable(persona_getter):
                try:
                    persona = str(await persona_getter(scope) or "").strip()
                except TypeError:
                    persona = str(await persona_getter() or "").strip()
            fixed = """你正在给刚刚真实送达的一份生活媒体补一句自然回复。
严格只输出一个 JSON 对象，不要使用 Markdown 代码块或解释：
{"reply_text":"角色真正说出口的一句中文短回复"}
JSON 只能包含 reply_text。{CORE_MEDIA_REPLY_RULES}
回复要像角色本人顺手接话，结合用户原本的要求和实际送达结果自然承接。
不要复述内部流程、文件信息、耗时或技术状态；没有看见成品内容时，不得编造具体画面细节。""".replace(
                "{CORE_MEDIA_REPLY_RULES}", CORE_MEDIA_REPLY_RULES
            )
            dynamic = (
                f"角色口吻参考：{persona[:800] if persona else '按当前角色口吻自然回复。'}\n"
                f"送达内容：{str(media_name or '生活媒体').strip()}\n"
                f"用户原本的要求：{str(request_text or '').strip()}\n"
                f"实际送达结果：{str(delivery_text or '').strip()}\n"
                f"补充语境：{str(guidance or '').strip()}"
            )
            raw = await call_llm(
                provider,
                cache_friendly_prompt(fixed, dynamic, dynamic_title="媒体已经送达"),
                f"daily_life_media_followup_{uuid.uuid4().hex[:8]}",
                empty_retries=0,
                primary_provider_id="",
            )
            reply_text = self._parse_delivered_media_reply(raw)
            if not reply_text:
                logger.debug(f"{LOG_PREFIX} 媒体送达补话生成失败：返回内容不符合协议")
            return reply_text
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 媒体送达补话生成失败：{self._media_error_summary(exc)}"
            )
            return ""

    async def _send_delivered_media_followup(
        self,
        scope: str,
        *,
        media_name: str,
        request_text: str,
        delivery_text: str,
        guidance: str = "",
        source_event: Any = None,
        source: str = "media_followup",
    ) -> bool:
        current_turn = getattr(self, "media_request_is_current_turn", None)
        if callable(current_turn) and not current_turn(source_event):
            logger.debug(f"{LOG_PREFIX} 跳过过期媒体补话：后续消息已经接管当前话轮。")
            return False
        text = await self._generate_delivered_media_reply(
            scope,
            media_name=media_name,
            request_text=request_text,
            delivery_text=delivery_text,
            guidance=guidance,
        )
        if not text:
            return False
        if callable(current_turn) and not current_turn(source_event):
            return False
        try:
            sent = await self.send_background_text(
                scope,
                text,
                mode=BackgroundTextMode.EXPRESSIVE,
                source_event=source_event,
                source=source,
            )
        except Exception as exc:
            logger.warning(
                f"{LOG_PREFIX} 媒体送达补话发送失败：{self._media_error_summary(exc)}"
            )
            return False
        if not sent:
            return False
        current_turn = getattr(self, "media_request_is_current_turn", None)
        if not callable(current_turn) or current_turn(source_event):
            try:
                await self._append_assistant_history(scope, text)
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 媒体送达补话历史记录失败："
                    f"{self._media_error_summary(exc)}"
                )
        return True

    @staticmethod
    def _localized_error_name(name: str) -> str:
        labels = {
            "TimeoutError": "超时",
            "RuntimeError": "运行错误",
            "ValueError": "取值错误",
            "TypeError": "类型错误",
            "FileNotFoundError": "文件不存在",
            "PermissionError": "权限不足",
            "ConnectionError": "连接错误",
            "ClientError": "请求错误",
        }
        text = str(name or "").strip()
        return labels.get(text, text or "未知错误")

    @staticmethod
    def _media_elapsed_text(started_at: float) -> str:
        elapsed = max(0.0, time.monotonic() - float(started_at or 0.0))
        if elapsed < 10:
            return f"{elapsed:.1f} 秒"
        return f"{round(elapsed)} 秒"

    @staticmethod
    def _media_size_text(size: int | None) -> str:
        if size is None or size < 0:
            return "大小未知"
        units = ("B", "KB", "MB", "GB")
        value = float(size)
        unit = units[0]
        for unit in units:
            if value < 1024 or unit == units[-1]:
                break
            value /= 1024
        if unit == "B":
            return f"{int(value)} B"
        return f"{value:.1f} {unit}"

    async def _media_file_size(self, value: object) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.startswith(("http://", "https://")):
            return None
        path = await asyncio.to_thread(expand_path, text)
        if not await asyncio.to_thread(path_is_file, path):
            return None
        try:
            return await asyncio.to_thread(path_size, path)
        except OSError:
            return None

    async def _media_result_summary(self, target: object, started_at: float) -> str:
        size = await self._media_file_size(target)
        return f"{self._media_size_text(size)}，耗时 {self._media_elapsed_text(started_at)}"

    @staticmethod
    def _media_error_summary(exc: Exception) -> str:
        detail = str(exc).strip()
        if detail:
            if detail == type(exc).__name__:
                return RuntimeMediaCommonMixin._localized_error_name(detail)
            return detail
        for nested in (
            getattr(exc, "__cause__", None),
            getattr(exc, "__context__", None),
        ):
            nested_detail = str(nested or "").strip()
            if nested_detail:
                return f"{RuntimeMediaCommonMixin._localized_error_name(type(exc).__name__)}：{nested_detail}"
        return RuntimeMediaCommonMixin._localized_error_name(type(exc).__name__)

    @staticmethod
    def _media_tool_failure_text(media_name: str, error: str) -> str:
        detail = str(error or "").strip()
        if detail and (
            "智能提取失败" in detail or "没有收到" in detail or "没有找到" in detail
        ):
            return f"{media_name}生成失败：{detail}"
        return f"{media_name}生成失败，已记录失败原因。"

    def _media_cadence_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_life_media_cadence", None)
        if not isinstance(store, dict):
            self._life_media_cadence = {}
            store = self._life_media_cadence
        return store

    def _prune_media_cadence(self, now: datetime.datetime | None = None) -> None:
        now = now or datetime.datetime.now()
        for scope, item in list(self._media_cadence_store().items()):
            last_at = item.get("last_at") if isinstance(item, dict) else None
            if not isinstance(last_at, datetime.datetime):
                self._life_media_cadence.pop(scope, None)
                continue
            try:
                expired = (
                    now - last_at
                ).total_seconds() > self._MEDIA_CADENCE_TTL_SECONDS
            except Exception:
                expired = True
            if expired:
                self._life_media_cadence.pop(scope, None)

    def note_life_media_sent(
        self,
        event_or_scope: Any,
        media: str,
        *,
        now: datetime.datetime | None = None,
    ) -> None:
        scope = (
            event_or_scope
            if isinstance(event_or_scope, str)
            else self._event_session_id(event_or_scope)
        )
        scope = str(scope or "").strip()
        media = "视频" if str(media or "").lower() in {"video", "视频"} else "图片"
        if not scope:
            return
        now = now or datetime.datetime.now()
        self._prune_media_cadence(now)
        store = self._media_cadence_store()
        item = store.get(scope, {})
        if not isinstance(item, dict):
            item = {}
        last_media = str(item.get("last_media") or "")
        item["last_media"] = media
        item["last_at"] = now
        item["count"] = int(item.get("count") or 0) + 1
        item["consecutive"] = (
            int(item.get("consecutive") or 0) + 1 if last_media == media else 1
        )
        store[scope] = item

    def _hidden_media_cadence_hint(self, event: Any = None) -> str:
        self._prune_media_cadence()
        scope = self._event_session_id(event) if event is not None else ""
        item = self._media_cadence_store().get(scope, {}) if scope else {}
        if not isinstance(item, dict) or not isinstance(
            item.get("last_at"), datetime.datetime
        ):
            return "当前会话最近没有生活图片或视频发送记录；可以按语境自然判断是否需要展示。"
        media = str(item.get("last_media") or "媒体")
        seconds = max(
            0, int((datetime.datetime.now() - item["last_at"]).total_seconds())
        )
        minutes = seconds // 60
        if minutes <= 0:
            time_text = "刚刚"
        elif minutes < 60:
            time_text = f"约 {minutes} 分钟前"
        else:
            time_text = f"约 {minutes // 60} 小时前"
        consecutive = int(item.get("consecutive") or 1)
        return (
            f"{time_text}发过{media}；最近同类连续 {consecutive} 次。"
            "如果这轮只是普通补充，优先文字；如果用户正在看状态、穿搭、场景、照片或视频效果，仍可自然展示。"
        )

    def _image_reference_from_items(self, items: list[Any]) -> str:
        for item in items:
            payload = self._message_media_payload(item)
            source = (
                payload.get("path")
                or payload.get("file")
                or payload.get("url")
                or payload.get("image")
                or ""
            ).strip()
            if source:
                return source
        return ""

    async def _media_payload_from_item_async(self, item: Any) -> dict[str, str]:
        payload = dict(self._message_media_payload(item))
        getter = getattr(item, "get_file", None)
        if callable(getter):
            try:
                resolved = getter()
                if hasattr(resolved, "__await__"):
                    resolved = await resolved
                text = str(resolved or "").strip()
                if text:
                    payload["path"] = text
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 媒体异步获取失败：{self._media_error_summary(exc)}"
                )
        converter = getattr(item, "convert_to_file_path", None)
        if callable(converter) and not payload.get("path"):
            try:
                resolved = converter()
                if hasattr(resolved, "__await__"):
                    resolved = await resolved
                text = str(resolved or "").strip()
                if text:
                    payload["path"] = text
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 媒体本地解析失败：{self._media_error_summary(exc)}"
                )
        return payload

    async def _image_reference_from_items_async(self, items: list[Any]) -> str:
        for item in items:
            payload = await self._media_payload_from_item_async(item)
            source = (
                payload.get("path")
                or payload.get("file")
                or payload.get("url")
                or payload.get("image")
                or ""
            ).strip()
            if source:
                return source
        return ""

    def _quote_image_reference_from_event(self, event: Any) -> str:
        for source in self._event_sources(event):
            for attr in ("quote", "reply", "reply_message"):
                value = getattr(source, attr, None)
                reference = self._quote_image_reference_from_value(value)
                if reference:
                    return reference
            for item in self._event_message_items(source):
                kind = self._event_component_kind(item)
                if "reply" not in kind and "quote" not in kind:
                    continue
                reference = self._quote_image_reference_from_value(item)
                if reference:
                    return reference
        return ""

    async def _quote_image_reference_from_event_async(self, event: Any) -> str:
        for source in self._event_sources(event):
            for attr in ("quote", "reply", "reply_message"):
                reference = await self._quote_image_reference_from_value_async(
                    getattr(source, attr, None)
                )
                if reference:
                    return reference
            for item in self._event_message_items(source):
                kind = self._event_component_kind(item)
                if "reply" not in kind and "quote" not in kind:
                    continue
                reference = await self._quote_image_reference_from_value_async(item)
                if reference:
                    return reference
        return ""

    def _quote_image_reference_from_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in (
                "chain",
                "message",
                "messages",
                "items",
                "segments",
                "components",
            ):
                raw_items = value.get(key)
                if isinstance(raw_items, list):
                    reference = self._image_reference_from_items(raw_items)
                    if reference:
                        return reference
            data = value.get("data")
            if isinstance(data, dict):
                reference = self._quote_image_reference_from_value(data)
                if reference:
                    return reference
            if "image" in self._event_component_kind(value):
                return self._image_reference_from_items([value])
            return ""

        for attr in ("chain", "message", "messages", "items", "segments", "components"):
            raw_items = getattr(value, attr, None)
            if isinstance(raw_items, list):
                reference = self._image_reference_from_items(raw_items)
                if reference:
                    return reference
        data = getattr(value, "data", None)
        if isinstance(data, dict):
            reference = self._quote_image_reference_from_value(data)
            if reference:
                return reference
        if "image" in self._event_component_kind(value):
            return self._image_reference_from_items([value])
        return ""

    async def _quote_image_reference_from_value_async(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in (
                "chain",
                "message",
                "messages",
                "items",
                "segments",
                "components",
            ):
                raw_items = value.get(key)
                if isinstance(raw_items, list):
                    reference = await self._image_reference_from_items_async(raw_items)
                    if reference:
                        return reference
            data = value.get("data")
            if isinstance(data, dict):
                reference = await self._quote_image_reference_from_value_async(data)
                if reference:
                    return reference
            if "image" in self._event_component_kind(value):
                return await self._image_reference_from_items_async([value])
            return ""

        for attr in ("chain", "message", "messages", "items", "segments", "components"):
            raw_items = getattr(value, attr, None)
            if isinstance(raw_items, list):
                reference = await self._image_reference_from_items_async(raw_items)
                if reference:
                    return reference
        data = getattr(value, "data", None)
        if isinstance(data, dict):
            reference = await self._quote_image_reference_from_value_async(data)
            if reference:
                return reference
        if "image" in self._event_component_kind(value):
            return await self._image_reference_from_items_async([value])
        return ""

    async def _resolve_life_image_reference_async(
        self,
        event: Any,
        reference_image: str = "",
        *,
        allow_last_generated: bool = False,
        prefer_last_generated: bool = False,
        current_items: tuple[Any, ...] | None = None,
    ) -> str:
        explicit = str(reference_image or "").strip()
        current = await self._image_reference_from_items_async(
            list(current_items)
            if current_items is not None
            else self._event_message_items(event)
        )
        if current:
            return current
        quoted = await self._quote_image_reference_from_event_async(event)
        if quoted:
            return quoted
        extractor = None
        try:
            from astrbot.core.utils.quoted_message_parser import (
                extract_quoted_message_images,
            )

            extractor = extract_quoted_message_images
        except Exception:
            extractor = None
        if callable(extractor):
            for source in self._event_sources(event):
                try:
                    images = await extractor(source)
                except Exception as exc:
                    logger.debug(
                        f"{LOG_PREFIX} 引用图片解析失败：{self._media_error_summary(exc)}"
                    )
                    images = []
                for image in images or []:
                    text = str(image or "").strip()
                    if text:
                        return text

        scope = self._event_session_id(event)
        last_generated = ""
        if allow_last_generated and scope:
            cached = self._last_generated_life_image_path(scope)
            if cached and await self._life_image_reference_is_usable(cached):
                last_generated = cached
            elif cached:
                self._forget_last_generated_life_image_path(scope, cached)

        if prefer_last_generated and last_generated:
            logger.debug(f"{LOG_PREFIX} 图片编辑参考：来源=上一张生成结果")
            return last_generated

        explicit_invalid = False
        if explicit:
            if await self._life_image_reference_is_usable(explicit):
                return explicit
            explicit_invalid = True

        if last_generated:
            if explicit_invalid:
                logger.debug(
                    f"{LOG_PREFIX} 显式参考图已失效，改用当前会话上一张生成结果"
                )
            else:
                logger.debug(f"{LOG_PREFIX} 图片编辑参考：来源=上一张生成结果")
            return last_generated
        return ""

    async def _life_image_reference_is_usable(self, reference: Any) -> bool:
        text = str(reference or "").strip()
        if not text:
            return False
        lowered = text.lower()
        if lowered.startswith(("http://", "https://")):
            return True
        if lowered.startswith("data:image/"):
            header, separator, payload = text.partition(",")
            return bool(separator and header.strip() and payload.strip())
        if lowered.startswith("base64://"):
            return bool(text[len("base64://") :].strip())
        path = await asyncio.to_thread(expand_path, text)
        return await asyncio.to_thread(path_is_file, path)

    def _resolve_life_image_reference(
        self, event: Any, reference_image: str = ""
    ) -> str:
        explicit = str(reference_image or "").strip()
        if explicit:
            return explicit
        return self._image_reference_from_items(
            self._event_message_items(event)
        ) or self._quote_image_reference_from_event(event)
