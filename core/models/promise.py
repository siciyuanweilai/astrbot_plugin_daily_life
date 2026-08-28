from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_float, optional_int


@dataclass(slots=True)
class CommitmentRecord:
    id: int = 0
    content: str = ""
    kind: str = "plan"
    trigger_date: str = ""
    trigger_time: str = ""
    time_window: str = ""
    owner: str = "未定"
    media_kind: str = "none"
    people: list[str] = field(default_factory=list)
    place: str = ""
    status: str = "active"
    confidence: float = 1.0
    source: str = "manual"
    source_session: str = ""
    source_message_id: str = ""
    source_message: str = ""
    created_at: str = ""
    activated_at: str = ""
    completed_at: str = ""

    @staticmethod
    def from_value(value: Any) -> CommitmentRecord | None:
        if isinstance(value, CommitmentRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        content = str(raw.get("content") or "").strip()
        if not content:
            return None
        people = raw.get("people", [])
        if isinstance(people, str):
            people = [people]
        elif not isinstance(people, list):
            people = []
        confidence = optional_float(raw.get("confidence"))
        owner = str(raw.get("owner") or "未定").strip()
        if owner not in {"当前角色", "说话人", "共同", "未定"}:
            owner = "未定"
        media_kind = str(raw.get("media_kind") or "none").strip().lower()
        if media_kind not in {"none", "photo", "video"}:
            media_kind = "none"
        return CommitmentRecord(
            id=optional_int(raw.get("id")) or 0,
            content=content,
            kind=str(raw.get("kind") or "plan").strip() or "plan",
            trigger_date=str(raw.get("trigger_date") or "").strip(),
            trigger_time=str(raw.get("trigger_time") or "").strip(),
            time_window=str(raw.get("time_window") or "").strip(),
            owner=owner,
            media_kind=media_kind,
            people=[str(person).strip() for person in people if str(person).strip()],
            place=str(raw.get("place") or "").strip(),
            status=str(raw.get("status") or "active").strip() or "active",
            confidence=confidence if confidence is not None else 1.0,
            source=str(raw.get("source") or "manual").strip() or "manual",
            source_session=str(raw.get("source_session") or "").strip(),
            source_message_id=str(raw.get("source_message_id") or "").strip(),
            source_message=str(raw.get("source_message") or "").strip(),
            created_at=str(raw.get("created_at") or "").strip(),
            activated_at=str(raw.get("activated_at") or "").strip(),
            completed_at=str(raw.get("completed_at") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "kind": self.kind,
            "trigger_date": self.trigger_date,
            "trigger_time": self.trigger_time,
            "time_window": self.time_window,
            "owner": self.owner,
            "media_kind": self.media_kind,
            "people": list(self.people),
            "place": self.place,
            "status": self.status,
            "confidence": self.confidence,
            "source": self.source,
            "source_session": self.source_session,
            "source_message_id": self.source_message_id,
            "source_message": self.source_message,
            "created_at": self.created_at,
            "activated_at": self.activated_at,
            "completed_at": self.completed_at,
        }
