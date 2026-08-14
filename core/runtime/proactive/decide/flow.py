import datetime
import uuid
from typing import Any

from astrbot.api import logger

from ....clock import now as life_now
from ....life.people import PROACTIVE_PERSON_TEXT_PATHS
from ....life.tools import extract_json_from_text
from ...markers import LOG_PREFIX


class ProactiveFlowMixin:
    @staticmethod
    def _proactive_skip(reason: str, *, handled: bool = False) -> dict[str, Any]:
        return {
            "should_reply": False,
            "handled": handled,
            "decision": "skip",
            "reason": reason,
        }

    def _proactive_group_anchor(
        self, event: Any, readiness: dict[str, Any], payload: dict[str, Any]
    ) -> bool:
        if not self._event_is_group_message(event):
            return True
        target_message_id = str(payload.get("target_message_id") or "").strip()
        target_topic = str(payload.get("target_topic") or "").strip()
        if not target_message_id:
            target_message_id = str(readiness.get("target_message_id") or "").strip()
        if not target_topic:
            target_topic = str(readiness.get("target_topic") or "").strip()
        if not target_message_id:
            target_message_id = self._event_message_id(event)
        if not target_topic:
            target_topic = str(getattr(event, "message_str", "") or "").strip()[:80]
        if target_message_id:
            payload["target_message_id"] = target_message_id
        if target_topic:
            payload["target_topic"] = target_topic
        return bool(target_message_id or target_topic)

    def _normalize_proactive_payload(
        self,
        event: Any,
        readiness: dict[str, Any],
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        confidence = self._clamp_float(payload.get("confidence"))
        reply_text = self._proactive_reply_text(payload.get("reply_text"))
        requested = self._proactive_bool(payload.get("should_reply"))
        expression_passed = self._expression_review_passed(payload)
        utility, utility_valid = self._normalize_proactive_utility(
            payload, confidence=confidence
        )
        reason_code = "proposal_approved"
        if not requested:
            reason_code = "model_declined"
        elif confidence < self.config.proactive.min_confidence:
            reason_code = "confidence_below_threshold"
        elif not expression_passed:
            reason_code = "expression_review_failed"
        elif not reply_text:
            reason_code = "empty_reply"
        elif not utility_valid:
            reason_code = "invalid_utility_scores"
        elif utility < self._PROACTIVE_UTILITY_THRESHOLD:
            reason_code = "utility_below_threshold"
        should_reply = (
            requested
            and confidence >= self.config.proactive.min_confidence
            and expression_passed
            and bool(reply_text)
            and utility_valid
            and utility >= self._PROACTIVE_UTILITY_THRESHOLD
        )
        if requested and reason_code in {
            "invalid_utility_scores",
            "utility_below_threshold",
        }:
            payload["decision"] = "observe"
            payload["reason"] = (
                "主动收益不足，继续观察以避免打扰"
                if utility_valid
                else "主动收益评分不完整，暂不发送"
            )
        if should_reply and not self._proactive_group_anchor(event, readiness, payload):
            should_reply = False
            payload["decision"] = "observe"
            payload["reason"] = "群聊闲时回复缺少自然承接点"
            reason_code = "group_anchor_missing"
        payload.update(
            {
                "should_reply": should_reply,
                "handled": True,
                "confidence": confidence,
                "stage": "proposal",
                "reason_code": reason_code,
                "reply_text": reply_text if should_reply else "",
            }
        )
        return payload, reply_text

    async def _record_proactive_decision(
        self,
        event: Any,
        sender_name: str,
        payload: dict[str, Any],
        reply_text: str,
        now: datetime.datetime,
    ) -> None:
        await self._save_proactive_expression_records(event, payload, reply_text)
        try:
            await self._save_proactive_decision(
                event,
                sender_name,
                payload,
                now,
                sent=False,
                reply_text=reply_text,
                stage="proposal",
            )
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 闲时回复审计记录失败：{exc}")
        if not payload.get("should_reply"):
            return
        chat_mode = "群聊" if self._event_is_group_message(event) else "私聊"
        logger.debug(
            f"{LOG_PREFIX} 闲时回复{chat_mode}裁定通过：长度={len(reply_text)}"
        )
        mark_changed = getattr(self, "mark_page_status_changed", None)
        if not callable(mark_changed):
            return
        try:
            await mark_changed("proactive_reply_decision")
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 闲时回复面板刷新通知失败：{exc}")

    async def _commit_proactive_decision(
        self,
        event: Any,
        sender_name: str,
        payload: dict[str, Any],
        reply_text: str,
        now: datetime.datetime,
        *,
        source: str = "proactive_reply",
    ) -> None:
        """在主动消息实际发送成功后提交决策和记忆。

        Args:
            event: 作为发送依据的消息事件。
            sender_name: 会话对象名称。
            payload: 已通过审计的主动裁定。
            reply_text: 实际发送文本。
            now: 发送成功时间。
            source: 主动消息来源。
        """
        committed = dict(payload)
        committed.update(
            {
                "stage": "commit",
                "reason_code": "send_succeeded",
                "source": source,
                "should_reply": True,
            }
        )
        try:
            await self._save_proactive_decision(
                event,
                sender_name,
                committed,
                now,
                sent=True,
                reply_text=reply_text,
                stage="commit",
            )
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 闲时回复发送提交记录失败：{exc}")

    async def evaluate_proactive_reply(
        self,
        event: Any,
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        if not self._proactive_allowed_for_event(event):
            return self._proactive_skip("未启用或不适合闲时回复")
        now = now or life_now()
        if not self._event_is_group_message(event):
            interaction_context = await self.resolve_interaction_context(
                event=event,
                now=now,
            )
            if interaction_context.mode == "co_present":
                return {
                    "should_reply": False,
                    "handled": True,
                    "decision": "observe",
                    "reason": "双方当前同处现场，不额外发起线上闲时回复",
                    "reason_code": "co_present_interaction",
                }
        remaining = self._proactive_cooldown_remaining(event, now)
        if remaining > 0:
            return {
                "should_reply": False,
                "handled": True,
                "decision": "cooldown",
                "reason": f"闲时回复冷却中，还剩 {remaining} 秒",
            }
        key = self._proactive_scope_key(event)
        pending_count = max(1, int(getattr(event, "proactive_pending_count", 1) or 1))
        air_delay = (
            self._proactive_air_delay_remaining(key, now, pending_count=pending_count)
            if key
            else 0
        )
        if air_delay > 0:
            return {
                "should_reply": False,
                "handled": True,
                "decision": "air_delay",
                "reason": f"会话空气正在等待或退避，还剩 {air_delay} 秒",
                "retry_after": air_delay,
            }
        readiness = await self._proactive_readiness_check(
            event, now, pending_count=pending_count
        )
        if not readiness.get("should_evaluate", False):
            if key:
                self._update_proactive_air_after_decision(
                    key, readiness, now, sent=False
                )
            return readiness
        provider = await self._get_proactive_provider()
        if not provider:
            return self._proactive_skip("没有可用模型")

        sender_name = await self.contact_resolver.resolve_event_sender(event)
        target_date_str, using_extended_night, day = await self._proactive_current_day(
            now
        )
        session_id = f"daily_life_proactive_{uuid.uuid4().hex[:8]}"
        prompt = await self._build_proactive_prompt(
            event,
            sender_name,
            now,
            day,
            target_date_str,
            using_extended_night,
            readiness,
        )
        try:
            provider_id = self.config.proactive.provider
            text = await self.call_text_model(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            payload = extract_json_from_text(text)
            if not isinstance(payload, dict):
                return self._proactive_skip("模型未返回有效裁定")
            payload, reply_text = self._normalize_proactive_payload(
                event, readiness, payload
            )
            if payload["should_reply"]:
                auditor = getattr(
                    getattr(self, "composer", None), "_audit_person_payload", None
                )
                if callable(auditor):
                    person_facts = await self.composer._build_person_fact_context(
                        persona=await self._current_proactive_persona(
                            self._event_session_id(event)
                        ),
                        explicit_instruction=str(
                            getattr(event, "message_str", "") or ""
                        ),
                    )
                    audit = await auditor(
                        payload,
                        context=person_facts,
                        patterns=PROACTIVE_PERSON_TEXT_PATHS,
                        provider=provider,
                        provider_id=provider_id,
                        subject="闲时回复",
                    )
                    if audit.unresolved:
                        payload.update(
                            {
                                "should_reply": False,
                                "decision": "observe",
                                "reason": "人物事实尚未核对清楚",
                                "reply_text": "",
                            }
                        )
                        reply_text = ""
                    else:
                        payload, reply_text = self._normalize_proactive_payload(
                            event, readiness, audit.payload
                        )
            if not payload["should_reply"]:
                self._update_proactive_air_after_decision(key, payload, now, sent=False)
            await self._record_proactive_decision(
                event, sender_name, payload, reply_text, now
            )
            return payload
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 闲时回复裁定失败：{exc}")
            return self._proactive_skip(f"闲时回复裁定失败：{exc}")
        finally:
            await self.close_text_session(session_id)

    async def evaluate_idle_proactive_candidates(
        self,
        now: datetime.datetime | None = None,
    ) -> None:
        if not self._proactive_idle_enabled():
            return
        candidates = self._proactive_idle_candidates
        if not candidates:
            return
        now = now or life_now()
        for key, candidate in list(candidates.items()):
            if not self._idle_candidate_ready(candidate, now):
                if not isinstance(candidate, dict) or not isinstance(
                    candidate.get("last_activity_at"), datetime.datetime
                ):
                    candidates.pop(key, None)
                continue
            await self._evaluate_idle_candidate(key, candidate, now)

    def _idle_candidate_ready(self, candidate: Any, now: datetime.datetime) -> bool:
        if not isinstance(candidate, dict):
            return False
        last_activity_at = candidate.get("last_activity_at")
        if not isinstance(last_activity_at, datetime.datetime):
            return False
        if self._proactive_observe_hold_remaining(candidate, now) > 0:
            return False
        return int((now - last_activity_at).total_seconds()) >= (
            self._proactive_idle_seconds(candidate)
        )

    async def _evaluate_idle_candidate(
        self, key: str, candidate: dict[str, Any], now: datetime.datetime
    ) -> None:
        revision = int(candidate.get("revision") or 0)
        self._transition_proactive_lifecycle(
            key,
            "considering",
            event="evaluation_started",
            reason="静默门槛已满足，开始主动性裁定",
            now=now,
            revision=revision,
            candidate=candidate,
        )
        event = self._proactive_candidate_event(candidate)
        decision = await self.evaluate_proactive_reply(event, now=now)
        reply_text = str(decision.get("reply_text") or "").strip()
        decision_name = decision.get("decision")
        current = self._proactive_idle_candidates.get(key)
        if current is not candidate or int(current.get("revision") or 0) != revision:
            await self._advance_proactive_decision_trace(
                event,
                decision,
                stage="interrupted",
                reason_code="conversation_revision_changed",
                outcome="裁定期间会话版本已变化",
            )
            self._transition_proactive_lifecycle(
                key,
                "interrupted",
                event="conversation_revision_changed",
                reason="裁定期间会话版本已变化",
                now=now,
                revision=revision,
                candidate=candidate,
                publish=False,
            )
            self._record_virtual_life_metric("idle_revision_interrupted")
            return
        if not self._proactive_chat_enabled(self._event_is_group_message(event)):
            await self._advance_proactive_decision_trace(
                event,
                decision,
                stage="abandoned",
                reason_code="channel_disabled",
                outcome="发送前主动回复通道已关闭",
            )
            self._transition_proactive_lifecycle(
                key,
                "abandoned",
                event="channel_disabled",
                reason="主动回复通道已关闭",
                now=now,
                revision=revision,
                candidate=candidate,
            )
            self._proactive_idle_candidates.pop(key, None)
            return
        if decision.get("should_reply") and not self._event_is_group_message(event):
            latest_interaction = await self.resolve_interaction_context(
                event=event,
                now=life_now(),
            )
            if latest_interaction.mode == "co_present":
                decision.update(
                    {
                        "should_reply": False,
                        "decision": "observe",
                        "reason": "发送前确认双方已同处现场，不再发送线上闲时回复",
                        "reason_code": "co_present_before_send",
                        "reply_text": "",
                    }
                )
                reply_text = ""
                decision_name = "observe"
        if decision_name in {"cooldown", "air_delay", "wait"}:
            retry_after = max(60, int(decision.get("retry_after") or 60))
            candidate["next_evaluation_at"] = now + datetime.timedelta(
                seconds=retry_after
            )
            candidate["state"] = "reevaluate_after_silence"
            target_state = "cooldown" if decision_name == "cooldown" else "waiting"
            self._transition_proactive_lifecycle(
                key,
                target_state,
                event="decision_deferred",
                reason=str(decision.get("reason") or "等待更合适的时机"),
                now=now,
                revision=revision,
                candidate=candidate,
            )
            await self._advance_proactive_decision_trace(
                event,
                decision,
                stage=target_state,
                reason_code=str(decision.get("reason_code") or "decision_deferred"),
                outcome=str(decision.get("reason") or "等待更合适的时机"),
            )
            return
        if decision.get("should_reply") and reply_text:
            self._transition_proactive_lifecycle(
                key,
                "sending",
                event="proposal_approved",
                reason=str(decision.get("reason") or "主动收益与连续性通过"),
                now=now,
                revision=revision,
                candidate=candidate,
            )
            await self._advance_proactive_decision_trace(
                event,
                decision,
                stage="sending",
                reason_code="proposal_approved",
                outcome="准备发送主动消息",
            )
            sent = await self._send_proactive_message(
                str(candidate.get("target_scope") or ""),
                reply_text,
                "闲时回复发送失败",
                send_payload={**decision, "source": "proactive_reply"},
                source_event=event,
            )
            if sent:
                sender_name = str(candidate.get("sender_name") or "").strip()
                await self._commit_proactive_decision(
                    event,
                    sender_name,
                    decision,
                    reply_text,
                    now,
                )
                self._mark_proactive_reply_sent(event, now)
                self._transition_proactive_lifecycle(
                    key,
                    "engaged",
                    event="send_succeeded",
                    reason="主动消息已实际送达，开始观察互动效果",
                    now=now,
                    revision=revision,
                    candidate=candidate,
                )
                self._track_proactive_reply_effect(
                    key, event, decision, reply_text, now
                )
                await self._save_pending_reply_effect(key, event, decision, reply_text)
                self._proactive_idle_candidates.pop(key, None)
                self._record_virtual_life_metric("idle_reply_sent")
                return
            latest = self._proactive_idle_candidates.get(key)
            interrupted = (
                latest is not candidate
                or int((latest or {}).get("revision") or 0) != revision
            )
            self._transition_proactive_lifecycle(
                key,
                "interrupted" if interrupted else "abandoned",
                event=(
                    "conversation_revision_changed" if interrupted else "send_failed"
                ),
                reason=(
                    "发送期间会话版本已变化" if interrupted else "主动消息未能成功发送"
                ),
                now=now,
                revision=revision,
                candidate=candidate,
                publish=not interrupted,
            )
            await self._advance_proactive_decision_trace(
                event,
                decision,
                stage="interrupted" if interrupted else "abandoned",
                reason_code=(
                    "conversation_revision_changed" if interrupted else "send_failed"
                ),
                outcome=(
                    "发送期间会话版本已变化" if interrupted else "主动消息发送失败"
                ),
            )
            self._update_proactive_air_after_decision(
                key,
                {
                    "decision": "observe",
                    "reason": "闲时回复发送失败，暂时收回续话意愿",
                },
                now,
                sent=False,
            )
            self._proactive_idle_candidates.pop(key, None)
            self._record_virtual_life_metric("idle_send_failed")
            return
        if decision_name == "observe":
            candidate["observe_hold_until"] = now + datetime.timedelta(
                seconds=self._proactive_observe_hold_seconds()
            )
            candidate["next_evaluation_at"] = candidate["observe_hold_until"]
            candidate["state"] = "reevaluate_after_silence"
            self._transition_proactive_lifecycle(
                key,
                "waiting",
                event="decision_observe",
                reason=str(decision.get("reason") or "继续观察会话空气"),
                now=now,
                revision=revision,
                candidate=candidate,
            )
            await self._advance_proactive_decision_trace(
                event,
                decision,
                stage="waiting",
                reason_code=str(decision.get("reason_code") or "decision_observe"),
                outcome=str(decision.get("reason") or "继续观察会话空气"),
            )
            self._record_virtual_life_metric("idle_observe")
            return
        self._transition_proactive_lifecycle(
            key,
            "abandoned",
            event="decision_abandoned",
            reason=str(decision.get("reason") or "本轮不适合主动续话"),
            now=now,
            revision=revision,
            candidate=candidate,
        )
        await self._advance_proactive_decision_trace(
            event,
            decision,
            stage="abandoned",
            reason_code=str(decision.get("reason_code") or "decision_abandoned"),
            outcome=str(decision.get("reason") or "本轮不适合主动续话"),
        )
        self._proactive_idle_candidates.pop(key, None)
        self._record_virtual_life_metric("turn_abandoned")
