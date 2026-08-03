import datetime
from typing import Any

from ....models import ActionDecisionRecord, LifeEpisodeRecord, MemoryEvidenceRecord


class ProactiveStoreMixin:
    async def _advance_proactive_decision_trace(
        self,
        event: Any,
        payload: dict,
        *,
        stage: str,
        reason_code: str,
        outcome: str,
        sent: bool = False,
    ) -> None:
        """推进已有主动裁定轨迹，但不新增业务记忆。

        Args:
            event: 裁定关联的消息事件。
            payload: 带轨迹编号的主动裁定。
            stage: 新阶段。
            reason_code: 结构化原因代码。
            outcome: 阶段结果摘要。
            sent: 消息是否已实际送达。
        """
        staged = dict(payload)
        staged["stage"] = stage
        staged["reason_code"] = reason_code
        await self._save_proactive_decision_trace(
            event, staged, stage=stage, sent=sent, outcome=outcome
        )
        trace_id = str(staged.get("_decision_trace_id") or "").strip()
        if trace_id:
            payload["_decision_trace_id"] = trace_id

    async def _save_proactive_decision_trace(
        self,
        event: Any,
        payload: dict,
        *,
        stage: str,
        sent: bool,
        outcome: str,
    ) -> None:
        """把主动裁定阶段写入可选的统一决策轨迹。

        Args:
            event: 裁定关联的消息事件。
            payload: 规范后的主动裁定。
            stage: 当前裁定阶段。
            sent: 消息是否已实际送达。
            outcome: 本阶段结果摘要。
        """
        saver = getattr(getattr(self, "archive", None), "save_decision_trace", None)
        if not callable(saver):
            return
        score_fields = (
            "benefit",
            "timeliness",
            "continuity",
            "disruption",
            "uncertainty",
            "utility",
        )
        scores = {
            field: payload.get(field)
            for field in score_fields
            if payload.get(field) is not None
        }
        scores["sent"] = sent
        evidence = [
            str(value).strip()
            for value in (
                payload.get("target_message_id"),
                payload.get("target_topic"),
                payload.get("reason"),
            )
            if str(value or "").strip()
        ]
        trace_payload = {
            "trace_id": str(payload.get("_decision_trace_id") or ""),
            "scope": self._proactive_scope_key(event),
            "stage": stage,
            "reason_code": self._str_payload(payload.get("reason_code")),
            "decision": self._str_payload(payload.get("decision"), "observe"),
            "scores": scores,
            "evidence": evidence,
            "outcome": str(outcome or "").strip(),
        }
        try:
            saved = await saver(trace_payload)
        except Exception:
            return
        trace_id = str(
            getattr(saved, "trace_id", "")
            or (saved.get("trace_id") if isinstance(saved, dict) else "")
        ).strip()
        if trace_id:
            payload["_decision_trace_id"] = trace_id

    async def _save_proactive_decision(
        self,
        event: Any,
        sender_name: str,
        payload: dict,
        now: datetime.datetime,
        *,
        sent: bool,
        reply_text: str = "",
        stage: str = "decision",
    ) -> None:
        """保存主动裁定审计，并仅为真实发送建立情节记忆。

        Args:
            event: 裁定关联的消息事件。
            sender_name: 会话对象名称。
            payload: 规范后的主动裁定。
            now: 裁定或提交时间。
            sent: 消息是否已经实际发送成功。
            reply_text: 候选或实际发送文本。
            stage: ``proposal``、``commit`` 等裁定阶段。
        """
        meta = await self._event_context_meta(event, sender_name, now)
        source = self._str_payload(payload.get("source"), "proactive_reply")
        decision_name = self._str_payload(payload.get("decision"), "observe")
        action = "private_revisit" if source == "private_revisit" else "proactive_reply"
        reason = self._str_payload(payload.get("reason"))
        strategy = self._str_payload(payload.get("reply_strategy"))
        saved = await self.archive.save_action_decision(
            ActionDecisionRecord(
                session_id=meta["session_id"],
                message_id=meta["message_id"],
                sender_profile_id=meta["sender_profile_id"],
                sender_name=meta["sender_name"],
                group_id=meta["group_id"],
                group_name=meta["group_name"],
                date=meta["date"],
                action=action,
                reason=reason,
                confidence=self._clamp_float(payload.get("confidence")),
                scene_type=(
                    f"私聊回访/{stage}"
                    if source == "private_revisit"
                    else f"闲时回复/{stage}"
                ),
                topic_owner=self._str_payload(payload.get("topic_owner")),
                understanding="understood" if sent else "partial",
                deep_analysis=False,
                inner_monologue=self._str_payload(payload.get("inner_monologue")),
                reply_strategy=strategy or reply_text,
                decision_category="proactive",
                decision_source=source,
                decision_stage=stage,
                decision_outcome="reply" if sent else decision_name,
            )
        )
        await self.composer._save_life_decision_record(
            kind=source,
            date=meta["date"],
            subject=meta["group_name"] or meta["sender_name"] or meta["session_id"],
            decision=action,
            reason=reason,
            evidence=self._str_payload(
                payload.get("target_topic")
                or payload.get("target_message_id")
                or payload.get("reason_code")
            ),
            outcome=reply_text
            if sent
            else self._str_payload(payload.get("wait_reason") or strategy),
            confidence=self._clamp_float(payload.get("confidence")),
            source=source,
            focus_scope=self._proactive_scope_key(event),
        )
        note = self._str_payload(payload.get("memory_note")) if sent else ""
        if sent:
            note = note or f"闲时续话：{reply_text}"
        if note:
            episode = await self.archive.save_life_episode(
                LifeEpisodeRecord(
                    date=meta["date"],
                    title=(
                        "群聊闲时续话"
                        if meta.get("is_group") == "true" and sent
                        else "群聊观察克制"
                        if meta.get("is_group") == "true"
                        else "私聊闲时回应"
                        if sent
                        else "私聊暂不回应"
                    ),
                    summary=note,
                    kind="group" if meta.get("is_group") == "true" else "chat",
                    related_people=[sender_name] if sender_name else [],
                    impact=reason,
                    confidence=self._clamp_float(payload.get("confidence"), 0.5),
                    source=source,
                )
            )
            await self.archive.save_memory_evidence(
                MemoryEvidenceRecord(
                    target_type="action_decision",
                    target_id=str(saved.id),
                    evidence_type="decision",
                    source_table="action_decisions",
                    source_id=str(saved.id),
                    session_id=meta["session_id"],
                    message_id=meta["message_id"],
                    date=meta["date"],
                    summary=note,
                    confidence=self._clamp_float(payload.get("confidence"), 0.5),
                )
            )
            await self.archive.save_memory_evidence(
                MemoryEvidenceRecord(
                    target_type="life_episode",
                    target_id=str(episode.id),
                    evidence_type="proactive_reply",
                    source_table="life_episodes",
                    source_id=str(episode.id),
                    session_id=meta["session_id"],
                    message_id=meta["message_id"],
                    date=meta["date"],
                    summary=reason or note,
                    confidence=self._clamp_float(payload.get("confidence"), 0.5),
                )
            )
        await self._save_proactive_decision_trace(
            event,
            payload,
            stage=stage,
            sent=sent,
            outcome=("消息已实际发送" if sent else reason or decision_name),
        )
