import asyncio
import datetime
import uuid
from typing import Any

from astrbot.api import logger

from ....clock import now as life_now
from ....life.tools import extract_json_from_text
from ....models import BehaviorFeedbackRecord, BehaviorSceneRecord, ReplyEffectRecord
from ....prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
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
        scene: str = "闲时回复读空气",
        source: str = "proactive_reply",
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
                    scene=scene,
                    action=action,
                    feedback=feedback,
                    result=result,
                    score=score,
                    reason=reason,
                    source=source,
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
        *,
        source: str = "",
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
            "source": str(source or payload.get("source") or "proactive_reply").strip()
            or "proactive_reply",
        }

    async def note_regular_reply_effect(self, event: Any) -> bool:
        if event is None or self._event_has_command_handler(event):
            return False
        key = self._proactive_scope_key(event)
        if not key or key in self._proactive_feedback_watches():
            return False
        result_reader = getattr(self, "_structured_result_text", None)
        if not callable(result_reader):
            return False
        reply_text, _ = result_reader(event)
        reply_text = str(reply_text or "").strip()
        if not reply_text:
            return False
        now = life_now()
        payload = {
            "target_message_id": self._event_message_id(event),
            "reason": "普通聊天回复后的自然效果观察",
            "source": "regular_reply",
        }
        self._track_proactive_reply_effect(
            key, event, payload, reply_text, now, source="regular_reply"
        )
        scheduler = getattr(self, "_schedule_background_task", None)
        if not callable(scheduler):
            await self._save_pending_reply_effect(key, event, payload, reply_text)
            return True
        message_id = self._event_message_id(event) or str(int(now.timestamp()))
        return bool(
            scheduler(
                self._save_pending_reply_effect(key, event, payload, reply_text),
                label="普通回复效果记录",
                key=f"regular_reply_effect:{key}:{message_id}",
                category="chat",
            )
        )

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
                    target_message_id=str(
                        payload.get("target_message_id")
                        or self._event_message_id(event)
                        or ""
                    ),
                    reply_text=reply_text,
                    outcome="pending",
                    reason=str(payload.get("reason") or "").strip(),
                    source=str(payload.get("source") or "proactive_reply").strip()
                    or "proactive_reply",
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

    @staticmethod
    def _reply_effect_source_labels(source: str) -> tuple[str, str]:
        return {
            "regular_reply": ("普通回复", "普通对话回应"),
            "private_revisit": ("私聊回访", "私聊回访效果"),
            "proactive_reply": ("闲时续话", "闲时回复读空气"),
        }.get(source, ("回复", "对话回应"))

    async def _classify_reply_effect(
        self, watch: dict[str, Any], event: Any
    ) -> dict[str, Any]:
        source = str(watch.get("source") or "proactive_reply").strip()
        provider_getter = getattr(self, "_get_proactive_provider", None)
        if not callable(provider_getter):
            return {
                "outcome": "neutral",
                "confidence": 0.0,
                "warmth": 50,
                "continuity": 50,
                "friction": 0,
                "reason": "没有可用的语义评估模型",
            }
        provider = await provider_getter()
        if not provider:
            return {
                "outcome": "neutral",
                "confidence": 0.0,
                "warmth": 50,
                "continuity": 50,
                "friction": 0,
                "reason": "没有可用的语义评估模型",
            }
        fixed = f"""评估一条回复之后，用户的新消息体现出的真实互动效果。

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON：
{{"outcome":"positive|neutral|negative","confidence":0.0,"warmth":0,"continuity":0,"friction":0,"reason":"简短依据"}}

判断边界：
- positive 表示用户确实接住、认可或自然推进了这条回复。
- neutral 表示普通接续、话题自然结束、证据不足或无法判断。
- negative 表示用户明确表现出反感、纠正、拒绝或互动受阻。
- 不能仅因为用户发了下一条消息就判定 positive，也不能根据固定词语或标点套结论。"""
        action, _ = self._reply_effect_source_labels(source)
        dynamic = f"""回复类型：{action}
Bot 上一条回复：{str(watch.get('reply_text') or '').strip()}
用户后续消息：{str(getattr(event, 'message_str', '') or '').strip() or '仅包含媒体'}"""
        session_id = f"daily_life_reply_effect_{uuid.uuid4().hex[:8]}"
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
                timeout=5.0,
            )
            payload = extract_json_from_text(raw)
            if not isinstance(payload, dict):
                raise ValueError("模型未返回有效对象")
            outcome = str(payload.get("outcome") or "neutral").strip().lower()
            if outcome not in {"positive", "neutral", "negative"}:
                outcome = "neutral"
            try:
                confidence = max(0.0, min(float(payload.get("confidence") or 0), 1.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < 0.72:
                outcome = "neutral"

            def level(name: str, default: int) -> int:
                try:
                    return max(0, min(int(payload.get(name)), 100))
                except (TypeError, ValueError):
                    return default

            return {
                "outcome": outcome,
                "confidence": confidence,
                "warmth": level("warmth", 50),
                "continuity": level("continuity", 50),
                "friction": level("friction", 0),
                "reason": str(payload.get("reason") or "").strip()
                or "后续互动语义评估完成",
            }
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 回复效果语义评估跳过：{type(exc).__name__}: {exc}")
            return {
                "outcome": "neutral",
                "confidence": 0.0,
                "warmth": 50,
                "continuity": 50,
                "friction": 0,
                "reason": "语义证据不足，按自然接续处理",
            }
        finally:
            await self.close_text_session(session_id)

    async def _learn_repeated_reply_effect(
        self,
        key: str,
        *,
        source: str,
        outcome: str,
        confidence: float,
        now: datetime.datetime,
    ) -> None:
        if outcome not in {"positive", "negative"} or confidence < 0.72:
            return
        getter = getattr(self.archive, "get_reply_effects", None)
        saver = getattr(self.archive, "upsert_behavior_scene", None)
        if not callable(getter) or not callable(saver):
            return
        effects = await getter(limit=8, scope=key)
        matching = [
            item
            for item in effects
            if str(getattr(item, "source", "") or "") == source
            and str(getattr(item, "outcome", "") or "") == outcome
        ]
        if len(matching) < 3:
            return
        action, scene_label = self._reply_effect_source_labels(source)
        positive = outcome == "positive"
        await saver(
            BehaviorSceneRecord(
                scope=key,
                scene=scene_label,
                cues=[f"同类{action}连续获得明确的{'正向' if positive else '负向'}反馈"],
                preferred_action="reply" if positive else "observe",
                avoid_action="" if positive else "reply",
                outcome_hint="只在重复且明确的互动结果下调整，不根据单次沉默推断。",
                confidence=min(0.9, confidence),
                support_count=1,
                last_seen=now.strftime("%Y-%m-%d"),
                source="reply_effect",
            )
        )

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
        has_media = (
            bool(has_media_checker(event)) if callable(has_media_checker) else False
        )
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
            source = str(watch.get("source") or "proactive_reply")
            action, _ = self._reply_effect_source_labels(source)
            silence_feedback = f"{action}后会话自然结束，没有足够证据判断好坏"
            await self._update_reply_effect_watch(
                watch,
                outcome="neutral",
                evidence=silence_feedback,
                warmth=50,
                continuity=45,
                friction=0,
            )
            return
        if not self._proactive_reply_was_accepted(event, now, key):
            return
        source = str(watch.get("source") or "proactive_reply")
        is_proactive = source in {"proactive_reply", "private_revisit"}
        assessment = await self._classify_reply_effect(watch, event)
        outcome = str(assessment["outcome"])
        confidence = float(assessment["confidence"])
        turns, seconds, reason = self._proactive_continuation_window(event, now, key)
        self._proactive_feedback_watches().pop(key, None)
        marker = getattr(self, "_response_gate_mark_continuation", None)
        if (
            is_proactive
            and outcome == "positive"
            and callable(marker)
            and turns > 0
        ):
            marker(
                self._response_gate_scope_key(event),
                now,
                reason=reason,
                turns=turns,
                seconds=seconds,
            )
        state = self._get_proactive_air_state(key)
        if is_proactive and outcome == "positive":
            state["last_effect"] = "positive"
            state["quiet_count"] = 0
            state["silence_inertia"] = 0
            state["silence_reason"] = ""
            state["backoff_until"] = None
            state["wait_until"] = None
        elif is_proactive and outcome == "negative":
            state["last_effect"] = "negative"
            state["quiet_count"] = int(state.get("quiet_count") or 0) + 1
            state["silence_inertia"] = min(
                100, int(state.get("silence_inertia") or 0) + 24
            )
            state["silence_reason"] = str(assessment["reason"])
            state["wait_until"] = None
            backoff_seconds = self._air_backoff_seconds(
                max(state["quiet_count"], self._AIR_BACKOFF_START_COUNT)
            )
            state["backoff_until"] = (
                now + datetime.timedelta(seconds=backoff_seconds)
                if backoff_seconds > 0
                else None
            )
        action, scene = self._reply_effect_source_labels(source)
        feedback = str(assessment["reason"])
        if outcome in {"positive", "negative"}:
            await self._save_proactive_behavior_feedback(
                key,
                date=now.strftime("%Y-%m-%d"),
                target_id=key,
                action=action,
                result=outcome,
                feedback=feedback,
                reason="语义评估置信度达到学习阈值",
                score=1.0 if outcome == "positive" else -1.0,
                scene=scene,
                source=source,
            )
        await self._update_reply_effect_watch(
            watch,
            outcome=outcome,
            evidence=feedback,
            warmth=int(assessment["warmth"]),
            continuity=int(assessment["continuity"]),
            friction=int(assessment["friction"]),
        )
        pacing = getattr(self, "_note_chat_pacing_effect", None)
        if callable(pacing):
            pacing(key, outcome)
        await self._learn_repeated_reply_effect(
            key,
            source=source,
            outcome=outcome,
            confidence=confidence,
            now=now,
        )
