from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

from ...sources.platforms import parse_unified_origin
from ..markers import LOG_PREFIX


class HistoryWriteMixin:
    async def _conversation_id_for_scope(self, scope: str) -> str:
        context = getattr(self, "context", None)
        manager = getattr(context, "conversation_manager", None)
        if not manager or not scope:
            return ""
        cid = await manager.get_curr_conversation_id(scope)
        if cid:
            return str(cid)
        new_conversation = getattr(manager, "new_conversation", None)
        if not callable(new_conversation):
            return ""
        try:
            return str(await new_conversation(scope, scope.split(":", 1)[0] if ":" in scope else None))
        except TypeError:
            return str(await new_conversation(scope))

    async def _append_assistant_history(
        self,
        scope: str,
        text: str,
    ) -> bool:
        scope = str(scope or "").strip()
        text = str(text or "").strip()
        if not scope or not text:
            return False
        context = getattr(self, "context", None)
        manager = getattr(context, "conversation_manager", None)
        if not manager:
            return False
        try:
            cid = await self._conversation_id_for_scope(scope)
            if not cid:
                return False
            conversation = await manager.get_conversation(scope, cid)
            history = self._conversation_history_list(conversation)
            history.append({"role": "assistant", "content": text})
            await manager.update_conversation(scope, cid, history=history)
            return True
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 写入对话历史失败：{exc}")
            return False

    async def _append_user_history(self, scope: str, event: Any, text: str) -> bool:
        scope = str(scope or "").strip()
        text = str(text or "").strip()
        if not scope or not text:
            return False
        context = getattr(self, "context", None)
        manager = getattr(context, "conversation_manager", None)
        if not manager:
            return False
        try:
            cid = await self._conversation_id_for_scope(scope)
            if not cid:
                return False
            conversation = await manager.get_conversation(scope, cid)
            history = self._conversation_history_list(conversation)
            user_item = await self._user_history_item_from_event(scope, event, text)
            if any(self._history_user_item_matches(item, user_item) for item in history[-6:]):
                return False
            history.append(user_item)
            await manager.update_conversation(scope, cid, history=history)
            return True
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 写入用户对话历史失败：{exc}")
            return False

    async def _append_platform_user_history(
        self,
        event: Any,
        text: str,
        *,
        media_label: str = "",
    ) -> bool:
        scope = self._event_session_id(event)
        platform_id, user_id = parse_unified_origin(scope)
        if not platform_id or not user_id:
            return False
        context = getattr(self, "context", None)
        manager = getattr(context, "message_history_manager", None)
        insert = getattr(manager, "insert", None)
        if not callable(insert):
            return False
        text = str(text or "").strip()
        message_parts = self._history_message_parts_for_user(text, media_label)
        for part in await self._history_media_parts_from_event(event):
            platform_part = await self._platform_media_part_from_history_payload(part)
            if platform_part:
                message_parts.append(platform_part)
        content = {
            "type": "user",
            "message": message_parts,
            "timestamp": int(time.time()),
            "source": self._OBSERVED_USER_HISTORY_MARKER,
        }
        if text:
            content["text"] = text
        try:
            await insert(
                platform_id=platform_id,
                user_id=user_id,
                content=content,
                sender_id=self._safe_event_call(event, "get_sender_id") or user_id,
                sender_name=self._safe_event_call(event, "get_sender_name") or "",
            )
            return True
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 写入用户平台消息历史失败：{exc}")
            return False

    def _recent_matching_user_index(self, history: list[dict], user_item: dict[str, Any]) -> int:
        start = max(0, len(history) - 6)
        for index in range(len(history) - 1, start - 1, -1):
            if self._history_user_item_matches(history[index], user_item):
                return index
        return -1

    def _recent_plain_user_index_for_media(self, history: list[dict], user_item: dict[str, Any]) -> int:
        expected_content = user_item.get("content")
        if not self._history_content_has_media(expected_content):
            return -1
        expected_text = self._history_primary_text(expected_content).strip()
        if not expected_text:
            return -1
        start = max(0, len(history) - 6)
        for index in range(len(history) - 1, start - 1, -1):
            item = history[index]
            if not isinstance(item, dict) or str(item.get("role") or "").strip() != "user":
                continue
            if self._history_content_has_media(item.get("content")):
                continue
            if self._history_primary_text(item.get("content")).strip() == expected_text:
                return index
        return -1

    async def _append_turn_history(
        self,
        scope: str,
        event: Any,
        user_text: str,
        assistant_text: str,
    ) -> bool:
        scope = str(scope or "").strip()
        user_text = str(user_text or "").strip()
        assistant_text = str(assistant_text or "").strip()
        if not scope or not assistant_text:
            return False
        context = getattr(self, "context", None)
        manager = getattr(context, "conversation_manager", None)
        if not manager:
            return False
        try:
            cid = await self._conversation_id_for_scope(scope)
            if not cid:
                return False
            conversation = await manager.get_conversation(scope, cid)
            history = self._conversation_history_list(conversation)
            trailing_assistant_index = (
                len(history) - 1
                if self._history_item_matches(history[-1] if history else None, "assistant", assistant_text)
                else -1
            )
            media_label_getter = getattr(self, "_event_user_history_media_label", None)
            media_label = media_label_getter(event) if callable(media_label_getter) else ""
            if user_text or media_label:
                user_item = await self._user_history_item_from_event(scope, event, user_text, media_label=media_label)
                if self._recent_matching_user_index(history, user_item) < 0:
                    enrich_index = self._recent_plain_user_index_for_media(history, user_item)
                    if enrich_index >= 0:
                        history[enrich_index] = user_item
                    elif trailing_assistant_index >= 0:
                        history.insert(trailing_assistant_index, user_item)
                    else:
                        history.append(user_item)
            if trailing_assistant_index < 0:
                history.append({"role": "assistant", "content": assistant_text})
            await manager.update_conversation(scope, cid, history=history)
            return True
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 写入本轮对话历史失败：{exc}")
            return False

    async def _append_platform_assistant_history(
        self,
        scope: str,
        text: str,
        *,
        sender_name: str = "bot",
    ) -> bool:
        scope = str(scope or "").strip()
        text = str(text or "").strip()
        if not scope or not text:
            return False
        context = getattr(self, "context", None)
        manager = getattr(context, "message_history_manager", None)
        insert = getattr(manager, "insert", None)
        if not callable(insert):
            return False
        platform_id, user_id = parse_unified_origin(scope)
        if not platform_id or not user_id:
            return False
        content = {
            "type": "assistant",
            "message": [{"type": "plain", "text": text}],
            "text": text,
            "timestamp": int(time.time()),
        }
        try:
            await insert(
                platform_id=platform_id,
                user_id=user_id,
                content=content,
                sender_id="assistant",
                sender_name=sender_name or "bot",
            )
            return True
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 写入平台消息历史失败：{exc}")
            return False

    async def _append_proactive_send_history(
        self,
        scope: str,
        text: str,
        *,
        sender_name: str = "bot",
    ) -> None:
        await self._append_assistant_history(scope, text)
        await self._append_platform_assistant_history(scope, text, sender_name=sender_name)
