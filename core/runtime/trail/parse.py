from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ...media.shared import image_data_url


class HistoryParseMixin:
    @staticmethod
    def _history_text_from_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            data = value.get("data")
            merged = {**value, **data} if isinstance(data, dict) else value
            for key in ("message_str", "text", "content", "raw_message"):
                text = HistoryParseMixin._history_text_from_value(merged.get(key))
                if text:
                    return text
            message = merged.get("message")
            if isinstance(message, list):
                return HistoryParseMixin._history_text_from_message_items(message)
            return ""
        for attr in ("message_str", "text", "content", "raw_message"):
            text = HistoryParseMixin._history_text_from_value(getattr(value, attr, None))
            if text:
                return text
        message = getattr(value, "message", None)
        if isinstance(message, list):
            return HistoryParseMixin._history_text_from_message_items(message)
        return ""

    @classmethod
    def _history_text_from_message_item(cls, item: Any) -> str:
        if isinstance(item, dict):
            kind = str(item.get("type") or item.get("kind") or "").strip().lower()
        else:
            kind = " ".join(
                str(value or "").strip().lower()
                for value in (
                    item.__class__.__name__,
                    getattr(item, "type", ""),
                    getattr(item, "kind", ""),
                )
            )
        if kind and not (kind in {"text", "plain"} or "text" in kind):
            return ""
        return cls._history_text_from_value(item)

    @classmethod
    def _history_text_from_message_items(cls, items: list[Any]) -> str:
        texts = [cls._history_text_from_message_item(item) for item in items or []]
        return " ".join(text for text in texts if text).strip()

    @classmethod
    def _history_primary_text(cls, value: Any) -> str:
        if isinstance(value, list):
            for item in value:
                text = cls._history_text_from_message_item(item)
                if text and not text.startswith("<system_reminder>"):
                    return text
            return ""
        return cls._history_text_from_value(value)

    def _event_user_history_text(self, event: Any) -> str:
        sources_getter = getattr(self, "_event_sources", None)
        sources = sources_getter(event) if callable(sources_getter) else [event]
        for source in sources:
            text = self._history_text_from_value(getattr(source, "message_str", None))
            if text:
                return text
            getter = getattr(source, "get_messages", None)
            if callable(getter):
                try:
                    text = self._history_text_from_message_items(getter() or [])
                    if text:
                        return text
                except Exception:
                    continue
            message_obj = getattr(source, "message_obj", None)
            for attr in ("message_str", "raw_message"):
                text = self._history_text_from_value(getattr(message_obj, attr, None))
                if text:
                    return text
            raw = getattr(message_obj, "message", None)
            if isinstance(raw, list):
                text = self._history_text_from_message_items(raw)
                if text:
                    return text
            for attr in ("raw_message", "text", "content"):
                text = self._history_text_from_value(getattr(source, attr, None))
                if text:
                    return text
        return ""

    @staticmethod
    def _conversation_history_list(conversation: Any) -> list[dict]:
        raw = getattr(conversation, "history", [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "[]")
            except json.JSONDecodeError:
                raw = []
        return list(raw) if isinstance(raw, list) else []

    @staticmethod
    def _history_item_matches(item: Any, role: str, content: str) -> bool:
        if not isinstance(item, dict):
            return False
        return (
            str(item.get("role") or "").strip() == role
            and HistoryParseMixin._history_primary_text(item.get("content")).strip()
            == str(content or "").strip()
        )

    @staticmethod
    def _history_user_item_matches(item: Any, expected: dict[str, Any]) -> bool:
        if not isinstance(item, dict) or not isinstance(expected, dict):
            return False
        if str(item.get("role") or "").strip() != "user":
            return False
        if item.get("content") == expected.get("content"):
            return True
        if HistoryParseMixin._history_content_has_media(item.get("content")) or HistoryParseMixin._history_content_has_media(
            expected.get("content")
        ):
            return False
        return HistoryParseMixin._history_primary_text(item.get("content")).strip() == (
            HistoryParseMixin._history_primary_text(expected.get("content")).strip()
        )

    @staticmethod
    def _history_content_has_media(content: Any) -> bool:
        if not isinstance(content, list):
            return False
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").strip().lower()
            if part_type in {"image", "image_url", "audio_url", "record", "voice", "video", "file"}:
                return True
            text = str(part.get("text") or "").strip()
            if " Attachment: " in text:
                return True
        return False

    @staticmethod
    def _history_message_parts_for_user(text: str, media_label: str = "") -> list[dict[str, str]]:
        if text:
            return [{"type": "plain", "text": text}]
        return [{"type": "plain", "text": media_label or "（无可见文本）"}]

    @staticmethod
    async def _history_display_url(path: str) -> str:
        path = str(path or "").strip()
        if not path or path.startswith(("http://", "https://", "data:")):
            return path
        local = Path(path).expanduser()
        if not local.is_file():
            return path
        try:
            data = await asyncio.to_thread(local.read_bytes)
        except OSError:
            return path
        return image_data_url(data) if data else path

    async def _platform_media_part_from_history_payload(self, part: dict[str, Any]) -> dict[str, Any]:
        media_type = str(part.get("type") or "").strip()
        if not media_type:
            return {}
        path = str(part.get("path") or part.get("file") or part.get("url") or "").strip()
        if not path:
            return {}
        display_url = await self._history_display_url(path)
        filename = str(part.get("name") or "").strip() or path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if media_type == "image":
            return {
                "type": "image",
                "filename": filename,
                "embedded_url": display_url,
            }
        if media_type in {"record", "video", "file"}:
            payload = {
                "type": media_type,
                "filename": filename,
                "embedded_url": display_url,
            }
            if media_type == "file":
                payload["path"] = path
            return payload
        return {}

    @classmethod
    async def _history_media_parts_from_event(cls, event: Any) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        event_items = cls._event_message_items(event)
        for item in event_items:
            kind = cls._event_component_kind(item)
            media_type = cls._history_media_type(kind)
            if not media_type:
                continue
            payload = await cls._history_media_payload(item, media_type)
            if payload:
                parts.append(payload)
        return parts

    @staticmethod
    def _history_media_type(kind: str) -> str:
        kind = str(kind or "").lower()
        if "image" in kind:
            return "image"
        if "record" in kind or "voice" in kind:
            return "record"
        if "video" in kind:
            return "video"
        if "file" in kind:
            return "file"
        return ""

    @staticmethod
    async def _history_media_payload(item: Any, media_type: str) -> dict[str, Any]:
        values: dict[str, str] = {}
        if isinstance(item, dict):
            raw = item
            data = item.get("data")
            if isinstance(data, dict):
                raw = {**item, **data}
            for key in ("file", "path", "url", "image", "name"):
                text = str(raw.get(key) or "").strip()
                if text:
                    values[key] = text
        else:
            for key in ("file", "path", "url", "image", "name"):
                text = str(getattr(item, key, "") or "").strip()
                if text:
                    values[key] = text
            converter_name = "convert_to_file_path" if media_type in {"image", "record"} else "get_file"
            converter = getattr(item, converter_name, None)
            if callable(converter) and not (values.get("path") or values.get("file")):
                try:
                    converted = converter()
                    if hasattr(converted, "__await__"):
                        converted = await converted
                    converted_text = str(converted or "").strip()
                    if converted_text:
                        values["path"] = converted_text
                except Exception:
                    pass
        ref = values.get("path") or values.get("file") or values.get("url") or values.get("image")
        if not ref:
            return {}
        part: dict[str, Any] = {"type": media_type}
        if values.get("path"):
            part["path"] = values["path"]
        if values.get("file"):
            part["file"] = values["file"]
        if values.get("url"):
            part["url"] = values["url"]
        if values.get("name"):
            part["name"] = values["name"]
        if media_type == "image":
            part["image_url"] = {"url": ref}
        return part
