"""模型上下文的无损整理与按事实分配预算，不参与生活语义裁定。"""

from __future__ import annotations

import json
import math
from collections import OrderedDict
from copy import deepcopy
from typing import Any


def compact_json(value: Any) -> str:
    """稳定字段顺序和紧凑编码，让相同资料保持相同前缀。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def token_estimate(text: str) -> int:
    """仅用于本地预算的保守估算；计费统计始终读取接口真实 usage。"""
    return math.ceil(sum(1 if ord(char) > 127 else 0.25 for char in text))


class EvidenceCache:
    """仅复用同一证据版本的成功结果；实例随所属服务释放。"""

    def __init__(self, limit: int = 64) -> None:
        self.limit = limit
        self._values: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str) -> Any:
        if key not in self._values:
            return None
        self._values.move_to_end(key)
        return deepcopy(self._values[key])

    def put(self, key: str, value: Any) -> None:
        self._values[key] = deepcopy(value)
        self._values.move_to_end(key)
        while len(self._values) > self.limit:
            self._values.popitem(last=False)


CHAT_CONTEXT_CHARS = 6000
CHAT_CONTEXT_TOKENS = 5000


def budget_chat_context(text: str, *, chars: int = CHAT_CONTEXT_CHARS,
                        tokens: int = CHAT_CONTEXT_TOKENS) -> str:
    """普通聊天的硬预算；按完整记录取舍，无无限豁免区，不截断事实句。"""
    # 只识别插件结构标签；不匹配用户话题、关键词或语言风格。
    priority = {
        "HiddenResidenceRefresh": 0, "HiddenScheduleUnavailable": 0,
        "HiddenInteractionContext": 0, "HiddenPersonFacts": 0,
        "HiddenMemoryCorrections": 0, "HiddenMemoryBoundaries": 0,
        "HiddenTime": 0, "ConversationTiming": 0,
        "HiddenCommitmentHint": 1, "HiddenActivityHint": 1,
        "HiddenContinuousLife": 1, "HiddenAppearanceHint": 1,
        "HiddenState": 1, "HiddenRestDelay": 1,
        "HiddenRecentVideoUnderstanding": 1, "HiddenStructuredConversation": 1,
        "HiddenExpressionHabits": 2, "HiddenRelevantLife": 2,
    }
    caps = {
        "HiddenInteractionContext": 1100, "HiddenPersonFacts": 1000,
        "HiddenCommitmentHint": 800, "HiddenRecentVideoUnderstanding": 1200,
        "HiddenStructuredConversation": 1200, "HiddenRelevantLife": 1000,
        "HiddenExpressionHabits": 650, "HiddenCognition": 700,
    }
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in {"<daily_life>", "</daily_life>"} or not stripped:
            continue
        tag, sep, _ = stripped.removeprefix("[").partition("]")
        if stripped.startswith("[") and sep:
            sections.append((tag, [line]))
        elif sections:
            sections[-1][1].append(line)
        else:
            sections.append(("", [line]))
    opening, closing = "<daily_life>\n", "\n</daily_life>"
    footer = "\n未列出的生活细节不代表没有发生；需要时读取记录，不能自行补全。"
    base = opening + closing + footer
    if len(base) > chars or token_estimate(base) > tokens:
        return ""
    remaining_chars = chars - len(base)
    remaining_tokens = tokens - token_estimate(base)
    chosen: dict[int, str] = {}
    seen = set()
    for index in sorted(range(len(sections)), key=lambda i: priority.get(sections[i][0], 3)):
        tag, lines = sections[index]
        identity = (tag, tuple(lines))
        if identity in seen:
            continue
        cap = min(caps.get(tag, 500), remaining_chars)
        selected = []
        used_chars = used_tokens = 0
        for line_index, line in enumerate(lines):
            cost = len(line) + 1
            tcost = token_estimate(line + "\n")
            if used_chars + cost > cap or used_tokens + tcost > remaining_tokens:
                # 标题容不下则整块略过；记录太长则跳过整条，不能截去否定或时间边界。
                if line_index == 0:
                    break
                continue
            selected.append(line)
            used_chars += cost
            used_tokens += tcost
        inline = bool(lines[0].partition("]")[2].strip()) if tag else True
        if selected and (len(selected) > 1 or inline):
            chosen[index] = "\n".join(selected)
            seen.add(identity)
            remaining_chars -= used_chars
            remaining_tokens -= used_tokens
    return opening + "\n".join(chosen[i] for i in sorted(chosen)) + footer + closing
