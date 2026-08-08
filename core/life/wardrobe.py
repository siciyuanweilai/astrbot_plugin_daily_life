OUTFIT_SCENE_CATEGORY_ENUM = "home | sleep | outdoor | public | mixed"
OUTFIT_STYLE_POOL_ENUM = "sleep_styles | outfit_styles | mixed"
OUTFIT_CURRENT_BASIS_ENUM = "stored | occurred_schedule | live_state"

_VALID_OUTFIT_SCENE_CATEGORIES = {"home", "sleep", "outdoor", "public", "mixed"}
_VALID_OUTFIT_DECISIONS = {"keep", "change", "partial_change", "sleepwear", "outdoor"}
_VALID_OUTFIT_STYLE_POOLS = {"sleep_styles", "outfit_styles", "mixed"}
_VALID_OUTFIT_CURRENT_BASES = {"stored", "occurred_schedule", "live_state"}

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
    "决定 keep 前分别审视主体服装、鞋履、外层和随身配饰是否适合当前活动；"
    "不能因为主体衣物仍舒适，就忽略局部组成对居住、家务、休息、睡眠、天气或公共场景的不适配。\n"
    "回家、进入室内或时段变化不等于已经换衣；衣服仍舒适干净或之后还要出门时可以继续穿。\n"
    "短暂停留且很快再次外出时可以保持合理的外出组成；进入持续居家、家务、休息或睡眠节奏时，"
    "应按实际舒适度和活动需要局部调整，不得仅因之后还有外出安排就忽略眼前状态。\n"
    "keep 延续当前 outfit、style、hair_style、hair；partial_change 只调整不适配的外层、鞋履、配饰或发型，主体衣物保持连续。\n"
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
    "normalize_outfit_decision",
    "normalize_outfit_scene_category",
    "normalize_outfit_style_pool",
    "outfit_scene_category_label",
    "outfit_style_pool_label",
    "resolve_outfit_style_pool",
    "style_pool_for_scene_category",
]
