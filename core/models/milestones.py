from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_float, optional_int


@dataclass(slots=True)
class PreferenceRecord:
    id: int = 0
    category: str = "general"
    content: str = ""
    weight: float = 1.0
    evidence: str = ""
    last_seen: str = ""
    source: str = "learning"
    created_at: str = ""
    updated_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "", source: str = "learning") -> "PreferenceRecord | None":
        if isinstance(value, PreferenceRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        content = str((raw.get("content") if isinstance(value, dict) else value) or "").strip()
        if not content:
            return None
        category = str(raw.get("category") or "general").strip() or "general"
        weight = optional_float(raw.get("weight"))
        return PreferenceRecord(
            id=optional_int(raw.get("id")) or 0,
            category=category[:40],
            content=content[:240],
            weight=max(0.1, min(weight if weight is not None else 1.0, 5.0)),
            evidence=str(raw.get("evidence") or "").strip()[:240],
            last_seen=str(raw.get("last_seen") or date).strip(),
            source=str(raw.get("source") or source).strip() or source,
            created_at=str(raw.get("created_at") or "").strip(),
            updated_at=str(raw.get("updated_at") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "content": self.content,
            "weight": self.weight,
            "evidence": self.evidence,
            "last_seen": self.last_seen,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class LifeEventRecord:
    id: int = 0
    date: str = ""
    title: str = ""
    detail: str = ""
    effect: str = ""
    status: str = "open"
    source: str = "life_event"
    created_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "", source: str = "life_event") -> "LifeEventRecord | None":
        if isinstance(value, LifeEventRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        title = str((raw.get("title") if isinstance(value, dict) else value) or "").strip()
        if not title:
            return None
        return LifeEventRecord(
            id=optional_int(raw.get("id")) or 0,
            date=str(raw.get("date") or date).strip(),
            title=title[:120],
            detail=str(raw.get("detail") or "").strip()[:360],
            effect=str(raw.get("effect") or "").strip()[:240],
            status=str(raw.get("status") or "open").strip() or "open",
            source=str(raw.get("source") or source).strip() or source,
            created_at=str(raw.get("created_at") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "title": self.title,
            "detail": self.detail,
            "effect": self.effect,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class DailyReviewRecord:
    date: str
    summary: str = ""
    memory_points: list[str] = field(default_factory=list)
    preference_points: list[PreferenceRecord] = field(default_factory=list)
    sleep_debt_delta: float = 0.0
    energy_carryover: float = 60.0
    life_events: list[LifeEventRecord] = field(default_factory=list)
    created_at: str = ""

    @staticmethod
    def from_value(value: Any, *, date: str = "") -> "DailyReviewRecord | None":
        if isinstance(value, DailyReviewRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        date_text = str(raw.get("date") or date).strip()
        if not date_text:
            return None
        prefs = raw.get("preference_points") or []
        events = raw.get("life_events") or []
        memory_points = raw.get("memory_points") or []
        if isinstance(memory_points, str):
            memory_points = [memory_points]
        elif not isinstance(memory_points, list):
            memory_points = []
        return DailyReviewRecord(
            date=date_text,
            summary=str(raw.get("summary") or "").strip()[:500],
            memory_points=[str(item).strip()[:240] for item in memory_points if str(item).strip()],
            preference_points=[
                item
                for item in (
                    PreferenceRecord.from_value(pref, date=date_text, source="daily_review")
                    for pref in (prefs if isinstance(prefs, list) else [])
                )
                if item is not None
            ],
            sleep_debt_delta=optional_float(raw.get("sleep_debt_delta")) or 0.0,
            energy_carryover=optional_float(raw.get("energy_carryover")) or 60.0,
            life_events=[
                item
                for item in (
                    LifeEventRecord.from_value(event, date=date_text, source="daily_review")
                    for event in (events if isinstance(events, list) else [])
                )
                if item is not None
            ],
            created_at=str(raw.get("created_at") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "summary": self.summary,
            "memory_points": list(self.memory_points),
            "preference_points": [item.as_dict() for item in self.preference_points],
            "sleep_debt_delta": self.sleep_debt_delta,
            "energy_carryover": self.energy_carryover,
            "life_events": [item.as_dict() for item in self.life_events],
            "created_at": self.created_at,
        }
