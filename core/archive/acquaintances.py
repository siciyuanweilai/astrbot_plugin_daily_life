from __future__ import annotations

import sqlite3
from typing import Any

from ..addresses import distinct_address
from ..models import (
    RelationshipContactRecord,
    RelationshipNote,
    RelationshipPoint,
    RelationshipRecord,
)

from typing import Protocol


def _pack_tags(tags: list[str]) -> str:
    return "\n".join(str(item).strip() for item in tags if str(item).strip())


def _unpack_tags(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").splitlines() if item.strip()]


class AcquaintanceArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _relationship_state_unlocked: Any
    _compose_relationship_contact: Any
    _compose_relationship_unlocked: Any
    _conn: Any
    _ensure_relationship_unlocked: Any
    _flag: Any
    _get_relationship_contacts_unlocked: Any
    _get_relationship_notes_unlocked: Any
    _get_relationship_points_unlocked: Any
    _insert_relationship_note_unlocked: Any
    _insert_relationship_point_unlocked: Any
    _is_generic_contact_name: Any
    _run_db: Any
    _text: Any
    _trim_relationship_notes_unlocked: Any
    _trim_relationship_points_unlocked: Any
    touch_relationship_contact: Any


class AcquaintanceArchiveComponent:
    def __init__(self, context: AcquaintanceArchiveComponentContext) -> None:
        self.context = context

    def _trim_relationship_notes_unlocked(
        self, profile_id: str, keep: int = 20
    ) -> None:
        old_notes = self.context._conn.execute(
            "SELECT id FROM relationship_notes WHERE profile_id = ? ORDER BY id DESC LIMIT -1 OFFSET ?",
            (profile_id, keep),
        ).fetchall()
        if old_notes:
            self.context._conn.executemany(
                "DELETE FROM relationship_notes WHERE id = ?",
                [(item["id"],) for item in old_notes],
            )

    def _trim_relationship_points_unlocked(
        self, profile_id: str, keep: int = 20
    ) -> None:
        old_points = self.context._conn.execute(
            "SELECT id FROM relationship_points WHERE profile_id = ? ORDER BY id DESC LIMIT -1 OFFSET ?",
            (profile_id, keep),
        ).fetchall()
        if old_points:
            self.context._conn.executemany(
                "DELETE FROM relationship_points WHERE id = ?",
                [(item["id"],) for item in old_points],
            )

    def _insert_relationship_note_unlocked(
        self,
        profile_id: str,
        date_str: str,
        content: str,
        source: str,
    ) -> None:
        latest = self.context._conn.execute(
            "SELECT content FROM relationship_notes WHERE profile_id = ? ORDER BY id DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if latest and latest["content"] == content:
            return
        self.context._conn.execute(
            "INSERT INTO relationship_notes(profile_id, date, content, source) VALUES (?, ?, ?, ?)",
            (profile_id, date_str, content, source),
        )
        self.context._trim_relationship_notes_unlocked(profile_id)

    def _insert_relationship_point_unlocked(
        self,
        profile_id: str,
        date_str: str,
        content: str,
        source: str,
        weight: float = 1.0,
    ) -> bool:
        latest = self.context._conn.execute(
            "SELECT content FROM relationship_points WHERE profile_id = ? ORDER BY id DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if latest and latest["content"] == content:
            return False
        self.context._conn.execute(
            """
            INSERT INTO relationship_points(profile_id, date, content, source, weight)
            VALUES (?, ?, ?, ?, ?)
            """,
            (profile_id, date_str, content, source, max(float(weight or 0.0), 0.0)),
        )
        self.context._trim_relationship_points_unlocked(profile_id)
        return True

    def _ensure_relationship_unlocked(
        self, profile_id: str, date_str: str, source: str = "memory"
    ) -> None:
        if self.context._conn.execute(
            "SELECT 1 FROM relationships WHERE id = ?", (profile_id,)
        ).fetchone():
            return
        self.context._conn.execute(
            """
            INSERT INTO relationships(id, name, first_seen, last_seen, interactions, source)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (
                profile_id,
                profile_id,
                self.context._text(date_str),
                self.context._text(date_str),
                self.context._text(source) or "memory",
            ),
        )

    def _compose_relationship_unlocked(self, row: sqlite3.Row) -> RelationshipRecord:
        return RelationshipRecord(
            id=row["id"],
            name=self.context._text(row["name"]),
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            interactions=int(row["interactions"] or 0),
            source=row["source"],
            notes=self.context._get_relationship_notes_unlocked(row["id"]),
            memory_points=self.context._get_relationship_points_unlocked(row["id"]),
            contacts=self.context._get_relationship_contacts_unlocked(row["id"]),
            platform=row["platform"],
            user_id=row["user_id"],
            alias=self.context._text(row["alias"]),
            persona_hint=self.context._text(row["persona_hint"]),
            subjective_name=distinct_address(row["subjective_name"],row["name"],row["alias"],row["id"],row["user_id"]),
            subjective_tags=[
                self.context._text(item) for item in _unpack_tags(row["subjective_tags"])
            ],
            relationship_story=self.context._text(row["relationship_story"]),
            relationship_state=self.context._relationship_state_unlocked(row["id"]),
        )

    async def touch_relationship(
        self,
        profile_id: str,
        name: str = "",
        note: str = "",
        date_str: str = "",
        source: str = "chat",
        platform: str = "",
        user_id: str = "",
        alias: str = "",
        persona_hint: str = "",
        subjective_name: str = "",
        subjective_tags: list[str] | None = None,
        relationship_story: str = "",
        contact_type: str = "",
        target_scope: str = "",
        group_id: str = "",
        group_name: str = "",
        is_reachable: bool = True,
        blocked_reason: str = "",
        observed_name: str = "",
    ):
        key = str(profile_id or name or "").strip()
        if not key:
            return
        display_name = self.context._text(name or key)
        note_text = self.context._text(note)
        platform_text = self.context._text(platform)
        user_id_text = self.context._text(user_id)
        alias_text = self.context._text(alias)
        if alias_text in {key, user_id_text}:
            alias_text = ""
        persona_text = self.context._text(persona_hint)
        # 兼容旧调用签名；称呼只能由有消息来源的 apply_relationship_address 修改。
        subjective_name_text = ""
        subjective_tags_text = _pack_tags(
            [self.context._text(item) for item in subjective_tags or []]
        )
        relationship_story_text = self.context._text(relationship_story)

        def write() -> None:
            nonlocal platform_text, user_id_text, alias_text, persona_text
            nonlocal subjective_name_text, subjective_tags_text, relationship_story_text
            row = self.context._conn.execute(
                "SELECT * FROM relationships WHERE id = ?",
                (key,),
            ).fetchone()
            if row:
                current_name = self.context._text(row["name"] or key)
                first_seen = str(row["first_seen"] or date_str)
                interactions = int(row["interactions"] or 0) + 1
                platform_text = platform_text or str(row["platform"] or "")
                user_id_text = user_id_text or str(row["user_id"] or "")
                alias_text = alias_text or self.context._text(row["alias"])
                persona_text = persona_text or self.context._text(row["persona_hint"])
                subjective_name_text = subjective_name_text or self.context._text(
                    row["subjective_name"]
                )
                subjective_tags_text = subjective_tags_text or _pack_tags(
                    [self.context._text(item) for item in _unpack_tags(row["subjective_tags"])]
                )
                relationship_story_text = relationship_story_text or self.context._text(
                    row["relationship_story"]
                )
            else:
                current_name = display_name or key
                first_seen = date_str
                interactions = 1
            if display_name and (
                not current_name
                or current_name == key
                or self.context._is_generic_contact_name(current_name)
            ):
                current_name = display_name
            if self.context._text(observed_name) not in {"", key, user_id_text}:
                current_name = self.context._text(observed_name)
                alias_text = current_name
            subjective_name_text = distinct_address(subjective_name_text, current_name, alias_text, key, user_id_text)
            self.context._conn.execute(
                """
                INSERT INTO relationships(
                    id, name, first_seen, last_seen, interactions,
                    platform, user_id, alias, persona_hint, subjective_name,
                    subjective_tags, relationship_story, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    first_seen = excluded.first_seen,
                    last_seen = excluded.last_seen,
                    interactions = excluded.interactions,
                    platform = excluded.platform,
                    user_id = excluded.user_id,
                    alias = excluded.alias,
                    persona_hint = excluded.persona_hint,
                    subjective_name = excluded.subjective_name,
                    subjective_tags = excluded.subjective_tags,
                    relationship_story = excluded.relationship_story,
                    source = excluded.source
                """,
                (
                    key,
                    current_name,
                    first_seen,
                    date_str,
                    interactions,
                    platform_text,
                    user_id_text,
                    alias_text,
                    persona_text,
                    subjective_name_text,
                    subjective_tags_text,
                    relationship_story_text,
                    source,
                ),
            )
            if note_text:
                self.context._insert_relationship_note_unlocked(
                    key, date_str, note_text, source
                )
            self.context._conn.commit()

        await self.context._run_db(write)

        contact_type_text = self.context._text(contact_type)
        target_scope_text = self.context._text(target_scope)
        group_id_text = self.context._text(group_id)
        if contact_type_text or target_scope_text or group_id_text:
            await self.context.touch_relationship_contact(
                key,
                platform=platform_text,
                user_id=user_id_text,
                contact_type=contact_type_text or "unknown",
                target_scope=target_scope_text,
                group_id=group_id_text,
                group_name=group_name,
                date_str=date_str,
                is_reachable=is_reachable,
                blocked_reason=blocked_reason,
                source=source,
            )

    async def touch_relationship_contact(
        self,
        profile_id: str,
        *,
        platform: str = "",
        user_id: str = "",
        contact_type: str = "unknown",
        target_scope: str = "",
        group_id: str = "",
        group_name: str = "",
        date_str: str = "",
        is_reachable: bool = True,
        blocked_reason: str = "",
        source: str = "chat",
    ) -> None:
        key = self.context._text(profile_id)
        if not key:
            return
        contact_type_text = self.context._text(contact_type) or "unknown"
        target_scope_text = self.context._text(target_scope)
        group_id_text = self.context._text(group_id)
        if not any([target_scope_text, group_id_text, self.context._text(user_id)]):
            return

        def write() -> None:
            row = self.context._conn.execute(
                "SELECT 1 FROM relationships WHERE id = ?", (key,)
            ).fetchone()
            if not row:
                self.context._conn.execute(
                    """
                    INSERT INTO relationships(id, name, first_seen, last_seen, interactions, source)
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (
                        key,
                        key,
                        self.context._text(date_str),
                        self.context._text(date_str),
                        self.context._text(source) or "chat",
                    ),
                )
            existing = self.context._conn.execute(
                """
                SELECT first_seen, group_name, blocked_reason, is_reachable
                FROM relationship_contacts
                WHERE profile_id = ? AND contact_type = ? AND target_scope = ? AND group_id = ?
                """,
                (key, contact_type_text, target_scope_text, group_id_text),
            ).fetchone()
            first_seen = (
                str(existing["first_seen"] or date_str)
                if existing
                else self.context._text(date_str)
            )
            group_name_text = self.context._text(group_name) or (
                self.context._text(existing["group_name"]) if existing else ""
            )
            blocked_reason_text = self.context._text(blocked_reason)
            if (
                existing
                and not blocked_reason_text
                and int(existing["is_reachable"] or 0)
            ):
                blocked_reason_text = self.context._text(existing["blocked_reason"])
            self.context._conn.execute(
                """
                INSERT INTO relationship_contacts(
                    profile_id, platform, user_id, contact_type, target_scope, group_id, group_name,
                    first_seen, last_seen, is_reachable, blocked_reason, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, contact_type, target_scope, group_id) DO UPDATE SET
                    platform = excluded.platform,
                    user_id = excluded.user_id,
                    group_name = excluded.group_name,
                    first_seen = excluded.first_seen,
                    last_seen = excluded.last_seen,
                    is_reachable = excluded.is_reachable,
                    blocked_reason = excluded.blocked_reason,
                    source = excluded.source
                """,
                (
                    key,
                    self.context._text(platform),
                    self.context._text(user_id),
                    contact_type_text,
                    target_scope_text,
                    group_id_text,
                    group_name_text,
                    first_seen,
                    self.context._text(date_str),
                    self.context._flag(is_reachable),
                    blocked_reason_text,
                    self.context._text(source) or "chat",
                ),
            )
            self.context._conn.commit()

        await self.context._run_db(write)

    async def mark_relationship_contact_unreachable(
        self,
        target_scope: str,
        reason: str,
        *,
        contact_type: str = "friend",
    ) -> None:
        scope = self.context._text(target_scope)
        if not scope:
            return

        def write() -> None:
            self.context._conn.execute(
                """
                UPDATE relationship_contacts
                SET is_reachable = 0, blocked_reason = ?
                WHERE target_scope = ? AND contact_type = ?
                """,
                (self.context._text(reason), scope, self.context._text(contact_type) or "friend"),
            )
            self.context._conn.commit()

        await self.context._run_db(write)

    async def get_reachable_relationship_contacts(
        self,
        profile_id: str,
        *,
        contact_type: str = "friend",
    ) -> list[RelationshipContactRecord]:
        key = self.context._text(profile_id)
        if not key:
            return []

        def read() -> list[RelationshipContactRecord]:
            rows = self.context._conn.execute(
                """
                SELECT *
                FROM relationship_contacts
                WHERE profile_id = ? AND contact_type = ? AND is_reachable = 1
                ORDER BY last_seen DESC, id DESC
                """,
                (key, self.context._text(contact_type) or "friend"),
            ).fetchall()
            return [self.context._compose_relationship_contact(row) for row in rows]

        return await self.context._run_db(read)

    async def get_recent_relationships(
        self, limit: int = 8
    ) -> list[RelationshipRecord]:
        def read() -> list[RelationshipRecord]:
            sql = (
                "SELECT * FROM relationships ORDER BY last_seen DESC, interactions DESC"
            )
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self.context._conn.execute(sql, params).fetchall()
            return [self.context._compose_relationship_unlocked(row) for row in rows]

        return await self.context._run_db(read)

    async def get_relationships_for_target(
        self, target_scope: str, limit: int = 5
    ) -> list[RelationshipRecord]:
        scope = self.context._text(target_scope)
        if not scope:
            return []

        def read() -> list[RelationshipRecord]:
            sql = """
                SELECT DISTINCT relationships.*
                FROM relationships
                INNER JOIN relationship_contacts
                    ON relationship_contacts.profile_id = relationships.id
                WHERE relationship_contacts.target_scope = ?
                ORDER BY relationships.last_seen DESC, relationships.interactions DESC
            """
            params: list[Any] = [scope]
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_relationship_unlocked(row) for row in rows]

        return await self.context._run_db(read)

    async def get_relationship(self, profile_id: str) -> RelationshipRecord | None:
        key = self.context._text(profile_id)
        if not key:
            return None

        def read() -> RelationshipRecord | None:
            row = self.context._conn.execute(
                "SELECT * FROM relationships WHERE id = ?", (key,)
            ).fetchone()
            return self.context._compose_relationship_unlocked(row) if row else None

        return await self.context._run_db(read)

    async def revise_relationship_profile(
        self,
        profile_id: str,
        *,
        date_str: str = "",
        source: str = "语义校准",
        subjective_name: str = "",
        subjective_tags: list[str] | None = None,
        relationship_story: str = "",
        note: str = "",
        relationship_points: list[str] | None = None,
    ) -> None:
        key = self.context._text(profile_id)
        if not key:
            return
        story = self.context._text(relationship_story)
        tags = [self.context._text(item) for item in subjective_tags or [] if self.context._text(item)]
        note_text = self.context._text(note)
        points = [
            self.context._text(item) for item in relationship_points or [] if self.context._text(item)
        ]
        if not any([story, tags, note_text, points]):
            return

        def write() -> None:
            row = self.context._conn.execute(
                "SELECT * FROM relationships WHERE id = ?", (key,)
            ).fetchone()
            if not row:
                return
            self.context._conn.execute(
                """
                UPDATE relationships
                SET subjective_tags = COALESCE(NULLIF(?, ''), subjective_tags),
                    relationship_story = COALESCE(NULLIF(?, ''), relationship_story),
                    source = ?
                WHERE id = ?
                """,
                (_pack_tags(tags), story, self.context._text(source) or "语义校准", key),
            )
            if note_text:
                self.context._insert_relationship_note_unlocked(
                    key,
                    self.context._text(date_str),
                    note_text,
                    self.context._text(source) or "语义校准",
                )
            for point in points[:6]:
                self.context._insert_relationship_point_unlocked(
                    key,
                    self.context._text(date_str),
                    point,
                    self.context._text(source) or "语义校准",
                )
            self.context._conn.commit()

        await self.context._run_db(write)

    def _get_relationship_notes_unlocked(
        self, profile_id: str
    ) -> list[RelationshipNote]:
        rows = self.context._conn.execute(
            """
            SELECT date, content, source
            FROM (
                SELECT id, date, content, source
                FROM relationship_notes
                WHERE profile_id = ?
                ORDER BY id DESC
                LIMIT 20
            )
            ORDER BY id
            """,
            (profile_id,),
        ).fetchall()
        return [
            RelationshipNote(
                date=row["date"],
                content=self.context._text(row["content"]),
                source=row["source"],
            )
            for row in rows
        ]

    def _get_relationship_points_unlocked(
        self, profile_id: str
    ) -> list[RelationshipPoint]:
        rows = self.context._conn.execute(
            """
            SELECT date, content, source, weight
            FROM (
                SELECT id, date, content, source, weight
                FROM relationship_points
                WHERE profile_id = ?
                ORDER BY id DESC
                LIMIT 20
            )
            ORDER BY id
            """,
            (profile_id,),
        ).fetchall()
        return [
            RelationshipPoint(
                date=row["date"],
                content=self.context._text(row["content"]),
                source=row["source"],
                weight=float(row["weight"] or 0.0),
            )
            for row in rows
        ]

    def _compose_relationship_contact(
        self, row: sqlite3.Row
    ) -> RelationshipContactRecord:
        return RelationshipContactRecord(
            profile_id=row["profile_id"],
            platform=row["platform"],
            user_id=row["user_id"],
            contact_type=row["contact_type"],
            target_scope=row["target_scope"],
            group_id=row["group_id"],
            group_name=row["group_name"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            is_reachable=bool(row["is_reachable"]),
            blocked_reason=self.context._text(row["blocked_reason"]),
            source=row["source"],
        )

    @staticmethod
    def _is_generic_contact_name(name: str) -> bool:
        return str(name or "").strip() in {"用户", "对方", "未知", "未知用户"}

    def _get_relationship_contacts_unlocked(
        self, profile_id: str
    ) -> list[RelationshipContactRecord]:
        rows = self.context._conn.execute(
            """
            SELECT *
            FROM relationship_contacts
            WHERE profile_id = ?
            ORDER BY last_seen DESC, id DESC
            """,
            (profile_id,),
        ).fetchall()
        return [self.context._compose_relationship_contact(row) for row in rows]

    async def add_relationship_point(
        self,
        profile_id: str,
        content: str,
        date_str: str = "",
        source: str = "memory",
        weight: float = 1.0,
    ) -> None:
        key = self.context._text(profile_id)
        text = self.context._text(content)
        if not key or not text:
            return

        def write() -> None:
            self.context._ensure_relationship_unlocked(key, date_str, source)
            changed = self.context._insert_relationship_point_unlocked(
                key,
                self.context._text(date_str),
                text,
                self.context._text(source) or "memory",
                weight,
            )
            if not changed:
                return
            self.context._conn.commit()

        await self.context._run_db(write)
