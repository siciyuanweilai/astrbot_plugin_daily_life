from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..models import PreferenceRecord

APPEARANCE_PREFERENCE_CATEGORIES = ("outfit", "hair", "style")
APPEARANCE_PRIORITY_RULE = (
    "优先级：用户当前明确要求 > 已经发生的当前穿着事实 > 短期生活纠偏 > 场景与天气适配 > "
    "近期重复抑制 > 已学习长期偏好 > 配置审美 > 模型自由发挥。"
    "长期偏好表示审美、舒适度和生活习惯，不等于每天复刻同一件具体服装；"
    "配置审美只作为软底色，当前场景不适合或用户纠偏时必须让位。"
)
CURRENT_APPEARANCE_GENERATION_RULES = (
    "outfit 写当前实际穿着的完整穿搭：至少交代服装类别、主色，以及版型/松紧、材质/纹理中的有效细节；"
    "鞋袜和配饰在确实存在时写入 outfit，不得虚构没有依据的组成。\n"
    "hair_style 只写简短发型名称；hair 单独写当前可见的详细发型，"
    "按场景自然交代长度/层次、扎法/分缝、刘海/发尾、发饰/整理状态中的有效细节；"
    "makeup 单独写当前实际妆容，nails 单独写当前实际美甲；没有可靠事实时留空，明确无妆、裸甲或卸除时如实写明。"
    "换衣不会自动改变美甲；只有用户明确要求、已发生的妆发护理/美甲动作或可靠实时状态才能改变对应事实。\n"
    "style 只写简短审美标签，不重复服装清单。"
)
_APPEARANCE_COMPARISON_IGNORED_CHARACTERS = frozenset(
    " ，。；：、！？!?.,;:()（）[]【】{}<>《》‘’“”\n\r\t"
)
_APPEARANCE_CLAUSE_BOUNDARIES = frozenset("，。；！？!?.,;\n\r")


def _clean_text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _appearance_characters(value: object) -> list[str]:
    return [
        character
        for character in str(value or "")
        if character not in _APPEARANCE_COMPARISON_IGNORED_CHARACTERS
    ]


def _appearance_pairs(value: object) -> list[str]:
    characters = _appearance_characters(value)
    return [
        f"{characters[index]}{characters[index + 1]}"
        for index in range(len(characters) - 1)
    ]


def _appearance_texts_overlap(left: object, right: object) -> bool:
    left_pairs = _appearance_pairs(left)
    right_pairs = _appearance_pairs(right)
    if not left_pairs or not right_pairs:
        return False
    shorter, longer = (
        (left_pairs, set(right_pairs))
        if len(left_pairs) <= len(right_pairs)
        else (right_pairs, set(left_pairs))
    )
    matched = sum(1 for pair in shorter if pair in longer)
    return matched >= 2 and matched / len(shorter) >= 0.55


def _appearance_clauses(value: object) -> list[str]:
    clauses = []
    current = []
    for character in str(value or ""):
        current.append(character)
        if character in _APPEARANCE_CLAUSE_BOUNDARIES:
            clauses.append("".join(current))
            current = []
    if current:
        clauses.append("".join(current))
    return clauses


def strip_hair_from_outfit(outfit: object, hair_style: object, hair: object) -> str:
    """从穿搭展示文本中移除已由结构化发型字段承载的片段。"""

    outfit_text = _clean_text(outfit, 280)
    normalized_hair_style = "".join(_appearance_characters(hair_style))
    appearance_reference = _clean_text(
        " ".join(value for value in (str(hair_style or ""), str(hair or "")) if value),
        260,
    )
    if not appearance_reference:
        return outfit_text
    clothing_clauses = []
    for clause in _appearance_clauses(outfit_text):
        normalized_clause = "".join(_appearance_characters(clause))
        contains_hair_style = bool(
            len(normalized_hair_style) >= 2
            and normalized_hair_style in normalized_clause
        )
        if contains_hair_style or _appearance_texts_overlap(
            appearance_reference, clause
        ):
            continue
        clothing_clauses.append(clause)
    result = "".join(clothing_clauses).strip()
    return result.rstrip(" ，。；：、！？!?.,;:\n\r\t")


