from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .coerce import compact_text as _text
from .primitive import optional_float, optional_int


@dataclass(slots=True)
class LongTermMemoryRecord:
    id: int = 0
    scope: str = ""
    category: str = "general"
    title: str = ""
    content: str = ""
    source_table: str = ""
    source_id: str = ""
    session_id: str = ""
    message_id: str = ""
    date: str = ""
    confidence: float = 1.0
    weight: float = 1.0
    status: str = "active"
    expires_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    score: float = 0.0

    @staticmethod
    def from_value(value: Any) -> "LongTermMemoryRecord | None":
        if isinstance(value, LongTermMemoryRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        content = _text(raw.get("content"), 1000)
        if not content:
            return None
        confidence = optional_float(raw.get("confidence"))
        weight = optional_float(raw.get("weight"))
        score = optional_float(raw.get("score"))
        return LongTermMemoryRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=_text(raw.get("scope"), 180),
            category=_text(raw.get("category") or "general", 40) or "general",
            title=_text(raw.get("title"), 120),
            content=content,
            source_table=_text(raw.get("source_table"), 60),
            source_id=_text(raw.get("source_id"), 80),
            session_id=_text(raw.get("session_id"), 160),
            message_id=_text(raw.get("message_id"), 80),
            date=_text(raw.get("date"), 20),
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            weight=max(0.0, min(weight if weight is not None else 1.0, 10.0)),
            status=_text(raw.get("status") or "active", 40) or "active",
            expires_at=_text(raw.get("expires_at"), 40),
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
            score=score if score is not None else 0.0,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "category": self.category,
            "title": self.title,
            "content": self.content,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "date": self.date,
            "confidence": self.confidence,
            "weight": self.weight,
            "status": self.status,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "score": self.score,
        }


@dataclass(slots=True)
class MemoryEpisodeClusterRecord:
    id: int = 0
    scope: str = ""
    title: str = ""
    summary: str = ""
    category: str = "life"
    first_date: str = ""
    last_date: str = ""
    memory_count: int = 0
    weight: float = 1.0
    status: str = "active"
    source: str = "memory_quality"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "MemoryEpisodeClusterRecord | None":
        if isinstance(value, MemoryEpisodeClusterRecord):
            return value
        if not isinstance(value, dict):
            return None
        title = _text(value.get("title"), 120)
        summary = _text(value.get("summary"), 1000)
        if not (title or summary):
            return None
        weight = optional_float(value.get("weight"))
        return MemoryEpisodeClusterRecord(
            id=optional_int(value.get("id")) or 0,
            scope=_text(value.get("scope"), 180),
            title=title or summary[:80],
            summary=summary,
            category=_text(value.get("category") or "life", 40) or "life",
            first_date=_text(value.get("first_date"), 20),
            last_date=_text(value.get("last_date"), 20),
            memory_count=optional_int(value.get("memory_count")) or 0,
            weight=max(0.0, min(weight if weight is not None else 1.0, 10.0)),
            status=_text(value.get("status") or "active", 40) or "active",
            source=_text(value.get("source") or "memory_quality", 40) or "memory_quality",
            created_at=_text(value.get("created_at"), 40),
            updated_at=_text(value.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "title": self.title,
            "summary": self.summary,
            "category": self.category,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "memory_count": self.memory_count,
            "weight": self.weight,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class MemoryEntityRecord:
    id: int = 0
    scope: str = ""
    entity_type: str = "topic"
    name: str = ""
    aliases: list[str] | None = None
    first_seen: str = ""
    last_seen: str = ""
    mention_count: int = 1
    confidence: float = 1.0
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def _split_aliases(value: Any) -> list[str]:
        if isinstance(value, list):
            raw = value
        else:
            raw = str(value or "").replace("，", ",").split(",")
        return [alias for item in raw if (alias := _text(item, 40))][:12]

    @staticmethod
    def from_value(value: Any) -> "MemoryEntityRecord | None":
        if isinstance(value, MemoryEntityRecord):
            return value
        if not isinstance(value, dict):
            return None
        name = _text(value.get("name"), 80)
        if not name:
            return None
        confidence = optional_float(value.get("confidence"))
        return MemoryEntityRecord(
            id=optional_int(value.get("id")) or 0,
            scope=_text(value.get("scope"), 180),
            entity_type=_text(value.get("entity_type") or "topic", 40) or "topic",
            name=name,
            aliases=MemoryEntityRecord._split_aliases(value.get("aliases")),
            first_seen=_text(value.get("first_seen"), 20),
            last_seen=_text(value.get("last_seen"), 20),
            mention_count=max(1, optional_int(value.get("mention_count")) or 1),
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            status=_text(value.get("status") or "active", 40) or "active",
            created_at=_text(value.get("created_at"), 40),
            updated_at=_text(value.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "entity_type": self.entity_type,
            "name": self.name,
            "aliases": list(self.aliases or []),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "mention_count": self.mention_count,
            "confidence": self.confidence,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class MemoryConflictRecord:
    id: int = 0
    scope: str = ""
    memory_id: int = 0
    related_memory_id: int = 0
    conflict_type: str = "tension"
    summary: str = ""
    resolution: str = ""
    status: str = "open"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "MemoryConflictRecord | None":
        if isinstance(value, MemoryConflictRecord):
            return value
        if not isinstance(value, dict):
            return None
        memory_id = optional_int(value.get("memory_id")) or 0
        related_id = optional_int(value.get("related_memory_id")) or 0
        summary = _text(value.get("summary"), 300)
        if not (memory_id and related_id and summary):
            return None
        return MemoryConflictRecord(
            id=optional_int(value.get("id")) or 0,
            scope=_text(value.get("scope"), 180),
            memory_id=memory_id,
            related_memory_id=related_id,
            conflict_type=_text(value.get("conflict_type") or "tension", 40) or "tension",
            summary=summary,
            resolution=_text(value.get("resolution"), 300),
            status=_text(value.get("status") or "open", 40) or "open",
            created_at=_text(value.get("created_at"), 40),
            updated_at=_text(value.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "memory_id": self.memory_id,
            "related_memory_id": self.related_memory_id,
            "conflict_type": self.conflict_type,
            "summary": self.summary,
            "resolution": self.resolution,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class MemoryDecisionLinkRecord:
    id: int = 0
    decision_id: int = 0
    memory_id: int = 0
    influence: str = ""
    weight: float = 1.0
    created_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "MemoryDecisionLinkRecord | None":
        if isinstance(value, MemoryDecisionLinkRecord):
            return value
        if not isinstance(value, dict):
            return None
        decision_id = optional_int(value.get("decision_id")) or 0
        memory_id = optional_int(value.get("memory_id")) or 0
        if not (decision_id and memory_id):
            return None
        weight = optional_float(value.get("weight"))
        return MemoryDecisionLinkRecord(
            id=optional_int(value.get("id")) or 0,
            decision_id=decision_id,
            memory_id=memory_id,
            influence=_text(value.get("influence"), 240),
            weight=max(0.0, min(weight if weight is not None else 1.0, 10.0)),
            created_at=_text(value.get("created_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "decision_id": self.decision_id,
            "memory_id": self.memory_id,
            "influence": self.influence,
            "weight": self.weight,
            "created_at": self.created_at,
        }
