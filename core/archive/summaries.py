import sqlite3
from typing import Any

from ..models import ChatSummaryRecord


class SummaryArchiveMixin:
    def _compose_chat_summary(self, row: sqlite3.Row) -> ChatSummaryRecord:
        return ChatSummaryRecord(
            id=int(row["id"] or 0),
            session_id=row["session_id"],
            date=row["date"],
            brief=self._text(row["brief"]),
            long_summary=self._text(row["long_summary"]),
            people=self._get_people_unlocked("chat_summary_people", "summary_id", row["id"]),
            source=row["source"],
            created_at=row["created_at"],
        )

    async def save_chat_summary(self, summary: ChatSummaryRecord) -> ChatSummaryRecord:
        item = ChatSummaryRecord.from_value(summary.as_dict() if isinstance(summary, ChatSummaryRecord) else summary)
        if not item:
            raise ValueError("聊天摘要不能为空")

        def write() -> ChatSummaryRecord:
            if item.id:
                cursor = self._conn.execute(
                    """
                    UPDATE chat_summaries
                    SET session_id = ?, date = ?, brief = ?, long_summary = ?, source = ?
                    WHERE id = ?
                    """,
                    (
                        self._text(item.session_id),
                        self._text(item.date),
                        self._text(item.brief),
                        self._text(item.long_summary),
                        self._text(item.source) or "chat",
                        int(item.id),
                    ),
                )
                summary_id = int(item.id)
                if cursor.rowcount <= 0:
                    item.id = 0
            if not item.id:
                cursor = self._conn.execute(
                    """
                    INSERT INTO chat_summaries(session_id, date, brief, long_summary, source)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        self._text(item.session_id),
                        self._text(item.date),
                        self._text(item.brief),
                        self._text(item.long_summary),
                        self._text(item.source) or "chat",
                    ),
                )
                summary_id = int(cursor.lastrowid)
            self._replace_people_unlocked("chat_summary_people", "summary_id", summary_id, item.people)
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM chat_summaries WHERE id = ?", (summary_id,)).fetchone()
            return self._compose_chat_summary(row)

        return await self._run_db(write)

    async def get_recent_chat_summaries(self, limit: int = 8) -> list[ChatSummaryRecord]:
        def read() -> list[ChatSummaryRecord]:
            sql = "SELECT * FROM chat_summaries ORDER BY id DESC"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self._conn.execute(sql, params).fetchall()
            return [self._compose_chat_summary(row) for row in rows]

        return await self._run_db(read)
