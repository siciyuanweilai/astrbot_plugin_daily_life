from __future__ import annotations

import json
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
        if isinstance(value, str):
            values = value.split(",")
        elif isinstance(value, (int, float)):
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
    def _style_catalog_scene_compatible(
        cls, item: Any, scene_category: object
    ) -> bool:
        """排除与居家或外出场景明显不匹配的衣橱候选。

        场景未知或尚未分类的候选仍可使用，避免已有衣橱条目
        仅因缺少场景标签而失效。
        """

        scene = str(scene_category or "").strip().lower()
        if scene not in {"home", "sleep", "outdoor", "public"}:
            return True
        kind = str(getattr(item, "kind", "") or "").strip().lower()
        role = cls._style_catalog_scene_role(item)
        attributes = cls._style_catalog_attributes(item)
        if kind in {"footwear", "accessory"}:
            if scene in {"home", "sleep"}:
                return role in {"home", "both", "unknown"}
            return role != "home"

        declared_scenes = attributes.get("scene_categories")
        scenes = {
            str(value).strip().lower()
            for value in declared_scenes
        } if isinstance(declared_scenes, (list, tuple, set)) else set()
        scenes.intersection_update({"home", "sleep", "outdoor", "public"})
        if scene in {"outdoor", "public"}:
            if role == "home":
                return False
            profiles = cls._style_catalog_component_profiles(item)
            if profiles and all(profile["role"] == "home" for profile in profiles):
                return False
            return not (scenes and scenes <= {"home", "sleep"})

        if role == "outdoor" and not attributes.get("home_description"):
            return False
        if scene == "sleep":
            return "sleep" in scenes or "home" in scenes or bool(attributes.get("home_description")) or not scenes
        return not (scenes and scenes <= {"outdoor", "public"})

    @staticmethod
    def _style_catalog_component_state(
        kind: str,
        role: str,
        carry_mode: str,
        *,
        home_scene: bool,
    ) -> str:
        """将衣橱候选的结构化角色映射为当前场景中的通用状态。"""

        normalized_kind = str(kind or "").strip().lower()
        normalized_role = str(role or "").strip().lower()
        normalized_carry = str(carry_mode or "").strip().lower()
        if normalized_carry == "none":
            return "removed"
        if normalized_carry == "staged":
            return "staged"
        if home_scene and normalized_role == "outdoor":
            return "staged"
        if normalized_carry == "carried":
            return "carried"
        if normalized_carry == "worn":
            return "worn"
        if normalized_role == "home":
            return "worn"
        if normalized_kind == "accessory" and normalized_role == "both":
            return "worn"
        return "unknown"

    @classmethod
    def _style_catalog_scene_components(
        cls, item: Any, description: str
    ) -> tuple[str, str]:
        """按候选的结构化场景角色分离当前组成与延后组成。"""

        attributes = cls._style_catalog_attributes(item)
        home_description = " ".join(
            str(attributes.get("home_description") or "").split()
        )[:800]
        reserve_description = " ".join(
            str(attributes.get("outing_reserve_description") or "").split()
        )[:800]
        if home_description or reserve_description:
            return home_description, reserve_description
        return description, ""

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
        wardrobe = attributes.get("wardrobe") or {}
        if wardrobe:
            from .dressing import CONDITION_LABELS
            details.append("衣物归属：" + ("正式拥有" if wardrobe.get("ownership") == "owned" else "灵感候选"))
            details.append("穿护状态：" + CONDITION_LABELS.get(wardrobe.get("condition"), "未知"))
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

        模型提供的完整引用优先；引用缺失时只补齐唯一可用的完整套装，
        或唯一的上装与下装组合。多套可选时必须由模型重新判断。
        """

        item_ids = self._style_catalog_reference_ids(value)
        getter = getattr(self.archive, "get_style_catalog_items", None)
        if not callable(getter):
            return item_ids
        complete_selection_seen = False
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
            complete_selection_seen = "outfit" in kinds or {
                "top",
                "bottom",
            }.issubset(kinds)
            if (
                complete_selection_seen
                and len(selected or []) >= len(item_ids)
                and all(
                    self._style_catalog_scene_compatible(item, scene_category)
                    for item in selected or []
                    if str(getattr(item, "kind", "") or "").strip().lower()
                    in STYLE_CATALOG_CLOTHING_KINDS
                )
            ):
                return item_ids

        try:
            outfits = await getter(kind="outfit", status="active", limit=64)
            outfit_ids = [
                int(getattr(outfit, "id", 0) or 0)
                for outfit in outfits or []
                if self._style_catalog_scene_compatible(outfit, scene_category)
                and int(getattr(outfit, "id", 0) or 0) > 0
            ]
            if len(outfit_ids) == 1:
                return outfit_ids
            if outfit_ids:
                return []
            tops = await getter(kind="top", status="active", limit=64)
            bottoms = await getter(kind="bottom", status="active", limit=64)
            tops = [
                item for item in tops or []
                if self._style_catalog_scene_compatible(item, scene_category)
            ]
            bottoms = [
                item for item in bottoms or []
                if self._style_catalog_scene_compatible(item, scene_category)
            ]
            top = tops[0] if len(tops) == 1 else None
            bottom = bottoms[0] if len(bottoms) == 1 else None
            top_id = int(getattr(top, "id", 0) or 0) if top else 0
            bottom_id = int(getattr(bottom, "id", 0) or 0) if bottom else 0
            if top_id > 0 and bottom_id > 0:
                return [top_id, bottom_id]
        except Exception:
            return item_ids
        return [] if complete_selection_seen else item_ids

    async def _style_catalog_new_outfit_selection(
        self, value: object, *, scene_category: object = ""
    ) -> tuple[dict[str, Any], str]:
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
        snapshot_getter = getattr(self.archive, "get_wardrobe_snapshot", None)
        snapshot = await snapshot_getter() if callable(snapshot_getter) else {}
        if complete_selection and snapshot.get("items"):
            for item in items:
                if item.kind not in STYLE_CATALOG_CLOTHING_KINDS:
                    continue
                state = snapshot.get("items", {}).get(str(item.id), {})
                if not state.get("available"):
                    return {}, "所选衣物尚未正式纳入数字衣橱，或处于待洗、清洗、晾晒、收纳状态；请使用可穿衣物，缺口由后台补充"
        if complete_selection and appearance.get("outfit"):
            return appearance, ""
        if not await self._style_catalog_has_clothing_candidates():
            return {}, ""
        return (
            {},
            "视觉衣橱已有启用的服装候选；自主生成新穿搭时必须选择一条完整套装，"
            "或同时选择上装与下装，并把采用编号写入 catalog_reference_ids",
        )

    async def _style_catalog_context(self, *, limit: int = 10, conditions: dict | None = None) -> str:
        getter = getattr(self.archive, "get_style_catalog_items", None)
        if not callable(getter):
            return ""
        safe_limit = max(1, min(limit, 24))
        candidates = await getter(
            status="active", limit=500
        )
        from .dressing import reviewed_candidates, suitability_key, wardrobe_fact_text
        snapshot_getter = getattr(self.archive, "get_wardrobe_snapshot", None)
        snapshot = await snapshot_getter() if callable(snapshot_getter) else {}
        for item in candidates:
            state = snapshot.get("items", {}).get(str(item.id))
            if state:
                item.attributes["wardrobe"] = state
        candidates = reviewed_candidates(candidates, snapshot.get("profile", {}) if conditions else {}, suitability_key(conditions) if conditions else "")
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
            "以下来自已学习或生成的造型资料。候选、正式拥有与当前穿着分别记录，不能把生成或收藏说成已经穿上。",
            "保持当前穿搭时不采用候选；自主产生新穿搭且存在合适服装候选时，具体服装必须从本轮候选中选择，长期偏好只用于排序，不能直接变成衣服。",
            "新穿搭可以采用一条完整套装，也可以同时组合上装与下装，再按需选择鞋袜和配饰；不要只选半套，也不要同时选取语义重复的整套与单品。",
            "完整候选中的可拆卸组成按结构化场景角色进入当前或延后状态；当前场景只写已经穿着或携带的组成，不把待用组成混入可见穿搭。",
            "先保留适配的当前穿搭，已有单品优先叠穿；干净、舒适且适合天气的喜欢衣服可自然复穿。正在洗护或仅为候选的衣物不能直接穿上；不足时保留缺口交给后台补充。",
            "发型、妆容和美甲必须分别选择，不能把候选图片中的人物身份、体貌、姿势、场景或品牌当作角色事实。",
            "候选中的“视觉提示词”是该类别的详细外观事实；实际采用后应忠实保留，不得自行简化款式、层次、颜色或装饰细节。",
            "只有实际采用对应类别时才改变该外观组成；局部换衣不能自动改掉发型、妆容或美甲。",
        ]
        lines.append(wardrobe_fact_text(snapshot))
        lines.extend(self._style_catalog_item_line(item) for item in items)
        detailed_ids = {item.id for item in items}
        clothing_index = [
            item for item in candidates
            if item.kind in STYLE_CATALOG_CLOTHING_KINDS and item.id not in detailed_ids
        ]
        if clothing_index:
            lines.append("其余可选服装简表：可按类别、用途和场景选择编号；采用后系统会读取该候选的完整外观，不能另造衣服。")
            for item in clothing_index:
                attributes = self._style_catalog_attributes(item)
                category = "、".join(self._style_catalog_list(attributes.get("category"), 4))
                scenes = "、".join(self._style_catalog_list(attributes.get("scenes"), 4))
                material = "、".join(self._style_catalog_list(attributes.get("material_appearance"), 3))
                title = " ".join(str(item.title or "").split())[:80]
                seasons = "、".join(self._style_catalog_list(attributes.get("seasons"), 4))
                weather = "、".join(self._style_catalog_list(attributes.get("weather_fit"), 4))
                state = attributes.get("wardrobe") or {}
                lines.append(f"- #{item.id} [{item.kind}] {title}；类别：{category}；场景：{scenes}；材质：{material}；季节：{seasons}；天气适配：{weather}；归属：{state.get('ownership','candidate')}；状态：{state.get('condition','unknown')}")
        return "\n".join(lines)

    async def _style_catalog_reference_appearance(
        self, value: object, *, scene_category: object = ""
    ) -> dict[str, Any]:
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
        result: dict[str, Any] = {
            "outfit": [],
            "outing_reserve": [],
            "hair_style": [],
            "hair": [],
            "makeup_style": [],
            "makeup": [],
            "nails_style": [],
            "nails": [],
        }
        component_values: dict[str, dict[str, str]] = {}
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
                        self._style_catalog_scene_components(item, description)
                    )
                elif home_scene and kind in {"footwear", "accessory"} and (
                    self._style_catalog_scene_role(item) not in {"home", "both"}
                ):
                    current_description, reserve_description = "", description
                if current_description:
                    result["outfit"].append(current_description)
                if reserve_description:
                    result["outing_reserve"].append(reserve_description)
                if kind == "outfit":
                    profiles = self._style_catalog_component_profiles(item)
                    has_structured_home_split = bool(
                        not home_scene
                        or self._style_catalog_attributes(item).get(
                            "home_description"
                        )
                        or profiles
                    )
                    if has_structured_home_split:
                        component_values["main_clothing"] = {
                            "state": "worn" if current_description else "removed",
                            "description": current_description,
                        }
                    for profile in profiles:
                        component_key = (
                            "footwear"
                            if profile["kind"] == "footwear"
                            else "carried_accessories"
                        )
                        state = self._style_catalog_component_state(
                            profile["kind"],
                            profile["role"],
                            profile["carry_mode"],
                            home_scene=home_scene,
                        )
                        component = component_values.setdefault(
                            component_key,
                            {"state": state, "description": ""},
                        )
                        component["state"] = state
                        component["description"] = (
                            f"{component['description']}；{profile['name']}"
                            if component["description"]
                            else profile["name"]
                        )
                elif kind in {"top", "bottom"}:
                    main = component_values.setdefault(
                        "main_clothing",
                        {"state": "worn", "description": ""},
                    )
                    main["state"] = "worn" if current_description else "removed"
                    main["description"] = (
                        f"{main['description']}；{current_description}"
                        if main["description"] and current_description
                        else current_description or main["description"]
                    )
                elif kind in {"footwear", "accessory"}:
                    component_key = (
                        "footwear" if kind == "footwear" else "carried_accessories"
                    )
                    role = self._style_catalog_scene_role(item)
                    carry_mode = str(
                        self._style_catalog_attributes(item).get("carry_mode") or ""
                    ).strip().lower()
                    state = self._style_catalog_component_state(
                        kind,
                        role,
                        carry_mode,
                        home_scene=home_scene,
                    )
                    component_values[component_key] = {
                        "state": state,
                        "description": description,
                    }
            elif kind == "hair" and description:
                if title:
                    result["hair_style"].append(title)
                result["hair"].append(description)
            elif kind in {"makeup", "nails"} and description:
                if title:
                    result[f"{kind}_style"].append(title)
                result[kind].append(description)
        output = {
            key: "；".join(dict.fromkeys(values))
            for key, values in result.items()
            if values
        }
        if component_values:
            output["outfit_components"] = json.dumps(
                component_values,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return output

    async def _mark_style_catalog_references(self, value: object) -> int:
        marker = getattr(self.archive, "mark_style_catalog_used", None)
        if not callable(marker):
            return 0
        item_ids = self._style_catalog_reference_ids(value)
        return await marker(item_ids) if item_ids else 0


__all__ = ["StyleCatalogMixin"]
