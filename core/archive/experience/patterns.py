from __future__ import annotations

import sqlite3
from typing import Any

from ...models import (
    BehaviorPatternRecord,
    BehaviorSceneRecord,
    SessionMidSummaryRecord,
)
from .lines import _pack_lines, _unpack_lines

from typing import Protocol


class BehaviorArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _compose_behavior_pattern: Any
    _compose_behavior_scene: Any
    _compose_session_mid_summary: Any
    _conn: Any
    _run_db: Any
    _text: Any


class BehaviorArchiveComponent:
    def __init__(self, context: BehaviorArchiveComponentContext) -> None:
        self.context = context

    def _compose_behavior_pattern(self, row: sqlite3.Row) -> BehaviorPatternRecord:
        return BehaviorPatternRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            scene=row["scene"],
            pattern=self.context._text(row["pattern"]),
            suggested_action=self.context._text(row["suggested_action"]),
            confidence=float(row["confidence"] or 0.0),
            support_count=int(row["support_count"] or 0),
            score=float(row["score"] or 0.0),
            evidence=self.context._text(row["evidence"]),
            source=row["source"],
            last_seen=row["last_seen"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


    async def get_behavior_patterns(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        scene: str = "",
    ) -> list[BehaviorPatternRecord]:
        def dbwork():
            sql = "SELECT * FROM behavior_patterns"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("(scope = ? OR scope = '')")
                params.append(self.context._text(scope))
            if scene:
                clauses.append("scene = ?")
                params.append(self.context._text(scene))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += (
                " ORDER BY confidence DESC, support_count DESC, last_seen DESC, id DESC"
            )
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_behavior_pattern(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_behavior_scene(self, row: sqlite3.Row) -> BehaviorSceneRecord:
        return BehaviorSceneRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            scene=self.context._text(row["scene"]),
            cues=_unpack_lines(row["cues"]),
            preferred_action=self.context._text(row["preferred_action"]),
            avoid_action=self.context._text(row["avoid_action"]),
            outcome_hint=self.context._text(row["outcome_hint"]),
            confidence=float(row["confidence"] or 0.0),
            support_count=int(row["support_count"] or 0),
            last_seen=row["last_seen"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def upsert_behavior_scene(
        self, scene: BehaviorSceneRecord
    ) -> BehaviorSceneRecord | None:
        item = BehaviorSceneRecord.from_value(
            scene.as_dict() if isinstance(scene, BehaviorSceneRecord) else scene
        )
        if not item:
            return None
        scope = self.context._text(item.scope)
        scene_text = self.context._text(item.scene)

        def dbwork():
            self.context._conn.execute(
                """
                INSERT INTO behavior_scenes(
                    scope, scene, cues, preferred_action, avoid_action, outcome_hint,
                    confidence, support_count, last_seen, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, scene) DO UPDATE SET
                    cues = COALESCE(NULLIF(excluded.cues, ''), behavior_scenes.cues),
                    preferred_action = COALESCE(NULLIF(excluded.preferred_action, ''), behavior_scenes.preferred_action),
                    avoid_action = COALESCE(NULLIF(excluded.avoid_action, ''), behavior_scenes.avoid_action),
                    outcome_hint = COALESCE(NULLIF(excluded.outcome_hint, ''), behavior_scenes.outcome_hint),
                    confidence = MAX(behavior_scenes.confidence, excluded.confidence),
                    support_count = behavior_scenes.support_count + excluded.support_count,
                    last_seen = COALESCE(NULLIF(excluded.last_seen, ''), behavior_scenes.last_seen),
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    scope,
                    scene_text,
                    _pack_lines(item.cues),
                    self.context._text(item.preferred_action),
                    self.context._text(item.avoid_action),
                    self.context._text(item.outcome_hint),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    max(1, int(item.support_count or 1)),
                    self.context._text(item.last_seen),
                    self.context._text(item.source) or "chat_memory",
                ),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM behavior_scenes WHERE scope = ? AND scene = ?",
                (scope, scene_text),
            ).fetchone()
            return self.context._compose_behavior_scene(row) if row else None

        return await self.context._run_db(dbwork)

    async def get_behavior_scenes(
        self,
        limit: int = 20,
        *,
        scope: str = "",
    ) -> list[BehaviorSceneRecord]:
        def dbwork():
            sql = "SELECT * FROM behavior_scenes"
            params: list[Any] = []
            if scope:
                sql += " WHERE scope = ? OR scope = ''"
                params.append(self.context._text(scope))
            sql += (
                " ORDER BY confidence DESC, support_count DESC, last_seen DESC, id DESC"
            )
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_behavior_scene(row) for row in rows]

        return await self.context._run_db(dbwork)

    def _compose_session_mid_summary(self, row: sqlite3.Row) -> SessionMidSummaryRecord:
        return SessionMidSummaryRecord(
            session_id=row["session_id"],
            scope_label=self.context._text(row["scope_label"]),
            summary=self.context._text(row["summary"]),
            topic=self.context._text(row["topic"]),
            mood=self.context._text(row["mood"]),
            participants=_unpack_lines(row["participants"]),
            message_count=int(row["message_count"] or 0),
            last_message_id=row["last_message_id"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


    async def get_session_mid_summaries(
        self,
        limit: int = 20,
        *,
        session_id: str = "",
    ) -> list[SessionMidSummaryRecord]:
        def dbwork():
            sql = "SELECT * FROM session_mid_summaries"
            params: list[Any] = []
            if session_id:
                sql += " WHERE session_id = ?"
                params.append(self.context._text(session_id))
            sql += " ORDER BY updated_at DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self.context._conn.execute(sql, tuple(params)).fetchall()
            return [self.context._compose_session_mid_summary(row) for row in rows]

        return await self.context._run_db(dbwork)
