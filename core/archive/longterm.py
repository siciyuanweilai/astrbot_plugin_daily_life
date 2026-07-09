from __future__ import annotations

import sqlite3
from typing import Any

from ..models import (
    LongTermMemoryRecord,
    MemoryConflictRecord,
    MemoryDecisionLinkRecord,
    MemoryEntityRecord,
    MemoryEpisodeClusterRecord,
)


class LongTermMemoryArchiveMixin:
    _ENTITY_CATEGORY_MAP = {
        "relationship": "person",
        "place": "place",
        "event": "event",
        "episode": "event",
        "chat_summary": "topic",
        "short_term": "constraint",
        "correction": "correction",
    }

    def _compose_long_term_memory(self, row: sqlite3.Row, score: float = 0.0) -> LongTermMemoryRecord:
        return LongTermMemoryRecord.from_value(
            {
                "id": row["id"],
                "scope": row["scope"],
                "category": row["category"],
                "title": row["title"],
                "content": row["content"],
                "source_table": row["source_table"],
                "source_id": row["source_id"],
                "session_id": row["session_id"],
                "message_id": row["message_id"],
                "date": row["date"],
                "confidence": row["confidence"],
                "weight": row["weight"],
                "status": row["status"],
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "score": score,
            }
        ) or LongTermMemoryRecord()

    def _compose_memory_cluster(self, row: sqlite3.Row) -> MemoryEpisodeClusterRecord:
        return MemoryEpisodeClusterRecord.from_value(dict(row)) or MemoryEpisodeClusterRecord()

    def _compose_memory_entity(self, row: sqlite3.Row) -> MemoryEntityRecord:
        data = dict(row)
        data["aliases"] = [item for item in str(data.get("aliases") or "").split(",") if item]
        return MemoryEntityRecord.from_value(data) or MemoryEntityRecord()

    def _compose_memory_conflict(self, row: sqlite3.Row) -> MemoryConflictRecord:
        return MemoryConflictRecord.from_value(dict(row)) or MemoryConflictRecord()

    def _compose_memory_decision_link(self, row: sqlite3.Row) -> MemoryDecisionLinkRecord:
        return MemoryDecisionLinkRecord.from_value(dict(row)) or MemoryDecisionLinkRecord()

    @staticmethod
    def _memory_search_query(text: str) -> str:
        tokens = [
            token.strip().replace('"', " ").replace("'", " ")
            for token in str(text or "")
            .replace("，", " ")
            .replace("。", " ")
            .replace("；", " ")
            .replace("：", " ")
            .replace("、", " ")
            .replace("|", " ")
            .replace("｜", " ")
            .split()
            if token.strip()
        ]
        if not tokens:
            return ""
        return " OR ".join(f'"{token}"' for token in tokens[:12])

    def _upsert_long_term_memory_unlocked(self, memory: LongTermMemoryRecord) -> LongTermMemoryRecord | None:
        if not memory.content:
            return None
        cursor = self._conn.execute(
            """
            INSERT INTO long_term_memories(
                scope, category, title, content, source_table, source_id,
                session_id, message_id, date, confidence, weight, status, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, category, content, source_table, source_id) DO UPDATE SET
                title = COALESCE(NULLIF(excluded.title, ''), long_term_memories.title),
                session_id = COALESCE(NULLIF(excluded.session_id, ''), long_term_memories.session_id),
                message_id = COALESCE(NULLIF(excluded.message_id, ''), long_term_memories.message_id),
                date = COALESCE(NULLIF(excluded.date, ''), long_term_memories.date),
                confidence = MAX(long_term_memories.confidence, excluded.confidence),
                weight = MAX(long_term_memories.weight, excluded.weight),
                status = excluded.status,
                expires_at = COALESCE(NULLIF(excluded.expires_at, ''), long_term_memories.expires_at),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                self._text(memory.scope),
                self._text(memory.category) or "general",
                self._text(memory.title),
                self._text(memory.content),
                self._text(memory.source_table),
                self._text(memory.source_id),
                self._text(memory.session_id),
                self._text(memory.message_id),
                self._text(memory.date),
                float(memory.confidence),
                float(memory.weight),
                self._text(memory.status) or "active",
                self._text(memory.expires_at),
            ),
        )
        row_id = cursor.lastrowid
        if not row_id:
            row = self._conn.execute(
                """
                SELECT * FROM long_term_memories
                WHERE scope = ? AND category = ? AND content = ?
                  AND source_table = ? AND source_id = ?
                """,
                (
                    self._text(memory.scope),
                    self._text(memory.category) or "general",
                    self._text(memory.content),
                    self._text(memory.source_table),
                    self._text(memory.source_id),
                ),
            ).fetchone()
        else:
            row = self._conn.execute("SELECT * FROM long_term_memories WHERE id = ?", (row_id,)).fetchone()
        saved = self._compose_long_term_memory(row) if row else None
        if saved:
            self._maintain_long_term_memory_quality_unlocked(saved)
        return saved

    def _memory_lifecycle_kind(self, category: str) -> str:
        category = self._text(category).lower()
        if category == "short_term" or category.startswith("focus"):
            return "short_term"
        if category == "correction" or "correction" in category:
            return "correction"
        if category.startswith("preference") or category in {"relationship", "place", "fact"}:
            return "long_term"
        return "episode"

    def _apply_memory_lifecycle_unlocked(self, memory: LongTermMemoryRecord) -> None:
        kind = self._memory_lifecycle_kind(memory.category)
        weight = float(memory.weight or 1.0)
        expires_at = self._text(memory.expires_at)
        if kind == "short_term" and not expires_at:
            expires_at = self._conn.execute("SELECT DATE('now', '+7 days', 'localtime') AS day").fetchone()["day"]
            weight = max(weight, 1.2)
        elif kind in {"long_term", "correction"}:
            weight = max(weight, 2.0 if kind == "correction" else 1.5)
        elif kind == "episode":
            weight = max(0.6, weight)
        self._conn.execute(
            """
            UPDATE long_term_memories
            SET weight = ?, expires_at = COALESCE(NULLIF(?, ''), expires_at),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (weight, expires_at, int(memory.id)),
        )
        memory.weight = weight
        if expires_at:
            memory.expires_at = expires_at

    def _memory_entity_name(self, memory: LongTermMemoryRecord) -> str:
        if memory.title:
            return memory.title[:80]
        content = memory.content.replace("，", " ").replace("。", " ").replace("；", " ")
        return (content.split() or [memory.category or "记忆"])[0][:80]

    def _memory_entity_type(self, memory: LongTermMemoryRecord) -> str:
        category = self._text(memory.category).lower()
        if category.startswith("preference"):
            return "preference"
        prefix = category.split(":", 1)[0]
        return self._ENTITY_CATEGORY_MAP.get(prefix, "topic")

    def _upsert_memory_entity_unlocked(self, memory: LongTermMemoryRecord) -> MemoryEntityRecord | None:
        name = self._text(self._memory_entity_name(memory))
        if not name:
            return None
        entity_type = self._memory_entity_type(memory)
        row = self._conn.execute(
            """
            SELECT * FROM memory_entities
            WHERE scope = ? AND entity_type = ? AND name = ?
            """,
            (self._text(memory.scope), entity_type, name),
        ).fetchone()
        if row:
            self._conn.execute(
                """
                UPDATE memory_entities
                SET last_seen = COALESCE(NULLIF(?, ''), last_seen),
                    mention_count = mention_count + 1,
                    confidence = MAX(confidence, ?),
                    status = 'active',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (self._text(memory.date), float(memory.confidence or 1.0), int(row["id"])),
            )
            entity_id = int(row["id"])
        else:
            cursor = self._conn.execute(
                """
                INSERT INTO memory_entities(
                    scope, entity_type, name, first_seen, last_seen,
                    mention_count, confidence, status
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, 'active')
                """,
                (
                    self._text(memory.scope),
                    entity_type,
                    name,
                    self._text(memory.date),
                    self._text(memory.date),
                    float(memory.confidence or 1.0),
                ),
            )
            entity_id = int(cursor.lastrowid)
        self._conn.execute(
            """
            INSERT OR REPLACE INTO memory_entity_links(entity_id, memory_id, relation, weight)
            VALUES (?, ?, 'mentions', ?)
            """,
            (entity_id, int(memory.id), float(memory.weight or 1.0)),
        )
        row = self._conn.execute("SELECT * FROM memory_entities WHERE id = ?", (entity_id,)).fetchone()
        return self._compose_memory_entity(row) if row else None

    def _memory_cluster_title(self, memory: LongTermMemoryRecord) -> str:
        category = self._text(memory.category).split(":", 1)[0] or "general"
        if category in {"short_term", "correction"}:
            return memory.title or memory.content[:60]
        return f"{category}:{memory.title or memory.content[:40]}"

    def _upsert_memory_cluster_unlocked(self, memory: LongTermMemoryRecord) -> MemoryEpisodeClusterRecord | None:
        if self._memory_lifecycle_kind(memory.category) not in {"episode", "short_term"}:
            return None
        title = self._memory_cluster_title(memory)[:120]
        summary = memory.content
        row = self._conn.execute(
            """
            SELECT * FROM memory_episode_clusters
            WHERE scope = ? AND title = ? AND category = ?
            """,
            (self._text(memory.scope), title, self._text(memory.category).split(":", 1)[0] or "life"),
        ).fetchone()
        if row:
            cluster_id = int(row["id"])
            self._conn.execute(
                """
                UPDATE memory_episode_clusters
                SET summary = COALESCE(NULLIF(?, ''), summary),
                    first_date = CASE WHEN first_date = '' OR (? <> '' AND ? < first_date) THEN ? ELSE first_date END,
                    last_date = CASE WHEN ? <> '' AND ? > last_date THEN ? ELSE last_date END,
                    memory_count = memory_count + 1,
                    weight = MAX(weight, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    summary,
                    memory.date,
                    memory.date,
                    memory.date,
                    memory.date,
                    memory.date,
                    memory.date,
                    float(memory.weight or 1.0),
                    cluster_id,
                ),
            )
        else:
            cursor = self._conn.execute(
                """
                INSERT INTO memory_episode_clusters(
                    scope, title, summary, category, first_date, last_date,
                    memory_count, weight, status
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'active')
                """,
                (
                    self._text(memory.scope),
                    title,
                    summary,
                    self._text(memory.category).split(":", 1)[0] or "life",
                    self._text(memory.date),
                    self._text(memory.date),
                    float(memory.weight or 1.0),
                ),
            )
            cluster_id = int(cursor.lastrowid)
        self._conn.execute(
            """
            INSERT OR REPLACE INTO memory_episode_cluster_items(cluster_id, memory_id, sort_order)
            VALUES (?, ?, COALESCE((SELECT MAX(sort_order) + 1 FROM memory_episode_cluster_items WHERE cluster_id = ?), 0))
            """,
            (cluster_id, int(memory.id), cluster_id),
        )
        row = self._conn.execute("SELECT * FROM memory_episode_clusters WHERE id = ?", (cluster_id,)).fetchone()
        return self._compose_memory_cluster(row) if row else None

    def _record_memory_conflicts_unlocked(self, memory: LongTermMemoryRecord) -> list[MemoryConflictRecord]:
        category = self._text(memory.category)
        if self._memory_lifecycle_kind(category) not in {"short_term", "correction"}:
            return []
        rows = self._conn.execute(
            """
            SELECT * FROM long_term_memories
            WHERE id <> ? AND status = 'active'
              AND scope = ?
              AND (category LIKE 'preference:%' OR category = 'relationship' OR category = 'place')
            ORDER BY weight DESC, id DESC
            LIMIT 8
            """,
            (int(memory.id), self._text(memory.scope)),
        ).fetchall()
        conflicts: list[MemoryConflictRecord] = []
        for row in rows:
            other = self._compose_long_term_memory(row)
            summary = f"{memory.content} / {other.content}"
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO memory_conflicts(
                    scope, memory_id, related_memory_id, conflict_type,
                    summary, resolution, status
                )
                VALUES (?, ?, ?, ?, ?, ?, 'open')
                """,
                (
                    self._text(memory.scope),
                    int(memory.id),
                    int(other.id),
                    "override" if self._memory_lifecycle_kind(category) == "correction" else "temporary_tension",
                    summary[:300],
                    "优先按时间范围和来源强度判断，不直接覆盖长期记忆。",
                ),
            )
            conflict_id = int(cursor.lastrowid or 0)
            if conflict_id:
                conflict_row = self._conn.execute("SELECT * FROM memory_conflicts WHERE id = ?", (conflict_id,)).fetchone()
                if conflict_row:
                    conflicts.append(self._compose_memory_conflict(conflict_row))
        return conflicts

    def _maintain_long_term_memory_quality_unlocked(self, memory: LongTermMemoryRecord) -> None:
        self._apply_memory_lifecycle_unlocked(memory)
        self._upsert_memory_entity_unlocked(memory)
        self._upsert_memory_cluster_unlocked(memory)
        self._record_memory_conflicts_unlocked(memory)

    async def upsert_long_term_memory(self, memory: LongTermMemoryRecord | dict[str, Any]) -> LongTermMemoryRecord | None:
        record = LongTermMemoryRecord.from_value(memory)
        if not record:
            return None

        def write() -> LongTermMemoryRecord | None:
            saved = self._upsert_long_term_memory_unlocked(record)
            self._conn.commit()
            return saved

        return await self._run_db(write)

    async def upsert_long_term_memories(
        self,
        memories: list[LongTermMemoryRecord | dict[str, Any]],
    ) -> list[LongTermMemoryRecord]:
        records = [record for item in memories if (record := LongTermMemoryRecord.from_value(item))]
        if not records:
            return []

        def write() -> list[LongTermMemoryRecord]:
            saved = [
                item
                for record in records
                if (item := self._upsert_long_term_memory_unlocked(record))
            ]
            self._conn.commit()
            return saved

        return await self._run_db(write)

    def _long_term_memory_scope_clause(self, scopes: list[str]) -> tuple[str, list[str]]:
        clean = [self._text(scope) for scope in scopes if self._text(scope)]
        if not clean:
            return "", []
        placeholders = ", ".join("?" for _ in clean)
        return f" AND (m.scope = '' OR m.scope IN ({placeholders}))", clean

    def _search_long_term_memory_fts_unlocked(
        self,
        query: str,
        *,
        scopes: list[str],
        categories: list[str],
        limit: int,
    ) -> list[LongTermMemoryRecord]:
        match = self._memory_search_query(query)
        if not match:
            return []
        scope_clause, scope_params = self._long_term_memory_scope_clause(scopes)
        category_params = [self._text(item) for item in categories if self._text(item)]
        category_clause = ""
        if category_params:
            category_clause = f" AND m.category IN ({', '.join('?' for _ in category_params)})"
        sql = f"""
            SELECT m.*, bm25(long_term_memories_fts) AS bm25_score
            FROM long_term_memories_fts f
            JOIN long_term_memories m ON m.id = f.rowid
            WHERE long_term_memories_fts MATCH ?
              AND m.status = 'active'
              AND (m.expires_at = '' OR m.expires_at >= DATE('now', 'localtime'))
              {scope_clause}
              {category_clause}
            ORDER BY bm25_score ASC, m.weight DESC, m.updated_at DESC, m.id DESC
            LIMIT ?
        """
        rows = self._conn.execute(sql, [match, *scope_params, *category_params, max(1, int(limit))]).fetchall()
        return [
            self._compose_long_term_memory(row, score=-float(row["bm25_score"] or 0.0))
            for row in rows
        ]

    def _search_long_term_memory_like_unlocked(
        self,
        query: str,
        *,
        scopes: list[str],
        categories: list[str],
        limit: int,
    ) -> list[LongTermMemoryRecord]:
        normalized = (
            str(query or "")
            .replace("，", " ")
            .replace("。", " ")
            .replace("；", " ")
            .replace("：", " ")
            .replace("、", " ")
            .replace("|", " ")
            .replace("｜", " ")
        )
        tokens = [item.strip() for item in normalized.split() if item.strip()]
        if not tokens:
            tokens = [str(query or "").strip()]
        tokens = [item for item in tokens if item][:8]
        if not tokens:
            return []
        conditions = " OR ".join("(m.title LIKE ? OR m.content LIKE ? OR m.category LIKE ?)" for _ in tokens)
        params: list[Any] = []
        for token in tokens:
            pattern = f"%{token}%"
            params.extend([pattern, pattern, pattern])
        scope_clause, scope_params = self._long_term_memory_scope_clause(scopes)
        category_params = [self._text(item) for item in categories if self._text(item)]
        category_clause = ""
        if category_params:
            category_clause = f" AND m.category IN ({', '.join('?' for _ in category_params)})"
        rows = self._conn.execute(
            f"""
            SELECT m.* FROM long_term_memories m
            WHERE m.status = 'active'
              AND (m.expires_at = '' OR m.expires_at >= DATE('now', 'localtime'))
              AND ({conditions})
              {scope_clause}
              {category_clause}
            ORDER BY m.weight DESC, m.updated_at DESC, m.id DESC
            LIMIT ?
            """,
            [*params, *scope_params, *category_params, max(1, int(limit))],
        ).fetchall()
        return [self._compose_long_term_memory(row, score=0.1) for row in rows]

    async def search_long_term_memories(
        self,
        query: str,
        *,
        scopes: list[str] | None = None,
        categories: list[str] | None = None,
        limit: int = 5,
    ) -> list[LongTermMemoryRecord]:
        query = self._text(query)
        if not query:
            return []

        def read() -> list[LongTermMemoryRecord]:
            try:
                hits = self._search_long_term_memory_fts_unlocked(
                    query,
                    scopes=scopes or [],
                    categories=categories or [],
                    limit=limit,
                )
                if hits:
                    return hits
            except sqlite3.Error:
                pass
            return self._search_long_term_memory_like_unlocked(
                query,
                scopes=scopes or [],
                categories=categories or [],
                limit=limit,
            )

        return await self._run_db(read)

    async def list_recent_long_term_memories(
        self,
        *,
        scopes: list[str] | None = None,
        categories: list[str] | None = None,
        limit: int = 8,
    ) -> list[LongTermMemoryRecord]:
        def read() -> list[LongTermMemoryRecord]:
            scope_clause, scope_params = self._long_term_memory_scope_clause(scopes or [])
            category_params = [self._text(item) for item in (categories or []) if self._text(item)]
            category_clause = ""
            if category_params:
                category_clause = f" AND category IN ({', '.join('?' for _ in category_params)})"
            rows = self._conn.execute(
                f"""
                SELECT * FROM long_term_memories m
                WHERE status = 'active'
                  AND (expires_at = '' OR expires_at >= DATE('now', 'localtime'))
                  {scope_clause}
                  {category_clause}
                ORDER BY weight DESC, updated_at DESC, id DESC
                LIMIT ?
                """,
                [*scope_params, *category_params, max(1, int(limit))],
            ).fetchall()
            return [self._compose_long_term_memory(row) for row in rows]

        return await self._run_db(read)

    async def get_memory_episode_clusters(
        self,
        *,
        scopes: list[str] | None = None,
        limit: int = 8,
    ) -> list[MemoryEpisodeClusterRecord]:
        def read() -> list[MemoryEpisodeClusterRecord]:
            scope_clause, scope_params = self._long_term_memory_scope_clause(scopes or [])
            rows = self._conn.execute(
                f"""
                SELECT * FROM memory_episode_clusters m
                WHERE status = 'active'
                  {scope_clause}
                ORDER BY weight DESC, last_date DESC, updated_at DESC, id DESC
                LIMIT ?
                """,
                [*scope_params, max(1, int(limit))],
            ).fetchall()
            return [self._compose_memory_cluster(row) for row in rows]

        return await self._run_db(read)

    async def get_memory_entities(
        self,
        *,
        scopes: list[str] | None = None,
        entity_type: str = "",
        limit: int = 12,
    ) -> list[MemoryEntityRecord]:
        def read() -> list[MemoryEntityRecord]:
            scope_clause, scope_params = self._long_term_memory_scope_clause(scopes or [])
            params: list[Any] = [*scope_params]
            type_clause = ""
            clean_type = self._text(entity_type)
            if clean_type:
                type_clause = " AND entity_type = ?"
                params.append(clean_type)
            rows = self._conn.execute(
                f"""
                SELECT * FROM memory_entities m
                WHERE status = 'active'
                  {scope_clause}
                  {type_clause}
                ORDER BY mention_count DESC, confidence DESC, last_seen DESC, id DESC
                LIMIT ?
                """,
                [*params, max(1, int(limit))],
            ).fetchall()
            return [self._compose_memory_entity(row) for row in rows]

        return await self._run_db(read)

    async def get_memory_conflicts(
        self,
        *,
        scopes: list[str] | None = None,
        status: str = "open",
        limit: int = 8,
    ) -> list[MemoryConflictRecord]:
        def read() -> list[MemoryConflictRecord]:
            scope_clause, scope_params = self._long_term_memory_scope_clause(scopes or [])
            params: list[Any] = [*scope_params]
            status_clause = ""
            clean_status = self._text(status)
            if clean_status:
                status_clause = " AND status = ?"
                params.append(clean_status)
            rows = self._conn.execute(
                f"""
                SELECT * FROM memory_conflicts m
                WHERE 1 = 1
                  {scope_clause}
                  {status_clause}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                [*params, max(1, int(limit))],
            ).fetchall()
            return [self._compose_memory_conflict(row) for row in rows]

        return await self._run_db(read)

    def _upsert_memory_decision_link_unlocked(
        self,
        decision_id: int,
        memory_id: int,
        *,
        influence: str = "",
        weight: float = 1.0,
    ) -> MemoryDecisionLinkRecord | None:
        if not decision_id or not memory_id:
            return None
        self._conn.execute(
            """
            INSERT INTO memory_decision_links(decision_id, memory_id, influence, weight)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(decision_id, memory_id) DO UPDATE SET
                influence = COALESCE(NULLIF(excluded.influence, ''), memory_decision_links.influence),
                weight = MAX(memory_decision_links.weight, excluded.weight)
            """,
            (
                int(decision_id),
                int(memory_id),
                self._text(influence),
                max(0.0, min(float(weight or 1.0), 10.0)),
            ),
        )
        row = self._conn.execute(
            "SELECT * FROM memory_decision_links WHERE decision_id = ? AND memory_id = ?",
            (int(decision_id), int(memory_id)),
        ).fetchone()
        return self._compose_memory_decision_link(row) if row else None

    def _decision_memory_query(self, decision: Any) -> str:
        data = decision.as_dict() if hasattr(decision, "as_dict") else dict(decision or {})
        return " ".join(
            self._text(data.get(key))
            for key in ("decision", "reason", "evidence", "outcome", "subject")
            if self._text(data.get(key))
        )

    def _link_decision_memory_candidates_unlocked(
        self,
        decision: Any,
        *,
        limit: int = 3,
    ) -> list[MemoryDecisionLinkRecord]:
        data = decision.as_dict() if hasattr(decision, "as_dict") else dict(decision or {})
        decision_id = int(data.get("id") or 0)
        query = self._decision_memory_query(data)
        if not (decision_id and query):
            return []
        try:
            memories = self._search_long_term_memory_fts_unlocked(
                query,
                scopes=[],
                categories=[],
                limit=limit,
            )
        except sqlite3.Error:
            memories = []
        if not memories:
            memories = self._search_long_term_memory_like_unlocked(
                query,
                scopes=[],
                categories=[],
                limit=limit,
            )
        links: list[MemoryDecisionLinkRecord] = []
        for memory in memories[: max(1, int(limit))]:
            link = self._upsert_memory_decision_link_unlocked(
                decision_id,
                int(memory.id),
                influence=memory.title or memory.content[:80],
                weight=max(float(memory.weight or 1.0), float(memory.score or 0.0)),
            )
            if link:
                links.append(link)
        return links

    async def link_decision_memories(
        self,
        decision_id: int,
        memories: list[int | LongTermMemoryRecord | dict[str, Any]],
        *,
        influence: str = "",
        weight: float = 1.0,
    ) -> list[MemoryDecisionLinkRecord]:
        def write() -> list[MemoryDecisionLinkRecord]:
            links: list[MemoryDecisionLinkRecord] = []
            for memory in memories:
                if isinstance(memory, int):
                    memory_id = memory
                elif isinstance(memory, dict):
                    memory_id = int(memory.get("id") or 0)
                else:
                    memory_id = int(getattr(memory, "id", 0) or 0)
                link = self._upsert_memory_decision_link_unlocked(
                    int(decision_id),
                    memory_id,
                    influence=influence,
                    weight=weight,
                )
                if link:
                    links.append(link)
            self._conn.commit()
            return links

        return await self._run_db(write)

    async def get_memory_decision_links(
        self,
        decision_id: int,
        *,
        limit: int = 8,
    ) -> list[MemoryDecisionLinkRecord]:
        def read() -> list[MemoryDecisionLinkRecord]:
            rows = self._conn.execute(
                """
                SELECT * FROM memory_decision_links
                WHERE decision_id = ?
                ORDER BY weight DESC, id DESC
                LIMIT ?
                """,
                (int(decision_id), max(1, int(limit))),
            ).fetchall()
            return [self._compose_memory_decision_link(row) for row in rows]

        return await self._run_db(read)

    async def get_memory_decision_sources(self, decision_id: int, *, limit: int = 5) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            rows = self._conn.execute(
                """
                SELECT l.*, m.scope, m.category, m.title, m.content, m.source_table, m.source_id, m.date
                FROM memory_decision_links l
                JOIN long_term_memories m ON m.id = l.memory_id
                WHERE l.decision_id = ? AND m.status = 'active'
                ORDER BY l.weight DESC, l.id DESC
                LIMIT ?
                """,
                (int(decision_id), max(1, int(limit))),
            ).fetchall()
            return [
                {
                    "id": int(row["id"] or 0),
                    "decision_id": int(row["decision_id"] or 0),
                    "memory_id": int(row["memory_id"] or 0),
                    "influence": self._text(row["influence"]),
                    "weight": float(row["weight"] or 0.0),
                    "scope": self._text(row["scope"]),
                    "category": self._text(row["category"]),
                    "title": self._text(row["title"]),
                    "content": self._text(row["content"]),
                    "source_table": self._text(row["source_table"]),
                    "source_id": self._text(row["source_id"]),
                    "date": self._text(row["date"]),
                    "created_at": self._text(row["created_at"]),
                }
                for row in rows
            ]

        return await self._run_db(read)

    async def cleanup_long_term_memories(self, cutoff_date: str) -> int:
        def write() -> int:
            deleted = 0
            for sql, params in (
                (
                    """
                    DELETE FROM long_term_memories
                    WHERE status <> 'active'
                       OR (expires_at <> '' AND expires_at < DATE('now', 'localtime'))
                       OR (date <> '' AND date < ? AND weight < 3)
                    """,
                    (cutoff_date,),
                ),
            ):
                cursor = self._conn.execute(sql, params)
                deleted += max(int(cursor.rowcount or 0), 0)
            self._conn.commit()
            return deleted

        return await self._run_db(write)
