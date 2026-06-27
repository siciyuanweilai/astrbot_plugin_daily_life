from __future__ import annotations

import json
from typing import Any


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
    def _history_message_parts_for_user(text: str, media_label: str = "") -> list[dict[str, str]]:
        if text:
            return [{"type": "text", "text": text}]
        return [{"type": "text", "text": media_label or "（无可见文本）"}]
