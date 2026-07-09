from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .primitive import optional_bool, optional_float, optional_int


@dataclass(slots=True)
class PlaceRecord:
    name: str
    type: str = "place"
    hint: str = ""
    visits: int = 0
    first_seen: str = ""
    last_seen: str = ""
    source: str = "daily"

    @staticmethod
    def from_value(value: Any) -> "PlaceRecord | None":
        if isinstance(value, PlaceRecord):
            return value
        if not isinstance(value, dict):
            return None
        name = str(value.get("name") or "").strip()
        if not name:
            return None
        return PlaceRecord(
            name=name,
            type=str(value.get("type") or "place").strip(),
            hint=str(value.get("hint") or "").strip(),
            visits=int(value.get("visits") or 0),
            first_seen=str(value.get("first_seen") or "").strip(),
            last_seen=str(value.get("last_seen") or "").strip(),
            source=str(value.get("source") or "daily").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "hint": self.hint,
            "visits": self.visits,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "source": self.source,
        }


@dataclass(slots=True)
class EventRecord:
    date: str
    summary: str
    people: list[str] = field(default_factory=list)
    place: str = ""
    importance: str = "normal"
    source: str = "event"

    @staticmethod
    def from_value(value: Any, date: str = "", source: str = "event") -> "EventRecord | None":
        if isinstance(value, EventRecord):
            return value
        if not isinstance(value, dict):
            return None
        raw = value
        summary = str(raw.get("summary") or "").strip()
        if not summary:
            return None
        people = raw.get("people", []) if isinstance(raw, dict) else []
        if isinstance(people, str):
            people = [people]
        people = [str(person).strip() for person in people if str(person).strip()] if isinstance(people, list) else []
        return EventRecord(
            date=str(raw.get("date") or date).strip(),
            summary=summary,
            people=people,
            place=str(raw.get("place") or "").strip(),
            importance=str(raw.get("importance") or "normal").strip(),
            source=str(raw.get("source") or source).strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "summary": self.summary,
            "people": list(self.people),
            "place": self.place,
            "importance": self.importance,
            "source": self.source,
        }


@dataclass(slots=True)
class RelationshipNote:
    date: str = ""
    content: str = ""
    source: str = "chat"

    @staticmethod
    def from_value(value: Any) -> "RelationshipNote":
        raw = value if isinstance(value, dict) else {}
        content = str(raw.get("content") or "").strip()
        return RelationshipNote(
            date=str(raw.get("date") or "").strip(),
            content=content,
            source=str(raw.get("source") or "chat").strip(),
        )

    def as_dict(self) -> dict[str, str]:
        return {"date": self.date, "content": self.content, "source": self.source}


@dataclass(slots=True)
class RelationshipPoint:
    date: str = ""
    content: str = ""
    source: str = "memory"
    weight: float = 1.0

    @staticmethod
    def from_value(value: Any) -> "RelationshipPoint":
        raw = value if isinstance(value, dict) else {}
        weight = optional_float(raw.get("weight"))
        content = str(raw.get("content") or "").strip()
        return RelationshipPoint(
            date=str(raw.get("date") or "").strip(),
            content=content,
            source=str(raw.get("source") or "memory").strip(),
            weight=weight if weight is not None else 1.0,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "content": self.content,
            "source": self.source,
            "weight": self.weight,
        }


@dataclass(slots=True)
class RelationshipContactRecord:
    profile_id: str = ""
    platform: str = ""
    user_id: str = ""
    contact_type: str = "unknown"
    target_scope: str = ""
    group_id: str = ""
    group_name: str = ""
    first_seen: str = ""
    last_seen: str = ""
    is_reachable: bool = True
    blocked_reason: str = ""
    source: str = "chat"

    @staticmethod
    def from_value(value: Any) -> "RelationshipContactRecord | None":
        raw = value if isinstance(value, dict) else {}
        profile_id = str(raw.get("profile_id") or "").strip()
        platform = str(raw.get("platform") or "").strip()
        user_id = str(raw.get("user_id") or "").strip()
        contact_type = str(raw.get("contact_type") or "unknown").strip() or "unknown"
        target_scope = str(raw.get("target_scope") or "").strip()
        if not profile_id and not any([platform, user_id, target_scope]):
            return None
        return RelationshipContactRecord(
            profile_id=profile_id,
            platform=platform,
            user_id=user_id,
            contact_type=contact_type,
            target_scope=target_scope,
            group_id=str(raw.get("group_id") or "").strip(),
            group_name=str(raw.get("group_name") or "").strip(),
            first_seen=str(raw.get("first_seen") or "").strip(),
            last_seen=str(raw.get("last_seen") or "").strip(),
            is_reachable=bool(raw.get("is_reachable", True)),
            blocked_reason=str(raw.get("blocked_reason") or "").strip(),
            source=str(raw.get("source") or "chat").strip() or "chat",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "platform": self.platform,
            "user_id": self.user_id,
            "contact_type": self.contact_type,
            "target_scope": self.target_scope,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "is_reachable": self.is_reachable,
            "blocked_reason": self.blocked_reason,
            "source": self.source,
        }


