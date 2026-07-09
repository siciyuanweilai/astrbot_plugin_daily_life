from __future__ import annotations

import datetime
from typing import Any


class ResponseGateSenseMixin:
    def _response_gate_attention_delta(
        self,
        key: str,
        now: datetime.datetime,
        reasons: list[str],
        *,
        is_group: bool,
    ) -> float:
        attention = getattr(self, "_response_gate_attention", None)
        if not isinstance(attention, dict):
            self._response_gate_attention = {}
            return 0.0
        focus_key = str(attention.get("focus_key") or "")
        updated_at = attention.get("updated_at")
        if not focus_key or not isinstance(updated_at, datetime.datetime):
            return 0.0
        age = max(0.0, (now - updated_at).total_seconds())
        if age > 15 * 60:
            return 0.0
        strength = max(0.0, 1.0 - age / (15 * 60))
        if focus_key == key:
            delta = 0.07 * strength
            if delta >= 0.03:
                reasons.append("注意力还停在当前会话")
            return delta
        delta = -0.08 * strength if is_group else -0.04 * strength
        if abs(delta) >= 0.03:
            reasons.append("注意力刚在别的会话里")
        return delta

    def _response_gate_presence_delta(
        self,
        key: str,
        now: datetime.datetime,
        reasons: list[str],
    ) -> float:
        reply_times = getattr(self, "_response_gate_reply_times", None)
        if not isinstance(reply_times, dict):
            self._response_gate_reply_times = {}
            return 0.0
        recent = [
            item
            for item in reply_times.get(key, [])
            if isinstance(item, datetime.datetime) and (now - item).total_seconds() <= 5 * 60
        ]
        reply_times[key] = recent[-8:]
        if len(recent) >= 3:
            reasons.append("最近已经连续接了好几轮")
            return -0.16
        if len(recent) == 2:
            reasons.append("最近已经接过两轮")
            return -0.08
        return 0.0

    def _response_gate_batch_delta(
        self,
        key: str,
        event: Any,
        pending_count: int,
        now: datetime.datetime,
        reasons: list[str],
        *,
        is_group: bool,
    ) -> float:
        first_seen = getattr(self, "_response_gate_first_seen_at", {}).get(key)
        batch_age = (now - first_seen).total_seconds() if isinstance(first_seen, datetime.datetime) else 0.0
        visible_text = self._response_gate_visible_text(event)
        text_size = len("".join(visible_text.split()))
        has_media = self._response_gate_has_media(event)
        delta = 0.0
        if pending_count >= 3:
            delta += 0.14
            reasons.append("这轮已经攒了几条消息")
        elif pending_count == 2:
            delta += 0.08
            reasons.append("这轮有连续补充")
        elif text_size <= 3 and not has_media:
            delta -= 0.08 if is_group else 0.04
            reasons.append("这条更像短促补一句")
        if pending_count > 0 and batch_age >= 90:
            delta += 0.08
            reasons.append("消息已经晾了一小会儿")
        return delta

    async def _response_gate_feedback_delta(
        self,
        key: str,
        reasons: list[str],
    ) -> float:
        archive = getattr(self, "archive", None)
        getter = getattr(archive, "get_reply_effects", None)
        if not key or not callable(getter):
            return 0.0
        try:
            effects = await getter(limit=6, scope=key)
        except Exception:
            return 0.0
        if not effects:
            return 0.0
        total = 0.0
        weight_sum = 0.0
        for index, item in enumerate(effects[:6]):
            item_delta = self._response_gate_reply_effect_delta(item)
            if item_delta == 0.0:
                continue
            weight = max(0.4, 1.0 - index * 0.12)
            total += item_delta * weight
            weight_sum += weight
        if weight_sum <= 0:
            return 0.0
        delta = max(-0.16, min(total / weight_sum, 0.16))
        if delta >= 0.03:
            reasons.append("近期接话反馈偏暖")
        elif delta <= -0.03:
            reasons.append("近期接话反馈偏冷")
        return delta

    def _response_gate_reply_effect_delta(self, item: Any) -> float:
        outcome = str(getattr(item, "outcome", "") or "").strip()
        warmth = self._response_gate_int(getattr(item, "warmth", None), 50)
        continuity = self._response_gate_int(getattr(item, "continuity", None), 50)
        friction = self._response_gate_int(getattr(item, "friction", None), 0)
        if outcome == "positive":
            return 0.05 + max(0, warmth + continuity - 100) / 1000
        if outcome in {"ignored", "negative"}:
            return -(0.05 + friction / 1000)
        return 0.0

    async def _response_gate_experience_delta(
        self,
        key: str,
        event: Any,
        reasons: list[str],
    ) -> float:
        archive = getattr(self, "archive", None)
        if archive is None:
            return 0.0
        delta = 0.0
        delta += await self._response_gate_scene_delta(archive, key, reasons)
        delta += await self._response_gate_episode_delta(archive, event, reasons)
        return max(-0.14, min(delta, 0.14))

    async def _response_gate_scene_delta(self, archive: Any, key: str, reasons: list[str]) -> float:
        getter = getattr(archive, "get_behavior_scenes", None)
        if not callable(getter):
            return 0.0
        try:
            scenes = await getter(limit=5, scope=key)
        except Exception:
            return 0.0
        delta = 0.0
        for item in scenes[:5]:
            confidence = max(0.0, min(float(getattr(item, "confidence", 0.0) or 0.0), 1.0))
            support = max(1, int(getattr(item, "support_count", 1) or 1))
            weight = min(1.0, confidence * (0.6 + min(support, 5) * 0.08))
            preferred = str(getattr(item, "preferred_action", "") or "").strip()
            avoid = str(getattr(item, "avoid_action", "") or "").strip()
            if preferred in {"reply", "comfort", "join_ritual", "push_back"}:
                delta += 0.04 * weight
            elif preferred in {"observe", "wait"}:
                delta -= 0.04 * weight
            if avoid == "reply":
                delta -= 0.03 * weight
        if delta >= 0.03:
            reasons.append("过往场景更适合接话")
        elif delta <= -0.03:
            reasons.append("过往场景更适合先观察")
        return delta

    async def _response_gate_episode_delta(self, archive: Any, event: Any, reasons: list[str]) -> float:
        getter = getattr(archive, "get_life_episodes", None)
        if not callable(getter):
            return 0.0
        people = {
            str(self._safe_event_call(event, "get_sender_id") or "").strip(),
            str(self._safe_event_call(event, "get_sender_name") or "").strip(),
        }
        people.update(self._response_gate_profile_ids(event))
        people.discard("")
        if not people:
            return 0.0
        try:
            episodes = await getter(limit=12)
        except Exception:
            return 0.0
        matched = 0
        for item in episodes:
            related = {str(value or "").strip() for value in list(getattr(item, "related_people", []) or [])}
            if people & related:
                matched += 1
        if matched <= 0:
            return 0.0
        reasons.append("近期生活片段里有这个人")
        return min(0.08, 0.03 + matched * 0.015)

    def _response_gate_note_attention(self, key: str, now: datetime.datetime, *, replied: bool) -> None:
        if not key:
            return
        attention = getattr(self, "_response_gate_attention", None)
        if not isinstance(attention, dict):
            self._response_gate_attention = {}
            attention = self._response_gate_attention
        if replied:
            attention["focus_key"] = key
            attention["updated_at"] = now
            attention["priority"] = min(100, int(attention.get("priority") or 50) + 12)
            return
        if attention.get("focus_key") == key:
            attention["updated_at"] = now
            attention["priority"] = max(0, int(attention.get("priority") or 50) - 6)
