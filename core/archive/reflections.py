import sqlite3
from typing import Any

from ..models import DailyReviewRecord, LifeEventRecord, PreferenceRecord


class LifecycleArchiveMixin:
    def _compose_preference(self, row: sqlite3.Row) -> PreferenceRecord:
        return PreferenceRecord(
            id=int(row["id"] or 0),
            category=row["category"],
            content=row["content"],
            weight=float(row["weight"] or 0.0),
            evidence=row["evidence"],
            last_seen=row["last_seen"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _compose_life_event(self, row: sqlite3.Row) -> LifeEventRecord:
        return LifeEventRecord(
            id=int(row["id"] or 0),
            date=row["date"],
            title=row["title"],
            detail=row["detail"],
            effect=row["effect"],
            status=row["status"],
            source=row["source"],
            created_at=row["created_at"],
        )

    def _get_review_points_unlocked(self, date_str: str, kind: str) -> list[str]:
        return [
            row["content"]
            for row in self._conn.execute(
                """
                SELECT content FROM daily_review_points
                WHERE date = ? AND kind = ?
                ORDER BY sort_order
                """,
                (date_str, kind),
            ).fetchall()
            if row["content"]
        ]

    def _replace_review_points_unlocked(self, date_str: str, kind: str, values: list[str]) -> None:
        self._conn.execute(
            "DELETE FROM daily_review_points WHERE date = ? AND kind = ?",
            (date_str, kind),
        )
        for idx, value in enumerate(values):
            text = self._text(value)
            if text:
                self._conn.execute(
                    """
                    INSERT INTO daily_review_points(date, sort_order, kind, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (date_str, idx, self._text(kind), text),
                )

    def _get_review_unlocked(self, date_str: str) -> DailyReviewRecord | None:
        row = self._conn.execute(
            "SELECT * FROM daily_reviews WHERE date = ?",
            (date_str,),
        ).fetchone()
        if not row:
            return None
        prefs = [
            self._compose_preference(item)
            for item in self._conn.execute(
                """
                SELECT p.*
                FROM review_preferences rp
                JOIN preferences p ON p.id = rp.preference_id
                WHERE rp.date = ?
                ORDER BY rp.sort_order
                """,
                (date_str,),
            ).fetchall()
        ]
        events = [
            self._compose_life_event(item)
            for item in self._conn.execute(
                "SELECT * FROM life_events WHERE date = ? AND source = 'daily_review' ORDER BY id",
                (date_str,),
            ).fetchall()
        ]
        return DailyReviewRecord(
            date=row["date"],
            summary=row["summary"],
            memory_points=self._get_review_points_unlocked(date_str, "memory"),
            preference_points=prefs,
            sleep_debt_delta=float(row["sleep_debt_delta"] or 0.0),
            energy_carryover=float(row["energy_carryover"] or 0.0),
            life_events=events,
            created_at=row["created_at"],
        )

    async def save_daily_review(self, review: DailyReviewRecord) -> DailyReviewRecord:
        item = DailyReviewRecord.from_value(review.as_dict() if isinstance(review, DailyReviewRecord) else review)
        if not item:
            raise ValueError("每日复盘日期不能为空")
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO daily_reviews(date, summary, sleep_debt_delta, energy_carryover, created_at)
                VALUES (?, ?, ?, ?, COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP))
                ON CONFLICT(date) DO UPDATE SET
                    summary = excluded.summary,
                    sleep_debt_delta = excluded.sleep_debt_delta,
                    energy_carryover = excluded.energy_carryover
                """,
                (
                    item.date,
                    self._text(item.summary),
                    float(item.sleep_debt_delta or 0.0),
                    float(item.energy_carryover or 0.0),
                    self._text(item.created_at),
                ),
            )
            self._replace_review_points_unlocked(item.date, "memory", item.memory_points)
            saved_prefs = self._upsert_preferences_unlocked(item.preference_points, item.date)
            self._conn.execute("DELETE FROM review_preferences WHERE date = ?", (item.date,))
            for idx, pref in enumerate(saved_prefs):
                self._conn.execute(
                    """
                    INSERT INTO review_preferences(date, preference_id, sort_order)
                    VALUES (?, ?, ?)
                    """,
                    (item.date, pref.id, idx),
                )
            self._conn.execute(
                "DELETE FROM life_events WHERE date = ? AND source = 'daily_review'",
                (item.date,),
            )
            for event in item.life_events:
                event.date = event.date or item.date
                event.source = "daily_review"
                self._insert_life_event_unlocked(event)
            self._conn.commit()
            return self._get_review_unlocked(item.date) or item

    async def get_daily_review(self, date_str: str) -> DailyReviewRecord | None:
        async with self._lock:
            return self._get_review_unlocked(date_str)

    async def get_recent_daily_reviews(self, limit: int = 7) -> list[DailyReviewRecord]:
        async with self._lock:
            sql = "SELECT date FROM daily_reviews ORDER BY date DESC"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self._conn.execute(sql, params).fetchall()
            return [
                review
                for review in (self._get_review_unlocked(row["date"]) for row in rows)
                if review is not None
            ]

    def _upsert_preferences_unlocked(
        self,
        preferences: list[PreferenceRecord],
        date_str: str = "",
    ) -> list[PreferenceRecord]:
        saved: list[PreferenceRecord] = []
        for pref in preferences:
            item = PreferenceRecord.from_value(pref, date=date_str)
            if not item:
                continue
            existing = self._conn.execute(
                "SELECT * FROM preferences WHERE category = ? AND content = ?",
                (self._text(item.category) or "general", self._text(item.content)),
            ).fetchone()
            if existing:
                weight = min(5.0, max(float(existing["weight"] or 0.0), 0.0) + max(float(item.weight or 0.0), 0.1))
                self._conn.execute(
                    """
                    UPDATE preferences
                    SET weight = ?, evidence = ?, last_seen = ?, source = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        weight,
                        self._text(item.evidence) or existing["evidence"],
                        self._text(item.last_seen) or self._text(date_str) or existing["last_seen"],
                        self._text(item.source) or existing["source"],
                        existing["id"],
                    ),
                )
                row = self._conn.execute("SELECT * FROM preferences WHERE id = ?", (existing["id"],)).fetchone()
            else:
                cursor = self._conn.execute(
                    """
                    INSERT INTO preferences(
                        category, content, weight, evidence, last_seen, source, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        self._text(item.category) or "general",
                        self._text(item.content),
                        max(float(item.weight or 0.0), 0.1),
                        self._text(item.evidence),
                        self._text(item.last_seen) or self._text(date_str),
                        self._text(item.source) or "learning",
                    ),
                )
                row = self._conn.execute("SELECT * FROM preferences WHERE id = ?", (cursor.lastrowid,)).fetchone()
            if row:
                saved.append(self._compose_preference(row))
        return saved

    async def upsert_preferences(
        self,
        preferences: list[PreferenceRecord],
        date_str: str = "",
    ) -> list[PreferenceRecord]:
        if not preferences:
            return []
        async with self._lock:
            saved = self._upsert_preferences_unlocked(preferences, date_str)
            self._conn.commit()
            return saved

    async def get_preferences(self, limit: int = 20, category: str = "") -> list[PreferenceRecord]:
        async with self._lock:
            sql = "SELECT * FROM preferences"
            params: list[Any] = []
            if category:
                sql += " WHERE category = ?"
                params.append(self._text(category))
            sql += " ORDER BY weight DESC, last_seen DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_preference(row) for row in rows]

    def _insert_life_event_unlocked(self, event: LifeEventRecord) -> LifeEventRecord | None:
        item = LifeEventRecord.from_value(event)
        if not item:
            return None
        cursor = self._conn.execute(
            """
            INSERT INTO life_events(date, title, detail, effect, status, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                self._text(item.date),
                self._text(item.title),
                self._text(item.detail),
                self._text(item.effect),
                self._text(item.status) or "open",
                self._text(item.source) or "life_event",
            ),
        )
        row = self._conn.execute("SELECT * FROM life_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._compose_life_event(row) if row else None

    async def add_life_event(self, event: LifeEventRecord) -> LifeEventRecord | None:
        async with self._lock:
            saved = self._insert_life_event_unlocked(event)
            self._conn.commit()
            return saved

    async def get_life_events(self, status: str = "", limit: int = 20) -> list[LifeEventRecord]:
        async with self._lock:
            sql = "SELECT * FROM life_events"
            params: list[Any] = []
            if status:
                sql += " WHERE status = ?"
                params.append(self._text(status))
            sql += " ORDER BY date DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_life_event(row) for row in rows]

    async def set_life_event_status(self, event_id: int, status: str) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                "UPDATE life_events SET status = ? WHERE id = ?",
                (self._text(status) or "open", int(event_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
