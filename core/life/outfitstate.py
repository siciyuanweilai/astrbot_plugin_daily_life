"""穿搭外观与组成部分的纯数据转换，不依赖运行时或归档组件。"""

from dataclasses import dataclass
from typing import Any
from ..outcome import ToolResultText

from .appearance import normalize_appearance_fact
from .wardrobe import (
    decision_for_occurred_outfit,
    format_outfit_components,
    merge_outfit_components,
    normalize_outfit_components,
    reconcile_outfit_components_for_scene,
)


@dataclass(frozen=True, slots=True)
class OutfitDecisionReview:
    decision: str
    main_clothing_review: str
    clothing_component_adjusted: bool
    occurred_change: bool
    partial_change: bool


def review_outfit_decision(
    decision: str,
    *,
    current_basis: str,
    scene_category: str,
    style_pool: str,
    component_review: dict,
    stored: dict[str, str],
    generated: dict[str, str],
    has_instruction: bool,
) -> OutfitDecisionReview:
    """只依据已解析字段校正模型决定，状态提交留给调用方。"""
    occurred = (
        decision == "keep"
        and current_basis in {"occurred_schedule", "live_state"}
        and bool(generated["outfit"])
        and generated["outfit"] != stored["outfit"]
    )
    if occurred:
        decision = decision_for_occurred_outfit(scene_category, style_pool)
    states = {
        str(key or "").strip(): str(value or "").strip().lower()
        for key, value in component_review.items()
    }
    clothing_adjusted = any(
        states.get(key) == "adjust"
        for key in ("main_clothing", "footwear", "outer_layer", "carried_accessories")
    )
    appearance_differs = any(
        generated[key] and generated[key] != stored[key]
        for key in ("outfit", "hair", "makeup", "nails")
    )
    partial = decision == "keep" and any((
        clothing_adjusted and bool(generated["outfit"])
        and generated["outfit"] != stored["outfit"],
        states.get("hair") == "adjust"
        and bool(generated["hair_style"] or generated["hair"])
        and (generated["hair_style"], generated["hair"])
        != (stored["hair_style"], stored["hair"]),
        states.get("makeup") == "adjust"
        and bool(generated["makeup_style"] or generated["makeup"])
        and (generated["makeup_style"], generated["makeup"])
        != (stored["makeup_style"], stored["makeup"]),
        states.get("nails") == "adjust"
        and bool(generated["nails_style"] or generated["nails"])
        and (generated["nails_style"], generated["nails"])
        != (stored["nails_style"], stored["nails"]),
        has_instruction and appearance_differs,
    ))
    if partial:
        decision = "partial_change"
    return OutfitDecisionReview(
        decision, states.get("main_clothing", "unknown"), clothing_adjusted,
        occurred, partial,
    )


@dataclass(frozen=True, slots=True)
class OutfitCommit:
    """已经通过事实校验的外观变更；保存阶段不再解释模型原文。"""

    outfit: str
    meta: dict[str, str]
    outfit_changed: bool
    appearance_changed: bool
    reference_ids: list[int]
    previous_reference_ids: list[int]
    partial_catalog: bool
    require_wearable: bool


@dataclass(frozen=True, slots=True)
class OutfitChange:
    clothing_changed: bool
    components_changed: bool
    appearance_changed: bool

    @property
    def changed(self) -> bool:
        return self.clothing_changed or self.components_changed or self.appearance_changed

    @property
    def status(self) -> str:
        return "completed" if self.clothing_changed else "partial" if self.changed else "unchanged"


def outfit_change(before: Any, after: Any) -> OutfitChange:
    """统一衣物与局部外观的状态差异；风格标签不作为物理变化。"""
    previous = normalize_outfit_components(before.meta.get("outfit_components"))
    current = normalize_outfit_components(after.meta.get("outfit_components"))
    old_main = previous.get("main_clothing", {})
    new_main = current.get("main_clothing", {})
    same_main = bool(old_main and new_main and old_main == new_main)
    detail_keys = ("hair", "makeup", "nails")
    appearance = any(before.meta.get(key) != after.meta.get(key) for key in detail_keys)
    components = any(
        previous.get(key) != current.get(key)
        for key in ("footwear", "outer_layer", "carried_accessories")
    )
    return OutfitChange(bool(before.outfit != after.outfit and not same_main), components, appearance)


