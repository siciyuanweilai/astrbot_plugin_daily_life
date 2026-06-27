import sqlite3
from typing import Any

from ..models import CommitmentRecord


class CommitmentArchiveMixin:
    def _compose_commitment(self, row: sqlite3.Row) -> CommitmentRecord:
        return CommitmentRecord(
            id=int(row["id"] or 0),
            content=row["content"],
            kind=row["kind"],
            trigger_date=row["trigger_date"],
            trigger_time=row["trigger_time"],
            time_window=row["time_window"],
            people=self._get_people_unlocked("commitment_people", "commitment_id", row["id"]),
            place=row["place"],
            status=row["status"],
            confidence=float(row["confidence"] or 0.0),
            source=row["source"],
            source_session=row["source_session"],
            source_message=row["source_message"],
            created_at=row["created_at"],
            activated_at=row["activated_at"],
            completed_at=row["completed_at"],
        )
    async def save_commitment(self, commitment: CommitmentRecord) -> CommitmentRecord:
        item = CommitmentRecord.from_value(commitment.as_dict() if isinstance(commitment, CommitmentRecord) else commitment)
        if not item:
            raise ValueError("承诺内容不能为空")
        item.kind = self._text(item.kind) or "plan"
        item.status = self._text(item.status) or "active"
        item.confidence = max(min(float(item.confidence or 0.0), 1.0), 0.0)
        async with self._lock:
            if item.id:
                self._conn.execute(
                    """
                    UPDATE commitments
                    SET content = ?, kind = ?, trigger_date = ?, trigger_time = ?,
                        time_window = ?, place = ?, status = ?, confidence = ?,
                        source = ?, source_session = ?, source_message = ?,
                        activated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        item.content,
                        item.kind,
                        item.trigger_date,
                        item.trigger_time,
                        item.time_window,
                        item.place,
                        item.status,
                        item.confidence,
                        item.source,
                        item.source_session,
                        item.source_message,
                        item.activated_at,
                        item.completed_at,
                        item.id,
                    ),
                )
                commitment_id = item.id
            else:
                cursor = self._conn.execute(
                    """
                    INSERT INTO commitments(
                        content, kind, trigger_date, trigger_time, time_window, place,
                        status, confidence, source, source_session, source_message,
                        activated_at, completed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.content,
                        item.kind,
                        item.trigger_date,
                        item.trigger_time,
                        item.time_window,
                        item.place,
                        item.status,
                        item.confidence,
                        item.source,
                        item.source_session,
                        item.source_message,
                        item.activated_at,
                        item.completed_at,
                    ),
                )
                commitment_id = int(cursor.lastrowid)
            self._replace_people_unlocked("commitment_people", "commitment_id", commitment_id, item.people)
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id,)).fetchone()
            return self._compose_commitment(row)
    async def get_commitments(self, status: str = "active", limit: int = 20) -> list[CommitmentRecord]:
        async with self._lock:
            params: list[Any] = []
            sql = "SELECT * FROM commitments"
            if status:
                sql += " WHERE status = ?"
                params.append(self._text(status))
            sql += " ORDER BY COALESCE(NULLIF(trigger_date, ''), '9999-12-31'), id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_commitment(row) for row in rows]
    async def get_commitment(self, commitment_id: int) -> CommitmentRecord | None:
        async with self._lock:
            row = self._conn.execute("SELECT * FROM commitments WHERE id = ?", (int(commitment_id),)).fetchone()
            return self._compose_commitment(row) if row else None
    async def get_due_commitments(self, date_str: str, include_scheduled: bool = False) -> list[CommitmentRecord]:
        statuses = ("active", "scheduled") if include_scheduled else ("active",)
        placeholders = ",".join("?" for _ in statuses)
        async with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM commitments
                WHERE status IN ({placeholders})
                  AND (trigger_date = ? OR (trigger_date = '' AND time_window IN ('next_chat', 'next_time')))
                ORDER BY trigger_time, id
                """,
                (*statuses, self._text(date_str)),
            ).fetchall()
            return [self._compose_commitment(row) for row in rows]
    async def set_commitment_status(self, commitment_id: int, status: str, when: str = "") -> bool:
        status = self._text(status)
        if status not in {"active", "scheduled", "done", "cancelled", "expired", "pending"}:
            return False
        field = "completed_at" if status in {"done", "cancelled", "expired"} else "activated_at"
        async with self._lock:
            cursor = self._conn.execute(
                f"UPDATE commitments SET status = ?, {field} = ? WHERE id = ?",
                (status, self._text(when), int(commitment_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    async def reschedule_commitment(self, commitment_id: int, trigger_date: str, time_window: str = "") -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE commitments
                SET trigger_date = ?, time_window = ?, status = 'active', activated_at = ''
                WHERE id = ?
                """,
                (self._text(trigger_date), self._text(time_window), int(commitment_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    async def link_commitments_to_day(self, date_str: str, commitment_ids: list[int]) -> None:
        ids = sorted({int(item) for item in commitment_ids if int(item or 0) > 0})
        if not ids:
            return
        async with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO day_commitments(date, commitment_id) VALUES (?, ?)",
                [(self._text(date_str), commitment_id) for commitment_id in ids],
            )
            self._conn.executemany(
                """
                UPDATE commitments
                SET status = 'scheduled', activated_at = COALESCE(NULLIF(activated_at, ''), CURRENT_TIMESTAMP)
                WHERE id = ? AND status = 'active'
                """,
                [(commitment_id,) for commitment_id in ids],
            )
            self._conn.commit()

