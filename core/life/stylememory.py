"""仪容的实际使用与设计范围；不从外观措辞推断类别或变化周期。"""
from __future__ import annotations

import json

from ..models import STYLE_CATALOG_KIND_SET, STYLE_CATALOG_KINDS

PERSONAL_STYLE_KINDS = ("hair", "makeup", "nails")
STYLE_USAGE_KEY = "style_usage_context"
DESIGN_FRAMES = {
    "full_body": "竖幅全身构图，从头到鞋完整入镜，穿搭结构清晰可辨。",
    "upper_body": "上半身构图，清楚呈现上装、叠穿和目标配饰。",
    "portrait": "头肩近景，清楚呈现发型与面部妆容细节。",
    "hands": "手部近景，以自然手势清楚呈现指甲的形状、颜色和设计。",
    "detail": "目标单品的近景，清楚呈现其结构、材质外观与佩戴方式。",
}


def style_values(meta: dict) -> dict:
    return {kind: {"name": str(meta.get(kind + "_style") or ""),
                   "description": str(meta.get(kind) or "")}
            for kind in PERSONAL_STYLE_KINDS}


def usage_context(meta: dict) -> dict:
    try:
        value = json.loads(meta.get(STYLE_USAGE_KEY) or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def record_style_transition(day, previous_meta: dict, *, at: str, reason: str, origin: str) -> None:
    """仅在实际结算时调用；未变化的部件保留自己的开始时间。"""
    previous, current = style_values(previous_meta), style_values(day.meta)
    context = usage_context(previous_meta)
    for kind, value in current.items():
        if value != previous[kind]:
            context[kind] = {**value, "changed_at": at, "reason": reason[:500],
                             "origin": origin, "transition": "selected" if any(value.values()) else "removed"}
    if context:
        day.meta[STYLE_USAGE_KEY] = json.dumps(context, ensure_ascii=False, separators=(",", ":"))


def style_usage_state(snapshot: dict) -> dict:
    state = {}
    for event in snapshot.get("style_history", []):
        value = event.get("payload") or {}
        kind = value.get("category")
        if kind in PERSONAL_STYLE_KINDS and kind not in state:
            state[kind] = {**value, "recorded_at": event["occurred_at"]}
    return state


def design_categories(value, *, default: tuple = ()) -> list[str] | None:
    if value is None:
        return list(default)
    if (not isinstance(value, list) or not value
            or any(not isinstance(kind, str) or kind not in STYLE_CATALOG_KIND_SET for kind in value)):
        return None
    return list(dict.fromkeys(value))


def style_comparisons(items, snapshot: dict, *, per_kind: int = 8) -> list[dict]:
    """各类别分别留出比较位置，避免大量套装挤掉妆容与美甲。"""
    result = []
    for kind in STYLE_CATALOG_KINDS:
        candidates = [item for item in items if item.kind == kind and item.status == "active"
                      and (kind in PERSONAL_STYLE_KINDS or
                           snapshot.get("items", {}).get(str(item.id), {}).get("ownership") == "owned")]
        candidates.sort(key=lambda item: str(item.last_used_at or ""), reverse=True)
        result.extend({"id": item.id, "kind": kind, "description": item.description[:400]}
                      for item in candidates[:per_kind])
    return result


def target_style_items(items, categories: list[str]) -> list:
    if not categories:
        return list(items)
    kinds = set(categories)
    if "outfit" in kinds:
        kinds.update(("top", "bottom", "footwear", "accessory"))
    return [item for item in items if item.kind in kinds]


def design_targets_present(items, categories: list[str]) -> bool:
    kinds = {item.kind for item in items if item.status == "active"}
    return all(("outfit" in kinds or {"top", "bottom"} <= kinds) if kind == "outfit"
               else kind in kinds for kind in categories)
