from __future__ import annotations

import json
import sqlite3
from typing import Any, Protocol

from ..models import STYLE_CATALOG_KIND_SET, StyleCatalogItemRecord


class StyleCatalogArchiveComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _compose_style_catalog_item: Any
    _conn: Any
    _run_db: Any
    _style_attributes: Any
    _style_catalog_ids: Any
    _style_catalog_status: Any
    _style_hash_distance: Any
    _text: Any
    _wardrobe_item_state_unlocked: Any
    _synchronize_wardrobe_unlocked: Any
    get_style_catalog_items: Any


class StyleCatalogArchiveComponent:
    def __init__(self, context: StyleCatalogArchiveComponentContext) -> None:
        self.context = context

    @staticmethod
    def _style_catalog_status(value: object) -> str:
        return str(value or "").strip().lower()

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
        wardrobe_reader = getattr(self.context, "_wardrobe_item_state_unlocked", None)
        if callable(wardrobe_reader):
            state = wardrobe_reader(int(row["id"]))
            if state:
                attributes["wardrobe"] = state
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
            status=self.context._style_catalog_status(row["status"] or "active"),
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
        if self.context._style_catalog_status(item.status) not in {"active", "disabled", "rejected"}:
            return None

        def write() -> StyleCatalogItemRecord | None:
            if item.attributes.get("wardrobe_job"):
                existing = self.context._conn.execute(
                    "SELECT * FROM style_catalog_items WHERE source_image_hash=? AND kind=?",
                    (item.source_image_hash, item.kind),
                ).fetchone()
                if existing:
                    prior = json.loads(existing["attributes_json"] or "{}")
                    # 同图恢复或另一任务返回旧图，都不能覆盖已核验资料与用户编辑。
                    if prior.get("wardrobe_job") != item.attributes["wardrobe_job"] or prior.get("design_validation") == "accepted":
                        return self.context._compose_style_catalog_item(existing)
            self.context._conn.execute(
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
                    seen_count = style_catalog_items.seen_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item.kind,
                    self.context._text(item.title),
                    self.context._text(item.description),
                    self.context._text(item.image_path),
                    self.context._text(item.source_url),
                    self.context._text(item.source_scope),
                    self.context._text(item.source_kind),
                    self.context._text(item.source_image_hash),
                    json.dumps(
                        self.context._style_attributes(item.attributes), ensure_ascii=False
                    ),
                    max(0.0, min(float(item.confidence or 0.0), 1.0)),
                    max(-2.0, min(float(item.preference_score or 0.0), 2.0)),
                    self.context._style_catalog_status(item.status) or "active",
                    self.context._text(item.created_at),
                ),
            )
            self.context._synchronize_wardrobe_unlocked(item.source_image_hash)
            self.context._conn.commit()
            row = self.context._conn.execute(
                """
                SELECT * FROM style_catalog_items
                WHERE source_image_hash = ? AND kind = ?
                """,
                (item.source_image_hash, item.kind),
            ).fetchone()
            return self.context._compose_style_catalog_item(row) if row else None

        return await self.context._run_db(write)

    async def get_style_catalog_items(
        self,
        *,
        kind: str = "",
        status: str = "active",
        ids: list[int] | tuple[int, ...] = (),
        source_scope: str = "",
        limit: int = 12,
    ) -> list[StyleCatalogItemRecord]:
        normalized_kind = self.context._text(kind).lower()
        normalized_status = self.context._style_catalog_status(status)
        normalized_scope = self.context._text(source_scope)
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
            if normalized_status == "active":
                clauses.append("COALESCE(json_extract(attributes_json,'$.design_validation'),'') NOT IN ('pending','rejected')")
            if normalized_scope:
                clauses.append("source_scope = ?")
                values.append(normalized_scope)
            if normalized_ids:
                placeholders = ",".join("?" for _ in normalized_ids)
                clauses.append(f"id IN ({placeholders})")
                values.extend(normalized_ids)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = self.context._conn.execute(
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
            return [self.context._compose_style_catalog_item(row) for row in rows]

        return await self.context._run_db(read)

    async def get_style_catalog_counts(
        self, *, status: str = "active"
    ) -> dict[str, int]:
        """按类别统计衣橱条目，不受候选展示数量限制。"""

        normalized_status = self.context._style_catalog_status(status)

        def read() -> dict[str, int]:
            clauses = ""
            values: tuple[str, ...] = ()
            if normalized_status:
                clauses = " WHERE status = ?"
                values = (normalized_status,)
            if normalized_status == "active":
                clauses += " AND COALESCE(json_extract(attributes_json,'$.design_validation'),'') NOT IN ('pending','rejected')"
            rows = self.context._conn.execute(
                f"""
                SELECT kind, COUNT(*) AS item_count
                FROM style_catalog_items{clauses}
                GROUP BY kind
                """,
                values,
            ).fetchall()
            return {
                str(row["kind"] or ""): max(0, int(row["item_count"] or 0))
                for row in rows
                if str(row["kind"] or "") in STYLE_CATALOG_KIND_SET
            }

        return await self.context._run_db(read)

    async def save_wardrobe_understanding(self, records: dict[int, dict]) -> int:
        """合并后台用途理解；资料已被编辑或删除时丢弃旧结果。"""
        from ..life.wardrobefit import understanding_key

        def write() -> int:
            changed = 0
            self.context._conn.execute("BEGIN IMMEDIATE")
            try:
                for item_id, understanding in records.items():
                    row = self.context._conn.execute(
                        "SELECT * FROM style_catalog_items WHERE id=? AND status='active'",
                        (item_id,),
                    ).fetchone()
                    if row is None:
                        continue
                    item = self.context._compose_style_catalog_item(row)
                    if understanding.get("basis") != understanding_key(item):
                        continue
                    attrs = json.loads(row["attributes_json"] or "{}")
                    if attrs.get("wardrobe_understanding") == understanding:
                        continue
                    attrs["wardrobe_understanding"] = understanding
                    self.context._conn.execute(
                        "UPDATE style_catalog_items SET attributes_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (json.dumps(attrs, ensure_ascii=False), item_id),
                    )
                    changed += 1
                self.context._conn.commit()
                return changed
            except BaseException:
                self.context._conn.rollback()
                raise

        return await self.context._run_db(write)

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
            row = self.context._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            return self.context._compose_style_catalog_item(row) if row else None

        return await self.context._run_db(read)

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
        normalized_kind = self.context._text(kind).lower()
        normalized_hash = self.context._text(perceptual_hash).lower()
        if normalized_kind not in STYLE_CATALOG_KIND_SET or not normalized_hash:
            return None
        candidates = await self.context.get_style_catalog_items(
            kind=normalized_kind, status="", limit=500
        )
        ranked = []
        for item in candidates:
            attributes = item.attributes if isinstance(item.attributes, dict) else {}
            candidate_hash = self.context._text(attributes.get("perceptual_hash")).lower()
            distance = self.context._style_hash_distance(normalized_hash, candidate_hash)
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
            existing = self.context._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            if not existing or str(existing["kind"] or "") != incoming.kind:
                return None
            use_incoming = float(incoming.confidence or 0.0) >= float(
                existing["confidence"] or 0.0
            )
            current_status = self.context._style_catalog_status(
                existing["status"] or "active"
            )
            self.context._conn.execute(
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
                    self.context._text(incoming.title),
                    int(use_incoming),
                    self.context._text(incoming.description),
                    self.context._text(incoming.source_url),
                    self.context._text(incoming.source_url),
                    self.context._text(incoming.source_scope),
                    self.context._text(incoming.source_scope),
                    self.context._text(incoming.source_kind),
                    self.context._text(incoming.source_kind),
                    int(use_incoming),
                    json.dumps(
                        self.context._style_attributes(incoming.attributes), ensure_ascii=False
                    ),
                    max(0.0, min(float(incoming.confidence or 0.0), 1.0)),
                    current_status,
                    normalized_id,
                ),
            )
            saved_hash = self.context._conn.execute(
                "SELECT source_image_hash FROM style_catalog_items WHERE id=?", (normalized_id,)
            ).fetchone()
            if saved_hash:
                self.context._synchronize_wardrobe_unlocked(saved_hash[0])
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            return self.context._compose_style_catalog_item(row) if row else None

        return await self.context._run_db(write)

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
        normalized_status = self.context._style_catalog_status(status)
        if normalized_id <= 0 or normalized_status not in {
            "active",
            "rejected",
            "disabled",
        }:
            return None

        def write() -> StyleCatalogItemRecord | None:
            cursor = self.context._conn.execute(
                """
                UPDATE style_catalog_items
                SET title = ?, description = ?, attributes_json = ?,
                    confidence = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    self.context._text(title),
                    self.context._text(description),
                    json.dumps(self.context._style_attributes(attributes), ensure_ascii=False),
                    max(0.0, min(float(confidence or 0.0), 1.0)),
                    normalized_status,
                    normalized_id,
                ),
            )
            if int(cursor.rowcount or 0) <= 0:
                return None
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            return self.context._compose_style_catalog_item(row) if row else None

        return await self.context._run_db(write)

    async def set_style_catalog_design_result(self, item_ids: list[int], *, job_id: str, verdict: str, reason: str) -> None:
        """只更新属于本次生成的核验回执，保留用户编辑和启停状态。"""
        if verdict not in {"accepted", "rejected"}:
            raise ValueError("无效设计核验状态")
        def write():
            self.context._conn.execute("BEGIN IMMEDIATE")
            try:
                for item_id in item_ids:
                    row = self.context._conn.execute("SELECT attributes_json FROM style_catalog_items WHERE id=?", (item_id,)).fetchone()
                    attributes = json.loads(row[0]) if row else {}
                    if attributes.get("wardrobe_job") != job_id:
                        continue
                    attributes.update(design_validation=verdict, design_validation_reason=reason[:500])
                    self.context._conn.execute("UPDATE style_catalog_items SET attributes_json=? WHERE id=?",
                                               (json.dumps(attributes, ensure_ascii=False), item_id))
                self.context._conn.commit()
            except BaseException:
                self.context._conn.rollback()
                raise
        await self.context._run_db(write)

    async def set_style_catalog_status(
        self,
        item_ids: list[int] | tuple[int, ...],
        status: str,
    ) -> int:
        normalized_status = self.context._style_catalog_status(status)
        if normalized_status not in {"active", "rejected", "disabled"}:
            return 0
        normalized = self.context._style_catalog_ids(item_ids)
        if not normalized:
            return 0

        def write() -> int:
            placeholders = ",".join("?" for _ in normalized)
            cursor = self.context._conn.execute(
                f"""
                UPDATE style_catalog_items
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                (normalized_status, *normalized),
            )
            self.context._conn.commit()
            return max(int(cursor.rowcount or 0), 0)

        return await self.context._run_db(write)

    async def delete_style_catalog_items(
        self, item_ids: list[int] | tuple[int, ...]
    ) -> int:
        normalized = self.context._style_catalog_ids(item_ids)
        if not normalized:
            return 0

        def write() -> int:
            placeholders = ",".join("?" for _ in normalized)
            cursor = self.context._conn.execute(
                f"DELETE FROM style_catalog_items WHERE id IN ({placeholders})",
                tuple(normalized),
            )
            self.context._conn.commit()
            return max(int(cursor.rowcount or 0), 0)

        return await self.context._run_db(write)

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
        scope = self.context._text(source_scope)
        if not scope:
            return []
        safe_limit = max(1, min(int(limit or 6), 30))

        def read() -> list[StyleCatalogItemRecord]:
            rows = self.context._conn.execute(
                """
                SELECT * FROM style_catalog_items
                WHERE source_scope = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (scope, safe_limit),
            ).fetchall()
            return [self.context._compose_style_catalog_item(row) for row in rows]

        return await self.context._run_db(read)

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
        normalized_sentiment = self.context._text(sentiment).lower()
        if normalized_sentiment not in {"prefer", "dislike", "neutral", "disable"}:
            normalized_sentiment = "neutral"
        try:
            delta = float(score_delta or 0.0)
        except (TypeError, ValueError):
            delta = 0.0
        delta = max(-1.0, min(delta, 1.0))
        normalized_status = self.context._style_catalog_status(status)
        if normalized_status not in {"active", "rejected", "disabled"}:
            normalized_status = ""

        def write() -> StyleCatalogItemRecord | None:
            existing = self.context._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            if not existing:
                return None
            next_score = max(
                -2.0, min(float(existing["preference_score"] or 0.0) + delta, 2.0)
            )
            next_status = normalized_status or self.context._style_catalog_status(
                existing["status"] or "active"
            )
            self.context._conn.execute(
                """
                INSERT INTO style_catalog_feedback(
                    item_id, scope, feedback, sentiment, score_delta, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_id,
                    self.context._text(scope),
                    self.context._text(feedback),
                    normalized_sentiment,
                    delta,
                    self.context._text(reason),
                ),
            )
            self.context._conn.execute(
                """
                UPDATE style_catalog_items
                SET preference_score = ?, feedback_count = feedback_count + 1,
                    status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (next_score, next_status, normalized_id),
            )
            self.context._conn.commit()
            row = self.context._conn.execute(
                "SELECT * FROM style_catalog_items WHERE id = ?", (normalized_id,)
            ).fetchone()
            return self.context._compose_style_catalog_item(row) if row else None

        return await self.context._run_db(write)

    async def mark_style_catalog_used(
        self, item_ids: list[int] | tuple[int, ...]
    ) -> int:
        normalized = self.context._style_catalog_ids(item_ids)
        if not normalized:
            return 0

        def write() -> int:
            placeholders = ",".join("?" for _ in normalized)
            rows = self.context._conn.execute(
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
                self.context._conn.execute(
                    """
                    UPDATE style_catalog_items
                    SET attributes_json = ?, last_used_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (json.dumps(attributes, ensure_ascii=False), int(row["id"])),
                )
            self.context._conn.commit()
            return len(rows)

        return await self.context._run_db(write)


__all__ = ["StyleCatalogArchiveComponent"]
