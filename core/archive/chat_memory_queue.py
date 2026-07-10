from __future__ import annotations

from typing import Any


class ChatMemoryQueueArchiveMixin:
    async def enqueue_chat_memory_message(self, snapshot: dict[str, Any]) -> tuple[int, bool]:
        def write() -> tuple[int, bool]:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO chat_memory_messages(
                    event_key, session_id, message_id, sender_profile_id, sender_name,
                    platform, user_id, group_id, group_name, is_group, is_directed,
                    is_quoted, message_text, message_facts, quote_context,
                    structured_context, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._text(snapshot.get("event_key")), self._text(snapshot.get("session_id")),
                    self._text(snapshot.get("message_id")), self._text(snapshot.get("sender_profile_id")),
                    self._text(snapshot.get("sender_name")), self._text(snapshot.get("platform")),
                    self._text(snapshot.get("user_id")), self._text(snapshot.get("group_id")),
                    self._text(snapshot.get("group_name")), int(bool(snapshot.get("is_group"))),
                    int(bool(snapshot.get("is_directed"))), int(bool(snapshot.get("is_quoted"))),
                    self._text(snapshot.get("message_text")), self._text(snapshot.get("message_facts")),
                    self._text(snapshot.get("quote_context")), self._text(snapshot.get("structured_context")),
                    self._text(snapshot.get("occurred_at")),
                ),
            )
            inserted = cursor.rowcount > 0
            if inserted:
                row_id = int(cursor.lastrowid)
                self._conn.execute(
                    """
                    INSERT INTO chat_memory_sessions(
                        session_id, is_group, pending_since, last_message_at, updated_at
                    ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(session_id) DO UPDATE SET
                        is_group = excluded.is_group,
                        pending_since = CASE
                            WHEN chat_memory_sessions.last_processed_row_id >= ? THEN excluded.pending_since
                            ELSE chat_memory_sessions.pending_since
                        END,
                        last_message_at = excluded.last_message_at,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        self._text(snapshot.get("session_id")), int(bool(snapshot.get("is_group"))),
                        self._text(snapshot.get("occurred_at")), self._text(snapshot.get("occurred_at")), row_id - 1,
                    ),
                )
                self._conn.commit()
                return row_id, True
            row = self._conn.execute(
                "SELECT id FROM chat_memory_messages WHERE event_key = ?",
                (self._text(snapshot.get("event_key")),),
            ).fetchone()
            return (int(row["id"]) if row else 0), False

        return await self._run_db(write)

    async def list_chat_memory_sessions(self) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            rows = self._conn.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM chat_memory_messages m
                        WHERE m.session_id = s.session_id AND m.id > s.last_processed_row_id) AS pending_count
                FROM chat_memory_sessions s
                WHERE EXISTS (
                    SELECT 1 FROM chat_memory_messages m
                    WHERE m.session_id = s.session_id AND m.id > s.last_processed_row_id
                )
                ORDER BY s.last_message_at ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run_db(read)

    async def begin_chat_memory_batch(
        self,
        session_id: str,
        *,
        max_messages: int,
        max_chars: int,
    ) -> dict[str, Any] | None:
        def write() -> dict[str, Any] | None:
            state = self._conn.execute(
                "SELECT last_processed_row_id FROM chat_memory_sessions WHERE session_id = ?",
                (self._text(session_id),),
            ).fetchone()
            if not state:
                return None
            rows = self._conn.execute(
                """
                SELECT * FROM chat_memory_messages
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (self._text(session_id), int(state["last_processed_row_id"]), max(1, int(max_messages))),
            ).fetchall()
            selected = []
            total_chars = 0
            for row in rows:
                row_chars = len(str(row["message_text"] or "")) + len(str(row["message_facts"] or ""))
                if selected and total_chars + row_chars > max(1, int(max_chars)):
                    break
                selected.append(dict(row))
                total_chars += row_chars
            if not selected:
                return None
            first_id, last_id = int(selected[0]["id"]), int(selected[-1]["id"])
            batch_key = f"{session_id}:{first_id}:{last_id}"
            existing = self._conn.execute(
                "SELECT * FROM chat_memory_batches WHERE batch_key = ?",
                (batch_key,),
            ).fetchone()
            if existing and str(existing["status"]) == "completed":
                self._conn.execute(
                    "UPDATE chat_memory_sessions SET last_processed_row_id = MAX(last_processed_row_id, ?), updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                    (last_id, self._text(session_id)),
                )
                self._conn.commit()
                return None
            if existing:
                batch_id = int(existing["id"])
                self._conn.execute(
                    "UPDATE chat_memory_batches SET status = 'processing', attempt_count = attempt_count + 1, error = '' WHERE id = ?",
                    (batch_id,),
                )
            else:
                cursor = self._conn.execute(
                    """
                    INSERT INTO chat_memory_batches(
                        batch_key, session_id, first_row_id, last_row_id, message_count, status
                    ) VALUES (?, ?, ?, ?, ?, 'processing')
                    """,
                    (batch_key, self._text(session_id), first_id, last_id, len(selected)),
                )
                batch_id = int(cursor.lastrowid)
            self._conn.commit()
            return {"id": batch_id, "batch_key": batch_key, "session_id": session_id, "messages": selected}

        return await self._run_db(write)

    async def complete_chat_memory_batch(self, batch_id: int, summary_id: int = 0) -> None:
        def write() -> None:
            batch = self._conn.execute(
                "SELECT session_id, last_row_id FROM chat_memory_batches WHERE id = ?",
                (int(batch_id),),
            ).fetchone()
            if not batch:
                return
            self._conn.execute(
                "UPDATE chat_memory_batches SET status = 'completed', summary_id = ?, error = '', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (max(0, int(summary_id)), int(batch_id)),
            )
            self._conn.execute(
                """
                UPDATE chat_memory_sessions
                SET last_processed_row_id = MAX(last_processed_row_id, ?),
                    pending_since = CASE WHEN EXISTS(
                        SELECT 1 FROM chat_memory_messages m
                        WHERE m.session_id = chat_memory_sessions.session_id AND m.id > ?
                    ) THEN (
                        SELECT occurred_at FROM chat_memory_messages m
                        WHERE m.session_id = chat_memory_sessions.session_id AND m.id > ? ORDER BY m.id LIMIT 1
                    ) ELSE '' END,
                    last_completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (int(batch["last_row_id"]), int(batch["last_row_id"]), int(batch["last_row_id"]), str(batch["session_id"])),
            )
            self._conn.commit()

        await self._run_db(write)

    async def fail_chat_memory_batch(self, batch_id: int, error: str) -> None:
        def write() -> None:
            self._conn.execute(
                "UPDATE chat_memory_batches SET status = 'failed', error = ? WHERE id = ?",
                (self._text(error)[:1000], int(batch_id)),
            )
            self._conn.commit()

        await self._run_db(write)

    async def purge_completed_chat_memory_messages(self, keep_recent: int = 200) -> int:
        def write() -> int:
            cursor = self._conn.execute(
                """
                DELETE FROM chat_memory_messages
                WHERE id IN (
                    SELECT m.id FROM chat_memory_messages m
                    JOIN chat_memory_sessions s ON s.session_id = m.session_id
                    WHERE m.id <= s.last_processed_row_id
                    ORDER BY m.id DESC LIMIT -1 OFFSET ?
                )
                """,
                (max(0, int(keep_recent)),),
            )
            self._conn.commit()
            return max(0, int(cursor.rowcount or 0))

        return await self._run_db(write)
