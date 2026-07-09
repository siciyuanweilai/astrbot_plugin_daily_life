from __future__ import annotations

import copy
from typing import Any

from astrbot.api import logger

from ..markers import LOG_PREFIX


class VoiceSwitchRecordMixin:
    @staticmethod
    def _voice_record_component(item: Any) -> bool:
        if isinstance(item, dict):
            kind = str(item.get("type") or item.get("kind") or "").strip().lower()
        else:
            kind = " ".join(
                str(value or "").strip().lower()
                for value in (
                    item.__class__.__name__,
                    getattr(item, "type", ""),
                    getattr(item, "kind", ""),
                )
            )
        return any(token in kind for token in ("record", "voice", "audio"))

    @staticmethod
    def _strip_voice_record_caption(item: Any) -> Any:
        if isinstance(item, dict):
            next_item = dict(item)
            next_item.pop("text", None)
            next_item.pop("content", None)
            return next_item
        next_item = copy.deepcopy(item)
        for attr in ("text", "content"):
            if hasattr(next_item, attr):
                try:
                    setattr(next_item, attr, None)
                except Exception:
                    pass
        return next_item

    def _replace_result_with_voice(self, event: Any, path: str) -> None:
        raw_chain = list(getattr(self._record_message_chain(path), "chain", []) or [])
        voice_items = [self._strip_voice_record_caption(item) for item in raw_chain if self._voice_record_component(item)]
        chain = voice_items or raw_chain
        setter = getattr(event, "set_result", None)
        chain_result = getattr(event, "chain_result", None)
        if callable(setter) and callable(chain_result):
            setter(chain_result(chain))
            return
        result = getattr(event, "get_result", lambda: None)()
        if result is not None:
            result.chain = chain

    def note_voice_switch_text_result(self, event: Any) -> bool:
        scope = self._voice_switch_scope_key(event)
        if not scope:
            return False
        self._prune_voice_switch_rounds()
        item = self._voice_switch_round_store().pop(scope, None)
        if not isinstance(item, dict) or item.get("used_voice"):
            return False
        self._mark_voice_switch_channel(event, "文字")
        return True

    @staticmethod
    def _voice_expression_log_title(channel: str, source: str) -> str:
        if source in {"主动回复", "闲时回复"}:
            return f"闲时回复表达裁定：{channel}"
        if source == "用户明确要求":
            return f"语音请求裁定：{channel}"
        return f"语音智能切换裁定：{channel}"

    def _log_voice_expression_decision(
        self,
        *,
        channel: str,
        source: str,
        reason: str,
        result: str,
    ) -> None:
        logger.info(
            f"{LOG_PREFIX} {self._voice_expression_log_title(channel, source)}；"
            f"结果：{result or '已裁定'}；原因：{reason or '未提供额外原因'}"
        )

    async def _note_voice_expression_decision(
        self,
        *,
        event: Any = None,
        scope: str = "",
        channel: str,
        source: str,
        reason: str,
        result: str,
        text: str = "",
        emotion: str = "",
        emotion_category: str = "",
        user_requested: bool = False,
        confidence: float = 1.0,
    ) -> None:
        if not (channel == "语音" and source == "普通聊天" and result == "已发送"):
            self._log_voice_expression_decision(
                channel=channel,
                source=source,
                reason=reason,
                result=result,
            )
