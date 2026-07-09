import datetime
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
    LongTermMemoryRecord,
    MemoryBoundaryRecord,
    MemoryCorrectionRecord,
    MemoryEvidenceRecord,
    PreferenceRecord,
    SessionMidSummaryRecord,
    TemporaryExpressionStateRecord,
)


class ImprintDepositMixin:
    def _long_term_scope(self, meta: dict[str, str], scope: str = "") -> str:
        return self._str_payload(scope or meta.get("group_id") or meta.get("session_id") or "")

    async def _save_long_term_memory(
        self,
        *,
        meta: dict[str, str],
        category: str,
        title: str,
        content: str,
        source_table: str,
        source_id: str = "",
        scope: str = "",
        confidence: float = 1.0,
        weight: float = 1.0,
        expires_at: str = "",
    ) -> LongTermMemoryRecord | None:
        text = self._str_payload(content, "")
        if not text:
            return None
        return await self.archive.upsert_long_term_memory(
            LongTermMemoryRecord(
                scope=self._long_term_scope(meta, scope),
                category=self._str_payload(category, "general") or "general",
                title=self._str_payload(title, 120),
                content=text,
                source_table=self._str_payload(source_table),
                source_id=self._str_payload(source_id),
                session_id=meta.get("session_id", ""),
                message_id=meta.get("message_id", ""),
                date=meta.get("date", ""),
                confidence=max(0.0, min(self._float_payload(confidence, 1.0), 1.0)),
                weight=max(0.0, min(self._float_payload(weight, 1.0), 10.0)),
                expires_at=self._str_payload(expires_at),
            )
        )

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
        saved = await self.archive.save_memory_evidence(
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
        if saved:
            await self._save_long_term_memory(
                meta=meta,
                category=f"evidence:{saved.evidence_type}",
                title=f"{saved.target_type}:{saved.target_id}",
                content=saved.summary,
                source_table="memory_evidence",
                source_id=str(saved.id),
                confidence=saved.confidence,
                weight=0.8,
            )
        return saved

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
            "life_adjustments": [],
            "preferences": [],
        }
        if saved_summary:
            await self._save_long_term_memory(
                meta=meta,
                category="chat_summary",
                title=saved_summary.brief,
                content=saved_summary.long_summary or saved_summary.brief,
                source_table="chat_summaries",
                source_id=str(saved_summary.id),
                confidence=1.0,
                weight=1.0,
            )
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
        await self._save_life_adjustment_payloads(payload, meta, scope, saved_records)
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
            await self._save_long_term_memory(
                meta=meta,
                category=f"episode:{saved_episode.kind}",
                title=saved_episode.title,
                content=saved_episode.summary or saved_episode.impact or saved_episode.title,
                source_table="life_episodes",
                source_id=str(saved_episode.id),
                confidence=saved_episode.confidence,
                weight=1.2 if saved_episode.protected else 1.0,
            )
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
        for raw_evidence in self._dict_payloads(payload.get("evidence_refs"))[:8]:
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
                await self._save_long_term_memory(
                    meta=meta,
                    category="feedback",
                    title=saved_feedback.scene or saved_feedback.action or "行为反馈",
                    content=saved_feedback.feedback or saved_feedback.result or saved_feedback.reason,
                    source_table="behavior_feedback",
                    source_id=str(saved_feedback.id),
                    confidence=0.75,
                    weight=1.0 + max(saved_feedback.score, 0.0) * 0.2,
                )

    async def _save_expression_profile_payloads(
        self,
        payload: dict,
        meta: dict[str, str],
        scope: str,
        saved_records: dict[str, list[Any]],
    ) -> None:
        for raw_expression in self._dict_payloads(payload.get("expression_profiles"))[:4]:
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
                content = "；".join(
                    item
                    for item in (
                        saved_profile.tone,
                        "、".join(saved_profile.habits),
                        f"避免：{'、'.join(saved_profile.avoid)}" if saved_profile.avoid else "",
                        saved_profile.evidence,
                    )
                    if item
                )
                await self._save_long_term_memory(
                    meta=meta,
                    category="expression",
                    title=saved_profile.label or "表达偏好",
                    content=content,
                    source_table="expression_profiles",
                    source_id=str(saved_profile.id),
                    scope=saved_profile.scope,
                    confidence=saved_profile.confidence,
                    weight=1.1,
                )

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
            await self._save_long_term_memory(
                meta=meta,
                category="correction",
                title=f"{saved_correction.target_type}:{saved_correction.target_id}",
                content=saved_correction.correction,
                source_table="memory_corrections",
                source_id=str(saved_correction.id),
                confidence=saved_correction.confidence,
                weight=2.0,
            )
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

    @staticmethod
    def _life_adjustment_default_expiry(meta: dict[str, str], days: int = 7) -> str:
        try:
            base = datetime.datetime.strptime(str(meta.get("date") or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            base = life_now().date()
        return (base + datetime.timedelta(days=days)).isoformat()

    def _life_adjustment_target_id(self, raw: dict[str, Any], content: str, scope: str) -> str:
        return (
            self._str_payload(raw.get("target_id"), "")
            or self._str_payload(raw.get("focus_key"), "")
            or self._str_payload(raw.get("label"), "")
            or self._str_payload(content, "")
            or scope
            or "general"
        )[:120]

    def _life_adjustment_payload_data(
        self,
        raw_adjustment: dict[str, Any],
        default_scope: str,
    ) -> dict[str, Any] | None:
        content = self._str_payload(
            raw_adjustment.get("content")
            or raw_adjustment.get("correction")
            or raw_adjustment.get("preference")
            or raw_adjustment.get("summary"),
            "",
        )
        if not content:
            return None
        scope = self._str_payload(raw_adjustment.get("scope"), "current") or "current"
        target_type = self._str_payload(raw_adjustment.get("target_type"), "other") or "other"
        target_id = self._life_adjustment_target_id(raw_adjustment, content, default_scope)
        reason = self._str_payload(raw_adjustment.get("reason") or raw_adjustment.get("evidence"), content)
        confidence = max(0.0, min(self._float_payload(raw_adjustment.get("confidence"), 0.8), 1.0))
        evidence_summary = reason if reason and reason != content else content
        return {
            "content": content,
            "scope": scope,
            "target_type": target_type,
            "target_id": target_id,
            "reason": reason,
            "confidence": confidence,
            "evidence_summary": evidence_summary,
        }

    async def _save_short_term_life_adjustment(
        self,
        raw_adjustment: dict[str, Any],
        meta: dict[str, str],
        item: dict[str, Any],
        saved_records: dict[str, list[Any]],
    ) -> None:
        expires_at = self._str_payload(raw_adjustment.get("expires_at")) or self._life_adjustment_default_expiry(meta)
        priority = self._int_payload(raw_adjustment.get("priority"), 70)
        saved_slot = await self.archive.upsert_focus_slot(
            FocusSlotRecord.from_value(
                {
                    "scope": self._str_payload(raw_adjustment.get("session_scope")) or "",
                    "focus_key": f"{item['target_type']}:{item['target_id']}"[:140],
                    "label": self._str_payload(raw_adjustment.get("label"), item["content"]),
                    "priority": priority,
                    "reason": item["reason"],
                    "last_active_at": life_now().strftime("%Y-%m-%d %H:%M"),
                    "expires_at": expires_at,
                }
            )
        )
        if not saved_slot:
            return
        saved_records.setdefault("life_adjustments", []).append(saved_slot)
        await self._save_long_term_memory(
            meta=meta,
            category="short_term",
            title=saved_slot.label or item["target_type"],
            content=saved_slot.reason or item["content"],
            source_table="focus_slots",
            source_id=str(saved_slot.id),
            scope=saved_slot.scope,
            confidence=item["confidence"],
            weight=max(1.0, priority / 50),
            expires_at=saved_slot.expires_at,
        )
        await self._save_experience_evidence(
            "focus",
            str(saved_slot.id),
            item["evidence_summary"],
            meta,
            evidence_type="correction",
            source_table="focus_slots",
            source_id=str(saved_slot.id),
            confidence=item["confidence"],
        )

    async def _save_long_term_life_adjustment(
        self,
        raw_adjustment: dict[str, Any],
        meta: dict[str, str],
        item: dict[str, Any],
        saved_records: dict[str, list[Any]],
    ) -> None:
        category = self._str_payload(raw_adjustment.get("category"), item["target_type"]) or "other"
        saved_preferences = await self.archive.upsert_preferences(
            [
                PreferenceRecord(
                    category=category,
                    content=item["content"],
                    weight=max(0.1, min(self._float_payload(raw_adjustment.get("weight"), 1.0), 5.0)),
                    evidence=item["evidence_summary"],
                    last_seen=meta.get("date", ""),
                    source="life_adjustment",
                )
            ],
            meta.get("date", ""),
        )
        for preference in saved_preferences:
            saved_records.setdefault("preferences", []).append(preference)
            await self._save_long_term_memory(
                meta=meta,
                category=f"preference:{preference.category}",
                title=preference.category,
                content=preference.content,
                source_table="preferences",
                source_id=str(preference.id),
                confidence=item["confidence"],
                weight=preference.weight,
            )
            await self._save_experience_evidence(
                "preference",
                str(preference.id),
                item["evidence_summary"],
                meta,
                evidence_type="correction",
                source_table="preferences",
                source_id=str(preference.id),
                confidence=item["confidence"],
            )

    async def _save_memory_correction_life_adjustment(
        self,
        meta: dict[str, str],
        item: dict[str, Any],
        saved_records: dict[str, list[Any]],
    ) -> None:
        correction = MemoryCorrectionRecord.from_value(
            {
                "target_type": item["target_type"],
                "target_id": item["target_id"],
                "correction": item["content"],
                "evidence": item["evidence_summary"],
                "confidence": item["confidence"],
                "source": "life_adjustment",
            }
        )
        saved_correction = await self.archive.save_memory_correction(correction) if correction else None
        if not saved_correction:
            return
        saved_records.setdefault("life_adjustments", []).append(saved_correction)
        await self._save_long_term_memory(
            meta=meta,
            category="correction",
            title=f"{saved_correction.target_type}:{saved_correction.target_id}",
            content=saved_correction.correction,
            source_table="memory_corrections",
            source_id=str(saved_correction.id),
            confidence=saved_correction.confidence,
            weight=2.0,
        )
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

    async def _save_life_adjustment_payloads(
        self,
        payload: dict,
        meta: dict[str, str],
        default_scope: str,
        saved_records: dict[str, list[Any]],
    ) -> None:
        for raw_adjustment in self._dict_payloads(payload.get("life_adjustment"), payload.get("life_adjustments"))[:6]:
            item = self._life_adjustment_payload_data(raw_adjustment, default_scope)
            if not item:
                continue
            if item["scope"] == "short_term":
                await self._save_short_term_life_adjustment(raw_adjustment, meta, item, saved_records)
                continue

            if item["scope"] == "long_term":
                await self._save_long_term_life_adjustment(raw_adjustment, meta, item, saved_records)
                continue

            if item["scope"] == "correction":
                await self._save_memory_correction_life_adjustment(meta, item, saved_records)
                continue

            await self._save_experience_evidence(
                "life_adjustment",
                item["target_id"],
                item["evidence_summary"],
                meta,
                evidence_type="instruction",
                source_table="chat_memory",
                confidence=item["confidence"],
            )

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
