import json

OUTFIT_SCENE_CATEGORY_ENUM = "home | sleep | outdoor | public | mixed"
OUTFIT_STYLE_POOL_ENUM = "sleep_styles | outfit_styles | mixed"
OUTFIT_CURRENT_BASIS_ENUM = "stored | occurred_schedule | live_state | action_intent"

_VALID_OUTFIT_SCENE_CATEGORIES = {"home", "sleep", "outdoor", "public", "mixed"}
_VALID_OUTFIT_DECISIONS = {"keep", "change", "partial_change", "sleepwear", "outdoor"}
_VALID_OUTFIT_STYLE_POOLS = {"sleep_styles", "outfit_styles", "mixed"}
_VALID_OUTFIT_CURRENT_BASES = {"stored", "occurred_schedule", "live_state", "action_intent"}
OUTFIT_COMPONENT_KEYS = (
    "main_clothing",
    "footwear",
    "outer_layer",
    "carried_accessories",
)
_VALID_OUTFIT_COMPONENT_STATES = {
    "worn",
    "carried",
    "staged",
    "removed",
    "unknown",
}

_OUTFIT_SCENE_CATEGORY_LABELS = {
    "home": "居家",
    "sleep": "睡眠/休息",
    "outdoor": "户外",
    "public": "公共场合",
    "mixed": "混合场景",
}

_OUTFIT_STYLE_POOL_LABELS = {
    "sleep_styles": "居家/睡眠风格",
    "outfit_styles": "日常/外出风格",
    "mixed": "混合风格",
}


def normalize_outfit_scene_category(value: object, default: str = "mixed") -> str:
    text = str(value or "").strip().lower()
    return text if text in _VALID_OUTFIT_SCENE_CATEGORIES else default


def scene_category_for_place_kind(value: object, default: str = "") -> str:
    """根据当前时间轴的地点，确定对应的实际穿搭场景。"""

    place_kind = str(value or "").strip().lower()
    return {
        "home": "home",
        "transit": "outdoor",
        "poi": "public",
        "generic": "public",
    }.get(place_kind, default)


def style_pool_for_scene_category(value: object) -> str:
    category = normalize_outfit_scene_category(value)
    if category == "sleep":
        return "sleep_styles"
    if category in {"outdoor", "public"}:
        return "outfit_styles"
    return "mixed"


def normalize_outfit_style_pool(value: object, default: str = "mixed") -> str:
    text = str(value or "").strip().lower()
    return text if text in _VALID_OUTFIT_STYLE_POOLS else default


def resolve_outfit_style_pool(
    scene_category: object,
    *,
    decision: object = "",
    requested: object = "",
    current: object = "",
) -> str:
    normalized_decision = normalize_outfit_decision(decision)
    current_text = normalize_outfit_style_pool(current, default="")
    requested_text = normalize_outfit_style_pool(requested, default="")

    if normalized_decision == "sleepwear":
        return "sleep_styles"
    if normalized_decision == "outdoor":
        return "outfit_styles"
    if normalized_decision == "keep" and current_text:
        return current_text
    if normalized_decision == "partial_change" and current_text:
        return "mixed" if requested_text == "mixed" else current_text
    if requested_text:
        return requested_text
    return style_pool_for_scene_category(scene_category)


def outfit_scene_category_label(value: object) -> str:
    category = normalize_outfit_scene_category(value)
    return _OUTFIT_SCENE_CATEGORY_LABELS.get(
        category, _OUTFIT_SCENE_CATEGORY_LABELS["mixed"]
    )


def outfit_style_pool_label(value: object) -> str:
    text = str(value or "").strip().lower()
    return _OUTFIT_STYLE_POOL_LABELS.get(text, _OUTFIT_STYLE_POOL_LABELS["mixed"])


def normalize_outfit_decision(value: object, default: str = "keep") -> str:
    text = str(value or "").strip().lower()
    return text if text in _VALID_OUTFIT_DECISIONS else default


def normalize_outfit_current_basis(value: object, default: str = "stored") -> str:
    text = str(value or "").strip().lower()
    return text if text in _VALID_OUTFIT_CURRENT_BASES else default


def normalize_outfit_components(value: object) -> dict[str, dict[str, str]]:
    """规范化模型返回的穿搭组成记录，不依赖具体场景。"""
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for key in OUTFIT_COMPONENT_KEYS:
        item = raw.get(key)
        if isinstance(item, str):
            description = " ".join(item.split())[:240]
            state = "unknown" if description else "removed"
        elif isinstance(item, dict):
            description = " ".join(str(item.get("description") or "").split())[:240]
            state = str(item.get("state") or "unknown").strip().lower()
            if state not in _VALID_OUTFIT_COMPONENT_STATES:
                state = "unknown"
        else:
            continue
        normalized[key] = {"state": state, "description": description}
        if isinstance(item, dict):
            presence = str(item.get("home_presence") or "").strip().lower()
            if presence in {"home", "outdoor", "both"}:
                normalized[key]["home_presence"] = presence
    return normalized


