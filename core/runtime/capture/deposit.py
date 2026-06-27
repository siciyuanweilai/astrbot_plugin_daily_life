from typing import Any

from ...clock import now as life_now
from ...models import (
    BehaviorFeedbackRecord,
    BehaviorPatternRecord,
    BehaviorSceneRecord,
    ChatSummaryRecord,
    ExpressionIntentRecord,
    ExpressionProfileRecord,
    FocusSlotRecord,
    FocusTargetRecord,
    LifeEpisodeRecord,
    LifeTermRecord,
    MemoryBoundaryRecord,
    MemoryCorrectionRecord,
    MemoryEvidenceRecord,
    SessionMidSummaryRecord,
    TemporaryExpressionStateRecord,
)


class ImprintDepositMixin:
    async def _save_experience_evidence(
        self,
        target_type: str,
        target_id: str,
        summary: Any,
        meta: dict[str, str],
        *,
        evidence_type: str = "observation",
        source_table: str = "",
        source_id: str = "",
        confidence: float = 1.0,
    ) -> MemoryEvidenceRecord | None:
        return await self.archive.save_memory_evidence(
            MemoryEvidenceRecord(
                target_type=self._str_payload(target_type),
                target_id=self._str_payload(target_id),
                evidence_type=self._str_payload(evidence_type, "observation"),
                source_table=self._str_payload(source_table),
                source_id=self._str_payload(source_id),
                session_id=meta.get("session_id", ""),
                message_id=meta.get("message_id", ""),
                date=meta.get("date", ""),
                summary=self._str_payload(summary),
                confidence=max(self._float_payload(confidence, 1.0), 0.0),
            )
        )

    async def _save_experience_payload(
        self,
        payload: dict,
        meta: dict[str, str],
        saved_summary: ChatSummaryRecord | None = None,
    ) -> dict[str, list[Any]]:
        saved_records: dict[str, list[Any]] = {
            "life_episodes": [],
            "behavior_feedback": [],
            "expression_profiles": [],
        }
        scope = meta.get("group_id") or meta.get("session_id") or ""
        await self._save_life_episode_payloads(payload, meta, saved_records)
        await self._save_evidence_payloads(payload, meta, saved_summary)
        await self._save_behavior_feedback_payloads(payload, meta, saved_records)
        await self._save_expression_profile_payloads(payload, meta, scope, saved_records)
        await self._save_behavior_context_payloads(payload, meta, scope)
        await self._save_session_summary_payload(payload, meta)
        await self._save_temporary_expression_payloads(payload, scope)
        await self._save_focus_payloads(payload, meta, scope)
        await self._save_memory_correction_payloads(payload, meta)
        await self._save_expression_intent_payload(payload, meta, scope)
        await self._save_life_term_payloads(payload, meta)
        await self._save_memory_boundary_payloads(payload)
        return saved_records

    def _dict_payloads(self, *values: Any) -> list[dict]:
        items: list[dict] = []
        for value in values:
            if isinstance(value, dict):
                items.append(value)
                continue
            items.extend(item for item in self._list_payload(value) if isinstance(item, dict))
        return items

    async def _save_life_episode_payloads(
        self,
        payload: dict,
        meta: dict[str, str],
        saved_records: dict[str, list[Any]],
    ) -> None:
        for raw_episode in self._dict_payloads(payload.get("life_episode"), payload.get("life_episodes"))[:4]:
            episode = LifeEpisodeRecord.from_value(
                {
                    **raw_episode,
                    "date": raw_episode.get("date") or meta["date"],
                    "source": raw_episode.get("source") or "chat_memory",
                }
            )
            if not episode:
                continue
            saved_episode = await self.archive.save_life_episode(episode)
            saved_records["life_episodes"].append(saved_episode)
            await self._save_experience_evidence(
                "life_episode",
                str(saved_episode.id),
                saved_episode.summary or saved_episode.title,
                meta,
                evidence_type="episode",
                source_table="life_episodes",
                source_id=str(saved_episode.id),
                confidence=saved_episode.confidence,
            )

    async def _save_evidence_payloads(
        self,
        payload: dict,
        meta: dict[str, str],
        saved_summary: ChatSummaryRecord | None,
    ) -> None:
        for raw_evidence in self._dict_payloads(payload.get("evidence_refs") or payload.get("memory_evidence"))[:8]:
            target_type = self._str_payload(raw_evidence.get("target_type")) or ("chat_summary" if saved_summary else "")
            target_id = self._str_payload(raw_evidence.get("target_id")) or (str(saved_summary.id) if saved_summary else "")
            summary = self._str_payload(raw_evidence.get("summary"))
            if not (target_type and target_id and summary):
                continue
            await self._save_experience_evidence(
                target_type,
                target_id,
                summary,
                meta,
                evidence_type=self._str_payload(raw_evidence.get("evidence_type"), "observation"),
                source_table=self._str_payload(raw_evidence.get("source_table")),
                source_id=self._str_payload(raw_evidence.get("source_id")),
                confidence=self._float_payload(raw_evidence.get("confidence"), 1.0),
            )

    async def _save_behavior_feedback_payloads(
        self,
        payload: dict,
        meta: dict[str, str],
        saved_records: dict[str, list[Any]],
    ) -> None:
        for raw_feedback in self._dict_payloads(payload.get("behavior_feedback"))[:5]:
            record = BehaviorFeedbackRecord.from_value(
                {
                    **raw_feedback,
                    "date": raw_feedback.get("date") or meta["date"],
                    "source": raw_feedback.get("source") or "chat_memory",
                }
            )
            if not record:
                continue
            saved_feedback = await self.archive.add_behavior_feedback(record)
            if saved_feedback:
                saved_records["behavior_feedback"].append(saved_feedback)

    async def _save_expression_profile_payloads(
        self,
        payload: dict,
        meta: dict[str, str],
        scope: str,
        saved_records: dict[str, list[Any]],
    ) -> None:
        raw_profiles = payload.get("expression_profiles") or payload.get("expression_habits")
        for raw_expression in self._dict_payloads(raw_profiles)[:4]:
            record = ExpressionProfileRecord.from_value(
                {
                    **raw_expression,
                    "scope": raw_expression.get("scope") or scope,
                    "profile_id": raw_expression.get("profile_id") or meta.get("sender_profile_id", ""),
                    "label": raw_expression.get("label") or meta.get("sender_name", ""),
                    "source": raw_expression.get("source") or "chat_memory",
                }
            )
            if not record:
                continue
            saved_profile = await self.archive.upsert_expression_profile(record)
            if saved_profile:
                saved_records["expression_profiles"].append(saved_profile)

    async def _save_behavior_context_payloads(
        self,
        payload: dict,
        meta: dict[str, str],
        scope: str,
    ) -> None:
        for raw_pattern in self._dict_payloads(payload.get("behavior_patterns"))[:5]:
            await self.archive.upsert_behavior_pattern(
                BehaviorPatternRecord.from_value(
                    {
                        **raw_pattern,
                        "scope": raw_pattern.get("scope") or scope,
                        "last_seen": raw_pattern.get("last_seen") or meta["date"],
                        "source": raw_pattern.get("source") or "chat_memory",
                    },
                    date=meta["date"],
                )
            )

        for raw_scene in self._dict_payloads(payload.get("behavior_scenes"))[:5]:
            await self.archive.upsert_behavior_scene(
                BehaviorSceneRecord.from_value(
                    {
                        **raw_scene,
                        "scope": raw_scene.get("scope") or scope,
                        "last_seen": raw_scene.get("last_seen") or meta["date"],
                        "source": raw_scene.get("source") or "chat_memory",
                    },
                    date=meta["date"],
                )
            )

    async def _save_session_summary_payload(self, payload: dict, meta: dict[str, str]) -> None:
        session_summary = payload.get("session_mid_summary")
        if isinstance(session_summary, dict):
            await self.archive.upsert_session_mid_summary(
                SessionMidSummaryRecord.from_value(
                    {
                        **session_summary,
                        "session_id": meta.get("session_id", ""),
                        "scope_label": session_summary.get("scope_label")
                        or meta.get("group_name")
                        or meta.get("sender_name")
                        or meta.get("session_id", ""),
                        "last_message_id": session_summary.get("last_message_id") or meta.get("message_id", ""),
                        "source": session_summary.get("source") or "chat_memory",
                    }
                )
            )

    async def _save_temporary_expression_payloads(self, payload: dict, scope: str) -> None:
        for raw_state in self._dict_payloads(
            payload.get("temporary_expression_state"),
            payload.get("temporary_expression_states"),
        )[:3]:
            await self.archive.upsert_temporary_expression_state(
                TemporaryExpressionStateRecord.from_value(
                    {
                        **raw_state,
                        "scope": raw_state.get("scope") or scope,
                        "source": raw_state.get("source") or "chat_memory",
                    }
                )
            )

    async def _save_focus_payloads(self, payload: dict, meta: dict[str, str], scope: str) -> None:
        for raw_focus in self._dict_payloads(payload.get("focus_updates"))[:6]:
            record = FocusTargetRecord.from_value(raw_focus)
            if not record:
                continue
            await self.archive.upsert_focus_target(record)

        for raw_slot in self._dict_payloads(payload.get("focus_slots"))[:4]:
            await self.archive.upsert_focus_slot(
                FocusSlotRecord.from_value(
                    {
                        **raw_slot,
                        "scope": raw_slot.get("scope") or scope,
                        "last_active_at": raw_slot.get("last_active_at") or life_now().strftime("%Y-%m-%d %H:%M"),
                    }
                )
            )

    async def _save_memory_correction_payloads(self, payload: dict, meta: dict[str, str]) -> None:
        for raw_correction in self._dict_payloads(payload.get("memory_correction"), payload.get("memory_corrections"))[:4]:
            correction = MemoryCorrectionRecord.from_value(
                {
                    **raw_correction,
                    "source": raw_correction.get("source") or "chat_memory",
                }
            )
            if not correction:
                continue
            saved_correction = await self.archive.save_memory_correction(correction)
            if not saved_correction:
                continue
            await self._save_experience_evidence(
                saved_correction.target_type,
                saved_correction.target_id,
                saved_correction.evidence or saved_correction.correction,
                meta,
                evidence_type="correction",
                source_table="memory_corrections",
                source_id=str(saved_correction.id),
                confidence=saved_correction.confidence,
            )
            applied = await self._apply_memory_correction(saved_correction, meta)
            if applied:
                await self.archive.mark_memory_correction_applied(saved_correction.id, True)
                self.schedule_memos_correction(saved_correction, meta)

    async def _save_expression_intent_payload(self, payload: dict, meta: dict[str, str], scope: str) -> None:
        expression_intent = payload.get("expression_intent")
        if isinstance(expression_intent, dict):
            await self.archive.save_expression_intent(
                ExpressionIntentRecord.from_value(
                    {
                        **expression_intent,
                        "scope": expression_intent.get("scope") or scope,
                        "message_id": expression_intent.get("message_id") or meta.get("message_id", ""),
                        "source": expression_intent.get("source") or "chat_memory",
                    },
                    source="chat_memory",
                )
            )

    async def _save_life_term_payloads(self, payload: dict, meta: dict[str, str]) -> None:
        for raw_term in self._dict_payloads(payload.get("life_terms"))[:8]:
            await self.archive.upsert_life_term(
                LifeTermRecord.from_value(
                    {
                        **raw_term,
                        "last_seen": raw_term.get("last_seen") or meta["date"],
                        "source": raw_term.get("source") or "chat_memory",
                    }
                )
            )

    async def _save_memory_boundary_payloads(self, payload: dict) -> None:
        boundary = payload.get("memory_boundary_hint")
        for raw_boundary in self._dict_payloads(boundary, payload.get("memory_boundaries"))[:4]:
            record = MemoryBoundaryRecord.from_value(raw_boundary)
            if record:
                await self.archive.set_memory_boundary(record)
