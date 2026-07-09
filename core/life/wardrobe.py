OUTFIT_SCENE_CATEGORY_ENUM = "home | sleep | outdoor | public | mixed"
OUTFIT_STYLE_POOL_ENUM = "sleep_styles | outfit_styles | mixed"

_VALID_OUTFIT_SCENE_CATEGORIES = {"home", "sleep", "outdoor", "public", "mixed"}
_VALID_OUTFIT_DECISIONS = {"keep", "change", "partial_change", "sleepwear", "outdoor"}

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


def normalize_outfit_scene_category(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in _VALID_OUTFIT_SCENE_CATEGORIES else "mixed"


def style_pool_for_scene_category(value: object) -> str:
    category = normalize_outfit_scene_category(value)
    if category in {"home", "sleep"}:
        return "sleep_styles"
    if category in {"outdoor", "public"}:
        return "outfit_styles"
    return "mixed"


def outfit_scene_category_label(value: object) -> str:
    category = normalize_outfit_scene_category(value)
    return _OUTFIT_SCENE_CATEGORY_LABELS.get(category, _OUTFIT_SCENE_CATEGORY_LABELS["mixed"])


def outfit_style_pool_label(value: object) -> str:
    text = str(value or "").strip().lower()
    return _OUTFIT_STYLE_POOL_LABELS.get(text, _OUTFIT_STYLE_POOL_LABELS["mixed"])


def normalize_outfit_decision(value: object, default: str = "keep") -> str:
    text = str(value or "").strip().lower()
    return text if text in _VALID_OUTFIT_DECISIONS else default


OUTFIT_SCENE_STYLE_RULES = (
    "scene_category 只描述当前真实场景，不要被尚未发生的未来换装节点提前覆盖。\n"
    f"scene_category 只能写 {OUTFIT_SCENE_CATEGORY_ENUM}。\n"
    "style_pool 由 scene_category 自动派生，不要额外硬写。"
)

DAILY_OUTFIT_SCENE_STYLE_RULES = (
    "先填写 life_decision.outfit.scene_category，再派生 life_decision.outfit.style_pool，最后生成顶层 outfit、style、hair。\n"
    f"scene_category 只能写 {OUTFIT_SCENE_CATEGORY_ENUM}，并且只描述当前/目标时刻已经到达的实际场景；未发生的未来换装节点不能改变它。\n"
    f"style_pool 只能写 {OUTFIT_STYLE_POOL_ENUM}；home/sleep 对应 sleep_styles，outdoor/public 对应 outfit_styles，mixed 对应 mixed。\n"
    "顶层 outfit 和 life_decision.outfit.style/hair 必须与 style_pool 一致；不要把外出风格写成居家睡衣，也不要把居家/睡前状态写成正式外出装。"
)


__all__ = [
    "DAILY_OUTFIT_SCENE_STYLE_RULES",
    "OUTFIT_SCENE_CATEGORY_ENUM",
    "OUTFIT_SCENE_STYLE_RULES",
    "OUTFIT_STYLE_POOL_ENUM",
    "normalize_outfit_decision",
    "normalize_outfit_scene_category",
    "outfit_scene_category_label",
    "outfit_style_pool_label",
    "style_pool_for_scene_category",
]
