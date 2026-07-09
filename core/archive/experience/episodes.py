import sqlite3

from ...models import LifeEpisodeRecord


class EpisodeArchiveMixin:
    def _compose_life_episode(self, row: sqlite3.Row) -> LifeEpisodeRecord:
        return LifeEpisodeRecord(
            id=int(row["id"] or 0),
            date=row["date"],
            title=self._text(row["title"]),
            summary=self._text(row["summary"]),
            kind=row["kind"],
            source=row["source"],
            related_people=self._get_people_unlocked("life_episode_people", "episode_id", row["id"]),
            related_places=self._get_ordered_texts_unlocked("life_episode_places", "episode_id", row["id"], "place"),
            impact=self._text(row["impact"]),
            confidence=float(row["confidence"] or 0.0),
            status=row["status"],
            protected=bool(row["protected"]),
            correction=self._text(row["correction"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def save_life_episode(self, episode: LifeEpisodeRecord) -> LifeEpisodeRecord:
        item = LifeEpisodeRecord.from_value(episode.as_dict() if isinstance(episode, LifeEpisodeRecord) else episode)
        if not item:
            raise ValueError("生活片段标题不能为空")
        async with self._lock:
            episode_id = int(item.id or 0)
            if not episode_id:
                existing = self._conn.execute(
                    """
                    SELECT id
                    FROM life_episodes
                    WHERE date = ? AND title = ? AND source = ? AND protected = 0
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self._text(item.date), self._text(item.title), self._text(item.source) or "daily"),
                ).fetchone()
                episode_id = int(existing["id"]) if existing else 0
            if episode_id:
                cursor = self._conn.execute(
                    """
                    UPDATE life_episodes
                    SET date = ?, title = ?, summary = ?, kind = ?, source = ?, impact = ?,
                        confidence = ?, status = ?, protected = ?, correction = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        self._text(item.date),
                        self._text(item.title),
                        self._text(item.summary),
                        self._text(item.kind) or "daily",
                        self._text(item.source) or "daily",
                        self._text(item.impact),
                        max(float(item.confidence or 0.0), 0.0),
                        self._text(item.status) or "open",
                        self._flag(item.protected),
                        self._text(item.correction),
                        episode_id,
                    ),
                )
                if cursor.rowcount <= 0:
                    episode_id = 0
            if not episode_id:
                cursor = self._conn.execute(
                    """
                    INSERT INTO life_episodes(
                        date, title, summary, kind, source, impact, confidence,
                        status, protected, correction, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        self._text(item.date),
                        self._text(item.title),
                        self._text(item.summary),
                        self._text(item.kind) or "daily",
                        self._text(item.source) or "daily",
                        self._text(item.impact),
                        max(float(item.confidence or 0.0), 0.0),
                        self._text(item.status) or "open",
                        self._flag(item.protected),
                        self._text(item.correction),
                    ),
                )
                episode_id = int(cursor.lastrowid)
            self._replace_people_unlocked("life_episode_people", "episode_id", episode_id, item.related_people)
            self._replace_texts_unlocked("life_episode_places", "episode_id", episode_id, "place", item.related_places)
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM life_episodes WHERE id = ?", (episode_id,)).fetchone()
            return self._compose_life_episode(row)
    async def get_life_episodes(self, limit: int = 20, status: str = "") -> list[LifeEpisodeRecord]:
        async with self._lock:
            sql = "SELECT * FROM life_episodes"
            params: list[Any] = []
            if status:
                sql += " WHERE status = ?"
                params.append(self._text(status))
            sql += " ORDER BY date DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_life_episode(row) for row in rows]
    async def correct_life_episode(self, episode_id: int, correction: str, *, protected: bool = True) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE life_episodes
                SET correction = ?, protected = ?, status = 'corrected', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (self._text(correction), self._flag(protected), int(episode_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    async def set_life_episode_protected(self, episode_id: int, protected: bool) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                "UPDATE life_episodes SET protected = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (self._flag(protected), int(episode_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
