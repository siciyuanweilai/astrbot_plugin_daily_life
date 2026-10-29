from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any, Protocol
from urllib.parse import urlparse

from astrbot.api import logger

from ....clock import now as life_now
from ....life.people import PROACTIVE_PERSON_TEXT_PATHS
from ....life.tools import extract_json_from_text
from ...markers import LOG_PREFIX
from ...contact import contact_rhythm


class ProactiveFlowComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _PROACTIVE_UTILITY_THRESHOLD: Any
    _advance_proactive_decision_trace: Any
    _audit_proactive_continuity: Any
    _build_proactive_prompt: Any
    _clamp_float: Any
    _commit_proactive_decision: Any
    _current_proactive_persona: Any
    _evaluate_idle_candidate: Any
    _event_is_group_message: Any
    _event_message_id: Any
    _event_session_id: Any
    _expression_review_passed: Any
    _format_recent_context_messages: Any
    _get_proactive_provider: Any
    _mark_proactive_reply_sent: Any
    _normalize_proactive_utility: Any
    _proactive_air_delay_remaining: Any
    _proactive_allowed_for_event: Any
    _proactive_bool: Any
    _proactive_candidate_event: Any
    _proactive_chat_enabled: Any
    _proactive_cooldown_remaining: Any
    _proactive_current_day: Any
    _proactive_idle_candidates: Any
    _proactive_idle_enabled: Any
    _proactive_idle_seconds: Any
    _refresh_private_proactive_timing: Any
    _proactive_observe_hold_remaining: Any
    _proactive_observe_hold_seconds: Any
    _proactive_readiness_check: Any
    _proactive_reply_text: Any
    _proactive_scope_key: Any
    _proactive_skip: Any
    _read_recent_context_messages: Any
    _record_virtual_life_metric: Any
    _save_pending_reply_effect: Any
    _save_proactive_decision: Any
    _save_proactive_expression_records: Any
    _send_proactive_message: Any
    _send_proactive_voice_call_invite: Any
    _split_voice_call_invite_message: Any
    _track_proactive_reply_effect: Any
    _transition_proactive_lifecycle: Any
    _update_proactive_air_after_decision: Any
    call_text_model: Any
    close_text_session: Any
    composer: Any
    config: Any
    contact_resolver: Any
    create_voice_call_invite: Any
    evaluate_proactive_reply: Any
    mark_page_status_changed: Any
    resolve_interaction_context: Any
    voice_call: Any


