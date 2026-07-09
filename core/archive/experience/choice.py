import sqlite3
from typing import Any

from ...models import LifeDecisionRecord


class LifeDecisionArchiveMixin:
    def _compose_life_decision(self, row: sqlite3.Row) -> LifeDecisionRecord:
        return LifeDecisionRecord(
            id=int(row["id"] or 0),
            date=row["date"],
            kind=row["kind"],
            subject=self._text(row["subject"]),
            decision=self._text(row["decision"]),
            reason=self._text(row["reason"]),
            evidence=self._text(row["evidence"]),
            outcome=self._text(row["outcome"]),
            confidence=float(row["confidence"] or 0.0),
            source=row["source"],
            created_at=row["created_at"],
        )

    async def save_life_decision(self, decision: LifeDecisionRecord) -> LifeDecisionRecord | None:
        item = LifeDecisionRecord.from_value(
            decision.as_dict() if isinstance(decision, LifeDecisionRecord) else decision
        )
        if not item:
            return None

        async with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO life_decisions(
                    date, kind, subject, decision, reason, evidence,
                    outcome, confidence, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    self._text(item.date),
                    self._text(item.kind) or "daily_plan",
                    self._text(item.subject),
                    self._text(item.decision),
                    self._text(item.reason),
                    self._text(item.evidence),
                    self._text(item.outcome),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    self._text(item.source) or "autonomous_life",
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM life_decisions WHERE id = ?", (cursor.lastrowid,)).fetchone()
            saved = self._compose_life_decision(row) if row else None
            linker = getattr(self, "_link_decision_memory_candidates_unlocked", None)
            if saved and callable(linker):
                linker(saved)
                self._conn.commit()
            return saved

    async def get_life_decisions(
        self,
        limit: int = 20,
        *,
        kind: str = "",
        date: str = "",
    ) -> list[LifeDecisionRecord]:
        async with self._lock:
            sql = "SELECT * FROM life_decisions"
            params: list[Any] = []
            clauses = []
            if kind:
                clauses.append("kind = ?")
                params.append(self._text(kind))
            if date:
                clauses.append("date = ?")
                params.append(self._text(date))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_life_decision(row) for row in rows]
