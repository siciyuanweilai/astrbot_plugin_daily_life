from __future__ import annotations

from typing import Any

from .grain import StructuredMessage, StructuredTarget


class StructuredRouteMixin:
    def _structured_extract_at_targets(self, event: Any) -> list[StructuredTarget]:
        targets: list[StructuredTarget] = []
        seen: set[str] = set()
        for item in self._event_message_items(event):
            kind = self._structured_component_kind(item)
            if "at" not in kind and kind not in {"mention"}:
                continue
            data = self._structured_component_data(item)
            user_id = self._structured_first_text(
                data.get("target_user_id"),
                data.get("user_id"),
                data.get("target_id"),
                data.get("target"),
                data.get("qq"),
            )
            if not user_id or user_id in seen:
                continue
            name = self._structured_first_text(
                data.get("target_user_cardname"),
                data.get("target_user_nickname"),
                data.get("target_name"),
                data.get("name"),
                user_id,
            )
            targets.append(StructuredTarget(user_id=user_id, name=name))
            seen.add(user_id)
        return targets

    def _structured_extract_reply(self, event: Any, scope: str) -> dict[str, str]:
        reply: dict[str, str] = {}
        for item in self._event_message_items(event):
            kind = self._structured_component_kind(item)
            if "reply" not in kind and "quote" not in kind:
                continue
            data = self._structured_component_data(item)
            reply["message_id"] = self._structured_first_text(
                data.get("target_message_id"),
                data.get("message_id"),
                data.get("reply_to"),
                data.get("id"),
            )
            reply["sender_id"] = self._structured_first_text(
                data.get("target_message_sender_id"),
                data.get("sender_id"),
            )
            reply["sender_name"] = self._structured_first_text(
                data.get("target_message_sender_cardname"),
                data.get("target_message_sender_nickname"),
                data.get("sender_name"),
            )
            reply["content"] = self._structured_first_text(
                data.get("target_message_content"),
                data.get("message_str"),
                data.get("text"),
                data.get("content"),
            )
            break

        reply_id = reply.get("message_id", "")
        if reply_id:
            target = self._structured_find_message(scope, reply_id)
            if target:
                if not reply.get("sender_id"):
                    reply["sender_id"] = target.sender_id
                if not reply.get("sender_name"):
                    reply["sender_name"] = target.display_sender
                if not reply.get("content"):
                    reply["content"] = target.content

        if not reply.get("content"):
            quote_context = self._event_quote_context(event)
            if quote_context:
                reply["content"] = quote_context
        return reply

    def _structured_find_message(self, scope: str, message_id: str) -> StructuredMessage | None:
        scope = str(scope or "").strip()
        message_id = str(message_id or "").strip()
        if not scope or not message_id:
            return None
        for item in reversed(self._structured_scope_messages(scope)):
            if message_id in {item.message_id, item.fallback_key, item.key}:
                return item
        return None

    def _structured_message_for_event(self, event: Any) -> StructuredMessage | None:
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event)
        message = self._structured_find_message(scope, message_id)
        if message:
            return message
        messages = list(self._structured_scope_messages(scope)) if scope else []
        return messages[-1] if messages else None

    @staticmethod
    def _structured_talking_to_bot(message: StructuredMessage | None) -> bool:
        if not message:
            return False
        return str(message.talking_to_id or "").strip() == "bot"

    @staticmethod
    def _structured_talking_to_other_user(message: StructuredMessage | None) -> bool:
        if not message:
            return False
        target = str(message.talking_to_id or "").strip()
        return bool(target and target not in {"bot", "group", "private"})

    def _structured_resolve_talking_to(
        self,
        message: StructuredMessage,
        self_id: str = "",
        source_event: Any = None,
    ) -> None:
        if message.is_bot:
            if message.reply_to_id:
                target = self._structured_find_message(message.scope, message.reply_to_id)
                if target and not target.is_bot:
                    message.talking_to_id = target.sender_id
                    message.talking_to_name = target.display_sender
                    return
            message.talking_to_id = "group" if message.group_id else "private"
            message.talking_to_name = "群聊" if message.group_id else "对方"
            return

        if not message.group_id:
            message.talking_to_id = "bot"
            message.talking_to_name = "我"
            return

        if any(target.user_id == self_id for target in message.at_targets if self_id):
            message.talking_to_id = "bot"
            message.talking_to_name = "我"
            return

        mentions_bot_alias = getattr(self, "_event_mentions_bot_alias", None)
        if callable(mentions_bot_alias) and source_event is not None and mentions_bot_alias(source_event):
            message.talking_to_id = "bot"
            message.talking_to_name = "我"
            return

        if message.reply_to_id:
            target = self._structured_find_message(message.scope, message.reply_to_id)
            if target:
                message.talking_to_id = "bot" if target.is_bot else target.sender_id
                message.talking_to_name = "我" if target.is_bot else target.display_sender
                return

        if self_id and message.reply_to_sender_id == self_id:
            message.talking_to_id = "bot"
            message.talking_to_name = "我"
            return
        if message.reply_to_sender_id:
            message.talking_to_id = message.reply_to_sender_id
            message.talking_to_name = message.reply_to_sender_name or message.reply_to_sender_id
            return

        for target in message.at_targets:
            if self_id and target.user_id == self_id:
                continue
            message.talking_to_id = target.user_id
            message.talking_to_name = target.name or target.user_id
            return

        message.talking_to_id = "group"
        message.talking_to_name = "群聊"
