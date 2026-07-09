from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_bool, optional_float, optional_int
from .coerce import compact_text as _text, compact_texts as _texts


@dataclass(slots=True)
class EmotionArcRecord:
    id: int = 0
    scope: str = ""
    date: str = ""
    label: str = ""
    valence: int = 0
    arousal: int = 50
    intensity: int = 50
    stability: int = 50
    trigger: str = ""
    evidence: str = ""
    influence: str = ""
    expires_at: str = ""
    status: str = "active"
    source: str = "state"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def _score(value: Any, default: int = 50, *, lower: int = 0, upper: int = 100) -> int:
        number = optional_int(value)
        if number is None:
            number = default
        return max(lower, min(number, upper))

    @staticmethod
    def from_value(value: Any, *, date: str = "", source: str = "state") -> "EmotionArcRecord | None":
        if isinstance(value, EmotionArcRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        label = _text(raw.get("label") or raw.get("emotion") or raw.get("mood"), 80)
        evidence = _text(raw.get("evidence") or raw.get("reason"), 240)
        trigger = _text(raw.get("trigger") or raw.get("source_event"), 160)
        influence = _text(raw.get("influence") or raw.get("effect"), 240)
        if not (label or evidence or trigger or influence):
            return None
        return EmotionArcRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=_text(raw.get("scope"), 180),
            date=_text(raw.get("date") or date, 20),
            label=label or "情绪波动",
            valence=EmotionArcRecord._score(raw.get("valence"), 0, lower=-100, upper=100),
            arousal=EmotionArcRecord._score(raw.get("arousal"), 50),
            intensity=EmotionArcRecord._score(raw.get("intensity"), 50),
            stability=EmotionArcRecord._score(raw.get("stability"), 50),
            trigger=trigger,
            evidence=evidence,
            influence=influence,
            expires_at=_text(raw.get("expires_at"), 40),
            status=_text(raw.get("status") or "active", 40) or "active",
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "date": self.date,
            "label": self.label,
            "valence": self.valence,
            "arousal": self.arousal,
            "intensity": self.intensity,
            "stability": self.stability,
            "trigger": self.trigger,
            "evidence": self.evidence,
            "influence": self.influence,
            "expires_at": self.expires_at,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class PhysiologicalRhythmLogRecord:
    id: int = 0
    date: str = ""
    source: str = "state"
    energy_curve: str = ""
    body_label: str = ""
    body_intensity: int = 0
    body_source: str = ""
    body_expires_at: str = ""
    recovery_actions: list[str] = field(default_factory=list)
    social_battery: int = 50
    attention_state: str = ""
    optional_cycle_enabled: bool = False
    optional_cycle_label: str = ""
    optional_cycle_intensity: int = 0
    optional_cycle_source: str = ""
    summary: str = ""
    lifecycle_kind: str = "transient"
    weight: float = 1.0
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def _score(value: Any, default: int = 0, *, lower: int = 0, upper: int = 100) -> int:
        number = optional_int(value)
        if number is None:
            number = default
        return max(lower, min(number, upper))

    @staticmethod
    def _lifecycle(value: Any) -> str:
        text = _text(value or "transient", 40)
        return text if text in {"transient", "short_term", "sustained"} else "transient"

    @staticmethod
    def _optional_cycle_enabled(raw: dict, label: str, source: str, intensity: int) -> bool:
        return bool(optional_bool(raw.get("optional_cycle_enabled"))) and bool(label or source or intensity > 0)

    @staticmethod
    def from_value(
        value: Any,
        *,
        date: str = "",
        source: str = "state",
    ) -> "PhysiologicalRhythmLogRecord | None":
        if isinstance(value, PhysiologicalRhythmLogRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        energy_curve = _text(raw.get("energy_curve"), 120)
        body_label = _text(raw.get("body_label"), 80)
        recovery_actions = _texts(raw.get("recovery_actions"), limit=6, item_limit=50)
        attention_state = _text(raw.get("attention_state"), 80)
        optional_cycle_label = _text(raw.get("optional_cycle_label"), 40)
        optional_cycle_source = _text(raw.get("optional_cycle_source"), 40)
        optional_cycle_intensity = PhysiologicalRhythmLogRecord._score(raw.get("optional_cycle_intensity"), 0)
        optional_cycle_enabled = PhysiologicalRhythmLogRecord._optional_cycle_enabled(
            raw,
            optional_cycle_label,
            optional_cycle_source,
            optional_cycle_intensity,
        )
        summary = _text(raw.get("summary"), 160)
        if not any((energy_curve, body_label, recovery_actions, attention_state, optional_cycle_label, summary)):
            return None
        weight = optional_float(raw.get("weight"))
        return PhysiologicalRhythmLogRecord(
            id=optional_int(raw.get("id")) or 0,
            date=_text(raw.get("date") or date, 20),
            source=_text(raw.get("source") or source, 40) or source,
            energy_curve=energy_curve,
            body_label=body_label,
            body_intensity=PhysiologicalRhythmLogRecord._score(raw.get("body_intensity"), 0),
            body_source=_text(raw.get("body_source"), 80),
            body_expires_at=_text(raw.get("body_expires_at"), 40),
            recovery_actions=recovery_actions,
            social_battery=PhysiologicalRhythmLogRecord._score(raw.get("social_battery"), 50),
            attention_state=attention_state,
            optional_cycle_enabled=optional_cycle_enabled,
            optional_cycle_label=optional_cycle_label if optional_cycle_enabled else "",
            optional_cycle_intensity=optional_cycle_intensity if optional_cycle_enabled else 0,
            optional_cycle_source=optional_cycle_source if optional_cycle_enabled else "",
            summary=summary,
            lifecycle_kind=PhysiologicalRhythmLogRecord._lifecycle(raw.get("lifecycle_kind")),
            weight=max(0.0, min(weight if weight is not None else 1.0, 3.0)),
            status=_text(raw.get("status") or "active", 40) or "active",
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "source": self.source,
            "energy_curve": self.energy_curve,
            "body_label": self.body_label,
            "body_intensity": self.body_intensity,
            "body_source": self.body_source,
            "body_expires_at": self.body_expires_at,
            "recovery_actions": list(self.recovery_actions),
            "social_battery": self.social_battery,
            "attention_state": self.attention_state,
            "optional_cycle_enabled": self.optional_cycle_enabled,
            "optional_cycle_label": self.optional_cycle_label,
            "optional_cycle_intensity": self.optional_cycle_intensity,
            "optional_cycle_source": self.optional_cycle_source,
            "summary": self.summary,
            "lifecycle_kind": self.lifecycle_kind,
            "weight": self.weight,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class BehaviorFeedbackRecord:
    id: int = 0
    date: str = ""
    target_type: str = "action"
    target_id: str = ""
    scene: str = ""
    action: str = ""
    feedback: str = ""
    result: str = ""
    score: float = 0.0
    reason: str = ""
    source: str = "chat_memory"
    created_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "", source: str = "chat_memory") -> "BehaviorFeedbackRecord | None":
        if isinstance(value, BehaviorFeedbackRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        action = _text(raw.get("action"), 60)
        feedback = _text(raw.get("feedback"), 200)
        result = _text(raw.get("result"), 120)
        reason = _text(raw.get("reason"), 240)
        if not (feedback or result or reason):
            return None
        score = optional_float(raw.get("score"))
        return BehaviorFeedbackRecord(
            id=optional_int(raw.get("id")) or 0,
            date=_text(raw.get("date") or date, 20),
            target_type=_text(raw.get("target_type") or "action", 40) or "action",
            target_id=_text(raw.get("target_id"), 80),
            scene=_text(raw.get("scene"), 80),
            action=action,
            feedback=feedback,
            result=result,
            score=max(-5.0, min(score if score is not None else 0.0, 5.0)),
            reason=reason,
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "scene": self.scene,
            "action": self.action,
            "feedback": self.feedback,
            "result": self.result,
            "score": self.score,
            "reason": self.reason,
            "source": self.source,
            "created_at": self.created_at,
        }

@dataclass(slots=True)
class ReplyEffectRecord:
    id: int = 0
    scope: str = ""
    target_message_id: str = ""
    reply_text: str = ""
    outcome: str = "pending"
    warmth: int = 50
    continuity: int = 50
    friction: int = 0
    reason: str = ""
    evidence: str = ""
    source: str = "proactive_reply"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, source: str = "proactive_reply") -> "ReplyEffectRecord | None":
        if isinstance(value, ReplyEffectRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        scope = _text(raw.get("scope"), 180)
        reply_text = _text(raw.get("reply_text"), 240)
        outcome = _text(raw.get("outcome") or "pending", 40) or "pending"
        reason = _text(raw.get("reason"), 240)
        evidence = _text(raw.get("evidence"), 240)
        if not (scope or reply_text or reason or evidence):
            return None
        return ReplyEffectRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=scope,
            target_message_id=_text(raw.get("target_message_id"), 80),
            reply_text=reply_text,
            outcome=outcome,
            warmth=max(0, min(optional_int(raw.get("warmth")) or 50, 100)),
            continuity=max(0, min(optional_int(raw.get("continuity")) or 50, 100)),
            friction=max(0, min(optional_int(raw.get("friction")) or 0, 100)),
            reason=reason,
            evidence=evidence,
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "target_message_id": self.target_message_id,
            "reply_text": self.reply_text,
            "outcome": self.outcome,
            "warmth": self.warmth,
            "continuity": self.continuity,
            "friction": self.friction,
            "reason": self.reason,
            "evidence": self.evidence,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass(slots=True)
class MemoryCorrectionRecord:
    id: int = 0
    target_type: str = ""
    target_id: str = ""
    correction: str = ""
    evidence: str = ""
    confidence: float = 1.0
    applied: bool = False
    source: str = "chat_memory"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, source: str = "chat_memory") -> "MemoryCorrectionRecord | None":
        if isinstance(value, MemoryCorrectionRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        target_type = _text(raw.get("target_type"), 40)
        target_id = _text(raw.get("target_id"), 120)
        correction = _text(raw.get("correction"), 300)
        evidence = _text(raw.get("evidence"), 240)
        if not (target_type and target_id and correction):
            return None
        confidence = optional_float(raw.get("confidence"))
        return MemoryCorrectionRecord(
            id=optional_int(raw.get("id")) or 0,
            target_type=target_type,
            target_id=target_id,
            correction=correction,
            evidence=evidence,
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            applied=optional_bool(raw.get("applied")) or False,
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "correction": self.correction,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "applied": self.applied,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
