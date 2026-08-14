from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import STYLE_CATALOG_KIND_SET, StyleCatalogItemRecord


class StyleCatalogArchiveMixin:
    @staticmethod
    def _style_attributes(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    def _compose_style_catalog_item(
        self, row: sqlite3.Row
    ) -> StyleCatalogItemRecord:
        try:
            attributes = json.loads(row["attributes_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            attributes = {}
        return StyleCatalogItemRecord(
            id=int(row["id"] or 0),
            kind=str(row["kind"] or "outfit"),
            title=str(row["title"] or ""),
            description=str(row["description"] or ""),
            image_path=str(row["image_path"] or ""),
            source_url=str(row["source_url"] or ""),
            source_scope=str(row["source_scope"] or ""),
            source_kind=str(row["source_kind"] or "user_image"),
            source_image_hash=str(row["source_image_hash"] or ""),
            attributes=attributes if isinstance(attributes, dict) else {},
            confidence=float(row["confidence"] or 0.0),
            preference_score=float(row["preference_score"] or 0.0),
            feedback_count=int(row["feedback_count"] or 0),
            seen_count=int(row["seen_count"] or 1),
            status=str(row["status"] or "active"),
            last_used_at=str(row["last_used_at"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    async def upsert_style_catalog_item(
        self, record: StyleCatalogItemRecord | dict
    ) -> StyleCatalogItemRecord | None:
        item = StyleCatalogItemRecord.from_value(record)
        if item is None:
            return None

        def write() -> StyleCatalogItemRecord | None:
            self._conn.execute(
                """
                INSERT INTO style_catalog_items(
                    kind, title, description, image_path, source_url, source_scope,
                    source_kind, source_image_hash, attributes_json, confidence,
                    preference_score, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
                ON CONFLICT(source_image_hash, kind) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    image_path = CASE WHEN excluded.image_path != '' THEN excluded.image_path ELSE style_catalog_items.image_path END,
                    source_url = CASE WHEN excluded.source_url != '' THEN excluded.source_url ELSE style_catalog_items.source_url END,
                    source_scope = CASE WHEN excluded.source_scope != '' THEN excluded.source_scope ELSE style_catalog_items.source_scope END,
                    source_kind = excluded.source_kind,
                    attributes_json = excluded.attributes_json,
                    confidence = MAX(style_catalog_items.confidence, excluded.confidence),
                    status = CASE
                        WHEN style_catalog_items.status = 'pending'
                         AND excluded.status = 'active'
                        THEN 'active'
                        ELSE style_catalog_items.status
                    END,
                    seen_count = style_catalog_items.seen_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item.kind,
                    self._text(item.title),
                    self._text(item.description),
                    self._text(item.image_path),
                    self._text(item.source_url),
                    self._text(item.source_scope),
                    self._text(item.source_kind),
                    self._text(item.source_image_hash),
                    json.dumps(
                        self._style_attributes(item.attributes), ensure_ascii=False
                    ),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    max(-2.0, min(float(item.preference_score or 0.0), 2.0)),
                    self._text(item.status) or "active",
                    self._text(item.created_at),
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                """
                SELECT * FROM style_catalog_items
                WHERE source_image_hash = ? AND kind = ?
                """,
                (item.source_image_hash, item.kind),
            ).fetchone()
            return self._compose_style_catalog_item(row) if row else None

        return await self._run_db(write)

    async def get_style_catalog_items(
        self,
        *,
        kind: str = "",
        status: str = "active",
        ids: list[int] | tuple[int, ...] = (),
        source_scope: str = "",
        limit: int = 12,
    ) -> list[StyleCatalogItemRecord]:
        normalized_kind = self._text(kind).lower()
        normalized_status = self._text(status).lower()
        normalized_scope = self._text(source_scope)
        normalized_ids = []
        for value in ids or ():
            try:
                item_id = int(value)
            except (TypeError, ValueError):
                continue
            if item_id > 0 and item_id not in normalized_ids:
                normalized_ids.append(item_id)
        safe_limit = max(1, min(int(limit or 12), 500))

        def read() -> list[StyleCatalogItemRecord]:
            clauses = []
            values: list[Any] = []
            if normalized_kind in STYLE_CATALOG_KIND_SET:
                clauses.append("kind = ?")
                values.append(normalized_kind)
            if normalized_status:
                clauses.append("status = ?")
                values.append(normalized_status)
            if normalized_scope:
                clauses.append("source_scope = ?")
                values.append(normalized_scope)
            if normalized_ids:
                placeholders = ",".join("?" for _ in normalized_ids)
                clauses.append(f"id IN ({placeholders})")
                values.extend(normalized_ids)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = self._conn.execute(
                f"""
                SELECT * FROM style_catalog_items{where}
                ORDER BY CASE
                           WHEN last_used_at != ''
                            AND datetime(last_used_at) >= datetime('now', '-48 hours')
                           THEN 1 ELSE 0
                         END ASC,
                         preference_score DESC,
                         CASE WHEN last_used_at = '' THEN 0 ELSE 1 END ASC,
                         datetime(NULLIF(last_used_at, '')) ASC,
                         feedback_count DESC, updated_at DESC, id DESC
                LIMIT ?
                """,
                (*values, safe_limit),
            ).fetchall()
            return [self._compose_style_catalog_item(row) for row in rows]

        return await self._run_db(read)

    async def get_style_catalog_item(
        self, item_id: int
    ) -> StyleCatalogItemRecord | None:
        try:
            normalized_id = int(item_id)
        except (TypeError, ValueError):
            return None
        if normalized_id <= 0:
            return None

        def read() -> StyleCatalogItemRecord | None:
            row = self._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            return self._compose_style_catalog_item(row) if row else None

        return await self._run_db(read)

    @staticmethod
    def _style_hash_distance(left: str, right: str) -> int:
        try:
            return (int(left, 16) ^ int(right, 16)).bit_count()
        except (TypeError, ValueError):
            return 10_000

    async def find_similar_style_catalog_item(
        self,
        kind: str,
        perceptual_hash: str,
        *,
        max_distance: int = 5,
    ) -> StyleCatalogItemRecord | None:
        normalized_kind = self._text(kind).lower()
        normalized_hash = self._text(perceptual_hash).lower()
        if normalized_kind not in STYLE_CATALOG_KIND_SET or not normalized_hash:
            return None
        candidates = await self.get_style_catalog_items(
            kind=normalized_kind, status="", limit=500
        )
        ranked = []
        for item in candidates:
            attributes = item.attributes if isinstance(item.attributes, dict) else {}
            candidate_hash = self._text(attributes.get("perceptual_hash")).lower()
            distance = self._style_hash_distance(normalized_hash, candidate_hash)
            if distance <= max(0, int(max_distance)):
                ranked.append((distance, -float(item.confidence or 0.0), item))
        return min(ranked, default=(0, 0.0, None), key=lambda value: value[:2])[2]

    async def merge_style_catalog_item(
        self,
        item_id: int,
        record: StyleCatalogItemRecord | dict,
    ) -> StyleCatalogItemRecord | None:
        incoming = StyleCatalogItemRecord.from_value(record)
        try:
            normalized_id = int(item_id)
        except (TypeError, ValueError):
            return None
        if normalized_id <= 0 or incoming is None:
            return None

        def write() -> StyleCatalogItemRecord | None:
            existing = self._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            if not existing or str(existing["kind"] or "") != incoming.kind:
                return None
            use_incoming = float(incoming.confidence or 0.0) >= float(
                existing["confidence"] or 0.0
            )
            current_status = str(existing["status"] or "active")
            next_status = (
                incoming.status
                if current_status == "pending" and incoming.status == "active"
                else current_status
            )
            self._conn.execute(
                """
                UPDATE style_catalog_items
                SET title = CASE WHEN ? THEN ? ELSE title END,
                    description = CASE WHEN ? THEN ? ELSE description END,
                    source_url = CASE WHEN ? != '' THEN ? ELSE source_url END,
                    source_scope = CASE WHEN ? != '' THEN ? ELSE source_scope END,
                    source_kind = CASE WHEN ? != '' THEN ? ELSE source_kind END,
                    attributes_json = CASE WHEN ? THEN ? ELSE attributes_json END,
                    confidence = MAX(confidence, ?), status = ?,
                    seen_count = seen_count + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(use_incoming),
                    self._text(incoming.title),
                    int(use_incoming),
                    self._text(incoming.description),
                    self._text(incoming.source_url),
                    self._text(incoming.source_url),
                    self._text(incoming.source_scope),
                    self._text(incoming.source_scope),
                    self._text(incoming.source_kind),
                    self._text(incoming.source_kind),
                    int(use_incoming),
                    json.dumps(
                        self._style_attributes(incoming.attributes), ensure_ascii=False
                    ),
                    max(0.0, min(float(incoming.confidence or 0.0), 1.0)),
                    next_status,
                    normalized_id,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            return self._compose_style_catalog_item(row) if row else None

        return await self._run_db(write)

    async def revise_style_catalog_item(
        self,
        item_id: int,
        *,
        title: str,
        description: str,
        attributes: dict[str, Any],
        confidence: float,
        status: str,
    ) -> StyleCatalogItemRecord | None:
        try:
            normalized_id = int(item_id)
        except (TypeError, ValueError):
            return None
        normalized_status = self._text(status).lower()
        if normalized_id <= 0 or normalized_status not in {
            "active",
            "pending",
            "rejected",
            "archived",
        }:
            return None

        def write() -> StyleCatalogItemRecord | None:
            cursor = self._conn.execute(
                """
                UPDATE style_catalog_items
                SET title = ?, description = ?, attributes_json = ?,
                    confidence = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    self._text(title),
                    self._text(description),
                    json.dumps(self._style_attributes(attributes), ensure_ascii=False),
                    max(0.0, min(float(confidence or 0.0), 1.0)),
                    normalized_status,
                    normalized_id,
                ),
            )
            if int(cursor.rowcount or 0) <= 0:
                return None
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            return self._compose_style_catalog_item(row) if row else None

        return await self._run_db(write)

    async def set_style_catalog_status(
        self,
        item_ids: list[int] | tuple[int, ...],
        status: str,
    ) -> int:
        normalized_status = self._text(status).lower()
        if normalized_status not in {"active", "pending", "rejected", "archived"}:
            return 0
        normalized = self._style_catalog_ids(item_ids)
        if not normalized:
            return 0

        def write() -> int:
            placeholders = ",".join("?" for _ in normalized)
            cursor = self._conn.execute(
                f"""
                UPDATE style_catalog_items
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                (normalized_status, *normalized),
            )
            self._conn.commit()
            return max(int(cursor.rowcount or 0), 0)

        return await self._run_db(write)

    async def delete_style_catalog_items(
        self, item_ids: list[int] | tuple[int, ...]
    ) -> int:
        normalized = self._style_catalog_ids(item_ids)
        if not normalized:
            return 0

        def write() -> int:
            placeholders = ",".join("?" for _ in normalized)
            cursor = self._conn.execute(
                f"DELETE FROM style_catalog_items WHERE id IN ({placeholders})",
                tuple(normalized),
            )
            self._conn.commit()
            return max(int(cursor.rowcount or 0), 0)

        return await self._run_db(write)

    @staticmethod
    def _style_catalog_ids(values: object) -> list[int]:
        result: list[int] = []
        for value in values or ():
            try:
                item_id = int(value)
            except (TypeError, ValueError):
                continue
            if item_id > 0 and item_id not in result:
                result.append(item_id)
        return result[:500]

    async def get_recent_style_catalog_items(
        self, source_scope: str, *, limit: int = 6
    ) -> list[StyleCatalogItemRecord]:
        scope = self._text(source_scope)
        if not scope:
            return []
        safe_limit = max(1, min(int(limit or 6), 30))

        def read() -> list[StyleCatalogItemRecord]:
            rows = self._conn.execute(
                """
                SELECT * FROM style_catalog_items
                WHERE source_scope = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (scope, safe_limit),
            ).fetchall()
            return [self._compose_style_catalog_item(row) for row in rows]

        return await self._run_db(read)

    async def add_style_catalog_feedback(
        self,
        item_id: int,
        *,
        scope: str = "",
        feedback: str = "",
        sentiment: str = "neutral",
        score_delta: float = 0.0,
        reason: str = "",
        status: str = "",
    ) -> StyleCatalogItemRecord | None:
        try:
            normalized_id = int(item_id)
        except (TypeError, ValueError):
            return None
        if normalized_id <= 0:
            return None
        normalized_sentiment = self._text(sentiment).lower()
        if normalized_sentiment not in {"prefer", "dislike", "neutral", "archive"}:
            normalized_sentiment = "neutral"
        try:
            delta = float(score_delta or 0.0)
        except (TypeError, ValueError):
            delta = 0.0
        delta = max(-1.0, min(delta, 1.0))
        normalized_status = self._text(status).lower()
        if normalized_status not in {"active", "pending", "rejected", "archived"}:
            normalized_status = ""

        def write() -> StyleCatalogItemRecord | None:
            existing = self._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            if not existing:
                return None
            next_score = max(
                -2.0, min(float(existing["preference_score"] or 0.0) + delta, 2.0)
            )
            next_status = normalized_status or str(existing["status"] or "active")
            self._conn.execute(
                """
                INSERT INTO style_catalog_feedback(
                    item_id, scope, feedback, sentiment, score_delta, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_id,
                    self._text(scope),
                    self._text(feedback),
                    normalized_sentiment,
                    delta,
                    self._text(reason),
                ),
            )
            self._conn.execute(
                """
                UPDATE style_catalog_items
                SET preference_score = ?, feedback_count = feedback_count + 1,
                    status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (next_score, next_status, normalized_id),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            return self._compose_style_catalog_item(row) if row else None

        return await self._run_db(write)

    async def mark_style_catalog_used(
        self, item_ids: list[int] | tuple[int, ...]
    ) -> int:
        normalized = self._style_catalog_ids(item_ids)
        if not normalized:
            return 0

        def write() -> int:
            placeholders = ",".join("?" for _ in normalized)
            rows = self._conn.execute(
                f"SELECT id, attributes_json FROM style_catalog_items "
                f"WHERE status = 'active' AND id IN ({placeholders})",
                tuple(normalized),
            ).fetchall()
            for row in rows:
                try:
                    attributes = json.loads(row["attributes_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    attributes = {}
                if not isinstance(attributes, dict):
                    attributes = {}
                try:
                    used_count = int(attributes.get("used_count") or 0)
                except (TypeError, ValueError):
                    used_count = 0
                attributes["used_count"] = max(0, used_count) + 1
                self._conn.execute(
                    """
                    UPDATE style_catalog_items
                    SET attributes_json = ?, last_used_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (json.dumps(attributes, ensure_ascii=False), int(row["id"])),
                )
            self._conn.commit()
            return len(rows)

        return await self._run_db(write)


__all__ = ["StyleCatalogArchiveMixin"]
