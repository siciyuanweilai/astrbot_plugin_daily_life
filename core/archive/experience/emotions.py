import datetime
import sqlite3
from dataclasses import replace
from typing import Any

from ...models import EmotionArcRecord


class EmotionArchiveMixin:
    @staticmethod
    def _emotion_now_text() -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_emotion_time(value: Any) -> datetime.datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        for candidate in (text, text.replace("T", " ")):
            for fmt, size in (
                ("%Y-%m-%d %H:%M:%S", 19),
                ("%Y-%m-%d %H:%M", 16),
                ("%Y-%m-%d", 10),
            ):
                try:
                    return datetime.datetime.strptime(candidate[:size], fmt)
                except ValueError:
                    continue
        try:
            return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            return None

    @staticmethod
    def _clamp_score(
        value: Any, default: int = 50, *, lower: int = 0, upper: int = 100
    ) -> int:
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            number = default
        return max(lower, min(number, upper))

    def _default_emotion_expiry(self, item: EmotionArcRecord) -> str:
        if item.expires_at:
            return item.expires_at
        if item.layer == "relationship":
            return ""
        created = self._parse_emotion_time(item.created_at) or datetime.datetime.now()
        if item.layer == "daily":
            hours = 48
        else:
            hours = 18 if item.source in {"daily_generation", "state"} else 8
        return (created + datetime.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

    def _compose_emotion_arc(self, row: sqlite3.Row) -> EmotionArcRecord:
        return EmotionArcRecord(
            id=int(row["id"] or 0),
            scope=self._text(row["scope"]),
            date=self._text(row["date"]),
            label=self._text(row["label"]),
            valence=max(-100, min(int(row["valence"] or 0), 100)),
            arousal=self._clamp_score(row["arousal"]),
            intensity=self._clamp_score(row["intensity"]),
            stability=self._clamp_score(row["stability"]),
            trigger=self._text(row["trigger"]),
            evidence=self._text(row["evidence"]),
            influence=self._text(row["influence"]),
            expires_at=self._text(row["expires_at"]),
            status=self._text(row["status"]) or "active",
            layer=self._text(row["layer"]) or "transient",
            baseline=max(0.0, min(float(row["baseline"] or 0.0), 100.0)),
            half_life_minutes=max(float(row["half_life_minutes"] or 240.0), 1.0),
            last_decay_at=self._text(row["last_decay_at"]),
            source=self._text(row["source"]) or "state",
            created_at=self._text(row["created_at"]),
            updated_at=self._text(row["updated_at"]),
        )

    def _decay_emotion_arc(
        self, item: EmotionArcRecord, now: datetime.datetime | None = None
    ) -> EmotionArcRecord:
        if item.status != "active":
            return item
        now = now or datetime.datetime.now()
        expires_at = self._parse_emotion_time(item.expires_at)
        reference_at = self._parse_emotion_time(
            item.last_decay_at
        ) or self._parse_emotion_time(item.updated_at)
        if expires_at and expires_at <= now:
            return replace(
                item,
                intensity=0,
                status="expired",
                last_decay_at=self._emotion_now_text(),
            )
        if not reference_at:
            return item
        elapsed_minutes = max((now - reference_at).total_seconds() / 60.0, 0.0)
        factor = 0.5 ** (elapsed_minutes / max(item.half_life_minutes, 1.0))
        decayed = item.baseline + (item.intensity - item.baseline) * factor
        return replace(
            item,
            intensity=max(0, min(round(decayed), 100)),
            last_decay_at=self._emotion_now_text(),
        )

    def _compact_active_emotion_arcs(
        self, *, scope: str, layer: str, label: str, keep_id: int
    ) -> int:
        scope_text = self._text(scope)
        label_text = self._text(label)
        if not label_text or not keep_id:
            return 0
        cursor = self._conn.execute(
            """
            UPDATE emotion_arcs
            SET status = 'superseded',
                intensity = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'active'
              AND scope = ?
              AND layer = ?
              AND label = ?
              AND id <> ?
            """,
            (scope_text, self._text(layer) or "transient", label_text, keep_id),
        )
        return int(cursor.rowcount or 0)

    async def save_emotion_arc(
        self, arc: EmotionArcRecord | dict
    ) -> EmotionArcRecord | None:
        item = EmotionArcRecord.from_value(
            arc.as_dict() if isinstance(arc, EmotionArcRecord) else arc
        )
        if not item:
            return None
        now_text = self._emotion_now_text()
        if not item.created_at:
            item.created_at = now_text
        if not item.updated_at:
            item.updated_at = now_text
        if not item.last_decay_at:
            item.last_decay_at = now_text
        item.expires_at = self._default_emotion_expiry(item)

        def dbwork():
            existing = None
            if item.id:
                existing = self._conn.execute(
                    "SELECT * FROM emotion_arcs WHERE id = ?", (int(item.id),)
                ).fetchone()
            if existing is None:
                existing = self._conn.execute(
                    """
                    SELECT * FROM emotion_arcs
                    WHERE scope = ?
                      AND date = ?
                      AND label = ?
                      AND source = ?
                      AND trigger = ?
                      AND evidence = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (
                        self._text(item.scope),
                        self._text(item.date),
                        self._text(item.label),
                        self._text(item.source) or "state",
                        self._text(item.trigger),
                        self._text(item.evidence),
                    ),
                ).fetchone()
            if existing:
                arc_id = int(existing["id"] or item.id or 0)
                self._conn.execute(
                    """
                    UPDATE emotion_arcs
                    SET valence = ?,
                        arousal = ?,
                        intensity = ?,
                        stability = ?,
                        influence = COALESCE(NULLIF(?, ''), influence),
                        expires_at = ?,
                        status = ?,
                        layer = ?,
                        baseline = ?,
                        half_life_minutes = ?,
                        last_decay_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        max(-100, min(int(item.valence or 0), 100)),
                        self._clamp_score(item.arousal),
                        self._clamp_score(item.intensity),
                        self._clamp_score(item.stability),
                        self._text(item.influence),
                        self._text(item.expires_at),
                        self._text(item.status) or "active",
                        self._text(item.layer) or "transient",
                        max(0.0, min(float(item.baseline), 100.0)),
                        max(float(item.half_life_minutes), 1.0),
                        self._text(item.last_decay_at),
                        arc_id,
                    ),
                )
                self._compact_active_emotion_arcs(
                    scope=self._text(item.scope),
                    layer=self._text(item.layer),
                    label=self._text(item.label),
                    keep_id=arc_id,
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM emotion_arcs WHERE id = ?", (arc_id,)
                ).fetchone()
                return self._compose_emotion_arc(row) if row else None

            cursor = self._conn.execute(
                """
                INSERT INTO emotion_arcs(
                    scope, date, label, valence, arousal, intensity, stability,
                    trigger, evidence, influence, expires_at, status, layer,
                    baseline, half_life_minutes, last_decay_at, source,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    self._text(item.scope),
                    self._text(item.date),
                    self._text(item.label) or "情绪波动",
                    max(-100, min(int(item.valence or 0), 100)),
                    self._clamp_score(item.arousal),
                    self._clamp_score(item.intensity),
                    self._clamp_score(item.stability),
                    self._text(item.trigger),
                    self._text(item.evidence),
                    self._text(item.influence),
                    self._text(item.expires_at),
                    self._text(item.status) or "active",
                    self._text(item.layer) or "transient",
                    max(0.0, min(float(item.baseline), 100.0)),
                    max(float(item.half_life_minutes), 1.0),
                    self._text(item.last_decay_at),
                    self._text(item.source) or "state",
                ),
            )
            arc_id = int(cursor.lastrowid or 0)
            self._compact_active_emotion_arcs(
                scope=self._text(item.scope),
                layer=self._text(item.layer),
                label=self._text(item.label) or "情绪波动",
                keep_id=arc_id,
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM emotion_arcs WHERE id = ?", (arc_id,)
            ).fetchone()
            return self._compose_emotion_arc(row) if row else None

        return await self._run_db(dbwork)

    async def get_emotion_arcs(
        self,
        limit: int = 20,
        *,
        scope: str = "",
        date: str = "",
        active_only: bool = True,
        include_global: bool = True,
    ) -> list[EmotionArcRecord]:
        def dbwork():
            sql = "SELECT * FROM emotion_arcs"
            clauses = []
            params: list[Any] = []
            scope_text = self._text(scope)
            if scope_text:
                if include_global:
                    clauses.append("scope IN (?, '')")
                    params.append(scope_text)
                else:
                    clauses.append("scope = ?")
                    params.append(scope_text)
            if date:
                clauses.append("date = ?")
                params.append(self._text(date))
            if active_only:
                clauses.append("status = 'active'")
                clauses.append(
                    "(expires_at = '' OR expires_at >= datetime('now', 'localtime'))"
                )
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY updated_at DESC, id DESC"
            if limit > 0:
                sql += " LIMIT ?"
                params.append(limit)
            rows = self._conn.execute(sql, tuple(params)).fetchall()
            now = datetime.datetime.now()
            items = [
                item
                for item in (
                    self._decay_emotion_arc(self._compose_emotion_arc(row), now)
                    for row in rows
                )
                if not active_only or item.intensity > 0
            ]
            return items

        return await self._run_db(dbwork)
