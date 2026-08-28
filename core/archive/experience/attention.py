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
            progress=int(row["progress"] or 0),
            status=self._text(row["status"]) or "active",
            reason=self._text(row["reason"]),
            last_evidence=self._text(row["last_evidence"]),
            last_active_at=row["last_active_at"],
            last_progress_at=row["last_progress_at"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def upsert_focus_slot(self, slot: FocusSlotRecord) -> FocusSlotRecord | None:
        item = FocusSlotRecord.from_value(
            slot.as_dict() if isinstance(slot, FocusSlotRecord) else slot
        )
        if not item:
            return None
        scope = self._text(item.scope)
        focus_key = self._text(item.focus_key) or self._text(item.label)

        def dbwork():
            self._conn.execute(
                """
                INSERT INTO focus_slots(
                    scope, focus_key, label, priority, progress, status, reason,
                    last_evidence, last_active_at, last_progress_at, expires_at,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, focus_key) DO UPDATE SET
                    label = COALESCE(NULLIF(excluded.label, ''), focus_slots.label),
                    priority = excluded.priority,
                    progress = MAX(focus_slots.progress, excluded.progress),
                    status = CASE
                        WHEN focus_slots.status <> 'active' THEN focus_slots.status
                        ELSE excluded.status
                    END,
                    reason = COALESCE(NULLIF(excluded.reason, ''), focus_slots.reason),
                    last_evidence = COALESCE(NULLIF(excluded.last_evidence, ''), focus_slots.last_evidence),
                    last_active_at = COALESCE(NULLIF(excluded.last_active_at, ''), focus_slots.last_active_at),
                    last_progress_at = COALESCE(NULLIF(excluded.last_progress_at, ''), focus_slots.last_progress_at),
                    expires_at = COALESCE(NULLIF(excluded.expires_at, ''), focus_slots.expires_at),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    scope,
                    focus_key,
                    self._text(item.label) or focus_key,
                    max(0, min(int(item.priority or 0), 100)),
                    max(0, min(int(item.progress or 0), 100)),
                    self._text(item.status) or "active",
                    self._text(item.reason),
                    self._text(item.last_evidence),
                    self._text(item.last_active_at),
                    self._text(item.last_progress_at),
                    self._text(item.expires_at),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM focus_slots WHERE scope = ? AND focus_key = ?",
                (scope, focus_key),
            ).fetchone()
            return self._compose_focus_slot(row) if row else None

        return await self._run_db(dbwork)

    async def get_focus_slots(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        active_only: bool = True,
    ) -> list[FocusSlotRecord]:
        def dbwork():
            sql = "SELECT * FROM focus_slots"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("(scope = ? OR scope = '')")
                params.append(self._text(scope))
            if active_only:
                clauses.append("status = 'active'")
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

        return await self._run_db(dbwork)

    async def update_focus_slot_progress(
        self,
        focus_id: int,
        *,
        progress_delta: int = 0,
        status: str = "active",
        evidence: str = "",
        date: str = "",
    ) -> FocusSlotRecord | None:
        """用可追溯证据更新短期目标，不从普通决策文本猜测进度。"""

        allowed_statuses = {"active", "completed", "blocked", "abandoned"}
        target_status = self._text(status).lower()
        if target_status not in allowed_statuses:
            return None
        try:
            target_id = int(focus_id)
            delta = max(0, min(int(progress_delta), 100))
        except (TypeError, ValueError):
            return None
        evidence_text = self._text(evidence)[:240]
        if target_id <= 0 or not evidence_text:
            return None

        def dbwork():
            row = self._conn.execute(
                "SELECT * FROM focus_slots WHERE id = ?", (target_id,)
            ).fetchone()
            if row is None or self._text(row["status"]) != "active":
                return None
            current_progress = max(0, min(int(row["progress"] or 0), 100))
            next_progress = min(100, current_progress + delta)
            if target_status == "completed":
                next_progress = 100
            next_priority = int(row["priority"] or 50)
            if target_status != "active":
                next_priority = 0
            self._conn.execute(
                """
                UPDATE focus_slots
                SET progress = ?, status = ?, priority = ?, last_evidence = ?,
                    last_progress_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    next_progress,
                    target_status,
                    max(0, min(next_priority, 100)),
                    evidence_text,
                    self._text(date),
                    target_id,
                ),
            )
            self._conn.commit()
            updated = self._conn.execute(
                "SELECT * FROM focus_slots WHERE id = ?", (target_id,)
            ).fetchone()
            return self._compose_focus_slot(updated) if updated else None

        return await self._run_db(dbwork)

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

    async def upsert_focus_target(
        self, target: FocusTargetRecord
    ) -> FocusTargetRecord | None:
        item = FocusTargetRecord.from_value(
            target.as_dict() if isinstance(target, FocusTargetRecord) else target
        )
        if not item:
            return None

        def dbwork():
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

        return await self._run_db(dbwork)

    async def get_focus_targets(
        self,
        limit: int = 20,
        enabled_only: bool = True,
        include_expired: bool = False,
        *,
        scope: str = "",
    ) -> list[FocusTargetRecord]:
        def dbwork():
            sql = "SELECT * FROM focus_targets"
            params: list[Any] = []
            clauses = []
            if not include_expired:
                clauses.append(self._focus_expiry_clause())
                params.append(life_today().isoformat())
            if enabled_only:
                clauses.append("enabled = 1")
            if scope:
                clauses.append("(scope = ? OR scope = '')")
                params.append(self._text(scope))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY priority DESC, updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_focus_target(row) for row in rows]

        return await self._run_db(dbwork)

    async def set_focus_target_enabled(self, target_id: int, enabled: bool) -> bool:
        def dbwork():
            cursor = self._conn.execute(
                "UPDATE focus_targets SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (self._flag(enabled), int(target_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0

        return await self._run_db(dbwork)
