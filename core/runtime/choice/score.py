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
        key = self._response_gate_scope_key(event)
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


        score += self._response_gate_batch_delta(key, event, pending_count, now, reasons, is_group=is_group)
        if pending_count >= max(1, int(config.bypass_pending_count or 0)):
            score += 0.18
            reasons.append("近期待看消息较多")
        elif pending_count <= 1 and is_group:
            score -= 0.08
            reasons.append("群聊还没有形成稳定话轮")

        last_reply_at = self._response_gate_last_reply_at.get(key)
        if isinstance(last_reply_at, datetime.datetime):
            elapsed = (now - last_reply_at).total_seconds()
            if elapsed < max(0, int(config.min_interval_seconds or 0)):
                score -= 0.35
                reasons.append("刚回复过，避免连续抢话")

        score += self._response_gate_presence_delta(key, now, reasons)
        score += self._response_gate_attention_delta(key, now, reasons, is_group=is_group)

        if state is not None:
            score += self._response_gate_state_delta(state, is_group, reasons)

        return max(0.0, min(score, 1.0)), reasons

    def _response_gate_state_delta(self, state: Any, is_group: bool, reasons: list[str]) -> float:
        delta = 0.0
        interaction = self._response_gate_int(getattr(state, "interaction_capacity", None), 55)
        attention = self._response_gate_int(getattr(state, "attention_openness", None), 55)
        mood_score = self._response_gate_int(getattr(state, "mood_score", None), 60)
        social = self._response_gate_int(getattr(state, "social", None), 50)
        sleepiness = self._response_gate_int(getattr(state, "sleepiness", None), 35)
        busyness = self._response_gate_int(getattr(state, "busyness", None), 45)
        stress = self._response_gate_int(getattr(state, "stress", None), 25)
        interrupt_level = str(getattr(state, "interrupt_level", "") or "").strip()
        watch_state = str(getattr(state, "watch_state", "") or "").strip()
        sleep_depth = str(getattr(getattr(state, "sleep", None), "depth", "") or "").strip()
        rhythm = self._response_gate_rhythm_dict(getattr(state, "physiological_rhythm", None))
        social_battery = self._response_gate_int(rhythm.get("social_battery"), interaction)
        body_condition = rhythm.get("body_condition") if isinstance(rhythm.get("body_condition"), dict) else {}
        body_intensity = self._response_gate_int(body_condition.get("intensity"), 0)

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

        if mood_score >= 75:
            delta += 0.08
            reasons.append("心情状态较轻松")
        elif mood_score <= 30:
            delta -= 0.12
            reasons.append("心情状态偏低")

        if social <= 25:
            delta -= 0.12
            reasons.append("社交意愿偏低")
        elif social >= 70:
            delta += 0.1
            reasons.append("社交意愿较高")

        if social_battery <= 25:
            delta -= 0.1
            reasons.append("社交电量偏低")
        elif social_battery >= 75:
            delta += 0.06
            reasons.append("社交电量较足")

        if body_intensity >= 70:
            delta -= 0.14
            reasons.append("身体负担较高")
        elif body_intensity >= 45:
            delta -= 0.08
            reasons.append("身体状态需要放缓")

        if sleepiness >= 75 or sleep_depth in {"light_sleep", "deep_sleep"}:
            delta -= 0.22
            reasons.append("困意明显")
        elif sleepiness <= 25:
            delta += 0.06
            reasons.append("困倦感较低")

        if busyness >= 75:
            delta -= 0.12
            reasons.append("当前忙碌度高")
        elif busyness <= 25:
            delta += 0.06
            reasons.append("当前不太忙")


        if stress >= 75:
            delta -= 0.14
            reasons.append("压力感较高")
        elif stress <= 20:
            delta += 0.04
            reasons.append("压力感较低")
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

    async def _response_gate_relationship_delta(
        self,
        event: Any,
        now: datetime.datetime,
        reasons: list[str],
    ) -> float:
        archive = getattr(self, "archive", None)
        getter = getattr(archive, "get_relationship", None)
        if not callable(getter):
            return 0.0

        relationship = None
        for profile_id in self._response_gate_profile_ids(event):
            try:
                value = getter(profile_id)
                relationship = await value if hasattr(value, "__await__") else value
            except Exception:
                relationship = None
            if relationship is not None:
                break
        if relationship is None:
            return 0.0

        delta = 0.0
        interactions = self._response_gate_int(getattr(relationship, "interactions", None), 0)
        if interactions >= 8:
            delta += 0.1
            reasons.append("关系互动很熟")
        elif interactions >= 3:
            delta += 0.06
            reasons.append("关系互动较熟")
        elif interactions > 0:
            delta += 0.03
            reasons.append("已有关系互动")

        signal_count = self._response_gate_relationship_signal_count(relationship)
        if signal_count >= 3:
            delta += 0.06
            reasons.append("关系印象较清楚")
        elif signal_count > 0:
            delta += 0.03
            reasons.append("有关系印象")

        days_since = self._response_gate_days_since(getattr(relationship, "last_seen", ""), now)
        if days_since is not None:
            if days_since <= 7:
                delta += 0.05
                reasons.append("最近刚互动过")
            elif days_since <= 30:
                delta += 0.03
                reasons.append("近期有互动")

        if not self._event_is_group_message(event):
            delta += 0.03
            reasons.append("私聊熟人语境")
        return min(delta, 0.18)

    def _response_gate_profile_ids(self, event: Any) -> list[str]:
        ids: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in ids:
                ids.append(text)

        profile_getter = getattr(self, "_event_profile_id", None)
        if callable(profile_getter):
            try:
                add(profile_getter(event, self._safe_event_call(event, "get_sender_name") or "用户"))
            except Exception:
                pass
        add(self._safe_event_call(event, "get_sender_id"))
        return ids

    @staticmethod
    def _response_gate_relationship_signal_count(relationship: Any) -> int:
        signals = 0
        for attr in ("subjective_name", "relationship_story", "persona_hint", "alias"):
            if str(getattr(relationship, attr, "") or "").strip():
                signals += 1
        for attr in ("subjective_tags", "notes", "memory_points", "contacts"):
            values = getattr(relationship, attr, None)
            if isinstance(values, list) and values:
                signals += 1
        return signals

    @staticmethod
    def _response_gate_days_since(date_text: Any, now: datetime.datetime) -> int | None:
        text = str(date_text or "").strip()[:10]
        if not text:
            return None
        try:
            seen = datetime.date.fromisoformat(text)
        except ValueError:
            return None
        return max(0, (now.date() - seen).days)

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
    def _response_gate_rhythm_dict(value: Any) -> dict:
        if isinstance(value, dict):
            return value
        as_dict = getattr(value, "as_dict", None)
        if callable(as_dict):
            data = as_dict()
            return data if isinstance(data, dict) else {}
        return {}

    @staticmethod
    def _response_gate_reply(reason: str, *, forced: bool = False) -> dict[str, Any]:
        return {"action": "reply", "reason": reason, "forced": forced}
