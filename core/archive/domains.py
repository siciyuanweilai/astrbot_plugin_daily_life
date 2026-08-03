from __future__ import annotations

import json
from typing import Any


class DomainArchiveMixin:
    """持久化活动、饮食、家务、运动和出行记录。"""

    @staticmethod
    def _domain_json(value: Any, fallback: Any) -> str:
        payload = value if value is not None else fallback
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _domain_value(value: Any, fallback: Any) -> Any:
        try:
            parsed = json.loads(str(value or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback
        return parsed

    @classmethod
    def _domain_row(cls, row: Any) -> dict[str, Any]:
        result = dict(row)
        for key in (
            "evidence_json",
            "metadata_json",
            "ingredients_json",
            "tags_json",
        ):
            if key in result:
                target = key.removesuffix("_json")
                default = {} if key == "metadata_json" else []
                result[target] = cls._domain_value(result.pop(key), default)
        return result

    async def upsert_activity_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        action_id = self._text(payload.get("action_id"))
        if not action_id:
            raise ValueError("活动会话缺少动作编号")

        def dbwork() -> dict[str, Any]:
            self._conn.execute(
                """
                INSERT INTO activity_sessions(
                    action_id, date, scope, activity_type, title, status,
                    started_at, ended_at, last_heartbeat_at, duration_seconds,
                    source, evidence_json, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(action_id) DO UPDATE SET
                    date = excluded.date,
                    scope = excluded.scope,
                    activity_type = excluded.activity_type,
                    title = excluded.title,
                    status = excluded.status,
                    started_at = COALESCE(NULLIF(activity_sessions.started_at, ''), excluded.started_at),
                    ended_at = excluded.ended_at,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    duration_seconds = excluded.duration_seconds,
                    source = excluded.source,
                    evidence_json = excluded.evidence_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    action_id,
                    self._text(payload.get("date")),
                    self._text(payload.get("scope")) or "global",
                    self._text(payload.get("activity_type")),
                    self._text(payload.get("title")),
                    self._text(payload.get("status")) or "active",
                    self._text(payload.get("started_at")),
                    self._text(payload.get("ended_at")),
                    self._text(payload.get("last_heartbeat_at")),
                    max(0, self._int(payload.get("duration_seconds"))),
                    self._text(payload.get("source")) or "daily_plan",
                    self._domain_json(payload.get("evidence"), []),
                    self._domain_json(payload.get("metadata"), {}),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM activity_sessions WHERE action_id = ?", (action_id,)
            ).fetchone()
            return self._domain_row(row)

        return await self._run_db(dbwork)

    async def get_activity_sessions(
        self, *, limit: int = 20, status: str = ""
    ) -> list[dict[str, Any]]:
        def dbwork() -> list[dict[str, Any]]:
            params: list[Any] = []
            sql = "SELECT * FROM activity_sessions"
            if status:
                sql += " WHERE status = ?"
                params.append(self._text(status))
            sql += " ORDER BY COALESCE(NULLIF(ended_at, ''), started_at) DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            return [
                self._domain_row(row)
                for row in self._conn.execute(sql, tuple(params)).fetchall()
            ]

        return await self._run_db(dbwork)

    async def upsert_route(self, payload: dict[str, Any]) -> dict[str, Any]:
        origin = self._text(payload.get("origin_name"))
        destination = self._text(payload.get("destination_name"))
        mode = self._text(payload.get("travel_mode")) or "walking"
        if not origin or not destination:
            raise ValueError("路线缓存缺少起点或终点")

        def dbwork() -> dict[str, Any]:
            self._conn.execute(
                """
                INSERT INTO route_cache(
                    origin_name, destination_name, travel_mode, distance_meters,
                    duration_seconds, provider, confidence, fetched_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(origin_name, destination_name, travel_mode) DO UPDATE SET
                    distance_meters = excluded.distance_meters,
                    duration_seconds = excluded.duration_seconds,
                    provider = excluded.provider,
                    confidence = excluded.confidence,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
                """,
                (
                    origin,
                    destination,
                    mode,
                    float(payload.get("distance_meters") or 0),
                    max(0, self._int(payload.get("duration_seconds"))),
                    self._text(payload.get("provider")) or "fallback",
                    max(0.0, min(1.0, float(payload.get("confidence") or 0))),
                    self._text(payload.get("fetched_at")),
                    self._text(payload.get("expires_at")),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                """
                SELECT * FROM route_cache
                WHERE origin_name = ? AND destination_name = ? AND travel_mode = ?
                """,
                (origin, destination, mode),
            ).fetchone()
            return dict(row)

        return await self._run_db(dbwork)

    async def get_route(
        self, origin: str, destination: str, mode: str = "walking"
    ) -> dict[str, Any] | None:
        def dbwork() -> dict[str, Any] | None:
            row = self._conn.execute(
                """
                SELECT * FROM route_cache
                WHERE origin_name = ? AND destination_name = ? AND travel_mode = ?
                """,
                (self._text(origin), self._text(destination), self._text(mode)),
            ).fetchone()
            return dict(row) if row else None

        return await self._run_db(dbwork)

    async def delete_routes_for_place(self, name: str) -> int:
        """删除起点或终点包含指定地点的路线缓存。"""

        place_name = self._text(name)
        if not place_name:
            return 0

        def dbwork() -> int:
            cursor = self._conn.execute(
                """
                DELETE FROM route_cache
                WHERE origin_name = ? OR destination_name = ?
                """,
                (place_name, place_name),
            )
            self._conn.commit()
            return max(0, int(cursor.rowcount or 0))

        return await self._run_db(dbwork)

    async def update_place_coordinates(
        self,
        name: str,
        latitude: float,
        longitude: float,
        *,
        source: str = "manual",
        updated_at: str = "",
    ) -> bool:
        def dbwork() -> bool:
            self._conn.execute(
                """
                INSERT INTO places(
                    name, latitude, longitude, coordinate_source,
                    coordinate_updated_at, source
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    coordinate_source = excluded.coordinate_source,
                    coordinate_updated_at = excluded.coordinate_updated_at
                """,
                (
                    self._text(name),
                    float(latitude),
                    float(longitude),
                    self._text(source),
                    self._text(updated_at),
                    self._text(source) or "manual",
                ),
            )
            self._conn.commit()
            return True

        return await self._run_db(dbwork)

    async def upsert_recipe(self, payload: dict[str, Any]) -> dict[str, Any]:
        recipe_id = self._text(payload.get("id"))
        name = self._text(payload.get("name"))
        if not recipe_id or not name:
            raise ValueError("食谱缺少编号或名称")

        def dbwork() -> dict[str, Any]:
            self._conn.execute(
                """
                INSERT INTO recipes(id, name, meal_type, ingredients_json, tags_json, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    meal_type = excluded.meal_type,
                    ingredients_json = excluded.ingredients_json,
                    tags_json = excluded.tags_json,
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    recipe_id,
                    name,
                    self._text(payload.get("meal_type")),
                    self._domain_json(payload.get("ingredients"), []),
                    self._domain_json(payload.get("tags"), []),
                    self._text(payload.get("source")) or "manual",
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            return self._domain_row(row)

        return await self._run_db(dbwork)

    async def get_recipes(self, limit: int = 20) -> list[dict[str, Any]]:
        def dbwork() -> list[dict[str, Any]]:
            sql = "SELECT * FROM recipes ORDER BY updated_at DESC, name"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (int(limit),)
            return [
                self._domain_row(row)
                for row in self._conn.execute(sql, params).fetchall()
            ]

        return await self._run_db(dbwork)

    async def adjust_pantry_item(
        self,
        name: str,
        delta: float,
        *,
        unit: str = "",
        minimum_quantity: float = 0,
        expires_at: str = "",
        reason: str = "",
        action_id: str = "",
        occurred_at: str = "",
        source: str = "life_action",
    ) -> dict[str, Any]:
        item_name = self._text(name)
        if not item_name:
            raise ValueError("库存物品名称不能为空")

        def dbwork() -> dict[str, Any]:
            row = self._conn.execute(
                "SELECT * FROM pantry_items WHERE name = ?", (item_name,)
            ).fetchone()
            current = float(row["quantity"] or 0) if row else 0.0
            updated = max(0.0, current + float(delta or 0))
            stored_unit = self._text(unit) or (self._text(row["unit"]) if row else "")
            stored_minimum = max(0.0, float(minimum_quantity or 0))
            if row and stored_minimum == 0:
                stored_minimum = max(0.0, float(row["minimum_quantity"] or 0))
            stored_expiry = self._text(expires_at) or (
                self._text(row["expires_at"]) if row else ""
            )
            self._conn.execute(
                """
                INSERT INTO pantry_items(
                    name, quantity, unit, minimum_quantity, expires_at, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                    quantity = excluded.quantity,
                    unit = excluded.unit,
                    minimum_quantity = excluded.minimum_quantity,
                    expires_at = excluded.expires_at,
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item_name,
                    updated,
                    stored_unit,
                    stored_minimum,
                    stored_expiry,
                    self._text(source) or "life_action",
                ),
            )
            self._conn.execute(
                """
                INSERT INTO pantry_movements(
                    item_name, delta, unit, reason, action_id, occurred_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_name,
                    float(delta or 0),
                    stored_unit,
                    self._text(reason),
                    self._text(action_id),
                    self._text(occurred_at),
                    self._text(source) or "life_action",
                ),
            )
            self._conn.commit()
            saved = self._conn.execute(
                "SELECT * FROM pantry_items WHERE name = ?", (item_name,)
            ).fetchone()
            return dict(saved)

        return await self._run_db(dbwork)

    async def get_pantry_items(self, limit: int = 50) -> list[dict[str, Any]]:
        def dbwork() -> list[dict[str, Any]]:
            sql = (
                "SELECT * FROM pantry_items ORDER BY expires_at, updated_at DESC, name"
            )
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (int(limit),)
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

        return await self._run_db(dbwork)

    async def save_meal_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._save_domain_action_record(
            "meal_records",
            payload,
            fields=(
                "date",
                "meal_type",
                "name",
                "recipe_id",
                "status",
                "ingredients_json",
                "place",
                "source",
                "evidence_json",
                "occurred_at",
            ),
            json_fields={
                "ingredients_json": "ingredients",
                "evidence_json": "evidence",
            },
        )

    async def upsert_chore(self, payload: dict[str, Any]) -> dict[str, Any]:
        chore_id = self._text(payload.get("id"))
        name = self._text(payload.get("name"))
        if not chore_id or not name:
            raise ValueError("家务事项缺少编号或名称")

        def dbwork() -> dict[str, Any]:
            self._conn.execute(
                """
                INSERT INTO chores(
                    id, name, cadence_days, effort, last_completed_at,
                    next_due_at, enabled, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    cadence_days = excluded.cadence_days,
                    effort = excluded.effort,
                    last_completed_at = excluded.last_completed_at,
                    next_due_at = excluded.next_due_at,
                    enabled = excluded.enabled,
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    chore_id,
                    name,
                    max(0, self._int(payload.get("cadence_days"))),
                    max(1, min(5, self._int(payload.get("effort"), 1))),
                    self._text(payload.get("last_completed_at")),
                    self._text(payload.get("next_due_at")),
                    self._flag(payload.get("enabled", True)),
                    self._text(payload.get("source")) or "daily_plan",
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM chores WHERE id = ?", (chore_id,)
            ).fetchone()
            return dict(row)

        return await self._run_db(dbwork)

    async def save_chore_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._save_domain_action_record(
            "chore_records",
            payload,
            fields=(
                "chore_id",
                "name",
                "status",
                "duration_minutes",
                "evidence_json",
                "occurred_at",
                "source",
            ),
            json_fields={"evidence_json": "evidence"},
        )

    async def save_fitness_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._save_domain_action_record(
            "fitness_records",
            payload,
            fields=(
                "date",
                "activity",
                "duration_minutes",
                "intensity",
                "load_score",
                "status",
                "source",
                "evidence_json",
                "occurred_at",
            ),
            json_fields={"evidence_json": "evidence"},
        )

    async def _save_domain_action_record(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        fields: tuple[str, ...],
        json_fields: dict[str, str],
    ) -> dict[str, Any]:
        action_id = self._text(payload.get("action_id"))
        if not action_id:
            raise ValueError("领域记录缺少动作编号")

        def dbwork() -> dict[str, Any]:
            values: list[Any] = []
            for field in fields:
                if field in json_fields:
                    values.append(
                        self._domain_json(payload.get(json_fields[field]), [])
                    )
                else:
                    values.append(payload.get(field, ""))
            columns = ", ".join(("action_id", *fields))
            placeholders = ", ".join("?" for _ in range(len(fields) + 1))
            assignments = ", ".join(f"{field} = excluded.{field}" for field in fields)
            self._conn.execute(
                f"""
                INSERT INTO {table}({columns}) VALUES ({placeholders})
                ON CONFLICT(action_id) DO UPDATE SET {assignments}
                """,
                (action_id, *values),
            )
            self._conn.commit()
            row = self._conn.execute(
                f"SELECT * FROM {table} WHERE action_id = ?", (action_id,)
            ).fetchone()
            return self._domain_row(row)

        return await self._run_db(dbwork)

    async def get_chores(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._get_domain_rows(
            "chores", "enabled DESC, next_due_at, updated_at DESC", limit
        )

    async def get_chore_records(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._get_domain_rows(
            "chore_records", "occurred_at DESC, id DESC", limit
        )

    async def get_meal_records(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._get_domain_rows(
            "meal_records", "date DESC, occurred_at DESC, id DESC", limit
        )

    async def get_fitness_records(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._get_domain_rows(
            "fitness_records", "date DESC, occurred_at DESC, id DESC", limit
        )

    async def _get_domain_rows(
        self, table: str, order_by: str, limit: int
    ) -> list[dict[str, Any]]:
        def dbwork() -> list[dict[str, Any]]:
            sql = f"SELECT * FROM {table} ORDER BY {order_by}"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (int(limit),)
            return [
                self._domain_row(row)
                for row in self._conn.execute(sql, params).fetchall()
            ]

        return await self._run_db(dbwork)

    async def save_conversation_action_item(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        commitment_id = max(0, self._int(payload.get("commitment_id")))
        title = self._text(payload.get("title"))
        if not commitment_id or not title:
            raise ValueError("会话行动项缺少承诺编号或标题")

        def dbwork() -> dict[str, Any]:
            self._conn.execute(
                """
                INSERT INTO conversation_action_items(
                    commitment_id, title, owner, due_at, status, source_session,
                    source_message, evidence_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(commitment_id) DO UPDATE SET
                    title = excluded.title,
                    owner = excluded.owner,
                    due_at = excluded.due_at,
                    status = excluded.status,
                    source_session = excluded.source_session,
                    source_message = excluded.source_message,
                    evidence_json = excluded.evidence_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    commitment_id,
                    title,
                    self._text(payload.get("owner")),
                    self._text(payload.get("due_at")),
                    self._text(payload.get("status")) or "open",
                    self._text(payload.get("source_session")),
                    self._text(payload.get("source_message")),
                    self._domain_json(payload.get("evidence"), []),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM conversation_action_items WHERE commitment_id = ?",
                (commitment_id,),
            ).fetchone()
            return self._domain_row(row)

        return await self._run_db(dbwork)

    async def get_conversation_action_items(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return await self._get_domain_rows(
            "conversation_action_items", "status, due_at, id DESC", limit
        )

    async def get_unified_life_timeline(self, limit: int = 60) -> list[dict[str, Any]]:
        sessions = await self.get_activity_sessions(limit=limit)
        meals = await self.get_meal_records(limit=limit)
        chores = await self.get_chore_records(limit=limit)
        fitness = await self.get_fitness_records(limit=limit)
        action_items = await self.get_conversation_action_items(limit=limit)
        values: list[dict[str, Any]] = []
        for item in sessions:
            values.append(
                {
                    "kind": "activity",
                    "title": item.get("title") or item.get("activity_type"),
                    "status": item.get("status"),
                    "occurred_at": item.get("ended_at") or item.get("started_at"),
                    "source": item.get("source"),
                    "action_id": item.get("action_id"),
                    "details": item.get("metadata", {}),
                }
            )
        for kind, records, title_key in (
            ("meal", meals, "name"),
            ("chore", chores, "name"),
            ("fitness", fitness, "activity"),
            ("conversation_action", action_items, "title"),
        ):
            for item in records:
                values.append(
                    {
                        "kind": kind,
                        "title": item.get(title_key),
                        "status": item.get("status"),
                        "occurred_at": item.get("occurred_at")
                        or item.get("due_at")
                        or item.get("created_at"),
                        "source": item.get("source") or "conversation",
                        "action_id": item.get("action_id") or "",
                        "details": item,
                    }
                )
        values.sort(key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
        return values[: max(0, int(limit))]

    async def get_domain_snapshot(self, limit: int = 20) -> dict[str, Any]:
        """在一次数据库锁定中读取生活领域面板数据。"""

        def dbwork() -> dict[str, Any]:
            def rows(table: str, order_by: str, row_limit: int) -> list[dict[str, Any]]:
                sql = f"SELECT * FROM {table} ORDER BY {order_by}"
                params: tuple[Any, ...] = ()
                if row_limit > 0:
                    sql += " LIMIT ?"
                    params = (int(row_limit),)
                return [
                    self._domain_row(row)
                    for row in self._conn.execute(sql, params).fetchall()
                ]

            return {
                "activity_sessions": rows(
                    "activity_sessions",
                    "COALESCE(NULLIF(ended_at, ''), started_at) DESC, id DESC",
                    limit,
                ),
                "pantry": rows(
                    "pantry_items", "expires_at, updated_at DESC, name", limit
                ),
                "recipes": rows("recipes", "updated_at DESC, name", limit),
                "meals": rows(
                    "meal_records", "date DESC, occurred_at DESC, id DESC", limit
                ),
                "chores": rows(
                    "chores", "enabled DESC, next_due_at, updated_at DESC", limit
                ),
                "chore_records": rows(
                    "chore_records", "occurred_at DESC, id DESC", limit
                ),
                "fitness": rows(
                    "fitness_records", "date DESC, occurred_at DESC, id DESC", limit
                ),
                "conversation_actions": rows(
                    "conversation_action_items", "status, due_at, id DESC", limit
                ),
            }

        snapshot = await self._run_db(dbwork)
        snapshot["timeline"] = self._domain_unified_timeline(snapshot, max(limit, 40))
        return snapshot

    @staticmethod
    def _domain_unified_timeline(
        snapshot: dict[str, Any], limit: int
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for item in snapshot.get("activity_sessions", []):
            values.append(
                {
                    "kind": "activity",
                    "title": item.get("title") or item.get("activity_type"),
                    "status": item.get("status"),
                    "occurred_at": item.get("ended_at") or item.get("started_at"),
                    "source": item.get("source"),
                    "action_id": item.get("action_id"),
                    "details": item.get("metadata", {}),
                }
            )
        for kind, key, title_key in (
            ("meal", "meals", "name"),
            ("chore", "chore_records", "name"),
            ("fitness", "fitness", "activity"),
            ("conversation_action", "conversation_actions", "title"),
        ):
            for item in snapshot.get(key, []):
                values.append(
                    {
                        "kind": kind,
                        "title": item.get(title_key),
                        "status": item.get("status"),
                        "occurred_at": item.get("occurred_at")
                        or item.get("due_at")
                        or item.get("created_at"),
                        "source": item.get("source") or "conversation",
                        "action_id": item.get("action_id") or "",
                        "details": item,
                    }
                )
        values.sort(key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
        return values[: max(0, int(limit))]


__all__ = ["DomainArchiveMixin"]