def serialize_outfit_components(value: object) -> str:
    components = normalize_outfit_components(value)
    return (
        json.dumps(components, ensure_ascii=False, separators=(",", ":"))
        if components
        else ""
    )


def outfit_action_issue(value: object) -> str:
    """计划与执行共用换装结果契约，不把未知的穿戴状态结算为完成。"""
    for component, item in normalize_outfit_components(value).items():
        if item.get("state") == "unknown" and item.get("description"):
            return f"换装后的穿戴状态尚未明确：{component}"
    return ""


def merge_outfit_components(base: object, updates: object) -> dict[str, dict[str, str]]:
    """合并局部组成记录，优先保留已确认事实而非不确定信息。"""
    merged = normalize_outfit_components(base)
    for key, item in normalize_outfit_components(updates).items():
        if item.get("state") == "unknown" and key in merged:
            continue
        merged[key] = item
    return normalize_outfit_components(merged)


def reconcile_outfit_components_for_scene(
    value: object,
    scene_category: object,
    *,
    catalog_components: object = None,
    catalog_selected: bool = False,
) -> dict[str, dict[str, str]]:
    """使穿搭组成记录与当前场景保持一致。

    衣橱提供缺失的外观与场景用途，不覆盖已经确认的穿脱状态。
    组成部分没有明确场景用途时，
    可选鞋履和随身配饰在居家或睡眠场景默认处于待用状态；
    衣橱状态明确适合居家的组成部分仍可继续使用。
    """

    components = normalize_outfit_components(value)
    catalog = normalize_outfit_components(catalog_components)
    for key, item in catalog.items():
        current = components.get(key)
        if current is None or current.get("state") == "unknown":
            components[key] = dict(item)
        elif item.get("home_presence"):
            current["home_presence"] = item["home_presence"]
    return project_outfit_components_for_scene(components, scene_category)


def project_outfit_components_for_scene(
    value: object, scene_category: object
) -> dict[str, dict[str, str]]:
    """根据已保存的穿搭事实，确定当前场景中可见的组成部分。

    此操作只读：外出鞋包在居家场景展示为待用，明确适合居家的
    鞋饰保留已确认状态；已脱下的物品不会因场景切换重新穿上。
    """

    components = normalize_outfit_components(value)
    category = normalize_outfit_scene_category(scene_category, default="")
    if category not in {"home", "sleep"}:
        return components
    for key in ("footwear", "carried_accessories"):
        item = components.get(key)
        if (
            not item
            or item.get("state") not in {"worn", "carried", "unknown"}
            or item.get("home_presence") in {"home", "both"}
        ):
            continue
        components[key] = {
            **item,
            "state": "staged",
        }
    return components


def synchronize_outfit_components_for_scene(
    value: object,
    scene_category: object,
    *,
    previous_scene_category: object = "",
) -> dict[str, dict[str, str]]:
    """将实际发生的场景变化应用到当前穿搭组成记录。"""

    target = normalize_outfit_scene_category(scene_category, default="")
    previous = normalize_outfit_scene_category(previous_scene_category, default="")
    components = reconcile_outfit_components_for_scene(value, target)
    if target in {"outdoor", "public"} and previous in {"home", "sleep"}:
        for key, state in (
            ("footwear", "worn"),
            ("carried_accessories", "carried"),
        ):
            item = components.get(key)
            if (
                item and item.get("state") == "staged"
                and item.get("home_presence") != "home"
            ):
                components[key] = {
                    **item,
                    "state": state,
                }
    return components


def format_outfit_components(value: object, *, include_staged: bool = False) -> str:
    """将人物当前可见的穿搭组成部分整理为一条简短事实。"""
    components = normalize_outfit_components(value)
    visible_states = {"worn", "carried"}
    if include_staged:
        visible_states.add("staged")
    descriptions: list[str] = []
    for key in OUTFIT_COMPONENT_KEYS:
        item = components.get(key) or {}
        if item.get("state") not in visible_states:
            continue
        description = str(item.get("description") or "").strip()
        if description and description not in descriptions:
            descriptions.append(description)
    return "；".join(descriptions)


def outfit_action_components(
    current: object, proposed: object, target: str
) -> tuple[dict[str, dict[str, str]], bool]:
    """根据动作组成区分整套换装与局部调整，保留未调整的穿着事实。"""
    existing = normalize_outfit_components(current)
    changes = normalize_outfit_components(proposed)
    if not changes and target:
        changes["main_clothing"] = {"state": "worn", "description": target}
    main = changes.get("main_clothing") or {}
    full_change = bool(
        main.get("state") == "worn"
        and main.get("description")
        != (existing.get("main_clothing") or {}).get("description")
    )
    return merge_outfit_components({} if full_change else existing, changes), full_change


