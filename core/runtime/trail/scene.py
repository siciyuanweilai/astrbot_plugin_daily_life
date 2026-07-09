from __future__ import annotations

from typing import Any


class HistorySceneMixin:
    def _astrbot_config(self, scope: str) -> dict[str, Any]:
        context = getattr(self, "context", None)
        getter = getattr(context, "get_config", None)
        if callable(getter):
            try:
                config = getter(umo=scope)
                if isinstance(config, dict):
                    return config
            except TypeError:
                pass
        config = getattr(context, "config", None)
        return config if isinstance(config, dict) else {}

    def _astrbot_provider_settings(self, scope: str) -> dict[str, Any]:
        settings = self._astrbot_config(scope).get("provider_settings", {})
        return settings if isinstance(settings, dict) else {}

    def _event_sender_id_name(self, event: Any) -> tuple[str, str]:
        sender_id = self._safe_event_call(event, "get_sender_id")
        sender_name = self._safe_event_call(event, "get_sender_name")
        for source in self._event_sources(event):
            sender = getattr(getattr(source, "message_obj", None), "sender", None)
            if sender is None:
                continue
            sender_id = sender_id or str(getattr(sender, "user_id", "") or "").strip()
            sender_name = sender_name or str(getattr(sender, "nickname", "") or "").strip()
        return sender_id, sender_name

    def _event_group_name(self, event: Any) -> str:
        group_name = self._safe_event_call(event, "get_group_name")
        for source in self._event_sources(event):
            group = getattr(getattr(source, "message_obj", None), "group", None)
            group_name = group_name or str(getattr(group, "group_name", "") or "").strip()
        return group_name

    def _system_reminder_text(self, scope: str, event: Any) -> str:
        settings = self._astrbot_provider_settings(scope)
        parts: list[str] = []
        if settings.get("identifier"):
            sender_id, sender_name = self._event_sender_id_name(event)
            parts.append(f"User ID: {sender_id}, Nickname: {sender_name}")
        if settings.get("group_name_display") and self._event_is_group_message(event):
            group_name = self._event_group_name(event)
            if group_name:
                parts.append(f"Group name: {group_name}")
        if settings.get("datetime_system_prompt", True):
            now = self._astrbot_now_for_scope(scope)
            current_time = now.strftime("%Y-%m-%d %H:%M (%Z)")
            parts.append(f"Current datetime: {current_time}, Weekday: {self._WEEKDAY_NAMES[now.weekday()]}")
        content = "\n".join(parts)
        return f"<system_reminder>{content}</system_reminder>" if content else ""

    def _conversation_user_content(self, scope: str, event: Any, text: str) -> str | list[dict[str, Any]]:
        reminder = self._system_reminder_text(scope, event)
        if not reminder:
            return text
        return [
            {"type": "text", "text": text},
            {"type": "text", "text": reminder},
        ]

    async def _conversation_user_content_from_event(
        self,
        scope: str,
        event: Any,
        text: str,
        media_label: str = "",
    ) -> str | list[dict[str, Any]]:
        text = str(text or "").strip()
        media_parts = await self._history_media_parts_from_event(event)
        reminder = self._system_reminder_text(scope, event)
        if not media_parts:
            return self._conversation_user_content(scope, event, text or f"（发送了{media_label}）")

        has_image = any(str(part.get("type") or "") == "image" for part in media_parts)
        placeholder = "[图片]" if has_image else f"（发送了{media_label}）"
        parts: list[dict[str, Any]] = [{"type": "text", "text": text or placeholder}]
        image_parts: list[dict[str, Any]] = []
        for part in media_parts:
            media_type = str(part.get("type") or "").strip()
            path = str(part.get("path") or part.get("file") or part.get("url") or "").strip()
            if not media_type or not path:
                continue
            if media_type == "image":
                parts.append({"type": "text", "text": f"[Image Attachment: path {path}]"})
                image_parts.append({"type": "image_url", "image_url": {"url": await self._history_display_url(path)}})
            elif media_type == "record":
                parts.append({"type": "text", "text": f"[Audio Attachment: path {path}]"})
            elif media_type == "video":
                parts.append({"type": "text", "text": f"[Video Attachment: path {path}]"})
            elif media_type == "file":
                name = str(part.get("name") or "").strip()
                prefix = f"name {name}, " if name else ""
                parts.append({"type": "text", "text": f"[File Attachment: {prefix}path {path}]"})
        if reminder:
            parts.append({"type": "text", "text": reminder})
        parts.extend(image_parts)
        return parts

    def _user_history_item(self, scope: str, event: Any, text: str) -> dict[str, Any]:
        return {"role": "user", "content": self._conversation_user_content(scope, event, text)}

    async def _user_history_item_from_event(
        self,
        scope: str,
        event: Any,
        text: str,
        media_label: str = "",
    ) -> dict[str, Any]:
        return {
            "role": "user",
            "content": await self._conversation_user_content_from_event(scope, event, text, media_label),
        }
