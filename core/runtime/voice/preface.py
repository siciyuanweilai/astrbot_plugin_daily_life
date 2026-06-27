from __future__ import annotations

import sys
from typing import Any

from astrbot.api import logger

try:
    from astrbot.core.pipeline.process_stage import follow_up as _astrbot_follow_up
except Exception:
    _astrbot_follow_up = None

from ..markers import LOG_PREFIX


class VoiceSwitchPrefaceMixin:
    @staticmethod
    def _follow_up_module() -> Any:
        package = sys.modules.get(__package__)
        if package is not None and hasattr(package, "_astrbot_follow_up"):
            return getattr(package, "_astrbot_follow_up")
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

    @staticmethod
    def _looks_like_voice_tool_preface(text: str) -> bool:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return False
        voice_markers = ("语音", "说给你听", "录给你", "听我说", "用声音", "念给你听")
        return any(marker in normalized for marker in voice_markers)

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
        if not self._looks_like_voice_tool_preface(self._voice_switch_reply_text_from_event(event)):
            return False
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        else:
            chain.clear()
        logger.debug(f"{LOG_PREFIX} 已屏蔽语音工具调用前的中间回复，等待语音发送结果。")
        return True

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
        runners = getattr(VoiceSwitchPrefaceMixin._follow_up_module(), "_ACTIVE_AGENT_RUNNERS", None)
        if not isinstance(runners, dict):
            return None
        return runners.get(umo)
