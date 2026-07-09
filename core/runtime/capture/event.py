import hashlib
import asyncio
import string
from pathlib import Path
from typing import Any

from ...sources.events import event_attr, event_call, iter_event_sources, safe_invoke
from ...sources.platforms import parse_unified_origin


class CaptureEventMixin:
    @staticmethod
    def _event_sources(event: Any) -> list[Any]:
        return iter_event_sources(event)

    @staticmethod
    def _event_session_id(event: Any) -> str:
        for current in CaptureEventMixin._event_sources(event):
            for attr in ("unified_msg_origin", "session_id"):
                value = str(getattr(current, attr, "") or "").strip()
                if value:
                    return value
        return ""

    @staticmethod
    def _safe_event_call(event: Any, method_name: str) -> str:
        return event_call(event, method_name)

    @staticmethod
    def _safe_event_attr(event: Any, attr_name: str) -> str:
        return event_attr(event, attr_name)

    @classmethod
    def _event_is_group_message(cls, event: Any) -> bool:
        if cls._safe_event_call(event, "get_group_id"):
            return True
        parts = cls._safe_event_attr(event, "unified_msg_origin").split(":", 2)
        return len(parts) >= 2 and parts[1].lower() == "groupmessage"

    @classmethod
    def _event_platform_user(cls, event: Any) -> tuple[str, str]:
        origin = cls._safe_event_attr(event, "unified_msg_origin")
        platform, real_id = parse_unified_origin(origin)
        sender_id = cls._safe_event_call(event, "get_sender_id")
        if cls._event_is_group_message(event):
            return platform or cls._safe_event_call(event, "get_platform_name"), sender_id
        if real_id:
            return platform, real_id
        return platform or cls._safe_event_call(event, "get_platform_name"), sender_id

    @classmethod
    def _event_group_meta(cls, event: Any) -> tuple[str, str]:
        group_id = cls._safe_event_call(event, "get_group_id")
        group_name = cls._safe_event_call(event, "get_group_name")
        for attr in ("group_id", "group_name"):
            value = cls._safe_event_attr(event, attr)
            if attr == "group_id" and value and not group_id:
                group_id = value
            if attr == "group_name" and value and not group_name:
                group_name = value
        if not group_id and cls._event_is_group_message(event):
            _, real_id = parse_unified_origin(cls._safe_event_attr(event, "unified_msg_origin"))
            group_id = real_id
        return group_id, group_name

    @classmethod
    def _event_message_id(cls, event: Any) -> str:
        attrs = ("message_id", "message_seq", "id")
        for source in cls._event_sources(event):
            for current in (source, getattr(source, "message_obj", None)):
                if current is None:
                    continue
                for attr in attrs:
                    value = str(getattr(current, attr, "") or "").strip()
                    if value:
                        return value
                raw = getattr(current, "raw_message", None)
                if isinstance(raw, dict):
                    for attr in attrs:
                        value = str(raw.get(attr) or "").strip()
                        if value:
                            return value
        return cls._safe_event_call(event, "get_message_id")

    @classmethod
    def _event_is_platform_directed(cls, event: Any) -> bool:
        return any(bool(getattr(source, "is_at_or_wake_command", False)) for source in cls._event_sources(event))

    def _event_bot_aliases(self) -> list[str]:
        config = getattr(self, "config", None)
        aliases = getattr(config, "bot_identity_aliases", []) if config is not None else []
        if not isinstance(aliases, (list, tuple, set)):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            text = str(alias or "").strip().lstrip("@").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @staticmethod
    def _event_ascii_token_char(char: str) -> bool:
        return bool(char) and (char in string.ascii_letters or char in string.digits or char == "_")

    @classmethod
    def _event_text_contains_bot_alias(cls, text: str, alias: str) -> bool:
        source = str(text or "")
        target = str(alias or "").strip()
        if not source or not target:
            return False
        if not target.isascii() or not target.replace("_", "").isalnum():
            return target in source

        source_lower = source.lower()
        target_lower = target.lower()
        start = 0
        while True:
            index = source_lower.find(target_lower, start)
            if index < 0:
                return False
            before = source_lower[index - 1] if index > 0 else ""
            after_index = index + len(target_lower)
            after = source_lower[after_index] if after_index < len(source_lower) else ""
            if not cls._event_ascii_token_char(before) and not cls._event_ascii_token_char(after):
                return True
            start = index + 1

    def _event_mentions_bot_alias(self, event: Any) -> bool:
        text = str(getattr(event, "message_str", "") or "").strip()
        if not text:
            return False
        return any(self._event_text_contains_bot_alias(text, alias) for alias in self._event_bot_aliases())

    def _event_is_directed(self, event: Any) -> bool:
        return self._event_is_platform_directed(event) or self._event_mentions_bot_alias(event)

    @classmethod
    def _event_has_command_handler(cls, event: Any) -> bool:
        for source in cls._event_sources(event):
            get_extra = getattr(source, "get_extra", None)
            handlers = get_extra("activated_handlers", []) if callable(get_extra) else []
            for handler in handlers or []:
                for item in getattr(handler, "event_filters", []) or []:
                    if "Command" in item.__class__.__name__:
                        return True
        return False

    @classmethod
    def _event_has_quote(cls, event: Any) -> bool:
        for source in cls._event_sources(event):
            for attr in ("quote", "reply", "reply_message", "reply_id", "quote_id"):
                if getattr(source, attr, None):
                    return True
            getter = getattr(source, "get_messages", None)
            messages = safe_invoke(getter)
            if messages is None:
                continue
            messages = messages or []
            for item in messages:
                type_name = item.__class__.__name__.lower()
                if isinstance(item, dict):
                    type_name = str(item.get("type") or item.get("kind") or type_name).strip().lower()
                if "reply" in type_name or "quote" in type_name:
                    return True
        return False

    @classmethod
    def _event_message_items(cls, event: Any) -> list[Any]:
        for source in cls._event_sources(event):
            getter = getattr(source, "get_messages", None)
            if callable(getter):
                items = safe_invoke(getter) or []
                if isinstance(items, list):
                    return items
            message_obj = getattr(source, "message_obj", None)
            raw = getattr(message_obj, "message", None)
            if isinstance(raw, list):
                return raw
        return []

    @classmethod
    def _event_message_component_facts(cls, event: Any, text: str = "") -> str:
        text = str(text if text is not None else getattr(event, "message_str", "") or "").strip()
        items = cls._event_message_items(event)
        counts = {"image": 0, "voice": 0, "video": 0, "file": 0, "text": 0, "other": 0}
        for item in items:
            kind = cls._event_component_kind(item)
            if "image" in kind:
                counts["image"] += 1
            elif "record" in kind or "voice" in kind:
                counts["voice"] += 1
            elif "video" in kind:
                counts["video"] += 1
            elif "file" in kind:
                counts["file"] += 1
            elif kind in {"text", "plain"}:
                counts["text"] += 1
            else:
                counts["other"] += 1

        parts = [
            f"真实图片组件：{counts['image']} 个",
            f"真实语音组件：{counts['voice']} 个",
            f"真实视频组件：{counts['video']} 个",
            f"文件组件：{counts['file']} 个",
        ]
        if counts["text"]:
            parts.append(f"文本组件：{counts['text']} 个")
        if counts["other"]:
            parts.append(f"其他组件：{counts['other']} 个")
        facts = "；".join(parts)
        content = text or "（无可见文本）"
        return (
            f"消息组件事实：{facts}。\n"
            f"可见文本内容：{content}\n"
            "判断媒体时只看真实组件数量，不要把可见文本里的“[图片已发送]”“图片已发送”等字样当成图片。"
        )

    @staticmethod
    def _event_component_kind(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("type") or item.get("kind") or "").strip().lower()
        explicit = str(getattr(item, "type", "") or getattr(item, "kind", "") or "").strip().lower()
        return explicit or item.__class__.__name__.strip().lower()

    @staticmethod
    def _completion_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("completion_text", "completion", "text", "content"):
                text = value.get(key)
                if isinstance(text, str) and text.strip():
                    return text.strip()
        for key in ("completion_text", "completion", "text", "content"):
            text = getattr(value, key, None)
            if isinstance(text, str) and text.strip():
                return text.strip()
        return str(value or "").strip()

    @classmethod
    def _message_media_payload(cls, item: Any) -> dict[str, str]:
        values: dict[str, str] = {}

        def collect_mapping(source: Any) -> None:
            if not isinstance(source, dict):
                return
            for key in ("file", "path", "url", "image", "hash", "file_id", "file_unique", "file_unique_id"):
                text = str(source.get(key) or "").strip()
                if text:
                    values[key] = text
            for key in ("data", "origin", "original", "raw", "source", "media"):
                nested = source.get(key)
                if isinstance(nested, dict):
                    collect_mapping(nested)

        def collect_object(source: Any) -> None:
            if source is None:
                return
            for key in ("file", "path", "url", "image", "hash", "file_id", "file_unique", "file_unique_id"):
                text = str(getattr(source, key, "") or "").strip()
                if text:
                    values[key] = text
            for key in ("data", "origin", "original", "raw", "source", "media"):
                nested = getattr(source, key, None)
                if isinstance(nested, dict):
                    collect_mapping(nested)

        if isinstance(item, dict):
            collect_mapping(item)
            return values
        collect_object(item)
        return values

    @staticmethod
    async def _media_fingerprint(payload: dict[str, str]) -> str:
        explicit = (payload.get("hash") or payload.get("file_unique") or payload.get("file_unique_id") or payload.get("file_id") or "").strip()
        if explicit:
            return explicit[:80]
        local_path = (payload.get("path") or payload.get("file") or "").strip()
        if local_path and not local_path.startswith(("http://", "https://")):
            try:
                path = Path(local_path).expanduser().resolve()
                if path.is_file():
                    data = await asyncio.to_thread(path.read_bytes)
                    return hashlib.sha256(data).hexdigest()
            except Exception:
                return ""
        marker = payload.get("path") or payload.get("file") or payload.get("url") or payload.get("image") or ""
        return hashlib.sha256(marker.encode("utf-8", errors="ignore")).hexdigest() if marker else ""

    @classmethod
    def _event_quote_context(cls, event: Any) -> str:
        items: list[str] = []

        def text_from(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, dict):
                for key in ("message_str", "message", "text", "content", "raw_message"):
                    text = str(value.get(key) or "").strip()
                    if text:
                        return text
                return ""
            for attr in ("message_str", "message", "text", "content", "raw_message"):
                text = str(getattr(value, attr, "") or "").strip()
                if text:
                    return text
            return ""

        for source in cls._event_sources(event):
            for attr in ("quote", "reply", "reply_message"):
                text = text_from(getattr(source, attr, None))
                if text and text not in items:
                    items.append(text)
            getter = getattr(source, "get_messages", None)
            if callable(getter):
                messages = safe_invoke(getter) or []
                for item in messages:
                    type_name = item.__class__.__name__.lower()
                    if "reply" not in type_name and "quote" not in type_name:
                        continue
                    text = text_from(item)
                    if text and text not in items:
                        items.append(text)
        return "\n".join(f"- {item[:220]}" for item in items[:3])

    def _event_profile_id(self, event: Any, fallback_name: str = "用户") -> str:
        if self._event_is_group_message(event):
            return self._safe_event_call(event, "get_sender_id") or fallback_name
        origin = self._safe_event_attr(event, "unified_msg_origin")
        if origin:
            _, real_id = parse_unified_origin(origin)
            return real_id or origin
        return self._safe_event_call(event, "get_sender_id") or fallback_name

    def _voice_allowed_for_scope(self, scope_or_event: Any) -> bool:
        voice_config = getattr(self.config, "voice_generation", None)
        return bool(voice_config and voice_config.enabled)
