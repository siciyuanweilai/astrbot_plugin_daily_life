import sqlite3
from typing import Any

from ..models import (
    ActionDecisionRecord,
    GroupEnvironmentRecord,
    MessageVisibilityRecord,
)


class PerceptionArchiveMixin:
    def _recent_scoped_rows_unlocked(self, table: str, limit: int) -> list[sqlite3.Row]:
        sql = f"""
            SELECT *
            FROM {table}
            WHERE id IN (
                SELECT MAX(id)
                FROM {table}
                GROUP BY COALESCE(
                    NULLIF(group_id, ''),
                    NULLIF(session_id, ''),
                    NULLIF(sender_profile_id, ''),
                    CAST(id AS TEXT)
                )
            )
            ORDER BY id DESC
        """
        params: tuple[Any, ...] = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        return self._conn.execute(sql, params).fetchall()

    def _compose_group_environment(self, row: sqlite3.Row) -> GroupEnvironmentRecord:
        return GroupEnvironmentRecord(
            id=int(row["id"] or 0),
            session_id=row["session_id"],
            group_id=row["group_id"],
            group_name=row["group_name"],
            date=row["date"],
            atmosphere=row["atmosphere"],
            topic=self._text(row["topic"]),
            topic_owner=row["topic_owner"],
            active_users=int(row["active_users"] or 0),
            is_multithread=bool(row["is_multithread"]),
            is_spam=bool(row["is_spam"]),
            is_repetition=bool(row["is_repetition"]),
            is_discussing_bot=bool(row["is_discussing_bot"]),
            suitable_to_join=row["suitable_to_join"],
            bot_watch_state=row["bot_watch_state"],
            participation_desire=int(row["participation_desire"] or 0),
            complexity_score=int(row["complexity_score"] or 0),
            understanding_confidence=int(row["understanding_confidence"] or 0),
            deep_analysis_needed=bool(row["deep_analysis_needed"]),
            summary=self._text(row["summary"]),
            created_at=row["created_at"],
        )

    async def save_group_environment(self, environment: GroupEnvironmentRecord) -> GroupEnvironmentRecord:
        item = GroupEnvironmentRecord.from_value(
            environment.as_dict() if isinstance(environment, GroupEnvironmentRecord) else environment
        )
        if not item:
            raise ValueError("群聊环境快照不能为空")

        def write() -> GroupEnvironmentRecord:
            cursor = self._conn.execute(
                """
                INSERT INTO group_environments(
                    session_id, group_id, group_name, date, atmosphere, topic, topic_owner,
                    active_users, is_multithread, is_spam, is_repetition, is_discussing_bot,
                    suitable_to_join, bot_watch_state, participation_desire, complexity_score,
                    understanding_confidence, deep_analysis_needed, summary
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._text(item.session_id),
                    self._text(item.group_id),
                    self._text(item.group_name),
                    self._text(item.date),
                    self._text(item.atmosphere),
                    self._text(item.topic),
                    self._text(item.topic_owner),
                    max(int(item.active_users or 0), 0),
                    self._flag(item.is_multithread),
                    self._flag(item.is_spam),
                    self._flag(item.is_repetition),
                    self._flag(item.is_discussing_bot),
                    self._text(item.suitable_to_join),
                    self._text(item.bot_watch_state),
                    max(int(item.participation_desire or 0), 0),
                    max(int(item.complexity_score or 0), 0),
                    max(int(item.understanding_confidence or 0), 0),
                    self._flag(item.deep_analysis_needed),
                    self._text(item.summary),
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM group_environments WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._compose_group_environment(row)

        return await self._run_db(write)

    async def get_recent_group_environments(self, limit: int = 8) -> list[GroupEnvironmentRecord]:
        def read() -> list[GroupEnvironmentRecord]:
            sql = """
                SELECT *
                FROM group_environments
                WHERE group_id <> ''
                ORDER BY id DESC
            """
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self._conn.execute(sql, params).fetchall()
            return [self._compose_group_environment(row) for row in rows]

        return await self._run_db(read)

    def _compose_message_visibility(self, row: sqlite3.Row) -> MessageVisibilityRecord:
        return MessageVisibilityRecord(
            id=int(row["id"] or 0),
            session_id=row["session_id"],
            message_id=row["message_id"],
            sender_profile_id=row["sender_profile_id"],
            sender_name=row["sender_name"],
            group_id=row["group_id"],
            group_name=row["group_name"],
            date=row["date"],
            visibility=row["visibility"],
            attention_level=int(row["attention_level"] or 0),
            priority=row["priority"],
            is_directed_at_bot=bool(row["is_directed_at_bot"]),
            freshness=row["freshness"],
            psychological_freshness=int(row["psychological_freshness"] or 0),
            reactivated_from_id=int(row["reactivated_from_id"] or 0),
            reactivation_hint=self._text(row["reactivation_hint"]),
            reason=self._text(row["reason"]),
            created_at=row["created_at"],
        )

    async def save_message_visibility(self, visibility: MessageVisibilityRecord) -> MessageVisibilityRecord:
        item = MessageVisibilityRecord.from_value(
            visibility.as_dict() if isinstance(visibility, MessageVisibilityRecord) else visibility
        )
        if not item:
            raise ValueError("消息可见性记录不能为空")

        def write() -> MessageVisibilityRecord:
            cursor = self._conn.execute(
                """
                INSERT INTO message_visibility(
                    session_id, message_id, sender_profile_id, sender_name, group_id, group_name,
                    date, visibility, attention_level, priority, is_directed_at_bot,
                    freshness, psychological_freshness, reactivated_from_id, reactivation_hint, reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._text(item.session_id),
                    self._text(item.message_id),
                    self._text(item.sender_profile_id),
                    self._text(item.sender_name),
                    self._text(item.group_id),
                    self._text(item.group_name),
                    self._text(item.date),
                    self._text(item.visibility) or "seen",
                    max(int(item.attention_level or 0), 0),
                    self._text(item.priority) or "normal",
                    self._flag(item.is_directed_at_bot),
                    self._text(item.freshness),
                    max(int(item.psychological_freshness or 0), 0),
                    max(int(item.reactivated_from_id or 0), 0),
                    self._text(item.reactivation_hint),
                    self._text(item.reason),
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM message_visibility WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._compose_message_visibility(row)

        return await self._run_db(write)

    async def get_recent_message_visibility(self, limit: int = 8) -> list[MessageVisibilityRecord]:
        def read() -> list[MessageVisibilityRecord]:
            rows = self._recent_scoped_rows_unlocked("message_visibility", limit)
            return [self._compose_message_visibility(row) for row in rows]

        return await self._run_db(read)

    async def get_message_visibility_records(self, limit: int = 20) -> list[MessageVisibilityRecord]:
        def read() -> list[MessageVisibilityRecord]:
            sql = "SELECT * FROM message_visibility ORDER BY id DESC"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self._conn.execute(sql, params).fetchall()
            return [self._compose_message_visibility(row) for row in rows]

        return await self._run_db(read)

    def _compose_action_decision(self, row: sqlite3.Row) -> ActionDecisionRecord:
        return ActionDecisionRecord(
            id=int(row["id"] or 0),
            session_id=row["session_id"],
            message_id=row["message_id"],
            sender_profile_id=row["sender_profile_id"],
            sender_name=row["sender_name"],
            group_id=row["group_id"],
            group_name=row["group_name"],
            date=row["date"],
            action=row["action"],
            reason=self._text(row["reason"]),
            confidence=float(row["confidence"] or 0.0),
            scene_type=row["scene_type"],
            topic_owner=row["topic_owner"],
            understanding=row["understanding"],
            deep_analysis=bool(row["deep_analysis"]),
            inner_monologue=self._text(row["inner_monologue"]),
            reply_strategy=self._text(row["reply_strategy"]),
            created_at=row["created_at"],
        )

    async def save_action_decision(self, decision: ActionDecisionRecord) -> ActionDecisionRecord:
        item = ActionDecisionRecord.from_value(
            decision.as_dict() if isinstance(decision, ActionDecisionRecord) else decision
        )
        if not item:
            raise ValueError("动作裁定记录不能为空")

        def write() -> ActionDecisionRecord:
            cursor = self._conn.execute(
                """
                INSERT INTO action_decisions(
                    session_id, message_id, sender_profile_id, sender_name, group_id, group_name,
                    date, action, reason, confidence, scene_type, topic_owner, understanding,
                    deep_analysis, inner_monologue, reply_strategy
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._text(item.session_id),
                    self._text(item.message_id),
                    self._text(item.sender_profile_id),
                    self._text(item.sender_name),
                    self._text(item.group_id),
                    self._text(item.group_name),
                    self._text(item.date),
                    self._text(item.action),
                    self._text(item.reason),
                    max(float(item.confidence or 0.0), 0.0),
                    self._text(item.scene_type),
                    self._text(item.topic_owner),
                    self._text(item.understanding),
                    self._flag(item.deep_analysis),
                    self._text(item.inner_monologue),
                    self._text(item.reply_strategy),
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM action_decisions WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._compose_action_decision(row)

        return await self._run_db(write)

    async def get_recent_action_decisions(self, limit: int = 8) -> list[ActionDecisionRecord]:
        def read() -> list[ActionDecisionRecord]:
            rows = self._recent_scoped_rows_unlocked("action_decisions", limit)
            return [self._compose_action_decision(row) for row in rows]

        return await self._run_db(read)

    async def get_action_decision_records(self, limit: int = 20) -> list[ActionDecisionRecord]:
        sql = "SELECT * FROM action_decisions ORDER BY id DESC"
        params: tuple[Any, ...] = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)

        def read() -> list[ActionDecisionRecord]:
            rows = self._conn.execute(sql, params).fetchall()
            return [self._compose_action_decision(row) for row in rows]

        return await self._run_db(read)