class ProactiveFlowComponent:
    def __init__(self, context: ProactiveFlowComponentContext) -> None:
        self.context = context

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
        if not self.context._event_is_group_message(event):
            return True
        target_message_id = str(payload.get("target_message_id") or "").strip()
        target_topic = str(payload.get("target_topic") or "").strip()
        if not target_message_id:
            target_message_id = str(readiness.get("target_message_id") or "").strip()
        if not target_topic:
            target_topic = str(readiness.get("target_topic") or "").strip()
        if not target_message_id:
            target_message_id = self.context._event_message_id(event)
        if not target_topic:
            target_topic = str(getattr(event, "message_str", "") or "").strip()[:80]
        if target_message_id:
            payload["target_message_id"] = target_message_id
        if target_topic:
            payload["target_topic"] = target_topic
        return bool(target_message_id or target_topic)

    def _normalize_proactive_voice_call_intent(
        self, event: Any, payload: dict[str, Any]
    ) -> None:
        """校验主动通话意愿，确保它不会绕过私聊和通话能力门控。"""

        raw = payload.get("voice_call_intent")
        raw = raw if isinstance(raw, dict) else {}
        requested = self.context._proactive_bool(raw.get("should_invite"))
        greeting = self.context._proactive_reply_text(raw.get("greeting"))[:500]
        reason = str(raw.get("reason") or "").strip()[:240]
        manager = getattr(self.context, "voice_call", None)
        checker = getattr(manager, "proactive_invite_available", None)
        available = False
        if not self.context._event_is_group_message(event) and callable(checker):
            try:
                available = bool(checker(self.context._event_session_id(event)))
            except Exception:
                available = False
        should_invite = (
            requested
            and bool(payload.get("should_reply"))
            and bool(payload.get("reply_text"))
            and available
            and payload.get("_interaction_mode") != "co_present"
        )
        if requested and payload.get("_interaction_mode") == "co_present":
            reason = "双方当前同处现场，不发起线上通话邀请"
        elif requested and not available:
            reason = "实时通话当前不可用，暂不主动邀请"
        elif requested and not payload.get("should_reply"):
            reason = "没有自然的主动回复承接，暂不主动邀请"
        payload["voice_call_intent"] = {
            "should_invite": should_invite,
            "greeting": greeting if should_invite else "",
            "reason": reason or ("此刻没有主动通话理由" if not should_invite else ""),
            "delivered": False,
        }

    @staticmethod
    def _split_voice_call_invite_message(message: str) -> tuple[str, str]:
        """拆分主动通话的提示文本和链接，链接单独投递便于复制。"""

        lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
        for index, line in enumerate(lines):
            parsed = urlparse(line)
            if parsed.scheme in {"https", "http"} and parsed.netloc:
                notice = "\n".join(lines[:index]).strip() or "语音通话邀请已生成。"
                return notice, line
        return "\n".join(lines).strip(), ""

    async def _prepare_proactive_voice_call_invite(
        self, event: Any, payload: dict[str, Any]
    ) -> tuple[str, str] | None:
        intent = payload.get("voice_call_intent")
        if not isinstance(intent, dict) or not intent.get("should_invite"):
            return None
        creator = getattr(self.context, "create_voice_call_invite", None)
        if not callable(creator):
            return None
        try:
            message = await creator(
                event,
                greeting=str(intent.get("greeting") or "").strip(),
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 主动语音邀请创建失败：{type(exc).__name__}")
            return None
        notice, link = self.context._split_voice_call_invite_message(message)
        return (notice, link) if notice and link else None

    def _normalize_proactive_payload(
        self,
        event: Any,
        readiness: dict[str, Any],
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        confidence = self.context._clamp_float(payload.get("confidence"))
        reply_text = self.context._proactive_reply_text(payload.get("reply_text"))
        payload["_expression_candidate_text"] = reply_text
        requested = self.context._proactive_bool(payload.get("should_reply"))
        expression_passed = self.context._expression_review_passed(payload)
        utility, utility_valid = self.context._normalize_proactive_utility(payload)
        reason_code = "proposal_approved"
        if not requested:
            reason_code = "model_declined"
        elif confidence < self.context.config.proactive.min_confidence:
            reason_code = "confidence_below_threshold"
        elif not expression_passed:
            reason_code = "expression_review_failed"
        elif not reply_text:
            reason_code = "empty_reply"
        elif not utility_valid:
            reason_code = "invalid_utility_scores"
        elif utility < self.context._PROACTIVE_UTILITY_THRESHOLD:
            reason_code = "utility_below_threshold"
        if requested and not expression_passed:
            payload["decision"] = "observe"
            payload["reason"] = "表达检查未通过或缺少有效检查，暂缓主动发送"
        should_reply = (
            requested
            and confidence >= self.context.config.proactive.min_confidence
            and expression_passed
            and bool(reply_text)
            and utility_valid
            and utility >= self.context._PROACTIVE_UTILITY_THRESHOLD
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
        self._normalize_proactive_voice_call_intent(event, payload)
        return payload, reply_text

    async def _record_proactive_decision(
        self,
        event: Any,
        sender_name: str,
        payload: dict[str, Any],
        reply_text: str,
        now: datetime.datetime,
    ) -> None:
        await self.context._save_proactive_expression_records(event, payload, reply_text)
        try:
            await self.context._save_proactive_decision(
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
        chat_mode = "群聊" if self.context._event_is_group_message(event) else "私聊"
        logger.debug(
            f"{LOG_PREFIX} 闲时回复{chat_mode}裁定通过：长度={len(reply_text)}"
        )
        mark_changed = getattr(self.context, "mark_page_status_changed", None)
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
            await self.context._save_proactive_decision(
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
        """整轮裁定共用一个等待上限，超时后收回本次续话。"""
        deadline = asyncio.timeout(self.context.config.proactive.semantic_timeout_seconds)
        try:
            async with deadline:
                return await self._evaluate_proactive_reply(event, now=now)
        except TimeoutError:
            if not deadline.expired():
                raise
            logger.debug(f"{LOG_PREFIX} 闲时回复裁定超时，跳过本次")
            return {
                **self.context._proactive_skip("闲时回复裁定超时，跳过本次", handled=True),
                "reason_code": "evaluation_timeout",
                "reply_text": "",
            }

    async def _evaluate_proactive_reply(
        self,
        event: Any,
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        if not self.context._proactive_allowed_for_event(
            event, continuing=bool(getattr(event, "is_proactive_synthetic", False))
        ):
            return self.context._proactive_skip("未启用或不适合闲时回复")
        now = now or life_now()
        if not self.context._event_is_group_message(event):
            rhythm = await contact_rhythm(self.context, self.context._event_session_id(event), now)
            if rhythm.get("ready") is False:
                return {
                    "should_reply": False, "handled": True, "decision": "wait",
                    "reason": "结合最近收发情况，给对方留一点回应空间",
                    "retry_after": rhythm["retry_after"],
                }
        interaction_mode = ""
        if not self.context._event_is_group_message(event):
            interaction_context = await self.context.resolve_interaction_context(
                event=event,
                now=now,
            )
            interaction_mode = interaction_context.mode
        remaining = self.context._proactive_cooldown_remaining(event, now)
        if remaining > 0:
            return {
                "should_reply": False,
                "handled": True,
                "decision": "cooldown",
                "reason": f"闲时回复冷却中，还剩 {remaining} 秒",
            }
        key = self.context._proactive_scope_key(event)
        pending_count = max(1, int(getattr(event, "proactive_pending_count", 1) or 1))
        air_delay = (
            self.context._proactive_air_delay_remaining(key, now, pending_count=pending_count)
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
        readiness = await self.context._proactive_readiness_check(
            event, now, pending_count=pending_count
        )
        if not readiness.get("should_evaluate", False):
            if key:
                self.context._update_proactive_air_after_decision(
                    key, readiness, now, sent=False
                )
            return readiness
        provider = await self.context._get_proactive_provider()
        if not provider:
            return self.context._proactive_skip("没有可用模型")

        sender_name = await self.context.contact_resolver.resolve_event_sender(event)
        target_date_str, using_extended_night, day = await self.context._proactive_current_day(
            now
        )
        session_id = f"daily_life_proactive_{uuid.uuid4().hex[:8]}"
        try:
            prompt = await self.context._build_proactive_prompt(
                event,
                sender_name,
                now,
                day,
                target_date_str,
                using_extended_night,
                readiness,
            )
            provider_id = self.context.config.proactive.provider
            text = await self.context.call_text_model(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            payload = extract_json_from_text(text)
            if not isinstance(payload, dict):
                return self.context._proactive_skip("模型未返回有效裁定")
            payload["_interaction_mode"] = interaction_mode
            payload, reply_text = self._normalize_proactive_payload(
                event, readiness, payload
            )
            if payload["should_reply"]:
                auditor = getattr(
                    getattr(self.context, "composer", None), "_audit_person_payload", None
                )
                if callable(auditor):
                    person_facts = await self.context.composer._build_person_fact_context(
                        persona=await self.context._current_proactive_persona(
                            self.context._event_session_id(event)
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
            if payload["should_reply"]:
                recent_messages = await self.context._read_recent_context_messages(
                    self.context._event_session_id(event), limit=12
                )
                if recent_messages:
                    valid, reason = await self.context._audit_proactive_continuity(
                        payload=payload,
                        recent_context=self.context._format_recent_context_messages(
                            recent_messages, now=now
                        ),
                        provider=provider,
                        provider_id=provider_id,
                    )
                    if not valid:
                        payload.update({
                            "should_reply": False,
                            "decision": "observe",
                            "reason": reason or "主动回复没有自然承接最近交流",
                            "reason_code": "continuity_audit_failed",
                            "reply_text": "",
                        })
                        reply_text = ""
                        self._normalize_proactive_voice_call_intent(event, payload)
            if not payload["should_reply"]:
                self.context._update_proactive_air_after_decision(key, payload, now, sent=False)
            await self._record_proactive_decision(
                event, sender_name, payload, reply_text, now
            )
            return payload
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 闲时回复裁定失败：{exc}")
            return self.context._proactive_skip(f"闲时回复裁定失败：{exc}")
        finally:
            try:
                async with asyncio.timeout(1):
                    await self.context.close_text_session(session_id)
            except TimeoutError:
                pass

    async def evaluate_idle_proactive_candidates(
        self,
        now: datetime.datetime | None = None,
    ) -> None:
        if not self.context._proactive_idle_enabled():
            return
        candidates = self.context._proactive_idle_candidates
        if not candidates:
            return
        now = now or life_now()
        for key, candidate in list(candidates.items()):
            if isinstance(candidate, dict):
                await self.context._refresh_private_proactive_timing(candidate, now)
            if not self._idle_candidate_ready(candidate, now):
                if not isinstance(candidate, dict) or not isinstance(
                    candidate.get("last_activity_at"), datetime.datetime
                ):
                    candidates.pop(key, None)
                continue
            await self.context._evaluate_idle_candidate(key, candidate, now)

    def _idle_candidate_ready(self, candidate: Any, now: datetime.datetime) -> bool:
        if not isinstance(candidate, dict):
            return False
        last_activity_at = candidate.get("last_activity_at")
        if not isinstance(last_activity_at, datetime.datetime):
            return False
        if self.context._proactive_observe_hold_remaining(candidate, now) > 0:
            return False
        due_at = candidate.get("next_evaluation_at")
        if isinstance(due_at, datetime.datetime) and now < due_at:
            return False
        opportunity_at = candidate.get("private_opportunity_at")
        if not candidate.get("is_group") and isinstance(
            opportunity_at, datetime.datetime
        ):
            return now >= opportunity_at
        return int((now - last_activity_at).total_seconds()) >= (
            self.context._proactive_idle_seconds(candidate)
        )

    async def _evaluate_idle_candidate(
        self, key: str, candidate: dict[str, Any], now: datetime.datetime
    ) -> None:
        if candidate.get("evaluation_in_progress"):
            return
        candidate["evaluation_in_progress"] = True
        try:
            await self.context._refresh_private_proactive_timing(candidate, now)
            if not self._idle_candidate_ready(candidate, now):
                return
            await self._run_idle_candidate_evaluation(key, candidate, now)
        finally:
            candidate.pop("evaluation_in_progress", None)

    async def _run_idle_candidate_evaluation(
        self, key: str, candidate: dict[str, Any], now: datetime.datetime
    ) -> None:
        revision = int(candidate.get("revision") or 0)

        def is_current() -> bool:
            current = self.context._proactive_idle_candidates.get(key)
            return current is candidate and int(current.get("revision") or 0) == revision

        if not is_current():
            return
        self.context._transition_proactive_lifecycle(
            key,
            "considering",
            event="evaluation_started",
            reason="来到适合重新考虑联系的窗口，开始主动性裁定",
            now=now,
            revision=revision,
            candidate=candidate,
        )
        event = self.context._proactive_candidate_event(candidate)
        decision = await self.context.evaluate_proactive_reply(event, now=now)
        reply_text = str(decision.get("reply_text") or "").strip()
        decision_name = decision.get("decision")
        if not is_current():
            await self.context._advance_proactive_decision_trace(
                event,
                decision,
                stage="interrupted",
                reason_code="conversation_revision_changed",
                outcome="裁定期间会话版本已变化",
            )
            self.context._transition_proactive_lifecycle(
                key,
                "interrupted",
                event="conversation_revision_changed",
                reason="裁定期间会话版本已变化",
                now=now,
                revision=revision,
                candidate=candidate,
                publish=False,
            )
            self.context._record_virtual_life_metric("idle_revision_interrupted")
            return
        if not self.context._proactive_chat_enabled(self.context._event_is_group_message(event)):
            await self.context._advance_proactive_decision_trace(
                event,
                decision,
                stage="abandoned",
                reason_code="channel_disabled",
                outcome="发送前主动回复通道已关闭",
            )
            self.context._transition_proactive_lifecycle(
                key,
                "abandoned",
                event="channel_disabled",
                reason="主动回复通道已关闭",
                now=now,
                revision=revision,
                candidate=candidate,
            )
            self.context._proactive_idle_candidates.pop(key, None)
            return
        if decision.get("should_reply") and not self.context._event_is_group_message(event):
            latest_interaction = await self.context.resolve_interaction_context(
                event=event,
                now=life_now(),
            )
            if not is_current():
                return
            planned_mode = decision.get("_interaction_mode")
            if planned_mode is not None and latest_interaction.mode != planned_mode:
                decision.update(
                    {
                        "should_reply": False,
                        "decision": "observe",
                        "reason": "发送前现实互动方式已变化，取消旧的主动表达",
                        "reason_code": "interaction_changed_before_send",
                        "reply_text": "",
                    }
                )
                intent = decision.get("voice_call_intent")
                if isinstance(intent, dict):
                    intent.update(
                        {
                            "should_invite": False,
                            "greeting": "",
                            "reason": "现实互动方式已变化，取消旧通话邀请",
                        }
                    )
                reply_text = ""
                decision_name = "observe"
        self._normalize_proactive_voice_call_intent(event, decision)
        if decision_name in {"cooldown", "air_delay", "wait"}:
            retry_after = max(60, int(decision.get("retry_after") or 60))
            candidate["next_evaluation_at"] = now + datetime.timedelta(
                seconds=retry_after
            )
            candidate["state"] = "reevaluate_after_silence"
            target_state = "cooldown" if decision_name == "cooldown" else "waiting"
            self.context._transition_proactive_lifecycle(
                key,
                target_state,
                event="decision_deferred",
                reason=str(decision.get("reason") or "等待更合适的时机"),
                now=now,
                revision=revision,
                candidate=candidate,
            )
            await self.context._advance_proactive_decision_trace(
                event,
                decision,
                stage=target_state,
                reason_code=str(decision.get("reason_code") or "decision_deferred"),
                outcome=str(decision.get("reason") or "等待更合适的时机"),
            )
            return
        voice_invite = await self._prepare_proactive_voice_call_invite(event, decision)
        if not is_current():
            return
        voice_intent = decision.get("voice_call_intent")
        if isinstance(voice_intent, dict) and voice_intent.get("should_invite") and voice_invite is None:
            decision.update(
                {
                    "should_reply": False,
                    "decision": "observe",
                    "reason": "主动通话邀请暂时无法创建，避免只说想打电话却不给入口",
                    "reason_code": "voice_call_invite_unavailable",
                    "reply_text": "",
                }
            )
            voice_intent.update({"should_invite": False, "reason": decision["reason"]})
            self.context._update_proactive_air_after_decision(key, decision, now, sent=False)
            self.context._proactive_idle_candidates.pop(key, None)
            await self.context._advance_proactive_decision_trace(
                event,
                decision,
                stage="abandoned",
                reason_code="voice_call_invite_unavailable",
                outcome=decision["reason"],
            )
            self.context._transition_proactive_lifecycle(
                key,
                "abandoned",
                event="voice_call_invite_unavailable",
                reason=decision["reason"],
                now=now,
                revision=revision,
                candidate=candidate,
            )
            return
        if decision.get("should_reply") and reply_text:
            self.context._transition_proactive_lifecycle(
                key,
                "sending",
                event="proposal_approved",
                reason=str(decision.get("reason") or "主动收益与连续性通过"),
                now=now,
                revision=revision,
                candidate=candidate,
            )
            await self.context._advance_proactive_decision_trace(
                event,
                decision,
                stage="sending",
                reason_code="proposal_approved",
                outcome="准备发送主动消息",
            )
            if not is_current():
                return
            delivery_payload = {**decision, "source": "proactive_reply"}
            sent = await self.context._send_proactive_message(
                str(candidate.get("target_scope") or ""),
                reply_text,
                "闲时回复发送失败",
                send_payload=delivery_payload,
                source_event=event,
            )
            delivery = delivery_payload.get("_delivery") or {}
            if delivery:
                decision["_delivery"] = delivery
            if sent:
                reply_text = str(delivery.get("sent_text") or reply_text)
                if voice_invite is not None:
                    notice, link = voice_invite
                    invite_sent = await self.context._send_proactive_voice_call_invite(
                        str(candidate.get("target_scope") or ""),
                        notice,
                        link,
                        source_event=event,
                    )
                    intent = decision.get("voice_call_intent")
                    if isinstance(intent, dict):
                        intent["delivered"] = invite_sent
                    if not invite_sent:
                        logger.warning(f"{LOG_PREFIX} 主动语音邀请链接发送失败")
                sender_name = str(candidate.get("sender_name") or "").strip()
                await self.context._commit_proactive_decision(
                    event,
                    sender_name,
                    decision,
                    reply_text,
                    now,
                )
                self.context._mark_proactive_reply_sent(event, now)
                if is_current():
                    self.context._transition_proactive_lifecycle(
                        key,
                        "engaged",
                        event="send_succeeded",
                        reason="主动消息已实际送达，开始观察互动效果",
                        now=now,
                        revision=revision,
                        candidate=candidate,
                    )
                self.context._track_proactive_reply_effect(
                    key, event, decision, reply_text, now
                )
                await self.context._save_pending_reply_effect(key, event, decision, reply_text)
                if is_current():
                    if candidate.get("is_group"):
                        self.context._proactive_idle_candidates.pop(key, None)
                    else:
                        # 私聊继续生活与观察；一次联系不会终止后续主动性。
                        sent_at = life_now()
                        candidate.update(
                            last_activity_at=sent_at, last_bot_reply_at=sent_at,
                            pending_count=0, next_evaluation_at=None,
                            revision=revision + 1,
                            state="after_proactive_reply", decision="",
                            decision_reason="主动联系已送达，继续留意生活与交流变化",
                        )
                        for field in ("private_opportunity_at", "observe_hold_until", "idle_observe_count"):
                            candidate.pop(field, None)
                        self.context._transition_proactive_lifecycle(
                            key,
                            "cooldown",
                            event="contact_round_completed",
                            reason="本轮主动联系已完成，结合收发情况等待下一个生活窗口",
                            now=sent_at,
                            revision=revision + 1,
                            candidate=candidate,
                        )
                self.context._record_virtual_life_metric("idle_reply_sent")
                return
            latest = self.context._proactive_idle_candidates.get(key)
            interrupted = (
                latest is not candidate
                or int((latest or {}).get("revision") or 0) != revision
                or delivery.get("status") == "interrupted"
            )
            status = "interrupted" if interrupted else str(delivery.get("status") or "failed")
            stage = "waiting" if status == "deferred" else (
                "interrupted" if interrupted else "abandoned"
            )
            reason = str(delivery.get("reason") or "主动消息未能成功发送")
            code = str(delivery.get("reason_code") or "send_failed")
            self.context._transition_proactive_lifecycle(
                key, stage, event=code, reason=reason, now=now,
                revision=revision, candidate=candidate, publish=not interrupted,
            )
            await self.context._advance_proactive_decision_trace(
                event, decision, stage=stage, reason_code=code, outcome=reason,
            )
            if is_current():
                if status == "deferred":
                    candidate["next_evaluation_at"] = now + datetime.timedelta(
                        seconds=self.context._proactive_observe_hold_seconds()
                    )
                else:
                    if status == "failed":
                        self.context._update_proactive_air_after_decision(
                            key, {"decision": "observe", "reason": reason}, now, sent=False,
                        )
                    # 结果不明时收起本次提案，等待新证据，避免重复发送。
                    self.context._proactive_idle_candidates.pop(key, None)
            self.context._record_virtual_life_metric(f"idle_send_{status}")
            return
        if decision_name == "observe":
            hold_seconds = self.context._proactive_observe_hold_seconds()
            if not candidate.get("is_group"):
                count = min(4, int(candidate.get("idle_observe_count") or 0) + 1)
                candidate["idle_observe_count"] = count
                hold_seconds = max(
                    hold_seconds,
                    int(
                        self.context._proactive_idle_seconds(candidate)
                        * min(1.0, 0.3 + 0.15 * count)
                    ),
                )
            candidate["observe_hold_until"] = now + datetime.timedelta(
                seconds=hold_seconds
            )
            candidate["next_evaluation_at"] = candidate["observe_hold_until"]
            candidate["state"] = "reevaluate_after_silence"
            self.context._transition_proactive_lifecycle(
                key,
                "waiting",
                event="decision_observe",
                reason=str(decision.get("reason") or "继续观察会话空气"),
                now=now,
                revision=revision,
                candidate=candidate,
            )
            await self.context._advance_proactive_decision_trace(
                event,
                decision,
                stage="waiting",
                reason_code=str(decision.get("reason_code") or "decision_observe"),
                outcome=str(decision.get("reason") or "继续观察会话空气"),
            )
            self.context._record_virtual_life_metric("idle_observe")
            return
        self.context._transition_proactive_lifecycle(
            key,
            "abandoned",
            event="decision_abandoned",
            reason=str(decision.get("reason") or "本轮不适合主动续话"),
            now=now,
            revision=revision,
            candidate=candidate,
        )
        await self.context._advance_proactive_decision_trace(
            event,
            decision,
            stage="abandoned",
            reason_code=str(decision.get("reason_code") or "decision_abandoned"),
            outcome=str(decision.get("reason") or "本轮不适合主动续话"),
        )
        if is_current():
            self.context._proactive_idle_candidates.pop(key, None)
        self.context._record_virtual_life_metric("turn_abandoned")
