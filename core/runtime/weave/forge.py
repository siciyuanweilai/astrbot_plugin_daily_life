from __future__ import annotations

import time
from typing import Any

from ...sources.platforms import parse_unified_origin
from .grain import StructuredMessage


class StructuredForgeMixin:
    def note_structured_incoming_message(self, event: Any) -> StructuredMessage | None:
        scope = self._event_session_id(event)
        if not scope or self._proactive_is_self_message(event):
            return None
        content = str(getattr(event, "message_str", "") or "").strip()
        items = self._event_message_items(event)
        if not content and not items:
            return None
        group_id, group_name = self._event_group_meta(event)
        sender_id = self._safe_event_call(event, "get_sender_id")
        sender_name = self._safe_event_call(event, "get_sender_name") or sender_id or "对方"
        message_id = self._event_message_id(event)
        reply = self._structured_extract_reply(event, scope)
        message = StructuredMessage(
            scope=scope,
            message_id=message_id,
            fallback_key=f"event:{id(event)}",
            sender_id=sender_id,
            sender_name=sender_name,
            sender_card=self._structured_sender_card_from_event(event),
            group_id=group_id,
            group_name=group_name,
            content=self._structured_content_with_media(content, items),
            timestamp=time.time(),
            is_bot=False,
            reply_to_id=reply.get("message_id", ""),
            reply_to_sender_id=reply.get("sender_id", ""),
            reply_to_sender_name=reply.get("sender_name", ""),
            reply_to_content=reply.get("content", ""),
            at_targets=self._structured_extract_at_targets(event),
        )
        self._structured_resolve_talking_to(message, self._structured_self_id(event), source_event=event)
        return self._structured_upsert_message(message)

    def note_structured_bot_message(
        self,
        scope: str,
        content: str,
        *,
        source_event: Any = None,
        media: str = "",
        reply_to_id: str = "",
    ) -> StructuredMessage | None:
        scope = str(scope or "").strip()
        content = str(content or "").strip()
        if not scope or not content:
            return None
        group_id = group_name = ""
        sender_id = sender_name = ""
        if source_event is not None:
            group_id, group_name = self._event_group_meta(source_event)
            sender_id = self._structured_self_id(source_event)
            sender_name = "我"
            reply_to_id = reply_to_id or self._event_message_id(source_event)
        elif "GroupMessage" in scope:
            _, group_id = parse_unified_origin(scope)
        target = self._structured_find_message(scope, reply_to_id)
        message = StructuredMessage(
            scope=scope,
            message_id=f"bot:{int(time.time() * 1000)}:{len(self._structured_scope_messages(scope))}",
            fallback_key=f"bot:{id(content)}",
            sender_id=sender_id or "bot",
            sender_name=sender_name or "我",
            sender_card="我",
            group_id=group_id,
            group_name=group_name,
            content=content,
            timestamp=time.time(),
            is_bot=True,
            media=media,
            reply_to_id=reply_to_id,
            reply_to_sender_id=target.sender_id if target else "",
            reply_to_sender_name=target.display_sender if target else "",
            reply_to_content=target.content if target else "",
        )
        self._structured_resolve_talking_to(message, sender_id)
        return self._structured_upsert_message(message)

    def update_structured_message_visual_summary(
        self,
        scope: str,
        message_key: str,
        summary: str,
    ) -> StructuredMessage | None:
        scope = str(scope or "").strip()
        message_key = str(message_key or "").strip()
        summary = self._structured_text(summary, 96).strip(" ：:，,。")
        if not scope or not message_key or not summary:
            return None

        message = self._structured_find_message(scope, message_key)
        if not message:
            return None

        placeholder = "[图片]"
        visual = f"[图片：{summary}]"
        content = str(message.content or "").strip()
        if placeholder in content:
            message.content = content.replace(placeholder, visual, 1)
        elif "[图片：" not in content:
            message.content = f"{content} {visual}".strip()
        message.visual_summary = summary
        return message

    def update_structured_message_video_summary(
        self,
        scope: str,
        message_key: str,
        summary: str,
    ) -> StructuredMessage | None:
        scope = str(scope or "").strip()
        message_key = str(message_key or "").strip()
        summary = self._structured_text(summary, 120).strip(" ：:，,。")
        if not scope or not message_key or not summary:
            return None

        message = self._structured_find_message(scope, message_key)
        if not message:
            return None

        placeholder = "[视频]"
        visual = f"[视频：{summary}]"
        content = str(message.content or "").strip()
        if placeholder in content:
            message.content = content.replace(placeholder, visual, 1)
        elif "[视频：" not in content:
            message.content = f"{content} {visual}".strip()
        return message
