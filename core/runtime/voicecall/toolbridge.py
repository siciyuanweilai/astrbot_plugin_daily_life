"""把 AstrBot 函数工具桥接到实时语音通话。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any


VOICE_CALL_END_TOOL_NAME = "life_voice_call_end"
_RECURSIVE_TOOLS = frozenset({"life_voice_call_invite", VOICE_CALL_END_TOOL_NAME})

_VOICE_CALL_END_SCHEMA = {
    "type": "function",
    "name": VOICE_CALL_END_TOOL_NAME,
    "description": (
        "结束当前实时语音通话。仅在用户明确要求挂断，或已经说完自然告别并确认继续没有必要时调用；"
        "调用前先完成一句简短告别。不要因为短暂停顿、单句晚安或用户尚未回应而调用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "简短的结束原因，例如用户要求挂断或已经自然道别。",
            }
        },
        "additionalProperties": False,
    },
}


def _clean_schema(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"type": "object", "properties": {}}
    try:
        schema = json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    if schema.get("type") != "object":
        schema["type"] = "object"
    schema.setdefault("properties", {})
    return schema


def _chain_text(value: Any) -> str:
    """把 AstrBot/MCP 工具结果整理成通话模型可理解的文本。"""

    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bytes):
        return ""
    if isinstance(value, Mapping):
        for key in ("text", "message", "content", "output", "result"):
            if key in value:
                text = _chain_text(value[key])
                if text:
                    return text
        return json.dumps(dict(value), ensure_ascii=False, default=str)
    if isinstance(value, (list, tuple, set)):
        parts = [_chain_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    text = getattr(value, "text", None)
    if text is not None:
        return str(text).strip()
    chain = getattr(value, "chain", None)
    if chain is not None:
        return _chain_text(chain)
    content = getattr(value, "content", None)
    if content is not None:
        return _chain_text(content)
    return str(value).strip()


class VoiceCallToolEvent:
    """为语音来源通话提供最小的 AstrMessageEvent 兼容适配器。"""

    def __init__(self, invite: Any, runtime: Any):
        self.unified_msg_origin = str(getattr(invite, "scope", "") or "").strip()
        self.message_str = ""
        self.message_obj = type("VoiceMessage", (), {"message": []})()
        self.raw_message = ""
        self.role = "user"
        self.extra: dict[str, Any] = {}
        self._stopped = False
        self.context = getattr(runtime, "context", None)
        self._invite = invite
        self._runtime = runtime
        self._result: Any = None
        self._outbound: list[Any] = []

    def get_sender_id(self) -> str:
        return str(getattr(self._invite, "user_id", "") or "")

    def get_sender_name(self) -> str:
        return str(getattr(self._invite, "user_name", "用户") or "用户")

    def get_group_id(self) -> str:
        return str(getattr(self._invite, "group_id", "") or "")

    def get_group_name(self) -> str:
        return str(getattr(self._invite, "group_name", "") or "")

    def get_platform_name(self) -> str:
        return self.unified_msg_origin.split(":", 1)[0] or "voice_call"

    def get_message_type(self) -> str:
        return "GroupMessage" if self.get_group_id() else "FriendMessage"

    def get_session_id(self) -> str:
        return self.unified_msg_origin

    def get_self_id(self) -> str:
        return ""

    def get_self_name(self) -> str:
        return str(getattr(self._invite, "bot_name", "对方") or "对方")

    def get_extra(self, key: str, default: Any = None) -> Any:
        return getattr(self, "extra", {}).get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        if not hasattr(self, "extra"):
            self.extra = {}
        self.extra[key] = value

    def is_admin(self) -> bool:
        return False

    def stop_event(self) -> None:
        self._stopped = True

    def continue_event(self) -> None:
        self._stopped = False

    def get_result(self) -> Any:
        return self._result

    def set_result(self, result: Any) -> None:
        self._result = result

    async def send(self, message: Any, *args: Any, **kwargs: Any) -> Any:
        # 语音通话不能把工具的发送动作重复投递到平台；发送链改为作为工具结果返回给模型。
        self._outbound.append(message)
        return message

    async def send_with_session(self, message: Any, *args: Any, **kwargs: Any) -> Any:
        return await self.send(message, *args, **kwargs)

    @property
    def outbound(self) -> list[Any]:
        return list(self._outbound)


class VoiceCallToolBridge:
    """把当前可用的 AstrBot 插件/MCP 工具提供给一次通话。"""

    def __init__(self, runtime: Any, invite: Any, manager: Any = None):
        self.runtime = runtime
        self.invite = invite
        self.manager = manager or getattr(runtime, "voice_call", None)
        self._lock = asyncio.Lock()

    def _tool_manager(self) -> Any:
        context = getattr(self.runtime, "context", None)
        getter = getattr(context, "get_llm_tool_manager", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _allow_function_calls(self) -> bool:
        settings = getattr(
            getattr(self.runtime, "config", None),
            "realtime_voice_call",
            None,
        )
        return bool(getattr(settings, "allow_function_calls", False))

    def _tools(self) -> list[Any]:
        manager = self._tool_manager()
        if manager is None:
            return []
        try:
            tool_set = manager.get_full_tool_set()
            candidates = list(getattr(tool_set, "tools", []) or [])
        except Exception:
            candidates = list(getattr(manager, "func_list", []) or [])
        result: list[Any] = []
        seen: set[str] = set()
        for tool in candidates:
            name = str(getattr(tool, "name", "") or "").strip()
            if not name or name in _RECURSIVE_TOOLS or name in seen:
                continue
            if not bool(getattr(tool, "active", True)):
                continue
            seen.add(name)
            result.append(tool)
        return result

    def schemas(self) -> list[dict[str, Any]]:
        # 结束通话是网关控制能力，不属于外部插件/MCP 工具；它始终可用，
        # 这样关闭普通工具调用时，Bot 仍能正确收束实时连接。
        schemas = [dict(_VOICE_CALL_END_SCHEMA)]
        if not self._allow_function_calls():
            return schemas
        for tool in self._tools():
            name = str(getattr(tool, "name", "") or "").strip()
            description = str(getattr(tool, "description", "") or "").strip()
            # 火山实时接口使用扁平的 realtime/OpenAI 函数结构，不使用聊天接口的嵌套结构。
            schemas.append(
                {
                    "type": "function",
                    "name": name,
                    "description": description,
                    "parameters": _clean_schema(getattr(tool, "parameters", {})),
                }
            )
        return schemas

    def _find(self, name: str) -> Any:
        wanted = str(name or "").strip()
        return next((tool for tool in self._tools() if getattr(tool, "name", "") == wanted), None)

    @staticmethod
    def _result_text(results: list[Any], event: VoiceCallToolEvent) -> str:
        parts = [_chain_text(result) for result in results]
        parts.extend(_chain_text(item) for item in event.outbound)
        text = "\n".join(part for part in parts if part).strip()
        return text or "工具已执行，但没有返回文字结果。"

    async def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> str:
        async with self._lock:
            if str(name or "").strip() == VOICE_CALL_END_TOOL_NAME:
                args = dict(arguments or {}) if isinstance(arguments, Mapping) else {}
                reason = str(args.get("reason") or "Bot结束通话").strip()[:160]
                request = getattr(self.manager, "request_hangup", None)
                if callable(request) and request(self.invite, reason):
                    return "已请求结束当前实时通话。"
                return "当前实时通话连接尚未就绪，无法结束。"
            if not self._allow_function_calls():
                return "实时通话未启用工具调用。"
            tool = self._find(name)
            if tool is None:
                return f"未找到可用工具：{str(name or '').strip()[:80]}"
            try:
                args = dict(arguments or {})
            except (TypeError, ValueError):
                args = {}
            event = VoiceCallToolEvent(self.invite, self.runtime)
            try:
                from astrbot.core.agent.run_context import ContextWrapper
                from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

                # FunctionToolExecutor 只依赖这三个字段；轻量适配器同时兼容
                # AstrBot 强类型运行时上下文和测试/代理上下文。
                agent_context = SimpleNamespace(
                    context=getattr(self.runtime, "context", None),
                    event=event,
                    extra={},
                )
                settings = getattr(
                    getattr(self.runtime, "config", None),
                    "realtime_voice_call",
                    None,
                )
                timeout = max(1, int(getattr(settings, "tool_call_timeout_seconds", 60) or 60))
                run_context = ContextWrapper(
                    context=agent_context, tool_call_timeout=timeout
                )
                results: list[Any] = []

                async def execute() -> None:
                    async for result in FunctionToolExecutor.execute(
                        tool, run_context, **args
                    ):
                        results.append(result)

                await asyncio.wait_for(execute(), timeout=timeout)
                invite_count = int(getattr(self.invite, "tool_call_count", 0) or 0)
                self.invite.tool_call_count = invite_count + 1
                return self._result_text(results, event)
            except asyncio.TimeoutError:
                return f"工具执行超时（{timeout}秒），请稍后再试。"
            except Exception as exc:  # noqa: BLE001
                return f"工具执行失败：{type(exc).__name__}。"


__all__ = ["VoiceCallToolBridge", "VoiceCallToolEvent", "_chain_text"]
