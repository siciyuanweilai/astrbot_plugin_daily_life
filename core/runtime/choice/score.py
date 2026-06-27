from __future__ import annotations

import datetime
import hashlib
from typing import Any


class ResponseGateScoreMixin:
    def _response_gate_score(
        self,
        event: Any,
        state: Any | None,
        pending_count: int,
        now: datetime.datetime,
    ) -> tuple[float, list[str]]:
        is_group = self._event_is_group_message(event)
        config = self.config.response_gate
        score = config.group_talk_frequency if is_group else config.private_talk_frequency
        reasons: list[str] = []

        visible_text = self._response_gate_visible_text(event)
        has_media = self._response_gate_has_media(event)
        if is_group and has_media and not visible_text:
            score = min(score, config.media_only_group_frequency)
            reasons.append("群聊纯媒体消息先后台识别")
        elif has_media:
            score += 0.1
            reasons.append("消息含真实媒体组件")

        if visible_text:
            length = len(visible_text)
            if length <= 2 and is_group:
                score -= 0.18
                reasons.append("群聊短促碎片消息")
            elif length >= 8:
                score += 0.08
                reasons.append("内容信息量较完整")

        if pending_count >= max(1, int(config.bypass_pending_count or 0)):
            score += 0.18
            reasons.append("近期待看消息较多")
        elif pending_count <= 1 and is_group:
            score -= 0.08
            reasons.append("群聊还没有形成稳定话轮")

        last_reply_at = self._response_gate_last_reply_at.get(self._response_gate_scope_key(event))
        if isinstance(last_reply_at, datetime.datetime):
            elapsed = (now - last_reply_at).total_seconds()
            if elapsed < max(0, int(config.min_interval_seconds or 0)):
                score -= 0.35
                reasons.append("刚回复过，避免连续抢话")

        if state is not None:
            score += self._response_gate_state_delta(state, is_group, reasons)

        return max(0.0, min(score, 1.0)), reasons

    def _response_gate_state_delta(self, state: Any, is_group: bool, reasons: list[str]) -> float:
        delta = 0.0
        interaction = self._response_gate_int(getattr(state, "interaction_capacity", None), 55)
        attention = self._response_gate_int(getattr(state, "attention_openness", None), 55)
        social = self._response_gate_int(getattr(state, "social", None), 50)
        sleepiness = self._response_gate_int(getattr(state, "sleepiness", None), 35)
        busyness = self._response_gate_int(getattr(state, "busyness", None), 45)
        interrupt_level = str(getattr(state, "interrupt_level", "") or "").strip()
        watch_state = str(getattr(state, "watch_state", "") or "").strip()
        sleep_depth = str(getattr(getattr(state, "sleep", None), "depth", "") or "").strip()

        if interaction >= 70:
            delta += 0.12
            reasons.append("互动余力较高")
        elif interaction <= 35:
            delta -= 0.16
            reasons.append("互动余力偏低")

        if attention >= 70:
            delta += 0.1
            reasons.append("注意力开放")
        elif attention <= 35:
            delta -= 0.14
            reasons.append("注意力收窄")

        if social <= 25:
            delta -= 0.12
            reasons.append("社交意愿偏低")
        elif social >= 70:
            delta += 0.08
            reasons.append("社交意愿较高")

        if sleepiness >= 75 or sleep_depth in {"light_sleep", "deep_sleep"}:
            delta -= 0.22
            reasons.append("困意明显")
        elif sleepiness <= 25:
            delta += 0.04

        if busyness >= 75:
            delta -= 0.12
            reasons.append("当前忙碌度高")

        if is_group:
            if interrupt_level == "high":
                delta -= 0.2
                reasons.append("群聊打断门槛高")
            elif interrupt_level == "medium":
                delta -= 0.08
                reasons.append("群聊打断门槛中等")
            if watch_state in {"active_watch", "engaged"}:
                delta += 0.12
                reasons.append("正在关注群聊")
            elif watch_state in {"blackout", "peek"}:
                delta -= 0.12
                reasons.append("只是偶尔瞥见群聊")

        return delta

    def _response_gate_roll(self, event: Any) -> float:
        marker = "|".join(
            [
                self._event_session_id(event),
                self._event_message_id(event),
                self._safe_event_call(event, "get_sender_id"),
                self._response_gate_visible_text(event)[:80],
            ]
        )
        digest = hashlib.sha256(marker.encode("utf-8", errors="ignore")).hexdigest()
        return int(digest[:8], 16) / 0xFFFFFFFF

    @staticmethod
    def _response_gate_int(value: Any, default: int) -> int:
        try:
            return max(0, min(int(value), 100))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _response_gate_reply(reason: str, *, forced: bool = False) -> dict[str, Any]:
        return {"action": "reply", "reason": reason, "forced": forced}
