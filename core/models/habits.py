from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_bool, optional_float, optional_int
from .coerce import compact_text as _text, compact_texts as _texts


@dataclass(slots=True)
class BehaviorPatternRecord:
    id: int = 0
    scope: str = ""
    scene: str = ""
    pattern: str = ""
    suggested_action: str = ""
    confidence: float = 1.0
    support_count: int = 1
    score: float = 0.0
    evidence: str = ""
    source: str = "chat_memory"
    last_seen: str = ""
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "", source: str = "chat_memory") -> "BehaviorPatternRecord | None":
        if isinstance(value, BehaviorPatternRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        scene = _text(raw.get("scene"), 80)
        pattern = _text(raw.get("pattern"), 240)
        suggested_action = _text(raw.get("suggested_action"), 80)
        evidence = _text(raw.get("evidence"), 240)
        if not (scene and pattern):
            return None
        confidence = optional_float(raw.get("confidence"))
        score = optional_float(raw.get("score"))
        support_count = optional_int(raw.get("support_count")) or 1
        return BehaviorPatternRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=_text(raw.get("scope"), 160),
            scene=scene,
            pattern=pattern,
            suggested_action=suggested_action,
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            support_count=max(1, support_count),
            score=max(-5.0, min(score if score is not None else 0.0, 5.0)),
            evidence=evidence,
            source=_text(raw.get("source") or source, 40) or source,
            last_seen=_text(raw.get("last_seen") or date, 20),
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "scene": self.scene,
            "pattern": self.pattern,
            "suggested_action": self.suggested_action,
            "confidence": self.confidence,
            "support_count": self.support_count,
            "score": self.score,
            "evidence": self.evidence,
            "source": self.source,
            "last_seen": self.last_seen,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass(slots=True)
class BehaviorSceneRecord:
    id: int = 0
    scope: str = ""
    scene: str = ""
    cues: list[str] = field(default_factory=list)
    preferred_action: str = ""
    avoid_action: str = ""
    outcome_hint: str = ""
    confidence: float = 1.0
    support_count: int = 1
    last_seen: str = ""
    source: str = "chat_memory"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "", source: str = "chat_memory") -> "BehaviorSceneRecord | None":
        if isinstance(value, BehaviorSceneRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        scene = _text(raw.get("scene"), 120)
        cues = _texts(raw.get("cues"), 8, 80)
        preferred_action = _text(raw.get("preferred_action"), 80)
        outcome_hint = _text(raw.get("outcome_hint"), 200)
        if not (scene and (cues or preferred_action or outcome_hint)):
            return None
        confidence = optional_float(raw.get("confidence"))
        support_count = optional_int(raw.get("support_count")) or 1
        return BehaviorSceneRecord(
            id=optional_int(raw.get("id")) or 0,
            scope=_text(raw.get("scope"), 180),
            scene=scene,
            cues=cues,
            preferred_action=preferred_action,
            avoid_action=_text(raw.get("avoid_action"), 80),
            outcome_hint=outcome_hint,
            confidence=max(0.0, min(confidence if confidence is not None else 1.0, 1.0)),
            support_count=max(1, support_count),
            last_seen=_text(raw.get("last_seen") or date, 20),
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "scene": self.scene,
            "cues": list(self.cues),
            "preferred_action": self.preferred_action,
            "avoid_action": self.avoid_action,
            "outcome_hint": self.outcome_hint,
            "confidence": self.confidence,
            "support_count": self.support_count,
            "last_seen": self.last_seen,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

@dataclass(slots=True)
class SessionMidSummaryRecord:
    session_id: str = ""
    scope_label: str = ""
    summary: str = ""
    topic: str = ""
    mood: str = ""
    participants: list[str] = field(default_factory=list)
    message_count: int = 0
    last_message_id: str = ""
    source: str = "chat_memory"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, source: str = "chat_memory") -> "SessionMidSummaryRecord | None":
        if isinstance(value, SessionMidSummaryRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        session_id = _text(raw.get("session_id"), 180)
        summary = _text(raw.get("summary"), 500)
        topic = _text(raw.get("topic"), 120)
        mood = _text(raw.get("mood"), 120)
        participants = _texts(raw.get("participants"), 12, 40)
        if not (session_id and (summary or topic or mood or participants)):
            return None
        return SessionMidSummaryRecord(
            session_id=session_id,
            scope_label=_text(raw.get("scope_label"), 120),
            summary=summary,
            topic=topic,
            mood=mood,
            participants=participants,
            message_count=max(0, optional_int(raw.get("message_count")) or 0),
            last_message_id=_text(raw.get("last_message_id"), 80),
            source=_text(raw.get("source") or source, 40) or source,
            created_at=_text(raw.get("created_at"), 40),
            updated_at=_text(raw.get("updated_at"), 40),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "scope_label": self.scope_label,
            "summary": self.summary,
            "topic": self.topic,
            "mood": self.mood,
            "participants": list(self.participants),
            "message_count": self.message_count,
            "last_message_id": self.last_message_id,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
