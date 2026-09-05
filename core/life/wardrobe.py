import json


OUTFIT_SCENE_CATEGORY_ENUM = "home | sleep | outdoor | public | mixed"
OUTFIT_STYLE_POOL_ENUM = "sleep_styles | outfit_styles | mixed"
OUTFIT_CURRENT_BASIS_ENUM = "stored | occurred_schedule | live_state"

_VALID_OUTFIT_SCENE_CATEGORIES = {"home", "sleep", "outdoor", "public", "mixed"}
_VALID_OUTFIT_DECISIONS = {"keep", "change", "partial_change", "sleepwear", "outdoor"}
_VALID_OUTFIT_STYLE_POOLS = {"sleep_styles", "outfit_styles", "mixed"}
_VALID_OUTFIT_CURRENT_BASES = {"stored", "occurred_schedule", "live_state"}
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
    """Map the current timeline location to the outfit scene it actually implies."""

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
    """Normalize the scene-independent component ledger returned by the model."""
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
    return normalized


def serialize_outfit_components(value: object) -> str:
    components = normalize_outfit_components(value)
    return (
        json.dumps(components, ensure_ascii=False, separators=(",", ":"))
        if components
        else ""
    )


def merge_outfit_components(base: object, updates: object) -> dict[str, dict[str, str]]:
    """Merge a partial ledger while keeping confirmed facts over uncertainty."""
    merged = normalize_outfit_components(base)
    for key, item in normalize_outfit_components(updates).items():
        if item.get("state") == "unknown" and key in merged:
            continue
        merged[key] = item
    return merged


def reconcile_outfit_components_for_scene(
    value: object,
    scene_category: object,
    *,
    catalog_components: object = None,
    catalog_selected: bool = False,
) -> dict[str, dict[str, str]]:
    """Keep the component ledger aligned with the current scene.

    Catalog states are authoritative when present. When a component has no
    explicit scene role, optional footwear and carried accessories default to
    staged in a home or sleep scene; the person can still keep a home-suitable
    component when its catalog state says so.
    """

    components = normalize_outfit_components(value)
    category = normalize_outfit_scene_category(scene_category, default="")
    if category not in {"home", "sleep"}:
        return components

    catalog = normalize_outfit_components(catalog_components)
    for key, item in catalog.items():
        state = item.get("state")
        if state in {"worn", "carried", "staged", "removed"}:
            components[key] = item

    for key in ("footwear", "carried_accessories"):
        item = components.get(key)
        if not item:
            continue
        catalog_item = catalog.get(key)
        if catalog_item and catalog_item.get("state") in {
            "worn",
            "carried",
            "staged",
            "removed",
        }:
            continue
        if item.get("state") in {"worn", "carried", "unknown"}:
            components[key] = {
                "state": "staged",
                "description": item.get("description", ""),
            }
    return components


def project_outfit_components_for_scene(
    value: object, scene_category: object
) -> dict[str, dict[str, str]]:
    """Project stored outfit facts into what is visible in the current scene.

    This is intentionally read-only: the ledger keeps the historical fact that
    shoes or a bag were worn outside, while a home scene renders them as staged.
    """

    components = normalize_outfit_components(value)
    category = normalize_outfit_scene_category(scene_category, default="")
    if category not in {"home", "sleep"}:
        return components
    for key in ("footwear", "carried_accessories"):
        item = components.get(key)
        if not item or item.get("state") not in {"worn", "carried", "unknown"}:
            continue
        components[key] = {
            "state": "staged",
            "description": item.get("description", ""),
        }
    return components


def synchronize_outfit_components_for_scene(
    value: object,
    scene_category: object,
    *,
    previous_scene_category: object = "",
) -> dict[str, dict[str, str]]:
    """Apply a real scene transition to the current component ledger."""

    target = normalize_outfit_scene_category(scene_category, default="")
    previous = normalize_outfit_scene_category(previous_scene_category, default="")
    components = reconcile_outfit_components_for_scene(value, target)
    if target in {"outdoor", "public"} and previous in {"home", "sleep"}:
        for key, state in (
            ("footwear", "worn"),
            ("carried_accessories", "carried"),
        ):
            item = components.get(key)
            if item and item.get("state") == "staged":
                components[key] = {
                    "state": state,
                    "description": item.get("description", ""),
                }
    return components


def format_outfit_components(value: object, *, include_staged: bool = False) -> str:
    """Render the components currently visible on the person into one short fact."""
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
    f"scene_category 只能写 {OUTFIT_SCENE_CATEGORY_ENUM}，只描述当前真实场景；"
    f"style_pool 只能写 {OUTFIT_STYLE_POOL_ENUM}，描述身上实际穿着，地点与衣着不能强制绑定。\n"
    "outfit 只写此刻实际穿在身上或实际携带的组成；不属于当前场景的组成进入备用状态，不写进当前穿搭。"
    "场景变化时分别维护主体服装、鞋履、外层和随身配饰的状态，不把整套造型当成不可拆分的文字。\n"
    "outfit_components 是当前穿搭的组成账本：worn 表示穿在身上，carried 表示随身携带，staged 表示已准备但尚未穿戴/携带，removed 表示已脱下或放下，unknown 只在事实不足时使用。"
    "每次更新都要让账本与 scene_category、当前活动和 outfit 同步；没有变化的组成沿用原状态，不要凭空清空或新增。\n"
    "决定 keep 前分别审视主体服装、鞋履、外层和随身配饰是否适合当前活动；不能因为主体衣物仍舒适，就忽略其他组成对居住、家务、休息、睡眠、天气或公共场景的不适配。\n"
    "回家、进入室内或时段变化不等于已经换衣；衣服仍舒适干净或之后还要出门时可以继续穿。\n"
    "换装采用事件驱动：普通相邻活动、用餐、聊天、阅读、办公或单纯时段推进时，应延续同一套合适衣服；"
    "只有起床后从睡眠穿搭转为日间穿搭、睡前/洗澡、运动或出汗、淋湿弄脏、明显冷热不适、正式程度确实变化等事实，才考虑更换主体服装。"
    "同一天再次更换主体服装必须有上一次换装之后新发生的依据，不能为了丰富生活记录反复换衣。\n"
    "短暂停留且很快再次切换场景时可以保持当前组成；进入持续居家、家务、休息或睡眠节奏时，"
    "应按实际舒适度和活动需要局部调整，不得仅因之后还有安排就忽略眼前状态。\n"
    "keep 延续当前 outfit、style、hair_style、hair、makeup、nails；partial_change 是穿戴组成的局部调整，不是完整换装，"
    "只调整有事实依据且不适配的鞋履、外层或随身配饰，主体衣物保持连续。"
    "换衣、换鞋或改变发型不会自动改变妆容和美甲，美甲尤其应保持跨场景连续，直到有明确护理、卸除或重做事实。\n"
    "淋雨、出汗、弄脏、明显不舒服或准备长时间放松/做家务时才考虑 change；"
    "洗澡或明确进入睡前状态时使用 sleepwear，转向外出且当前穿搭不合适时使用 outdoor。"
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