@dataclass(slots=True)
class RelationshipRecord:
    id: str
    name: str
    first_seen: str = ""
    last_seen: str = ""
    interactions: int = 0
    platform: str = ""
    user_id: str = ""
    alias: str = ""
    persona_hint: str = ""
    subjective_name: str = ""
    subjective_tags: list[str] = field(default_factory=list)
    relationship_story: str = ""
    source: str = "chat"
    notes: list[RelationshipNote] = field(default_factory=list)
    memory_points: list[RelationshipPoint] = field(default_factory=list)
    contacts: list[RelationshipContactRecord] = field(default_factory=list)

    @staticmethod
    def from_value(value: Any) -> "RelationshipRecord | None":
        raw = value if isinstance(value, dict) else {}
        profile_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or profile_id).strip()
        if not profile_id and not name:
            return None
        notes = raw.get("notes", [])
        notes = notes if isinstance(notes, list) else [notes]
        points = raw.get("memory_points", [])
        points = points if isinstance(points, list) else [points]
        contacts = raw.get("contacts", [])
        contacts = contacts if isinstance(contacts, list) else [contacts]
        return RelationshipRecord(
            id=profile_id or name,
            name=name or profile_id,
            first_seen=str(raw.get("first_seen") or "").strip(),
            last_seen=str(raw.get("last_seen") or "").strip(),
            interactions=optional_int(raw.get("interactions")) or 0,
            platform=str(raw.get("platform") or "").strip(),
            user_id=str(raw.get("user_id") or "").strip(),
            alias=str(raw.get("alias") or "").strip(),
            persona_hint=str(raw.get("persona_hint") or "").strip(),
            subjective_name=str(raw.get("subjective_name") or "").strip(),
            subjective_tags=[
                str(item).strip()
                for item in (raw.get("subjective_tags", []) if isinstance(raw.get("subjective_tags"), list) else [])
                if str(item).strip()
            ],
            relationship_story=str(raw.get("relationship_story") or "").strip(),
            source=str(raw.get("source") or "chat").strip(),
            notes=[RelationshipNote.from_value(note) for note in notes],
            memory_points=[RelationshipPoint.from_value(point) for point in points],
            contacts=[
                contact for contact in (RelationshipContactRecord.from_value(item) for item in contacts) if contact
            ],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "interactions": self.interactions,
            "platform": self.platform,
            "user_id": self.user_id,
            "alias": self.alias,
            "persona_hint": self.persona_hint,
            "subjective_name": self.subjective_name,
            "subjective_tags": list(self.subjective_tags),
            "relationship_story": self.relationship_story,
            "source": self.source,
            "notes": [note.as_dict() for note in self.notes],
            "memory_points": [point.as_dict() for point in self.memory_points],
            "contacts": [contact.as_dict() for contact in self.contacts],
        }