def current_appearance_values(day: Any) -> dict[str, str]:
    if day is None:
        return {
            "outfit": "",
            "style": "",
            "hair_style": "",
            "hair": "",
            "makeup": "",
            "nails": "",
        }
    meta = getattr(day, "meta", {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    hair_style = _clean_text(meta.get("hair_style"), 80)
    hair = _clean_text(meta.get("hair"), 180)
    makeup = _clean_text(meta.get("makeup"), 160)
    nails = _clean_text(meta.get("nails"), 160)
    return {
        "outfit": strip_hair_from_outfit(
            getattr(day, "outfit", ""), hair_style, hair
        ),
        "style": _clean_text(meta.get("style"), 120),
        "hair_style": hair_style,
        "hair": hair,
        "makeup": makeup,
        "nails": nails,
    }


def format_current_appearance_context(day: Any) -> str:
    values = current_appearance_values(day)
    lines = [
        f"当前穿搭：{values['outfit']}" if values["outfit"] else "",
        f"当前穿搭风格：{values['style']}" if values["style"] else "",
        f"当前发型名称：{values['hair_style']}" if values["hair_style"] else "",
        f"当前发型细节：{values['hair']}" if values["hair"] else "",
        f"当前妆容：{values['makeup']}" if values["makeup"] else "",
        f"当前美甲：{values['nails']}" if values["nails"] else "",
    ]
    return "\n".join(line for line in lines if line)


def _preference_key(item: PreferenceRecord) -> tuple[str, str]:
    return (_clean_text(item.category, 40), _clean_text(item.content))


def default_appearance_preferences(config: Any) -> list[PreferenceRecord]:
    outfit = getattr(config, "outfit", None)
    if not outfit:
        return []
    try:
        weight = float(getattr(outfit, "default_preference_weight", 0.0))
    except (TypeError, ValueError):
        weight = 0.0
    weight = max(0.0, min(weight, 2.0))
    if weight <= 0:
        return []
    seeds = (
        ("outfit", getattr(outfit, "default_style_preference", "")),
        ("hair", getattr(outfit, "default_hair_preference", "")),
    )
    return [
        PreferenceRecord(
            category=category,
            content=text,
            weight=weight,
            evidence="配置审美",
            source="config",
        )
        for category, raw in seeds
        if (text := _clean_text(raw))
    ]


def appearance_preferences(
    preferences: Iterable[PreferenceRecord],
) -> list[PreferenceRecord]:
    categories = set(APPEARANCE_PREFERENCE_CATEGORIES)
    return [item for item in preferences if _clean_text(item.category) in categories]


def _unique_preferences(
    preferences: Iterable[PreferenceRecord],
) -> list[PreferenceRecord]:
    seen: set[tuple[str, str]] = set()
    result: list[PreferenceRecord] = []
    for item in preferences:
        key = _preference_key(item)
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(item)
    result.sort(
        key=lambda item: (
            float(item.weight or 0.0),
            _clean_text(item.last_seen, 40),
            int(item.id or 0),
        ),
        reverse=True,
    )
    return result


def _format_preference_line(item: PreferenceRecord) -> str:
    evidence = _clean_text(item.evidence or item.source, 80)
    suffix = f"，证据：{evidence}" if evidence else ""
    return f"- [{_clean_text(item.category, 40)}] {_clean_text(item.content)} (权重 {float(item.weight or 0.0):.1f}{suffix})"


def format_life_preference_context(
    preferences: Iterable[PreferenceRecord],
    config: Any,
    *,
    limit: int,
    appearance_only: bool = False,
) -> str:
    learned = list(preferences)
    if appearance_only:
        learned = appearance_preferences(learned)
    learned = _unique_preferences(learned)[: max(0, limit)]
    defaults = default_appearance_preferences(config)
    parts = [f"- {APPEARANCE_PRIORITY_RULE}"]
    if learned:
        parts.append("已学习长期偏好：")
        parts.extend(_format_preference_line(item) for item in learned)
        parts.append("- 同义偏好即使存在多条也只能视为一次证据，不得叠加权重或据此复刻同一具体方案。")
    if defaults:
        parts.append("配置审美（由审美影响程度控制；只作为软参考）：")
        parts.extend(_format_preference_line(item) for item in defaults)
    if len(parts) == 1:
        return ""
    return "\n".join(parts)
