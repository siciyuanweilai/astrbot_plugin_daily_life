from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

from astrbot.api import logger

from ..models import PreferenceRecord
from ..prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from .tools import extract_json_from_text

APPEARANCE_PREFERENCE_CATEGORIES = (
    "outfit",
    "top",
    "bottom",
    "footwear",
    "accessory",
    "hair",
    "makeup",
    "nails",
    "style",
)
AUTONOMOUS_APPEARANCE_PREFERENCE_SOURCES = frozenset(
    {"daily_review", "daily_generation", "autonomous"}
)
APPEARANCE_PRIORITY_RULE = (
    "优先级：用户当前明确要求 > 已经发生的当前穿着事实 > 短期生活纠偏 > 场景与天气适配 > "
    "近期重复抑制 > 已学习长期偏好 > 配置审美 > 模型自由发挥。"
    "长期偏好表示审美、舒适度和生活习惯，不等于每天复刻同一件具体服装；"
    "配置审美只作为软底色，当前场景不适合或用户纠偏时必须让位。"
)
PERSONA_STABLE_APPEARANCE_RULES = (
    "角色人设中明确写出的稳定外观事实（例如自然发色、固定肤色、明确的身份性外观）属于身份层，"
    "优先于视觉衣橱、参考图、同图拆出的造型候选、近期偏好和模型自由发挥。"
    "同图候选仍可补充可变的发型形状、长度、扎法、妆容、美甲、配饰和服装；与明确人设事实冲突的部分必须服从人设，"
    "不能因为候选图写了另一种发色就改写当前角色。只有用户原始话语明确要求染发、改发色或改变该稳定外观，"
    "或已有可靠生活事实确认已经发生变化时，才允许覆盖；没有明确依据时不要猜测或新增稳定外观。"
)
CURRENT_APPEARANCE_GENERATION_RULES = (
    "outfit 写当前实际穿着的完整穿搭：至少交代服装类别、主色，以及版型/松紧、材质/纹理中的有效细节；"
    "鞋袜和配饰在确实存在时写入 outfit，不得虚构没有依据的组成。\n"
    "hair_style 只写简短发型名称；hair 单独写当前可见的详细发型，"
    "按场景自然交代长度/层次、扎法/分缝、刘海/发尾、发饰/整理状态中的有效细节；"
    "makeup_style 只写简短妆容名称，makeup 单独写当前实际妆容细节；"
    "nails_style 只写简短美甲名称，nails 单独写当前实际美甲细节；"
    "没有可靠事实时对应字段留空，明确无妆、裸甲或卸除时如实写明。"
    "换衣不会自动改变美甲；只有用户明确要求、已发生的妆发护理/美甲动作或可靠实时状态才能改变对应事实。\n"
    f"{PERSONA_STABLE_APPEARANCE_RULES}\n"
    "style 只写简短审美标签，不重复服装清单。"
)
_APPEARANCE_COMPARISON_IGNORED_CHARACTERS = frozenset(
    " ，。；：、！？!?.,;:()（）[]【】{}<>《》‘’“”\n\r\t"
)
_APPEARANCE_CLAUSE_BOUNDARIES = frozenset("，。；！？!?.,;\n\r")
_MISSING_APPEARANCE_FACTS = frozenset({"未知", "unknown"})
_PERSONA_APPEARANCE_FIELDS = (
    "hair_style",
    "hair",
    "makeup_style",
    "makeup",
    "nails_style",
    "nails",
)
_PERSONA_APPEARANCE_LIMITS = {
    "hair_style": 80,
    "hair": 180,
    "makeup_style": 80,
    "makeup": 160,
    "nails_style": 80,
    "nails": 160,
}


def _clean_text(value: object, limit: int = 240) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def normalize_appearance_fact(value: object, limit: int = 240) -> str:
    """规范化可选外观事实，不把结构化缺失标记当作真实描述。"""

    text = _clean_text(value, limit)
    if text.casefold() in _MISSING_APPEARANCE_FACTS:
        return ""
    return text


