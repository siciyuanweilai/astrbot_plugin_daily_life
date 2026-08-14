from __future__ import annotations

from typing import Any

from astrbot.core.agent.tool import FunctionTool

from .delivery import BackgroundTextMode


class ExpressiveSendMessageTool(FunctionTool):
    """让当前会话的纯文本工具消息复用聊天表达发送管线。"""

    def __init__(self, wrapped: FunctionTool, runtime: Any) -> None:
        super().__init__(
            name=wrapped.name,
            description=wrapped.description,
            parameters=getattr(wrapped, "parameters", {}),
        )
        self._wrapped = wrapped
        self._runtime = runtime
        self.active = getattr(wrapped, "active", True)
        self.handler_module_path = getattr(wrapped, "handler_module_path", None)
        self.is_background_task = getattr(wrapped, "is_background_task", False)

    @staticmethod
    def _current_session_plain_text(
        context: Any, kwargs: dict[str, Any]
    ) -> tuple[Any, str, str] | None:
        try:
            event = context.context.event
        except AttributeError:
            return None
        scope = str(getattr(event, "unified_msg_origin", "") or "").strip()
        target = str(kwargs.get("session") or scope).strip()
        if not scope or target != scope:
            return None
        messages = kwargs.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        texts: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                return None
            if str(message.get("type") or "").strip().lower() != "plain":
                return None
            text = str(message.get("text") or "").strip()
            if not text:
                return None
            texts.append(text)
        return event, scope, "\n".join(texts)

    @staticmethod
    def _note_direct_send(event: Any, source_text: str) -> None:
        event._has_send_oper = True
        getter = getattr(event, "get_extra", None)
        setter = getattr(event, "set_extra", None)
        if not callable(setter):
            return
        sent_texts = (
            getter("_send_message_to_user_current_session_plain_texts", [])
            if callable(getter)
            else []
        )
        if not isinstance(sent_texts, list):
            sent_texts = []
        sent_texts.append(source_text)
        setter("_send_message_to_user_current_session_plain_texts", sent_texts)

    async def call(self, context: Any, **kwargs: Any) -> Any:
        candidate = self._current_session_plain_text(context, kwargs)
        if candidate is None:
            return await self._wrapped.call(context, **kwargs)
        event, scope, source_text = candidate
        duplicate_checker = getattr(
            self._runtime, "should_skip_duplicate_send_message", None
        )
        if callable(duplicate_checker) and duplicate_checker(event, source_text):
            self._note_direct_send(event, source_text)
            return f"Message sent to session {scope}"
        enabled = getattr(self._runtime, "_semantic_segment_enabled", None)
        structural = getattr(self._runtime, "_chat_style_text_is_structural", None)
        if not callable(enabled) or not enabled():
            return await self._wrapped.call(context, **kwargs)
        if callable(structural) and structural(source_text):
            return await self._wrapped.call(context, **kwargs)

        sender = getattr(self._runtime, "send_background_text", None)
        if not callable(sender):
            return await self._wrapped.call(context, **kwargs)
        sent = await sender(
            scope,
            source_text,
            mode=BackgroundTextMode.EXPRESSIVE,
            source_event=event,
            source="send_message_to_user",
            user_message=str(getattr(event, "message_str", "") or "").strip(),
        )
        if not sent:
            return await self._wrapped.call(context, **kwargs)
        self._note_direct_send(event, source_text)
        return f"Message sent to session {scope}"


def install_expressive_send_message_tool(toolset: Any, runtime: Any) -> bool:
    """只替换当前请求中的内置发送工具，不修改全局工具注册表。"""

    getter = getattr(toolset, "get_tool", None)
    adder = getattr(toolset, "add_tool", None)
    if not callable(getter) or not callable(adder):
        return False
    current = getter("send_message_to_user")
    if current is None or isinstance(current, ExpressiveSendMessageTool):
        return False
    adder(ExpressiveSendMessageTool(current, runtime))
    return True


__all__ = ["ExpressiveSendMessageTool", "install_expressive_send_message_tool"]
