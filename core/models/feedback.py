from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_bool, optional_float, optional_int
from .coerce import compact_text as _text, compact_texts as _texts


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
        outcome = _text(raw.get("outcome") or raw.get("result") or "pending", 40) or "pending"
        reason = _text(raw.get("reason"), 240)
        evidence = _text(raw.get("evidence") or raw.get("feedback"), 240)
        if not (scope or reply_text or reason or evidence):
            return None
        return ReplyEffectRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=scope,
            target_message_id=_text(raw.get("target_message_id") or raw.get("message_id"), 80),
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
        correction = _text(raw.get("correction") or raw.get("content"), 300)
        evidence = _text(raw.get("evidence") or raw.get("reason"), 240)
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
