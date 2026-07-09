import sqlite3
from typing import Any

from ...models import BehaviorPatternRecord, BehaviorSceneRecord, SessionMidSummaryRecord
from .lines import _pack_lines, _unpack_lines


class BehaviorArchiveMixin:
    def _compose_behavior_pattern(self, row: sqlite3.Row) -> BehaviorPatternRecord:
        return BehaviorPatternRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            scene=row["scene"],
            pattern=self._text(row["pattern"]),
            suggested_action=self._text(row["suggested_action"]),
            confidence=float(row["confidence"] or 0.0),
            support_count=int(row["support_count"] or 0),
            score=float(row["score"] or 0.0),
            evidence=self._text(row["evidence"]),
            source=row["source"],
            last_seen=row["last_seen"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def upsert_behavior_pattern(self, pattern: BehaviorPatternRecord) -> BehaviorPatternRecord | None:
        item = BehaviorPatternRecord.from_value(
            pattern.as_dict() if isinstance(pattern, BehaviorPatternRecord) else pattern
        )
        if not item:
            return None
        scope = self._text(item.scope)
        scene = self._text(item.scene)
        pattern_text = self._text(item.pattern)
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO behavior_patterns(
                    scope, scene, pattern, suggested_action, confidence, support_count,
                    score, evidence, source, last_seen, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(scope, scene, pattern) DO UPDATE SET
                    suggested_action = COALESCE(NULLIF(excluded.suggested_action, ''), behavior_patterns.suggested_action),
                    confidence = MAX(behavior_patterns.confidence, excluded.confidence),
                    support_count = behavior_patterns.support_count + excluded.support_count,
                    score = MAX(-5.0, MIN(5.0, behavior_patterns.score + excluded.score)),
                    evidence = COALESCE(NULLIF(excluded.evidence, ''), behavior_patterns.evidence),
                    source = excluded.source,
                    last_seen = excluded.last_seen,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    scope,
                    scene,
                    pattern_text,
                    self._text(item.suggested_action),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    max(1, int(item.support_count or 1)),
                    max(-5.0, min(float(item.score or 0.0), 5.0)),
                    self._text(item.evidence),
                    self._text(item.source) or "chat_memory",
                    self._text(item.last_seen),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM behavior_patterns WHERE scope = ? AND scene = ? AND pattern = ?",
                (scope, scene, pattern_text),
            ).fetchone()
            return self._compose_behavior_pattern(row) if row else None
    async def get_behavior_patterns(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        scene: str = "",
    ) -> list[BehaviorPatternRecord]:
        async with self._lock:
            sql = "SELECT * FROM behavior_patterns"
            params: list[Any] = []
            clauses = []
            if scope:
                clauses.append("(scope = ? OR scope = '')")
                params.append(self._text(scope))
            if scene:
                clauses.append("scene = ?")
                params.append(self._text(scene))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY confidence DESC, support_count DESC, last_seen DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_behavior_pattern(row) for row in rows]
    def _compose_behavior_scene(self, row: sqlite3.Row) -> BehaviorSceneRecord:
        return BehaviorSceneRecord(
            id=int(row["id"] or 0),
            scope=row["scope"],
            scene=self._text(row["scene"]),
            cues=_unpack_lines(row["cues"]),
            preferred_action=self._text(row["preferred_action"]),
            avoid_action=self._text(row["avoid_action"]),
            outcome_hint=self._text(row["outcome_hint"]),
            confidence=float(row["confidence"] or 0.0),
            support_count=int(row["support_count"] or 0),
            last_seen=row["last_seen"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def upsert_behavior_scene(self, scene: BehaviorSceneRecord) -> BehaviorSceneRecord | None:
        item = BehaviorSceneRecord.from_value(scene.as_dict() if isinstance(scene, BehaviorSceneRecord) else scene)
        if not item:
            return None
        scope = self._text(item.scope)
        scene_text = self._text(item.scene)
        async with self._lock:
            self._conn.execute(
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
                    self._text(item.preferred_action),
                    self._text(item.avoid_action),
                    self._text(item.outcome_hint),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    max(1, int(item.support_count or 1)),
                    self._text(item.last_seen),
                    self._text(item.source) or "chat_memory",
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM behavior_scenes WHERE scope = ? AND scene = ?",
                (scope, scene_text),
            ).fetchone()
            return self._compose_behavior_scene(row) if row else None
    async def get_behavior_scenes(
        self,
        limit: int = 20,
        *,
        scope: str = "",
    ) -> list[BehaviorSceneRecord]:
        async with self._lock:
            sql = "SELECT * FROM behavior_scenes"
            params: list[Any] = []
            if scope:
                sql += " WHERE scope = ? OR scope = ''"
                params.append(self._text(scope))
            sql += " ORDER BY confidence DESC, support_count DESC, last_seen DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_behavior_scene(row) for row in rows]
    def _compose_session_mid_summary(self, row: sqlite3.Row) -> SessionMidSummaryRecord:
        return SessionMidSummaryRecord(
            session_id=row["session_id"],
            scope_label=self._text(row["scope_label"]),
            summary=self._text(row["summary"]),
            topic=self._text(row["topic"]),
            mood=self._text(row["mood"]),
            participants=_unpack_lines(row["participants"]),
            message_count=int(row["message_count"] or 0),
            last_message_id=row["last_message_id"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    async def upsert_session_mid_summary(self, summary: SessionMidSummaryRecord) -> SessionMidSummaryRecord | None:
        item = SessionMidSummaryRecord.from_value(
            summary.as_dict() if isinstance(summary, SessionMidSummaryRecord) else summary
        )
        if not item:
            return None
        session_id = self._text(item.session_id)
        async with self._lock:
            existing = self._conn.execute(
                "SELECT message_count FROM session_mid_summaries WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            message_count = max(
                int(item.message_count or 0),
                int(existing["message_count"] or 0) + 1 if existing else 1,
            )
            self._conn.execute(
                """
                INSERT INTO session_mid_summaries(
                    session_id, scope_label, summary, topic, mood, participants,
                    message_count, last_message_id, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    scope_label = COALESCE(NULLIF(excluded.scope_label, ''), session_mid_summaries.scope_label),
                    summary = COALESCE(NULLIF(excluded.summary, ''), session_mid_summaries.summary),
                    topic = COALESCE(NULLIF(excluded.topic, ''), session_mid_summaries.topic),
                    mood = COALESCE(NULLIF(excluded.mood, ''), session_mid_summaries.mood),
                    participants = COALESCE(NULLIF(excluded.participants, ''), session_mid_summaries.participants),
                    message_count = excluded.message_count,
                    last_message_id = COALESCE(NULLIF(excluded.last_message_id, ''), session_mid_summaries.last_message_id),
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    session_id,
                    self._text(item.scope_label),
                    self._text(item.summary),
                    self._text(item.topic),
                    self._text(item.mood),
                    _pack_lines(item.participants),
                    message_count,
                    self._text(item.last_message_id),
                    self._text(item.source) or "chat_memory",
                ),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM session_mid_summaries WHERE session_id = ?", (session_id,)).fetchone()
            return self._compose_session_mid_summary(row) if row else None
    async def get_session_mid_summaries(
        self,
        limit: int = 20,
        *,
        session_id: str = "",
    ) -> list[SessionMidSummaryRecord]:
        async with self._lock:
            sql = "SELECT * FROM session_mid_summaries"
            params: list[Any] = []
            if session_id:
                sql += " WHERE session_id = ?"
                params.append(self._text(session_id))
            sql += " ORDER BY updated_at DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            return [self._compose_session_mid_summary(row) for row in rows]
