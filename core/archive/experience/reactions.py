import sqlite3
from typing import Any

from ...models import BehaviorFeedbackRecord, MemoryCorrectionRecord, ReplyEffectRecord


class FeedbackArchiveMixin:
    def _compose_behavior_feedback(self, row: sqlite3.Row) -> BehaviorFeedbackRecord:
        return BehaviorFeedbackRecord(
            id=int(row["id"] or 0),
            date=row["date"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            scene=row["scene"],
            action=row["action"],
            feedback=self._text(row["feedback"]),
            result=row["result"],
            score=float(row["score"] or 0.0),
            reason=self._text(row["reason"]),
            source=row["source"],
            created_at=row["created_at"],
        )
    async def add_behavior_feedback(self, feedback: BehaviorFeedbackRecord) -> BehaviorFeedbackRecord | None:
        item = BehaviorFeedbackRecord.from_value(
            feedback.as_dict() if isinstance(feedback, BehaviorFeedbackRecord) else feedback
        )
        if not item:
            return None
        values = (
            self._text(item.date),
            self._text(item.target_type) or "action",
            self._text(item.target_id),
            self._text(item.scene),
            self._text(item.action),
            self._text(item.feedback),
            self._text(item.result),
            self._text(item.reason),
            self._text(item.source) or "chat_memory",
        )
        async with self._lock:
            existing = self._conn.execute(
                """
                SELECT * FROM behavior_feedback
                WHERE date = ?
                  AND target_type = ?
                  AND target_id = ?
                  AND scene = ?
                  AND action = ?
                  AND feedback = ?
                  AND result = ?
                  AND reason = ?
                  AND source = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                values,
            ).fetchone()
            if existing:
                return self._compose_behavior_feedback(existing)
            cursor = self._conn.execute(
                """
                INSERT INTO behavior_feedback(
                    date, target_type, target_id, scene, action, feedback,
                    result, score, reason, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    *values[:7],
                    max(-5.0, min(float(item.score or 0.0), 5.0)),
                    values[7],
                    values[8],
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM behavior_feedback WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._compose_behavior_feedback(row) if row else None
    async def get_behavior_feedback(self, limit: int = 20) -> list[BehaviorFeedbackRecord]:
        async with self._lock:
            sql = "SELECT * FROM behavior_feedback ORDER BY date DESC, id DESC"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self._conn.execute(sql, params).fetchall()
            return [self._compose_behavior_feedback(row) for row in rows]
    def _compose_reply_effect(self, row: sqlite3.Row) -> ReplyEffectRecord:
        return ReplyEffectRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            target_message_id=row["target_message_id"],
            reply_text=self._text(row["reply_text"]),
            outcome=row["outcome"],
            warmth=int(row["warmth"] or 0),
            continuity=int(row["continuity"] or 0),
            friction=int(row["friction"] or 0),
            reason=self._text(row["reason"]),
            evidence=self._text(row["evidence"]),
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def save_reply_effect(self, effect: ReplyEffectRecord) -> ReplyEffectRecord | None:
        item = ReplyEffectRecord.from_value(effect.as_dict() if isinstance(effect, ReplyEffectRecord) else effect)
        if not item:
            return None
        async with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO reply_effects(
                    scope, target_message_id, reply_text, outcome, warmth, continuity,
                    friction, reason, evidence, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    self._text(item.scope),
                    self._text(item.target_message_id),
                    self._text(item.reply_text),
                    self._text(item.outcome) or "pending",
                    max(0, min(int(item.warmth or 0), 100)),
                    max(0, min(int(item.continuity or 0), 100)),
                    max(0, min(int(item.friction or 0), 100)),
                    self._text(item.reason),
                    self._text(item.evidence),
                    self._text(item.source) or "proactive_reply",
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM reply_effects WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._compose_reply_effect(row) if row else None
    async def update_reply_effect_outcome(
        self,
        effect_id: int,
        *,
        outcome: str,
        evidence: str = "",
        warmth: int | None = None,
        continuity: int | None = None,
        friction: int | None = None,
    ) -> bool:
        async with self._lock:
            row = self._conn.execute("SELECT * FROM reply_effects WHERE id = ?", (int(effect_id),)).fetchone()
            if not row:
                return False
            cursor = self._conn.execute(
                """
                UPDATE reply_effects
                SET outcome = ?,
                    evidence = COALESCE(NULLIF(?, ''), evidence),
                    warmth = ?,
                    continuity = ?,
                    friction = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    self._text(outcome) or row["outcome"],
                    self._text(evidence),
                    max(0, min(int(row["warmth"] if warmth is None else warmth), 100)),
                    max(0, min(int(row["continuity"] if continuity is None else continuity), 100)),
                    max(0, min(int(row["friction"] if friction is None else friction), 100)),
                    int(effect_id),
                ),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    async def expire_stale_reply_effects(
        self,
        max_age_seconds: int,
        *,
        evidence: str = "闲时续话后一段时间内没有新的可见回应",
    ) -> int:
        max_age = max(int(max_age_seconds or 0), 1)
        async with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE reply_effects
                SET outcome = 'ignored',
                    evidence = COALESCE(NULLIF(evidence, ''), ?),
                    warmth = MIN(warmth, 35),
                    continuity = MIN(continuity, 25),
                    friction = MAX(friction, 10),
                    updated_at = CURRENT_TIMESTAMP
                WHERE outcome = 'pending'
                  AND COALESCE(NULLIF(updated_at, ''), created_at) < datetime('now', ?)
                """,
                (self._text(evidence), f"-{max_age} seconds"),
            )
            self._conn.commit()
            return max(int(cursor.rowcount or 0), 0)
    async def get_reply_effects(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        outcome: str = "",
    ) -> list[ReplyEffectRecord]:
        async with self._lock:
            sql = "SELECT * FROM reply_effects"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("scope = ?")
                params.append(self._text(scope))
            if outcome:
                clauses.append("outcome = ?")
                params.append(self._text(outcome))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_reply_effect(row) for row in rows]
    def _compose_memory_correction(self, row: sqlite3.Row) -> MemoryCorrectionRecord:
        return MemoryCorrectionRecord(
            id=int(row["id"] or 0),
            target_type=row["target_type"],
            target_id=row["target_id"],
            correction=self._text(row["correction"]),
            evidence=self._text(row["evidence"]),
            confidence=float(row["confidence"] or 0.0),
            applied=bool(row["applied"]),
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def save_memory_correction(self, correction: MemoryCorrectionRecord) -> MemoryCorrectionRecord | None:
        item = MemoryCorrectionRecord.from_value(
            correction.as_dict() if isinstance(correction, MemoryCorrectionRecord) else correction
        )
        if not item:
            return None
        async with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO memory_corrections(
                    target_type, target_id, correction, evidence, confidence,
                    applied, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    self._text(item.target_type),
                    self._text(item.target_id),
                    self._text(item.correction),
                    self._text(item.evidence),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    self._flag(item.applied),
                    self._text(item.source) or "chat_memory",
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM memory_corrections WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._compose_memory_correction(row) if row else None
    async def mark_memory_correction_applied(self, correction_id: int, applied: bool = True) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                "UPDATE memory_corrections SET applied = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (self._flag(applied), int(correction_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    async def get_memory_corrections(
        self,
        limit: int = 20,
        *,
        target_type: str = "",
        target_id: str = "",
        unapplied_only: bool = False,
    ) -> list[MemoryCorrectionRecord]:
        async with self._lock:
            sql = "SELECT * FROM memory_corrections"
            params: list[Any] = []
            clauses = []
            if target_type:
                clauses.append("target_type = ?")
                params.append(self._text(target_type))
            if target_id:
                clauses.append("target_id = ?")
                params.append(self._text(target_id))
            if unapplied_only:
                clauses.append("applied = 0")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_memory_correction(row) for row in rows]