def merge_outfit_catalog_references(
    previous_ids: list[int], selected_ids: list[int], kinds: dict[int, str],
    components: dict[str, dict[str, str]], *, partial: bool,
) -> list[int]:
    """局部换装按单品类型替换引用，脱下或待用的鞋包不再计入当前穿着。"""
    replaced = {kinds.get(item_id) for item_id in selected_ids}
    if "outfit" in replaced:
        replaced.update({"top", "bottom"})
    elif {"top", "bottom"} <= replaced:
        replaced.add("outfit")
    retained = (
        [item_id for item_id in previous_ids if kinds.get(item_id) not in replaced]
        if partial else []
    )
    result = []
    for item_id in [*retained, *selected_ids]:
        component = {"footwear": "footwear", "accessory": "carried_accessories"}.get(
            kinds.get(item_id)
        )
        if component and (components.get(component) or {}).get("state") in {
            "removed", "staged"
        }:
            continue
        if item_id not in result:
            result.append(item_id)
    return result


def format_outfit_component_ledger(value: object) -> str:
    components = normalize_outfit_components(value)
    state_labels = {
        "worn": "穿着",
        "carried": "携带",
        "staged": "待用",
        "removed": "已放下/脱下",
        "unknown": "未知",
    }
    labels = {
        "main_clothing": "主体服装",
        "footwear": "鞋履",
        "outer_layer": "外层",
        "carried_accessories": "随身配饰",
    }
    lines: list[str] = []
    for key in OUTFIT_COMPONENT_KEYS:
        item = components.get(key)
        if not item:
            continue
        state = state_labels.get(item.get("state", "unknown"), "未知")
        description = item.get("description") or "无"
        lines.append(f"{labels[key]}={state}：{description}")
    return "；".join(lines)


def decision_for_occurred_outfit(
    scene_category: object,
    style_pool: object,
) -> str:
    category = normalize_outfit_scene_category(scene_category)
    pool = normalize_outfit_style_pool(style_pool, default="")
    if pool == "sleep_styles" or category == "sleep":
        return "sleepwear"
    if category in {"outdoor", "public"}:
        return "outdoor"
    return "change"


OUTFIT_CONTINUITY_RULES = (
    f"scene_category 只能写 {OUTFIT_SCENE_CATEGORY_ENUM}，描述实际场景；"
    f"style_pool 只能写 {OUTFIT_STYLE_POOL_ENUM}，描述实际衣着，地点与衣着不能强制绑定。\n"
    "outfit_components 分别记录 main_clothing、footwear、outer_layer、carried_accessories。"
    "worn=已穿戴，carried=随身携带，staged=备好但未使用，removed=已脱下或放下，unknown=事实不足；"
    "description 只写物品外观，状态只写 state；主体服装不重复鞋履、外层与配饰。"
    "账本、当前场景与 outfit 同步，outfit 仅含已经穿戴或携带的组成，其他组成保留实际状态。\n"
    "决定 keep 前分别审视主体服装、鞋履、外层和随身配饰，依据舒适、清洁、天气与眼前活动取舍；"
    "适合当前场合与天气时继续穿，局部不适配时只调整对应组成。回家、进入室内或时段变化不等于已经换衣；"
    "未来安排不能替代眼前需要，短暂停留可自然延续，进入持续居家、家务、休息或睡眠节奏时重新判断舒适与清洁需要。\n"
    "keep 延续当前 outfit、style、hair_style、hair、makeup、nails；partial_change 保留主体服装，只调整确有依据的其他组成。"
    "change 须有上次换装后新出现的实际需要；sleepwear 表示洗澡或睡前的适配选择，outdoor 表示外出适配。"
    "普通相邻活动和起床不要求换主体衣物，也不为丰富记录频繁换装。换衣、换鞋或改发型不自动改变妆容、美甲，"
    "后两者持续到明确护理、卸除或重做的事实出现。"
)


__all__ = [
    "OUTFIT_CURRENT_BASIS_ENUM",
    "OUTFIT_CONTINUITY_RULES",
    "OUTFIT_SCENE_CATEGORY_ENUM",
    "OUTFIT_STYLE_POOL_ENUM",
    "decision_for_occurred_outfit",
    "normalize_outfit_current_basis",
    "normalize_outfit_components",
    "serialize_outfit_components",
    "merge_outfit_components",
    "reconcile_outfit_components_for_scene",
    "project_outfit_components_for_scene",
    "synchronize_outfit_components_for_scene",
    "format_outfit_components",
    "format_outfit_component_ledger",
    "OUTFIT_COMPONENT_KEYS",
    "normalize_outfit_decision",
    "normalize_outfit_scene_category",
    "scene_category_for_place_kind",
    "normalize_outfit_style_pool",
    "outfit_scene_category_label",
    "outfit_style_pool_label",
    "resolve_outfit_style_pool",
    "style_pool_for_scene_category",
]
