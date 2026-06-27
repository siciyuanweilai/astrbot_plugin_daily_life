import sqlite3
from typing import Any

from ...models import MemoryEvidenceRecord


class EvidenceArchiveMixin:
    def _compose_memory_evidence(self, row: sqlite3.Row) -> MemoryEvidenceRecord:
        return MemoryEvidenceRecord(
            id=int(row["id"] or 0),
            target_type=row["target_type"],
            target_id=row["target_id"],
            evidence_type=row["evidence_type"],
            source_table=row["source_table"],
            source_id=row["source_id"],
            session_id=row["session_id"],
            message_id=row["message_id"],
            date=row["date"],
            summary=self._text(row["summary"]),
            confidence=float(row["confidence"] or 0.0),
            status=row["status"],
            created_at=row["created_at"],
        )
    async def save_memory_evidence(self, evidence: MemoryEvidenceRecord) -> MemoryEvidenceRecord | None:
        item = MemoryEvidenceRecord.from_value(evidence.as_dict() if isinstance(evidence, MemoryEvidenceRecord) else evidence)
        if not item:
            return None
        async with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO memory_evidence(
                    target_type, target_id, evidence_type, source_table, source_id,
                    session_id, message_id, date, summary, confidence, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    self._text(item.target_type),
                    self._text(item.target_id),
                    self._text(item.evidence_type) or "observation",
                    self._text(item.source_table),
                    self._text(item.source_id),
                    self._text(item.session_id),
                    self._text(item.message_id),
                    self._text(item.date),
                    self._text(item.summary),
                    max(float(item.confidence or 0.0), 0.0),
                    self._text(item.status) or "active",
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM memory_evidence WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._compose_memory_evidence(row) if row else None
    async def get_memory_evidence(
        self,
        target_type: str = "",
        target_id: str = "",
        limit: int = 30,
    ) -> list[MemoryEvidenceRecord]:
        async with self._lock:
            sql = "SELECT * FROM memory_evidence"
            params: list[Any] = []
            clauses = []
            if target_type:
                clauses.append("target_type = ?")
                params.append(self._text(target_type))
            if target_id:
                clauses.append("target_id = ?")
                params.append(self._text(target_id))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_memory_evidence(row) for row in rows]
