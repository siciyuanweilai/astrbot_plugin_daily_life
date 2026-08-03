from __future__ import annotations

from typing import Any

DECISION_CATEGORIES = frozenset(
    {"conversation", "memory", "proactive", "expression", "other"}
)

PROACTIVE_DECISION_STAGES = frozenset(
    {
        "proposal",
        "commit",
        "waiting",
        "cooldown",
        "sending",
        "interrupted",
        "abandoned",
    }
)

_ACTION_CATEGORIES = {
    "ignore": "conversation",
    "reply": "conversation",
    "observe": "conversation",
    "watch": "conversation",
    "watch_only": "conversation",
    "skim": "conversation",
    "reactivate": "conversation",
    "defer": "conversation",
    "comfort": "conversation",
    "push_back": "conversation",
    "join_ritual": "conversation",
    "eat_melon": "conversation",
    "interrupt": "conversation",
    "no_reply": "conversation",
    "regular_reply": "conversation",
    "previous_reply": "conversation",
    "wait": "conversation",
    "cooldown": "conversation",
    "air_delay": "conversation",
    "skip": "conversation",
    "none": "conversation",
    "remember": "memory",
    "save": "memory",
    "save_memory": "memory",
    "skip_memory": "memory",
    "need_deep_analysis": "memory",
    "deep_analysis": "memory",
    "voice_expression": "expression",
    "text_expression": "expression",
    "proactive_reply": "proactive",
    "private_revisit": "proactive",
    "proactive_observe": "proactive",
    "proactive_wait": "proactive",
    "proactive_skip": "proactive",
}

_PROACTIVE_SOURCES = frozenset({"proactive_reply", "private_revisit"})
_LEGACY_SCENE_SOURCES = {
    "闲时回复": "proactive_reply",
    "私聊回访": "private_revisit",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _legacy_proactive_action(action: str) -> tuple[str, str]:
    parts = action.split("_", 2)
    if len(parts) != 3 or parts[0] != "proactive":
        return "", ""
    stage = parts[1]
    if stage not in PROACTIVE_DECISION_STAGES:
        return "", ""
    return stage, parts[2]


def normalize_action_decision_dimensions(
    *,
    action: Any = "",
    scene_type: Any = "",
    category: Any = "",
    source: Any = "",
    stage: Any = "",
    outcome: Any = "",
) -> dict[str, str]:
    """补齐动作裁定的稳定分类维度。

    旧记录只按既有动作枚举和主动裁定协议格式回填，不读取理由文本。
    """

    action_text = _text(action)
    action_key = action_text.lower()
    scene_text = _text(scene_type)
    category_text = _text(category).lower()
    source_text = _text(source).lower()
    stage_text = _text(stage).lower()
    outcome_text = _text(outcome).lower()

    legacy_stage, legacy_outcome = _legacy_proactive_action(action_key)
    scene_source, separator, scene_stage = scene_text.partition("/")
    if not source_text:
        source_text = _LEGACY_SCENE_SOURCES.get(scene_source, "")
    normalized_scene_stage = scene_stage.lower()
    if (
        not stage_text
        and separator
        and normalized_scene_stage in PROACTIVE_DECISION_STAGES
    ):
        stage_text = normalized_scene_stage
    if not stage_text:
        stage_text = legacy_stage
    if not outcome_text:
        outcome_text = legacy_outcome

    if action_key in _PROACTIVE_SOURCES and not source_text:
        source_text = action_key
    if not outcome_text:
        outcome_text = {
            "proactive_reply": "reply",
            "private_revisit": "reply",
        }.get(action_key, action_key)

    if category_text not in DECISION_CATEGORIES:
        category_text = ""
    if not category_text:
        if source_text in _PROACTIVE_SOURCES or legacy_stage:
            category_text = "proactive"
        else:
            category_text = _ACTION_CATEGORIES.get(action_key, "other")

    return {
        "decision_category": category_text,
        "decision_source": source_text,
        "decision_stage": stage_text,
        "decision_outcome": outcome_text,
    }


__all__ = [
    "DECISION_CATEGORIES",
    "PROACTIVE_DECISION_STAGES",
    "normalize_action_decision_dimensions",
]
