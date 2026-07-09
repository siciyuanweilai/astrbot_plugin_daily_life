from __future__ import annotations

import datetime
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ..markers import LOG_PREFIX


class ResponseGateApplyMixin:
    async def apply_response_gate_for_event(self, event: Any) -> dict[str, Any]:
        decision = await self.evaluate_response_gate(event)
        if decision.get("action") == "observe":
            await self.record_observed_private_user_message(event)
            self._suppress_default_llm(event)
            logger.debug(f"{LOG_PREFIX} 随心回复：观察不回复；{decision.get('reason') or '当前不适合回复'}")
        elif decision.get("action") == "wait":
            await self.record_observed_private_user_message(event)
            self._suppress_default_llm(event)
            logger.debug(f"{LOG_PREFIX} 随心回复：等待；{decision.get('reason') or '先把发言权留给对方'}")
        return decision

    async def evaluate_response_gate(
        self,
        event: Any,
        *,
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        config = getattr(self.config, "response_gate", None)
        if not config or not config.enabled:
            return self._response_gate_reply("未启用随心回复")
        if self._response_gate_should_skip(event):
            return self._response_gate_reply("命令、插件处理、自身消息或已停止事件不进入门控")

        now = now or life_now()
        key = self._response_gate_scope_key(event)
        if not key:
            return self._response_gate_reply("没有可用会话标识")

        forced_reason = self._response_gate_force_reply_reason(event)
        if forced_reason:
            self._response_gate_record_reply(key, now)
            return self._response_gate_reply(forced_reason, forced=True)

        pending_count = self._response_gate_record_seen(key, now)
        continuation_reason = self._response_gate_continuation_reason(key, now)
        if continuation_reason:
            self._response_gate_record_reply(key, now)
            return self._response_gate_reply(continuation_reason, forced=True)

        if self._response_gate_in_backoff(key, now, pending_count):
            return {"action": "observe", "reason": "连续不回复后的短暂安静观察仍在生效"}

        state = await self._response_gate_current_state(now)
        score, reasons = self._response_gate_score(event, state, pending_count, now)
        score += await self._response_gate_relationship_delta(event, now, reasons)
        score += await self._response_gate_feedback_delta(key, reasons)
        score += await self._response_gate_experience_delta(key, event, reasons)
        score = max(0.0, min(score, 1.0))
        roll = self._response_gate_roll(event)
        action = "reply" if roll <= score else "observe"

        if action == "reply":
            self._response_gate_record_reply(key, now)
            return {
                "action": "reply",
                "score": round(score, 3),
                "roll": round(roll, 3),
                "reason": "；".join(reasons) or "当前自然可以回复",
            }

        self._response_gate_record_no_reply(key, now)
        return {
            "action": "observe",
            "score": round(score, 3),
            "roll": round(roll, 3),
            "reason": "；".join(reasons) or "当前更适合看见但不打断",
        }
