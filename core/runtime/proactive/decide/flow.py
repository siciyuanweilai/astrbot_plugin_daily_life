import datetime
import uuid
from typing import Any

from astrbot.api import logger

from ....clock import now as life_now
from ....life.tools import extract_json_from_text
from ...markers import LOG_PREFIX


class ProactiveFlowMixin:

    async def evaluate_proactive_reply(
        self,
        event: Any,
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        if not self._proactive_allowed_for_event(event):
            return {"should_reply": False, "handled": False, "decision": "skip", "reason": "未启用或不适合闲时回复"}
        now = now or life_now()
        remaining = self._proactive_cooldown_remaining(event, now)
        if remaining > 0:
            return {"should_reply": False, "handled": True, "decision": "cooldown", "reason": f"闲时回复冷却中，还剩 {remaining} 秒"}
        key = self._proactive_scope_key(event)
        pending_count = max(1, int(getattr(event, "proactive_pending_count", 1) or 1))
        air_delay = self._proactive_air_delay_remaining(key, now, pending_count=pending_count) if key else 0
        if air_delay > 0:
            return {
                "should_reply": False,
                "handled": True,
                "decision": "air_delay",
                "reason": f"会话空气正在等待或退避，还剩 {air_delay} 秒",
                "retry_after": air_delay,
            }
        provider = await self._get_proactive_provider()
        if not provider:
            return {"should_reply": False, "handled": False, "decision": "skip", "reason": "没有可用模型"}

        sender_name = await self.contact_resolver.resolve_event_sender(event)
        target_date_str, using_extended_night, day = await self._proactive_current_day(now)
        session_id = f"daily_life_proactive_{uuid.uuid4().hex[:8]}"
        prompt = await self._build_proactive_prompt(
            event,
            sender_name,
            now,
            day,
            target_date_str,
            using_extended_night,
        )
        try:
            provider_id = self.config.proactive.provider
            text = await self.composer._call_llm_text(
                provider,
                prompt,
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            payload = extract_json_from_text(text)
            if not isinstance(payload, dict):
                return {"should_reply": False, "handled": False, "decision": "skip", "reason": "模型未返回有效裁定"}
            confidence = self._clamp_float(payload.get("confidence"))
            reply_text = self._proactive_reply_text(payload.get("reply_text"))
            should_reply = (
                self._proactive_bool(payload.get("should_reply"))
                and confidence >= self.config.proactive.min_confidence
                and self._expression_review_passed(payload)
                and bool(reply_text)
            )
            payload["should_reply"] = should_reply
            payload["handled"] = True
            payload["confidence"] = confidence
            payload["reply_text"] = reply_text if should_reply else ""
            if not should_reply:
                self._update_proactive_air_after_decision(key, payload, now, sent=False)
            await self._save_proactive_expression_records(event, payload, reply_text)
            try:
                await self._save_proactive_decision(
                    event,
                    sender_name,
                    payload,
                    now,
                    sent=should_reply,
                    reply_text=reply_text,
                )
            except Exception as exc:
                logger.warning(f"{LOG_PREFIX} 闲时回复审计记录失败：{exc}")
            if should_reply:
                chat_mode = "群聊" if self._event_is_group_message(event) else "私聊"
                logger.info(f"{LOG_PREFIX} 闲时回复{chat_mode}裁定通过：{reply_text}")
                mark_changed = getattr(self, "mark_page_status_changed", None)
                if callable(mark_changed):
                    try:
                        await mark_changed("proactive_reply_decision")
                    except Exception as exc:
                        logger.warning(f"{LOG_PREFIX} 闲时回复面板刷新通知失败：{exc}")
            return payload
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 闲时回复裁定失败：{exc}")
            return {"should_reply": False, "handled": False, "decision": "skip", "reason": f"闲时回复裁定失败：{exc}"}
        finally:
            await self.composer._cleanup_conversation(session_id)

    async def evaluate_idle_proactive_candidates(
        self,
        now: datetime.datetime | None = None,
    ) -> None:
        if not self.config.proactive.enabled:
            return
        candidates = self._proactive_idle_candidates
        if not candidates:
            return
        now = now or life_now()
        for key, candidate in list(candidates.items()):
            if not isinstance(candidate, dict):
                candidates.pop(key, None)
                continue
            last_activity_at = candidate.get("last_activity_at")
            if not isinstance(last_activity_at, datetime.datetime):
                candidates.pop(key, None)
                continue
            if int((now - last_activity_at).total_seconds()) < self._proactive_idle_seconds(candidate):
                continue

            event = self._proactive_candidate_event(candidate)
            decision = await self.evaluate_proactive_reply(event, now=now)
            reply_text = str(decision.get("reply_text") or "").strip()
            if decision.get("decision") in {"cooldown", "air_delay"}:
                continue
            if decision.get("should_reply") and reply_text:
                sent = await self._send_proactive_message(
                    str(candidate.get("target_scope") or ""),
                    reply_text,
                    "闲时回复发送失败",
                    send_payload=decision,
                    source_event=event,
                )
                if sent:
                    self._mark_proactive_reply_sent(event, now)
                    self._track_proactive_reply_effect(key, event, decision, reply_text, now)
                    await self._save_pending_reply_effect(key, event, decision, reply_text)
                    candidates.pop(key, None)
                    continue
                self._update_proactive_air_after_decision(
                    key,
                    {"decision": "observe", "reason": "闲时回复发送失败，暂时收回续话意愿"},
                    now,
                    sent=False,
                )
                candidates.pop(key, None)
                continue
            if decision.get("decision") == "wait":
                continue
            candidates.pop(key, None)
