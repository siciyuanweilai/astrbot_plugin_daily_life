import datetime
from typing import Any

from ....models import ActionDecisionRecord, LifeEpisodeRecord, MemoryEvidenceRecord


class ProactiveStoreMixin:

    async def _save_proactive_decision(
        self,
        event: Any,
        sender_name: str,
        payload: dict,
        now: datetime.datetime,
        *,
        sent: bool,
        reply_text: str = "",
    ) -> None:
        meta = await self._event_context_meta(event, sender_name, now)
        action = "proactive_reply" if sent else f"proactive_{self._str_payload(payload.get('decision'), 'observe')}"
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
                scene_type="闲时回复",
                topic_owner=self._str_payload(payload.get("topic_owner")),
                understanding="understood" if sent else "partial",
                deep_analysis=False,
                inner_monologue=self._str_payload(payload.get("inner_monologue")),
                reply_strategy=strategy or reply_text,
            )
        )
        await self.composer._save_life_decision_record(
            kind="proactive_reply",
            date=meta["date"],
            subject=meta["group_name"] or meta["sender_name"] or meta["session_id"],
            decision=action,
            reason=reason,
            evidence=self._str_payload(payload.get("target_topic") or payload.get("target_message_id")),
            outcome=reply_text if sent else self._str_payload(payload.get("wait_reason") or strategy),
            confidence=self._clamp_float(payload.get("confidence")),
            source="proactive_reply",
            focus_scope=self._proactive_scope_key(event),
        )
        note = self._str_payload(payload.get("memory_note"))
        if sent:
            note = note or f"闲时续话：{reply_text}"
        if note:
            episode = await self.archive.save_life_episode(
                LifeEpisodeRecord(
                    date=meta["date"],
                    title=(
                        "群聊闲时续话" if meta.get("is_group") == "true" and sent
                        else "群聊观察克制" if meta.get("is_group") == "true"
                        else "私聊闲时回应" if sent
                        else "私聊暂不回应"
                    ),
                    summary=note,
                    kind="group" if meta.get("is_group") == "true" else "chat",
                    related_people=[sender_name] if sender_name else [],
                    impact=reason,
                    confidence=self._clamp_float(payload.get("confidence"), 0.5),
                    source="proactive_reply",
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
