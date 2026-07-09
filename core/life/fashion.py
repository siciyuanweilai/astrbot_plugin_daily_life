def compact_text(value: object) -> str:
    return "".join(str(value or "").split())


def outfit_style_contamination_reason(
    style: object,
    *,
    theme: object = "",
    mood: object = "",
    schedule_type: object = "",
) -> str:
    style_text = compact_text(style)
    if not style_text:
        return ""
    for label, value in (
        ("今日主题", theme),
        ("心情色彩", mood),
        ("日程类型", schedule_type),
    ):
        reference = compact_text(value)
        if reference and reference in style_text:
            return (
                f"life_decision.outfit.style 混入了{label}「{value}」，"
                "请只写穿搭风格，例如甜美制服风、元气休闲风或奶呼呼毛绒风"
            )
    return ""


__all__ = ["outfit_style_contamination_reason"]