def persona_appearance_values(values: object) -> dict[str, str]:
    """提取可接受人物外观审计的结构化字段。"""

    source = values if isinstance(values, dict) else {}
    return {
        field: normalize_appearance_fact(
            source.get(field), _PERSONA_APPEARANCE_LIMITS[field]
        )
        for field in _PERSONA_APPEARANCE_FIELDS
    }


class AppearanceAuditMixin:
    async def _audit_persona_appearance(
        self,
        appearances: dict[str, dict[str, str]],
        *,
        persona: str,
        original_instruction: str,
        provider,
        provider_id: str,
        subject: str,
    ) -> dict[str, dict[str, str]]:
        """依据人设语义审计候选外观，不用词面规则猜测发色。"""

        normalized = {
            str(slot): persona_appearance_values(values)
            for slot, values in appearances.items()
            if str(slot).strip()
        }
        persona = str(persona or "").strip()
        if not persona or not provider or not any(
            any(values.values()) for values in normalized.values()
        ):
            return normalized

        fixed = f"""审阅角色当前或计划外观是否与明确人设冲突。

审计规则：
{PERSONA_STABLE_APPEARANCE_RULES}
- 只校正人设明确固定且与候选冲突的外观事实；人设没有明确写出的部分保持候选原样，不猜测、不补设定。
- 发型的长度、层次、扎法、分缝、刘海、发尾、发饰和整理状态通常是可变造型；人设只明确自然发色时，只校正冲突的发色语义，不要因此删掉这些造型细节。
- 妆容、美甲同样默认是可变状态；只有人设明确固定对应事实且候选冲突时才校正。
- 判断用户是否授权改变稳定外观时，只能依据“用户原始请求”；衣橱识图、同图造型展开、模型补充、候选描述和旧记录来源标签都不等于用户授权。
- 每个槽位独立审阅，并完整返回六个字段。除必要的冲突校正外，不润色、不扩写、不改变其他事实。

返回 JSON：
{{
  "valid": true,
  "reason": "简短审计结论",
  "appearances": {{
    "槽位名称": {{
      "hair_style": "简短发型名称或空字符串",
      "hair": "详细发型或空字符串",
      "makeup_style": "简短妆容名称或空字符串",
      "makeup": "妆容细节或空字符串",
      "nails_style": "简短美甲名称或空字符串",
      "nails": "美甲细节或空字符串"
    }}
  }}
}}

{CORE_JSON_OUTPUT_RULES}"""
        dynamic = json.dumps(
            {
                "subject": str(subject or "外观记录").strip(),
                "persona": persona,
                "original_user_instruction": str(original_instruction or "").strip(),
                "appearances": normalized,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        session_id = f"daily_life_appearance_{uuid.uuid4().hex[:8]}"
        try:
            text = await self._call_llm_text(
                provider,
                cache_friendly_prompt(fixed, dynamic, dynamic_title="待审计外观"),
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
                timeout_seconds=min(
                    30.0,
                    max(8.0, float(getattr(self.config, "llm_timeout_seconds", 120))),
                ),
            )
            payload = extract_json_from_text(text)
            audited = payload.get("appearances") if isinstance(payload, dict) else None
            if not isinstance(audited, dict):
                logger.debug("[人物外观审计] 未获得有效结构化结果，保留原外观")
                return normalized

            result: dict[str, dict[str, str]] = {}
            for slot, original in normalized.items():
                candidate = audited.get(slot)
                if not isinstance(candidate, dict):
                    result[slot] = original
                    continue
                result[slot] = {
                    field: normalize_appearance_fact(
                        candidate.get(field, original[field]),
                        _PERSONA_APPEARANCE_LIMITS[field],
                    )
                    for field in _PERSONA_APPEARANCE_FIELDS
                }
            reason = str(payload.get("reason") or "").strip()
            changed = any(result.get(slot) != values for slot, values in normalized.items())
            logger.debug(
                "[人物外观审计] "
                + ("已校正与人设冲突的外观" if changed else "通过")
                + (f"：{reason[:160]}" if reason else "")
            )
            return result
        except Exception as exc:
            logger.debug(f"[人物外观审计] 执行失败，保留原外观：{exc}")
            return normalized
        finally:
            await self._cleanup_conversation(session_id)


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
            "makeup_style": "",
            "makeup": "",
            "nails_style": "",
            "nails": "",
        }
    meta = getattr(day, "meta", {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    hair_style = normalize_appearance_fact(meta.get("hair_style"), 80)
    hair = normalize_appearance_fact(meta.get("hair"), 180)
    makeup_style = normalize_appearance_fact(meta.get("makeup_style"), 80)
    makeup = normalize_appearance_fact(meta.get("makeup"), 160)
    nails_style = normalize_appearance_fact(meta.get("nails_style"), 80)
    nails = normalize_appearance_fact(meta.get("nails"), 160)
    return {
        "outfit": strip_hair_from_outfit(
            getattr(day, "outfit", ""), hair_style, hair
        ),
        "style": normalize_appearance_fact(meta.get("style"), 120),
        "hair_style": hair_style,
        "hair": hair,
        "makeup_style": makeup_style,
        "makeup": makeup,
        "nails_style": nails_style,
        "nails": nails,
    }


def format_current_appearance_context(day: Any) -> str:
    values = current_appearance_values(day)
    lines = [
        f"当前穿搭：{values['outfit']}" if values["outfit"] else "",
        f"当前穿搭风格：{values['style']}" if values["style"] else "",
        f"当前发型名称：{values['hair_style']}" if values["hair_style"] else "",
        f"当前发型细节：{values['hair']}" if values["hair"] else "",
        f"当前妆容名称：{values['makeup_style']}" if values["makeup_style"] else "",
        f"当前妆容细节：{values['makeup']}" if values["makeup"] else "",
        f"当前美甲名称：{values['nails_style']}" if values["nails_style"] else "",
        f"当前美甲细节：{values['nails']}" if values["nails"] else "",
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
    return [
        item
        for item in preferences
        if _clean_text(item.category) in categories
        and not is_autonomous_appearance_preference(item)
    ]


def is_autonomous_appearance_preference(item: PreferenceRecord) -> bool:
    """判断偏好是否由角色自己生成的外观反向学习而来。"""

    return bool(
        _clean_text(item.category) in APPEARANCE_PREFERENCE_CATEGORIES
        and _clean_text(item.source).lower()
        in AUTONOMOUS_APPEARANCE_PREFERENCE_SOURCES
    )


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
    catalog_backed: bool = False,
) -> str:
    learned = [
        item
        for item in preferences
        if not is_autonomous_appearance_preference(item)
    ]
    if appearance_only:
        learned = appearance_preferences(learned)
    learned = _unique_preferences(learned)[: max(0, limit)]
    defaults = default_appearance_preferences(config)
    parts = [f"- {APPEARANCE_PRIORITY_RULE}"]
    if learned:
        parts.append("已学习长期偏好：")
        parts.extend(_format_preference_line(item) for item in learned)
        parts.append("- 同义偏好即使存在多条也只能视为一次证据，不得叠加权重或据此复刻同一具体方案。")
        if catalog_backed:
            parts.append(
                "- 已启用视觉衣橱时，长期外观偏好只用于比较衣橱候选；"
                "具体服装必须来自本轮提供的衣橱候选，不能把偏好文字直接改写成一套新衣服。"
            )
    if defaults:
        parts.append("配置审美（由审美影响程度控制；只作为软参考）：")
        parts.extend(_format_preference_line(item) for item in defaults)
    if len(parts) == 1:
        return ""
    return "\n".join(parts)
