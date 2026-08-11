from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...config.options.cast import as_bool
from ...life.tools import extract_json_from_text
from ...prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..markers import LOG_PREFIX

RESPONSE_GATE_SEMANTIC_SCORE_MIN = 0.40
RESPONSE_GATE_SEMANTIC_SCORE_MAX = 0.68
RESPONSE_GATE_SEMANTIC_TIMEOUT_SECONDS = 2.5


class ResponseGateApplyMixin:
    def _response_gate_superseded_decision(self, event: Any) -> dict[str, Any] | None:
        checker = getattr(self, "stop_stale_continuous_turn_event", None)
        if not callable(checker) or not checker(event):
            return None
        return {
            "action": "observe",
            "reason": "当前话轮已由后续消息接管",
            "superseded": True,
        }

    def _response_gate_scope_enabled(self, event: Any) -> bool:
        is_group = self._event_is_group_message(event)
        field = "group_enabled" if is_group else "private_enabled"
        config = getattr(self.config, "response_gate", None)
        parsed_value = bool(getattr(config, field, False)) if config else False

        raw_config = getattr(self, "raw_config", None)
        raw_section = (
            raw_config.get("response_gate_config")
            if isinstance(raw_config, dict)
            else None
        )
        if isinstance(raw_section, dict) and field in raw_section:
            return as_bool(raw_section.get(field), parsed_value)
        return parsed_value

    async def apply_response_gate_for_event(self, event: Any) -> dict[str, Any]:
        decision = await self.evaluate_response_gate(event)
        if decision.get("superseded"):
            return decision
        if decision.get("action") == "wait":
            wait_handler = getattr(self, "wait_continuous_turn_after_semantic", None)
            wait_outcome = (
                await wait_handler(event) if callable(wait_handler) else "disabled"
            )
            if wait_outcome == "superseded":
                return {
                    "action": "observe",
                    "reason": "当前话轮已由后续消息接管",
                    "superseded": True,
                }
            if wait_outcome == "reply":
                key = self._response_gate_scope_key(event)
                self._response_gate_record_reply(key, life_now())
                decision = {
                    **decision,
                    "action": "reply",
                    "reason": "语义判断适合等待补充，已在收束上限后统一回复",
                    "continuous_turn_waited": True,
                }
        note_decision = getattr(self, "note_conversation_turn_decision", None)
        if callable(note_decision):
            note_decision(event, decision)
        if decision.get("action") == "observe":
            await self.record_observed_private_user_message(event)
            self._suppress_default_llm(event)
            complete_turn = getattr(self, "complete_continuous_turn", None)
            if callable(complete_turn):
                complete_turn(event)
            logger.debug(
                f"{LOG_PREFIX} 随心回复：观察不回复；{decision.get('reason') or '当前不适合回复'}"
            )
        elif decision.get("action") == "wait":
            key = self._response_gate_scope_key(event)
            now = life_now()
            self._response_gate_mark_wait(
                key,
                now,
                reason=str(decision.get("reason") or ""),
                message_id=self._event_message_id(event),
                message_text=self._response_gate_visible_text(event),
            )
            await self.record_observed_private_user_message(event)
            self._suppress_default_llm(event)
            logger.debug(
                f"{LOG_PREFIX} 随心回复：等待；{decision.get('reason') or '先把发言权留给对方'}"
            )
        return decision

    async def _response_gate_semantic_decision(
        self,
        event: Any,
        *,
        score: float,
        reasons: list[str],
        pending_count: int,
        wait_state: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        provider_getter = getattr(self, "_get_proactive_provider", None)
        if not callable(provider_getter):
            return None
        provider = await provider_getter()
        if not provider:
            return None
        is_group = self._event_is_group_message(event)
        turn_getter = getattr(self, "continuous_turn_messages", None)
        turn_messages = list(turn_getter(event)) if callable(turn_getter) else []
        current_message = (
            turn_messages[-1]
            if turn_messages
            else self._response_gate_visible_text(event) or "仅包含媒体"
        )
        accumulated_messages = (
            list(wait_state.get("messages") or []) if wait_state else []
        )
        accumulated_messages.extend(turn_messages or [current_message])
        fixed = f"""判断当前角色在这一轮对话中应当立即回复、短暂等待对方补充，还是只看见但不打断。

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON：
{{"action":"reply|wait|observe","confidence":0.0,"reason":"基于当前话轮语义和状态的简短理由"}}

边界：
- 命令、明确指向当前角色、平台状态和发送许可已经由代码处理，这里只判断模糊话轮。
- reply 表示现在接话自然；wait 表示当前表达像仍会继续，适合短暂聚合；observe 表示看见但不介入更自然。
- 不根据单个词、标点或固定句式裁定，也不要改写或回答消息。"""
        dynamic = f"""场景：{"群聊" if is_group else "私聊"}
当前消息：{current_message}
本轮连续内容：{json.dumps(accumulated_messages[-3:], ensure_ascii=False)}
本轮累计消息：{pending_count}
已在等待聚合：{"是" if wait_state else "否"}
连续等待次数：{int(wait_state.get("rounds") or 0) if wait_state else 0}
本地状态分：{score:.3f}
本地状态依据：{"；".join(reasons) or "无"}"""
        session_id = f"daily_life_response_gate_{uuid.uuid4().hex[:8]}"
        provider_id = str(getattr(self.config.proactive, "provider", "") or "")
        try:
            raw = await asyncio.wait_for(
                self.call_text_model(
                    provider,
                    cache_friendly_prompt(fixed, dynamic),
                    session_id,
                    empty_retries=0,
                    primary_provider_id=provider_id,
                ),
                timeout=RESPONSE_GATE_SEMANTIC_TIMEOUT_SECONDS,
            )
            payload = extract_json_from_text(raw)
            if not isinstance(payload, dict):
                self._response_gate_semantic_metrics["invalid"] += 1
                return None
            action = str(payload.get("action") or "").strip().lower()
            try:
                confidence = max(0.0, min(float(payload.get("confidence") or 0.0), 1.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if action not in {"reply", "wait", "observe"} or confidence < 0.55:
                self._response_gate_semantic_metrics["invalid"] += 1
                return None
            if action == "wait" and (
                pending_count >= 3
                or (wait_state and int(wait_state.get("rounds") or 0) >= 2)
            ):
                action = "reply"
            self._response_gate_semantic_metrics["accepted"] += 1
            return {
                "action": action,
                "confidence": round(confidence, 3),
                "reason": str(payload.get("reason") or "").strip()
                or "已结合当前话轮语义裁定",
                "semantic": True,
            }
        except asyncio.TimeoutError:
            self._response_gate_semantic_metrics["timeout"] += 1
            return None
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 随心回复语义裁定跳过：{type(exc).__name__}: {exc}"
            )
            return None
        finally:
            await self.close_text_session(session_id)

    async def evaluate_response_gate(
        self,
        event: Any,
        *,
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        superseded = self._response_gate_superseded_decision(event)
        if superseded:
            return superseded
        follow_up_checker = getattr(
            self, "continuous_turn_event_is_inflight_follow_up", None
        )
        if callable(follow_up_checker) and follow_up_checker(event):
            return self._response_gate_reply("接续正在生成的当前话轮", forced=True)
        config = getattr(self.config, "response_gate", None)
        if not config:
            return self._response_gate_reply("随心回复配置不可用")
        if self._response_gate_should_skip(event):
            return self._response_gate_reply(
                "命令、插件处理、自身消息或已停止事件不进入门控"
            )
        is_group = self._event_is_group_message(event)
        if not self._response_gate_scope_enabled(event):
            scope = "群聊" if is_group else "私聊"
            return self._response_gate_reply(f"未启用{scope}随心回复")

        now = now or life_now()
        key = self._response_gate_scope_key(event)
        if not key:
            return self._response_gate_reply("没有可用会话标识")

        forced_reason = self._response_gate_force_reply_reason(event)
        if forced_reason:
            self._response_gate_record_reply(key, now)
            self._response_gate_note_attention(key, now, replied=True, priority=100)
            return self._response_gate_reply(forced_reason, forced=True)

        pending_count = self._response_gate_record_seen(key, now)
        turn_count_getter = getattr(self, "continuous_turn_message_count", None)
        turn_count = (
            int(turn_count_getter(event) or 0) if callable(turn_count_getter) else 0
        )
        if turn_count > pending_count:
            pending_count = turn_count
            self._response_gate_pending_count[key] = pending_count
        wait_state = self._response_gate_wait_state(key, now)
        continuation_reason = self._response_gate_continuation_reason(key, now)
        if continuation_reason:
            self._response_gate_record_reply(key, now)
            return self._response_gate_reply(continuation_reason, forced=True)

        if self._response_gate_in_backoff(key, now, pending_count):
            return {"action": "observe", "reason": "连续不回复后的短暂安静观察仍在生效"}

        relationship_reasons: list[str] = []
        feedback_reasons: list[str] = []
        experience_reasons: list[str] = []
        (
            state,
            relationship_delta,
            feedback_delta,
            experience_delta,
        ) = await asyncio.gather(
            self._response_gate_current_state(now),
            self._response_gate_relationship_delta(event, now, relationship_reasons),
            self._response_gate_feedback_delta(key, feedback_reasons),
            self._response_gate_experience_delta(key, event, experience_reasons),
        )
        superseded = self._response_gate_superseded_decision(event)
        if superseded:
            return superseded
        score, reasons = self._response_gate_score(event, state, pending_count, now)
        score += relationship_delta + feedback_delta + experience_delta
        reasons.extend(relationship_reasons)
        reasons.extend(feedback_reasons)
        reasons.extend(experience_reasons)
        if wait_state:
            reasons.append("正在聚合当前连续话轮")
            score += 0.08
        score = max(0.0, min(score, 1.0))

        semantic_turn_checker = getattr(
            self, "continuous_turn_semantic_enabled_for_event", None
        )
        semantic_turn = bool(
            semantic_turn_checker(event) if callable(semantic_turn_checker) else False
        )
        if (
            RESPONSE_GATE_SEMANTIC_SCORE_MIN
            <= score
            <= RESPONSE_GATE_SEMANTIC_SCORE_MAX
            or wait_state
            or semantic_turn
        ):
            semantic = await self._response_gate_semantic_decision(
                event,
                score=score,
                reasons=reasons,
                pending_count=pending_count,
                wait_state=wait_state,
            )
            superseded = self._response_gate_superseded_decision(event)
            if superseded:
                return superseded
            if semantic:
                action = semantic["action"]
                if action == "reply":
                    self._response_gate_record_reply(key, now)
                elif action == "observe":
                    self._response_gate_clear_wait(key)
                    self._response_gate_record_no_reply(key, now)
                return {**semantic, "score": round(score, 3)}

        self._response_gate_semantic_metrics["fallback"] += 1
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
