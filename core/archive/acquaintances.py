import sqlite3
from typing import Any

from ..models import (
    RelationshipContactRecord,
    RelationshipNote,
    RelationshipPoint,
    RelationshipRecord,
)


def _pack_tags(tags: list[str]) -> str:
    return "\n".join(str(item).strip() for item in tags if str(item).strip())


def _unpack_tags(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").splitlines() if item.strip()]


class AcquaintanceArchiveMixin:
    def _trim_relationship_notes_unlocked(self, profile_id: str, keep: int = 20) -> None:
        old_notes = self._conn.execute(
            "SELECT id FROM relationship_notes WHERE profile_id = ? ORDER BY id DESC LIMIT -1 OFFSET ?",
            (profile_id, keep),
        ).fetchall()
        if old_notes:
            self._conn.executemany("DELETE FROM relationship_notes WHERE id = ?", [(item["id"],) for item in old_notes])

    def _trim_relationship_points_unlocked(self, profile_id: str, keep: int = 20) -> None:
        old_points = self._conn.execute(
            "SELECT id FROM relationship_points WHERE profile_id = ? ORDER BY id DESC LIMIT -1 OFFSET ?",
            (profile_id, keep),
        ).fetchall()
        if old_points:
            self._conn.executemany("DELETE FROM relationship_points WHERE id = ?", [(item["id"],) for item in old_points])

    def _insert_relationship_note_unlocked(
        self,
        profile_id: str,
        date_str: str,
        content: str,
        source: str,
    ) -> None:
        latest = self._conn.execute(
            "SELECT content FROM relationship_notes WHERE profile_id = ? ORDER BY id DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if latest and latest["content"] == content:
            return
        self._conn.execute(
            "INSERT INTO relationship_notes(profile_id, date, content, source) VALUES (?, ?, ?, ?)",
            (profile_id, date_str, content, source),
        )
        self._trim_relationship_notes_unlocked(profile_id)

    def _insert_relationship_point_unlocked(
        self,
        profile_id: str,
        date_str: str,
        content: str,
        source: str,
        weight: float = 1.0,
    ) -> bool:
        latest = self._conn.execute(
            "SELECT content FROM relationship_points WHERE profile_id = ? ORDER BY id DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if latest and latest["content"] == content:
            return False
        self._conn.execute(
            """
            INSERT INTO relationship_points(profile_id, date, content, source, weight)
            VALUES (?, ?, ?, ?, ?)
            """,
            (profile_id, date_str, content, source, max(float(weight or 0.0), 0.0)),
        )
        self._trim_relationship_points_unlocked(profile_id)
        return True

    def _ensure_relationship_unlocked(self, profile_id: str, date_str: str, source: str = "memory") -> None:
        if self._conn.execute("SELECT 1 FROM relationships WHERE id = ?", (profile_id,)).fetchone():
            return
        self._conn.execute(
            """
            INSERT INTO relationships(id, name, first_seen, last_seen, interactions, source)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (profile_id, profile_id, self._text(date_str), self._text(date_str), self._text(source) or "memory"),
        )

    def _compose_relationship_unlocked(self, row: sqlite3.Row) -> RelationshipRecord:
        return RelationshipRecord(
            id=row["id"],
            name=self._text(row["name"]),
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            interactions=int(row["interactions"] or 0),
            source=row["source"],
            notes=self._get_relationship_notes_unlocked(row["id"]),
            memory_points=self._get_relationship_points_unlocked(row["id"]),
            contacts=self._get_relationship_contacts_unlocked(row["id"]),
            platform=row["platform"],
            user_id=row["user_id"],
            alias=self._text(row["alias"]),
            persona_hint=self._text(row["persona_hint"]),
            subjective_name=self._text(row["subjective_name"]),
            subjective_tags=[self._text(item) for item in _unpack_tags(row["subjective_tags"])],
            relationship_story=self._text(row["relationship_story"]),
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
    ):
        key = str(profile_id or name or "").strip()
        if not key:
            return
        display_name = self._text(name or key)
        note_text = self._text(note)
        platform_text = self._text(platform)
        user_id_text = self._text(user_id)
        alias_text = self._text(alias)
        persona_text = self._text(persona_hint)
        subjective_name_text = self._text(subjective_name)
        subjective_tags_text = _pack_tags([self._text(item) for item in subjective_tags or []])
        relationship_story_text = self._text(relationship_story)

        def write() -> None:
            nonlocal platform_text, user_id_text, alias_text, persona_text
            nonlocal subjective_name_text, subjective_tags_text, relationship_story_text
            row = self._conn.execute(
                "SELECT * FROM relationships WHERE id = ?",
                (key,),
            ).fetchone()
            if row:
                current_name = self._text(row["name"] or key)
                first_seen = str(row["first_seen"] or date_str)
                interactions = int(row["interactions"] or 0) + 1
                platform_text = platform_text or str(row["platform"] or "")
                user_id_text = user_id_text or str(row["user_id"] or "")
                alias_text = alias_text or self._text(row["alias"])
                persona_text = persona_text or self._text(row["persona_hint"])
                subjective_name_text = subjective_name_text or self._text(row["subjective_name"])
                subjective_tags_text = subjective_tags_text or _pack_tags(
                    [self._text(item) for item in _unpack_tags(row["subjective_tags"])]
                )
                relationship_story_text = relationship_story_text or self._text(row["relationship_story"])
            else:
                current_name = display_name or key
                first_seen = date_str
                interactions = 1
            if display_name and (not current_name or current_name == key or self._is_generic_contact_name(current_name)):
                current_name = display_name
            self._conn.execute(
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
                self._insert_relationship_note_unlocked(key, date_str, note_text, source)
            self._conn.commit()

        await self._run_db(write)

        contact_type_text = self._text(contact_type)
        target_scope_text = self._text(target_scope)
        group_id_text = self._text(group_id)
        if contact_type_text or target_scope_text or group_id_text:
            await self.touch_relationship_contact(
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
        key = self._text(profile_id)
        if not key:
            return
        contact_type_text = self._text(contact_type) or "unknown"
        target_scope_text = self._text(target_scope)
        group_id_text = self._text(group_id)
        if not any([target_scope_text, group_id_text, self._text(user_id)]):
            return

        def write() -> None:
            row = self._conn.execute("SELECT 1 FROM relationships WHERE id = ?", (key,)).fetchone()
            if not row:
                self._conn.execute(
                    """
                    INSERT INTO relationships(id, name, first_seen, last_seen, interactions, source)
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (key, key, self._text(date_str), self._text(date_str), self._text(source) or "chat"),
                )
            existing = self._conn.execute(
                """
                SELECT first_seen, group_name, blocked_reason, is_reachable
                FROM relationship_contacts
                WHERE profile_id = ? AND contact_type = ? AND target_scope = ? AND group_id = ?
                """,
                (key, contact_type_text, target_scope_text, group_id_text),
            ).fetchone()
            first_seen = str(existing["first_seen"] or date_str) if existing else self._text(date_str)
            group_name_text = self._text(group_name) or (self._text(existing["group_name"]) if existing else "")
            blocked_reason_text = self._text(blocked_reason)
            if existing and not blocked_reason_text and int(existing["is_reachable"] or 0):
                blocked_reason_text = self._text(existing["blocked_reason"])
            self._conn.execute(
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
                    self._text(platform),
                    self._text(user_id),
                    contact_type_text,
                    target_scope_text,
                    group_id_text,
                    group_name_text,
                    first_seen,
                    self._text(date_str),
                    self._flag(is_reachable),
                    blocked_reason_text,
                    self._text(source) or "chat",
                ),
            )
            self._conn.commit()

        await self._run_db(write)

    async def mark_relationship_contact_unreachable(
        self,
        target_scope: str,
        reason: str,
        *,
        contact_type: str = "friend",
    ) -> None:
        scope = self._text(target_scope)
        if not scope:
            return

        def write() -> None:
            self._conn.execute(
                """
                UPDATE relationship_contacts
                SET is_reachable = 0, blocked_reason = ?
                WHERE target_scope = ? AND contact_type = ?
                """,
                (self._text(reason), scope, self._text(contact_type) or "friend"),
            )
            self._conn.commit()

        await self._run_db(write)

    async def get_reachable_relationship_contacts(
        self,
        profile_id: str,
        *,
        contact_type: str = "friend",
    ) -> list[RelationshipContactRecord]:
        key = self._text(profile_id)
        if not key:
            return []

        def read() -> list[RelationshipContactRecord]:
            rows = self._conn.execute(
                """
                SELECT *
                FROM relationship_contacts
                WHERE profile_id = ? AND contact_type = ? AND is_reachable = 1
                ORDER BY last_seen DESC, id DESC
                """,
                (key, self._text(contact_type) or "friend"),
            ).fetchall()
            return [self._compose_relationship_contact(row) for row in rows]

        return await self._run_db(read)

    async def get_recent_relationships(self, limit: int = 8) -> list[RelationshipRecord]:
        def read() -> list[RelationshipRecord]:
            sql = "SELECT * FROM relationships ORDER BY last_seen DESC, interactions DESC"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self._conn.execute(sql, params).fetchall()
            return [self._compose_relationship_unlocked(row) for row in rows]

        return await self._run_db(read)

    async def get_relationship(self, profile_id: str) -> RelationshipRecord | None:
        key = self._text(profile_id)
        if not key:
            return None

        def read() -> RelationshipRecord | None:
            row = self._conn.execute("SELECT * FROM relationships WHERE id = ?", (key,)).fetchone()
            return self._compose_relationship_unlocked(row) if row else None

        return await self._run_db(read)

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
        key = self._text(profile_id)
        if not key:
            return
        story = self._text(relationship_story)
        name = self._text(subjective_name)
        tags = [self._text(item) for item in subjective_tags or [] if self._text(item)]
        note_text = self._text(note)
        points = [self._text(item) for item in relationship_points or [] if self._text(item)]
        if not any([story, name, tags, note_text, points]):
            return

        def write() -> None:
            row = self._conn.execute("SELECT * FROM relationships WHERE id = ?", (key,)).fetchone()
            if not row:
                return
            self._conn.execute(
                """
                UPDATE relationships
                SET subjective_name = COALESCE(NULLIF(?, ''), subjective_name),
                    subjective_tags = COALESCE(NULLIF(?, ''), subjective_tags),
                    relationship_story = COALESCE(NULLIF(?, ''), relationship_story),
                    source = ?
                WHERE id = ?
                """,
                (name, _pack_tags(tags), story, self._text(source) or "语义校准", key),
            )
            if note_text:
                self._insert_relationship_note_unlocked(
                    key,
                    self._text(date_str),
                    note_text,
                    self._text(source) or "语义校准",
                )
            for point in points[:6]:
                self._insert_relationship_point_unlocked(
                    key,
                    self._text(date_str),
                    point,
                    self._text(source) or "语义校准",
                )
            self._conn.commit()

        await self._run_db(write)

    def _get_relationship_notes_unlocked(self, profile_id: str) -> list[RelationshipNote]:
        rows = self._conn.execute(
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
            RelationshipNote(date=row["date"], content=self._text(row["content"]), source=row["source"])
            for row in rows
        ]

    def _get_relationship_points_unlocked(self, profile_id: str) -> list[RelationshipPoint]:
        rows = self._conn.execute(
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
                content=self._text(row["content"]),
                source=row["source"],
                weight=float(row["weight"] or 0.0),
            )
            for row in rows
        ]

    def _compose_relationship_contact(self, row: sqlite3.Row) -> RelationshipContactRecord:
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
            blocked_reason=self._text(row["blocked_reason"]),
            source=row["source"],
        )

    @staticmethod
    def _is_generic_contact_name(name: str) -> bool:
        return str(name or "").strip() in {"用户", "对方", "未知", "未知用户"}

    def _get_relationship_contacts_unlocked(self, profile_id: str) -> list[RelationshipContactRecord]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM relationship_contacts
            WHERE profile_id = ?
            ORDER BY last_seen DESC, id DESC
            """,
            (profile_id,),
        ).fetchall()
        return [self._compose_relationship_contact(row) for row in rows]

    async def add_relationship_point(
        self,
        profile_id: str,
        content: str,
        date_str: str = "",
        source: str = "memory",
        weight: float = 1.0,
    ) -> None:
        key = self._text(profile_id)
        text = self._text(content)
        if not key or not text:
            return

        def write() -> None:
            self._ensure_relationship_unlocked(key, date_str, source)
            changed = self._insert_relationship_point_unlocked(
                key,
                self._text(date_str),
                text,
                self._text(source) or "memory",
                weight,
            )
            if not changed:
                return
            self._conn.commit()

        await self._run_db(write)
