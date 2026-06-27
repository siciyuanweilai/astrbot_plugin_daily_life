from __future__ import annotations

import datetime
import html
import time
from typing import Any

from .grain import StructuredMessage


class StructuredGlanceMixin:
    def _structured_result_text(self, event: Any) -> tuple[str, str]:
        pending = str(getattr(event, "_daily_life_structured_pending_bot_text", "") or "").strip()
        pending_media = str(getattr(event, "_daily_life_structured_pending_bot_media", "") or "").strip()
        if pending:
            return pending, pending_media
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list):
            return "", ""
        texts: list[str] = []
        media: list[str] = []
        for comp in chain:
            if isinstance(comp, str):
                text = comp
                kind = "text"
            elif isinstance(comp, dict):
                kind = str(comp.get("type") or "").lower()
                text = str(comp.get("text") or comp.get("content") or "")
            else:
                kind = comp.__class__.__name__.lower()
                text = str(getattr(comp, "text", "") or getattr(comp, "content", "") or "")
            text = text.strip()
            if text:
                texts.append(text)
            if "record" in kind or "voice" in kind:
                media.append("语音")
            elif "image" in kind:
                media.append("图片")
            elif "video" in kind:
                media.append("视频")
        if texts:
            return "\n".join(texts).strip(), ""
        if media:
            label = "、".join(dict.fromkeys(media))
            return f"[{label}]", label
        return "", ""

    def mark_structured_pending_bot_text(self, event: Any, text: str, media: str = "") -> None:
        if event is None:
            return
        setattr(event, "_daily_life_structured_pending_bot_text", str(text or "").strip())
        setattr(event, "_daily_life_structured_pending_bot_media", str(media or "").strip())

    def note_structured_sent_result(self, event: Any) -> StructuredMessage | None:
        scope = self._event_session_id(event)
        if not scope:
            return None
        text, media = self._structured_result_text(event)
        if not text:
            return None
        return self.note_structured_bot_message(scope, text, source_event=event, media=media)

    def format_structured_message_context(self, event_or_scope: Any, limit: int = 8) -> str:
        scope = event_or_scope if isinstance(event_or_scope, str) else self._event_session_id(event_or_scope)
        scope = str(scope or "").strip()
        if not scope:
            return ""
        messages = list(self._structured_scope_messages(scope))[-max(1, int(limit or 8)) :]
        if not messages:
            return ""
        lines = [
            "<structured_conversation>",
            "  <note>最近真实消息结构，优先用于判断群聊里谁在对谁说话；不是长期记忆。</note>",
        ]
        for item in messages:
            lines.append(self._format_structured_message_line(item))
        lines.append("</structured_conversation>")
        return "\n".join(lines)

    def _format_structured_message_line(self, item: StructuredMessage) -> str:
        attrs = {
            "id": item.message_id or item.fallback_key,
            "time": datetime.datetime.fromtimestamp(item.timestamp or time.time()).strftime("%H:%M"),
            "role": "assistant" if item.is_bot else "user",
            "user_id": item.sender_id,
            "user": item.display_sender,
            "group_card": item.sender_card,
            "talking_to": item.talking_to_name or item.talking_to_id,
            "reply_to": item.reply_to_id,
        }
        attr_text = " ".join(
            f'{key}="{html.escape(str(value), quote=True)}"'
            for key, value in attrs.items()
            if str(value or "").strip()
        )
        content = self._structured_text(item.content, 180)
        if item.media and item.media not in content:
            content = f"[{item.media}] {content}".strip()
        at_targets = "、".join(target.name or target.user_id for target in item.at_targets)
        quote = self._structured_text(item.reply_to_content, 100)
        extras = []
        if at_targets:
            extras.append(f"@{at_targets}")
        if quote:
            extras.append(f"引用：{quote}")
        body = content
        if extras:
            body = f"{body}（{'；'.join(extras)}）" if body else "；".join(extras)
        return f"  <message {attr_text}>{html.escape(body)}</message>"
