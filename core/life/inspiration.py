from __future__ import annotations

from typing import Any

from ..models import (
    STYLE_CATALOG_CLOTHING_KINDS,
    STYLE_CATALOG_KINDS,
    STYLE_CATALOG_KIND_LABELS,
)


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
        return result[:8]

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
        kind = STYLE_CATALOG_KIND_LABELS.get(
            str(getattr(item, "kind", "")), "造型"
        )
        attributes = getattr(item, "attributes", {}) or {}
        if not isinstance(attributes, dict):
            attributes = {}
        details = []
        for label, key in (
            ("类别", "category"),
            ("服饰类型", "garment_type"),
            ("单品", "pieces"),
            ("组成", "items"),
            ("叠穿", "layers"),
            ("色彩", "colors"),
            ("图案", "patterns"),
            ("轮廓", "silhouette"),
            ("领口", "neckline"),
            ("袖型", "sleeve"),
            ("长度", "length"),
            ("腰线", "waist"),
            ("版型", "fit"),
            ("下摆", "hem"),
            ("材质观感", "material_appearance"),
            ("厚度", "thickness"),
            ("露肤程度", "exposure_level"),
            ("风格", "styles"),
            ("季节", "seasons"),
            ("场景", "scenes"),
            ("天气", "weather_fit"),
            ("活动", "activity_fit"),
            ("鞋袜", "footwear"),
            ("袜子", "socks"),
            ("配饰", "accessories"),
            ("位置", "placement"),
            ("妆效", "finish"),
            ("底妆", "base"),
            ("眉形", "brows"),
            ("眼妆", "eyes"),
            ("腮红", "cheeks"),
            ("唇妆", "lips"),
            ("甲型", "shape"),
            ("设计", "designs"),
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
        safe_limit = max(1, min(limit, 24))
        candidates = await getter(
            status="active", limit=max(64, safe_limit * len(STYLE_CATALOG_KINDS))
        )
        grouped = {
            kind: [item for item in candidates if getattr(item, "kind", "") == kind]
            for kind in STYLE_CATALOG_KINDS
        }
        items = []
        for kind in STYLE_CATALOG_KINDS:
            if grouped[kind]:
                items.append(grouped[kind].pop(0))
        while len(items) < safe_limit:
            added = False
            for kind in STYLE_CATALOG_KINDS:
                if grouped[kind] and len(items) < safe_limit:
                    items.append(grouped[kind].pop(0))
                    added = True
            if not added:
                break
        if not items:
            return ""
        lines = [
            "## 👗 视觉衣橱候选",
            "以下来自用户明确学习的商品图或造型图，只是新造型灵感，不是当前已经穿上的事实。",
            "需要新造型时才可选择适合当前天气、活动和场景的候选；不合适可以完全不用。",
            "可以采用一条完整套装，也可以组合上装、下装、鞋袜和配饰；不要同时选取语义重复的整套与单品。",
            "发型、妆容和美甲必须分别选择，不能把候选图片中的人物身份、体貌、姿势、场景或品牌当作角色事实。",
            "只有实际采用对应类别时才改变该外观组成；局部换衣不能自动改掉发型、妆容或美甲。",
        ]
        lines.extend(self._style_catalog_item_line(item) for item in items)
        return "\n".join(lines)

    async def _style_catalog_reference_appearance(
        self, value: object
    ) -> dict[str, str]:
        """读取已明确采用候选中的各个独立外观组成。"""

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
        item_map = {
            int(getattr(item, "id", 0) or 0): item for item in items or []
        }
        items = [item_map[item_id] for item_id in item_ids if item_id in item_map]
        result: dict[str, list[str]] = {
            "outfit": [],
            "hair_style": [],
            "hair": [],
            "makeup": [],
            "nails": [],
        }
        for item in items or []:
            kind = str(getattr(item, "kind", "")).strip().lower()
            description = " ".join(
                str(getattr(item, "description", "") or "").strip().split()
            )[:260]
            title = " ".join(
                str(getattr(item, "title", "") or "").strip().split()
            )[:80]
            if kind in STYLE_CATALOG_CLOTHING_KINDS and description:
                result["outfit"].append(description)
                if kind == "outfit":
                    attributes = getattr(item, "attributes", {}) or {}
                    if isinstance(attributes, dict):
                        for legacy_key in ("makeup", "nails"):
                            result[legacy_key].extend(
                                self._style_catalog_list(
                                    attributes.get(legacy_key), 4
                                )
                            )
            elif kind == "hair" and description:
                if title:
                    result["hair_style"].append(title)
                result["hair"].append(description)
            elif kind in {"makeup", "nails"} and description:
                result[kind].append(description)
        return {
            key: "；".join(dict.fromkeys(values))
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
