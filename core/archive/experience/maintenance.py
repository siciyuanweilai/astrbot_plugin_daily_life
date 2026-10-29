from __future__ import annotations

import sqlite3
from typing import Any

from ...models import MemoryMaintenanceRecord

from typing import Protocol


class MaintenanceArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _compose_memory_maintenance: Any
    _conn: Any
    _run_db: Any
    _text: Any
    get_behavior_feedback: Any
    get_emotion_arcs: Any
    get_life_episodes: Any
    get_life_terms: Any
    get_memory_boundaries: Any
    get_memory_evidence: Any
    get_physiological_rhythm_logs: Any
    get_storage_overview: Any


class MaintenanceArchiveComponent:
    def __init__(self, context: MaintenanceArchiveComponentContext) -> None:
        self.context = context

    def _compose_memory_maintenance(self, row: sqlite3.Row) -> MemoryMaintenanceRecord:
        return MemoryMaintenanceRecord(
            id=int(row["id"] or 0),
            date=row["date"],
            summary=self.context._text(row["summary"]),
            merged_count=int(row["merged_count"] or 0),
            corrected_count=int(row["corrected_count"] or 0),
            pruned_count=int(row["pruned_count"] or 0),
            reason=self.context._text(row["reason"]),
            created_at=row["created_at"],
        )

    async def save_memory_maintenance(
        self, maintenance: MemoryMaintenanceRecord
    ) -> MemoryMaintenanceRecord | None:
        item = MemoryMaintenanceRecord.from_value(
            maintenance.as_dict()
            if isinstance(maintenance, MemoryMaintenanceRecord)
            else maintenance
        )
        if not item:
            return None

        def dbwork():
            cursor = self.context._conn.execute(
                """
                INSERT INTO memory_maintenance(
                    date, summary, merged_count, corrected_count, pruned_count, reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    self.context._text(item.date),
                    self.context._text(item.summary),
                    max(int(item.merged_count or 0), 0),
                    max(int(item.corrected_count or 0), 0),
                    max(int(item.pruned_count or 0), 0),
                    self.context._text(item.reason),
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM memory_maintenance WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return self.context._compose_memory_maintenance(row) if row else None

        return await self.context._run_db(dbwork)

    async def get_memory_maintenance(
        self, limit: int = 20
    ) -> list[MemoryMaintenanceRecord]:
        def dbwork():
            sql = "SELECT * FROM memory_maintenance ORDER BY date DESC, id DESC"
            params: tuple[Any, ...] = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = (limit,)
            rows = self.context._conn.execute(sql, params).fetchall()
            return [self.context._compose_memory_maintenance(row) for row in rows]

        return await self.context._run_db(dbwork)

    async def get_life_health_report(self, policy: Any = None) -> dict:
        storage = await self.context.get_storage_overview(policy)
        evidence = await self.context.get_memory_evidence(limit=5)
        episodes = await self.context.get_life_episodes(limit=5)
        feedback = await self.context.get_behavior_feedback(limit=5)
        emotion_arcs = await self.context.get_emotion_arcs(limit=5)
        rhythm_logs = await self.context.get_physiological_rhythm_logs(limit=5)
        terms = await self.context.get_life_terms(limit=5)
        boundaries = await self.context.get_memory_boundaries(limit=5)
        memory_category_keys = {
            "relationships",
            "world",
            "conversation",
            "experience",
            "expression",
            "media",
            "longterm",
        }
        memory_rows = sum(
            int(item.get("total_rows") or 0)
            for item in storage.get("categories", [])
            if item.get("key") in memory_category_keys
        )
        checks = [
            {
                "key": "life_episodes",
                "label": "生活片段",
                "ok": bool(episodes),
                "count": len(episodes),
            },
            {
                "key": "memory_evidence",
                "label": "证据链",
                "ok": bool(evidence),
                "count": len(evidence),
            },
            {
                "key": "behavior_feedback",
                "label": "行为反馈",
                "ok": bool(feedback),
                "count": len(feedback),
            },
            {
                "key": "emotion_arcs",
                "label": "情绪脉络",
                "ok": bool(emotion_arcs),
                "count": len(emotion_arcs),
            },
            {
                "key": "physiological_rhythm_logs",
                "label": "生理节律",
                "ok": bool(rhythm_logs),
                "count": len(rhythm_logs),
            },
            {
                "key": "life_terms",
                "label": "语言",
                "ok": bool(terms),
                "count": len(terms),
            },
            {
                "key": "memory_boundaries",
                "label": "记忆边界",
                "ok": True,
                "count": len(boundaries),
            },
            {
                "key": "memory_rows",
                "label": "记忆存储",
                "ok": memory_rows >= 0,
                "count": memory_rows,
            },
        ]
        ok_count = sum(1 for item in checks if item["ok"])
        return {
            "score": round(ok_count / len(checks) * 100),
            "checks": checks,
            "summary": f"体验层 {len(checks)} 项检查",
        }
