import sqlite3
from typing import Any

from ...clock import today as life_today
from ...models import (
    EmojiAssetRecord,
    ExpressionIntentRecord,
    ExpressionProfileRecord,
    ExpressionReviewRecord,
    TemporaryExpressionStateRecord,
)
from .lines import _pack_lines, _unpack_lines


class ExpressionArchiveMixin:
    def _compose_expression_profile(self, row: sqlite3.Row) -> ExpressionProfileRecord:
        return ExpressionProfileRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            profile_id=row["profile_id"],
            label=self._text(row["label"]),
            tone=self._text(row["tone"]),
            habits=_unpack_lines(row["habits"]),
            avoid=_unpack_lines(row["avoid"]),
            evidence=self._text(row["evidence"]),
            confidence=float(row["confidence"] or 0.0),
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def upsert_expression_profile(self, profile: ExpressionProfileRecord) -> ExpressionProfileRecord | None:
        item = ExpressionProfileRecord.from_value(
            profile.as_dict() if isinstance(profile, ExpressionProfileRecord) else profile
        )
        if not item:
            return None
        scope = self._text(item.scope)
        profile_id = self._text(item.profile_id)
        label = self._text(item.label) or scope or profile_id or "表达习惯"
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO expression_profiles(
                    scope, profile_id, label, tone, habits, avoid, evidence,
                    confidence, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, profile_id, label) DO UPDATE SET
                    tone = COALESCE(NULLIF(excluded.tone, ''), expression_profiles.tone),
                    habits = COALESCE(NULLIF(excluded.habits, ''), expression_profiles.habits),
                    avoid = COALESCE(NULLIF(excluded.avoid, ''), expression_profiles.avoid),
                    evidence = COALESCE(NULLIF(excluded.evidence, ''), expression_profiles.evidence),
                    confidence = MAX(expression_profiles.confidence, excluded.confidence),
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    scope,
                    profile_id,
                    label,
                    self._text(item.tone),
                    _pack_lines(item.habits),
                    _pack_lines(item.avoid),
                    self._text(item.evidence),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    self._text(item.source) or "chat_memory",
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM expression_profiles WHERE scope = ? AND profile_id = ? AND label = ?",
                (scope, profile_id, label),
            ).fetchone()
            return self._compose_expression_profile(row) if row else None
    async def get_expression_profiles(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        profile_id: str = "",
    ) -> list[ExpressionProfileRecord]:
        async with self._lock:
            sql = "SELECT * FROM expression_profiles"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("scope = ?")
                params.append(self._text(scope))
            if profile_id:
                clauses.append("profile_id = ?")
                params.append(self._text(profile_id))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, confidence DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_expression_profile(row) for row in rows]
    def _compose_expression_review(self, row: sqlite3.Row) -> ExpressionReviewRecord:
        return ExpressionReviewRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            reply_text=self._text(row["reply_text"]),
            passed=bool(row["passed"]),
            risk=self._text(row["risk"]),
            suggestion=self._text(row["suggestion"]),
            reason=self._text(row["reason"]),
            source=row["source"],
            created_at=row["created_at"],
        )
    async def save_expression_review(self, review: ExpressionReviewRecord) -> ExpressionReviewRecord | None:
        item = ExpressionReviewRecord.from_value(
            review.as_dict() if isinstance(review, ExpressionReviewRecord) else review
        )
        if not item:
            return None
        async with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO expression_reviews(
                    scope, reply_text, passed, risk, suggestion, reason, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    self._text(item.scope),
                    self._text(item.reply_text),
                    self._flag(item.passed),
                    self._text(item.risk),
                    self._text(item.suggestion),
                    self._text(item.reason),
                    self._text(item.source) or "proactive_reply",
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM expression_reviews WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._compose_expression_review(row) if row else None
    async def get_expression_reviews(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        passed: bool | None = None,
    ) -> list[ExpressionReviewRecord]:
        async with self._lock:
            sql = "SELECT * FROM expression_reviews"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("scope = ?")
                params.append(self._text(scope))
            if passed is not None:
                clauses.append("passed = ?")
                params.append(self._flag(passed))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_expression_review(row) for row in rows]
    def _compose_temporary_expression_state(self, row: sqlite3.Row) -> TemporaryExpressionStateRecord:
        return TemporaryExpressionStateRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            label=self._text(row["label"]),
            tone=self._text(row["tone"]),
            reason=self._text(row["reason"]),
            intensity=int(row["intensity"] or 0),
            expires_at=row["expires_at"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def upsert_temporary_expression_state(
        self,
        state: TemporaryExpressionStateRecord,
    ) -> TemporaryExpressionStateRecord | None:
        item = TemporaryExpressionStateRecord.from_value(
            state.as_dict() if isinstance(state, TemporaryExpressionStateRecord) else state
        )
        if not item:
            return None
        scope = self._text(item.scope)
        label = self._text(item.label) or self._text(item.tone) or "临时表达状态"
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO temporary_expression_states(
                    scope, label, tone, reason, intensity, expires_at,
                    source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, label) DO UPDATE SET
                    tone = COALESCE(NULLIF(excluded.tone, ''), temporary_expression_states.tone),
                    reason = COALESCE(NULLIF(excluded.reason, ''), temporary_expression_states.reason),
                    intensity = excluded.intensity,
                    expires_at = COALESCE(NULLIF(excluded.expires_at, ''), temporary_expression_states.expires_at),
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    scope,
                    label,
                    self._text(item.tone),
                    self._text(item.reason),
                    max(0, min(int(item.intensity or 0), 100)),
                    self._text(item.expires_at),
                    self._text(item.source) or "chat_memory",
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM temporary_expression_states WHERE scope = ? AND label = ?",
                (scope, label),
            ).fetchone()
            return self._compose_temporary_expression_state(row) if row else None
    async def get_temporary_expression_states(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        active_only: bool = True,
    ) -> list[TemporaryExpressionStateRecord]:
        async with self._lock:
            sql = "SELECT * FROM temporary_expression_states"
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
            sql += " ORDER BY intensity DESC, updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_temporary_expression_state(row) for row in rows]
    def _compose_expression_intent(self, row: sqlite3.Row) -> ExpressionIntentRecord:
        return ExpressionIntentRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            message_id=row["message_id"],
            reply_text=self._text(row["reply_text"]),
            emotion=self._text(row["emotion"]),
            emotion_category=self._text(row["emotion_category"]),
            emoji_intent=self._text(row["emoji_intent"]),
            action_intent=self._text(row["action_intent"]),
            send_emoji=bool(row["send_emoji"]),
            emoji_id=int(row["emoji_id"] or 0),
            reason=self._text(row["reason"]),
            source=row["source"],
            created_at=row["created_at"],
        )
    async def save_expression_intent(self, intent: ExpressionIntentRecord) -> ExpressionIntentRecord | None:
        item = ExpressionIntentRecord.from_value(
            intent.as_dict() if isinstance(intent, ExpressionIntentRecord) else intent
        )
        if not item:
            return None
        async with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO expression_intents(
                    scope, message_id, reply_text, emotion, emotion_category, emoji_intent, action_intent,
                    send_emoji, emoji_id, reason, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    self._text(item.scope),
                    self._text(item.message_id),
                    self._text(item.reply_text),
                    self._text(item.emotion),
                    self._text(item.emotion_category),
                    self._text(item.emoji_intent),
                    self._text(item.action_intent),
                    self._flag(item.send_emoji),
                    max(int(item.emoji_id or 0), 0),
                    self._text(item.reason),
                    self._text(item.source) or "proactive_reply",
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM expression_intents WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._compose_expression_intent(row) if row else None
    async def get_expression_intents(
        self,
        limit: int = 20,
        *,
        scope: str = "",
    ) -> list[ExpressionIntentRecord]:
        async with self._lock:
            sql = "SELECT * FROM expression_intents"
            params: list[Any] = []
            if scope:
                sql += " WHERE scope = ? OR scope = ''"
                params.append(self._text(scope))
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_expression_intent(row) for row in rows]
    def _compose_emoji_asset(self, row: sqlite3.Row) -> EmojiAssetRecord:
        return EmojiAssetRecord(
            id=int(row["id"] or 0),
            file_hash=row["file_hash"],
            file_path=self._text(row["file_path"]),
            label=self._text(row["label"]),
            description=self._text(row["description"]),
            emotions=_unpack_lines(row["emotions"]),
            source_scope=row["source_scope"],
            source_message_id=row["source_message_id"],
            source_url=self._text(row["source_url"]),
            status=row["status"],
            used_count=int(row["used_count"] or 0),
            last_used_at=row["last_used_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def upsert_emoji_asset(self, asset: EmojiAssetRecord) -> EmojiAssetRecord | None:
        item = EmojiAssetRecord.from_value(asset.as_dict() if isinstance(asset, EmojiAssetRecord) else asset)
        if not item:
            return None
        file_hash = self._text(item.file_hash) or self._text(item.file_path)
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO emoji_assets(
                    file_hash, file_path, label, description, emotions, source_scope,
                    source_message_id, source_url, status, used_count, last_used_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(file_hash) DO UPDATE SET
                    file_path = COALESCE(NULLIF(excluded.file_path, ''), emoji_assets.file_path),
                    label = COALESCE(NULLIF(excluded.label, ''), emoji_assets.label),
                    description = COALESCE(NULLIF(excluded.description, ''), emoji_assets.description),
                    emotions = COALESCE(NULLIF(excluded.emotions, ''), emoji_assets.emotions),
                    source_scope = COALESCE(NULLIF(excluded.source_scope, ''), emoji_assets.source_scope),
                    source_message_id = COALESCE(NULLIF(excluded.source_message_id, ''), emoji_assets.source_message_id),
                    source_url = COALESCE(NULLIF(excluded.source_url, ''), emoji_assets.source_url),
                    status = CASE
                        WHEN emoji_assets.status IN ('failed', 'disabled') AND excluded.status = 'pending'
                        THEN emoji_assets.status
                        ELSE COALESCE(NULLIF(excluded.status, ''), emoji_assets.status)
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    file_hash,
                    self._text(item.file_path),
                    self._text(item.label),
                    self._text(item.description),
                    _pack_lines(item.emotions),
                    self._text(item.source_scope),
                    self._text(item.source_message_id),
                    self._text(item.source_url),
                    self._text(item.status) or "pending",
                    max(int(item.used_count or 0), 0),
                    self._text(item.last_used_at),
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM emoji_assets WHERE file_hash = ?", (file_hash,)).fetchone()
            return self._compose_emoji_asset(row) if row else None

    async def get_emoji_asset_by_hash(self, file_hash: str) -> EmojiAssetRecord | None:
        async with self._lock:
            row = self._conn.execute(
                "SELECT * FROM emoji_assets WHERE file_hash = ?",
                (self._text(file_hash),),
            ).fetchone()
            return self._compose_emoji_asset(row) if row else None

    async def get_emoji_assets(
        self,
        limit: int = 20,
        *,
        status: str = "",
    ) -> list[EmojiAssetRecord]:
        async with self._lock:
            sql = "SELECT * FROM emoji_assets"
            params: list[Any] = []
            if status:
                sql += " WHERE status = ?"
                params.append(self._text(status))
            sql += " ORDER BY used_count ASC, updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_emoji_asset(row) for row in rows]

    async def delete_emoji_assets(self, emoji_ids: list[int]) -> int:
        ids = sorted({int(item) for item in emoji_ids if int(item or 0) > 0})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        async with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM emoji_assets WHERE id IN ({placeholders})",
                tuple(ids),
            )
            self._conn.commit()
            return max(int(cursor.rowcount or 0), 0)

    async def mark_emoji_used(self, emoji_id: int, used_at: str = "") -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE emoji_assets
                SET used_count = used_count + 1,
                    last_used_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (self._text(used_at), int(emoji_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
