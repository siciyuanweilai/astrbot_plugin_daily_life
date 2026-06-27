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
            parts.append(f"用户标识：{sender_id}，昵称：{sender_name}")
        if settings.get("group_name_display") and self._event_is_group_message(event):
            group_name = self._event_group_name(event)
            if group_name:
                parts.append(f"群名称：{group_name}")
        if settings.get("datetime_system_prompt", True):
            now = self._astrbot_now_for_scope(scope)
            current_time = now.strftime("%Y-%m-%d %H:%M (%Z)")
            parts.append(f"当前时间：{current_time}，星期：{self._WEEKDAY_NAMES[now.weekday()]}")
        content = "\n".join(parts)
        return f"<system_reminder>{content}</system_reminder>" if content else ""

    def _conversation_user_content(self, scope: str, event: Any, text: str) -> str | list[dict[str, str]]:
        reminder = self._system_reminder_text(scope, event)
        if not reminder:
            return text
        return [
            {"type": "text", "text": text},
            {"type": "text", "text": reminder},
        ]

    def _user_history_item(self, scope: str, event: Any, text: str) -> dict[str, Any]:
        return {"role": "user", "content": self._conversation_user_content(scope, event, text)}
