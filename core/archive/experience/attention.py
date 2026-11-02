from __future__ import annotations

import sqlite3
from typing import Any

from ...clock import today as life_today
from ...models import FocusSlotRecord, FocusTargetRecord

from typing import Protocol


class FocusArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _compose_focus_slot: Any
    _compose_focus_target: Any
    _conn: Any
    _flag: Any
    _focus_expiry_clause: Any
    _run_db: Any
    _text: Any


class FocusArchiveComponent:
    def __init__(self, context: FocusArchiveComponentContext) -> None:
        self.context = context

    @staticmethod
    def _focus_expiry_clause() -> str:
        return "(expires_at = '' OR expires_at >= ?)"

    def _compose_focus_slot(self, row: sqlite3.Row) -> FocusSlotRecord:
        return FocusSlotRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            focus_key=row["focus_key"],
            label=self.context._text(row["label"]),
            priority=int(row["priority"] or 0),
            progress=int(row["progress"] or 0),
            status=self.context._text(row["status"]) or "active",
            reason=self.context._text(row["reason"]),
            last_evidence=self.context._text(row["last_evidence"]),
            last_active_at=row["last_active_at"],
            last_progress_at=row["last_progress_at"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


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
                params.append(self.context._text(scope))
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
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_focus_slot(row) for row in rows]

        return await self.context._run_db(dbwork)


    def _compose_focus_target(self, row: sqlite3.Row) -> FocusTargetRecord:
        return FocusTargetRecord(
            id=int(row["id"] or 0),
            target_type=row["target_type"],
            target_id=row["target_id"],
            label=self.context._text(row["label"]),
            priority=int(row["priority"] or 0),
            reason=self.context._text(row["reason"]),
            scope=row["scope"],
            enabled=bool(row["enabled"]),
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


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
                clauses.append(self.context._focus_expiry_clause())
                params.append(life_today().isoformat())
            if enabled_only:
                clauses.append("enabled = 1")
            if scope:
                clauses.append("(scope = ? OR scope = '')")
                params.append(self.context._text(scope))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY priority DESC, updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_focus_target(row) for row in rows]

        return await self.context._run_db(dbwork)

