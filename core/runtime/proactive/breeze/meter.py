import datetime
from typing import Any

from astrbot.api import logger

from ....life.condition import classify_message_interrupt, message_can_interrupt, normalize_state
from ...markers import LOG_PREFIX


class AirMeterMixin:
    _AIR_BACKOFF_BASE_SECONDS = 15 * 60
    _AIR_BACKOFF_CAP_SECONDS = 120 * 60
    _AIR_BACKOFF_START_COUNT = 2
    _AIR_BYPASS_PENDING_COUNT = 4
    _AIR_WAIT_SECONDS = 10 * 60
    _AIR_MAX_RECENT_MESSAGES = 8

    def _proactive_air_states(self) -> dict[str, dict[str, Any]]:
        states = getattr(self, "_proactive_air_state", None)
        if states is None:
            self._proactive_air_state = {}
            states = self._proactive_air_state
        return states

    def _get_proactive_air_state(self, key: str) -> dict[str, Any]:
        return self._proactive_air_states().setdefault(
            key,
            {
                "quiet_count": 0,
                "silence_inertia": 0,
                "silence_reason": "",
                "backoff_until": None,
                "wait_until": None,
                "last_decision": "",
                "last_reason": "",
                "last_effect": "",
            },
        )

    def _reset_proactive_air_state(self, key: str) -> None:
        if key:
            self._proactive_air_states().pop(key, None)

    def _air_label(self, state: dict[str, Any] | None = None) -> str:
        state = state or {}
        quiet_count = int(state.get("quiet_count") or 0)
        silence_inertia = int(state.get("silence_inertia") or 0)
        last_effect = str(state.get("last_effect") or "").strip()
        if last_effect == "positive":
            return "刚刚接话有回应，空气偏暖，可以保持轻量自然"
        if last_effect == "ignored":
            return "上次闲时续话没有被接住，需要更克制"
        if silence_inertia >= 80:
            return "沉默惯性很高，我已经习惯先安静，除非非常自然否则不主动开口"
        if silence_inertia >= 50:
            return "沉默惯性偏高，先判断开口是否真的顺"
        if quiet_count >= 4:
            return "连续几次都觉得不适合说话，空气偏冷，除非很自然否则安静"
        if quiet_count >= 2:
            return "刚才已经多次选择观察，先别急着插话"
        return "空气正常，仍以自然和不打扰为准"

    def _format_proactive_air_state(self, key: str, now: datetime.datetime) -> str:
        state = self._get_proactive_air_state(key)
        parts = [self._air_label(state)]
        quiet_count = int(state.get("quiet_count") or 0)
        if quiet_count:
            parts.append(f"连续克制 {quiet_count} 次")
        silence_inertia = int(state.get("silence_inertia") or 0)
        if silence_inertia:
            parts.append(f"沉默惯性 {silence_inertia}/100")
        silence_reason = str(state.get("silence_reason") or "").strip()
        if silence_reason:
            parts.append(f"沉默理由：{silence_reason[:80]}")
        last_decision = str(state.get("last_decision") or "").strip()
        last_reason = str(state.get("last_reason") or "").strip()
        if last_decision:
            parts.append(f"上次裁定 {last_decision}")
        if last_reason:
            parts.append(f"上次理由：{last_reason[:80]}")
        wait_until = state.get("wait_until")
        if isinstance(wait_until, datetime.datetime) and wait_until > now:
            parts.append(f"正在等待更多话题自然落点，约 {wait_until.strftime('%H:%M')} 后再看")
        backoff_until = state.get("backoff_until")
        if isinstance(backoff_until, datetime.datetime) and backoff_until > now:
            parts.append(f"退避到 {backoff_until.strftime('%H:%M')}")
        return "；".join(parts)

    def _air_backoff_seconds(self, quiet_count: int) -> int:
        if quiet_count < self._AIR_BACKOFF_START_COUNT:
            return 0
        exponent = max(0, quiet_count - self._AIR_BACKOFF_START_COUNT)
        return min(self._AIR_BACKOFF_CAP_SECONDS, self._AIR_BACKOFF_BASE_SECONDS * (2**exponent))

    def _candidate_pending_count(self, candidate: dict[str, Any]) -> int:
        try:
            return max(1, int(candidate.get("pending_count") or 1))
        except (TypeError, ValueError):
            return 1

    def _proactive_air_delay_remaining(
        self,
        key: str,
        now: datetime.datetime,
        *,
        pending_count: int = 1,
    ) -> int:
        state = self._get_proactive_air_state(key)
        wait_until = state.get("wait_until")
        if isinstance(wait_until, datetime.datetime) and wait_until > now:
            return max(1, int((wait_until - now).total_seconds()))
        if isinstance(wait_until, datetime.datetime) and wait_until <= now:
            state["wait_until"] = None

        backoff_until = state.get("backoff_until")
        if isinstance(backoff_until, datetime.datetime) and backoff_until > now:
            if pending_count >= self._AIR_BYPASS_PENDING_COUNT:
                state["backoff_until"] = None
                logger.debug(f"{LOG_PREFIX} 闲时回复退避被新消息打破：待看消息 {pending_count} 条")
                return 0
            return max(1, int((backoff_until - now).total_seconds()))
        if isinstance(backoff_until, datetime.datetime) and backoff_until <= now:
            state["backoff_until"] = None
        return 0

    async def _proactive_readiness_check(
        self,
        event: Any,
        now: datetime.datetime,
        *,
        pending_count: int = 1,
    ) -> dict[str, Any]:
        key = self._proactive_scope_key(event)
        is_group = self._event_is_group_message(event)
        config = self.config.proactive
        score = config.talk_frequency if is_group else config.private_talk_frequency
        reasons: list[str] = []

        text = str(getattr(event, "message_str", "") or "").strip()
        compact_length = len("".join(text.split()))
        has_media_checker = getattr(self, "_response_gate_has_media", None)
        has_media = bool(has_media_checker(event)) if callable(has_media_checker) else False
        directed = bool(getattr(self, "_event_is_directed", lambda *_: False)(event))
        quoted = bool(getattr(self, "_event_has_quote", lambda *_: False)(event))
        interrupt = classify_message_interrupt(text, directed=directed, quoted=quoted)
        local_target = self._select_proactive_reply_target(event)
        if pending_count >= 4:
            score += 0.18
            reasons.append("待看消息已经攒起来")
        elif pending_count >= 2:
            score += 0.08
            reasons.append("会话有连续补充")
        elif is_group and compact_length <= 3 and not has_media:
            score -= 0.12
            reasons.append("群聊里只是很短的一句")

        last_activity_at = getattr(event, "proactive_last_activity_at", None)
        if isinstance(last_activity_at, datetime.datetime):
            idle_seconds = max(0, int((now - last_activity_at).total_seconds()))
            if idle_seconds >= self._proactive_idle_seconds(event) * 2:
                score -= 0.04
                reasons.append("已经安静太久，先别突兀续话")
            elif idle_seconds >= self._proactive_idle_seconds(event):
                score += 0.04
                reasons.append("沉默时间刚好到自然回看")

        last_bot_reply_at = getattr(event, "proactive_last_bot_reply_at", None)
        if isinstance(last_bot_reply_at, datetime.datetime):
            elapsed = max(0, int((now - last_bot_reply_at).total_seconds()))
            if elapsed <= 10 * 60:
                score -= 0.12
                reasons.append("普通回复刚接过这轮")

        if key:
            score += self._proactive_air_delta(key, reasons)

        state_getter = getattr(self, "_response_gate_current_state", None)
        state_delta = getattr(self, "_response_gate_state_delta", None)
        state = None
        if callable(state_getter) and callable(state_delta):
            state = await state_getter(now)
            if state is not None:
                normalized = normalize_state(state.as_dict() if hasattr(state, "as_dict") else state)
                if not message_can_interrupt(normalized, interrupt) and pending_count < self._AIR_BYPASS_PENDING_COUNT:
                    reasons.append("当前生活状态不适合被这类闲时消息打断")
                    return {
                        "should_reply": False,
                        "handled": True,
                        "decision": "observe",
                        "confidence": 1.0,
                        "reason": "；".join(reasons),
                        "reply_text": "",
                        "local_score": round(max(0.0, min(score, 1.0)), 3),
                        "local_reasons": reasons,
                        "interrupt_level": interrupt.get("level", "ordinary"),
                        "interrupt_reason": interrupt.get("reason", ""),
                        "can_interrupt": False,
                        "target_message_id": local_target.get("message_id", ""),
                        "target_topic": local_target.get("topic", ""),
                        "target_sender_name": local_target.get("sender_name", ""),
                    }
                score += state_delta(state, is_group, reasons)

        relationship_delta = getattr(self, "_response_gate_relationship_delta", None)
        if callable(relationship_delta):
            score += await relationship_delta(event, now, reasons)

        feedback_delta = getattr(self, "_response_gate_feedback_delta", None)
        if callable(feedback_delta):
            scope = self._event_session_id(event) or key
            if scope:
                score += await feedback_delta(scope, reasons)

        experience_delta = getattr(self, "_response_gate_experience_delta", None)
        if callable(experience_delta) and key:
            score += await experience_delta(key, event, reasons)

        score = max(0.0, min(score, 1.0))
        threshold = 0.16 if not is_group else 0.2
        if pending_count >= self._AIR_BYPASS_PENDING_COUNT:
            threshold = min(threshold, 0.1)
        if score < threshold:
            return {
                "should_reply": False,
                "handled": True,
                "decision": "observe",
                "confidence": round(1.0 - score, 3),
                "reason": "；".join(reasons) or "本地状态判断更适合继续观察",
                "reply_text": "",
                "local_score": round(score, 3),
                "local_reasons": reasons,
                "interrupt_level": interrupt.get("level", "ordinary"),
                "interrupt_reason": interrupt.get("reason", ""),
                "can_interrupt": True,
                "target_message_id": local_target.get("message_id", ""),
                "target_topic": local_target.get("topic", ""),
                "target_sender_name": local_target.get("sender_name", ""),
            }
        return {
            "should_evaluate": True,
            "local_score": round(score, 3),
            "local_reasons": reasons,
            "interrupt_level": interrupt.get("level", "ordinary"),
            "interrupt_reason": interrupt.get("reason", ""),
            "can_interrupt": True,
            "target_message_id": local_target.get("message_id", ""),
            "target_topic": local_target.get("topic", ""),
            "target_sender_name": local_target.get("sender_name", ""),
        }

    def _proactive_air_delta(self, key: str, reasons: list[str]) -> float:
        state = self._get_proactive_air_state(key)
        delta = 0.0
        last_effect = str(state.get("last_effect") or "").strip()
        if last_effect == "positive":
            delta += 0.12
            reasons.append("上次闲时接话被接住了")
        elif last_effect == "ignored":
            delta -= 0.16
            reasons.append("上次闲时接话没有被接住")
        quiet_count = max(0, int(state.get("quiet_count") or 0))
        if quiet_count >= 3:
            delta -= 0.12
            reasons.append("最近多次选择克制")
        elif quiet_count >= 1:
            delta -= 0.05
            reasons.append("刚才已经选择观察")
        silence_inertia = max(0, min(int(state.get("silence_inertia") or 0), 100))
        if silence_inertia >= 70:
            delta -= 0.14
            reasons.append("会话沉默惯性偏高")
        elif silence_inertia >= 40:
            delta -= 0.06
            reasons.append("会话有一点沉默惯性")
        return delta

    def _update_proactive_air_after_decision(
        self,
        key: str,
        payload: dict[str, Any],
        now: datetime.datetime,
        *,
        sent: bool,
    ) -> None:
        if not key:
            return
        state = self._get_proactive_air_state(key)
        decision = str(payload.get("decision") or ("reply" if sent else "observe")).strip() or "observe"
        state["last_decision"] = decision
        state["last_reason"] = str(payload.get("reason") or "").strip()
        state["silence_reason"] = state["last_reason"]

        if sent:
            self._reset_proactive_air_state(key)
            return

        state["quiet_count"] = int(state.get("quiet_count") or 0) + 1
        state["silence_inertia"] = min(100, int(state.get("silence_inertia") or 0) + (16 if decision == "wait" else 12))
        if decision == "wait":
            state["wait_until"] = now + datetime.timedelta(seconds=self._AIR_WAIT_SECONDS)
            state["backoff_until"] = None
            return

        state["wait_until"] = None
        seconds = self._air_backoff_seconds(int(state.get("quiet_count") or 0))
        state["backoff_until"] = now + datetime.timedelta(seconds=seconds) if seconds > 0 else None

    def _recent_messages_for_candidate(self, candidate: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(candidate, dict):
            return []
        messages = candidate.get("recent_messages")
        if isinstance(messages, list):
            return [item for item in messages if isinstance(item, dict)][-self._AIR_MAX_RECENT_MESSAGES:]
        message = str(candidate.get("message") or "").strip()
        if not message:
            return []
        return [
            {
                "message_id": str(candidate.get("message_id") or ""),
                "sender_id": str(candidate.get("sender_id") or ""),
                "sender_name": str(candidate.get("sender_name") or ""),
                "content": message,
                "seen_at": candidate.get("last_activity_at"),
            }
        ]

    def _select_proactive_reply_target(self, event: Any) -> dict[str, str]:
        raw_messages = getattr(event, "proactive_recent_messages", None)
        if not isinstance(raw_messages, list) or not raw_messages:
            raw_messages = [
                {
                    "message_id": self._event_message_id(event),
                    "sender_name": self._safe_event_call(event, "get_sender_name"),
                    "content": str(getattr(event, "message_str", "") or "").strip(),
                }
            ]
        for item in reversed([message for message in raw_messages if isinstance(message, dict)]):
            content = " ".join(str(item.get("content") or "").split())
            message_id = str(item.get("message_id") or "").strip()
            if not (content or message_id):
                continue
            return {
                "message_id": message_id,
                "topic": content[:80],
                "sender_name": str(item.get("sender_name") or "").strip(),
            }
        return {"message_id": "", "topic": "", "sender_name": ""}

    def _format_candidate_recent_messages(self, event: Any, fallback_sender_name: str) -> str:
        raw_messages = getattr(event, "proactive_recent_messages", None)
        if not isinstance(raw_messages, list) or not raw_messages:
            raw_messages = [
                {
                    "message_id": self._event_message_id(event),
                    "sender_name": fallback_sender_name,
                    "content": str(getattr(event, "message_str", "") or "").strip(),
                    "seen_at": getattr(event, "proactive_last_activity_at", None),
                }
            ]
        lines: list[str] = []
        for index, item in enumerate(raw_messages[-self._AIR_MAX_RECENT_MESSAGES:], start=1):
            content = "；".join(part.strip() for part in str(item.get("content") or "").splitlines() if part.strip())
            if len(content) > 120:
                content = content[:120].rstrip() + "..."
            if not content:
                continue
            message_id = str(item.get("message_id") or "").strip() or f"recent_{index}"
            sender_name = str(item.get("sender_name") or "").strip() or fallback_sender_name or "对方"
            seen_at = item.get("seen_at")
            time_text = seen_at.strftime("%H:%M") if isinstance(seen_at, datetime.datetime) else ""
            prefix = f"- {message_id}"
            if time_text:
                prefix += f" · {time_text}"
            lines.append(f"{prefix} · {sender_name}: {content}")
        return "\n".join(lines) if lines else "暂无可选目标消息。"
