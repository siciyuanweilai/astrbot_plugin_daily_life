import json
import sqlite3
from ..models import DayRecord, EventRecord, LifeState, PlaceRecord, TimelineItem, WeatherInfo


class DayArchiveMixin:
    @staticmethod
    def _load_json_object(value: object) -> dict:
        if isinstance(value, dict):
            return value
        text = str(value or "").strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _get_day_unlocked(self, date_str: str) -> DayRecord | None:
        row = self._conn.execute(
            "SELECT * FROM days WHERE date = ?",
            (date_str,),
        ).fetchone()
        if not row:
            return None

        meta = {
            "theme": row["meta_theme"],
            "mood": row["meta_mood"],
            "style": row["meta_style"],
            "hair": row["meta_hair"],
        }
        meta = {key: value for key, value in meta.items() if value}
        meta.update(
            {
                item["key"]: item["value"]
                for item in self._conn.execute(
                    "SELECT key, value FROM day_meta WHERE date = ? ORDER BY key",
                    (date_str,),
                ).fetchall()
                if item["key"] and item["value"]
            }
        )

        timeline = [
            TimelineItem(time=item["time"], activity=item["activity"], status=item["status"])
            for item in self._conn.execute(
                "SELECT time, activity, status FROM timelines WHERE date = ? ORDER BY sort_order",
                (date_str,),
            ).fetchall()
        ]

        outfit_history = {
            item["period"]: item["outfit"]
            for item in self._conn.execute(
                "SELECT period, outfit FROM outfit_history WHERE date = ? ORDER BY period",
                (date_str,),
            ).fetchall()
            if item["period"]
        }

        state = self._get_state_unlocked(date_str)

        state_log = [
            item["entry"]
            for item in self._conn.execute(
                "SELECT entry FROM state_logs WHERE date = ? ORDER BY sort_order",
                (date_str,),
            ).fetchall()
            if item["entry"]
        ]

        places = [
            PlaceRecord(name=item["name"], type=item["type"], hint=item["hint"])
            for item in self._conn.execute(
                "SELECT name, type, hint FROM day_places WHERE date = ? ORDER BY sort_order",
                (date_str,),
            ).fetchall()
            if item["name"]
        ]

        events = self._get_day_events_unlocked(date_str)

        return DayRecord(
            date=date_str,
            outfit=row["outfit"],
            timeline=timeline,
            places=places,
            new_events=events,
            weather=row["weather"],
            weather_info=self._compose_weather_info(row),
            weather_last_update=int(row["weather_last_update"] or 0),
            time_period=row["time_period"],
            meta=meta,
            outfit_history=outfit_history,
            memo=row["memo"],
            state=state,
            state_log=state_log,
        )
    def _compose_weather_info(self, row: sqlite3.Row) -> WeatherInfo:
        keys = (
            "weather_temp",
            "weather_condition",
            "weather_temp_desc",
            "weather_outfit_hint",
            "weather_activity_hint",
            "weather_is_hot",
            "weather_is_warm",
            "weather_is_cool",
            "weather_is_cold",
            "weather_is_rainy",
            "weather_is_sunny",
            "weather_is_cloudy",
            "weather_is_foggy",
        )
        if not any(row[key] for key in keys):
            return WeatherInfo(raw=row["weather"])
        return WeatherInfo(
            raw=row["weather"],
            temp=row["weather_temp"],
            condition=row["weather_condition"],
            is_hot=bool(row["weather_is_hot"]),
            is_warm=bool(row["weather_is_warm"]),
            is_cool=bool(row["weather_is_cool"]),
            is_cold=bool(row["weather_is_cold"]),
            is_rainy=bool(row["weather_is_rainy"]),
            is_sunny=bool(row["weather_is_sunny"]),
            is_cloudy=bool(row["weather_is_cloudy"]),
            is_foggy=bool(row["weather_is_foggy"]),
            outfit_hint=row["weather_outfit_hint"],
            activity_hint=row["weather_activity_hint"],
            temp_desc=row["weather_temp_desc"],
        )
    def _get_state_unlocked(self, date_str: str) -> LifeState | None:
        row = self._conn.execute("SELECT * FROM states WHERE date = ?", (date_str,)).fetchone()
        if not row:
            return None
        return LifeState.from_value(
            {
                "energy": row["energy"],
                "mood": row["mood"],
                "mood_score": row["mood_score"],
                "busyness": row["busyness"],
                "social": row["social"],
                "stress": row["stress"],
                "focus": row["focus"],
                "sleepiness": row["sleepiness"],
                "outgoing": row["outgoing"],
                "emotional_stability": row["emotional_stability"],
                "interaction_capacity": row["interaction_capacity"],
                "boredom": row["boredom"],
                "fishing": row["fishing"],
                "attention_openness": row["attention_openness"],
                "watch_state": row["watch_state"],
                "interrupt_level": row["interrupt_level"],
                "interrupt_reason": row["interrupt_reason"],
                "sleep": {
                    "quality": row["sleep_quality"],
                    "depth": row["sleep_depth"],
                    "summary": row["sleep_summary"],
                },
                "physiological_rhythm": self._load_json_object(row["physiological_rhythm"]),
                "summary": row["summary"],
                "updated_at": row["updated_at"],
                "source": row["source"],
            }
        )
    def _get_day_events_unlocked(self, date_str: str) -> list[EventRecord]:
        rows = self._conn.execute(
            "SELECT * FROM day_events WHERE date = ? ORDER BY sort_order",
            (date_str,),
        ).fetchall()
        return [self._compose_day_event(row) for row in rows]
    def _compose_day_event(self, row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            date=row["date"],
            summary=row["summary"],
            people=self._get_people_unlocked("day_event_people", "day_event_id", row["id"]),
            place=row["place"],
            importance=row["importance"],
            source=row["source"],
        )
    def _set_day_unlocked(self, day: DayRecord) -> None:
        weather_info = day.weather_info
        meta = day.meta
        self._conn.execute(
            """
            INSERT INTO days(
                date, outfit, weather, time_period, memo, weather_last_update,
                weather_temp, weather_condition, weather_temp_desc, weather_outfit_hint, weather_activity_hint,
                weather_is_hot, weather_is_warm, weather_is_cool, weather_is_cold,
                weather_is_rainy, weather_is_sunny, weather_is_cloudy, weather_is_foggy,
                meta_theme, meta_mood, meta_style, meta_hair
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                outfit = excluded.outfit,
                weather = excluded.weather,
                time_period = excluded.time_period,
                memo = excluded.memo,
                weather_last_update = excluded.weather_last_update,
                weather_temp = excluded.weather_temp,
                weather_condition = excluded.weather_condition,
                weather_temp_desc = excluded.weather_temp_desc,
                weather_outfit_hint = excluded.weather_outfit_hint,
                weather_activity_hint = excluded.weather_activity_hint,
                weather_is_hot = excluded.weather_is_hot,
                weather_is_warm = excluded.weather_is_warm,
                weather_is_cool = excluded.weather_is_cool,
                weather_is_cold = excluded.weather_is_cold,
                weather_is_rainy = excluded.weather_is_rainy,
                weather_is_sunny = excluded.weather_is_sunny,
                weather_is_cloudy = excluded.weather_is_cloudy,
                weather_is_foggy = excluded.weather_is_foggy,
                meta_theme = excluded.meta_theme,
                meta_mood = excluded.meta_mood,
                meta_style = excluded.meta_style,
                meta_hair = excluded.meta_hair
            """,
            (
                day.date,
                day.outfit,
                day.weather or weather_info.raw,
                day.time_period,
                day.memo,
                self._int(day.weather_last_update, 0),
                weather_info.temp,
                weather_info.condition,
                weather_info.temp_desc,
                weather_info.outfit_hint,
                weather_info.activity_hint,
                self._flag(weather_info.is_hot),
                self._flag(weather_info.is_warm),
                self._flag(weather_info.is_cool),
                self._flag(weather_info.is_cold),
                self._flag(weather_info.is_rainy),
                self._flag(weather_info.is_sunny),
                self._flag(weather_info.is_cloudy),
                self._flag(weather_info.is_foggy),
                self._text(meta.get("theme")),
                self._text(meta.get("mood")),
                self._text(meta.get("style")),
                self._text(meta.get("hair")),
            ),
        )
        self._replace_timeline_unlocked(day.date, day.timeline)
        self._replace_outfit_history_unlocked(day.date, day.outfit_history)
        self._replace_day_meta_unlocked(day.date, day.meta)
        self._replace_state_unlocked(day.date, day.state)
        self._replace_state_log_unlocked(day.date, day.state_log)
        self._replace_day_places_unlocked(day.date, day.places)
        self._replace_day_events_unlocked(day.date, day.new_events)
    def _replace_timeline_unlocked(self, date_str: str, timeline: list[TimelineItem]) -> None:
        self._conn.execute("DELETE FROM timelines WHERE date = ?", (date_str,))
        for idx, item in enumerate(timeline):
            self._conn.execute(
                "INSERT INTO timelines(date, sort_order, time, activity, status) VALUES (?, ?, ?, ?, ?)",
                (
                    date_str,
                    idx,
                    item.time,
                    item.activity,
                    item.status,
                ),
            )
    def _replace_outfit_history_unlocked(self, date_str: str, history: dict[str, str]) -> None:
        self._conn.execute("DELETE FROM outfit_history WHERE date = ?", (date_str,))
        for period, outfit in history.items():
            period_text = self._text(period)
            if not period_text:
                continue
            self._conn.execute(
                "INSERT INTO outfit_history(date, period, outfit) VALUES (?, ?, ?)",
                (date_str, period_text, self._text(outfit)),
            )
    def _replace_day_meta_unlocked(self, date_str: str, meta: dict[str, str]) -> None:
        self._conn.execute("DELETE FROM day_meta WHERE date = ?", (date_str,))
        for key, value in sorted((meta or {}).items()):
            key_text = self._text(key)
            value_text = self._text(value)
            if not key_text or not value_text:
                continue
            self._conn.execute(
                "INSERT INTO day_meta(date, key, value) VALUES (?, ?, ?)",
                (date_str, key_text, value_text),
            )
    def _replace_state_unlocked(self, date_str: str, state: LifeState | None) -> None:
        self._conn.execute("DELETE FROM states WHERE date = ?", (date_str,))
        if state is None:
            return
        self._conn.execute(
            """
            INSERT INTO states(
                date, energy, mood, mood_score, busyness, social, stress, focus,
                sleepiness, outgoing, emotional_stability, interaction_capacity,
                boredom, fishing, attention_openness, watch_state, interrupt_level, interrupt_reason,
                sleep_quality, sleep_depth, sleep_summary, physiological_rhythm,
                summary, updated_at, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date_str,
                state.energy,
                state.mood,
                state.mood_score,
                state.busyness,
                state.social,
                state.stress,
                state.focus,
                state.sleepiness,
                state.outgoing,
                state.emotional_stability,
                state.interaction_capacity,
                state.boredom,
                state.fishing,
                state.attention_openness,
                state.watch_state,
                state.interrupt_level,
                state.interrupt_reason,
                state.sleep.quality,
                state.sleep.depth,
                state.sleep.summary,
                json.dumps(state.physiological_rhythm.as_dict(), ensure_ascii=False, separators=(",", ":")),
                state.summary,
                state.updated_at,
                state.source,
            ),
        )
    def _replace_state_log_unlocked(self, date_str: str, logs: list[str]) -> None:
        self._conn.execute("DELETE FROM state_logs WHERE date = ?", (date_str,))
        for idx, entry in enumerate(logs[-10:]):
            self._conn.execute(
                "INSERT INTO state_logs(date, sort_order, entry) VALUES (?, ?, ?)",
                (date_str, idx, self._text(entry)),
            )
    def _replace_day_places_unlocked(self, date_str: str, places: list[PlaceRecord]) -> None:
        self._conn.execute("DELETE FROM day_places WHERE date = ?", (date_str,))
        for idx, place in enumerate(places):
            self._conn.execute(
                "INSERT INTO day_places(date, sort_order, name, type, hint) VALUES (?, ?, ?, ?, ?)",
                (date_str, idx, place.name, place.type, place.hint),
            )
    def _replace_day_events_unlocked(self, date_str: str, events: list[EventRecord]) -> None:
        old_ids = [
            row["id"]
            for row in self._conn.execute("SELECT id FROM day_events WHERE date = ?", (date_str,)).fetchall()
        ]
        if old_ids:
            self._conn.executemany("DELETE FROM day_events WHERE id = ?", [(event_id,) for event_id in old_ids])
        for idx, event in enumerate(events):
            cursor = self._conn.execute(
                """
                INSERT INTO day_events(date, sort_order, summary, place, importance, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    date_str,
                    idx,
                    event.summary,
                    event.place,
                    event.importance,
                    event.source,
                ),
            )
            self._replace_people_unlocked(
                "day_event_people",
                "day_event_id",
                cursor.lastrowid,
                event.people,
            )
    async def get_day(self, date_str: str) -> DayRecord | None:
        def read() -> DayRecord | None:
            return self._get_day_unlocked(date_str)

        return await self._run_db(read)

    async def get_future_memo_days(self, after_date: str, limit: int = 8) -> list[DayRecord]:
        max_rows = max(0, int(limit or 0))
        if max_rows <= 0:
            return []

        def read() -> list[DayRecord]:
            rows = self._conn.execute(
                """
                SELECT date FROM days
                WHERE date > ? AND TRIM(COALESCE(memo, '')) <> ''
                ORDER BY date ASC
                LIMIT ?
                """,
                (after_date, max_rows),
            ).fetchall()
            days = []
            for row in rows:
                day = self._get_day_unlocked(row["date"])
                if day:
                    days.append(day)
            return days

        return await self._run_db(read)

    async def save_day(self, day: DayRecord):
        def write() -> None:
            self._set_day_unlocked(day)
            self._conn.commit()

        await self._run_db(write)

    async def replace_day_timeline(self, date_str: str, timeline: list[TimelineItem]) -> DayRecord | None:
        def write() -> DayRecord | None:
            day = self._get_day_unlocked(date_str)
            if not day:
                return None
            day.timeline = [TimelineItem.from_value(item) for item in timeline]
            self._set_day_unlocked(day)
            self._conn.commit()
            return self._get_day_unlocked(date_str)

        return await self._run_db(write)

    async def delete_day(self, date_str: str):
        def write() -> None:
            self._conn.execute("DELETE FROM days WHERE date = ?", (date_str,))
            self._conn.commit()

        await self._run_db(write)

    async def set_memo(self, date_str: str, memo_text: str):
        def write() -> None:
            text = self._text(memo_text)
            if not text:
                return
            day = self._get_day_unlocked(date_str) or DayRecord(date=date_str)
            lines = [line.strip() for line in str(day.memo or "").splitlines() if line.strip()]
            new_line = f"- {text}"
            if new_line not in lines and text not in lines:
                lines.append(new_line)
            day.memo = "\n".join(lines)
            self._set_day_unlocked(day)
            self._conn.commit()

        await self._run_db(write)

