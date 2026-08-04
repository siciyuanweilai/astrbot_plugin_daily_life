from __future__ import annotations

import datetime
from typing import Any

from ..clock import now as life_now
from ..models.cognition import AffectiveStateRecord, ReflectionRecord
from .affect import AffectEngine, AffectiveSnapshot


class LifeEvolutionService:
    """把夜间复盘结算为情绪、关系、反思和有证据日记。"""

    def __init__(self, archive: Any):
        self.archive = archive
        self.affect = AffectEngine()

    @staticmethod
    def evidence_ids(
        *,
        events: list[Any],
        decisions: list[Any],
        feedback: list[Any],
        reply_effects: list[Any],
    ) -> set[str]:
        """为已落库输入建立允许引用的稳定证据编号集合。"""

        allowed: set[str] = set()
        for prefix, items in (
            ("event", events),
            ("decision", decisions),
            ("feedback", feedback),
            ("reply_effect", reply_effects),
        ):
            for item in items:
                try:
                    item_id = int(getattr(item, "id", 0) or 0)
                except (TypeError, ValueError):
                    item_id = 0
                if item_id > 0:
                    allowed.add(f"{prefix}:{item_id}")
        return allowed

    @staticmethod
    def _snapshot(item: Any) -> AffectiveSnapshot:
        updated_at = None
        raw_updated_at = str(getattr(item, "updated_at", "") or "").strip()
        if raw_updated_at:
            try:
                updated_at = datetime.datetime.fromisoformat(
                    raw_updated_at.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                updated_at = None
        return AffectiveSnapshot(
            scope=str(getattr(item, "scope", "") or ""),
            layer=str(getattr(item, "layer", "transient") or "transient"),
            label=str(getattr(item, "label", "") or ""),
            valence=float(getattr(item, "valence", 0.0) or 0.0),
            arousal=float(getattr(item, "arousal", 0.5) or 0.5),
            intensity=float(getattr(item, "intensity", 0.5) or 0.5),
            baseline=float(getattr(item, "baseline", 0.5) or 0.5),
            decay_half_life_minutes=float(
                getattr(item, "decay_half_life_minutes", 240.0) or 240.0
            ),
            evidence_ids=[
                str(value)
                for value in (getattr(item, "evidence", []) or [])
                if str(value).strip()
            ],
            updated_at=updated_at,
        )

    async def _save_trace(
        self,
        *,
        trace_id: str,
        scope: str,
        stage: str,
        reason_code: str,
        decision: str,
        scores: dict[str, Any] | None = None,
        evidence: list[Any] | None = None,
        outcome: str = "",
    ) -> None:
        saver = getattr(self.archive, "save_decision_trace", None)
        if not callable(saver):
            return
        await saver(
            {
                "trace_id": trace_id,
                "scope": scope,
                "stage": stage,
                "reason_code": reason_code,
                "decision": decision,
                "scores": scores or {},
                "evidence": evidence or [],
                "outcome": outcome,
            }
        )

    async def _settle_affect_updates(
        self,
        payload: dict[str, Any],
        *,
        allowed_evidence_ids: set[str],
        now: datetime.datetime,
    ) -> int:
        getter = getattr(self.archive, "get_affective_states", None)
        saver = getattr(self.archive, "save_affective_state", None)
        if not callable(getter) or not callable(saver):
            return 0
        saved_count = 0
        for signal in self.affect.signals_from_payload(payload):
            signal.evidence_ids = [
                item for item in signal.evidence_ids if item in allowed_evidence_ids
            ]
            if not signal.evidence_ids:
                continue
            scope = "global"
            states = await getter(scope=scope, layer=signal.layer, limit=20)
            current = next(
                (item for item in states if str(item.label) == signal.label), None
            )
            settled = self.affect.apply(
                self._snapshot(current) if current else None,
                signal,
                scope=scope,
                now=now,
            )
            await saver(
                AffectiveStateRecord(
                    scope=settled.scope,
                    layer=settled.layer,
                    label=settled.label,
                    valence=settled.valence,
                    arousal=settled.arousal,
                    intensity=settled.intensity,
                    baseline=settled.baseline,
                    decay_half_life_minutes=settled.decay_half_life_minutes,
                    evidence=list(settled.evidence_ids),
                    valid_from=now.strftime("%Y-%m-%d %H:%M:%S"),
                    source=signal.source,
                )
            )
            saved_count += 1
        return saved_count

    async def _settle_relationship_updates(
        self,
        payload: dict[str, Any],
        *,
        allowed_evidence_ids: set[str],
        date: str,
        now: datetime.datetime,
    ) -> int:
        relationship_getter = getattr(self.archive, "get_relationship", None)
        state_getter = getattr(self.archive, "get_affective_states", None)
        state_saver = getattr(self.archive, "save_affective_state", None)
        point_adder = getattr(self.archive, "add_relationship_point", None)
        if not all(
            callable(method)
            for method in (relationship_getter, state_getter, state_saver)
        ):
            return 0
        saved_count = 0
        for update in self.affect.relationship_updates_from_payload(payload):
            update.evidence_ids = [
                item for item in update.evidence_ids if item in allowed_evidence_ids
            ]
            if not update.evidence_ids:
                continue
            relationship = await relationship_getter(update.profile_id)
            if relationship is None:
                continue
            scope = f"relationship:{update.profile_id}"
            states = await state_getter(scope=scope, layer="relationship", limit=20)
            current = next(
                (item for item in states if str(item.label) == "关系亲近度"), None
            )
            delta = (
                update.familiarity_delta + update.trust_delta + update.affinity_delta
            ) / 3.0
            signal = {
                "layer": "relationship",
                "label": "关系亲近度",
                "valence": 1.0 if delta >= 0 else -1.0,
                "arousal": min(1.0, 0.5 + abs(delta) * 3.0),
                "intensity": min(1.0, abs(delta) * 8.0),
                "evidence_ids": update.evidence_ids,
                "source": "daily_review",
            }
            normalized = self.affect.signals_from_payload({"affect_updates": [signal]})[
                0
            ]
            settled = self.affect.apply(
                self._snapshot(current) if current else None,
                normalized,
                scope=scope,
                now=now,
            )
            await state_saver(
                AffectiveStateRecord(
                    scope=scope,
                    layer="relationship",
                    label="关系亲近度",
                    valence=settled.valence,
                    arousal=settled.arousal,
                    intensity=settled.intensity,
                    baseline=settled.baseline,
                    decay_half_life_minutes=settled.decay_half_life_minutes,
                    evidence=list(settled.evidence_ids),
                    valid_from=now.strftime("%Y-%m-%d %H:%M:%S"),
                    source="daily_review",
                )
            )
            if callable(point_adder) and update.reason:
                await point_adder(
                    update.profile_id,
                    update.reason,
                    date_str=date,
                    source="日常复盘",
                    weight=max(0.1, min(1.0 + delta, 2.0)),
                )
            fact_writer = getattr(self.archive, "upsert_current_temporal_fact", None)
            if callable(fact_writer):
                await fact_writer(
                    scope="global",
                    subject=f"relationship:{update.profile_id}",
                    predicate="latest_change",
                    object_value={
                        "familiarity_delta": update.familiarity_delta,
                        "trust_delta": update.trust_delta,
                        "affinity_delta": update.affinity_delta,
                        "reason": update.reason,
                    },
                    observed_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                    source="daily_review",
                    source_type="relationship_update",
                    source_id=f"relationship:{update.profile_id}:{date}",
                    provenance={"evidence_ids": update.evidence_ids, "date": date},
                    evidence_summary=update.reason,
                )
            saved_count += 1
        return saved_count

    async def _settle_reflection(
        self,
        payload: dict[str, Any],
        *,
        allowed_evidence_ids: set[str],
        scope: str,
        now: datetime.datetime,
    ) -> tuple[bool, float, str]:
        raw_score = (
            payload.get("reflection_score")
            if isinstance(payload.get("reflection_score"), dict)
            else {}
        )
        gate = self.affect.reflection_gate(
            novelty=raw_score.get("novelty"),
            emotional_intensity=raw_score.get("emotional_intensity"),
            goal_impact=raw_score.get("goal_impact"),
            social_impact=raw_score.get("social_impact"),
        )
        raw = (
            payload.get("reflection")
            if isinstance(payload.get("reflection"), dict)
            else {}
        )
        evidence = [
            str(item)
            for item in (raw.get("evidence_ids") or [])
            if str(item) in allowed_evidence_ids
        ]
        summary = " ".join(str(raw.get("summary") or "").split())[:1000]
        saver = getattr(self.archive, "save_reflection", None)
        if (
            not gate.should_reflect
            or not summary
            or not evidence
            or not callable(saver)
        ):
            return False, gate.importance, gate.reason_code
        getter = getattr(self.archive, "get_reflections", None)
        if callable(getter):
            previous = await getter(scope=scope, limit=1)
            if previous:
                last_created_at = str(getattr(previous[0], "created_at", "") or "")
                try:
                    last_time = datetime.datetime.fromisoformat(
                        last_created_at.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except ValueError:
                    last_time = None
                if last_time and now.replace(
                    tzinfo=None
                ) < last_time + datetime.timedelta(hours=12):
                    return False, gate.importance, "reflection_cooldown"
        assertion = (
            raw.get("assertion") if isinstance(raw.get("assertion"), dict) else {}
        )
        await saver(
            ReflectionRecord(
                scope=scope,
                kind="daily_review",
                summary=summary,
                importance=gate.importance / 100.0,
                evidence_ids=evidence,
                assertion_subject=" ".join(str(assertion.get("subject") or "").split())[
                    :180
                ],
                assertion_predicate=" ".join(
                    str(assertion.get("predicate") or "").split()
                )[:120],
                assertion_object=assertion.get("object"),
                confidence=max(0.0, min(gate.importance / 100.0, 1.0)),
                source="daily_review",
            )
        )
        promoter = getattr(self.archive, "promote_reflections", None)
        if callable(promoter):
            await promoter(min_importance=0.7, min_evidence=2, limit=8)
        return True, gate.importance, gate.reason_code

    async def settle_review(
        self,
        payload: dict[str, Any],
        *,
        date: str,
        events: list[Any],
        decisions: list[Any],
        feedback: list[Any],
        reply_effects: list[Any],
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        """结算一轮夜间复盘的长期演化结果。

        Args:
            payload: 模型返回且已解析的结构化复盘对象。
            date: 所属生活日期。
            events: 参与本轮复盘的已落库生活事件。
            decisions: 参与本轮复盘的已落库生活决策。
            feedback: 当天已落库行为反馈。
            reply_effects: 当天已落库互动效果。
            now: 可选结算时间，测试时用于固定衰减结果。

        Returns:
            各演化通道的实际保存数量和反思门控结果。
        """

        now = now or life_now()
        scope = "global"
        allowed = self.evidence_ids(
            events=events,
            decisions=decisions,
            feedback=feedback,
            reply_effects=reply_effects,
        )
        trace_id = f"daily_review:{date}"
        await self._save_trace(
            trace_id=trace_id,
            scope=scope,
            stage="validated",
            reason_code="review_evidence_validated",
            decision="settle" if allowed else "skip",
            evidence=sorted(allowed),
        )
        affect_count = await self._settle_affect_updates(
            payload,
            allowed_evidence_ids=allowed,
            now=now,
        )
        relationship_count = await self._settle_relationship_updates(
            payload,
            allowed_evidence_ids=allowed,
            date=date,
            now=now,
        )
        reflected, importance, reason_code = await self._settle_reflection(
            payload,
            allowed_evidence_ids=allowed,
            scope=scope,
            now=now,
        )
        diary = self.affect.grounded_diary_from_payload(
            payload,
            date=date,
            allowed_evidence_ids=allowed,
            scope=scope,
        )
        diary_saved = False
        diary_saver = getattr(self.archive, "save_grounded_diary_entry", None)
        if diary and callable(diary_saver):
            await diary_saver(diary)
            diary_saved = True
        outcome = {
            "affective_states": affect_count,
            "relationship_updates": relationship_count,
            "reflection_saved": reflected,
            "reflection_importance": importance,
            "diary_saved": diary_saved,
        }
        await self._save_trace(
            trace_id=trace_id,
            scope=scope,
            stage="committed",
            reason_code=reason_code,
            decision="settled",
            scores={"reflection_importance": importance},
            evidence=sorted(allowed),
            outcome=str(outcome),
        )
        return outcome


__all__ = ["LifeEvolutionService"]
