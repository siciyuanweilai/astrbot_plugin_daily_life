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
            if any(self._history_item_matches(item, "user", text) for item in history[-6:]):
                return False
            history.append(self._user_history_item(scope, event, text))
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
        content = {
            "type": "user",
            "message": self._history_message_parts_for_user(text, media_label),
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
            user_already_recorded = user_text and any(
                self._history_item_matches(item, "user", user_text)
                for item in history[-6:]
            )
            if user_text and not user_already_recorded:
                user_item = self._user_history_item(scope, event, user_text)
                if trailing_assistant_index >= 0:
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
            "message": [{"type": "text", "text": text}],
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
