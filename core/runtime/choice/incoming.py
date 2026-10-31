from __future__ import annotations

from typing import Any

from typing import Protocol


class ResponseGateEventComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _event_component_kind: Any
    _event_group_meta: Any
    _event_has_command_handler: Any
    _event_has_quote: Any
    _event_is_directed: Any
    _event_is_group_message: Any
    _event_is_platform_directed: Any
    _event_mentions_bot_alias: Any
    _event_message_items: Any
    _event_session_id: Any
    _proactive_is_self_message: Any
    _response_gate_has_media: Any
    _response_gate_visible_text: Any
    _safe_event_call: Any
    _structured_message_for_event: Any
    _structured_talking_to_bot: Any
    _structured_talking_to_other_user: Any
    continuous_turn_messages: Any


class ResponseGateEventComponent:
    def __init__(self, context: ResponseGateEventComponentContext) -> None:
        self.context = context

    def _response_gate_should_skip(self, event: Any) -> bool:
        is_stopped = getattr(event, "is_stopped", None)
        if callable(is_stopped) and is_stopped():
            return True
        if bool(getattr(event, "_has_send_oper", False)) and not getattr(event, "_daily_life_bili_natural", False):
            return True
        if self.context._event_has_command_handler(event):
            return True
        if getattr(self.context, "_proactive_is_self_message", lambda _: False)(event):
            return True
        return False

    def _response_gate_force_reply_reason(self, event: Any) -> str:
        if self.context._event_has_quote(event):
            if self.context._event_is_group_message(event):
                structured_getter = getattr(self.context, "_structured_message_for_event", None)
                structured = (
                    structured_getter(event) if callable(structured_getter) else None
                )
                talks_to_bot = getattr(
                    self.context, "_structured_talking_to_bot", lambda _: False
                )
                talks_to_other = getattr(
                    self.context, "_structured_talking_to_other_user", lambda _: False
                )
                if callable(talks_to_bot) and talks_to_bot(structured):
                    return "群聊里引用/回复了我的消息"
                if callable(talks_to_other) and talks_to_other(structured):
                    return ""
            return "消息带有引用/回复链，优先交给普通聊天处理"
        if self.context._event_is_group_message(event) and self.context._event_is_directed(event):
            return "群聊里收到 @、唤醒或明确指向我的消息"
        if not self.context._event_is_group_message(event):
            return ""
        if self.context._response_gate_has_media(event) and self.context._response_gate_visible_text(
            event
        ):
            return "群聊里带图文内容，适合交给普通聊天理解"
        return ""

    def mark_alias_directed_event_as_wake(self, event: Any) -> bool:
        if not self.context._event_is_group_message(event):
            return False
        if self.context._event_is_platform_directed(
            event
        ) or not self.context._event_mentions_bot_alias(event):
            return False
        is_stopped = getattr(event, "is_stopped", None)
        if callable(is_stopped) and is_stopped():
            return False
        setattr(event, "is_at_or_wake_command", True)
        setter = getattr(event, "should_call_llm", None)
        if callable(setter):
            setter(False)
        else:
            setattr(event, "call_llm", False)
        return True

    def _response_gate_scope_key(self, event: Any) -> str:
        group_id, _ = self.context._event_group_meta(event)
        return (
            group_id
            or self.context._event_session_id(event)
            or self.context._safe_event_call(event, "get_sender_id")
        )

    def _response_gate_visible_text(self, event: Any) -> str:
        turn_getter = getattr(self.context, "continuous_turn_messages", None)
        if callable(turn_getter):
            turn_messages = turn_getter(event)
            if turn_messages:
                return "\n".join(turn_messages)
        return str(getattr(event, "message_str", "") or "").strip()

    def _response_gate_has_media(self, event: Any) -> bool:
        for item in self.context._event_message_items(event):
            kind = self.context._event_component_kind(item)
            if any(
                token in kind for token in ("image", "record", "voice", "video", "file")
            ):
                return True
        return False

    @staticmethod
    def _suppress_default_llm(event: Any) -> None:
        setter = getattr(event, "should_call_llm", None)
        if callable(setter):
            setter(True)
            return
        setattr(event, "call_llm", True)
