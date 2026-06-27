from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_bool, optional_float, optional_int
from .coerce import compact_text as _text, compact_texts as _texts


@dataclass(slots=True)
class LifeEpisodeRecord:
    id: int = 0
    date: str = ""
    title: str = ""
    summary: str = ""
    kind: str = "daily"
    source: str = "daily"
    related_people: list[str] = field(default_factory=list)
    related_places: list[str] = field(default_factory=list)
    impact: str = ""
    confidence: float = 1.0
    status: str = "open"
    protected: bool = False
    correction: str = ""
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "", source: str = "daily") -> "LifeEpisodeRecord | None":
        if isinstance(value, LifeEpisodeRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        title = _text(raw.get("title"), 120)
        summary = _text(raw.get("summary"), 500)
        if not title:
            return None
        confidence = optional_float(raw.get("confidence"))
        return LifeEpisodeRecord(
            id=optional_int(raw.get("id")) or 0,
            date=_text(raw.get("date") or date, 20),
            title=title,
            summary=summary,
            kind=_text(raw.get("kind") or "daily", 40) or "daily",
            source=_text(raw.get("source") or source, 40) or source,
            related_people=_texts(raw.get("related_people") or raw.get("people"), 10, 40),
            related_places=_texts(raw.get("related_places") or raw.get("places"), 8, 40),
            impact=_text(raw.get("impact"), 240),
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            status=_text(raw.get("status") or "open", 40) or "open",
            protected=optional_bool(raw.get("protected")) or False,
            correction=_text(raw.get("correction"), 240),
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "title": self.title,
            "summary": self.summary,
            "kind": self.kind,
            "source": self.source,
            "related_people": list(self.related_people),
            "related_places": list(self.related_places),
            "impact": self.impact,
            "confidence": self.confidence,
            "status": self.status,
            "protected": self.protected,
            "correction": self.correction,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass(slots=True)
class MemoryEvidenceRecord:
    id: int = 0
    target_type: str = ""
    target_id: str = ""
    evidence_type: str = "observation"
    source_table: str = ""
    source_id: str = ""
    session_id: str = ""
    message_id: str = ""
    date: str = ""
    summary: str = ""
    confidence: float = 1.0
    status: str = "active"
    created_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "") -> "MemoryEvidenceRecord | None":
        if isinstance(value, MemoryEvidenceRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        target_type = _text(raw.get("target_type"), 40)
        target_id = _text(raw.get("target_id"), 80)
        summary = _text(raw.get("summary"), 300)
        if not (target_type and target_id and summary):
            return None
        confidence = optional_float(raw.get("confidence"))
        return MemoryEvidenceRecord(
            id=optional_int(raw.get("id")) or 0,
            target_type=target_type,
            target_id=target_id,
            evidence_type=_text(raw.get("evidence_type") or "observation", 40) or "observation",
            source_table=_text(raw.get("source_table") or raw.get("source"), 60),
            source_id=_text(raw.get("source_id"), 80),
            session_id=_text(raw.get("session_id"), 160),
            message_id=_text(raw.get("message_id"), 80),
            date=_text(raw.get("date") or date, 20),
            summary=summary,
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            status=_text(raw.get("status") or "active", 40) or "active",
            created_at=_text(raw.get("created_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "evidence_type": self.evidence_type,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "date": self.date,
            "summary": self.summary,
            "confidence": self.confidence,
            "status": self.status,
            "created_at": self.created_at,
        }