def outfit_update_feedback(before: Any, after: Any) -> str:
    change = outfit_change(before, after)
    if change.clothing_changed:
        return ""
    reason = str(after.meta.get("outfit_reason") or "").strip()
    lines = [
        f"当前穿搭保持：{after.outfit or '未记录'}。",
        "本轮未完成新的换装。",
    ]
    if change.changed:
        lines.append("仅局部外观或穿戴记录发生调整；以当前记录为准。")
    if reason:
        lines.append(f"本次判断：{reason}")
    return ToolResultText("\n".join(lines), status=change.status, media="outfit", facts={
        "clothing_changed": False, "components_changed": change.components_changed,
        "appearance_changed": change.appearance_changed, "current_outfit": after.outfit,
        "reason": reason,
    })


def normalize_outfit_appearance(values: dict) -> dict[str, str]:
    """统一外观字段的边界，判断与提交使用相同字段集合。"""
    limits = {
        "style": 120,
        "hair_style": 80,
        "hair": 180,
        "makeup_style": 80,
        "makeup": 160,
        "nails_style": 80,
        "nails": 160,
    }
    return {
        "outfit": str(values.get("outfit") or "").strip(),
        **{
            key: normalize_appearance_fact(values.get(key), limit)
            for key, limit in limits.items()
        },
    }


def select_outfit_appearance(
    decision: str, stored: dict[str, str], generated: dict[str, str]
) -> dict[str, str]:
    """按已验证的决定合并外观；换衣不自动清除妆容、美甲。"""
    if decision == "keep":
        same_outfit = not generated["outfit"] or generated["outfit"] == stored["outfit"]
        return {
            key: value or (generated[key] if same_outfit and key != "outfit" else "")
            for key, value in stored.items()
        }
    final = (
        {key: value or stored[key] for key, value in generated.items()}
        if decision == "partial_change"
        else dict(generated)
    )
    for key in ("makeup", "nails"):
        style = key + "_style"
        final[key] = generated[key] or stored[key]
        final[style] = generated[style] or ("" if generated[key] else stored[style])
    return final


@dataclass(frozen=True, slots=True)
class OutfitComponentProjection:
    stored: dict
    effective: dict
    visible_outfit: str
    changed: bool


def prepare_outfit_components(
    stored_value: Any,
    generated_value: Any,
    catalog_value: Any,
    *,
    full_change: bool,
    component_only: bool,
    scene_category: str,
    catalog_selected: bool,
    generated_outfit: str,
) -> OutfitComponentProjection:
    """合并实际穿戴状态；局部调整保留主体衣物，场合变化按部件处理。"""
    stored = normalize_outfit_components(stored_value)
    generated = normalize_outfit_components(generated_value)
    catalog = normalize_outfit_components(catalog_value)
    if component_only:
        generated.pop("main_clothing", None)
        catalog.pop("main_clothing", None)
    if (
        full_change
        and generated_outfit
        and "main_clothing" not in generated
        and "main_clothing" not in catalog
    ):
        generated["main_clothing"] = {"state": "worn", "description": generated_outfit}
    effective = merge_outfit_components({} if full_change else stored, generated)
    # 套装资料描述的是可搭配的外观；已确认的主体拆分和穿脱状态优先。
    effective = reconcile_outfit_components_for_scene(
        effective,
        scene_category,
        catalog_components=catalog,
        catalog_selected=catalog_selected,
    )
    visible = (
        format_outfit_components(effective)
        if effective and (not component_only or stored.get("main_clothing"))
        else ""
    )
    changed = bool(
        effective != stored if (effective or stored) else generated or catalog
    )
    return OutfitComponentProjection(stored, effective, visible, changed)
