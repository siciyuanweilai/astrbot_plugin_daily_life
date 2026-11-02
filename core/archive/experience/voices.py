from __future__ import annotations

import sqlite3
import json
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
from .expressiongrowth import apply_expression_learning, get_expression_context, learning_value, recover_expression_watch

from typing import Protocol


class ExpressionArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _compose_emoji_asset: Any
    _compose_expression_intent: Any
    _compose_expression_profile: Any
    _compose_expression_review: Any
    _compose_temporary_expression_state: Any
    _conn: Any
    _flag: Any
    _run_db: Any
    _text: Any


class ExpressionArchiveComponent:
    def __init__(self, context: ExpressionArchiveComponentContext) -> None:
        self.context = context

    def _compose_expression_profile(self, row: sqlite3.Row) -> ExpressionProfileRecord:
        return ExpressionProfileRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            profile_id=row["profile_id"],
            label=self.context._text(row["label"]),
            tone=self.context._text(row["tone"]),
            habits=_unpack_lines(row["habits"]),
            avoid=_unpack_lines(row["avoid"]),
            evidence=self.context._text(row["evidence"]),
            confidence=float(row["confidence"] or 0.0),
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            learning=learning_value(row["learning_json"]),
        )

    async def upsert_expression_profile(
        self, profile: ExpressionProfileRecord, *, replace: bool = False
    ) -> ExpressionProfileRecord | None:
        item = ExpressionProfileRecord.from_value(
            profile.as_dict()
            if isinstance(profile, ExpressionProfileRecord)
            else profile
        )
        if not item:
            return None
        scope = self.context._text(item.scope)
        profile_id = self.context._text(item.profile_id)
        label = self.context._text(item.label) or scope or profile_id or "表达习惯"
        updates = (
            "tone = excluded.tone, habits = excluded.habits, avoid = excluded.avoid, "
            "evidence = excluded.evidence, confidence = excluded.confidence,"
            if replace
            else """
                tone = COALESCE(NULLIF(excluded.tone, ''), expression_profiles.tone),
                habits = COALESCE(NULLIF(excluded.habits, ''), expression_profiles.habits),
                avoid = COALESCE(NULLIF(excluded.avoid, ''), expression_profiles.avoid),
                evidence = COALESCE(NULLIF(excluded.evidence, ''), expression_profiles.evidence),
                confidence = MAX(expression_profiles.confidence, excluded.confidence),
            """
        )

        def dbwork():
            self.context._conn.execute(
                f"""
                INSERT INTO expression_profiles(
                    scope, profile_id, label, tone, habits, avoid, evidence,
                    confidence, source, learning_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, profile_id, label) DO UPDATE SET
                    {updates}
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    scope,
                    profile_id,
                    label,
                    self.context._text(item.tone),
                    _pack_lines(item.habits),
                    _pack_lines(item.avoid),
                    self.context._text(item.evidence),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    self.context._text(item.source) or "chat_memory",
                    json.dumps(item.learning, ensure_ascii=False),
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM expression_profiles WHERE scope = ? AND profile_id = ? AND label = ?",
                (scope, profile_id, label),
            ).fetchone()
            return self.context._compose_expression_profile(row) if row else None

        return await self.context._run_db(dbwork)

    async def apply_expression_learning(self, profile: ExpressionProfileRecord, event: dict[str, Any]) -> ExpressionProfileRecord | None:
        return await apply_expression_learning(self.context, profile, event)

    async def get_expression_context(self, *, scope: str, profile_id: str = "", limit: int = 16) -> list[ExpressionProfileRecord]:
        return await get_expression_context(self.context, scope=scope, profile_id=profile_id, limit=limit)

    async def recover_expression_watch(self, *, scope: str, max_age_seconds: int = 1800) -> dict[str, Any] | None:
        return await recover_expression_watch(self.context, scope=scope, max_age_seconds=max_age_seconds)

    async def get_expression_profiles(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        profile_id: str = "",
    ) -> list[ExpressionProfileRecord]:
        def dbwork():
            sql = "SELECT * FROM expression_profiles"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("scope = ?")
                params.append(self.context._text(scope))
            if profile_id:
                clauses.append("profile_id = ?")
                params.append(self.context._text(profile_id))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, confidence DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_expression_profile(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_expression_review(self, row: sqlite3.Row) -> ExpressionReviewRecord:
        return ExpressionReviewRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            reply_text=self.context._text(row["reply_text"]),
            passed=bool(row["passed"]),
            risk=self.context._text(row["risk"]),
            suggestion=self.context._text(row["suggestion"]),
            reason=self.context._text(row["reason"]),
            source=row["source"],
            created_at=row["created_at"],
        )

    async def save_expression_review(
        self, review: ExpressionReviewRecord
    ) -> ExpressionReviewRecord | None:
        item = ExpressionReviewRecord.from_value(
            review.as_dict() if isinstance(review, ExpressionReviewRecord) else review
        )
        if not item:
            return None

        def dbwork():
            cursor = self.context._conn.execute(
                """
                INSERT INTO expression_reviews(
                    scope, reply_text, passed, risk, suggestion, reason, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    self.context._text(item.scope),
                    self.context._text(item.reply_text),
                    self.context._flag(item.passed),
                    self.context._text(item.risk),
                    self.context._text(item.suggestion),
                    self.context._text(item.reason),
                    self.context._text(item.source) or "proactive_reply",
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM expression_reviews WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return self.context._compose_expression_review(row) if row else None

        return await self.context._run_db(dbwork)

    async def get_expression_reviews(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        passed: bool | None = None,
    ) -> list[ExpressionReviewRecord]:
        def dbwork():
            sql = "SELECT * FROM expression_reviews"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("scope = ?")
                params.append(self.context._text(scope))
            if passed is not None:
                clauses.append("passed = ?")
                params.append(self.context._flag(passed))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_expression_review(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_temporary_expression_state(
        self, row: sqlite3.Row
    ) -> TemporaryExpressionStateRecord:
        return TemporaryExpressionStateRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            label=self.context._text(row["label"]),
            tone=self.context._text(row["tone"]),
            reason=self.context._text(row["reason"]),
            intensity=int(row["intensity"] or 0),
            expires_at=row["expires_at"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


    async def get_temporary_expression_states(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        active_only: bool = True,
    ) -> list[TemporaryExpressionStateRecord]:
        def dbwork():
            sql = "SELECT * FROM temporary_expression_states"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("(scope = ? OR scope = '')")
                params.append(self.context._text(scope))
            if active_only:
                clauses.append("(expires_at = '' OR expires_at >= ?)")
                params.append(life_today().isoformat())
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY intensity DESC, updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_temporary_expression_state(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_expression_intent(self, row: sqlite3.Row) -> ExpressionIntentRecord:
        return ExpressionIntentRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            message_id=row["message_id"],
            reply_text=self.context._text(row["reply_text"]),
            emotion=self.context._text(row["emotion"]),
            emotion_category=self.context._text(row["emotion_category"]),
            emoji_intent=self.context._text(row["emoji_intent"]),
            action_intent=self.context._text(row["action_intent"]),
            send_emoji=bool(row["send_emoji"]),
            emoji_id=int(row["emoji_id"] or 0),
            reason=self.context._text(row["reason"]),
            source=row["source"],
            created_at=row["created_at"],
        )

    async def save_expression_intent(
        self, intent: ExpressionIntentRecord
    ) -> ExpressionIntentRecord | None:
        item = ExpressionIntentRecord.from_value(
            intent.as_dict() if isinstance(intent, ExpressionIntentRecord) else intent
        )
        if not item:
            return None

        def dbwork():
            cursor = self.context._conn.execute(
                """
                INSERT INTO expression_intents(
                    scope, message_id, reply_text, emotion, emotion_category, emoji_intent, action_intent,
                    send_emoji, emoji_id, reason, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    self.context._text(item.scope),
                    self.context._text(item.message_id),
                    self.context._text(item.reply_text),
                    self.context._text(item.emotion),
                    self.context._text(item.emotion_category),
                    self.context._text(item.emoji_intent),
                    self.context._text(item.action_intent),
                    self.context._flag(item.send_emoji),
                    max(int(item.emoji_id or 0), 0),
                    self.context._text(item.reason),
                    self.context._text(item.source) or "proactive_reply",
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM expression_intents WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return self.context._compose_expression_intent(row) if row else None

        return await self.context._run_db(dbwork)

    async def get_expression_intents(
        self,
        limit: int = 20,
        *,
        scope: str = "",
    ) -> list[ExpressionIntentRecord]:
        def dbwork():
            sql = "SELECT * FROM expression_intents"
            params: list[Any] = []
            if scope:
                sql += " WHERE scope = ? OR scope = ''"
                params.append(self.context._text(scope))
            sql += " ORDER BY id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_expression_intent(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_emoji_asset(self, row: sqlite3.Row) -> EmojiAssetRecord:
        return EmojiAssetRecord(
            id=int(row["id"] or 0),
            file_hash=row["file_hash"],
            file_path=self.context._text(row["file_path"]),
            label=self.context._text(row["label"]),
            description=self.context._text(row["description"]),
            emotions=_unpack_lines(row["emotions"]),
            source_scope=row["source_scope"],
            source_message_id=row["source_message_id"],
            source_url=self.context._text(row["source_url"]),
            source_kind=self.context._text(row["source_kind"]) or "trusted",
            asset_type=self.context._text(row["asset_type"]),
            confidence=float(row["confidence"] or 0.0),
            sendable=bool(row["sendable"]),
            rejected_reason=self.context._text(row["rejected_reason"]),
            status=row["status"],
            used_count=int(row["used_count"] or 0),
            last_used_at=row["last_used_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def upsert_emoji_asset(
        self, asset: EmojiAssetRecord
    ) -> EmojiAssetRecord | None:
        item = EmojiAssetRecord.from_value(
            asset.as_dict() if isinstance(asset, EmojiAssetRecord) else asset
        )
        if not item:
            return None
        file_hash = self.context._text(item.file_hash) or self.context._text(item.file_path)

        def dbwork():
            self.context._conn.execute(
                """
                INSERT INTO emoji_assets(
                    file_hash, file_path, label, description, emotions, source_scope,
                    source_message_id, source_url, source_kind, asset_type, confidence, sendable,
                    rejected_reason, status, used_count, last_used_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(file_hash) DO UPDATE SET
                    file_path = COALESCE(NULLIF(excluded.file_path, ''), emoji_assets.file_path),
                    label = COALESCE(NULLIF(excluded.label, ''), emoji_assets.label),
                    description = COALESCE(NULLIF(excluded.description, ''), emoji_assets.description),
                    emotions = COALESCE(NULLIF(excluded.emotions, ''), emoji_assets.emotions),
                    source_scope = COALESCE(NULLIF(excluded.source_scope, ''), emoji_assets.source_scope),
                    source_message_id = COALESCE(NULLIF(excluded.source_message_id, ''), emoji_assets.source_message_id),
                    source_url = COALESCE(NULLIF(excluded.source_url, ''), emoji_assets.source_url),
                    source_kind = COALESCE(NULLIF(excluded.source_kind, ''), emoji_assets.source_kind),
                    asset_type = COALESCE(NULLIF(excluded.asset_type, ''), emoji_assets.asset_type),
                    confidence = CASE
                        WHEN excluded.confidence > 0 THEN excluded.confidence
                        ELSE emoji_assets.confidence
                    END,
                    sendable = excluded.sendable,
                    rejected_reason = COALESCE(NULLIF(excluded.rejected_reason, ''), emoji_assets.rejected_reason),
                    status = CASE
                        WHEN emoji_assets.status IN ('failed', 'disabled', 'rejected') AND excluded.status IN ('pending', 'reviewing')
                        THEN emoji_assets.status
                        ELSE COALESCE(NULLIF(excluded.status, ''), emoji_assets.status)
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    file_hash,
                    self.context._text(item.file_path),
                    self.context._text(item.label),
                    self.context._text(item.description),
                    _pack_lines(item.emotions),
                    self.context._text(item.source_scope),
                    self.context._text(item.source_message_id),
                    self.context._text(item.source_url),
                    self.context._text(item.source_kind) or "trusted",
                    self.context._text(item.asset_type),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    self.context._flag(item.sendable),
                    self.context._text(item.rejected_reason),
                    self.context._text(item.status) or "pending",
                    max(int(item.used_count or 0), 0),
                    self.context._text(item.last_used_at),
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM emoji_assets WHERE file_hash = ?", (file_hash,)
            ).fetchone()
            return self.context._compose_emoji_asset(row) if row else None

        return await self.context._run_db(dbwork)

    async def get_emoji_asset_by_hash(self, file_hash: str) -> EmojiAssetRecord | None:
        def dbwork():
            row = self.context._conn.execute(
                "SELECT * FROM emoji_assets WHERE file_hash = ?",
                (self.context._text(file_hash),),
            ).fetchone()
            return self.context._compose_emoji_asset(row) if row else None

        return await self.context._run_db(dbwork)

    async def get_emoji_assets(
        self,
        limit: int = 20,
        *,
        status: str = "",
        sendable_only: bool = False,
    ) -> list[EmojiAssetRecord]:
        def dbwork():
            sql = "SELECT * FROM emoji_assets"
            params: list[Any] = []
            clauses: list[str] = []
            if status:
                clauses.append("status = ?")
                params.append(self.context._text(status))
            if sendable_only:
                clauses.append("sendable = 1")
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY used_count ASC, updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_emoji_asset(row) for row in rows]

        return await self.context._run_db(dbwork)

    async def delete_emoji_assets(self, emoji_ids: list[int]) -> int:
        ids = sorted({int(item) for item in emoji_ids if int(item or 0) > 0})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)

        def dbwork():
            cursor = self.context._conn.execute(
                f"DELETE FROM emoji_assets WHERE id IN ({placeholders})",
                tuple(ids),
            )
            self.context._conn.commit()
            return max(int(cursor.rowcount or 0), 0)

        return await self.context._run_db(dbwork)

    async def mark_emoji_used(self, emoji_id: int, used_at: str = "") -> bool:
        def dbwork():
            cursor = self.context._conn.execute(
                """
                UPDATE emoji_assets
                SET used_count = used_count + 1,
                    last_used_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (self.context._text(used_at), int(emoji_id)),
            )
            self.context._conn.commit()
            return cursor.rowcount > 0

        return await self.context._run_db(dbwork)
