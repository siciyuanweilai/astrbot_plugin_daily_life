import sqlite3

from ..models import WeekPlanRecord


class WeekArchiveMixin:
    async def get_week_plan(self, week_id: str) -> WeekPlanRecord | None:
        async with self._lock:
            row = self._conn.execute(
                "SELECT * FROM week_plans WHERE week_id = ?",
                (week_id,),
            ).fetchone()
            return self._compose_week_plan(row) if row else None

    async def save_week_plan(self, plan: WeekPlanRecord):
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO week_plans(week_id, theme, generated) VALUES (?, ?, ?)
                ON CONFLICT(week_id) DO UPDATE SET
                    theme = excluded.theme,
                    generated = excluded.generated
                """,
                (
                    plan.week_id,
                    plan.theme,
                    self._flag(plan.generated),
                ),
            )
            self._replace_week_goals_unlocked(plan.week_id, plan.goals)
            self._replace_week_hints_unlocked(plan.week_id, plan.daily_hints)
            self._replace_week_suggestions_unlocked(plan.week_id, plan.suggested_activities)
            self._conn.commit()

    async def get_all_week_plans(self) -> dict[str, WeekPlanRecord]:
        async with self._lock:
            rows = self._conn.execute("SELECT * FROM week_plans").fetchall()
            return {row["week_id"]: self._compose_week_plan(row) for row in rows}

    def _compose_week_plan(self, row: sqlite3.Row) -> WeekPlanRecord:
        week_id = row["week_id"]
        goals = [
            item["goal"]
            for item in self._conn.execute(
                "SELECT goal FROM week_goals WHERE week_id = ? ORDER BY sort_order",
                (week_id,),
            ).fetchall()
        ]
        hints = {
            item["day_key"]: item["hint"]
            for item in self._conn.execute(
                "SELECT day_key, hint FROM week_hints WHERE week_id = ? ORDER BY day_key",
                (week_id,),
            ).fetchall()
        }
        suggestions: dict[str, list[str]] = {}
        for item in self._conn.execute(
            "SELECT day_key, suggestion FROM week_suggestions WHERE week_id = ? ORDER BY day_key, sort_order",
            (week_id,),
        ).fetchall():
            suggestions.setdefault(item["day_key"], []).append(item["suggestion"])
        return WeekPlanRecord(
            week_id=week_id,
            theme=row["theme"],
            goals=goals,
            daily_hints=hints,
            suggested_activities=suggestions,
            generated=bool(row["generated"]),
        )

    def _replace_week_goals_unlocked(self, week_id: str, goals: list[str]) -> None:
        self._conn.execute("DELETE FROM week_goals WHERE week_id = ?", (week_id,))
        for idx, goal in enumerate(goals):
            text = self._text(goal)
            if text:
                self._conn.execute(
                    "INSERT INTO week_goals(week_id, sort_order, goal) VALUES (?, ?, ?)",
                    (week_id, idx, text),
                )

    def _replace_week_hints_unlocked(self, week_id: str, hints: dict[str, str]) -> None:
        self._conn.execute("DELETE FROM week_hints WHERE week_id = ?", (week_id,))
        for day_key, hint in hints.items():
            key = self._text(day_key)
            if key:
                self._conn.execute(
                    "INSERT INTO week_hints(week_id, day_key, hint) VALUES (?, ?, ?)",
                    (week_id, key, self._text(hint)),
                )

    def _replace_week_suggestions_unlocked(self, week_id: str, suggestions: dict[str, list[str]]) -> None:
        self._conn.execute("DELETE FROM week_suggestions WHERE week_id = ?", (week_id,))
        for day_key, values in suggestions.items():
            key = self._text(day_key)
            if not key:
                continue
            for idx, value in enumerate(values):
                text = self._text(value)
                if text:
                    self._conn.execute(
                        "INSERT INTO week_suggestions(week_id, day_key, sort_order, suggestion) VALUES (?, ?, ?, ?)",
                        (week_id, key, idx, text),
                    )
