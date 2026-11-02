from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger
from astrbot.api.message_components import At, Reply

from .markers import LOG_PREFIX


@dataclass(frozen=True)
class _GroupReplyTarget:
    mode: str = "none"
    message_id: str = ""
    user_id: str = ""
    reason: str = ""
    intervening_count: int = 0



@dataclass(frozen=True)
class _GroupAddressingDecision:
    target: _GroupReplyTarget = field(default_factory=_GroupReplyTarget)

    @property
    def reply_message_id(self) -> str:
        return (
            self.target.message_id
            if self.target.mode in {"quote_current", "quote_source"}
            else ""
        )

    @property
    def at_user_id(self) -> str:
        return self.target.user_id if self.target.mode == "at_sender" else ""

    @property
    def reason(self) -> str:
        return self.target.reason


class ChatAddressingService:
    """为群聊回复补充结构化引用/艾特组件。"""

    def __init__(
        self,
        *,
        is_group: Callable[[Any], bool],
        message_id: Callable[[Any], str],
        session_id: Callable[[Any], str],
        is_directed: Callable[[Any], bool],
        safe_event_call: Callable[..., Any],
        find_message: Callable[[str, str], Any],
        scope_messages: Callable[[str], list[Any]],
        extract_reply: Callable[[Any, str], dict[str, Any]],
        keep_default_send: Callable[[Any, str], bool],
        sequence_attr: str,
        pending_segments_attr: str,
    ) -> None:
        self._is_group = is_group
        self._message_id = message_id
        self._session_id = session_id
        self._is_directed = is_directed
        self._safe_event_call = safe_event_call
        self._find_message = find_message
        self._scope_messages = scope_messages
        self._extract_reply = extract_reply
        self._keep_default_send = keep_default_send
        self._sequence_attr = sequence_attr
        self._pending_segments_attr = pending_segments_attr
        self._last_logs: OrderedDict[str, tuple[Any, ...]] = OrderedDict()

    def evict_scopes(self, scopes: set[str]) -> None:
        for scope in scopes:
            self._last_logs.pop(scope, None)

    def clear(self) -> None:
        self._last_logs.clear()

    @staticmethod
    def _addressing_component_kind(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("type") or item.get("kind") or "").strip().lower()
        explicit = (
            str(getattr(item, "type", "") or getattr(item, "kind", "") or "")
            .strip()
            .lower()
        )
        return explicit or item.__class__.__name__.strip().lower()

    @staticmethod
    def _addressing_component_text(item: Any) -> str:
        if isinstance(item, str):
            return str(item)
        if isinstance(item, dict):
            kind = str(item.get("type") or item.get("kind") or "text").strip().lower()
            if kind in {"", "text", "plain"}:
                return str(item.get("text") or item.get("content") or "")
            return ""
        return str(getattr(item, "text", "") or getattr(item, "content", "") or "")

    @staticmethod
    def _addressing_chain_items(chain: Any) -> list[Any] | None:
        if isinstance(chain, list):
            return chain
        for attr in ("chain", "items"):
            items = getattr(chain, attr, None)
            if isinstance(items, list):
                return items
        return None

    @classmethod
    def _addressing_chain_is_plain_text_only(cls, items: list[Any]) -> bool:
        return bool(items) and all(
            cls._addressing_component_text(item).strip() for item in items
        )

    def _addressing_should_preserve_t2i_result(
        self, event: Any, items: list[Any]
    ) -> bool:
        if not self._addressing_chain_is_plain_text_only(items):
            return False
        text = "".join(self._addressing_component_text(item) for item in items)
        return bool(self._keep_default_send(event, text))

    @classmethod
    def _addressing_has_component(cls, items: list[Any], target_kind: str) -> bool:
        return any(
            target_kind in cls._addressing_component_kind(item) for item in items
        )

    @staticmethod
    def _addressing_make_reply_component(message_id: str) -> Any:
        message_id = str(message_id or "").strip()
        return Reply(id=message_id)

    @staticmethod
    def _addressing_make_at_component(user_id: str) -> Any:
        user_id = str(user_id or "").strip()
        return At(qq=user_id)

    def _addressing_scope_is_group(
        self, target_scope: str = "", source_event: Any = None
    ) -> bool:
        if source_event is not None:
            try:
                if self._is_group(source_event):
                    return True
            except Exception:
                pass
        return ":GroupMessage:" in str(target_scope or "")

    def _addressing_source_message_id(
        self, source_event: Any = None, source_message_id: str = ""
    ) -> str:
        message_id = str(source_message_id or "").strip()
        if message_id or source_event is None:
            return message_id
        try:
            return str(self._message_id(source_event) or "").strip()
        except Exception:
            return ""

    def _addressing_scope(
        self,
        target_scope: str = "",
        source_event: Any = None,
    ) -> str:
        scope = str(target_scope or "").strip()
        if scope or source_event is None:
            return scope
        try:
            return self._session_id(source_event)
        except Exception:
            return ""

    def _addressing_sender_id(self, source_event: Any = None) -> str:
        if source_event is None:
            return ""
        try:
            return str(
                self._safe_event_call(source_event, "get_sender_id") or ""
            ).strip()
        except Exception:
            return ""

    def _addressing_current_structured_message(
        self, scope: str, message_id: str
    ) -> Any | None:
        return self._find_message(scope, message_id)

    @staticmethod
    def _addressing_message_sequence(message: Any | None) -> int:
        try:
            return max(0, int(getattr(message, "sequence", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _addressing_source_sequence(
        self, source_event: Any = None, current_message: Any | None = None
    ) -> int:
        sequence = self._addressing_message_sequence(current_message)
        if sequence or source_event is None:
            return sequence
        try:
            return max(0, int(getattr(source_event, self._sequence_attr, 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _addressing_intervening_group_messages(
        self,
        scope: str,
        *,
        source_event: Any = None,
        current_message: Any | None = None,
    ) -> list[Any]:
        messages = list(self._scope_messages(scope))

        source_sequence = self._addressing_source_sequence(
            source_event, current_message
        )
        candidates: list[Any] = []
        if source_sequence > 0:
            candidates = [
                message
                for message in messages
                if self._addressing_message_sequence(message) > source_sequence
            ]
        elif current_message is not None:
            source_key = str(getattr(current_message, "key", "") or "").strip()
            source_index = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if message is current_message
                    or (source_key and str(getattr(message, "key", "") or "") == source_key)
                ),
                -1,
            )
            if source_index >= 0:
                candidates = messages[source_index + 1 :]

        source_sender_id = str(
            getattr(current_message, "sender_id", "") or ""
        ).strip() or self._addressing_sender_id(source_event)
        return [
            message
            for message in candidates
            if not bool(getattr(message, "is_bot", False))
            and (
                not source_sender_id
                or str(getattr(message, "sender_id", "") or "").strip()
                != source_sender_id
            )
        ]

    def _addressing_event_is_directed_to_bot(
        self, source_event: Any = None, current_message: Any | None = None
    ) -> bool:
        if (
            current_message is not None
            and getattr(current_message, "talking_to_id", "") == "bot"
        ):
            return True
        if source_event is None:
            return False
        try:
            return bool(self._is_directed(source_event))
        except Exception:
            return False

    def _addressing_explicit_old_bot_reply(
        self,
        scope: str,
        *,
        source_event: Any = None,
        current_message: Any | None = None,
    ) -> bool:
        """判断用户是否明确引用了较早的 Bot 消息。"""
        reply_id = str(
            getattr(current_message, "reply_to_id", "") if current_message else ""
        ).strip()
        if not reply_id and source_event is not None:
            reply_id = str(
                (self._extract_reply(source_event, scope) or {}).get("message_id") or ""
            ).strip()
        if not reply_id:
            return False

        target = self._addressing_current_structured_message(scope, reply_id)
        if target is None or not bool(getattr(target, "is_bot", False)):
            return False

        messages = list(self._scope_messages(scope))
        target_index = next(
            (
                index
                for index, message in enumerate(messages)
                if message is target or message.key == target.key
            ),
            -1,
        )
        current_index = next(
            (
                index
                for index, message in enumerate(messages)
                if current_message is not None
                and (message is current_message or message.key == current_message.key)
            ),
            len(messages),
        )
        if target_index < 0 or target_index >= current_index:
            return False
        return any(
            bool(getattr(message, "is_bot", False))
            for message in messages[target_index + 1 : current_index]
        )

    def group_reply_target(
        self,
        *,
        target_scope: str = "",
        source_event: Any = None,
        source_message_id: str = "",
        source: str = "chat",
    ) -> _GroupReplyTarget:
        if not self._addressing_scope_is_group(target_scope, source_event):
            return _GroupReplyTarget()

        scope = self._addressing_scope(target_scope, source_event)
        message_id = self._addressing_source_message_id(source_event, source_message_id)
        current_message = (
            self._addressing_current_structured_message(scope, message_id)
            if scope and message_id
            else None
        )

        if str(source or "").strip() == "proactive":
            if message_id:
                return _GroupReplyTarget(
                    mode="quote_source", message_id=message_id, reason="主动续话"
                )
            return _GroupReplyTarget()

        if source_event is None:
            return _GroupReplyTarget()

        directed = self._addressing_event_is_directed_to_bot(
            source_event, current_message
        )
        explicit_old_reply = self._addressing_explicit_old_bot_reply(
            scope,
            source_event=source_event,
            current_message=current_message,
        )
        intervening_messages = self._addressing_intervening_group_messages(
            scope,
            source_event=source_event,
            current_message=current_message,
        )
        intervening_count = len(intervening_messages)
        if directed and message_id and explicit_old_reply:
            return _GroupReplyTarget(
                mode="quote_current",
                message_id=message_id,
                reason="引用了较早的Bot消息",
                intervening_count=intervening_count,
            )
        if directed and message_id and intervening_count:
            return _GroupReplyTarget(
                mode="quote_current",
                message_id=message_id,
                reason="发送前出现插话",
                intervening_count=intervening_count,
            )

        if directed and not message_id and intervening_count:
            sender_id = self._addressing_sender_id(source_event)
            if sender_id:
                return _GroupReplyTarget(
                    mode="at_sender",
                    user_id=sender_id,
                    reason="发送前出现插话且无可引用消息",
                    intervening_count=intervening_count,
                )

        if directed:
            return _GroupReplyTarget(
                message_id=message_id,
                reason="直接回应",
                intervening_count=intervening_count,
            )

        return _GroupReplyTarget()

    def _group_addressing_decision(
        self,
        *,
        target_scope: str = "",
        source_event: Any = None,
        source_message_id: str = "",
        source: str = "chat",
    ) -> _GroupAddressingDecision:
        target = self.group_reply_target(
            target_scope=target_scope,
            source_event=source_event,
            source_message_id=source_message_id,
            source=source,
        )
        return _GroupAddressingDecision(target=target)

    def decorate_group_addressing_chain(
        self,
        chain: Any,
        *,
        target_scope: str = "",
        source_event: Any = None,
        source_message_id: str = "",
        segment_index: int = 0,
        source: str = "chat",
    ) -> bool:
        if int(segment_index or 0) != 0:
            return False
        items = self._addressing_chain_items(chain)
        if items is None or not self._addressing_chain_is_plain_text_only(items):
            return False
        if self._addressing_should_preserve_t2i_result(source_event, items):
            return False

        decision = self._group_addressing_decision(
            target_scope=target_scope,
            source_event=source_event,
            source_message_id=source_message_id,
            source=source,
        )
        if decision.reason:
            self._log_group_addressing_decision(
                self._addressing_scope(target_scope, source_event), decision
            )
        additions: list[Any] = []
        if decision.reply_message_id and not self._addressing_has_component(
            items, "reply"
        ):
            additions.append(
                self._addressing_make_reply_component(decision.reply_message_id)
            )
        elif decision.at_user_id and not self._addressing_has_component(items, "at"):
            additions.append(self._addressing_make_at_component(decision.at_user_id))
        if not additions:
            return False

        items[:0] = additions
        return True

    def apply_group_addressing_before_send(self, event: Any) -> bool:
        if event is None:
            return False
        pending_attr = self._pending_segments_attr
        if pending_attr and len(list(getattr(event, pending_attr, []) or [])) >= 2:
            return False
        result = getattr(event, "get_result", lambda: None)()
        if result is None:
            return False
        return self.decorate_group_addressing_chain(
            result,
            target_scope=self._session_id(event),
            source_event=event,
            segment_index=0,
            source="chat",
        )

    def _log_group_addressing_decision(
        self, scope: str, decision: _GroupAddressingDecision
    ) -> None:
        key = (
            decision.target.mode,
            decision.target.message_id,
            decision.at_user_id,
            decision.reason,
            decision.target.intervening_count,
        )
        if self._last_logs.get(scope) == key:
            return
        self._last_logs[scope] = key
        self._last_logs.move_to_end(scope)
        while len(self._last_logs) > 256:
            self._last_logs.popitem(last=False)
        logger.debug(
            f"{LOG_PREFIX} 群聊寻址：引用={'是' if decision.reply_message_id else '否'}；"
            f"提及={'是' if decision.at_user_id else '否'}；"
            f"来源={decision.reason or '自然接话'}；"
            f"插话数={decision.target.intervening_count}"
        )


__all__ = ["ChatAddressingService"]
