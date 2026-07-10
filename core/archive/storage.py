import datetime
import sqlite3
from typing import Any

from ..clock import today as life_today
from .categories import STORAGE_CATEGORIES, StorageCategory, normalize_storage_category


class StorageArchiveMixin:
    def _table_exists_unlocked(self, table: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return bool(row)

    def _table_count_unlocked(self, table: str) -> int:
        if not self._table_exists_unlocked(table):
            return 0
        try:
            row = self._conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        except sqlite3.Error:
            return 0
        return int(row["count"] or 0) if row else 0

    def _storage_keep_days(self, category: StorageCategory, policy: Any = None) -> int:
        key = f"{category.key}_keep_days"
        value = None
        if isinstance(policy, dict):
            value = policy.get(key)
        elif policy is not None:
            value = getattr(policy, key, None)
        if value is None:
            value = category.default_keep_days
        try:
            return max(int(float(value)), 0)
        except (TypeError, ValueError):
            return category.default_keep_days

    def _storage_category_stats_unlocked(self, category: StorageCategory, policy: Any = None) -> dict:
        tables = [
            {"name": table, "rows": self._table_count_unlocked(table)}
            for table in category.tables
        ]
        rows_by_table = {item["name"]: item["rows"] for item in tables}
        groups = [
            {
                "key": group.key,
                "label": group.label,
                "total_rows": sum(rows_by_table.get(table, 0) for table in group.tables),
                "tables": [
                    {"name": table, "rows": rows_by_table.get(table, 0)}
                    for table in group.tables
                ],
            }
            for group in category.groups
        ]
        total = sum(item["rows"] for item in tables)
        keep_days = self._storage_keep_days(category, policy)
        return {
            "key": category.key,
            "label": category.label,
            "description": category.description,
            "retention_days": keep_days,
            "auto_cleanup": bool(category.auto_cleanup and keep_days > 0),
            "total_rows": total,
            "tables": tables,
            "groups": groups,
        }

    async def get_storage_categories(self, policy: Any = None) -> list[dict]:
        def read() -> list[dict]:
            return [
                self._storage_category_stats_unlocked(category, policy)
                for category in STORAGE_CATEGORIES.values()
            ]

        return await self._run_db(read)

    async def get_storage_overview(self, policy: Any = None) -> dict:
        categories = await self.get_storage_categories(policy)
        return {
            "categories": categories,
            "total_rows": sum(item["total_rows"] for item in categories),
        }

    def _delete_all_unlocked(self, tables: tuple[str, ...]) -> int:
        deleted = 0
        for table in tables:
            if not self._table_exists_unlocked(table):
                continue
            cursor = self._conn.execute(f"DELETE FROM {table}")
            if cursor.rowcount and cursor.rowcount > 0:
                deleted += int(cursor.rowcount)
                if table == "physiological_rhythm_logs":
                    invalidate = getattr(self, "_invalidate_physiological_rhythm_trend_cache", None)
                    if callable(invalidate):
                        invalidate()
        return deleted

    async def clear_storage_category(self, category_key: str) -> dict:
        category = STORAGE_CATEGORIES.get(normalize_storage_category(category_key))
        if not category:
            raise ValueError("存储分类不存在")

        def write() -> dict:
            deleted = self._delete_all_unlocked(category.clear_order)
            self._conn.commit()
            return {
                "category": category.key,
                "label": category.label,
                "deleted_rows": deleted,
            }

        return await self._run_db(write)

    def _cutoff_date(self, keep_days: int) -> str:
        cutoff = life_today() - datetime.timedelta(days=keep_days)
        return cutoff.strftime("%Y-%m-%d")

    def _cutoff_week_id(self, keep_days: int) -> str:
        cutoff = life_today() - datetime.timedelta(days=keep_days)
        year, week, _ = cutoff.isocalendar()
        return f"{year:04d}-W{week:02d}"

    def _cleanup_daily_unlocked(self, keep_days: int) -> int:
        cutoff = self._cutoff_date(keep_days)
        cursor = self._conn.execute(
            "DELETE FROM days WHERE date <> '' AND date < ?",
            (cutoff,),
        )
        return max(int(cursor.rowcount or 0), 0)

    def _cleanup_review_unlocked(self, keep_days: int) -> int:
        cutoff = self._cutoff_date(keep_days)
        cursor = self._conn.execute(
            "DELETE FROM daily_reviews WHERE date <> '' AND date < ?",
            (cutoff,),
        )
        return max(int(cursor.rowcount or 0), 0)

    def _delete_statements_unlocked(self, statements: tuple[tuple[str, tuple[Any, ...]], ...]) -> int:
        deleted = 0
        for sql, params in statements:
            cursor = self._conn.execute(sql, params)
            if cursor.rowcount and cursor.rowcount > 0:
                deleted += int(cursor.rowcount)
                if "physiological_rhythm_logs" in sql:
                    invalidate = getattr(self, "_invalidate_physiological_rhythm_trend_cache", None)
                    if callable(invalidate):
                        invalidate()
        return deleted

    def _cleanup_relationships_unlocked(self, keep_days: int) -> int:
        return 0

    def _cleanup_world_unlocked(self, keep_days: int) -> int:
        cutoff = self._cutoff_date(keep_days)
        return self._delete_statements_unlocked(
            (
                ("DELETE FROM event_people WHERE event_id IN (SELECT id FROM events WHERE date <> '' AND date < ?)", (cutoff,)),
                ("DELETE FROM events WHERE date <> '' AND date < ?", (cutoff,)),
                (
                    """
                    DELETE FROM life_events
                    WHERE date <> '' AND date < ?
                      AND status IN ('done', 'closed', 'cancelled', 'expired', 'resolved')
                    """,
                    (cutoff,),
                ),
            )
        )

    def _cleanup_conversation_unlocked(self, keep_days: int) -> int:
        cutoff = self._cutoff_date(keep_days)
        return self._delete_statements_unlocked(
            (
                (
                    """
                    DELETE FROM chat_summary_people
                    WHERE summary_id IN (
                        SELECT id FROM chat_summaries
                        WHERE COALESCE(NULLIF(date, ''), created_at) < ?
                    )
                    """,
                    (cutoff,),
                ),
                ("DELETE FROM chat_summaries WHERE COALESCE(NULLIF(date, ''), created_at) < ?", (cutoff,)),
                ("DELETE FROM group_environments WHERE COALESCE(NULLIF(date, ''), created_at) < ?", (cutoff,)),
                ("DELETE FROM message_visibility WHERE COALESCE(NULLIF(date, ''), created_at) < ?", (cutoff,)),
                ("DELETE FROM action_decisions WHERE COALESCE(NULLIF(date, ''), created_at) < ?", (cutoff,)),
                ("DELETE FROM session_mid_summaries WHERE updated_at < ?", (cutoff,)),
            )
        )

    def _cleanup_experience_unlocked(self, keep_days: int) -> int:
        cutoff = self._cutoff_date(keep_days)
        stale_episode_where = """
            date <> '' AND date < ?
            AND protected = 0
            AND status IN ('done', 'closed', 'cancelled', 'expired', 'resolved', 'corrected')
        """
        return self._delete_statements_unlocked(
            (
                (
                    f"DELETE FROM life_episode_people WHERE episode_id IN (SELECT id FROM life_episodes WHERE {stale_episode_where})",
                    (cutoff,),
                ),
                (
                    f"DELETE FROM life_episode_places WHERE episode_id IN (SELECT id FROM life_episodes WHERE {stale_episode_where})",
                    (cutoff,),
                ),
                (f"DELETE FROM life_episodes WHERE {stale_episode_where}", (cutoff,)),
                ("DELETE FROM memory_evidence WHERE COALESCE(NULLIF(date, ''), created_at) < ?", (cutoff,)),
                ("DELETE FROM behavior_feedback WHERE COALESCE(NULLIF(date, ''), created_at) < ?", (cutoff,)),
                (
                    """
                    DELETE FROM emotion_arcs
                    WHERE COALESCE(NULLIF(date, ''), updated_at) < ?
                       OR status <> 'active'
                       OR (expires_at <> '' AND expires_at < datetime('now', 'localtime'))
                    """,
                    (cutoff,),
                ),
                (
                    """
                    DELETE FROM physiological_rhythm_logs
                    WHERE COALESCE(NULLIF(date, ''), updated_at) < ?
                       OR status <> 'active'
                    """,
                    (cutoff,),
                ),
                ("DELETE FROM reply_effects WHERE updated_at < ?", (cutoff,)),
                ("DELETE FROM memory_decision_links WHERE decision_id IN (SELECT id FROM life_decisions WHERE COALESCE(NULLIF(date, ''), created_at) < ?)", (cutoff,)),
                ("DELETE FROM life_decisions WHERE COALESCE(NULLIF(date, ''), created_at) < ?", (cutoff,)),
                ("DELETE FROM memory_corrections WHERE applied = 1 AND updated_at < ?", (cutoff,)),
                ("DELETE FROM behavior_patterns WHERE COALESCE(NULLIF(last_seen, ''), updated_at) < ?", (cutoff,)),
                ("DELETE FROM behavior_scenes WHERE COALESCE(NULLIF(last_seen, ''), updated_at) < ?", (cutoff,)),
                ("DELETE FROM focus_slots WHERE COALESCE(NULLIF(expires_at, ''), updated_at) < ?", (cutoff,)),
                ("DELETE FROM focus_targets WHERE expires_at <> '' AND expires_at < ?", (cutoff,)),
                ("DELETE FROM life_terms WHERE COALESCE(NULLIF(last_seen, ''), updated_at) < ?", (cutoff,)),
                ("DELETE FROM memory_boundaries WHERE enabled = 0 AND updated_at < ?", (cutoff,)),
                ("DELETE FROM memory_maintenance WHERE COALESCE(NULLIF(date, ''), created_at) < ?", (cutoff,)),
            )
        )

    def _cleanup_expression_unlocked(self, keep_days: int) -> int:
        cutoff = self._cutoff_date(keep_days)
        return self._delete_statements_unlocked(
            (
                ("DELETE FROM expression_profiles WHERE updated_at < ?", (cutoff,)),
                ("DELETE FROM expression_reviews WHERE created_at < ?", (cutoff,)),
                ("DELETE FROM temporary_expression_states WHERE COALESCE(NULLIF(expires_at, ''), updated_at) < ?", (cutoff,)),
                ("DELETE FROM expression_intents WHERE created_at < ?", (cutoff,)),
                ("DELETE FROM emoji_assets WHERE status = 'disabled' AND updated_at < ?", (cutoff,)),
                ("DELETE FROM reverse_prompts WHERE created_at < ?", (cutoff,)),
            )
        )

    def _cleanup_media_unlocked(self, keep_days: int) -> int:
        cutoff_time = datetime.datetime.combine(
            life_today() - datetime.timedelta(days=keep_days),
            datetime.time.min,
        ).timestamp()
        return self._delete_statements_unlocked(
            (
                ("DELETE FROM video_insight_frames WHERE insight_id IN (SELECT id FROM video_insights WHERE updated_at > 0 AND updated_at < ?)", (cutoff_time,)),
                ("DELETE FROM video_insight_details WHERE insight_id IN (SELECT id FROM video_insights WHERE updated_at > 0 AND updated_at < ?)", (cutoff_time,)),
                ("DELETE FROM video_insights WHERE updated_at > 0 AND updated_at < ?", (cutoff_time,)),
            )
        )

    def _cleanup_longterm_unlocked(self, keep_days: int) -> int:
        cutoff = self._cutoff_date(keep_days)
        return self._delete_statements_unlocked(
            (
                (
                    """
                    DELETE FROM long_term_memories
                    WHERE status <> 'active'
                       OR (expires_at <> '' AND expires_at < DATE('now', 'localtime'))
                       OR (date <> '' AND date < ? AND weight < 3)
                    """,
                    (cutoff,),
                ),
                (
                    """
                    DELETE FROM memory_episode_clusters
                    WHERE status <> 'active'
                       OR COALESCE(NULLIF(last_date, ''), updated_at) < ?
                    """,
                    (cutoff,),
                ),
                ("DELETE FROM memory_entities WHERE status <> 'active'", ()),
                ("DELETE FROM memory_conflicts WHERE status <> 'open'", ()),
                (
                    """
                    DELETE FROM memory_entity_links
                    WHERE memory_id NOT IN (SELECT id FROM long_term_memories)
                       OR entity_id NOT IN (SELECT id FROM memory_entities)
                    """,
                    (),
                ),
                (
                    """
                    DELETE FROM memory_episode_cluster_items
                    WHERE memory_id NOT IN (SELECT id FROM long_term_memories)
                       OR cluster_id NOT IN (SELECT id FROM memory_episode_clusters)
                    """,
                    (),
                ),
                (
                    """
                    DELETE FROM memory_conflicts
                    WHERE memory_id NOT IN (SELECT id FROM long_term_memories)
                       OR related_memory_id NOT IN (SELECT id FROM long_term_memories)
                    """,
                    (),
                ),
                (
                    """
                    DELETE FROM memory_decision_links
                    WHERE memory_id NOT IN (SELECT id FROM long_term_memories)
                       OR decision_id NOT IN (SELECT id FROM life_decisions)
                    """,
                    (),
                ),
            )
        )

    def _cleanup_planning_unlocked(self, keep_days: int) -> int:
        cutoff = self._cutoff_date(keep_days)
        cutoff_week = self._cutoff_week_id(keep_days)
        deleted = 0
        for sql, params in (
            ("DELETE FROM week_plans WHERE week_id <> '' AND week_id < ?", (cutoff_week,)),
            ("DELETE FROM day_commitments WHERE date <> '' AND date < ?", (cutoff,)),
            (
                """
                DELETE FROM commitments
                WHERE status IN ('done', 'cancelled', 'expired')
                  AND COALESCE(NULLIF(completed_at, ''), NULLIF(trigger_date, ''), created_at) < ?
                """,
                (cutoff,),
            ),
        ):
            cursor = self._conn.execute(sql, params)
            if cursor.rowcount and cursor.rowcount > 0:
                deleted += int(cursor.rowcount)
        return deleted

    def _cleanup_category_unlocked(self, category: StorageCategory, keep_days: int) -> int:
        if keep_days <= 0:
            return 0
        if category.key == "daily":
            return self._cleanup_daily_unlocked(keep_days)
        if category.key == "review":
            return self._cleanup_review_unlocked(keep_days)
        if category.key == "relationships":
            return self._cleanup_relationships_unlocked(keep_days)
        if category.key == "world":
            return self._cleanup_world_unlocked(keep_days)
        if category.key == "conversation":
            return self._cleanup_conversation_unlocked(keep_days)
        if category.key == "experience":
            return self._cleanup_experience_unlocked(keep_days)
        if category.key == "expression":
            return self._cleanup_expression_unlocked(keep_days)
        if category.key == "media":
            return self._cleanup_media_unlocked(keep_days)
        if category.key == "longterm":
            return self._cleanup_longterm_unlocked(keep_days)
        if category.key == "planning":
            return self._cleanup_planning_unlocked(keep_days)
        return 0

    async def cleanup_storage_category(self, category_key: str, keep_days: int | None = None) -> dict:
        category = STORAGE_CATEGORIES.get(normalize_storage_category(category_key))
        if not category:
            raise ValueError("存储分类不存在")
        retention = self._storage_keep_days(category) if keep_days is None else max(int(keep_days), 0)

        def write() -> dict:
            deleted = self._cleanup_category_unlocked(category, retention)
            self._conn.commit()
            return {
                "category": category.key,
                "label": category.label,
                "keep_days": retention,
                "deleted_rows": deleted,
            }

        return await self._run_db(write)

    async def cleanup_by_storage_policy(self, policy: Any = None) -> dict:
        def write() -> dict:
            results = []
            for category in STORAGE_CATEGORIES.values():
                keep_days = self._storage_keep_days(category, policy)
                if keep_days <= 0 or not category.auto_cleanup:
                    continue
                deleted = self._cleanup_category_unlocked(category, keep_days)
                results.append(
                    {
                        "category": category.key,
                        "label": category.label,
                        "keep_days": keep_days,
                        "deleted_rows": deleted,
                    }
                )
            self._conn.commit()
            return {
                "results": results,
                "deleted_rows": sum(item["deleted_rows"] for item in results),
            }

        return await self._run_db(write)
