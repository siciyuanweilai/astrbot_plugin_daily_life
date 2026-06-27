import sqlite3

from ..models import WeekPlanRecord, WeekTemplateRecord


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
                INSERT INTO week_plans(week_id, theme, template_id, generated) VALUES (?, ?, ?, ?)
                ON CONFLICT(week_id) DO UPDATE SET
                    theme = excluded.theme,
                    template_id = excluded.template_id,
                    generated = excluded.generated
                """,
                (
                    plan.week_id,
                    plan.theme,
                    plan.template_id,
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
            template_id=row["template_id"],
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
    async def get_custom_week_templates(self, include_disabled: bool = False) -> dict[str, WeekTemplateRecord]:
        async with self._lock:
            sql = "SELECT * FROM custom_week_templates"
            if not include_disabled:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY name"
            rows = self._conn.execute(sql).fetchall()
            return {row["template_id"]: self._compose_week_template(row) for row in rows}
    async def get_custom_week_template(self, template_id: str) -> WeekTemplateRecord | None:
        async with self._lock:
            row = self._conn.execute(
                "SELECT * FROM custom_week_templates WHERE template_id = ?",
                (self._text(template_id),),
            ).fetchone()
            return self._compose_week_template(row) if row else None
    async def save_custom_week_template(self, template: WeekTemplateRecord) -> WeekTemplateRecord:
        async with self._lock:
            self._set_custom_week_template_unlocked(template)
            self._conn.commit()
            return self._compose_week_template(
                self._conn.execute(
                    "SELECT * FROM custom_week_templates WHERE template_id = ?",
                    (template.template_id,),
                ).fetchone()
            )
    async def set_custom_week_template_enabled(self, template_id: str, enabled: bool) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE custom_week_templates
                SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE template_id = ?
                """,
                (self._flag(enabled), self._text(template_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    async def set_custom_week_template_weight(self, template_id: str, weight: float) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE custom_week_templates
                SET weight = ?, updated_at = CURRENT_TIMESTAMP
                WHERE template_id = ?
                """,
                (max(float(weight), 0.0), self._text(template_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    async def delete_custom_week_template(self, template_id: str) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM custom_week_templates WHERE template_id = ?",
                (self._text(template_id),),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    def _compose_week_template(self, row: sqlite3.Row | None) -> WeekTemplateRecord:
        if row is None:
            return WeekTemplateRecord()
        template_id = row["template_id"]
        return WeekTemplateRecord(
            template_id=template_id,
            name=row["name"],
            description=row["description"],
            emoji=row["emoji"],
            weight=float(row["weight"] or 0.0),
            enabled=bool(row["enabled"]),
            cooldown_weeks=int(row["cooldown_weeks"] or 3),
            source=row["source"],
            goals=self._get_ordered_texts_unlocked("custom_week_template_goals", "template_id", template_id, "goal"),
            daily_hints={
                item["day_key"]: item["hint"]
                for item in self._conn.execute(
                    "SELECT day_key, hint FROM custom_week_template_hints WHERE template_id = ? ORDER BY day_key",
                    (template_id,),
                ).fetchall()
            },
            suggested_activities=self._get_template_suggestions_unlocked(template_id),
            tags=self._get_ordered_texts_unlocked("custom_week_template_tags", "template_id", template_id, "tag"),
        )
    def _get_template_suggestions_unlocked(self, template_id: str) -> dict[str, list[str]]:
        suggestions: dict[str, list[str]] = {}
        rows = self._conn.execute(
            """
            SELECT day_key, suggestion
            FROM custom_week_template_suggestions
            WHERE template_id = ?
            ORDER BY day_key, sort_order
            """,
            (template_id,),
        ).fetchall()
        for row in rows:
            suggestions.setdefault(row["day_key"], []).append(row["suggestion"])
        return suggestions
    def _set_custom_week_template_unlocked(self, template: WeekTemplateRecord) -> None:
        template.template_id = self._text(template.template_id)
        template.name = self._text(template.name)
        template.description = self._text(template.description) or template.name
        template.emoji = self._text(template.emoji) or "📅"
        template.weight = max(float(template.weight or 0.0), 0.0)
        template.cooldown_weeks = max(int(template.cooldown_weeks or 0), 0)
        template.source = self._text(template.source) or "custom"
        if not template.template_id or not template.name:
            raise ValueError("模板标识和名称不能为空")
        self._conn.execute(
            """
            INSERT INTO custom_week_templates(
                template_id, name, description, emoji, weight, enabled,
                cooldown_weeks, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(template_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                emoji = excluded.emoji,
                weight = excluded.weight,
                enabled = excluded.enabled,
                cooldown_weeks = excluded.cooldown_weeks,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                template.template_id,
                template.name,
                template.description,
                template.emoji,
                template.weight,
                self._flag(template.enabled),
                template.cooldown_weeks,
                template.source,
            ),
        )
        self._replace_template_goals_unlocked(template.template_id, template.goals)
        self._replace_template_hints_unlocked(template.template_id, template.daily_hints)
        self._replace_template_suggestions_unlocked(template.template_id, template.suggested_activities)
        self._replace_template_tags_unlocked(template.template_id, template.tags)
    def _replace_template_goals_unlocked(self, template_id: str, goals: list[str]) -> None:
        self._conn.execute("DELETE FROM custom_week_template_goals WHERE template_id = ?", (template_id,))
        for idx, goal in enumerate(goals):
            text = self._text(goal)
            if text:
                self._conn.execute(
                    "INSERT INTO custom_week_template_goals(template_id, sort_order, goal) VALUES (?, ?, ?)",
                    (template_id, idx, text),
                )
    def _replace_template_hints_unlocked(self, template_id: str, hints: dict[str, str]) -> None:
        self._conn.execute("DELETE FROM custom_week_template_hints WHERE template_id = ?", (template_id,))
        for day_key, hint in hints.items():
            key = self._text(day_key)
            if key:
                self._conn.execute(
                    "INSERT INTO custom_week_template_hints(template_id, day_key, hint) VALUES (?, ?, ?)",
                    (template_id, key, self._text(hint)),
                )
    def _replace_template_suggestions_unlocked(self, template_id: str, suggestions: dict[str, list[str]]) -> None:
        self._conn.execute("DELETE FROM custom_week_template_suggestions WHERE template_id = ?", (template_id,))
        for day_key, values in suggestions.items():
            key = self._text(day_key)
            if not key:
                continue
            for idx, value in enumerate(values):
                text = self._text(value)
                if text:
                    self._conn.execute(
                        """
                        INSERT INTO custom_week_template_suggestions(
                            template_id, day_key, sort_order, suggestion
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (template_id, key, idx, text),
                    )
    def _replace_template_tags_unlocked(self, template_id: str, tags: list[str]) -> None:
        self._conn.execute("DELETE FROM custom_week_template_tags WHERE template_id = ?", (template_id,))
        for idx, tag in enumerate(tags):
            text = self._text(tag)
            if text:
                self._conn.execute(
                    "INSERT INTO custom_week_template_tags(template_id, sort_order, tag) VALUES (?, ?, ?)",
                    (template_id, idx, text),
                )

