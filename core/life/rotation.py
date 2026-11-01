"""按实物与已发生的穿着汇总轮换依据，不从描述措辞判断是否换新。"""
from __future__ import annotations

import datetime

from .dressing import digest

MAIN_KINDS = frozenset({"outfit", "top", "bottom"})


def wardrobe_rotation(items, snapshot: dict, date: str) -> dict:
    """整套与拆分条目共享实物身份；鞋饰调整和重复回执不增加穿着日数。"""
    from .wardrobefit import item_traits

    try:
        today = datetime.date.fromisoformat(str(date)[:10])
    except ValueError:
        return {"items": {}, "scenes": [], "recent_combinations": []}
    states = snapshot.get("items") or {}
    catalog = {item.id: item for item in items if item.kind in MAIN_KINDS}

    def units(item_id):
        state = states.get(str(item_id)) or {}
        # 没有实物映射时不把相似描述或同图外观当成同一件衣服。
        return set(state.get("unit_ids") or [f"catalog:{item_id}"])

    unit_days: dict[str, set[str]] = {}
    combinations: dict[tuple, set[str]] = {}
    history = snapshot.get("wear_history", snapshot.get("events", []))
    for event in history:
        if event.get("kind") != "wear":
            continue
        try:
            day = datetime.date.fromisoformat(str(event.get("occurred_at") or "")[:10])
        except ValueError:
            continue
        if not 0 <= (today - day).days < 7:
            continue
        actual = set()
        for item_id in (event.get("payload") or {}).get("item_ids", []):
            if type(item_id) is int and item_id in catalog:
                actual.update(units(item_id))
        if not actual:
            continue
        combinations.setdefault(tuple(sorted(actual)), set()).add(day.isoformat())
        for unit in actual:
            unit_days.setdefault(unit, set()).add(day.isoformat())
    recent = [
        {"identity": digest(key), "unit_ids": list(key), "wear_dates": sorted(days)}
        for key, days in sorted(combinations.items())
    ]
    usage = {}
    for item_id in catalog:
        days = set().union(*(unit_days.get(unit, set()) for unit in units(item_id)))
        usage[str(item_id)] = {"physical_identity": digest(sorted(units(item_id))),
                               "wear_dates": sorted(days), "wear_days": len(days)}
    scenes = []
    for scene in ("home", "sleep", "public", "outdoor"):
        owned = [item for item in catalog.values()
                 if states.get(str(item.id), {}).get("ownership") == "owned"
                 and scene in (item_traits(item).get("scene_categories") or [])]
        if not owned:
            continue
        available = [item for item in owned if states[str(item.id)].get("available")]
        owned_units = set().union(*(units(item.id) for item in owned))
        available_units = set().union(*(units(item.id) for item in available))
        complete = {tuple(sorted(units(item.id))) for item in available if item.kind == "outfit"}
        tops = {tuple(sorted(units(item.id))) for item in available if item.kind == "top"}
        bottoms = {tuple(sorted(units(item.id))) for item in available if item.kind == "bottom"}
        # 只统计结构上的选择范围，是否适合天气、是否协调仍由 scene_choices 裁定。
        single = (len(complete) == 1 and available_units <= set(next(iter(complete)))) or (
            not complete and len(tops) == len(bottoms) == 1
        )
        repeated = [entry for entry in recent if len(entry["wear_dates"]) >= 2
                    and set(entry["unit_ids"]) <= owned_units]
        scenes.append({
            "scene_category": scene, "owned_item_ids": sorted(item.id for item in owned),
            "available_item_ids": sorted(item.id for item in available),
            "owned_unit_ids": sorted(owned_units),
            "distinct_available_outfits": len(complete),
            "single_structural_option": single,
            "recent_repeated_combinations": repeated,
            "rotation_pressure": bool(repeated or (single and owned_units - available_units)),
        })
    return {"window_days": 7, "items": usage, "scenes": scenes, "recent_combinations": recent}


def care_backlog(snapshot: dict) -> list[dict]:
    """旧待洗事实独立于近期事件窗口；同一实物的多个衣橱条目合并呈现。"""
    pending = {}
    for item_id, state in snapshot.get("items", {}).items():
        if (state.get("ownership") != "owned" or state.get("currently_worn")
                or state.get("condition") != "dirty"):
            continue
        for unit in state.get("units", []):
            if (unit.get("ownership") != "owned" or unit.get("condition") != "dirty"
                    or (unit.get("payload") or {}).get("wearing")):
                continue
            key = unit["unit_id"]
            entry = pending.setdefault(key, {"unit_id": key, "item_ids": [],
                                            "waiting_since": unit.get("changed_at", "")})
            entry["item_ids"].append(int(item_id))
    return sorted(pending.values(), key=lambda entry: (entry["waiting_since"], entry["unit_id"]))


def unreviewed_care(values, backlog: list[dict], sources: dict) -> list[dict]:
    """每件待洗衣物须得到处理或有理由的暂缓决定，不替模型决定洗涤时机。"""
    covered = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict) or value.get("decision") not in {"washing", "defer"}:
            continue
        ids, refs = value.get("item_ids"), value.get("evidence_ids")
        if not isinstance(ids, list) or not isinstance(refs, list) or not str(value.get("reason") or "").strip():
            continue
        evidenced = {sources[ref]["item_id"] for ref in refs if isinstance(ref, str)
                     and ref in sources and sources[ref].get("kind") == "inventory_state"}
        covered.update(item_id for item_id in ids if type(item_id) is int and item_id in evidenced)
    return [entry for entry in backlog if not covered.intersection(entry["item_ids"])]
