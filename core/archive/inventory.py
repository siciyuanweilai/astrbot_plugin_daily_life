import sqlite3
import uuid

from ..presets import DEFAULT_CATALOG_POOLS
from ..models import CatalogItemRecord, HairStyleRecord


class CatalogArchiveMixin:
    async def get_custom_catalog_items(self, include_disabled: bool = False) -> dict[str, list[CatalogItemRecord]]:
        async with self._lock:
            sql = "SELECT * FROM custom_catalog_items"
            if not include_disabled:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY category, sort_order, updated_at, item_id"
            rows = self._conn.execute(sql).fetchall()
            items: dict[str, list[CatalogItemRecord]] = {category: [] for category in DEFAULT_CATALOG_POOLS}
            for row in rows:
                record = self._compose_catalog_item(row)
                if record.category in DEFAULT_CATALOG_POOLS:
                    items.setdefault(record.category, []).append(record)
            return items
    async def save_custom_catalog_item(self, item: CatalogItemRecord) -> CatalogItemRecord:
        async with self._lock:
            item = self._normalize_catalog_item_unlocked(item)
            self._conn.execute(
                """
                INSERT INTO custom_catalog_items(
                    category, item_id, text, enabled, sort_order, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(category, item_id) DO UPDATE SET
                    text = excluded.text,
                    enabled = excluded.enabled,
                    sort_order = excluded.sort_order,
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item.category,
                    item.item_id,
                    item.text,
                    self._flag(item.enabled),
                    item.sort_order,
                    item.source,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM custom_catalog_items WHERE category = ? AND item_id = ?",
                (item.category, item.item_id),
            ).fetchone()
            return self._compose_catalog_item(row)
    async def set_custom_catalog_item_enabled(self, category: str, item_id: str, enabled: bool) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE custom_catalog_items
                SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE category = ? AND item_id = ?
                """,
                (self._flag(enabled), self._text(category), self._text(item_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    async def delete_custom_catalog_item(self, category: str, item_id: str) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM custom_catalog_items WHERE category = ? AND item_id = ?",
                (self._text(category), self._text(item_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    def _compose_catalog_item(self, row: sqlite3.Row | None) -> CatalogItemRecord:
        if row is None:
            return CatalogItemRecord()
        return CatalogItemRecord(
            category=row["category"],
            item_id=row["item_id"],
            text=row["text"],
            enabled=bool(row["enabled"]),
            sort_order=int(row["sort_order"] or 0),
            source=row["source"],
        )
    def _normalize_catalog_item_unlocked(self, item: CatalogItemRecord) -> CatalogItemRecord:
        item.category = self._text(item.category)
        item.item_id = self._text(item.item_id)
        item.text = self._text(item.text)
        item.source = self._text(item.source) or "custom"
        if item.category not in DEFAULT_CATALOG_POOLS:
            raise ValueError("素材分类不存在")
        if not item.text:
            raise ValueError("素材内容不能为空")
        if not item.item_id:
            item.item_id = f"item_{uuid.uuid4().hex[:12]}"
        row = self._conn.execute(
            "SELECT sort_order FROM custom_catalog_items WHERE category = ? AND item_id = ?",
            (item.category, item.item_id),
        ).fetchone()
        if row is None and int(item.sort_order or 0) <= 0:
            max_row = self._conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS value FROM custom_catalog_items WHERE category = ?",
                (item.category,),
            ).fetchone()
            item.sort_order = int(max_row["value"] or 0) + 1
        else:
            item.sort_order = max(int(item.sort_order or 0), 0)
        return item
    async def get_custom_hair_styles(self, include_disabled: bool = False) -> dict[str, HairStyleRecord]:
        async with self._lock:
            sql = "SELECT * FROM custom_hair_styles"
            if not include_disabled:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY sort_order, updated_at, style_id"
            rows = self._conn.execute(sql).fetchall()
            return {row["style_id"]: self._compose_hair_style(row) for row in rows}
    async def save_custom_hair_style(self, style: HairStyleRecord) -> HairStyleRecord:
        async with self._lock:
            style = self._normalize_hair_style_unlocked(style)
            self._conn.execute(
                """
                INSERT INTO custom_hair_styles(
                    style_id, name, enabled, sort_order, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(style_id) DO UPDATE SET
                    name = excluded.name,
                    enabled = excluded.enabled,
                    sort_order = excluded.sort_order,
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    style.style_id,
                    style.name,
                    self._flag(style.enabled),
                    style.sort_order,
                    style.source,
                ),
            )
            self._replace_hair_options_unlocked(style.style_id, style.hairstyles)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM custom_hair_styles WHERE style_id = ?",
                (style.style_id,),
            ).fetchone()
            return self._compose_hair_style(row)
    async def set_custom_hair_style_enabled(self, style_id: str, enabled: bool) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE custom_hair_styles
                SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE style_id = ?
                """,
                (self._flag(enabled), self._text(style_id)),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    async def delete_custom_hair_style(self, style_id: str) -> bool:
        async with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM custom_hair_styles WHERE style_id = ?",
                (self._text(style_id),),
            )
            self._conn.commit()
            return cursor.rowcount > 0
    def _compose_hair_style(self, row: sqlite3.Row | None) -> HairStyleRecord:
        if row is None:
            return HairStyleRecord()
        style_id = row["style_id"]
        return HairStyleRecord(
            style_id=style_id,
            name=row["name"],
            hairstyles=self._get_ordered_texts_unlocked("custom_hair_options", "style_id", style_id, "hairstyle"),
            enabled=bool(row["enabled"]),
            sort_order=int(row["sort_order"] or 0),
            source=row["source"],
        )
    def _normalize_hair_style_unlocked(self, style: HairStyleRecord) -> HairStyleRecord:
        style.style_id = self._text(style.style_id)
        style.name = self._text(style.name)
        style.source = self._text(style.source) or "custom"
        style.hairstyles = [self._text(item) for item in style.hairstyles if self._text(item)]
        if not style.name:
            raise ValueError("发型风格名称不能为空")
        if not style.hairstyles:
            raise ValueError("发型列表不能为空")
        if not style.style_id:
            style.style_id = f"hair_{uuid.uuid4().hex[:12]}"
        row = self._conn.execute(
            "SELECT sort_order FROM custom_hair_styles WHERE style_id = ?",
            (style.style_id,),
        ).fetchone()
        if row is None and int(style.sort_order or 0) <= 0:
            max_row = self._conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS value FROM custom_hair_styles",
            ).fetchone()
            style.sort_order = int(max_row["value"] or 0) + 1
        else:
            style.sort_order = max(int(style.sort_order or 0), 0)
        return style
    def _replace_hair_options_unlocked(self, style_id: str, hairstyles: list[str]) -> None:
        self._conn.execute("DELETE FROM custom_hair_options WHERE style_id = ?", (style_id,))
        for idx, hairstyle in enumerate(hairstyles):
            text = self._text(hairstyle)
            if text:
                self._conn.execute(
                    "INSERT INTO custom_hair_options(style_id, sort_order, hairstyle) VALUES (?, ?, ?)",
                    (style_id, idx, text),
                )

