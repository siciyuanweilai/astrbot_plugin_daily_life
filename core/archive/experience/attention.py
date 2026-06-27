import sqlite3
from typing import Any

from ...clock import today as life_today
from ...models import FocusSlotRecord, FocusTargetRecord


class FocusArchiveMixin:
    @staticmethod
    def _focus_expiry_clause() -> str:
        return "(expires_at = '' OR expires_at >= ?)"
    def _compose_focus_slot(self, row: sqlite3.Row) -> FocusSlotRecord:
        return FocusSlotRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            focus_key=row["focus_key"],
            label=self._text(row["label"]),
            priority=int(row["priority"] or 0),
            reason=self._text(row["reason"]),
            last_active_at=row["last_active_at"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def upsert_focus_slot(self, slot: FocusSlotRecord) -> FocusSlotRecord | None:
        item = FocusSlotRecord.from_value(slot.as_dict() if isinstance(slot, FocusSlotRecord) else slot)
        if not item:
            return None
        scope = self._text(item.scope)
        focus_key = self._text(item.focus_key) or self._text(item.label)
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO focus_slots(
                    scope, focus_key, label, priority, reason, last_active_at,
                    expires_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, focus_key) DO UPDATE SET
                    label = COALESCE(NULLIF(excluded.label, ''), focus_slots.label),
                    priority = excluded.priority,
                    reason = COALESCE(NULLIF(excluded.reason, ''), focus_slots.reason),
                    last_active_at = COALESCE(NULLIF(excluded.last_active_at, ''), focus_slots.last_active_at),
                    expires_at = COALESCE(NULLIF(excluded.expires_at, ''), focus_slots.expires_at),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    scope,
                    focus_key,
                    self._text(item.label) or focus_key,
                    max(0, min(int(item.priority or 0), 100)),
                    self._text(item.reason),
                    self._text(item.last_active_at),
                    self._text(item.expires_at),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM focus_slots WHERE scope = ? AND focus_key = ?",
                (scope, focus_key),
            ).fetchone()
            return self._compose_focus_slot(row) if row else None
    async def get_focus_slots(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        active_only: bool = True,
    ) -> list[FocusSlotRecord]:
        async with self._lock:
            sql = "SELECT * FROM focus_slots"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("(scope = ? OR scope = '')")
                params.append(self._text(scope))
            if active_only:
                clauses.append("(expires_at = '' OR expires_at >= ?)")
                params.append(life_today().isoformat())
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY priority DESC, updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_focus_slot(row) for row in rows]
    def _compose_focus_target(self, row: sqlite3.Row) -> FocusTargetRecord:
        return FocusTargetRecord(
            id=int(row["id"] or 0),
            target_type=row["target_type"],
            target_id=row["target_id"],
            label=self._text(row["label"]),
            priority=int(row["priority"] or 0),
            reason=self._text(row["reason"]),
            scope=row["scope"],
            enabled=bool(row["enabled"]),
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def upsert_focus_target(self, target: FocusTargetRecord) -> FocusTargetRecord | None:
        item = FocusTargetRecord.from_value(target.as_dict() if isinstance(target, FocusTargetRecord) else target)
        if not item:
            return None
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO focus_targets(
                    target_type, target_id, label, priority, reason, scope,
                    enabled, expires_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(target_type, target_id, scope) DO UPDATE SET
                    label = excluded.label,
                    priority = excluded.priority,
                    reason = excluded.reason,
                    enabled = excluded.enabled,
                    expires_at = excluded.expires_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    self._text(item.target_type) or "topic",
                    self._text(item.target_id) or self._text(item.label),
                    self._text(item.label) or self._text(item.target_id),
                    max(0, min(int(item.priority or 0), 100)),
                    self._text(item.reason),
                    self._text(item.scope),
                    self._flag(item.enabled),
                    self._text(item.expires_at),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                """
                SELECT *
                FROM focus_targets
                WHERE target_type = ? AND target_id = ? AND scope = ?
                """,
                (
                    self._text(item.target_type) or "topic",
                    self._text(item.target_id) or self._text(item.label),
                    self._text(item.scope),
                ),
            ).fetchone()
            return self._compose_focus_target(row) if row else None
    async def get_focus_targets(
        self,
        limit: int = 20,
        enabled_only: bool = True,
        include_expired: bool = False,
    ) -> list[FocusTargetRecord]:
        async with self._lock:
            sql = "SELECT * FROM focus_targets"
            params: list[Any] = []
            clauses = []
            if not include_expired:
                clauses.append(self._focus_expiry_clause())
                params.append(life_today().isoformat())
            if enabled_only:
                clauses.append("enabled = 1")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY priority DESC, updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_focus_target(row) for row in rows]
    async def set_focus_target_enabled(self, target_id: int, enabled: bool) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                "UPDATE focus_targets SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (self._flag(enabled), int(target_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
