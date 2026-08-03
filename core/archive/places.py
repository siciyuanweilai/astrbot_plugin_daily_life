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
        self._replace_people_unlocked(
            "event_people", "event_id", cursor.lastrowid, event.people
        )
        return True

    async def add_events(self, date_str: str, events: list[EventRecord]):
        if not events:
            return

        def dbwork():
            changed = False
            for event in events:
                changed = self._insert_event_unlocked(event, date_str) or changed
            if changed:
                self._conn.commit()

        return await self._run_db(dbwork)

    async def get_recent_events(self, limit: int = 8) -> list[EventRecord]:
        def dbwork():
            sql = "SELECT * FROM events ORDER BY id DESC"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self._conn.execute(sql, params).fetchall()
            return [self._compose_event(row) for row in rows]

        return await self._run_db(dbwork)

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

        def dbwork():
            for place in places:
                name = place.name
                place_type = place.type
                hint = place.hint
                row = self._conn.execute(
                    """
                    SELECT type, hint, latitude, longitude, coordinate_source,
                           coordinate_updated_at, visits, first_seen
                    FROM places WHERE name = ?
                    """,
                    (name,),
                ).fetchone()
                if row:
                    stored_type = str(row["type"] or "place")
                    stored_hint = str(row["hint"] or "")
                    place_type = (
                        place_type
                        if stored_type == "place" and place_type
                        else stored_type
                    )
                    hint = hint or stored_hint
                    visits = int(row["visits"] or 0) + 1
                    first_seen = str(row["first_seen"] or date_str)
                    latitude = (
                        place.latitude
                        if place.latitude is not None
                        else row["latitude"]
                    )
                    longitude = (
                        place.longitude
                        if place.longitude is not None
                        else row["longitude"]
                    )
                    coordinate_source = place.coordinate_source or str(
                        row["coordinate_source"] or ""
                    )
                    coordinate_updated_at = place.coordinate_updated_at or str(
                        row["coordinate_updated_at"] or ""
                    )
                else:
                    visits = 1
                    first_seen = date_str
                    latitude = place.latitude
                    longitude = place.longitude
                    coordinate_source = place.coordinate_source
                    coordinate_updated_at = place.coordinate_updated_at
                self._conn.execute(
                    """
                    INSERT INTO places(
                        name, type, hint, latitude, longitude, coordinate_source,
                        coordinate_updated_at, visits, first_seen, last_seen, source
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        type = excluded.type,
                        hint = excluded.hint,
                        latitude = excluded.latitude,
                        longitude = excluded.longitude,
                        coordinate_source = excluded.coordinate_source,
                        coordinate_updated_at = excluded.coordinate_updated_at,
                        visits = excluded.visits,
                        first_seen = excluded.first_seen,
                        last_seen = excluded.last_seen,
                        source = excluded.source
                    """,
                    (
                        name,
                        place_type or "place",
                        hint,
                        latitude,
                        longitude,
                        coordinate_source,
                        coordinate_updated_at,
                        visits,
                        first_seen,
                        date_str,
                        source,
                    ),
                )
            self._conn.commit()

        return await self._run_db(dbwork)

    async def get_recent_places(self, limit: int = 10) -> list[PlaceRecord]:
        def dbwork():
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
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    coordinate_source=row["coordinate_source"],
                    coordinate_updated_at=row["coordinate_updated_at"],
                    visits=int(row["visits"] or 0),
                    first_seen=row["first_seen"],
                    last_seen=row["last_seen"],
                    source=row["source"],
                )
                for row in rows
            ]

        return await self._run_db(dbwork)
