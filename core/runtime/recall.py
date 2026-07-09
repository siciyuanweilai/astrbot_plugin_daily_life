from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from astrbot.api import logger

from .markers import LOG_PREFIX


class RecallMixin:
    """记录用户撤回，并在插件发送前做轻量拦截。"""

    _RECALL_NOTICE_TYPES = {"friend_recall", "group_recall"}
    _RECALL_TTL_SECONDS = 30 * 60

    def _recall_now(self) -> float:
        try:
            return asyncio.get_running_loop().time()
        except RuntimeError:
            return time.monotonic()

    def _recalled_message_store(self) -> dict[str, dict[str, float]]:
        store = getattr(self, "_recalled_messages", None)
        if not isinstance(store, dict):
            store = {}
            self._recalled_messages = store
        return store

    def _prune_recalled_messages(self) -> None:
        store = self._recalled_message_store()
        now = self._recall_now()
        for scope, items in list(store.items()):
            for message_id, expires_at in list(items.items()):
                if expires_at <= now:
                    items.pop(message_id, None)
            if not items:
                store.pop(scope, None)

    def _event_raw_payload(self, event: Any) -> dict[str, Any]:
        for source in self._event_sources(event):
            for current in (
                getattr(source, "raw_message", None),
                getattr(getattr(source, "message_obj", None), "raw_message", None),
            ):
                if isinstance(current, dict):
                    return current
        return {}

    def _event_is_recall_notice(self, event: Any) -> bool:
        raw = self._event_raw_payload(event)
        return (
            str(raw.get("post_type") or "").strip() == "notice"
            and str(raw.get("notice_type") or "").strip() in self._RECALL_NOTICE_TYPES
        )

    def note_recalled_message(self, event: Any) -> bool:
        if not self._event_is_recall_notice(event):
            return False
        raw = self._event_raw_payload(event)
        message_id = str(raw.get("message_id") or "").strip()
        if not message_id:
            return True
        scope = self._event_session_id(event)
        if not scope:
            return True
        self._prune_recalled_messages()
        self._recalled_message_store().setdefault(scope, {})[message_id] = (
            self._recall_now() + self._RECALL_TTL_SECONDS
        )
        self._remove_recalled_message_from_runtime_context(scope, message_id)
        logger.debug(f"{LOG_PREFIX} 已记录用户撤回消息：{scope}#{message_id}")
        return True

    def _remove_recalled_message_from_runtime_context(self, scope: str, message_id: str) -> None:
        self._remove_recalled_structured_message(scope, message_id)
        self._remove_recalled_proactive_candidate(scope, message_id)
        remover = getattr(self, "remove_recalled_sight_context", None)
        if callable(remover):
            remover(scope, message_id)

    def _remove_recalled_structured_message(self, scope: str, message_id: str) -> None:
        store = getattr(self, "_structured_messages", None)
        if not isinstance(store, dict):
            return
        messages = store.get(scope)
        if not messages:
            return
        kept = [
            item
            for item in list(messages)
            if message_id not in {getattr(item, "message_id", ""), getattr(item, "fallback_key", ""), getattr(item, "key", "")}
        ]
        store[scope] = deque(kept, maxlen=getattr(messages, "maxlen", None))

    def _remove_recalled_proactive_candidate(self, scope: str, message_id: str) -> None:
        candidates = getattr(self, "_proactive_idle_candidates", None)
        if not isinstance(candidates, dict):
            return
        for key, candidate in list(candidates.items()):
            if not isinstance(candidate, dict):
                continue
            same_target = str(candidate.get("target_scope") or "").strip() == scope
            same_message = str(candidate.get("message_id") or "").strip() == message_id
            recent_messages = [
                item
                for item in list(candidate.get("recent_messages") or [])
                if str(item.get("message_id") or "").strip() != message_id
            ]
            if same_target and same_message:
                candidates.pop(key, None)
                continue
            if len(recent_messages) != len(list(candidate.get("recent_messages") or [])):
                candidate["recent_messages"] = recent_messages
                candidate["pending_count"] = len(recent_messages)

    def _message_was_recalled(self, scope: str, message_id: str) -> bool:
        scope = str(scope or "").strip()
        message_id = str(message_id or "").strip()
        if not scope or not message_id:
            return False
        self._prune_recalled_messages()
        return message_id in self._recalled_message_store().get(scope, {})

    def _event_message_was_recalled(self, event: Any) -> bool:
        return self._message_was_recalled(self._event_session_id(event), self._event_message_id(event))

    def _recalled_skip_log_store(self) -> set[str]:
        store = getattr(self, "_recalled_skip_logged", None)
        if not isinstance(store, set):
            store = set()
            self._recalled_skip_logged = store
        return store

    def log_recalled_history_skip(self, event: Any) -> None:
        scope = self._event_session_id(event)
        message_id = self._event_message_id(event)
        key = f"{scope}:{message_id}" if scope and message_id else f"event:{id(event)}"
        logged = self._recalled_skip_log_store()
        if key in logged:
            return
        logged.add(key)
        logger.debug(f"{LOG_PREFIX} 原消息已撤回，已跳过本轮回复与历史沉淀。")

    def event_was_recalled(self, event: Any, *, log_skip: bool = False) -> bool:
        recalled = self._event_message_was_recalled(event)
        if recalled and log_skip:
            self.log_recalled_history_skip(event)
        return recalled

    def _send_source_was_recalled(
        self,
        scope: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        message_id = str(source_message_id or "").strip()
        if source_event is not None:
            event_scope = self._event_session_id(source_event) or scope
            message_id = message_id or self._event_message_id(source_event)
            return self._message_was_recalled(event_scope, message_id)
        return self._message_was_recalled(scope, message_id)

    def source_was_recalled(
        self,
        scope: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        return self._send_source_was_recalled(
            scope,
            source_event=source_event,
            source_message_id=source_message_id,
        )

    def can_send_for_source(
        self,
        scope: str,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        if self._send_source_was_recalled(
            scope,
            source_event=source_event,
            source_message_id=source_message_id,
        ):
            logger.debug(f"{LOG_PREFIX} 原消息已撤回，取消本次发送。")
            return False
        return True

    async def send_message_if_not_recalled(
        self,
        scope: str,
        chain: Any,
        *,
        source_event: Any = None,
        source_message_id: str = "",
    ) -> bool:
        if not self.can_send_for_source(scope, source_event=source_event, source_message_id=source_message_id):
            return False
        await self.context.send_message(scope, chain)
        return True

    def suppress_recalled_event_result(self, event: Any) -> bool:
        if not self._event_message_was_recalled(event):
            return False
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        else:
            result = getattr(event, "get_result", lambda: None)()
            chain = getattr(result, "chain", None)
            if isinstance(chain, list):
                chain.clear()
        logger.debug(f"{LOG_PREFIX} 原消息已撤回，已取消本轮回复发送。")
        return True

    def stop_recalled_event_before_history(self, event: Any) -> bool:
        if not self._event_message_was_recalled(event):
            return False
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()
        self.log_recalled_history_skip(event)
        return True
