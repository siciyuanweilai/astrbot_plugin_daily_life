import json
import time
from typing import Any


class ClipArchiveMixin:
    @staticmethod
    def _json_dict_unlocked(value: Any) -> dict:
        text = str(value or "").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _video_lines_unlocked(self, table: str, column: str, insight_id: int) -> list[str]:
        rows = self._conn.execute(
            f"SELECT {column} FROM {table} WHERE insight_id = ? ORDER BY sort_order",
            (int(insight_id),),
        ).fetchall()
        return [self._text(row[column]) for row in rows if self._text(row[column])]

    def _replace_video_lines_unlocked(self, table: str, column: str, insight_id: int, values: list[str]) -> None:
        self._conn.execute(f"DELETE FROM {table} WHERE insight_id = ?", (int(insight_id),))
        for index, value in enumerate(values):
            text = self._text(value)
            if text:
                self._conn.execute(
                    f"INSERT INTO {table}(insight_id, sort_order, {column}) VALUES (?, ?, ?)",
                    (int(insight_id), index, text),
                )

    def _compose_video_insight_unlocked(self, row: Any) -> dict:
        insight_id = int(row["id"] or 0)
        return {
            "clip": {
                "scope": row["scope"],
                "message_id": row["message_id"],
                "source": row["source"],
                "file_id": row["file_id"],
                "name": row["name"],
                "origin": row["origin"],
                "text": row["text"],
                "created_at": float(row["created_at"] or time.time()),
            },
            "summary": self._text(row["summary"]),
            "transcript": self._text(row["transcript"]) if "transcript" in row.keys() else "",
            "transcript_source": self._text(row["transcript_source"]) if "transcript_source" in row.keys() else "",
            "note": self._text(row["note"]) if "note" in row.keys() else "",
            "note_source": self._text(row["note_source"]) if "note_source" in row.keys() else "",
            "metadata": self._json_dict_unlocked(row["metadata"]) if "metadata" in row.keys() else {},
            "details": self._video_lines_unlocked("video_insight_details", "detail_text", insight_id),
            "frame_notes": self._video_lines_unlocked("video_insight_frames", "frame_text", insight_id),
            "source_note": self._text(row["source_note"]),
            "status": self._text(row["status"]) or "ready",
            "error": self._text(row["error"]),
            "updated_at": float(row["updated_at"] or time.time()),
        }

    def _delete_video_insight_ids_unlocked(self, insight_ids: list[int]) -> int:
        ids = [int(item) for item in insight_ids if int(item or 0) > 0]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = self._conn.execute(
            f"DELETE FROM video_insights WHERE id IN ({placeholders})",
            tuple(ids),
        )
        return max(int(cursor.rowcount or 0), 0)

    def _prune_video_insights_unlocked(self, ttl_seconds: int = 7200, max_items: int = 60) -> int:
        now = time.time()
        max_items = max(1, int(max_items or 60))
        deleted = 0
        expired = [
            int(row["id"])
            for row in self._conn.execute(
                "SELECT id FROM video_insights WHERE expires_at > 0 AND expires_at < ?",
                (now,),
            ).fetchall()
        ]
        deleted += self._delete_video_insight_ids_unlocked(expired)

        rows = self._conn.execute(
            "SELECT id FROM video_insights ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        overflow = [int(row["id"]) for row in rows[max_items:]]
        deleted += self._delete_video_insight_ids_unlocked(overflow)
        return deleted

    async def prune_video_insights(self, ttl_seconds: int = 7200, max_items: int = 60) -> int:
        async with self._lock:
            deleted = self._prune_video_insights_unlocked(ttl_seconds, max_items)
            self._conn.commit()
            return deleted

    async def upsert_video_insight(
        self,
        item: dict,
        *,
        ttl_seconds: int = 7200,
        max_items: int = 60,
    ) -> dict | None:
        clip = item.get("clip") if isinstance(item, dict) else {}
        if not isinstance(clip, dict):
            return None
        cache_key = self._text(item.get("cache_key")) or self._text(item.get("key"))
        if not cache_key:
            return None
        now = time.time()
        updated_at = float(item.get("updated_at") or now)
        created_at = float(clip.get("created_at") or now)
        expires_at = updated_at + max(60, int(ttl_seconds or 7200))
        details = list(item.get("details") or [])
        frame_notes = list(item.get("frame_notes") or [])
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO video_insights(
                    cache_key, scope, message_id, source, file_id, name, origin, text,
                    summary, transcript, transcript_source, note, note_source, metadata,
                    source_note, status, error, created_at, updated_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    scope = excluded.scope,
                    message_id = excluded.message_id,
                    source = excluded.source,
                    file_id = excluded.file_id,
                    name = excluded.name,
                    origin = excluded.origin,
                    text = excluded.text,
                    summary = excluded.summary,
                    transcript = excluded.transcript,
                    transcript_source = excluded.transcript_source,
                    note = excluded.note,
                    note_source = excluded.note_source,
                    metadata = excluded.metadata,
                    source_note = excluded.source_note,
                    status = excluded.status,
                    error = excluded.error,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    cache_key,
                    self._text(clip.get("scope")),
                    self._text(clip.get("message_id")),
                    self._text(clip.get("source")),
                    self._text(clip.get("file_id")),
                    self._text(clip.get("name")),
                    self._text(clip.get("origin")) or "current",
                    self._text(clip.get("text")),
                    self._text(item.get("summary")),
                    self._text(item.get("transcript")),
                    self._text(item.get("transcript_source")),
                    self._text(item.get("note")),
                    self._text(item.get("note_source")),
                    json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                    self._text(item.get("source_note")),
                    self._text(item.get("status")) or "ready",
                    self._text(item.get("error")),
                    created_at,
                    updated_at,
                    expires_at,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM video_insights WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if not row:
                self._conn.commit()
                return None
            insight_id = int(row["id"])
            self._replace_video_lines_unlocked("video_insight_details", "detail_text", insight_id, details)
            self._replace_video_lines_unlocked("video_insight_frames", "frame_text", insight_id, frame_notes)
            self._prune_video_insights_unlocked(ttl_seconds, max_items)
            row = self._conn.execute(
                "SELECT * FROM video_insights WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            self._conn.commit()
            return self._compose_video_insight_unlocked(row) if row else None

    async def get_video_insight(
        self,
        cache_key: str,
        *,
        ttl_seconds: int = 7200,
        max_items: int = 60,
    ) -> dict | None:
        key = self._text(cache_key)
        if not key:
            return None
        async with self._lock:
            self._prune_video_insights_unlocked(ttl_seconds, max_items)
            row = self._conn.execute(
                "SELECT * FROM video_insights WHERE cache_key = ?",
                (key,),
            ).fetchone()
            self._conn.commit()
            return self._compose_video_insight_unlocked(row) if row else None

    async def get_recent_video_insights(
        self,
        scope: str = "",
        *,
        limit: int = 3,
        ttl_seconds: int = 7200,
        max_items: int = 60,
    ) -> list[dict]:
        scope = self._text(scope)
        limit = max(1, int(limit or 3))
        async with self._lock:
            self._prune_video_insights_unlocked(ttl_seconds, max_items)
            sql = "SELECT * FROM video_insights"
            params: list[Any] = []
            if scope:
                sql += " WHERE scope = ?"
                params.append(scope)
            sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            self._conn.commit()
            return [self._compose_video_insight_unlocked(row) for row in rows]

    async def delete_video_insights_for_message(self, scope: str, message_id: str) -> int:
        scope = self._text(scope)
        message_id = self._text(message_id)
        if not scope or not message_id:
            return 0
        async with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM video_insights WHERE scope = ? AND message_id = ?",
                (scope, message_id),
            ).fetchall()
            deleted = self._delete_video_insight_ids_unlocked([int(row["id"]) for row in rows])
            self._conn.commit()
            return deleted
