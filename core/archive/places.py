import sqlite3
from typing import Any

from ..models import EventRecord, PlaceRecord


class PlaceArchiveMixin:
    def _insert_event_unlocked(self, event: EventRecord, date_str: str) -> bool:
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO events(date, summary, place, importance, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.date,
                event.summary,
                event.place,
                event.importance,
                event.source,
            ),
        )
        if cursor.rowcount <= 0:
            return False
        self._replace_people_unlocked("event_people", "event_id", cursor.lastrowid, event.people)
        return True

    async def add_events(self, date_str: str, events: list[EventRecord]):
        if not events:
            return
        changed = False
        async with self._lock:
            for event in events:
                changed = self._insert_event_unlocked(event, date_str) or changed
            if changed:
                self._conn.commit()

    async def get_recent_events(self, limit: int = 8) -> list[EventRecord]:
        async with self._lock:
            sql = "SELECT * FROM events ORDER BY id DESC"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self._conn.execute(sql, params).fetchall()
            return [self._compose_event(row) for row in rows]

    def _compose_event(self, row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            date=row["date"],
            summary=row["summary"],
            people=self._get_people_unlocked("event_people", "event_id", row["id"]),
            place=row["place"],
            importance=row["importance"],
            source=row["source"],
        )

    async def touch_places(self, date_str: str, places: list, source: str = "daily"):
        if not places:
            return
        async with self._lock:
            for place in places:
                name = place.name
                place_type = place.type
                hint = place.hint
                row = self._conn.execute(
                    "SELECT type, hint, visits, first_seen FROM places WHERE name = ?",
                    (name,),
                ).fetchone()
                if row:
                    stored_type = str(row["type"] or "place")
                    stored_hint = str(row["hint"] or "")
                    place_type = place_type if stored_type == "place" and place_type else stored_type
                    hint = hint or stored_hint
                    visits = int(row["visits"] or 0) + 1
                    first_seen = str(row["first_seen"] or date_str)
                else:
                    visits = 1
                    first_seen = date_str
                self._conn.execute(
                    """
                    INSERT INTO places(name, type, hint, visits, first_seen, last_seen, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        type = excluded.type,
                        hint = excluded.hint,
                        visits = excluded.visits,
                        first_seen = excluded.first_seen,
                        last_seen = excluded.last_seen,
                        source = excluded.source
                    """,
                    (name, place_type or "place", hint, visits, first_seen, date_str, source),
                )
            self._conn.commit()

    async def get_recent_places(self, limit: int = 10) -> list[PlaceRecord]:
        async with self._lock:
            sql = "SELECT * FROM places ORDER BY last_seen DESC, visits DESC"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self._conn.execute(sql, params).fetchall()
            return [
                PlaceRecord(
                    name=row["name"],
                    type=row["type"],
                    hint=row["hint"],
                    visits=int(row["visits"] or 0),
                    first_seen=row["first_seen"],
                    last_seen=row["last_seen"],
                    source=row["source"],
                )
                for row in rows
            ]
