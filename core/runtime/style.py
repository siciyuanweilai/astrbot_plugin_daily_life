from __future__ import annotations

import copy
import asyncio
import random
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from .markers import LOG_PREFIX


@dataclass(frozen=True)
class _ChatStyleUnit:
    body: str
    separator: str
    break_kind: str

    @property
    def text(self) -> str:
        return f"{self.body}{self.separator}"


@dataclass(frozen=True)
class _ChatStyleSegmentPlan:
    raw_text: str
    text: str
    separator: str
    break_kind: str


@dataclass(frozen=True)
class _ChatStylePendingSegment:
    text: str
    compact_length: int
    break_kind: str
    reason: str


@dataclass(frozen=True)
class _ChatStyleSegmentSource:
    normalized: str
    units: list[_ChatStyleUnit]
    explicit_line_count: int


class ChatStyleRuntimeMixin:
    _CHAT_STYLE_PENDING_SEGMENTS_ATTR = "_daily_life_chat_style_pending_segments"
    _CHAT_STYLE_SEGMENT_DELAY_RANGE = (0.8, 1.8)
    _CHAT_STYLE_SHORT_DELAY_RANGE = (0.45, 0.9)
    _CHAT_STYLE_LONG_DELAY_RANGE = (1.1, 2.1)
    _CHAT_STYLE_LEAD_SHORT_DELAY_RANGE = (0.65, 1.05)
    _CHAT_STYLE_LEAD_MEDIUM_DELAY_RANGE = (0.85, 1.35)
    _CHAT_STYLE_LEAD_LONG_DELAY_RANGE = (1.0, 1.55)
    _CHAT_STYLE_MAX_DELAY_SECONDS = 3.5
    _CHAT_STYLE_STRONG_BREAKS = frozenset({"。", "！", "？", "!", "?", "~", "～", "…"})
    _CHAT_STYLE_SOFT_BREAKS = frozenset({"，", ",", "；", ";", "、"})
    _CHAT_STYLE_URL_PREFIXES = ("https://", "http://", "www.")
    _CHAT_STYLE_URL_TRAILING_BREAKS = frozenset(" \t\r\n，。！？；、）】》")
    _CHAT_STYLE_QUOTE_PAIRS = {
        "“": "”",
        "‘": "’",
        "「": "」",
        "『": "』",
        "《": "》",
        "【": "】",
        "（": "）",
        "(": ")",
        "[": "]",
    }

    def _chat_style_enabled(self) -> bool:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        return bool(style)

    def _chat_style_astrbot_send_config(self, event: Any) -> dict[str, Any]:
        context = getattr(self, "context", None)
        getter = getattr(context, "get_config", None)
        if callable(getter):
            try:
                config = getter(getattr(event, "unified_msg_origin", None))
            except TypeError:
                config = getter()
            if isinstance(config, dict):
                return config
        config = getattr(context, "config", None)
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _chat_style_int_config(value: Any, default: int, *, minimum: int = 0) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(number, minimum)

    def _chat_style_should_keep_default_send(self, event: Any, text: str) -> bool:
        config = self._chat_style_astrbot_send_config(event)
        compact_length = len(str(text or ""))
        if not config or compact_length <= 0:
            return False
        if bool(config.get("t2i")):
            threshold = self._chat_style_int_config(config.get("t2i_word_threshold"), 150, minimum=50)
            if compact_length > threshold:
                logger.debug(
                    f"{LOG_PREFIX} 自然短句分段跳过：回复长度 {compact_length} 超过 AstrBot 文本转图像阈值 {threshold}。"
                )
                return True
        return False

    def _classify_chat_message(self, event: Any, message: str = "") -> dict[str, Any]:
        text = " ".join(str(message if message is not None else getattr(event, "message_str", "") or "").split())
        is_group = self._event_is_group_message(event) if event is not None else False
        result: dict[str, Any] = {
            "kind": "casual" if text else "unknown",
            "scope": "group" if is_group else "private",
            "reason": "轻闲聊" if text else "无可见文本",
        }
        if not text:
            return result
        result.update(kind="casual", reason="普通表达")
        return result

    def _chat_style_limit_for_event(self, event: Any) -> int:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        if not style:
            return 0
        casual_limit = int(getattr(style, "casual_max_chars", 50) or 50)
        if event is not None and self._event_is_group_message(event):
            channel_limit = int(getattr(style, "group_casual_max_chars", 30) or 30)
        else:
            channel_limit = int(getattr(style, "private_casual_max_chars", 15) or 15)
        return min(casual_limit, channel_limit) if casual_limit > 0 else channel_limit

    def _chat_style_natural_segment_delay_range(
        self,
        previous_segment: str = "",
        next_segment: str = "",
        *,
        previous_break_kind: str = "",
    ) -> tuple[float, float]:
        previous_length = len(self._chat_style_compact_text(previous_segment))
        next_length = len(self._chat_style_compact_text(next_segment))
        previous_weight = self._chat_style_typing_weight(previous_segment)
        next_weight = self._chat_style_typing_weight(next_segment)
        if 0 < previous_length <= 4 and next_length >= 5:
            if next_length <= 8:
                delay_range = self._CHAT_STYLE_LEAD_SHORT_DELAY_RANGE
            elif next_length <= 16:
                delay_range = self._CHAT_STYLE_LEAD_MEDIUM_DELAY_RANGE
            else:
                delay_range = self._CHAT_STYLE_LEAD_LONG_DELAY_RANGE
            return delay_range

        length = max(previous_weight, next_weight)
        if length <= 12:
            return self._CHAT_STYLE_SHORT_DELAY_RANGE
        if length >= 32:
            return self._CHAT_STYLE_LONG_DELAY_RANGE
        low, high = self._CHAT_STYLE_SEGMENT_DELAY_RANGE
        if previous_break_kind == "soft" and length >= 20:
            return low + 0.1, high + 0.1
        return low, high

    def _chat_style_natural_segment_delay_seconds(
        self,
        previous_segment: str = "",
        next_segment: str = "",
        *,
        previous_break_kind: str = "",
    ) -> float:
        low, high = self._chat_style_natural_segment_delay_range(
            previous_segment,
            next_segment,
            previous_break_kind=previous_break_kind,
        )
        length = max(
            self._chat_style_typing_weight(previous_segment),
            self._chat_style_typing_weight(next_segment),
        )
        if not (0 < len(self._chat_style_compact_text(previous_segment)) <= 4):
            adjustment = round(min(0.45, max(0.0, (length - 18) * 0.012)), 2)
            low += adjustment
            high += adjustment
        low = round(low, 2)
        high = round(high, 2)
        return min(self._CHAT_STYLE_MAX_DELAY_SECONDS, random.uniform(low, high))

    def _build_chat_style_decision_hint(self, event: Any, message: str, decision: dict[str, Any]) -> str:
        if not self._chat_style_enabled():
            return ""
        limit = self._chat_style_limit_for_event(event)
        scope_label = "群聊" if decision.get("scope") == "group" else "私聊"
        focus = "自然接话；轻闲聊用短气口，一句只放一个主要意思，有内容就完整说清楚。"
        lines = [
            "\n[HiddenChatDecision]",
            f"- 当前回应重心：{focus}",
            f"- 场景：{scope_label}；判断依据：{decision.get('reason') or '自然判断'}。",
        ]
        if limit > 0:
            lines.append(f"- {scope_label}轻闲聊参考长度约 {limit} 字左右；有正事时按内容自然展开。")
        return "\n".join(lines)

    async def build_chat_style_injection_context(self, event: Any, message: str = "") -> str:
        if not self._chat_style_enabled():
            return ""
        message = " ".join(str(message if message is not None else getattr(event, "message_str", "") or "").split())
        decision = self._classify_chat_message(event, message)
        if event is not None:
            setattr(event, "_daily_life_chat_style_decision", decision)
        return self._build_chat_style_decision_hint(event, message, decision)

    @staticmethod
    def _chat_style_text_is_structural(text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        if "```" in normalized:
            return True
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        starters = sum(1 for line in lines if line[:2].strip()[:1] in {"-", "*", "+", "1", "2", "3"})
        return starters >= 2

    def _replace_text_result(self, event: Any, text: str) -> bool:
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not chain:
            return False
        text_indexes = [
            index
            for index, item in enumerate(chain)
            if self._chat_style_text_component_text(item).strip()
        ]
        if text_indexes and len(text_indexes) == len(chain):
            chain[:] = [self._chat_style_copy_text_component(chain[text_indexes[0]], text)]
            return True
        for index, item in enumerate(chain):
            if isinstance(item, str):
                chain[index] = text
                return True
            if isinstance(item, dict):
                kind = str(item.get("type") or item.get("kind") or "text").strip().lower()
                if kind in {"", "text", "plain"}:
                    next_item = dict(item)
                    next_item["text"] = text
                    chain[index] = next_item
                    return True
            elif hasattr(item, "text"):
                try:
                    setattr(item, "text", text)
                    return True
                except Exception:
                    return False
        return False

    @staticmethod
    def _chat_style_text_component_text(item: Any) -> str:
        if isinstance(item, str):
            return str(item)
        if isinstance(item, dict):
            kind = str(item.get("type") or item.get("kind") or "text").strip().lower()
            if kind in {"", "text", "plain"}:
                return str(item.get("text") or item.get("content") or "")
            return ""
        return str(getattr(item, "text", "") or getattr(item, "content", "") or "")

    @staticmethod
    def _chat_style_copy_text_component(item: Any, text: str) -> Any:
        if isinstance(item, str):
            return text
        if isinstance(item, dict):
            next_item = dict(item)
            if "content" in next_item and "text" not in next_item:
                next_item["content"] = text
            else:
                next_item["text"] = text
            return next_item
        next_item = copy.deepcopy(item)
        if hasattr(next_item, "text"):
            setattr(next_item, "text", text)
        elif hasattr(next_item, "content"):
            setattr(next_item, "content", text)
        return next_item

    @classmethod
    def _chat_style_result_is_text_only(cls, event: Any) -> bool:
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        return isinstance(chain, list) and bool(chain) and all(
            cls._chat_style_text_component_text(item).strip() for item in chain
        )

    @classmethod
    def _chat_style_compact_text(cls, text: str) -> str:
        return "".join(str(text or "").split())

    @staticmethod
    def _chat_style_char_typing_weight(char: str) -> float:
        if not char or char.isspace():
            return 0.0
        code = ord(char)
        if (
            0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
            or 0x3040 <= code <= 0x30FF
            or 0xAC00 <= code <= 0xD7AF
        ):
            return 1.0
        if char.isascii():
            if char.isalpha():
                return 0.45
            if char.isdigit():
                return 0.35
            return 0.25
        if char.isalnum():
            return 0.8
        return 0.35

    @classmethod
    def _chat_style_typing_weight(cls, text: str) -> float:
        compact = cls._chat_style_compact_text(text)
        if not compact:
            return 0.0
        weight = sum(cls._chat_style_char_typing_weight(char) for char in compact)
        return round(weight, 2)

    def _chat_style_initial_typing_delay_seconds(self, text: str) -> float:
        weight = self._chat_style_typing_weight(text)
        if weight <= 0:
            return 0.0
        low = 0.35 + min(1.0, weight * 0.018)
        high = low + 0.45 + min(0.7, weight * 0.012)
        low = round(min(self._CHAT_STYLE_MAX_DELAY_SECONDS, low), 2)
        high = round(min(self._CHAT_STYLE_MAX_DELAY_SECONDS, max(low, high)), 2)
        if high <= 0:
            return 0.0
        return min(self._CHAT_STYLE_MAX_DELAY_SECONDS, random.uniform(low, high))

    @classmethod
    def _chat_style_protected_span_end(cls, text: str, index: int) -> int:
        if index < 0 or index >= len(text):
            return index
        tail = text[index:].lower()
        if text[index] == "`":
            end = text.find("`", index + 1)
            return end + 1 if end > index else index
        if any(tail.startswith(prefix) for prefix in cls._CHAT_STYLE_URL_PREFIXES):
            end = index
            while end < len(text) and text[end] not in cls._CHAT_STYLE_URL_TRAILING_BREAKS:
                end += 1
            return end
        return index

    @classmethod
    def _chat_style_split_units(cls, text: str) -> list[_ChatStyleUnit]:
        normalized = " ".join(str(text or "").split())
        units: list[_ChatStyleUnit] = []
        current: list[str] = []
        quote_stack: list[str] = []
        index = 0
        while index < len(normalized):
            protected_end = cls._chat_style_protected_span_end(normalized, index)
            if protected_end > index:
                current.append(normalized[index:protected_end])
                index = protected_end
                continue
            char = normalized[index]
            if quote_stack and char == quote_stack[-1]:
                quote_stack.pop()
                current.append(char)
            elif char in cls._CHAT_STYLE_QUOTE_PAIRS:
                quote_stack.append(cls._CHAT_STYLE_QUOTE_PAIRS[char])
                current.append(char)
            elif not quote_stack and (char in cls._CHAT_STYLE_STRONG_BREAKS or char in cls._CHAT_STYLE_SOFT_BREAKS):
                separator = [char]
                while (
                    index + 1 < len(normalized)
                    and normalized[index + 1] in cls._CHAT_STYLE_STRONG_BREAKS | cls._CHAT_STYLE_SOFT_BREAKS
                ):
                    index += 1
                    separator.append(normalized[index])
                body = "".join(current).strip()
                if body:
                    separator_text = "".join(separator)
                    break_kind = (
                        "strong"
                        if any(ch in cls._CHAT_STYLE_STRONG_BREAKS for ch in separator_text)
                        else "soft"
                    )
                    units.append(_ChatStyleUnit(body, separator_text, break_kind))
                current = []
            else:
                current.append(char)
            index += 1
        tail = "".join(current).strip()
        if tail:
            units.append(_ChatStyleUnit(tail, "", "tail"))
        return units

    @staticmethod
    def _chat_style_merge_tail_segments(
        segments: list[_ChatStyleSegmentPlan],
        max_segments: int,
    ) -> list[_ChatStyleSegmentPlan]:
        cleaned = [segment for segment in segments if segment.raw_text.strip()]
        if len(cleaned) <= max_segments:
            return cleaned
        head = cleaned[: max_segments - 1]
        tail_parts = cleaned[max_segments - 1 :]
        tail_raw = "".join(segment.raw_text for segment in tail_parts).strip()
        if not tail_raw:
            return head
        tail_last = tail_parts[-1]
        return [
            *head,
            _ChatStyleSegmentPlan(
                raw_text=tail_raw,
                text=tail_raw,
                separator=tail_last.separator,
                break_kind=tail_last.break_kind,
            ),
        ]

    @classmethod
    def _chat_style_units_from_lines(cls, lines: list[str]) -> list[_ChatStyleUnit]:
        units: list[_ChatStyleUnit] = []
        for line in lines:
            line_units = cls._chat_style_split_units(line) or [_ChatStyleUnit(line, "", "tail")]
            if line_units and line_units[-1].break_kind != "strong":
                last = line_units[-1]
                line_units[-1] = _ChatStyleUnit(last.body, last.separator, "strong")
            units.extend(line_units)
        return units

    @classmethod
    def _chat_style_should_split_short_lead(
        cls,
        current_text: str,
        next_unit_text: str,
        limit: int,
        segment_count: int,
        total_units: int,
    ) -> bool:
        if segment_count != 0 or total_units != 2 or not (0 < limit <= 18):
            return False
        current_compact = cls._chat_style_compact_text(current_text)
        next_compact = cls._chat_style_compact_text(next_unit_text)
        if not (2 <= len(current_compact) <= 4):
            return False
        if len(next_compact) < 4:
            return False
        return "://" not in current_compact and "://" not in next_compact

    @classmethod
    def _chat_style_refine_segment_tail(
        cls,
        raw_text: str,
        separator: str,
        break_kind: str,
        *,
        is_final: bool,
    ) -> str:
        text = str(raw_text or "").strip()
        separator = str(separator or "")
        if not text or is_final or not separator or separator[-1:] not in cls._CHAT_STYLE_SOFT_BREAKS:
            return text
        base = text[: -len(separator)].rstrip() if text.endswith(separator) else text
        if not base:
            return text
        if base[-1:] in cls._CHAT_STYLE_STRONG_BREAKS:
            return base
        compact = cls._chat_style_compact_text(base)
        if separator[-1:] in {"；", ";"}:
            return f"{base}。"
        soft_inside = sum(1 for ch in compact if ch in cls._CHAT_STYLE_SOFT_BREAKS)
        if len(compact) >= 22 or (len(compact) >= 14 and soft_inside >= 2):
            return f"{base}。"
        return base

    @classmethod
    def _chat_style_should_hold_soft_break_for_sentence(
        cls,
        current_text: str,
        next_unit: _ChatStyleUnit,
        limit: int,
        has_more_units: bool,
        *,
        segment_count: int = 0,
        has_later_strong: bool = False,
    ) -> bool:
        current_compact = cls._chat_style_compact_text(current_text)
        next_compact = cls._chat_style_compact_text(next_unit.text)
        if segment_count > 0 and next_unit.break_kind == "soft" and has_later_strong:
            if len(current_compact) >= (15 if 0 < limit <= 30 else 22):
                return False
            max_current = 36 if 0 < limit <= 30 else 48
            return len(current_compact) + len(next_compact) <= max_current
        if not has_more_units or next_unit.break_kind != "strong":
            return False
        min_current = 8 if segment_count > 0 else 14
        if len(current_compact) < min_current:
            return False
        max_combined = 48 if 0 < limit <= 30 else 60
        return len(current_compact) + len(next_compact) <= max_combined

    @classmethod
    def _chat_style_has_natural_three_beat(
        cls,
        units: list[_ChatStyleUnit],
        normalized: str,
        limit: int,
    ) -> bool:
        if not (0 < limit <= 30 and 34 <= len(normalized) <= 64 and len(units) >= 3):
            return False
        if sum(1 for unit in units if unit.break_kind == "strong") < 3:
            return False

        intro_length = 0
        intro_break = ""
        intro_separator = ""
        for unit in units[:3]:
            intro_length += len(cls._chat_style_compact_text(unit.text))
            intro_break = unit.break_kind
            intro_separator = unit.separator
            if unit.break_kind == "strong" or intro_length > 12:
                break
        first_unit_length = len(cls._chat_style_compact_text(units[0].text))
        intro_ok = (
            (units[0].break_kind == "soft" and 5 <= first_unit_length <= 10)
            or (
                intro_break == "strong"
                and 2 <= intro_length <= 12
                and not set(intro_separator or "").issubset({"…"})
            )
        )
        if not intro_ok:
            return False

        tail_length = len(cls._chat_style_compact_text(units[-1].text))
        if units[-1].break_kind != "strong" or not (5 <= tail_length <= 18):
            return False
        return len(normalized) - intro_length - tail_length >= 8

    @classmethod
    def _chat_style_has_compound_reaction_lead(
        cls,
        units: list[_ChatStyleUnit],
        normalized: str,
        limit: int,
    ) -> bool:
        if not (0 < limit <= 30 and 38 <= len(normalized) <= 72 and len(units) >= 5):
            return False
        first_lengths = [len(cls._chat_style_compact_text(unit.text)) for unit in units[:2]]
        if any(unit.break_kind != "strong" for unit in units[:3]):
            return False
        if not (2 <= first_lengths[0] <= 4 and 2 <= first_lengths[1] <= 5 and sum(first_lengths) <= 9):
            return False
        third_length = len(cls._chat_style_compact_text(units[2].text))
        if not (5 <= third_length <= 16):
            return False
        return any(unit.break_kind == "soft" for unit in units[3:])

    @classmethod
    def _chat_style_has_reaction_lead_soft_tail(
        cls,
        units: list[_ChatStyleUnit],
        normalized: str,
        limit: int,
    ) -> bool:
        if not (0 < limit <= 30 and 28 <= len(normalized) <= 64 and len(units) >= 4):
            return False
        if units[0].break_kind != "strong" or units[-1].break_kind != "strong":
            return False
        lead_text = str(units[0].text or "")
        if "…" in lead_text or "..." in lead_text:
            return False
        lead_length = len(cls._chat_style_compact_text(units[0].text))
        if not (2 <= lead_length <= 10):
            return False
        if any("://" in cls._chat_style_compact_text(unit.text) for unit in units):
            return False
        tail_units = units[1:]
        soft_count = sum(1 for unit in tail_units if unit.break_kind == "soft")
        if soft_count < 2:
            return False
        tail_length = len(normalized) - lead_length
        if tail_length < 18:
            return False
        soft_lengths = [
            len(cls._chat_style_compact_text(unit.body))
            for unit in tail_units[:-1]
            if unit.break_kind == "soft"
        ]
        if any(5 <= length <= 14 for length in soft_lengths):
            return True
        return len(soft_lengths) >= 2 and 6 <= sum(soft_lengths) <= 18 and all(
            2 <= length <= 8 for length in soft_lengths
        )

    @classmethod
    def _chat_style_auto_max_segments(
        cls,
        normalized: str,
        limit: int,
        strong_break_count: int,
        soft_break_count: int,
        short_reaction_lead: bool = False,
        natural_three_beat: bool = False,
        compound_reaction_lead: bool = False,
        reaction_lead_soft_tail: bool = False,
        explicit_line_count: int = 0,
    ) -> int:
        text_length = len(normalized)
        allow_three_segments = False
        if short_reaction_lead or natural_three_beat or reaction_lead_soft_tail:
            allow_three_segments = True
        elif explicit_line_count >= 2 and 0 < limit <= 30 and text_length >= 28 and strong_break_count >= 3:
            allow_three_segments = True
        elif text_length >= 60 and strong_break_count >= 3:
            allow_three_segments = True
        elif 0 < limit <= 30 and text_length >= 44:
            allow_three_segments = (
                strong_break_count >= 3
                or (strong_break_count >= 2 and soft_break_count >= 2)
                or soft_break_count >= 4
            )
        elif text_length >= 72 and strong_break_count >= 2 and soft_break_count >= 3:
            allow_three_segments = True

        auto_max_segments = 3 if allow_three_segments else 2
        if compound_reaction_lead:
            auto_max_segments = max(auto_max_segments, 4)
        soft_rich_long_text = (
            0 < limit <= 30
            and text_length >= max(84, limit * 3)
            and strong_break_count >= 2
            and soft_break_count >= 5
        )
        if text_length >= 110 and (
            strong_break_count >= 5
            or (strong_break_count >= 4 and soft_break_count >= 6)
            or soft_break_count >= 8
        ):
            auto_max_segments = 5
        elif soft_rich_long_text or (text_length >= 96 and (strong_break_count >= 4 or soft_break_count >= 5)):
            auto_max_segments = 4
        return auto_max_segments

    @classmethod
    def _chat_style_has_short_reaction_lead(cls, units: list[_ChatStyleUnit], normalized: str, limit: int) -> bool:
        if not (0 < limit <= 30) or len(units) != 3:
            return False
        if not (18 <= len(normalized) <= 60):
            return False
        if any(unit.break_kind != "strong" for unit in units):
            return False
        lengths = [len(cls._chat_style_compact_text(unit.body)) for unit in units]
        return 2 <= lengths[0] <= 8 and lengths[1] >= 5 and lengths[2] >= 5

    @classmethod
    def _chat_style_should_hold_short_reaction_pair(
        cls,
        current_text: str,
        next_unit: _ChatStyleUnit,
        segment_count: int,
    ) -> bool:
        if segment_count != 0 or next_unit.break_kind != "strong":
            return False
        current_compact = cls._chat_style_compact_text(current_text)
        next_compact = cls._chat_style_compact_text(next_unit.text)
        if "://" in current_compact or "://" in next_compact:
            return False
        return 2 <= len(current_compact) <= 4 and 2 <= len(next_compact) <= 5 and len(current_compact) + len(next_compact) <= 9

    @classmethod
    def _chat_style_should_split_short_bridge(
        cls,
        current_text: str,
        next_unit_text: str,
        segment_count: int,
    ) -> bool:
        if segment_count < 2:
            return False
        current_compact = cls._chat_style_compact_text(current_text)
        next_compact = cls._chat_style_compact_text(next_unit_text)
        if "://" in current_compact or "://" in next_compact:
            return False
        return 5 <= len(current_compact) <= 8 and len(next_compact) >= 10

    @classmethod
    def _chat_style_single_segment_plan(cls, text: str) -> list[_ChatStyleSegmentPlan]:
        text = str(text or "").strip()
        if not text:
            return []
        return [_ChatStyleSegmentPlan(raw_text=text, text=text, separator="", break_kind="tail")]

    @classmethod
    def _chat_style_segment_source(cls, raw: str) -> _ChatStyleSegmentSource:
        raw_lines = [" ".join(line.split()) for line in raw.splitlines() if line.strip()]
        if len(raw_lines) > 1:
            normalized = " ".join(raw_lines)
            units = cls._chat_style_units_from_lines(raw_lines)
        else:
            normalized = " ".join(raw.split())
            units = cls._chat_style_split_units(normalized)
        return _ChatStyleSegmentSource(normalized=normalized, units=units, explicit_line_count=len(raw_lines))

    @classmethod
    def _chat_style_segment_controls(
        cls,
        source: _ChatStyleSegmentSource,
        limit: int,
        max_segments_cap: int,
    ) -> tuple[bool, int, int, int]:
        strong_break_count = sum(1 for unit in source.units if unit.break_kind == "strong")
        soft_break_count = sum(1 for unit in source.units if unit.break_kind == "soft")
        allow_soft_split = (
            strong_break_count == 0
            or len(source.normalized) >= 48
            or (0 < limit <= 30 and soft_break_count >= 2)
        )
        auto_max_segments = cls._chat_style_auto_max_segments(
            source.normalized,
            limit,
            strong_break_count,
            soft_break_count,
            cls._chat_style_has_short_reaction_lead(source.units, source.normalized, limit),
            cls._chat_style_has_natural_three_beat(source.units, source.normalized, limit),
            cls._chat_style_has_compound_reaction_lead(source.units, source.normalized, limit),
            cls._chat_style_has_reaction_lead_soft_tail(source.units, source.normalized, limit),
            source.explicit_line_count,
        )
        try:
            max_segments_cap = int(max_segments_cap)
        except (TypeError, ValueError):
            max_segments_cap = 5
        max_segments = max(2, min(max(2, max_segments_cap), auto_max_segments))
        target = max(
            12,
            min(
                30,
                limit if 0 < limit <= 30 else (len(source.normalized) + max_segments - 1) // max_segments,
            ),
        )
        min_current = 8 if 0 < limit <= 18 else 10
        return allow_soft_split, max_segments, target, min_current

    @classmethod
    def _chat_style_collect_segment_plans(
        cls,
        units: list[_ChatStyleUnit],
        *,
        limit: int,
        max_segments: int,
        target: int,
        min_current: int,
        allow_soft_split: bool,
    ) -> list[_ChatStyleSegmentPlan]:
        segments: list[_ChatStyleSegmentPlan] = []
        current = ""
        current_break = "tail"
        current_separator = ""
        for unit_index, unit in enumerate(units):
            unit_text = unit.text
            compact_current = cls._chat_style_compact_text(current)
            should_split = bool(current) and len(segments) + 1 < max_segments and (
                (
                    current_break == "strong"
                    and len(compact_current) >= 2
                    and not cls._chat_style_should_hold_short_reaction_pair(current, unit, len(segments))
                )
                or (
                    allow_soft_split
                    and current_break == "soft"
                    and (
                        (
                            len(compact_current) >= min_current
                            and len(current) + len(unit_text) > target
                            and not cls._chat_style_should_hold_soft_break_for_sentence(
                                current,
                                unit,
                                limit,
                                unit_index + 1 < len(units),
                                segment_count=len(segments),
                                has_later_strong=any(
                                    candidate.break_kind == "strong" for candidate in units[unit_index:]
                                ),
                            )
                        )
                        or cls._chat_style_should_split_short_lead(
                            current,
                            unit_text,
                            limit,
                            len(segments),
                            len(units),
                        )
                        or cls._chat_style_should_split_short_bridge(
                            current,
                            unit_text,
                            len(segments),
                        )
                    )
                )
            )
            if should_split:
                segment_raw = current.strip()
                if segment_raw:
                    segments.append(
                        _ChatStyleSegmentPlan(
                            raw_text=segment_raw,
                            text=segment_raw,
                            separator=current_separator,
                            break_kind=current_break,
                        )
                    )
                current = unit_text
            else:
                current = f"{current}{unit_text}"
            current_break = unit.break_kind
            current_separator = unit.separator
        segment_raw = current.strip()
        if segment_raw:
            segments.append(
                _ChatStyleSegmentPlan(
                    raw_text=segment_raw,
                    text=segment_raw,
                    separator=current_separator,
                    break_kind=current_break,
                )
            )
        return segments

    @classmethod
    def _chat_style_refine_segment_plans(
        cls,
        segments: list[_ChatStyleSegmentPlan],
        *,
        normalized: str,
        max_segments: int,
    ) -> list[_ChatStyleSegmentPlan]:
        segments = cls._chat_style_merge_tail_segments(segments, max_segments)
        refined: list[_ChatStyleSegmentPlan] = []
        for index, segment in enumerate(segments):
            display_text = cls._chat_style_refine_segment_tail(
                segment.raw_text,
                segment.separator,
                segment.break_kind,
                is_final=index == len(segments) - 1,
            )
            if display_text:
                refined.append(
                    _ChatStyleSegmentPlan(
                        raw_text=segment.raw_text.strip(),
                        text=display_text.strip(),
                        separator=segment.separator,
                        break_kind=segment.break_kind,
                    )
                )
        if len(refined) < 2 or any(len(cls._chat_style_compact_text(segment.text)) < 2 for segment in refined):
            return cls._chat_style_single_segment_plan(normalized)
        return refined

    @classmethod
    def _plan_chat_style_natural_segments(
        cls,
        text: str,
        limit: int = 0,
        max_segments_cap: int = 5,
    ) -> list[_ChatStyleSegmentPlan]:
        raw = str(text or "").strip()
        if not raw:
            return []
        if cls._chat_style_text_is_structural(raw):
            return cls._chat_style_single_segment_plan(raw)
        source = cls._chat_style_segment_source(raw)
        normalized = source.normalized
        min_length = 8 if 0 < limit <= 18 else (12 if 0 < limit <= 30 else 18)
        if not (min_length <= len(normalized) <= 240):
            return cls._chat_style_single_segment_plan(normalized)
        if len(source.units) < 2:
            return cls._chat_style_single_segment_plan(normalized)

        allow_soft_split, max_segments, target, min_current = cls._chat_style_segment_controls(
            source,
            limit,
            max_segments_cap,
        )
        segments = cls._chat_style_collect_segment_plans(
            source.units,
            limit=limit,
            max_segments=max_segments,
            target=target,
            min_current=min_current,
            allow_soft_split=allow_soft_split,
        )
        return cls._chat_style_refine_segment_plans(
            segments,
            normalized=normalized,
            max_segments=max_segments,
        )

    @classmethod
    def _chat_style_segment_reason(cls, segment: _ChatStyleSegmentPlan, *, index: int, total: int) -> str:
        if total <= 1:
            return "单段发送"
        if index == total - 1:
            return "收尾"
        if segment.break_kind == "strong":
            return "完整句停顿"
        if segment.break_kind == "soft":
            compact = cls._chat_style_compact_text(segment.text)
            return "短气口停顿" if len(compact) <= 8 else "语义软停顿"
        return "自然断点"

    @classmethod
    def _chat_style_pending_segments(
        cls,
        segments: list[_ChatStyleSegmentPlan],
    ) -> list[_ChatStylePendingSegment]:
        total = len([segment for segment in segments if str(segment.text or "").strip()])
        pending: list[_ChatStylePendingSegment] = []
        for index, segment in enumerate(segments):
            text = str(segment.text or "").strip()
            if not text:
                continue
            pending.append(
                _ChatStylePendingSegment(
                    text=text,
                    compact_length=len(cls._chat_style_compact_text(text)),
                    break_kind=segment.break_kind,
                    reason=cls._chat_style_segment_reason(segment, index=index, total=total),
                )
            )
        return pending

    @staticmethod
    def _chat_style_pending_trace(segments: list[_ChatStylePendingSegment]) -> str:
        parts = [
            f"{index + 1}:{segment.compact_length}字/{segment.reason}"
            for index, segment in enumerate(segments)
        ]
        return "；".join(parts)

    @classmethod
    def _split_chat_style_natural_segments(
        cls,
        text: str,
        limit: int = 0,
        max_segments_cap: int = 5,
    ) -> list[str]:
        return [
            segment.text
            for segment in cls._plan_chat_style_natural_segments(text, limit, max_segments_cap=max_segments_cap)
            if segment.text
        ]

    def _replace_text_result_with_segments(self, event: Any, segments: list[_ChatStyleSegmentPlan]) -> bool:
        if event is not None:
            setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, [])
        pending = self._chat_style_pending_segments(segments)
        if len(pending) < 2:
            return False
        cleaned = [segment.text for segment in pending]
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or not chain:
            return False
        original_parts = [self._chat_style_text_component_text(item).strip() for item in chain]
        if not all(original_parts):
            return False
        if cleaned == original_parts:
            return False
        try:
            item = chain[0]
            chain[:] = [self._chat_style_copy_text_component(item, segment) for segment in cleaned]
            setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, pending)
            logger.debug(
                f"{LOG_PREFIX} 自然短句发送计划：{len(pending)} 段；"
                f"{self._chat_style_pending_trace(pending)}"
            )
            return True
        except Exception:
            setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, [])
            return False

    def _chat_style_segment_chain(self, event: Any, result: Any, item: Any) -> Any:
        derive = getattr(result, "derive", None)
        if callable(derive):
            return derive([item])
        chain_result = getattr(event, "chain_result", None)
        if callable(chain_result):
            return chain_result([item])
        return [item]

    def _note_chat_style_segmented_send(self, event: Any) -> None:
        for method_name in (
            "note_structured_sent_result",
            "note_media_source_event",
            "note_proactive_bot_reply",
            "note_voice_switch_text_result",
        ):
            method = getattr(self, method_name, None)
            if callable(method):
                method(event)

    async def send_chat_style_segments_if_needed(self, event: Any) -> bool:
        if event is None:
            return False
        pending = [
            segment
            for segment in list(getattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, []) or [])
            if isinstance(segment, _ChatStylePendingSegment) and segment.text
        ]
        if len(pending) < 2:
            return False
        setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, [])
        segments = [segment.text for segment in pending]
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list) or len(chain) != len(segments):
            logger.debug(
                f"{LOG_PREFIX} 自然短句分段发送跳过：结果链已变化"
                f"（segments={len(segments)}，chain={len(chain) if isinstance(chain, list) else '不可用'}）。"
            )
            return False
        texts = [self._chat_style_text_component_text(item).strip() for item in chain]
        if texts != segments:
            logger.debug(f"{LOG_PREFIX} 自然短句分段发送跳过：结果文本已被其他阶段改写。")
            return False
        send = getattr(event, "send", None)
        if not callable(send):
            logger.debug(f"{LOG_PREFIX} 自然短句分段发送跳过：当前事件没有可用发送方法。")
            return False
        try:
            for index, item in enumerate(chain):
                if index > 0:
                    previous = pending[index - 1]
                    current = pending[index]
                    delay_seconds = self._chat_style_natural_segment_delay_seconds(
                        previous.text,
                        current.text,
                        previous_break_kind=previous.break_kind,
                    )
                else:
                    delay_seconds = 0.0
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                message = self._chat_style_segment_chain(event, result, item)
                decorator = getattr(self, "decorate_group_addressing_chain", None)
                if callable(decorator):
                    decorator(
                        message,
                        target_scope=self._event_session_id(event),
                        source_event=event,
                        segment_index=index,
                        source="chat",
                    )
                await send(message)
            self._note_chat_style_segmented_send(event)
            clearer = getattr(event, "clear_result", None)
            if callable(clearer):
                clearer()
            logger.info(
                f"{LOG_PREFIX} 自然短句分段发送：{len(segments)} 段；"
                f"{self._chat_style_pending_trace(pending)}"
            )
            return True
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 自然短句分段发送失败，默认发送：{exc}", exc_info=True)
            return False

    def apply_chat_style_before_send(self, event: Any) -> bool:
        if event is not None:
            setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, [])
        style = getattr(getattr(self, "config", None), "chat_style", None)
        if not style:
            return False
        text_getter = getattr(self, "_voice_switch_reply_text_from_event", None)
        reply_text = text_getter(event) if callable(text_getter) else ""
        if not reply_text:
            return False
        if self._chat_style_should_keep_default_send(event, reply_text):
            decision = getattr(event, "_daily_life_chat_style_decision", None)
            if not isinstance(decision, dict):
                decision = self._classify_chat_message(event, getattr(event, "message_str", ""))
                setattr(event, "_daily_life_chat_style_decision", decision)
            self.log_chat_style_trace(event, reply_text, decision, changed=False)
            return False
        decision = getattr(event, "_daily_life_chat_style_decision", None)
        if not isinstance(decision, dict):
            decision = self._classify_chat_message(event, getattr(event, "message_str", ""))
            setattr(event, "_daily_life_chat_style_decision", decision)
        elif decision.get("kind") == "unknown" and str(getattr(event, "message_str", "") or "").strip():
            next_decision = self._classify_chat_message(event, getattr(event, "message_str", ""))
            if next_decision.get("kind") != "unknown":
                decision = next_decision
                setattr(event, "_daily_life_chat_style_decision", decision)
        changed = False
        kind = str(decision.get("kind") or "unknown")
        if kind in {"casual", "unknown"}:
            plans = self._plan_chat_style_natural_segments(
                reply_text,
                self._chat_style_limit_for_event(event),
            )
            if self._replace_text_result_with_segments(event, plans):
                changed = True
                reply_text = "\n".join(segment.text for segment in plans)
        self.log_chat_style_trace(event, reply_text, decision, changed=changed)
        return changed

    def log_chat_style_trace(
        self,
        event: Any,
        reply_text: str,
        decision: dict[str, Any] | None = None,
        *,
        changed: bool = False,
    ) -> None:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        if not style:
            return
        decision = decision if isinstance(decision, dict) else {}
        scope = decision.get("scope") or ("group" if event is not None and self._event_is_group_message(event) else "private")
        logger.info(
            f"{LOG_PREFIX} 表达节奏：类型={decision.get('kind') or 'unknown'}；"
            f"场景={'群聊' if scope == 'group' else '私聊'}；"
            f"自然分段={'是' if changed else '否'}；"
            f"长度={len(str(reply_text or ''))}"
        )
