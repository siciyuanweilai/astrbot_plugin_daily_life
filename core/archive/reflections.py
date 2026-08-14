import json
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

    def _replace_review_points_unlocked(
        self, date_str: str, kind: str, values: list[str]
    ) -> None:
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
        payload_points = self._get_review_points_unlocked(date_str, "payload")
        try:
            payload = json.loads(payload_points[0]) if payload_points else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        return DailyReviewRecord(
            date=row["date"],
            summary=row["summary"],
            memory_points=self._get_review_points_unlocked(date_str, "memory"),
            preference_points=prefs,
            sleep_debt_delta=float(row["sleep_debt_delta"] or 0.0),
            energy_carryover=float(row["energy_carryover"] or 0.0),
            life_events=events,
            payload=payload if isinstance(payload, dict) else {},
            created_at=row["created_at"],
        )

    async def save_daily_review(self, review: DailyReviewRecord) -> DailyReviewRecord:
        item = DailyReviewRecord.from_value(
            review.as_dict() if isinstance(review, DailyReviewRecord) else review
        )
        if not item:
            raise ValueError("每日复盘日期不能为空")

        def dbwork():
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
            self._replace_review_points_unlocked(
                item.date, "memory", item.memory_points
            )
            self._replace_review_points_unlocked(
                item.date,
                "payload",
                [
                    json.dumps(
                        item.payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ]
                if item.payload
                else [],
            )
            saved_prefs = self._upsert_preferences_unlocked(
                item.preference_points, item.date
            )
            self._conn.execute(
                "DELETE FROM review_preferences WHERE date = ?", (item.date,)
            )
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

        return await self._run_db(dbwork)

    async def get_daily_review(self, date_str: str) -> DailyReviewRecord | None:
        return await self._run_db(self._get_review_unlocked, date_str)

    async def get_recent_daily_reviews(self, limit: int = 7) -> list[DailyReviewRecord]:
        def dbwork():
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

        return await self._run_db(dbwork)

    def _upsert_preferences_unlocked(
        self,
        preferences: list[PreferenceRecord],
        date_str: str = "",
    ) -> list[PreferenceRecord]:
        saved: list[PreferenceRecord] = []
        saved_ids: set[int] = set()
        for pref in preferences:
            item = PreferenceRecord.from_value(pref, date=date_str)
            if not item:
                continue
            category = self._text(item.category) or "general"
            existing = None
            if int(item.id or 0) > 0:
                existing = self._conn.execute(
                    "SELECT * FROM preferences WHERE id = ? AND category = ?",
                    (int(item.id), category),
                ).fetchone()
            if existing is None:
                existing = self._conn.execute(
                    "SELECT * FROM preferences WHERE category = ? AND content = ?",
                    (category, self._text(item.content)),
                ).fetchone()
            if existing:
                weight = min(
                    5.0,
                    max(
                        max(float(existing["weight"] or 0.0), 0.0),
                        max(float(item.weight or 0.0), 0.1),
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE preferences
                    SET weight = ?, evidence = ?, last_seen = ?, source = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        weight,
                        self._text(item.evidence) or existing["evidence"],
                        self._text(item.last_seen)
                        or self._text(date_str)
                        or existing["last_seen"],
                        self._text(item.source) or existing["source"],
                        existing["id"],
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM preferences WHERE id = ?", (existing["id"],)
                ).fetchone()
            else:
                cursor = self._conn.execute(
                    """
                    INSERT INTO preferences(
                        category, content, weight, evidence, last_seen, source, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        category,
                        self._text(item.content),
                        max(float(item.weight or 0.0), 0.1),
                        self._text(item.evidence),
                        self._text(item.last_seen) or self._text(date_str),
                        self._text(item.source) or "learning",
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM preferences WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
            if row and int(row["id"] or 0) not in saved_ids:
                saved_ids.add(int(row["id"] or 0))
                saved.append(self._compose_preference(row))
        return saved

    def _merge_preferences_unlocked(
        self,
        groups: list[dict[str, Any]],
    ) -> dict[int, int]:
        merged_ids: dict[int, int] = {}
        claimed_ids: set[int] = set()
        for raw in groups:
            if not isinstance(raw, dict):
                continue
            try:
                canonical_id = int(raw.get("canonical_id") or 0)
            except (TypeError, ValueError):
                continue
            canonical = self._conn.execute(
                "SELECT * FROM preferences WHERE id = ?", (canonical_id,)
            ).fetchone()
            if canonical is None or canonical_id in claimed_ids:
                continue
            merge_ids = []
            for value in raw.get("merge_ids") or []:
                try:
                    preference_id = int(value)
                except (TypeError, ValueError):
                    continue
                if (
                    preference_id <= 0
                    or preference_id == canonical_id
                    or preference_id in claimed_ids
                    or preference_id in merge_ids
                ):
                    continue
                merge_ids.append(preference_id)
            if not merge_ids:
                continue
            placeholders = ",".join("?" for _ in merge_ids)
            rows = self._conn.execute(
                f"SELECT * FROM preferences WHERE id IN ({placeholders})",
                tuple(merge_ids),
            ).fetchall()
            rows = [
                row for row in rows if str(row["category"]) == str(canonical["category"])
            ]
            if not rows:
                continue
            valid_merge_ids = [int(row["id"]) for row in rows]
            latest = max(
                [canonical, *rows],
                key=lambda row: (str(row["last_seen"] or ""), int(row["id"] or 0)),
            )
            content = self._text(raw.get("content")) or canonical["content"]
            evidence = self._text(raw.get("evidence")) or latest["evidence"]
            weight = max(float(row["weight"] or 0.0) for row in [canonical, *rows])
            last_seen = max(str(row["last_seen"] or "") for row in [canonical, *rows])
            merge_placeholders = ",".join("?" for _ in valid_merge_ids)
            review_rows = self._conn.execute(
                f"""
                SELECT date, MIN(sort_order) AS sort_order
                FROM review_preferences
                WHERE preference_id IN ({merge_placeholders})
                GROUP BY date
                """,
                tuple(valid_merge_ids),
            ).fetchall()
            for review_row in review_rows:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO review_preferences(date, preference_id, sort_order)
                    VALUES (?, ?, ?)
                    """,
                    (review_row["date"], canonical_id, review_row["sort_order"]),
                )
            self._conn.execute(
                f"DELETE FROM review_preferences WHERE preference_id IN ({merge_placeholders})",
                tuple(valid_merge_ids),
            )
            source_ids = tuple(str(value) for value in valid_merge_ids)
            source_placeholders = ",".join("?" for _ in source_ids)
            self._conn.execute(
                f"""
                UPDATE long_term_memories
                SET status = 'superseded', updated_at = CURRENT_TIMESTAMP
                WHERE source_table = 'preferences' AND source_id IN ({source_placeholders})
                """,
                source_ids,
            )
            self._conn.execute(
                f"""
                UPDATE memory_evidence
                SET target_id = ?
                WHERE target_type = 'preference' AND target_id IN ({source_placeholders})
                """,
                (str(canonical_id), *source_ids),
            )
            self._conn.execute(
                f"""
                DELETE FROM memory_vectors
                WHERE target_type = 'preference' AND target_id IN ({source_placeholders})
                """,
                source_ids,
            )
            self._conn.execute(
                f"DELETE FROM preferences WHERE id IN ({merge_placeholders})",
                tuple(valid_merge_ids),
            )
            self._conn.execute(
                """
                UPDATE preferences
                SET content = ?, weight = ?, evidence = ?, last_seen = ?,
                    source = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    content,
                    weight,
                    evidence,
                    last_seen,
                    latest["source"],
                    canonical_id,
                ),
            )
            for preference_id in valid_merge_ids:
                merged_ids[preference_id] = canonical_id
            claimed_ids.add(canonical_id)
            claimed_ids.update(valid_merge_ids)
        return merged_ids

    async def upsert_preferences(
        self,
        preferences: list[PreferenceRecord],
        date_str: str = "",
    ) -> list[PreferenceRecord]:
        if not preferences:
            return []

        def dbwork():
            saved = self._upsert_preferences_unlocked(preferences, date_str)
            self._conn.commit()
            return saved

        return await self._run_db(dbwork)

    async def merge_preferences(
        self,
        groups: list[dict[str, Any]],
    ) -> dict[int, int]:
        if not groups:
            return {}

        def dbwork():
            merged = self._merge_preferences_unlocked(groups)
            self._conn.commit()
            return merged

        return await self._run_db(dbwork)

    async def get_preferences(
        self, limit: int = 20, category: str = ""
    ) -> list[PreferenceRecord]:
        def dbwork():
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

        return await self._run_db(dbwork)

    def _insert_life_event_unlocked(
        self, event: LifeEventRecord
    ) -> LifeEventRecord | None:
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
        row = self._conn.execute(
            "SELECT * FROM life_events WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return self._compose_life_event(row) if row else None

    async def add_life_event(self, event: LifeEventRecord) -> LifeEventRecord | None:
        def dbwork():
            saved = self._insert_life_event_unlocked(event)
            self._conn.commit()
            return saved

        return await self._run_db(dbwork)

    async def get_life_events(
        self, status: str = "", limit: int = 20
    ) -> list[LifeEventRecord]:
        def dbwork():
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

        return await self._run_db(dbwork)

    async def set_life_event_status(self, event_id: int, status: str) -> bool:
        def dbwork():
            cursor = self._conn.execute(
                "UPDATE life_events SET status = ? WHERE id = ?",
                (self._text(status) or "open", int(event_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0

        return await self._run_db(dbwork)

    async def close_stale_life_events(self, before_date: str) -> int:
        def dbwork() -> int:
            cursor = self._conn.execute(
                """
                UPDATE life_events
                SET status = 'expired'
                WHERE status = 'open' AND date <> '' AND date < ?
                """,
                (self._text(before_date),),
            )
            self._conn.commit()
            return max(0, int(cursor.rowcount or 0))

        return await self._run_db(dbwork)
