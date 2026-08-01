from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .prompts import (
    CORE_JSON_OUTPUT_RULES,
    CORE_PERSONA_AUDIT_POLICY,
    CORE_PERSONA_PRONOUN_RULES,
    cache_friendly_prompt,
)


PathPart = str | int
PathPattern = tuple[str | int, ...]


def _compact(value: object, limit: int = 1200) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit]


@dataclass(frozen=True, slots=True)
class PersonFact:
    """保留原始证据的结构化人物身份锚点。"""

    name: str
    aliases: tuple[str, ...] = ()
    statement: str = ""
    source: str = "relationship"
    priority: int = 0

    def names(self) -> tuple[str, ...]:
        values: list[str] = []
        for value in (self.name, *self.aliases):
            text = _compact(value, 80)
            if text and text not in values:
                values.append(text)
        return tuple(values)


@dataclass(frozen=True, slots=True)
class PersonFactContext:
    persona: str = ""
    explicit_instruction: str = ""
    facts: tuple[PersonFact, ...] = field(default_factory=tuple)
    unverified_people: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_external_facts(self) -> bool:
        return any(fact.statement and fact.names() for fact in self.facts)

    @property
    def has_external_people(self) -> bool:
        persona = _compact(self.persona, 120)
        has_custom_persona = bool(persona and persona != "一个热爱生活的人")
        return self.has_external_facts or bool(self.unverified_people) or has_custom_persona

    def fact_for_name(self, name: str) -> PersonFact | None:
        target = _compact(name, 80).casefold()
        if not target:
            return None
        candidates = [
            fact
            for fact in self.facts
            if target in {item.casefold() for item in fact.names()}
        ]
        return max(candidates, key=lambda item: item.priority, default=None)

    def format_for_generation(
        self, *, include_persona: bool = False, include_rules: bool = True
    ) -> str:
        if not self.has_external_people:
            return ""
        lines = ["## 人物事实边界"]
        if include_rules:
            lines.append(CORE_PERSONA_PRONOUN_RULES)
        lines.extend(
            [
            "事实优先级：本轮用户明确说明 > 当前角色人设 > 已保存人物人设线索 > 其他稳定背景。",
            "同一个人的本名、昵称、备注和关系档案名需要按资料语义对应；不能根据名字形式猜测性别。",
            ]
        )
        if self.explicit_instruction:
            lines.append(f"- 本轮用户明确说明：{_compact(self.explicit_instruction, 1200)}")
        if include_persona and self.persona:
            lines.append(f"- 当前角色人设：{_compact(self.persona, 6000)}")
        for fact in sorted(self.facts, key=lambda item: item.priority, reverse=True):
            aliases = "、".join(fact.names())
            lines.append(
                f"- 人物：{aliases}；依据={fact.source}；明确线索={_compact(fact.statement, 600)}"
            )
        if self.unverified_people:
            lines.append(
                "- 暂无明确人设线索的人物："
                + "、".join(self.unverified_people)
                + "；称谓依据=证据不足；称呼策略=使用姓名或中性称呼。"
            )
        return "\n".join(lines)


def person_fact_from_relationship(record: Any) -> PersonFact | None:
    statement = _compact(getattr(record, "persona_hint", ""), 600)
    if not statement:
        return None
    name = _compact(
        getattr(record, "name", "") or getattr(record, "id", ""), 80
    )
    aliases = []
    for value in (
        getattr(record, "alias", ""),
        getattr(record, "subjective_name", ""),
    ):
        text = _compact(value, 80)
        if text and text != name and text not in aliases:
            aliases.append(text)
    if not name:
        return None
    return PersonFact(
        name=name,
        aliases=tuple(aliases),
        statement=statement,
        source="已保存人物人设线索",
        priority=200,
    )


def merge_person_facts(facts: Iterable[PersonFact]) -> tuple[PersonFact, ...]:
    merged: dict[tuple[str, ...], PersonFact] = {}
    for fact in facts:
        names = fact.names()
        if not names or not fact.statement:
            continue
        key = tuple(sorted(item.casefold() for item in names))
        previous = merged.get(key)
        if previous is None or fact.priority > previous.priority:
            merged[key] = fact
    return tuple(merged.values())


