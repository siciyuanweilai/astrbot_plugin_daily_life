import datetime
from typing import Any

from astrbot.api import logger

from ....models import BehaviorFeedbackRecord, ReplyEffectRecord
from ...markers import LOG_PREFIX


class AirEffectMixin:
    _AIR_FEEDBACK_WINDOW_SECONDS = 30 * 60

    def _proactive_feedback_watches(self) -> dict[str, dict[str, Any]]:
        watches = getattr(self, "_proactive_feedback_watch", None)
        if watches is None:
            self._proactive_feedback_watch = {}
            watches = self._proactive_feedback_watch
        return watches

    async def _save_proactive_behavior_feedback(
        self,
        key: str,
        *,
        date: str,
        target_id: str,
        action: str,
        result: str,
        feedback: str,
        reason: str,
        score: float,
    ) -> None:
        if not key:
            return
        adder = getattr(self.archive, "add_behavior_feedback", None)
        if not callable(adder):
            return
        try:
            await adder(
                BehaviorFeedbackRecord(
                    date=date,
                    target_type="proactive_session",
                    target_id=target_id or key,
                    scene="闲时回复读空气",
                    action=action,
                    feedback=feedback,
                    result=result,
                    score=score,
                    reason=reason,
                    source="proactive_reply",
                )
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时回复反馈记录失败：{exc}")

    def _track_proactive_reply_effect(
        self,
        key: str,
        event: Any,
        payload: dict[str, Any],
        reply_text: str,
        now: datetime.datetime,
    ) -> None:
        if not key:
            return
        self._proactive_feedback_watches()[key] = {
            "sent_at": now,
            "target_scope": self._event_session_id(event),
            "message_id": self._event_message_id(event),
            "target_message_id": str(payload.get("target_message_id") or "").strip(),
            "target_topic": str(payload.get("target_topic") or "").strip(),
            "reply_text": reply_text,
            "reason": str(payload.get("reason") or "").strip(),
        }

    async def _save_pending_reply_effect(
        self,
        key: str,
        event: Any,
        payload: dict[str, Any],
        reply_text: str,
    ) -> None:
        saver = getattr(self.archive, "save_reply_effect", None)
        if not callable(saver):
            return
        try:
            saved = await saver(
                ReplyEffectRecord(
                    scope=self._event_session_id(event) or key,
                    target_message_id=str(payload.get("target_message_id") or self._event_message_id(event) or ""),
                    reply_text=reply_text,
                    outcome="pending",
                    reason=str(payload.get("reason") or "").strip(),
                    source="proactive_reply",
                )
            )
            if saved and key in self._proactive_feedback_watches():
                self._proactive_feedback_watches()[key]["reply_effect_id"] = saved.id
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时回复效果记录失败：{exc}")

    async def _update_reply_effect_watch(
        self,
        watch: dict[str, Any],
        *,
        outcome: str,
        evidence: str,
        warmth: int,
        continuity: int,
        friction: int,
    ) -> None:
        effect_id = int(watch.get("reply_effect_id") or 0)
        updater = getattr(self.archive, "update_reply_effect_outcome", None)
        if effect_id <= 0 or not callable(updater):
            return
        try:
            await updater(
                effect_id,
                outcome=outcome,
                evidence=evidence,
                warmth=warmth,
                continuity=continuity,
                friction=friction,
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时回复效果更新失败：{exc}")

    async def _settle_stale_reply_effects(self) -> int:
        expirer = getattr(self.archive, "expire_stale_reply_effects", None)
        if not callable(expirer):
            return 0
        try:
            return await expirer(self._AIR_FEEDBACK_WINDOW_SECONDS)
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 闲时回复效果过期结算失败：{exc}")
            return 0

    def _proactive_reply_was_accepted(
        self,
        event: Any,
        now: datetime.datetime,
        key: str | None = None,
    ) -> bool:
        key = key or self._proactive_scope_key(event)
        if not key:
            return False
        watch = self._proactive_feedback_watches().get(key)
        if not isinstance(watch, dict):
            return False
        sent_at = watch.get("sent_at")
        if not isinstance(sent_at, datetime.datetime):
            return False
        elapsed = (now - sent_at).total_seconds()
        if elapsed <= 0 or elapsed > self._AIR_FEEDBACK_WINDOW_SECONDS:
            return False
        if self._proactive_is_self_message(event):
            return False
        if str(getattr(event, "message_str", "") or "").strip():
            return True
        has_media_checker = getattr(self, "_response_gate_has_media", None)
        return bool(has_media_checker(event)) if callable(has_media_checker) else False

    def _proactive_continuation_window(
        self,
        event: Any,
        now: datetime.datetime,
        key: str | None = None,
    ) -> tuple[int, int, str]:
        if not self._proactive_reply_was_accepted(event, now, key):
            return 0, 0, ""
        text = str(getattr(event, "message_str", "") or "").strip()
        compact = "".join(text.split())
        has_media_checker = getattr(self, "_response_gate_has_media", None)
        has_media = bool(has_media_checker(event)) if callable(has_media_checker) else False
        items_getter = getattr(self, "_event_message_items", None)
        item_count = len(items_getter(event)) if callable(items_getter) else 0
        line_count = sum(1 for line in text.splitlines() if line.strip())
        text_length = len(compact)
        if not compact and not has_media:
            return 0, 0, ""
        if not has_media and text_length <= 2 and item_count <= 1:
            return 0, 0, ""

        score = 0
        if text_length >= 3:
            score += 1
        if text_length >= 10:
            score += 1
        if text_length >= 18:
            score += 1
        if line_count >= 2:
            score += 1
        if item_count >= 2:
            score += 1
        if has_media:
            score += 1

        if score <= 0:
            return 0, 0, ""
        if score >= 3:
            return 2, 8 * 60, "闲时回复被用户认真接住，顺着当前话题自然多聊两句"
        return 1, 5 * 60, "闲时回复刚被用户接住，顺势回应一轮更自然"

    async def _observe_proactive_reply_effect(
        self,
        event: Any,
        now: datetime.datetime,
    ) -> None:
        key = self._proactive_scope_key(event)
        if not key:
            return
        watch = self._proactive_feedback_watches().get(key)
        if not isinstance(watch, dict):
            return
        sent_at = watch.get("sent_at")
        if not isinstance(sent_at, datetime.datetime):
            self._proactive_feedback_watches().pop(key, None)
            return
        elapsed = (now - sent_at).total_seconds()
        if elapsed <= 0:
            return
        if elapsed > self._AIR_FEEDBACK_WINDOW_SECONDS:
            self._proactive_feedback_watches().pop(key, None)
            state = self._get_proactive_air_state(key)
            state["last_effect"] = "ignored"
            state["silence_inertia"] = min(100, int(state.get("silence_inertia") or 0) + 20)
            state["silence_reason"] = "闲时续话后一段时间内没有新的可见回应"
            await self._save_proactive_behavior_feedback(
                key,
                date=now.strftime("%Y-%m-%d"),
                target_id=str(watch.get("target_scope") or key),
                action="闲时续话",
                result="ignored",
                feedback="闲时续话后一段时间内没有新的可见回应",
                reason=str(watch.get("reason") or ""),
                score=-1.0,
            )
            await self._update_reply_effect_watch(
                watch,
                outcome="ignored",
                evidence="闲时续话后一段时间内没有新的可见回应",
                warmth=35,
                continuity=25,
                friction=10,
            )
            return
        if not self._proactive_reply_was_accepted(event, now, key):
            return
        message = str(getattr(event, "message_str", "") or "").strip()
        turns, seconds, reason = self._proactive_continuation_window(event, now, key)
        continuation_marked = bool(watch.get("continuation_marked"))
        self._proactive_feedback_watches().pop(key, None)
        marker = getattr(self, "_response_gate_mark_continuation", None)
        if not continuation_marked and callable(marker) and turns > 0:
            marker(
                self._response_gate_scope_key(event),
                now,
                reason=reason,
                turns=turns,
                seconds=seconds,
            )
        state = self._get_proactive_air_state(key)
        state["last_effect"] = "positive"
        state["quiet_count"] = 0
        state["silence_inertia"] = 0
        state["silence_reason"] = ""
        state["backoff_until"] = None
        state["wait_until"] = None
        await self._save_proactive_behavior_feedback(
            key,
            date=now.strftime("%Y-%m-%d"),
            target_id=str(watch.get("target_scope") or key),
            action="闲时续话",
            result="positive",
            feedback="闲时续话后会话继续有新回应",
            reason=f"后续消息：{message[:120]}",
            score=1.0,
        )
        await self._update_reply_effect_watch(
            watch,
            outcome="positive",
            evidence=f"闲时续话后会话继续有新回应：{message[:80]}",
            warmth=70,
            continuity=70,
            friction=0,
        )
