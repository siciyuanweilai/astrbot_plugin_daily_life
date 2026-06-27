import sqlite3
from typing import Any

from ...models import LifeTermRecord, MemoryBoundaryRecord
from .lines import _pack_lines, _unpack_lines


class LexiconArchiveMixin:
    def _compose_life_term(self, row: sqlite3.Row) -> LifeTermRecord:
        return LifeTermRecord(
            id=int(row["id"] or 0),
            term=row["term"],
            meaning=self._text(row["meaning"]),
            scope=row["scope"],
            scene=self._text(row["scene"]),
            examples=_unpack_lines(row["examples"]),
            familiarity=int(row["familiarity"] or 0),
            source=row["source"],
            confidence=float(row["confidence"] or 0.0),
            last_seen=row["last_seen"],
            evidence=self._text(row["evidence"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def upsert_life_term(self, term: LifeTermRecord) -> LifeTermRecord | None:
        item = LifeTermRecord.from_value(term.as_dict() if isinstance(term, LifeTermRecord) else term)
        if not item:
            return None
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO life_terms(
                    term, meaning, scope, scene, examples, familiarity, source,
                    confidence, last_seen, evidence, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(term, scope) DO UPDATE SET
                    meaning = COALESCE(NULLIF(excluded.meaning, ''), life_terms.meaning),
                    scene = COALESCE(NULLIF(excluded.scene, ''), life_terms.scene),
                    examples = COALESCE(NULLIF(excluded.examples, ''), life_terms.examples),
                    familiarity = MAX(life_terms.familiarity, excluded.familiarity),
                    source = excluded.source,
                    confidence = MAX(life_terms.confidence, excluded.confidence),
                    last_seen = excluded.last_seen,
                    evidence = COALESCE(NULLIF(excluded.evidence, ''), life_terms.evidence),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    self._text(item.term),
                    self._text(item.meaning),
                    self._text(item.scope),
                    self._text(item.scene),
                    _pack_lines(item.examples),
                    max(0, min(int(item.familiarity or 0), 100)),
                    self._text(item.source) or "chat_memory",
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    self._text(item.last_seen),
                    self._text(item.evidence),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM life_terms WHERE term = ? AND scope = ?",
                (self._text(item.term), self._text(item.scope)),
            ).fetchone()
            return self._compose_life_term(row) if row else None
    async def get_life_terms(self, limit: int = 20, *, scope: str = "") -> list[LifeTermRecord]:
        async with self._lock:
            sql = "SELECT * FROM life_terms"
            params: list[Any] = []
            if scope:
                sql += " WHERE scope = ? OR scope = ''"
                params.append(self._text(scope))
            sql += " ORDER BY last_seen DESC, confidence DESC, familiarity DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_life_term(row) for row in rows]
    def _compose_memory_boundary(self, row: sqlite3.Row) -> MemoryBoundaryRecord:
        return MemoryBoundaryRecord(
            id=int(row["id"] or 0),
            source_scope=row["source_scope"],
            target_scope=row["target_scope"],
            policy=row["policy"],
            reason=row["reason"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def set_memory_boundary(self, boundary: MemoryBoundaryRecord) -> MemoryBoundaryRecord | None:
        item = MemoryBoundaryRecord.from_value(
            boundary.as_dict() if isinstance(boundary, MemoryBoundaryRecord) else boundary
        )
        if not item:
            return None
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_boundaries(
                    source_scope, target_scope, policy, reason, enabled,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(source_scope, target_scope) DO UPDATE SET
                    policy = excluded.policy,
                    reason = excluded.reason,
                    enabled = excluded.enabled,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    self._text(item.source_scope),
                    self._text(item.target_scope),
                    self._text(item.policy) or "ask",
                    self._text(item.reason),
                    self._flag(item.enabled),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM memory_boundaries WHERE source_scope = ? AND target_scope = ?",
                (self._text(item.source_scope), self._text(item.target_scope)),
            ).fetchone()
            return self._compose_memory_boundary(row) if row else None
    async def get_memory_boundaries(self, limit: int = 20, enabled_only: bool = True) -> list[MemoryBoundaryRecord]:
        async with self._lock:
            sql = "SELECT * FROM memory_boundaries WHERE LOWER(TRIM(source_scope)) <> LOWER(TRIM(target_scope))"
            params: list[Any] = []
            if enabled_only:
                sql += " AND enabled = 1"
            sql += " ORDER BY updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_memory_boundary(row) for row in rows]
