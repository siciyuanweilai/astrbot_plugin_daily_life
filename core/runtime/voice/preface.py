from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain

from ..delivery import BackgroundTextMode

try:
    from astrbot.core.pipeline.process_stage import follow_up as _astrbot_follow_up
except Exception:
    _astrbot_follow_up = None

from ..markers import LOG_PREFIX

VOICE_TOOL_NAME = "life_voice_generate"
EMOJI_TOOL_NAME = "life_emoji_send"
SEND_MESSAGE_TOOL_NAME = "send_message_to_user"


@dataclass(frozen=True, slots=True)
class ToolReplyPolicy:
    preface_silent: bool = False
    replaces_text: bool = False
    post_send_comment: bool = True


TOOL_REPLY_POLICIES = {
    VOICE_TOOL_NAME: ToolReplyPolicy(
        preface_silent=True,
        replaces_text=True,
        post_send_comment=False,
    ),
    EMOJI_TOOL_NAME: ToolReplyPolicy(preface_silent=True),
    "life_image_generate": ToolReplyPolicy(),
    "life_photo_suite_generate": ToolReplyPolicy(),
    "edit_life_image": ToolReplyPolicy(),
    "life_video_generate": ToolReplyPolicy(post_send_comment=False),
}
DEFAULT_TOOL_REPLY_POLICY = ToolReplyPolicy()
SILENT_TOOL_PREFACE_NAMES = frozenset(
    name for name, policy in TOOL_REPLY_POLICIES.items() if policy.preface_silent
)


def tool_reply_policy(tool_name: str) -> ToolReplyPolicy:
    return TOOL_REPLY_POLICIES.get(
        str(tool_name or "").strip(), DEFAULT_TOOL_REPLY_POLICY
    )