def person_fact_context_from_relationships(
    *,
    persona: str = "",
    explicit_instruction: str = "",
    relationships: Iterable[Any] | None = None,
) -> PersonFactContext:
    records = list(relationships or [])
    facts = merge_person_facts(
        fact
        for fact in (person_fact_from_relationship(item) for item in records)
        if fact is not None
    )
    verified_names = {
        name.casefold()
        for fact in facts
        for name in fact.names()
    }
    unverified: list[str] = []
    for item in records:
        name = _compact(
            getattr(item, "name", "") or getattr(item, "id", ""), 80
        )
        if name and name.casefold() not in verified_names and name not in unverified:
            unverified.append(name)
    return PersonFactContext(
        persona=str(persona or "").strip(),
        explicit_instruction=str(explicit_instruction or "").strip(),
        facts=facts,
        unverified_people=tuple(unverified[:12]),
    )


def _path_matches(path: tuple[PathPart, ...], pattern: PathPattern) -> bool:
    if len(path) != len(pattern):
        return False
    return all(expected == "*" or expected == actual for actual, expected in zip(path, pattern))


def allowed_string_fields(
    payload: Any, patterns: Sequence[PathPattern]
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []

    def visit(value: Any, path: tuple[PathPart, ...]) -> None:
        if isinstance(value, str):
            if any(_path_matches(path, pattern) for pattern in patterns):
                fields.append({"path": list(path), "value": value})
            return
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, (*path, str(key)))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, index))

    visit(payload, ())
    return fields


def apply_string_replacements(
    payload: dict[str, Any],
    replacements: Any,
    patterns: Sequence[PathPattern],
    *,
    limit: int = 32,
) -> tuple[dict[str, Any], int]:
    allowed = {
        tuple(item["path"])
        for item in allowed_string_fields(payload, patterns)
    }
    if not isinstance(replacements, list) or not allowed:
        return payload, 0
    revised = copy.deepcopy(payload)
    changed = 0
    seen: set[tuple[PathPart, ...]] = set()
    for item in replacements[:limit]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), list):
            continue
        path = tuple(item["path"])
        value = item.get("value")
        if path not in allowed or path in seen or not isinstance(value, str):
            continue
        cursor: Any = revised
        try:
            for part in path[:-1]:
                cursor = cursor[part]
            leaf = path[-1]
            if cursor[leaf] == value:
                continue
            cursor[leaf] = value
        except (IndexError, KeyError, TypeError):
            continue
        seen.add(path)
        changed += 1
    return revised, changed


def build_person_fact_audit_prompt(
    context: PersonFactContext,
    payload: dict[str, Any],
    patterns: Sequence[PathPattern],
    *,
    subject: str,
) -> str:
    fields = allowed_string_fields(payload, patterns)
    fixed = f"""审计一份{subject}里的人物身份与称谓是否和明确资料一致。
这只检查人物指代、称呼、性别、亲疏和关系归属，不评价文风、剧情、日程安排或其他内容。

{CORE_PERSONA_AUDIT_POLICY}

{CORE_JSON_OUTPUT_RULES}

只输出 JSON 对象：
{{
  "valid": true,
  "reason": "简短审计结论",
  "conflicts": ["发现的事实冲突"],
  "replacements": [{{"path": ["字段", 0, "子字段"], "value": "只修正冲突后的完整字段文本"}}]
}}

要求：
- 没有冲突时 valid=true，conflicts 和 replacements 都为空数组。
- 有冲突时 valid=false；只为确有冲突的既有字符串字段提供 replacement。
- replacement.path 必须原样使用候选字段里的路径；不能新增、删除、移动字段或数组项。
- replacement.value 必须是修正后的完整字符串字段。"""
    dynamic = f"""{context.format_for_generation(include_persona=True, include_rules=False)}

可审计字段：
{json.dumps(fields, ensure_ascii=False)}"""
    return cache_friendly_prompt(fixed, dynamic, dynamic_title="人物事实审计资料")


__all__ = [
    "PathPattern",
    "PersonFact",
    "PersonFactContext",
    "allowed_string_fields",
    "apply_string_replacements",
    "build_person_fact_audit_prompt",
    "merge_person_facts",
    "person_fact_context_from_relationships",
    "person_fact_from_relationship",
]
