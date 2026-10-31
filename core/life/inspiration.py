from __future__ import annotations

import json
from typing import Any, Protocol

from ..models import (
    STYLE_CATALOG_CARRY_MODES,
    STYLE_CATALOG_CLOTHING_KINDS,
    STYLE_CATALOG_HOME_PRESENCE,
    STYLE_CATALOG_KIND_LABELS,
    STYLE_CATALOG_KINDS,
)


class StyleCatalogComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _style_catalog_attributes: Any
    _style_catalog_component_profiles: Any
    _style_catalog_component_state: Any
    _style_catalog_description: Any
    _style_catalog_has_clothing_candidates: Any
    _style_catalog_item_line: Any
    _style_catalog_list: Any
    _style_catalog_reference_appearance: Any
    _style_catalog_reference_ids: Any
    _style_catalog_scene_compatible: Any
    _style_catalog_scene_components: Any
    _style_catalog_scene_role: Any
    archive: Any


class StyleCatalogComponent:
    def __init__(self, context: StyleCatalogComponentContext) -> None:
        self.context = context

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
            if isinstance(item, str):
                # 提示中的 # 是编号展示前缀，仍按明确引用读取同一衣橱条目。
                item = item.strip().removeprefix("#")
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
        from .wardrobefit import item_traits

        return item_traits(item)

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
            details.append("衣物归属：" + ("正式拥有" if wardrobe.get("ownership") == "owned" else "待核验，尚未入橱"))
            details.append("穿护状态：" + CONDITION_LABELS.get(wardrobe.get("condition"), "未知"))
            if wardrobe.get("currently_worn"):
                details.append("实际穿着：正在穿着")
        suffix = f"；{'；'.join(details)}" if details else ""
        heading = title or f"{kind}候选"
        return (
            f"- #{int(getattr(item, 'id', 0) or 0)} [{kind}] "
            f"{heading}；描述：{description}{suffix}；偏好分 {score:.1f}"
        )

    async def _style_catalog_has_clothing_candidates(self) -> bool:
        return await self._style_catalog_has_eligible_clothing()

    async def _style_catalog_has_eligible_clothing(
        self, *, scene_category: object = "", conditions: dict | None = None
    ) -> bool:
        getter = getattr(self.context.archive, "get_style_catalog_items", None)
        if not callable(getter):
            return False
        try:
            items = await getter(status="active", limit=500)
            snapshot_getter = getattr(self.context.archive, "get_wardrobe_snapshot", None)
            snapshot = await snapshot_getter() if callable(snapshot_getter) else {}
            outfits = [
                item for item in items or []
                if item.kind == "outfit"
                and self._style_catalog_item_eligible(
                    item, snapshot, scene_category=scene_category, conditions=conditions
                )
            ]
            if outfits:
                return True
            tops = [
                item for item in items or []
                if item.kind == "top"
                and self._style_catalog_item_eligible(
                    item, snapshot, scene_category=scene_category, conditions=conditions
                )
            ]
            bottoms = [
                item for item in items or []
                if item.kind == "bottom"
                and self._style_catalog_item_eligible(
                    item, snapshot, scene_category=scene_category, conditions=conditions
                )
            ]
            return bool(tops and bottoms)
        except Exception:
            return False

    async def _style_catalog_has_active_clothing(self) -> bool:
        getter = getattr(self.context.archive, "get_style_catalog_items", None)
        if not callable(getter):
            return False
        try:
            items = await getter(status="active", limit=500)
        except Exception:
            return False
        return any(
            str(getattr(item, "kind", "") or "").strip().lower()
            in STYLE_CATALOG_CLOTHING_KINDS
            for item in items or []
        )

    @staticmethod
    def _style_catalog_item_eligible(
        item: Any,
        snapshot: dict,
        *,
        scene_category: object = "",
        conditions: dict | None = None,
    ) -> bool:
        """判断衣物是否能在当前时刻直接采用；未知资料不会伪装成已拥有。"""
        kind = str(getattr(item, "kind", "") or "").strip().lower()
        if kind in STYLE_CATALOG_CLOTHING_KINDS:
            states = snapshot.get("items") or {}
            state = states.get(str(getattr(item, "id", 0) or 0)) or {}
            if state.get("ownership") != "owned" or not state.get("available"):
                return False
        if not StyleCatalogComponent._style_catalog_scene_compatible(item, scene_category):
            return False
        if conditions and scene_category and scene_category == conditions.get("current_scene_category"):
            from .dressing import suitability_key

            profile = snapshot.get("profile") or {}
            key = suitability_key(conditions)
            if profile.get("context_key") == key:
                verdict = (profile.get("suitability") or {}).get(
                    str(getattr(item, "id", 0) or 0), {}
                ).get("verdict")
                if verdict == "unsuitable":
                    return False
        return True

    async def _style_catalog_resolve_new_outfit_reference_ids(
        self,
        value: object,
        *,
        scene_category: object = "",
        conditions: dict | None = None,
    ) -> list[int]:
        """修复自主换装漏填或只填半套时的衣橱引用。

        模型提供的完整引用优先；引用缺失时，只采用同一生活条件下
        已明确判断适配的唯一完整组合，不凭单品数量拼凑搭配。
        """

        item_ids = self.context._style_catalog_reference_ids(value)
        getter = getattr(self.context.archive, "get_style_catalog_items", None)
        if not callable(getter):
            return item_ids
        complete_selection_seen = False
        snapshot = await self._style_catalog_snapshot()
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
                    self._style_catalog_item_eligible(
                        item,
                        snapshot,
                        scene_category=scene_category,
                        conditions=conditions,
                    )
                    for item in selected or []
                    if str(getattr(item, "kind", "") or "").strip().lower()
                    in STYLE_CATALOG_CLOTHING_KINDS
                )
            ):
                return item_ids

        from .dressing import suitability_key
        from .wardrobefit import understanding_key

        profile = snapshot.get("profile") or {}
        if conditions and profile.get("context_key") == suitability_key(conditions):
            matches = [
                choice for choice in profile.get("scene_choices", [])
                if choice.get("current") and choice.get("verdict") == "fits"
                and choice.get("scene_category") == scene_category
                and choice.get("item_ids") and not choice.get("current_outfit")
            ]
            if len(matches) == 1:
                match = matches[0]
                approved = await getter(status="active", ids=match["item_ids"], limit=len(match["item_ids"]))
                if len(approved) == len(match["item_ids"]) and all(
                    match.get("item_bases", {}).get(str(item.id)) == understanding_key(item)
                    and self._style_catalog_item_eligible(
                        item, snapshot, scene_category=scene_category, conditions=conditions
                    ) for item in approved
                ):
                    return list(match["item_ids"])
        return [] if complete_selection_seen else item_ids

    async def _style_catalog_snapshot(self) -> dict:
        getter = getattr(self.context.archive, "get_wardrobe_snapshot", None)
        if not callable(getter):
            return {}
        try:
            return await getter()
        except Exception:
            return {}

    async def _style_catalog_new_outfit_selection(
        self,
        value: object,
        *,
        scene_category: object = "",
        conditions: dict | None = None,
    ) -> tuple[dict[str, Any], str]:
        """校验新穿搭是否真正采用了启用中的衣橱服装。"""

        item_ids = self.context._style_catalog_reference_ids(value)
        getter = getattr(self.context.archive, "get_style_catalog_items", None)
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
            await self.context._style_catalog_reference_appearance(
                item_ids, scene_category=scene_category
            )
            if complete_selection
            else {}
        )
        snapshot = await self._style_catalog_snapshot()
        if complete_selection:
            for item in items:
                if item.kind not in STYLE_CATALOG_CLOTHING_KINDS:
                    continue
                if not self._style_catalog_item_eligible(
                    item, snapshot, scene_category=scene_category, conditions=conditions
                ):
                    return {}, "所选衣物尚未正式纳入数字衣橱、正在洗护或不适合此场景；请改用已拥有且可穿的衣物，缺口由后台补充"
        if complete_selection and appearance.get("outfit"):
            return appearance, ""
        if not await self._style_catalog_has_eligible_clothing(
            scene_category=scene_category, conditions=conditions
        ):
            if await self._style_catalog_has_active_clothing():
                return (
                    {},
                    "衣橱资料中暂时没有正式拥有、可穿且适合此场景的完整搭配；先保留当前穿着，等待补齐或选择其他已拥有衣物",
                )
            return {}, ""
        return (
            {},
            "视觉衣橱已有启用的服装候选；自主生成新穿搭时必须选择一条完整套装，"
            "或同时选择上装与下装，并把采用编号写入 catalog_reference_ids",
        )

    async def _style_catalog_context(
        self, *, limit: int = 10, conditions: dict | None = None,
        scene_category: object = "",
    ) -> str:
        getter = getattr(self.context.archive, "get_style_catalog_items", None)
        if not callable(getter):
            return ""
        safe_limit = max(1, min(limit, 24))
        candidates = await getter(
            status="active", limit=500
        )
        from .dressing import reviewed_candidates, suitability_key, wardrobe_fact_text
        snapshot_getter = getattr(self.context.archive, "get_wardrobe_snapshot", None)
        snapshot = await snapshot_getter() if callable(snapshot_getter) else {}
        for item in candidates:
            state = snapshot.get("items", {}).get(str(item.id))
            if state:
                item.attributes["wardrobe"] = state
        # 全天规划保留各场景可穿衣物，当前换装才应用本轮适配裁定。
        current_conditions = conditions if scene_category else None
        candidates = reviewed_candidates(
            candidates,
            snapshot.get("profile", {}) if conditions else {},
            suitability_key(conditions) if conditions else "",
        )
        unavailable_clothing_count = sum(
            1
            for item in candidates
            if item.kind in STYLE_CATALOG_CLOTHING_KINDS
            and not self._style_catalog_item_eligible(
                item, snapshot, scene_category=scene_category, conditions=current_conditions
            )
        )
        candidates = [
            item
            for item in candidates
            if item.kind not in STYLE_CATALOG_CLOTHING_KINDS
            or self._style_catalog_item_eligible(
                item, snapshot, scene_category=scene_category, conditions=current_conditions
            )
        ]
        profile = snapshot.get("profile") or {}
        reviewed = bool(conditions and profile.get("context_key") == suitability_key(conditions))
        preferred_ids = {
            item_id for choice in profile.get("scene_choices", [])
            if reviewed and (not scene_category or choice.get("scene_category") == scene_category)
            for item_id in choice.get("item_ids", [])
        }
        # 先呈现已核实场景方案；其余继续保留语义适配与偏好顺序。
        candidates.sort(key=lambda item: item.id not in preferred_ids)
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
        if not items and not unavailable_clothing_count:
            return ""
        lines = [
            "## 👗 数字衣橱",
            "服装编号只列正式拥有、当前可穿的衣物；按目标活动、场景和天气选择，未拥有或待洗的资料不能当作可穿候选。",
            "保持当前穿搭时不采用候选；自主换装优先从本轮可穿衣物中选择完整套装或上、下装组合，长期偏好只用于排序。",
            "新穿搭可以采用一条完整套装，也可以同时组合上装与下装，再按需选择鞋袜和配饰；不要只选半套，也不要同时选取语义重复的整套与单品。catalog_reference_ids 填正整数数组，例如 [1, 102]；# 仅用于展示编号。",
            "完整候选中的可拆卸组成按结构化场景角色进入当前或延后状态；当前场景只写已经穿着或携带的组成，不把待用组成混入可见穿搭。",
            "先保留适配的当前穿搭，已有单品优先叠穿；干净、舒适且适合天气的衣物可自然复穿。场景、活动、身体和天气不合适时换用其他可穿衣物；衣橱无适配完整搭配时交由后台评估补充。",
            "发型、妆容和美甲按需独立选择，无需每类都选；不能把候选图片中的人物身份、体貌、姿势、场景或品牌当作角色事实。",
            "候选中的“视觉提示词”是该类别的详细外观事实；实际采用后应忠实保留，不得自行简化款式、层次、颜色或装饰细节。",
            "只有实际采用对应类别时才改变该外观组成；局部换衣不能自动改掉发型、妆容或美甲。",
        ]
        if unavailable_clothing_count:
            lines.append(
                f"另有 {unavailable_clothing_count} 条服装资料当前不具备直接穿用条件，本轮不提供其编号。"
            )
        lines.append(wardrobe_fact_text(snapshot))
        profile = snapshot.get("profile") or {}
        if conditions and profile.get("context_key") == suitability_key(conditions):
            choices = [
                {key: c.get(key) for key in ("scene_category", "plan", "verdict", "outfit_basis", "current_outfit", "item_ids", "reason")}
                for c in profile.get("scene_choices", [])
            ]
            if choices:
                lines.append("已核实的场景搭配（未来安排仅作准备，换装仍以当前活动为准）：" + json.dumps(choices, ensure_ascii=False))
        lines.extend(self.context._style_catalog_item_line(item) for item in items)
        detailed_ids = {item.id for item in items}
        remaining_clothing = [
            item for item in candidates
            if item.kind in STYLE_CATALOG_CLOTHING_KINDS and item.id not in detailed_ids
        ]
        # 简表按结构化用途轮流取样，避免大量同类上装挤掉少见的睡眠、运动等衣物。
        buckets = {}
        for item in remaining_clothing:
            attributes = self.context._style_catalog_attributes(item)
            bucket = (item.kind, tuple(self.context._style_catalog_list(attributes.get("scenes"), 4)))
            buckets.setdefault(bucket, []).append(item)
        clothing_index = []
        while buckets and len(clothing_index) < 32:
            for bucket in list(buckets):
                clothing_index.append(buckets[bucket].pop(0))
                if not buckets[bucket]:
                    del buckets[bucket]
                if len(clothing_index) >= 32:
                    break
        if clothing_index:
            lines.append("其余可穿服装简表：可按类别、用途和场景选择编号；采用后读取完整外观。")
            for item in clothing_index:
                attributes = self.context._style_catalog_attributes(item)
                category = "、".join(self.context._style_catalog_list(attributes.get("category"), 4))
                scenes = "、".join(self.context._style_catalog_list(attributes.get("scenes"), 4))
                material = "、".join(self.context._style_catalog_list(attributes.get("material_appearance"), 3))
                title = " ".join(str(item.title or "").split())[:80]
                seasons = "、".join(self.context._style_catalog_list(attributes.get("seasons"), 4))
                weather = "、".join(self.context._style_catalog_list(attributes.get("weather_fit"), 4))
                state = attributes.get("wardrobe") or {}
                lines.append(f"- #{item.id} [{item.kind}] {title}；类别：{category}；场景：{scenes}；材质：{material}；季节：{seasons}；天气适配：{weather}；归属：{state.get('ownership','candidate')}；状态：{state.get('condition','unknown')}")
        if len(remaining_clothing) > len(clothing_index):
            lines.append(f"本轮展示相关候选，另有 {len(remaining_clothing) - len(clothing_index)} 件可穿衣物未展开；未展示不代表衣橱缺少此类衣物，补衣以完整衣橱的场景评估为准。")
        return "\n".join(lines)

    async def _style_catalog_reference_appearance(
        self, value: object, *, scene_category: object = ""
    ) -> dict[str, Any]:
        """读取已明确采用候选中的各个独立外观组成。"""

        getter = getattr(self.context.archive, "get_style_catalog_items", None)
        item_ids = self.context._style_catalog_reference_ids(value)
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
            description = self.context._style_catalog_description(item)
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
                        self.context._style_catalog_scene_components(item, description)
                    )
                elif home_scene and kind in {"footwear", "accessory"} and (
                    self.context._style_catalog_scene_role(item) not in {"home", "both"}
                ):
                    current_description, reserve_description = "", description
                if current_description:
                    result["outfit"].append(current_description)
                if reserve_description:
                    result["outing_reserve"].append(reserve_description)
                if kind == "outfit":
                    profiles = self.context._style_catalog_component_profiles(item)
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
                        state = self.context._style_catalog_component_state(
                            profile["kind"],
                            profile["role"],
                            profile["carry_mode"],
                            home_scene=home_scene,
                        )
                        component = component_values.setdefault(
                            component_key,
                            {"state": state, "description": "", "home_presence": profile["role"]},
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
                    role = self.context._style_catalog_scene_role(item)
                    carry_mode = str(
                        self.context._style_catalog_attributes(item).get("carry_mode") or ""
                    ).strip().lower()
                    state = self.context._style_catalog_component_state(
                        kind,
                        role,
                        carry_mode,
                        home_scene=home_scene,
                    )
                    component_values[component_key] = {
                        "state": state,
                        "description": description,
                        "home_presence": role,
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
        marker = getattr(self.context.archive, "mark_style_catalog_used", None)
        if not callable(marker):
            return 0
        item_ids = self.context._style_catalog_reference_ids(value)
        return await marker(item_ids) if item_ids else 0


__all__ = ["StyleCatalogComponent"]
