from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StructuredTarget:
    user_id: str = ""
    name: str = ""


@dataclass
class StructuredMessage:
    scope: str
    message_id: str = ""
    fallback_key: str = ""
    sender_id: str = ""
    sender_name: str = ""
    sender_card: str = ""
    group_id: str = ""
    group_name: str = ""
    content: str = ""
    timestamp: float = 0.0
    is_bot: bool = False
    media: str = ""
    reply_to_id: str = ""
    reply_to_sender_id: str = ""
    reply_to_sender_name: str = ""
    reply_to_content: str = ""
    at_targets: list[StructuredTarget] = field(default_factory=list)
    talking_to_id: str = ""
    talking_to_name: str = ""
    visual_summary: str = ""

    @property
    def key(self) -> str:
        return self.message_id or self.fallback_key

    @property
    def display_sender(self) -> str:
        return self.sender_card or self.sender_name or self.sender_id or ("我" if self.is_bot else "对方")


class StructuredBaseMixin:
    _STRUCTURED_CONTEXT_LIMIT = 32

    @staticmethod
    def _structured_text(value: Any, limit: int = 180) -> str:
        text = " ".join(str(value or "").split())
        if limit > 0 and len(text) > limit:
            return text[:limit].rstrip() + "..."
        return text

    @staticmethod
    def _structured_first_text(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _structured_component_kind(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("type") or item.get("kind") or "").strip().lower()
        kind = item.__class__.__name__.strip().lower()
        explicit = str(getattr(item, "type", "") or getattr(item, "kind", "") or "").strip().lower()
        return explicit or kind

    @staticmethod
    def _structured_component_data(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            data = item.get("data")
            if isinstance(data, dict):
                return {**item, **data}
            return dict(item)
        data: dict[str, Any] = {}
        for key in (
            "user_id",
            "qq",
            "target",
            "target_id",
            "target_user_id",
            "target_user_nickname",
            "target_user_cardname",
            "target_name",
            "name",
            "message_id",
            "target_message_id",
            "reply_to",
            "sender_id",
            "target_message_sender_id",
            "target_message_sender_nickname",
            "target_message_sender_cardname",
            "target_message_content",
            "message_str",
            "text",
            "content",
        ):
            value = getattr(item, key, None)
            if value not in (None, ""):
                data[key] = value
        return data

    def _structured_sender_card_from_event(self, event: Any) -> str:
        for source in self._event_sources(event):
            message_obj = getattr(source, "message_obj", None)
            sender = getattr(message_obj, "sender", None)
            card = self._structured_first_text(
                getattr(sender, "card", ""),
                getattr(sender, "cardname", ""),
                getattr(sender, "user_cardname", ""),
            )
            if card:
                return card
            raw = getattr(message_obj, "raw_message", None) or getattr(source, "raw_message", None)
            if isinstance(raw, dict):
                raw_sender = raw.get("sender")
                if isinstance(raw_sender, dict):
                    card = self._structured_first_text(
                        raw_sender.get("card"),
                        raw_sender.get("cardname"),
                        raw_sender.get("user_cardname"),
                    )
                    if card:
                        return card
        return ""

    def _structured_self_id(self, event: Any) -> str:
        self_id = self._safe_event_call(event, "get_self_id")
        if self_id:
            return self_id
        for source in self._event_sources(event):
            message_obj = getattr(source, "message_obj", None)
            for current in (source, message_obj):
                text = self._structured_first_text(
                    getattr(current, "self_id", ""),
                    getattr(current, "bot_id", ""),
                )
                if text:
                    return text
        return ""

    @staticmethod
    def _structured_media_outline(items: list[Any]) -> str:
        labels: list[str] = []
        for item in items:
            kind = StructuredBaseMixin._structured_component_kind(item)
            if "image" in kind:
                labels.append("[图片]")
            elif "record" in kind or "voice" in kind:
                labels.append("[语音]")
            elif "video" in kind:
                labels.append("[视频]")
            elif "file" in kind:
                labels.append("[文件]")
        return " ".join(labels)

    @classmethod
    def _structured_content_with_media(cls, content: str, items: list[Any]) -> str:
        content = str(content or "").strip()
        media = cls._structured_media_outline(items)
        if content and media:
            return f"{content} {media}"
        return content or media
