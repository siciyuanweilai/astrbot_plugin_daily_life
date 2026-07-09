from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .coerce import compact_text as _text
from .primitive import optional_float, optional_int


@dataclass(slots=True)
class LifeDecisionRecord:
    id: int = 0
    date: str = ""
    kind: str = "daily_plan"
    subject: str = ""
    decision: str = ""
    reason: str = ""
    evidence: str = ""
    outcome: str = ""
    confidence: float = 1.0
    source: str = "autonomous_life"
    created_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "", source: str = "autonomous_life") -> "LifeDecisionRecord | None":
        if isinstance(value, LifeDecisionRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        decision = _text(raw.get("decision"), 300)
        reason = _text(raw.get("reason"), 360)
        if not (decision or reason):
            return None
        confidence = optional_float(raw.get("confidence"))
        return LifeDecisionRecord(
            id=optional_int(raw.get("id")) or 0,
            date=_text(raw.get("date") or date, 20),
            kind=_text(raw.get("kind") or "daily_plan", 40) or "daily_plan",
            subject=_text(raw.get("subject"), 160),
            decision=decision,
            reason=reason,
            evidence=_text(raw.get("evidence"), 500),
            outcome=_text(raw.get("outcome"), 500),
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "kind": self.kind,
            "subject": self.subject,
            "decision": self.decision,
            "reason": self.reason,
            "evidence": self.evidence,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at,
        }
