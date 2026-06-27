from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_bool, optional_float, optional_int
from .coerce import compact_text as _text, compact_texts as _texts


@dataclass(slots=True)
class LifeTermRecord:
    id: int = 0
    term: str = ""
    meaning: str = ""
    scope: str = ""
    scene: str = ""
    examples: list[str] = field(default_factory=list)
    familiarity: int = 0
    source: str = "chat_memory"
    confidence: float = 1.0
    last_seen: str = ""
    evidence: str = ""
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "", source: str = "chat_memory") -> "LifeTermRecord | None":
        if isinstance(value, LifeTermRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        term = _text(raw.get("term"), 60)
        meaning = _text(raw.get("meaning"), 240)
        if not (term and meaning):
            return None
        confidence = optional_float(raw.get("confidence"))
        return LifeTermRecord(
            id=optional_int(raw.get("id")) or 0,
            term=term,
            meaning=meaning,
            scope=_text(raw.get("scope"), 160),
            scene=_text(raw.get("scene"), 120),
            examples=_texts(raw.get("examples") or raw.get("example"), 6, 120),
            familiarity=max(0, min(optional_int(raw.get("familiarity")) or 0, 100)),
            source=_text(raw.get("source") or source, 40) or source,
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            last_seen=_text(raw.get("last_seen") or date, 20),
            evidence=_text(raw.get("evidence"), 240),
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "term": self.term,
            "meaning": self.meaning,
            "scope": self.scope,
            "scene": self.scene,
            "examples": list(self.examples),
            "familiarity": self.familiarity,
            "source": self.source,
            "confidence": self.confidence,
            "last_seen": self.last_seen,
            "evidence": self.evidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass(slots=True)
class MemoryBoundaryRecord:
    id: int = 0
    source_scope: str = ""
    target_scope: str = ""
    policy: str = "ask"
    reason: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def is_same_scope(source_scope: str, target_scope: str) -> bool:
        return _text(source_scope, 160).lower() == _text(target_scope, 160).lower()

    @staticmethod
    def from_value(value: Any) -> "MemoryBoundaryRecord | None":
        if isinstance(value, MemoryBoundaryRecord):
            return None if MemoryBoundaryRecord.is_same_scope(value.source_scope, value.target_scope) else value
        raw = value if isinstance(value, dict) else {}
        source_scope = _text(raw.get("source_scope") or raw.get("source"), 160)
        target_scope = _text(raw.get("target_scope") or raw.get("target"), 160)
        if not (source_scope and target_scope) or MemoryBoundaryRecord.is_same_scope(source_scope, target_scope):
            return None
        policy = _text(raw.get("policy") or "ask", 20) or "ask"
        if policy not in {"allow", "deny", "ask"}:
            policy = "ask"
        return MemoryBoundaryRecord(
            id=optional_int(raw.get("id")) or 0,
            source_scope=source_scope,
            target_scope=target_scope,
            policy=policy,
            reason=_text(raw.get("reason"), 240),
            enabled=True if raw.get("enabled") is None else bool(optional_bool(raw.get("enabled"))),
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_scope": self.source_scope,
            "target_scope": self.target_scope,
            "policy": self.policy,
            "reason": self.reason,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass(slots=True)
class MemoryMaintenanceRecord:
    id: int = 0
    date: str = ""
    summary: str = ""
    merged_count: int = 0
    corrected_count: int = 0
    pruned_count: int = 0
    reason: str = ""
    created_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "") -> "MemoryMaintenanceRecord | None":
        if isinstance(value, MemoryMaintenanceRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        summary = _text(raw.get("summary"), 300)
        reason = _text(raw.get("reason"), 240)
        if not (summary or reason):
            return None
        return MemoryMaintenanceRecord(
            id=optional_int(raw.get("id")) or 0,
            date=_text(raw.get("date") or date, 20),
            summary=summary,
            merged_count=max(optional_int(raw.get("merged_count")) or 0, 0),
            corrected_count=max(optional_int(raw.get("corrected_count")) or 0, 0),
            pruned_count=max(optional_int(raw.get("pruned_count")) or 0, 0),
            reason=reason,
            created_at=_text(raw.get("created_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "summary": self.summary,
            "merged_count": self.merged_count,
            "corrected_count": self.corrected_count,
            "pruned_count": self.pruned_count,
            "reason": self.reason,
            "created_at": self.created_at,
        }
