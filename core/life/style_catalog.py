from __future__ import annotations

from typing import Any


class StyleCatalogMixin:
    @staticmethod
    def _style_catalog_reference_ids(value: object) -> list[int]:
        if isinstance(value, (str, int, float)):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = []
        result: list[int] = []
        for item in values:
            try:
                item_id = int(item)
            except (TypeError, ValueError):
                continue
            if item_id > 0 and item_id not in result:
                result.append(item_id)
        return result[:6]

    @staticmethod
    def _style_catalog_list(value: object, limit: int = 5) -> list[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
        else:
            values = []
        result = []
        for item in values:
            text = " ".join(str(item or "").strip().split())[:48]
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _style_catalog_item_line(cls, item: Any) -> str:
        kind = "服装" if str(getattr(item, "kind", "")) == "outfit" else "发型"
        attributes = getattr(item, "attributes", {}) or {}
        if not isinstance(attributes, dict):
            attributes = {}
        details = []
        for label, key in (
            ("类别", "category"),
            ("单品", "pieces"),
            ("色彩", "colors"),
            ("图案", "patterns"),
            ("轮廓", "silhouette"),
            ("领口", "neckline"),
            ("袖型", "sleeve"),
            ("长度", "length"),
            ("材质观感", "material_appearance"),
            ("厚度", "thickness"),
            ("露肤程度", "exposure_level"),
            ("风格", "styles"),
            ("季节", "seasons"),
            ("场景", "scenes"),
            ("天气", "weather_fit"),
            ("活动", "activity_fit"),
            ("鞋袜", "footwear"),
            ("配饰", "accessories"),
            ("妆容", "makeup"),
            ("美甲", "nails"),
        ):
            values = cls._style_catalog_list(attributes.get(key), 4)
            if values:
                details.append(f"{label}：{'、'.join(values)}")
        score = float(getattr(item, "preference_score", 0.0) or 0.0)
        title = " ".join(str(getattr(item, "title", "") or "").split())[:80]
        description = " ".join(
            str(getattr(item, "description", "") or "").split()
        )[:260]
        suffix = f"；{'；'.join(details)}" if details else ""
        heading = title or f"{kind}候选"
        return (
            f"- #{int(getattr(item, 'id', 0) or 0)} [{kind}] "
            f"{heading}；{description}{suffix}；偏好分 {score:.1f}"
        )

    async def _style_catalog_context(self, *, limit: int = 10) -> str:
        getter = getattr(self.archive, "get_style_catalog_items", None)
        if not callable(getter):
            return ""
        items = await getter(status="active", limit=max(1, min(limit, 16)))
        if not items:
            return ""
        lines = [
            "## 👗 视觉衣橱候选",
            "以下来自用户明确学习的商品图或造型图，只是新造型灵感，不是当前已经穿上的事实。",
            "需要新造型时才可选择适合当前天气、活动和场景的候选；不合适可以完全不用。",
            "服装与发型分别选择，不能把候选图片中的人物身份、体貌、姿势、场景或品牌当作角色事实。",
            "妆容与美甲是独立的当前事实：只有实际采用候选中对应组成时才写入，换衣不能自动改掉已有美甲。",
        ]
        lines.extend(self._style_catalog_item_line(item) for item in items)
        return "\n".join(lines)

    async def _style_catalog_reference_appearance(
        self, value: object
    ) -> dict[str, str]:
        """读取已明确采用的候选中的独立妆容与美甲字段。"""

        getter = getattr(self.archive, "get_style_catalog_items", None)
        item_ids = self._style_catalog_reference_ids(value)
        if not callable(getter) or not item_ids:
            return {}
        try:
            items = await getter(
                status="active", ids=item_ids, limit=len(item_ids)
            )
        except Exception:
            return {}
        result: dict[str, list[str]] = {"makeup": [], "nails": []}
        for item in items or []:
            if str(getattr(item, "kind", "")).strip().lower() != "outfit":
                continue
            attributes = getattr(item, "attributes", {}) or {}
            if not isinstance(attributes, dict):
                continue
            for key in result:
                for text in self._style_catalog_list(attributes.get(key), 4):
                    if text not in result[key]:
                        result[key].append(text)
        return {
            key: "、".join(values[:4])
            for key, values in result.items()
            if values
        }

    async def _mark_style_catalog_references(self, value: object) -> int:
        marker = getattr(self.archive, "mark_style_catalog_used", None)
        if not callable(marker):
            return 0
        item_ids = self._style_catalog_reference_ids(value)
        return await marker(item_ids) if item_ids else 0


__all__ = ["StyleCatalogMixin"]
