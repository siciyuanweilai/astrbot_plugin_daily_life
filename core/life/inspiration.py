from __future__ import annotations

from typing import Any

from ..models import (
    STYLE_CATALOG_CARRY_MODES,
    STYLE_CATALOG_CLOTHING_KINDS,
    STYLE_CATALOG_HOME_PRESENCE,
    STYLE_CATALOG_KIND_LABELS,
    STYLE_CATALOG_KINDS,
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

    @staticmethod
    def _style_catalog_description(item: Any) -> str:
        return " ".join(str(getattr(item, "description", "") or "").split())[:800]

    @staticmethod
    def _style_catalog_attributes(item: Any) -> dict[str, Any]:
        attributes = getattr(item, "attributes", {}) or {}
        return attributes if isinstance(attributes, dict) else {}

    @classmethod
    def _style_catalog_scene_role(cls, item: Any) -> str:
        """读取候选入库时的结构化居家适配角色，不分析自然语言描述。"""

        attributes = cls._style_catalog_attributes(item)
        role = str(attributes.get("home_presence") or "").strip().lower()
        return role if role in STYLE_CATALOG_HOME_PRESENCE else "unknown"

    @classmethod
    def _style_catalog_component_profiles(cls, item: Any) -> list[dict[str, str]]:
        """读取套装的结构化组成角色。"""

        attributes = cls._style_catalog_attributes(item)
        raw = attributes.get("component_roles")
        if not isinstance(raw, (list, tuple)):
            return []
        profiles: list[dict[str, str]] = []
        for value in raw:
            if not isinstance(value, dict):
                continue
            kind = str(value.get("kind") or "").strip().lower()
            role = str(value.get("home_presence") or "").strip().lower()
            carry_mode = str(value.get("carry_mode") or "").strip().lower()
            if kind not in {"footwear", "accessory"} or role not in STYLE_CATALOG_HOME_PRESENCE:
                continue
            name = " ".join(str(value.get("name") or "").split())[:120]
            if name:
                profiles.append(
                    {
                        "kind": kind,
                        "role": role,
                        "name": name,
                        "carry_mode": carry_mode
                        if carry_mode in STYLE_CATALOG_CARRY_MODES
                        else "unknown",
                    }
                )
        return profiles

    @classmethod
    def _home_outfit_components(cls, item: Any, description: str) -> tuple[str, str]:
        """把套装中的外出鞋和随身物品从居家当前穿着中分离。"""

        attributes = cls._style_catalog_attributes(item)
        home_description = " ".join(
            str(attributes.get("home_description") or "").split()
        )[:800]
        reserve_description = " ".join(
            str(attributes.get("outing_reserve_description") or "").split()
        )[:800]
        if home_description or reserve_description:
            return home_description, reserve_description

        profiles = cls._style_catalog_component_profiles(item)
        reserved_components = [
            profile["name"]
            for profile in profiles
            if profile["role"] in {"outdoor", "unknown"}
        ]
        if not reserved_components:
            return description, ""

        pieces = cls._style_catalog_list(attributes.get("pieces"), 12)
        reserved_set = set(reserved_components)
        current_pieces = [
            piece for piece in pieces if piece not in reserved_set
        ]
        if current_pieces:
            return "；".join(current_pieces), "；".join(reserved_components)
        return "", description

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
            ("居家适配", "home_presence"),
            ("使用方式", "carry_mode"),
            ("居家组成", "home_description"),
            ("外出备选", "outing_reserve_description"),
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
        description = cls._style_catalog_description(item)[:420]
        suffix = f"；{'；'.join(details)}" if details else ""
        heading = title or f"{kind}候选"
        return (
            f"- #{int(getattr(item, 'id', 0) or 0)} [{kind}] "
            f"{heading}；描述：{description}{suffix}；偏好分 {score:.1f}"
        )

    async def _style_catalog_has_clothing_candidates(self) -> bool:
        getter = getattr(self.archive, "get_style_catalog_items", None)
        if not callable(getter):
            return False
        try:
            if await getter(kind="outfit", status="active", limit=1):
                return True
            tops = await getter(kind="top", status="active", limit=1)
            bottoms = await getter(kind="bottom", status="active", limit=1)
            return bool(tops and bottoms)
        except Exception:
            return False

    async def _style_catalog_resolve_new_outfit_reference_ids(
        self, value: object, *, scene_category: object = ""
    ) -> list[int]:
        """修复自主换装漏填或只填半套时的衣橱引用。

        模型提供的完整引用优先；只有引用缺失、失效或无法组成完整穿搭时，
        才从当前启用候选中选一套完整套装，或选择一件上装加一件下装。
        """

        del scene_category  # 保留参数，便于后续按场景筛选候选。
        item_ids = self._style_catalog_reference_ids(value)
        getter = getattr(self.archive, "get_style_catalog_items", None)
        if not callable(getter):
            return item_ids
        if item_ids:
            try:
                selected = await getter(
                    status="active", ids=item_ids, limit=len(item_ids)
                )
            except Exception:
                selected = []
            kinds = {
                str(getattr(item, "kind", "") or "").strip().lower()
                for item in selected or []
            }
            if "outfit" in kinds or {"top", "bottom"}.issubset(kinds):
                return item_ids

        try:
            outfits = await getter(kind="outfit", status="active", limit=1)
            if outfits:
                outfit_id = int(getattr(outfits[0], "id", 0) or 0)
                if outfit_id > 0:
                    return [outfit_id]
            tops = await getter(kind="top", status="active", limit=1)
            bottoms = await getter(kind="bottom", status="active", limit=1)
            top_id = int(getattr(tops[0], "id", 0) or 0) if tops else 0
            bottom_id = int(getattr(bottoms[0], "id", 0) or 0) if bottoms else 0
            if top_id > 0 and bottom_id > 0:
                return [top_id, bottom_id]
        except Exception:
            return item_ids
        return item_ids

    async def _style_catalog_new_outfit_selection(
        self, value: object, *, scene_category: object = ""
    ) -> tuple[dict[str, str], str]:
        """校验新穿搭是否真正采用了启用中的衣橱服装。"""

        item_ids = self._style_catalog_reference_ids(value)
        getter = getattr(self.archive, "get_style_catalog_items", None)
        items = []
        if callable(getter) and item_ids:
            try:
                items = await getter(
                    status="active", ids=item_ids, limit=len(item_ids)
                )
            except Exception:
                items = []
        kinds = {str(getattr(item, "kind", "") or "") for item in items}
        complete_selection = "outfit" in kinds or {"top", "bottom"}.issubset(kinds)
        appearance = (
            await self._style_catalog_reference_appearance(
                item_ids, scene_category=scene_category
            )
            if complete_selection
            else {}
        )
        if complete_selection and appearance.get("outfit"):
            return appearance, ""
        if not await self._style_catalog_has_clothing_candidates():
            return {}, ""
        return (
            {},
            "视觉衣橱已有启用的服装候选；自主生成新穿搭时必须选择一条完整套装，"
            "或同时选择上装与下装，并把采用编号写入 catalog_reference_ids",
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
        other_kinds = [kind for kind in STYLE_CATALOG_KINDS if kind != "outfit"]
        reserved_other = sum(bool(grouped[kind]) for kind in other_kinds)
        outfit_quota = min(
            6,
            len(grouped["outfit"]),
            max(1, safe_limit - reserved_other),
        )
        for _ in range(outfit_quota):
            items.append(grouped["outfit"].pop(0))
        for kind in other_kinds:
            if grouped[kind] and len(items) < safe_limit:
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
            "保持当前穿搭时不采用候选；自主产生新穿搭且存在合适服装候选时，具体服装必须从本轮候选中选择，长期偏好只用于排序，不能直接变成衣服。",
            "新穿搭可以采用一条完整套装，也可以同时组合上装与下装，再按需选择鞋袜和配饰；不要只选半套，也不要同时选取语义重复的整套与单品。",
            "完整套装候选中的外出鞋和随身包只在实际出门时加入当前穿搭；居家时主体衣物可以继续采用同一候选，但鞋包应作为外出备选，不得描述成仍穿着或携带。",
            "近期使用过的候选已由系统降权；场景与天气适配和近期轮换优先于偏好分，避免把高偏好候选穿成固定制服。",
            "发型、妆容和美甲必须分别选择，不能把候选图片中的人物身份、体貌、姿势、场景或品牌当作角色事实。",
            "候选中的“视觉提示词”是该类别的详细外观事实；实际采用后应忠实保留，不得自行简化款式、层次、颜色或装饰细节。",
            "只有实际采用对应类别时才改变该外观组成；局部换衣不能自动改掉发型、妆容或美甲。",
        ]
        lines.extend(self._style_catalog_item_line(item) for item in items)
        return "\n".join(lines)

    async def _style_catalog_reference_appearance(
        self, value: object, *, scene_category: object = ""
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
            "outing_reserve": [],
            "hair_style": [],
            "hair": [],
            "makeup_style": [],
            "makeup": [],
            "nails_style": [],
            "nails": [],
        }
        for item in items or []:
            kind = str(getattr(item, "kind", "")).strip().lower()
            description = self._style_catalog_description(item)
            title = " ".join(
                str(getattr(item, "title", "") or "").strip().split()
            )[:80]
            home_scene = str(scene_category or "").strip().lower() in {
                "home",
                "sleep",
            }
            if kind in STYLE_CATALOG_CLOTHING_KINDS and description:
                current_description = description
                reserve_description = ""
                if home_scene and kind == "outfit":
                    current_description, reserve_description = (
                        self._home_outfit_components(item, description)
                    )
                elif home_scene and kind in {"footwear", "accessory"} and (
                    self._style_catalog_scene_role(item) not in {"home", "both"}
                ):
                    current_description, reserve_description = "", description
                if current_description:
                    result["outfit"].append(current_description)
                if reserve_description:
                    result["outing_reserve"].append(reserve_description)
            elif kind == "hair" and description:
                if title:
                    result["hair_style"].append(title)
                result["hair"].append(description)
            elif kind in {"makeup", "nails"} and description:
                if title:
                    result[f"{kind}_style"].append(title)
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