@dataclass(slots=True)
class ChatSummaryRecord:
    id: int = 0
    session_id: str = ""
    date: str = ""
    brief: str = ""
    long_summary: str = ""
    people: list[str] = field(default_factory=list)
    source: str = "chat"
    created_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "ChatSummaryRecord | None":
        if isinstance(value, ChatSummaryRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        brief = str(raw.get("brief") or "").strip()
        long_summary = str(raw.get("long_summary") or "").strip()
        if not brief and not long_summary:
            return None
        people = raw.get("people", [])
        if isinstance(people, str):
            people = [people]
        elif not isinstance(people, list):
            people = []
        return ChatSummaryRecord(
            id=optional_int(raw.get("id")) or 0,
            session_id=str(raw.get("session_id") or "").strip(),
            date=str(raw.get("date") or "").strip(),
            brief=brief,
            long_summary=long_summary,
            people=[str(item).strip() for item in people if str(item).strip()],
            source=str(raw.get("source") or "chat").strip(),
            created_at=str(raw.get("created_at") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "date": self.date,
            "brief": self.brief,
            "long_summary": self.long_summary,
            "people": list(self.people),
            "source": self.source,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class GroupEnvironmentRecord:
    id: int = 0
    session_id: str = ""
    group_id: str = ""
    group_name: str = ""
    date: str = ""
    atmosphere: str = ""
    topic: str = ""
    topic_owner: str = ""
    active_users: int = 0
    is_multithread: bool = False
    is_spam: bool = False
    is_repetition: bool = False
    is_discussing_bot: bool = False
    suitable_to_join: str = ""
    bot_watch_state: str = ""
    participation_desire: int = 0
    complexity_score: int = 0
    understanding_confidence: int = 0
    deep_analysis_needed: bool = False
    summary: str = ""
    created_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "GroupEnvironmentRecord | None":
        if isinstance(value, GroupEnvironmentRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        session_id = str(raw.get("session_id") or "").strip()
        topic = str(raw.get("topic") or "").strip()
        atmosphere = str(raw.get("atmosphere") or "").strip()
        summary = str(raw.get("summary") or "").strip()
        if not (session_id or topic or atmosphere or summary):
            return None
        return GroupEnvironmentRecord(
            id=optional_int(raw.get("id")) or 0,
            session_id=session_id,
            group_id=str(raw.get("group_id") or "").strip(),
            group_name=str(raw.get("group_name") or "").strip(),
            date=str(raw.get("date") or "").strip(),
            atmosphere=atmosphere,
            topic=topic,
            topic_owner=str(raw.get("topic_owner") or "").strip(),
            active_users=optional_int(raw.get("active_users")) or 0,
            is_multithread=optional_bool(raw.get("is_multithread")) or False,
            is_spam=optional_bool(raw.get("is_spam")) or False,
            is_repetition=optional_bool(raw.get("is_repetition")) or False,
            is_discussing_bot=optional_bool(raw.get("is_discussing_bot")) or False,
            suitable_to_join=str(raw.get("suitable_to_join") or "").strip(),
            bot_watch_state=str(raw.get("bot_watch_state") or "").strip(),
            participation_desire=optional_int(raw.get("participation_desire")) or 0,
            complexity_score=optional_int(raw.get("complexity_score")) or 0,
            understanding_confidence=optional_int(raw.get("understanding_confidence")) or 0,
            deep_analysis_needed=optional_bool(raw.get("deep_analysis_needed")) or False,
            summary=summary,
            created_at=str(raw.get("created_at") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "date": self.date,
            "atmosphere": self.atmosphere,
            "topic": self.topic,
            "topic_owner": self.topic_owner,
            "active_users": self.active_users,
            "is_multithread": self.is_multithread,
            "is_spam": self.is_spam,
            "is_repetition": self.is_repetition,
            "is_discussing_bot": self.is_discussing_bot,
            "suitable_to_join": self.suitable_to_join,
            "bot_watch_state": self.bot_watch_state,
            "participation_desire": self.participation_desire,
            "complexity_score": self.complexity_score,
            "understanding_confidence": self.understanding_confidence,
            "deep_analysis_needed": self.deep_analysis_needed,
            "summary": self.summary,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class MessageVisibilityRecord:
    id: int = 0
    session_id: str = ""
    message_id: str = ""
    sender_profile_id: str = ""
    sender_name: str = ""
    group_id: str = ""
    group_name: str = ""
    date: str = ""
    visibility: str = "seen"
    attention_level: int = 0
    priority: str = "normal"
    is_directed_at_bot: bool = False
    freshness: str = ""
    psychological_freshness: int = 0
    reactivated_from_id: int = 0
    reactivation_hint: str = ""
    reason: str = ""
    created_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "MessageVisibilityRecord | None":
        if isinstance(value, MessageVisibilityRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        session_id = str(raw.get("session_id") or "").strip()
        sender = str(raw.get("sender_profile_id") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not (session_id or sender or reason):
            return None
        return MessageVisibilityRecord(
            id=optional_int(raw.get("id")) or 0,
            session_id=session_id,
            message_id=str(raw.get("message_id") or "").strip(),
            sender_profile_id=sender,
            sender_name=str(raw.get("sender_name") or "").strip(),
            group_id=str(raw.get("group_id") or "").strip(),
            group_name=str(raw.get("group_name") or "").strip(),
            date=str(raw.get("date") or "").strip(),
            visibility=str(raw.get("visibility") or "seen").strip(),
            attention_level=optional_int(raw.get("attention_level")) or 0,
            priority=str(raw.get("priority") or "normal").strip(),
            is_directed_at_bot=optional_bool(raw.get("is_directed_at_bot")) or False,
            freshness=str(raw.get("freshness") or "").strip(),
            psychological_freshness=optional_int(raw.get("psychological_freshness")) or 0,
            reactivated_from_id=optional_int(raw.get("reactivated_from_id")) or 0,
            reactivation_hint=str(raw.get("reactivation_hint") or "").strip(),
            reason=reason,
            created_at=str(raw.get("created_at") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "sender_profile_id": self.sender_profile_id,
            "sender_name": self.sender_name,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "date": self.date,
            "visibility": self.visibility,
            "attention_level": self.attention_level,
            "priority": self.priority,
            "is_directed_at_bot": self.is_directed_at_bot,
            "freshness": self.freshness,
            "psychological_freshness": self.psychological_freshness,
            "reactivated_from_id": self.reactivated_from_id,
            "reactivation_hint": self.reactivation_hint,
            "reason": self.reason,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class ActionDecisionRecord:
    id: int = 0
    session_id: str = ""
    message_id: str = ""
    sender_profile_id: str = ""
    sender_name: str = ""
    group_id: str = ""
    group_name: str = ""
    date: str = ""
    action: str = ""
    reason: str = ""
    confidence: float = 0.0
    scene_type: str = ""
    topic_owner: str = ""
    understanding: str = ""
    deep_analysis: bool = False
    inner_monologue: str = ""
    reply_strategy: str = ""
    created_at: str = ""

    @staticmethod
    def from_value(value: Any) -> "ActionDecisionRecord | None":
        if isinstance(value, ActionDecisionRecord):
            return value
        raw = value if isinstance(value, dict) else {}
        action = str(raw.get("action") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not (action or reason):
            return None
        confidence = optional_float(raw.get("confidence"))
        return ActionDecisionRecord(
            id=optional_int(raw.get("id")) or 0,
            session_id=str(raw.get("session_id") or "").strip(),
            message_id=str(raw.get("message_id") or "").strip(),
            sender_profile_id=str(raw.get("sender_profile_id") or "").strip(),
            sender_name=str(raw.get("sender_name") or "").strip(),
            group_id=str(raw.get("group_id") or "").strip(),
            group_name=str(raw.get("group_name") or "").strip(),
            date=str(raw.get("date") or "").strip(),
            action=action,
            reason=reason,
            confidence=confidence if confidence is not None else 0.0,
            scene_type=str(raw.get("scene_type") or "").strip(),
            topic_owner=str(raw.get("topic_owner") or "").strip(),
            understanding=str(raw.get("understanding") or "").strip(),
            deep_analysis=optional_bool(raw.get("deep_analysis")) or False,
            inner_monologue=str(raw.get("inner_monologue") or "").strip(),
            reply_strategy=str(raw.get("reply_strategy") or "").strip(),
            created_at=str(raw.get("created_at") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "sender_profile_id": self.sender_profile_id,
            "sender_name": self.sender_name,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "date": self.date,
            "action": self.action,
            "reason": self.reason,
            "confidence": self.confidence,
            "scene_type": self.scene_type,
            "topic_owner": self.topic_owner,
            "understanding": self.understanding,
            "deep_analysis": self.deep_analysis,
            "inner_monologue": self.inner_monologue,
            "reply_strategy": self.reply_strategy,
            "created_at": self.created_at,
        }
