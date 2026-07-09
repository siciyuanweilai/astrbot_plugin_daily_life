from __future__ import annotations

from typing import Any

from astrbot.api import logger

try:
    from astrbot.core.pipeline.process_stage import follow_up as _astrbot_follow_up
except Exception:
    _astrbot_follow_up = None

from ..markers import LOG_PREFIX

VOICE_TOOL_NAME = "life_voice_generate"
EMOJI_TOOL_NAME = "life_emoji_send"
SILENT_TOOL_PREFACE_NAMES = frozenset({VOICE_TOOL_NAME, EMOJI_TOOL_NAME})


class SilentToolPrefaceMixin:
    """屏蔽指定媒体工具调用前的模型旁白。

    只覆盖会由插件自己完成表达的工具，避免模型先把
    "我要发语音/表情" 这类说明当成普通消息发出去。
    """

    @classmethod
    def _tool_names_from_llm_response(cls, llm_response: Any) -> set[str]:
        names = cls._coerce_tool_names(getattr(llm_response, "tools_call_name", None))
        if names:
            return names
        names = cls._coerce_tool_names(getattr(llm_response, "tool_calls", None))
        if names:
            return names
        return cls._coerce_tool_names(llm_response)

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
                text = str(getattr(comp, "text", "") or getattr(comp, "content", "") or "")
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
        runner = self._active_agent_runner(event)
        if runner is None or self._runner_is_done(runner):
            return False
        scope = self._silent_preface_scope_key(event)
        used_voice_tool = self._silent_preface_voice_tool_marked(scope)
        pending_silent_tool = self._runner_has_pending_silent_preface_tool(runner)
        if not (used_voice_tool or pending_silent_tool):
            return False
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        else:
            chain.clear()
        logger.debug(f"{LOG_PREFIX} 已隐藏工具调用前的文字占位，等待插件发送结果。")
        return True

    def _is_active_agent_intermediate_result(self, event: Any) -> bool:
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not chain:
            return False
        if not self._is_llm_result_object(result):
            return False
        runner = self._active_agent_runner(event)
        return runner is not None and not self._runner_is_done(runner)

    @classmethod
    def _runner_has_pending_silent_preface_tool(cls, runner: Any) -> bool:
        return bool(cls._runner_pending_tool_names(runner) & SILENT_TOOL_PREFACE_NAMES)

    @classmethod
    def _runner_pending_tool_names(cls, runner: Any) -> set[str]:
        names: set[str] = set()
        for source in cls._runner_tool_name_sources(runner):
            names.update(cls._coerce_tool_names(source))
        return names

    @staticmethod
    def _runner_tool_name_sources(runner: Any) -> list[Any]:
        sources = [
            getattr(runner, "tools_call_name", None),
            getattr(runner, "tool_calls_name", None),
            getattr(runner, "tool_calls", None),
            getattr(runner, "current_llm_response", None),
            getattr(runner, "last_llm_response", None),
            getattr(runner, "_current_llm_response", None),
            getattr(runner, "_last_llm_response", None),
        ]
        getter = getattr(runner, "get_final_llm_resp", None)
        if callable(getter):
            try:
                sources.append(getter())
            except Exception:
                pass
        return sources

    @classmethod
    def _coerce_tool_names(cls, value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            text = value.strip()
            return {text} if text else set()
        if isinstance(value, dict):
            return cls._coerce_tool_names(
                value.get("name")
                or value.get("tool_name")
                or value.get("function_name")
                or value.get("function")
                or value.get("tool_calls")
                or value.get("tools_call_name")
            )
        function = getattr(value, "function", None)
        if function is not None:
            names = cls._coerce_tool_names(getattr(function, "name", None))
            if names:
                return names
        direct = (
            getattr(value, "name", None)
            or getattr(value, "tool_name", None)
            or getattr(value, "function_name", None)
            or getattr(value, "tools_call_name", None)
            or getattr(value, "tool_calls", None)
        )
        if direct is not None and direct is not value:
            names = cls._coerce_tool_names(direct)
            if names:
                return names
        try:
            iterator = iter(value)
        except TypeError:
            return set()
        names: set[str] = set()
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
        runners = getattr(SilentToolPrefaceMixin._follow_up_module(), "_ACTIVE_AGENT_RUNNERS", None)
        if not isinstance(runners, dict):
            return None
        return runners.get(umo)

    def _silent_preface_scope_key(self, event: Any) -> str:
        getter = getattr(self, "_voice_switch_scope_key", None)
        if callable(getter):
            try:
                return str(getter(event) or "").strip()
            except Exception:
                pass
        return str(getattr(event, "unified_msg_origin", "") or "").strip()

    def _silent_preface_voice_tool_marked(self, scope: str) -> bool:
        if not scope:
            return False
        store = getattr(self, "_voice_switch_round_store", None)
        if not callable(store):
            return False
        item = store().get(scope)
        return isinstance(item, dict) and bool(item.get("used_voice_tool"))