class SilentToolPrefaceMixin:
    """屏蔽指定媒体工具调用前的模型旁白。

    只覆盖会由插件自己完成表达的工具，避免模型先把
    "我要发语音/表情" 这类说明当成普通消息发出去。
    """

    def _tool_preface_scope_key(self, event: Any) -> str:
        getter = getattr(self, "_event_session_id", None)
        if callable(getter):
            try:
                scope = str(getter(event) or "").strip()
                if scope:
                    return scope
            except Exception:
                pass
        return str(getattr(event, "unified_msg_origin", "") or "").strip()

    def _tool_preface_event_key(self, event: Any) -> str:
        """为工具状态绑定当前消息，避免同一会话的旧状态影响下一轮。"""

        message_getter = getattr(self, "_event_message_id", None)
        if callable(message_getter):
            try:
                message_id = str(message_getter(event) or "").strip()
            except Exception:
                message_id = ""
            if message_id:
                return f"message:{message_id}"
        return f"event:{id(event)}"

    def _tool_reply_state_for_event(self, event: Any) -> dict[str, Any] | None:
        """读取当前事件的工具状态，并清理属于上一轮的残留状态。"""

        scope = self._tool_preface_scope_key(event)
        if not scope:
            return None
        state = self._tool_reply_round_store().get(scope)
        if not isinstance(state, dict):
            return None
        expected_key = self._tool_preface_event_key(event)
        if state.get("event_key") != expected_key:
            self._tool_reply_round_store().pop(scope, None)
            logger.debug(f"{LOG_PREFIX} 已清理上一话轮残留的媒体工具状态。")
            return None
        return state

    def _tool_reply_round_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_tool_reply_rounds", None)
        if not isinstance(store, dict):
            store = {}
            self._tool_reply_rounds = store
        return store

    @classmethod
    def _tool_name(cls, tool: Any) -> str:
        names = cls._coerce_tool_names(tool)
        return next(iter(names), "")

    @staticmethod
    def _copy_result_chain(result: Any) -> Any:
        raw_chain = list(getattr(result, "chain", None) or [])
        derive = getattr(result, "derive", None)
        if callable(derive):
            try:
                return derive(raw_chain)
            except Exception:
                pass
        chain = MessageChain()
        target = getattr(chain, "chain", None)
        if isinstance(target, list):
            target.extend(raw_chain)
        return chain

    @staticmethod
    def _normalize_visible_text(value: Any) -> str:
        """统一可见文本的空白，便于同一工具轮次做完整内容比较。"""

        return " ".join(str(value or "").split()).strip()

    @classmethod
    def _same_visible_text(cls, left: Any, right: Any) -> bool:
        first = cls._normalize_visible_text(left)
        second = cls._normalize_visible_text(right)
        return bool(first) and first == second

    @staticmethod
    def _plain_send_message_tool_text(event: Any, tool_args: Any) -> str:
        """读取当前会话发送工具中的纯文本，不处理跨会话或媒体消息。"""

        if not isinstance(tool_args, dict):
            return ""
        scope = str(getattr(event, "unified_msg_origin", "") or "").strip()
        target = str(tool_args.get("session") or scope).strip()
        if not scope or target != scope:
            return ""
        messages = tool_args.get("messages")
        if not isinstance(messages, (list, tuple)) or not messages:
            return ""
        texts: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                return ""
            if str(message.get("type") or "").strip().lower() != "plain":
                return ""
            text = str(message.get("text") or "").strip()
            if not text:
                return ""
            texts.append(text)
        return "\n".join(texts)

    def _remember_tool_preface(self, event: Any, result: Any) -> bool:
        scope = self._tool_preface_scope_key(event)
        chain = getattr(result, "chain", None)
        if not scope or not isinstance(chain, list) or not chain:
            return False
        copied = self._copy_result_chain(result)
        self._tool_reply_round_store()[scope] = {
            "event_key": self._tool_preface_event_key(event),
            "preface": copied,
            "preface_text": self._plain_tool_preface_text(copied),
            "tool_name": "",
            "outcome": "",
            "preface_sent": False,
            "preface_suppressed": False,
            "tool_completed": False,
            "final_response_pending": False,
        }
        return True

    @staticmethod
    def _plain_tool_preface_text(chain: Any) -> str:
        parts = getattr(chain, "chain", None)
        if not isinstance(parts, list) or not parts:
            return ""
        texts: list[str] = []
        for part in parts:
            if isinstance(part, str):
                text = str(part)
            elif isinstance(part, Plain):
                text = str(getattr(part, "text", "") or "")
            elif isinstance(part, dict):
                part_type = str(part.get("type") or "").strip().lower()
                if part_type not in {"", "text", "plain"}:
                    return ""
                data = part.get("data")
                if isinstance(data, dict):
                    text = str(data.get("text") or data.get("content") or "")
                else:
                    text = str(part.get("text") or part.get("content") or "")
            else:
                return ""
            text = text.strip()
            if text:
                texts.append(text)
        return " ".join(texts).strip()

    async def _send_visible_tool_preface(
        self, scope: str, chain: Any, event: Any
    ) -> tuple[bool, str]:
        text = self._plain_tool_preface_text(chain)
        expressed_sender = getattr(self, "send_background_text", None)
        if text and callable(expressed_sender):
            try:
                sent = await expressed_sender(
                    scope,
                    text,
                    mode=BackgroundTextMode.EXPRESSIVE,
                    source_event=event,
                    source="tool_preface",
                )
            except Exception as exc:
                logger.warning(f"{LOG_PREFIX} 工具调用前回复聊天表达发送失败：{exc}")
                return False, "聊天表达"
            return bool(sent), "聊天表达"

        sender = getattr(self, "send_message_if_not_recalled", None)
        if callable(sender):
            sent = await sender(scope, chain, source_event=event)
            return bool(sent), "原始消息链"
        event_sender = getattr(event, "send", None)
        if callable(event_sender):
            await event_sender(chain)
            return True, "原始消息链"
        return False, "原始消息链"

    async def handle_llm_tool_start(
        self, event: Any, tool: Any, tool_args: dict[str, Any] | None = None
    ) -> None:
        """在 AstrBot 确认工具后释放或丢弃已经暂存的中间回复。"""

        scope = self._tool_preface_scope_key(event)
        if not scope:
            return
        state = self._tool_reply_state_for_event(event)
        if state is None:
            return
        name = self._tool_name(tool)
        policy = tool_reply_policy(name)
        state["tool_name"] = name
        state["reply_policy"] = policy
        state["tool_completed"] = False
        chain = state.pop("preface", None)
        preface_text = str(state.get("preface_text") or "").strip()
        if (
            name == SEND_MESSAGE_TOOL_NAME
            and chain is not None
            and self._same_visible_text(
                preface_text,
                self._plain_send_message_tool_text(event, tool_args),
            )
        ):
            state["preface_suppressed"] = True
            state["preface_duplicate_owned_by_tool"] = True
            logger.debug(f"{LOG_PREFIX} 工具前置回复与发送工具内容一致，跳过重复发送。")
            return
        if policy.preface_silent:
            state["preface_suppressed"] = True
            logger.debug(f"{LOG_PREFIX} 工具调用前回复已静默：{name}")
            return
        if chain is None:
            return
        sent, channel = await self._send_visible_tool_preface(scope, chain, event)
        state["preface_sent"] = bool(sent)
        state["preface_channel"] = channel
        if sent:
            if channel == "原始消息链":
                noter = getattr(self, "note_structured_bot_message", None)
                if callable(noter):
                    noter(
                        scope,
                        getattr(chain, "get_plain_text", lambda: "")(),
                        source_event=event,
                    )
            logger.debug(
                f"{LOG_PREFIX} 工具调用前回复已发送：{name or '未知工具'}；通道={channel}"
            )

    def should_skip_duplicate_send_message(
        self, event: Any, source_text: str
    ) -> bool:
        """发送工具已覆盖同内容前置回复时，阻止第二次投递。"""

        state = self._tool_reply_state_for_event(event)
        if state is None or not state.get("preface_sent"):
            return False
        if not self._same_visible_text(state.get("preface_text"), source_text):
            return False
        state["direct_send_suppressed"] = True
        logger.debug(f"{LOG_PREFIX} 发送工具内容已由工具前置回复发送，跳过重复投递。")
        return True

    async def handle_llm_tool_respond(
        self,
        event: Any,
        tool: Any,
        tool_args: dict[str, Any] | None = None,
        tool_result: Any = None,
    ) -> None:
        """结束工具阶段，保留结果状态供最终文字回复判断。"""

        del tool_args, tool_result
        state = self._tool_reply_state_for_event(event)
        if state is None:
            return
        state["tool_name"] = state.get("tool_name") or self._tool_name(tool)
        state["tool_completed"] = True
        state["final_response_pending"] = True

    def mark_tool_outcome(self, event: Any, tool_name: str, outcome: str) -> None:
        scope = self._tool_preface_scope_key(event)
        if not scope:
            return
        state = self._tool_reply_round_store().setdefault(
            scope,
            {"event_key": self._tool_preface_event_key(event)},
        )
        if state.get("event_key") != self._tool_preface_event_key(event):
            state.clear()
            state["event_key"] = self._tool_preface_event_key(event)
        state["tool_name"] = str(tool_name or "").strip()
        state["outcome"] = str(outcome or "").strip().lower()
        state["final_response_pending"] = True

    def note_tool_final_response(self, event: Any, llm_response: Any) -> None:
        scope = self._tool_preface_scope_key(event)
        state = self._tool_reply_state_for_event(event)
        if state is None:
            return
        completion = str(getattr(llm_response, "completion_text", "") or "").strip()
        result_chain = getattr(llm_response, "result_chain", None)
        chain_items = getattr(result_chain, "chain", result_chain)
        if not completion and not chain_items:
            self._tool_reply_round_store().pop(scope, None)
            return
        state["final_response_pending"] = True

    def suppress_final_silent_tool_result(self, event: Any) -> bool:
        """语音成功后清除重复文字，表情成功后保留自然补话。"""

        scope = self._tool_preface_scope_key(event)
        state = self._tool_reply_state_for_event(event)
        if state is None:
            return False
        name = str(state.get("tool_name") or "").strip()
        outcome = str(state.get("outcome") or "").strip().lower()
        policy = tool_reply_policy(name)
        if not policy.preface_silent or outcome != "sent":
            if state.get("final_response_pending"):
                state["final_response_pending"] = False
                self._tool_reply_round_store().pop(scope, None)
            return False
        if not policy.replaces_text:
            self._tool_reply_round_store().pop(scope, None)
            logger.debug(f"{LOG_PREFIX} 媒体已发送，保留后续文字回复：{name}")
            return False
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        else:
            result = getattr(event, "get_result", lambda: None)()
            chain = getattr(result, "chain", None)
            if isinstance(chain, list):
                chain.clear()
        self._tool_reply_round_store().pop(scope, None)
        logger.debug(f"{LOG_PREFIX} 语音已发送，已隐藏重复文字回复")
        return True

    @staticmethod
    def _follow_up_module() -> Any:
        return _astrbot_follow_up

    @staticmethod
    def _voice_switch_reply_text_from_event(event: Any) -> str:
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list):
            return ""
        texts: list[str] = []
        for comp in chain:
            if isinstance(comp, str):
                text = comp
            elif isinstance(comp, dict):
                if comp.get("type") not in {None, "text", "plain"}:
                    continue
                text = str(comp.get("text") or comp.get("content") or "")
            else:
                text = str(
                    getattr(comp, "text", "") or getattr(comp, "content", "") or ""
                )
            text = text.strip()
            if text:
                texts.append(text)
        return "\n".join(texts).strip()

    def suppress_intermediate_tool_result(self, event: Any) -> bool:
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not chain:
            return False
        if not self._is_llm_result_object(result):
            return False
        if not self._is_active_agent_intermediate_result(event):
            return False
        self._remember_tool_preface(event, result)
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        else:
            chain.clear()
        logger.debug(f"{LOG_PREFIX} 工具调用前回复已暂存，等待工具类型确认。")
        return True

    def _is_active_agent_intermediate_result(self, event: Any) -> bool:
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not chain:
            return False
        if not self._is_llm_result_object(result):
            return False
        # 运行时还有图片通道的同名辅助方法；这里必须固定调用工具前置
        # 所属实现，避免 MRO 将“当前图片生成任务”误当成 Agent runner。
        runner = SilentToolPrefaceMixin._active_agent_runner(event)
        return runner is not None and not self._runner_is_done(runner)

    @classmethod
    def _coerce_tool_names(cls, value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            text = value.strip()
            return {text} if text else set()
        if isinstance(value, dict):
            names: set[str] = set()
            for key in (
                "name",
                "tool_name",
                "function_name",
                "function",
                "tool_calls",
                "tools_call_name",
                "message",
                "choices",
                "completion",
                "raw_response",
                "response",
            ):
                names.update(cls._coerce_tool_names(value.get(key)))
            return names
        function = getattr(value, "function", None)
        if function is not None:
            names = cls._coerce_tool_names(getattr(function, "name", None))
            if names:
                return names
        names: set[str] = set()
        for attr in (
            "name",
            "tool_name",
            "function_name",
            "tools_call_name",
            "tool_calls",
            "message",
            "choices",
            "completion",
            "raw_response",
            "response",
        ):
            direct = getattr(value, attr, None)
            if direct is not None and direct is not value:
                names.update(cls._coerce_tool_names(direct))
        if names:
            return names
        try:
            iterator = iter(value)
        except TypeError:
            return set()
        names = set()
        for item in iterator:
            names.update(cls._coerce_tool_names(item))
        return names

    @staticmethod
    def _is_llm_result_object(result: Any) -> bool:
        checker = getattr(result, "is_llm_result", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                pass
        return str(getattr(result, "result_content_type", "")).endswith("LLM_RESULT")

    @staticmethod
    def _runner_is_done(runner: Any) -> bool:
        checker = getattr(runner, "done", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return False

    @staticmethod
    def _active_agent_runner(event: Any) -> Any | None:
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not umo:
            return None
        runners = getattr(
            SilentToolPrefaceMixin._follow_up_module(), "_ACTIVE_AGENT_RUNNERS", None
        )
        if not isinstance(runners, dict):
            return None
        return runners.get(umo)
