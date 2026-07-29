from __future__ import annotations

from collections import deque
from typing import Any


class HistoryWatchMixin:
    def _observed_history_keys(self) -> set[str]:
        keys = getattr(self, "_observed_user_history_keys", None)
        if not isinstance(keys, set):
            keys = set()
            self._observed_user_history_keys = keys
        return keys

    def _remember_observed_history_key(self, key: str) -> bool:
        keys = self._observed_history_keys()
        if key in keys:
            return False
        order = getattr(self, "_observed_user_history_order", None)
        if not isinstance(order, deque):
            order = deque()
            self._observed_user_history_order = order
        keys.add(key)
        order.append(key)
        limit = max(1, int(getattr(self, "_MESSAGE_DEDUP_LIMIT", 4096)))
        while len(order) > limit:
            keys.discard(order.popleft())
        return True

    def _observed_history_key(self, event: Any, text: str) -> str:
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event)
        if scope and message_id:
            return f"{scope}:{message_id}"
        sender_id = self._safe_event_call(event, "get_sender_id")
        return f"{scope}:{sender_id}:{text}"

    def _event_user_history_media_label(self, event: Any) -> str:
        counts = {"image": 0, "voice": 0, "video": 0, "file": 0}
        for item in self._event_message_items(event):
            kind = self._event_component_kind(item)
            if "image" in kind:
                counts["image"] += 1
            elif "record" in kind or "voice" in kind:
                counts["voice"] += 1
            elif "video" in kind:
                counts["video"] += 1
            elif "file" in kind:
                counts["file"] += 1
        labels = []
        if counts["image"]:
            labels.append(f"{counts['image']} 张图片")
        if counts["voice"]:
            labels.append(f"{counts['voice']} 条语音")
        if counts["video"]:
            labels.append(f"{counts['video']} 个视频")
        if counts["file"]:
            labels.append(f"{counts['file']} 个文件")
        return "、".join(labels)

    async def record_observed_private_user_message(self, event: Any) -> bool:
        if self._event_is_group_message(event):
            return False
        scope = self._event_session_id(event)
        if not scope:
            return False
        text = self._event_user_history_text(event)
        media_label = self._event_user_history_media_label(event)
        if not text and not media_label:
            return False
        key = self._observed_history_key(event, text or media_label)
        if not self._remember_observed_history_key(key):
            return False
        if self.event_was_recalled(event):
            return False
        conversation_text = text or (
            "[图片]" if "图片" in media_label else f"（发送了{media_label}）"
        )
        conversation_saved = await self._append_user_history(
            scope, event, conversation_text
        )
        platform_saved = await self._append_platform_user_history(
            event, text, media_label=media_label
        )
        return conversation_saved or platform_saved
