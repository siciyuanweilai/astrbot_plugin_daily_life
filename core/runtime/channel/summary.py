from __future__ import annotations

import asyncio
import datetime
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ..markers import LOG_PREFIX


class RuntimeMediaCommonMixin:
    _MEDIA_CADENCE_TTL_SECONDS = 6 * 60 * 60

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
        path = Path(text).expanduser()
        if not path.is_file():
            return None
        try:
            return await asyncio.to_thread(lambda: path.stat().st_size)
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
        for nested in (getattr(exc, "__cause__", None), getattr(exc, "__context__", None)):
            nested_detail = str(nested or "").strip()
            if nested_detail:
                return f"{RuntimeMediaCommonMixin._localized_error_name(type(exc).__name__)}：{nested_detail}"
        return RuntimeMediaCommonMixin._localized_error_name(type(exc).__name__)

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
                expired = (now - last_at).total_seconds() > self._MEDIA_CADENCE_TTL_SECONDS
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
        scope = event_or_scope if isinstance(event_or_scope, str) else self._event_session_id(event_or_scope)
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
        item["consecutive"] = int(item.get("consecutive") or 0) + 1 if last_media == media else 1
        store[scope] = item

    def _hidden_media_cadence_hint(self, event: Any = None) -> str:
        self._prune_media_cadence()
        scope = self._event_session_id(event) if event is not None else ""
        item = self._media_cadence_store().get(scope, {}) if scope else {}
        if not isinstance(item, dict) or not isinstance(item.get("last_at"), datetime.datetime):
            return "当前会话最近没有生活图片或视频发送记录；可以按语境自然判断是否需要展示。"
        media = str(item.get("last_media") or "媒体")
        seconds = max(0, int((datetime.datetime.now() - item["last_at"]).total_seconds()))
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

    def _quote_image_reference_from_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in ("chain", "message", "messages", "items", "segments", "components"):
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

    async def _resolve_life_image_reference_async(self, event: Any, reference_image: str = "") -> str:
        explicit = str(reference_image or "").strip()
        if explicit:
            return explicit
        current = self._image_reference_from_items(self._event_message_items(event))
        if current:
            return current
        quoted = self._quote_image_reference_from_event(event)
        if quoted:
            return quoted
        extractor = None
        try:
            from astrbot.core.utils.quoted_message_parser import extract_quoted_message_images

            extractor = extract_quoted_message_images
        except Exception:
            extractor = None
        if callable(extractor):
            for source in self._event_sources(event):
                try:
                    images = await extractor(source)
                except Exception as exc:
                    logger.debug(f"{LOG_PREFIX} 引用图片解析失败：{self._media_error_summary(exc)}")
                    images = []
                for image in images or []:
                    text = str(image or "").strip()
                    if text:
                        return text
        return ""

    def _resolve_life_image_reference(self, event: Any, reference_image: str = "") -> str:
        explicit = str(reference_image or "").strip()
        if explicit:
            return explicit
        return (
            self._image_reference_from_items(self._event_message_items(event))
            or self._quote_image_reference_from_event(event)
        )
