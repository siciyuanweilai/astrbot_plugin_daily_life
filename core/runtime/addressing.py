from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

try:
    from astrbot.api.message_components import At, Reply
except Exception:
    At = None
    Reply = None

from .markers import LOG_PREFIX


@dataclass(frozen=True)
class _GroupReplyTarget:
    mode: str = "none"
    message_id: str = ""
    user_id: str = ""
    reason: str = ""

    @property
    def has_action(self) -> bool:
        return self.mode != "none" and bool(self.message_id or self.user_id)


@dataclass(frozen=True)
class _GroupAddressingDecision:
    target: _GroupReplyTarget = field(default_factory=_GroupReplyTarget)

    @property
    def reply_message_id(self) -> str:
        return self.target.message_id if self.target.mode in {"quote_current", "quote_source"} else ""

    @property
    def at_user_id(self) -> str:
        return self.target.user_id if self.target.mode == "at_sender" else ""

    @property
    def reason(self) -> str:
        return self.target.reason


class ChatAddressingMixin:
    """为群聊回复补充结构化引用/艾特组件。"""

    _CHAT_ADDRESSING_LAST_LOG_ATTR = "_daily_life_group_addressing_last_log"

    @staticmethod
    def _addressing_component_kind(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("type") or item.get("kind") or "").strip().lower()
        explicit = str(getattr(item, "type", "") or getattr(item, "kind", "") or "").strip().lower()
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
        return bool(items) and all(cls._addressing_component_text(item).strip() for item in items)

    @staticmethod
    def _addressing_int_config(value: Any, default: int, *, minimum: int = 0) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(number, minimum)

    def _addressing_astrbot_send_config(self, event: Any) -> dict[str, Any]:
        context = getattr(self, "context", None)
        getter = getattr(context, "get_config", None)
        if callable(getter):
            try:
                config = getter(getattr(event, "unified_msg_origin", None))
            except TypeError:
                config = getter()
            if isinstance(config, dict):
                return config
        config = getattr(context, "config", None)
        return config if isinstance(config, dict) else {}

    def _addressing_should_preserve_t2i_result(self, event: Any, items: list[Any]) -> bool:
        if not self._addressing_chain_is_plain_text_only(items):
            return False
        config = self._addressing_astrbot_send_config(event)
        if not bool(config.get("t2i")):
            return False
        text = "".join(self._addressing_component_text(item) for item in items)
        threshold = self._addressing_int_config(config.get("t2i_word_threshold"), 150, minimum=50)
        if len(text) <= threshold:
            return False
        logger.debug(
            f"{LOG_PREFIX} 群聊寻址跳过：回复长度 {len(text)} 超过 AstrBot 文本转图像阈值 {threshold}。"
        )
        return True

    @classmethod
    def _addressing_has_component(cls, items: list[Any], target_kind: str) -> bool:
        return any(target_kind in cls._addressing_component_kind(item) for item in items)

    @staticmethod
    def _addressing_make_reply_component(message_id: str) -> Any:
        message_id = str(message_id or "").strip()
        if Reply is not None:
            return Reply(id=message_id)
        return {"type": "reply", "id": message_id}

    @staticmethod
    def _addressing_make_at_component(user_id: str) -> Any:
        user_id = str(user_id or "").strip()
        if At is not None:
            return At(qq=user_id)
        return {"type": "at", "qq": user_id}

    def _addressing_scope_is_group(self, target_scope: str = "", source_event: Any = None) -> bool:
        if source_event is not None:
            try:
                if self._event_is_group_message(source_event):
                    return True
            except Exception:
                pass
        return ":GroupMessage:" in str(target_scope or "")

    def _addressing_source_message_id(self, source_event: Any = None, source_message_id: str = "") -> str:
        message_id = str(source_message_id or "").strip()
        if message_id or source_event is None:
            return message_id
        try:
            return str(self._event_message_id(source_event) or "").strip()
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
            return self._event_session_id(source_event)
        except Exception:
            return ""

    def _addressing_sender_id(self, source_event: Any = None) -> str:
        if source_event is None:
            return ""
        try:
            return str(self._safe_event_call(source_event, "get_sender_id") or "").strip()
        except Exception:
            return ""

    def _addressing_event_has_quote(self, source_event: Any = None) -> bool:
        if source_event is None:
            return False
        try:
            return bool(self._event_has_quote(source_event))
        except Exception:
            return False

    def _addressing_current_structured_message(self, scope: str, message_id: str) -> Any | None:
        finder = getattr(self, "_structured_find_message", None)
        if not callable(finder):
            return None
        try:
            return finder(scope, message_id)
        except Exception:
            return None

    def _addressing_recent_group_is_ambiguous(self, scope: str, current_message: Any | None = None) -> bool:
        getter = getattr(self, "_structured_scope_messages", None)
        if not callable(getter):
            return False
        try:
            messages = list(getter(scope))[-5:]
        except Exception:
            return False
        sender_ids: list[str] = []
        for message in messages:
            if getattr(message, "is_bot", False):
                continue
            sender_id = str(getattr(message, "sender_id", "") or "").strip()
            if sender_id and sender_id not in sender_ids:
                sender_ids.append(sender_id)
        if len(sender_ids) >= 2:
            return True
        if current_message is None:
            return False
        return bool(getattr(current_message, "reply_to_id", "") and getattr(current_message, "talking_to_id", "") != "bot")

    def _addressing_event_is_directed_to_bot(self, source_event: Any = None, current_message: Any | None = None) -> bool:
        if current_message is not None and getattr(current_message, "talking_to_id", "") == "bot":
            return True
        if source_event is None:
            return False
        try:
            return bool(self._event_is_directed(source_event))
        except Exception:
            return False

    def _group_reply_target(
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
        current_message = self._addressing_current_structured_message(scope, message_id) if scope and message_id else None

        if str(source or "").strip() == "proactive":
            if message_id:
                return _GroupReplyTarget(mode="quote_source", message_id=message_id, reason="主动续话")
            return _GroupReplyTarget()

        if source_event is None:
            return _GroupReplyTarget()

        if self._addressing_event_has_quote(source_event) and message_id:
            return _GroupReplyTarget(mode="quote_current", message_id=message_id, reason="引用消息")

        directed = self._addressing_event_is_directed_to_bot(source_event, current_message)
        if directed and message_id and self._addressing_recent_group_is_ambiguous(scope, current_message):
            return _GroupReplyTarget(mode="quote_current", message_id=message_id, reason="群聊对象容易混淆")

        if directed and not message_id and self._addressing_recent_group_is_ambiguous(scope, current_message):
            sender_id = self._addressing_sender_id(source_event)
            if sender_id:
                return _GroupReplyTarget(mode="at_sender", user_id=sender_id, reason="无可引用消息")

        return _GroupReplyTarget()

    def _group_addressing_decision(
        self,
        *,
        target_scope: str = "",
        source_event: Any = None,
        source_message_id: str = "",
        source: str = "chat",
    ) -> _GroupAddressingDecision:
        target = self._group_reply_target(
            target_scope=target_scope,
            source_event=source_event,
            source_message_id=source_message_id,
            source=source,
        )
        if target.has_action:
            return _GroupAddressingDecision(target=target)
        return _GroupAddressingDecision()

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
        additions: list[Any] = []
        if decision.reply_message_id and not self._addressing_has_component(items, "reply"):
            additions.append(self._addressing_make_reply_component(decision.reply_message_id))
        elif decision.at_user_id and not self._addressing_has_component(items, "at"):
            additions.append(self._addressing_make_at_component(decision.at_user_id))
        if not additions:
            return False

        items[:0] = additions
        self._log_group_addressing_decision(decision)
        return True

    def apply_group_addressing_before_send(self, event: Any) -> bool:
        if event is None:
            return False
        pending_attr = getattr(self, "_CHAT_STYLE_PENDING_SEGMENTS_ATTR", "")
        if pending_attr and len(list(getattr(event, pending_attr, []) or [])) >= 2:
            return False
        result = getattr(event, "get_result", lambda: None)()
        if result is None:
            return False
        return self.decorate_group_addressing_chain(
            result,
            target_scope=self._event_session_id(event),
            source_event=event,
            segment_index=0,
            source="chat",
        )

    def _log_group_addressing_decision(self, decision: _GroupAddressingDecision) -> None:
        key = (decision.reply_message_id, decision.at_user_id, decision.reason)
        if getattr(self, self._CHAT_ADDRESSING_LAST_LOG_ATTR, None) == key:
            return
        setattr(self, self._CHAT_ADDRESSING_LAST_LOG_ATTR, key)
        logger.info(
            f"{LOG_PREFIX} 群聊寻址：引用={'是' if decision.reply_message_id else '否'}；"
            f"At={'是' if decision.at_user_id else '否'}；来源={decision.reason or '自然接话'}"
        )
