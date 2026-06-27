OUTFIT_SCENE_CATEGORY_ENUM = "home | sleep | outdoor | public | mixed"
OUTFIT_STYLE_POOL_ENUM = "sleep_styles | outfit_styles | mixed"

OUTFIT_SCENE_STYLE_RULES = (
    "必须先填写 scene_category，再由 scene_category 派生 style_pool，最后再生成 outfit/style/hair。\n"
    f"scene_category 只能写 {OUTFIT_SCENE_CATEGORY_ENUM}；它只描述当前实际场景，不能被尚未发生的未来换装节点覆盖。\n"
    f"style_pool 只能写 {OUTFIT_STYLE_POOL_ENUM}；home/sleep 对应 sleep_styles，outdoor/public 对应 outfit_styles，mixed 对应 mixed。\n"
    "素材库只作为风格参考，不是硬性抽签；可以自然自创，但 outfit/style/hair 必须和 style_pool 一致。"
)

DAILY_OUTFIT_SCENE_STYLE_RULES = (
    "先填写 life_decision.outfit.scene_category，再派生 life_decision.outfit.style_pool，最后生成顶层 outfit、style、hair。\n"
    f"scene_category 只能写 {OUTFIT_SCENE_CATEGORY_ENUM}，并且只描述当前/目标时刻已经到达的实际场景；未发生的未来换装节点不能改变它。\n"
    f"style_pool 只能写 {OUTFIT_STYLE_POOL_ENUM}；home/sleep 对应 sleep_styles，outdoor/public 对应 outfit_styles，mixed 对应 mixed。\n"
    "素材库不是硬性抽签，但顶层 outfit 和 life_decision.outfit.style/hair 必须与 style_pool 一致；不要把外出风格写成居家睡衣，也不要把居家/睡前状态写成正式外出装。"
)


__all__ = [
    "DAILY_OUTFIT_SCENE_STYLE_RULES",
    "OUTFIT_SCENE_CATEGORY_ENUM",
    "OUTFIT_SCENE_STYLE_RULES",
    "OUTFIT_STYLE_POOL_ENUM",
]
