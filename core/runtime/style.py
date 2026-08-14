from __future__ import annotations

import asyncio
import copy
import random
import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

from .delay import ChatDelayMixin
from .delivery import EventDeliveryRequest, ReplyDeliveryService
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


class ChatStyleRuntimeMixin(ChatDelayMixin):
    _CHAT_STYLE_PENDING_SEGMENTS_ATTR = "_daily_life_chat_style_pending_segments"
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
    _CHAT_STYLE_PROTECTED_INLINE_MARKDOWN = re.compile(
        r"(?P<code>`+)[^\r\n]*?(?P=code)|!?\[[^\]\r\n]*\]\([^\r\n)]*\)"
    )
    _CHAT_STYLE_INLINE_MARKDOWN_PATTERNS = (
        re.compile(r"(?<!\*)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)"),
        re.compile(r"(?<!_)__(?=\S)(.+?)(?<=\S)__(?!_)"),
        re.compile(r"(?<!~)~~(?=\S)(.+?)(?<=\S)~~(?!~)"),
        re.compile(r"(?<!\*)\*(?=\S)([^*\r\n]+?)(?<=\S)\*(?!\*)"),
        re.compile(r"(?<![\w_])_(?=\S)([^_\r\n]+?)(?<=\S)_(?![\w_])"),
    )

    def _chat_style_enabled(self) -> bool:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        return bool(style and getattr(style, "enabled", False))

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
        decision_attr = "_daily_life_t2i_default_send"
        if event is not None and getattr(event, decision_attr, False):
            if not getattr(event, "_daily_life_t2i_source_text", ""):
                setattr(event, "_daily_life_t2i_source_text", str(text or ""))
            return True
        config = self._chat_style_astrbot_send_config(event)
        compact_length = len(str(text or ""))
        if not config or compact_length <= 0:
            return False
        if bool(config.get("t2i")):
            threshold = self._chat_style_int_config(
                config.get("t2i_word_threshold"), 150, minimum=50
            )
            if compact_length > threshold:
                if event is not None:
                    setattr(event, decision_attr, True)
                    setattr(event, "_daily_life_t2i_source_text", str(text or ""))
                logger.debug(
                    f"{LOG_PREFIX} 回复长度 {compact_length} 超过文本转图像阈值 {threshold}。"
                )
                return True
        return False

    def _chat_style_context(self, event: Any) -> dict[str, str]:
        is_group = self._event_is_group_message(event) if event is not None else False
        return {"scope": "group" if is_group else "private"}

    def _chat_style_limit_for_event(self, event: Any) -> int:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        if not self._chat_style_enabled() or not style:
            return 0
        casual_limit = int(getattr(style, "casual_max_chars", 50) or 50)
        if event is not None and self._event_is_group_message(event):
            channel_limit = int(getattr(style, "group_casual_max_chars", 30) or 30)
        else:
            channel_limit = int(getattr(style, "private_casual_max_chars", 15) or 15)
        return min(casual_limit, channel_limit) if casual_limit > 0 else channel_limit

    def _build_chat_style_decision_hint(
        self, event: Any, context: dict[str, str]
    ) -> str:
        if not self._chat_style_enabled():
            return ""
        limit = self._chat_style_limit_for_event(event)
        scope_label = "群聊" if context.get("scope") == "group" else "私聊"
        focus = "自然接话；轻闲聊用短气口，一句只放一个主要意思，有内容就完整说清楚。"
        lines = [
            "\n[HiddenChatDecision]",
            f"- 当前回应重心：{focus}",
            f"- 消息传输范围：{scope_label}；只用于表达长度和发送节奏，不代表现实距离。",
            "- 普通聊天使用纯文本，不使用 Markdown 加粗、斜体、删除线或标题格式。",
        ]
        if limit > 0:
            lines.append(
                f"- {scope_label}轻闲聊参考长度约 {limit} 字左右；有正事时按内容自然展开。"
            )
        return "\n".join(lines)

    async def build_chat_style_injection_context(
        self, event: Any, message: str = ""
    ) -> str:
        if not self._chat_style_enabled():
            return ""
        if self._semantic_segment_enabled():
            limit = self._chat_style_limit_for_event(event)
            scope_label = (
                "群聊"
                if event is not None and self._event_is_group_message(event)
                else "私聊"
            )
            length_hint = (
                f"当前{scope_label}闲聊的单个分段参考长度约为 {limit} 字；这是表达倾向，不是硬性截断。"
                if limit > 0
                else "当前没有设置单个分段的参考长度；按自然表达决定。"
            )
            return (
                "\n[HiddenChatStyle]\n"
                "先判断这轮回复要完成的表达动作和信息深度，再用角色自己的口吻回答。"
                "普通闲聊优先使用短而完整的自然句，一个连续表达只承载一个主要意思；"
                "需要转换表达重心或追加独立意思时自然另起一条。"
                "有必要信息时完整说清，不机械压缩，也不为了凑分段拆碎一个意思。"
                "普通聊天使用纯文本，不使用 Markdown 加粗、斜体、删除线或标题格式。"
                + length_hint
            )
        context = self._chat_style_context(event)
        if event is not None:
            setattr(event, "_daily_life_chat_style_context", context)
        return self._build_chat_style_decision_hint(event, context)

    @staticmethod
    def _chat_style_text_is_structural(text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False
        if "```" in normalized:
            return True
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        return any(
            re.match(r"^(?:#{1,6}\s+|>\s+|[-+*]\s+|\d+[.)、]\s+)", line)
            for line in lines
        )

    @classmethod
    def _chat_style_clean_inline_markdown(cls, text: str) -> str:
        source = str(text or "")

        def clean_plain(value: str) -> str:
            cleaned = value
            for pattern in cls._CHAT_STYLE_INLINE_MARKDOWN_PATTERNS:
                cleaned = pattern.sub(lambda match: match.group(1), cleaned)
            return cleaned

        parts: list[str] = []
        position = 0
        for match in cls._CHAT_STYLE_PROTECTED_INLINE_MARKDOWN.finditer(source):
            parts.append(clean_plain(source[position : match.start()]))
            parts.append(match.group(0))
            position = match.end()
        parts.append(clean_plain(source[position:]))
        return "".join(parts)

    @staticmethod
    def _chat_style_plain_fence_marker(line: str) -> str:
        marker = str(line or "").strip()
        if len(marker) < 3 or marker[0] not in {"`", "~"}:
            return ""
        return marker if all(char == marker[0] for char in marker) else ""

    @staticmethod
    def _chat_style_fenced_block_is_prose(text: str) -> bool:
        value = str(text or "").strip()
        if len(value) < 40:
            return False
        lines = [line for line in value.splitlines() if line.strip()]
        if not lines:
            return False
        compact = "".join(char for char in value if not char.isspace())
        if not compact:
            return False
        readable = sum(char.isalnum() for char in compact)
        code_symbols = sum(char in "{}[]();=<>\\|$" for char in compact)
        sentence_marks = sum(char in "，。！？；：、,.!?;:" for char in compact)
        indented_lines = sum(line.startswith((" ", "\t")) for line in lines)
        return (
            readable / len(compact) >= 0.55
            and code_symbols / len(compact) <= 0.06
            and sentence_marks >= 2
            and indented_lines <= 1
        )

    @classmethod
    def _chat_style_unwrap_t2i_prose_fences(cls, text: str) -> str:
        lines = str(text or "").splitlines(keepends=True)
        if not lines:
            return str(text or "")
        output: list[str] = []
        index = 0
        while index < len(lines):
            marker = cls._chat_style_plain_fence_marker(lines[index])
            if not marker:
                output.append(lines[index])
                index += 1
                continue
            closing = index + 1
            while closing < len(lines):
                candidate = cls._chat_style_plain_fence_marker(lines[closing])
                if (
                    candidate
                    and candidate[0] == marker[0]
                    and len(candidate) >= len(marker)
                ):
                    break
                closing += 1
            if closing >= len(lines):
                output.append(lines[index])
                index += 1
                continue
            body = "".join(lines[index + 1 : closing])
            if cls._chat_style_fenced_block_is_prose(body):
                output.extend(lines[index + 1 : closing])
            else:
                output.extend(lines[index : closing + 1])
            index = closing + 1
        return "".join(output)

    def apply_chat_plain_text_cleanup_before_send(self, event: Any) -> bool:
        if not self._chat_style_enabled() or not self._chat_style_result_is_text_only(
            event
        ):
            return False
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None)
        source_text = "".join(
            self._chat_style_text_component_text(item) for item in chain or []
        )
        if not source_text:
            return False
        if self._chat_style_should_keep_default_send(event, source_text):
            cleaned = self._chat_style_unwrap_t2i_prose_fences(source_text)
            if cleaned == source_text:
                return False
            setattr(event, "_daily_life_t2i_source_text", cleaned)
            return self._replace_text_result(event, cleaned)
        if self._chat_style_text_is_structural(source_text):
            return False
        cleaned = self._chat_style_clean_inline_markdown(source_text)
        if cleaned == source_text:
            return False
        return self._replace_text_result(event, cleaned)

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
            chain[:] = [
                self._chat_style_copy_text_component(chain[text_indexes[0]], text)
            ]
            return True
        for index, item in enumerate(chain):
            if isinstance(item, str):
                chain[index] = text
                return True
            if isinstance(item, dict):
                kind = (
                    str(item.get("type") or item.get("kind") or "text").strip().lower()
                )
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
        return (
            isinstance(chain, list)
            and bool(chain)
            and all(cls._chat_style_text_component_text(item).strip() for item in chain)
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
            while (
                end < len(text) and text[end] not in cls._CHAT_STYLE_URL_TRAILING_BREAKS
            ):
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
            elif not quote_stack and (
                char in cls._CHAT_STYLE_STRONG_BREAKS
                or char in cls._CHAT_STYLE_SOFT_BREAKS
            ):
                separator = [char]
                while (
                    index + 1 < len(normalized)
                    and normalized[index + 1]
                    in cls._CHAT_STYLE_STRONG_BREAKS | cls._CHAT_STYLE_SOFT_BREAKS
                ):
                    index += 1
                    separator.append(normalized[index])
                body = "".join(current).strip()
                if body:
                    separator_text = "".join(separator)
                    break_kind = (
                        "strong"
                        if any(
                            ch in cls._CHAT_STYLE_STRONG_BREAKS for ch in separator_text
                        )
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
        tail_raw = tail_parts[0].raw_text.strip()
        previous = tail_parts[0]
        for segment in tail_parts[1:]:
            if previous.break_kind == "strong" and not previous.separator:
                tail_raw += " "
            tail_raw += segment.raw_text.strip()
            previous = segment
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
            line_units = cls._chat_style_split_units(line) or [
                _ChatStyleUnit(line, "", "tail")
            ]
            if line_units and line_units[-1].break_kind != "strong":
                last = line_units[-1]
                line_units[-1] = _ChatStyleUnit(last.body, last.separator, "strong")
            units.extend(line_units)
        return units

    @classmethod
    def _chat_style_single_segment_plan(cls, text: str) -> list[_ChatStyleSegmentPlan]:
        text = str(text or "").strip()
        if not text:
            return []
        return [
            _ChatStyleSegmentPlan(
                raw_text=text, text=text, separator="", break_kind="tail"
            )
        ]

    @classmethod
    def _chat_style_segment_source(cls, raw: str) -> _ChatStyleSegmentSource:
        raw_lines = [
            " ".join(line.split()) for line in raw.splitlines() if line.strip()
        ]
        if len(raw_lines) > 1:
            normalized = " ".join(raw_lines)
            units = cls._chat_style_units_from_lines(raw_lines)
        else:
            normalized = " ".join(raw.split())
            units = cls._chat_style_split_units(normalized)
        return _ChatStyleSegmentSource(
            normalized=normalized, units=units, explicit_line_count=len(raw_lines)
        )

    @classmethod
    def _chat_style_segment_controls(
        cls,
        source: _ChatStyleSegmentSource,
        limit: int,
        max_segments_cap: int,
    ) -> tuple[bool, int, int, int]:
        try:
            max_segments_cap = int(max_segments_cap)
        except (TypeError, ValueError):
            max_segments_cap = 5
        cap = max(2, min(10, max_segments_cap))
        target = max(12, min(60, limit if limit > 0 else 24))
        text_length = len(cls._chat_style_compact_text(source.normalized))
        leading_strong_length = (
            len(cls._chat_style_compact_text(source.units[0].text))
            if source.units and source.units[0].break_kind == "strong"
            else 0
        )
        short_strong_lead = 4 <= leading_strong_length <= max(4, min(8, target // 2))
        length_segments = max(2, (text_length + target - 1) // target)
        explicit_segments = max(1, source.explicit_line_count)
        max_segments = min(
            cap, len(source.units), max(length_segments, explicit_segments)
        )
        max_segments = max(2, max_segments)
        strong_break_count = sum(
            1 for unit in source.units[:-1] if unit.break_kind == "strong"
        )
        boundary_segments = strong_break_count + 1
        break_aware_segments = 0
        group_length = 0
        group_soft_breaks = 0
        for unit_index, unit in enumerate(source.units):
            group_length += len(cls._chat_style_compact_text(unit.text))
            if unit.break_kind == "soft" and unit_index < len(source.units) - 1:
                group_soft_breaks += 1
            if unit.break_kind == "strong" or unit_index == len(source.units) - 1:
                desired = max(1, (group_length + target - 1) // target)
                break_aware_segments += min(1 + group_soft_breaks, desired)
                group_length = 0
                group_soft_breaks = 0
        max_segments = min(
            cap,
            len(source.units),
            max(
                length_segments,
                explicit_segments,
                boundary_segments,
                break_aware_segments,
            ),
        )
        max_segments = max(2, max_segments)
        if short_strong_lead and text_length - leading_strong_length > target:
            max_segments = min(cap, len(source.units), max_segments + 1)
        allow_soft_split = (
            strong_break_count == 0 or text_length > target * 2 or short_strong_lead
        )
        balanced_target = max(8, (text_length + max_segments - 1) // max_segments)
        min_current = (
            2
            if source.explicit_line_count > 1
            else max(4, min(10, balanced_target // 2))
        )
        target = max(min_current * 2, balanced_target)
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
            remaining_text = "".join(item.text for item in units[unit_index:])
            remaining_length = len(cls._chat_style_compact_text(remaining_text))
            room_for_another = remaining_length >= min_current
            remaining_strong_breaks = sum(
                1 for item in units[unit_index:-1] if item.break_kind == "strong"
            )
            soft_split_preserves_strong_breaks = (
                len(segments) + remaining_strong_breaks + 2 <= max_segments
            )
            should_split = (
                bool(current)
                and len(segments) + 1 < max_segments
                and room_for_another
                and (
                    (
                        current_break == "strong"
                        and len(compact_current) >= min(min_current, 4)
                    )
                    or (
                        allow_soft_split
                        and current_break == "soft"
                        and soft_split_preserves_strong_breaks
                        and len(compact_current) >= min_current
                        and len(compact_current)
                        + len(cls._chat_style_compact_text(unit_text))
                        > target
                        and abs(target - len(compact_current))
                        <= abs(
                            target
                            - len(compact_current)
                            - len(cls._chat_style_compact_text(unit_text))
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
                if current and current_break == "strong" and not current_separator:
                    current = f"{current} {unit_text}"
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
        for segment in segments:
            display_text = str(segment.raw_text or "").strip()
            if display_text:
                refined.append(
                    _ChatStyleSegmentPlan(
                        raw_text=segment.raw_text.strip(),
                        text=display_text.strip(),
                        separator=segment.separator,
                        break_kind=segment.break_kind,
                    )
                )
        if len(refined) < 2 or any(
            len(cls._chat_style_compact_text(segment.text)) < 2 for segment in refined
        ):
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

        allow_soft_split, max_segments, target, min_current = (
            cls._chat_style_segment_controls(
                source,
                limit,
                max_segments_cap,
            )
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
    def _chat_style_segment_reason(
        cls, segment: _ChatStyleSegmentPlan, *, index: int, total: int
    ) -> str:
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
        total = len(
            [segment for segment in segments if str(segment.text or "").strip()]
        )
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
                    reason=cls._chat_style_segment_reason(
                        segment, index=index, total=total
                    ),
                )
            )
        return pending

    @staticmethod
    def _chat_style_pending_trace(segments: list[_ChatStylePendingSegment]) -> str:
        parts = [
            f"{index + 1}={segment.compact_length}字"
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
            for segment in cls._plan_chat_style_natural_segments(
                text, limit, max_segments_cap=max_segments_cap
            )
            if segment.text
        ]

    def _replace_text_result_with_segments(
        self, event: Any, segments: list[_ChatStyleSegmentPlan]
    ) -> bool:
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
        original_parts = [
            self._chat_style_text_component_text(item).strip() for item in chain
        ]
        if not all(original_parts):
            return False
        if cleaned == original_parts:
            return False
        try:
            item = chain[0]
            chain[:] = [
                self._chat_style_copy_text_component(item, segment)
                for segment in cleaned
            ]
            setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, pending)
            return True
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 自然分段结果替换失败：{type(exc).__name__}",
                exc_info=True,
            )
            setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, [])
            return False

    def _chat_style_clean_segment_plans(
        self,
        event: Any,
        segments: list[_ChatStyleSegmentPlan],
        source_text: str,
    ) -> list[_ChatStyleSegmentPlan]:
        if not segments:
            return segments
        try:
            from .reply import SegmentPart, SemanticSegmentPlan

            style = getattr(getattr(self, "config", None), "chat_style", None)
            cleanup_enabled = bool(getattr(style, "punctuation_cleanup_enabled", True))
            configured_cleanup_applies = self._chat_style_punctuation_cleaning_active(
                event, source_text
            )
            source_segments = [
                _ChatStyleSegmentPlan(
                    raw_text=segment.raw_text,
                    text=segment.raw_text
                    if (not cleanup_enabled or configured_cleanup_applies)
                    else segment.text,
                    separator=segment.separator,
                    break_kind=segment.break_kind,
                )
                for segment in segments
            ]
            raw_plan = SemanticSegmentPlan(
                tuple(SegmentPart(segment.text) for segment in source_segments),
                source="natural",
                valid=True,
            )
            clean_plan = self._semantic_segment_clean_plan_punctuation(
                event, raw_plan, source_text
            )
            return [
                _ChatStyleSegmentPlan(
                    raw_text=segment.raw_text,
                    text=planned_segment.text,
                    separator=segment.separator,
                    break_kind=segment.break_kind,
                )
                for segment, planned_segment in zip(
                    source_segments, clean_plan.segments
                )
            ]
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 自然分段标点清洗失败：{type(exc).__name__}",
                exc_info=True,
            )
            return segments

    def _chat_style_punctuation_cleaning_active(
        self, event: Any, source_text: str
    ) -> bool:
        if not self._chat_style_enabled():
            return False
        if not self._chat_style_result_is_text_only(event):
            return False
        config = self._chat_style_astrbot_send_config(event)
        if not isinstance(config, dict) or "t2i_word_threshold" not in config:
            return False
        threshold = self._chat_style_int_config(
            config.get("t2i_word_threshold"), 150, minimum=50
        )
        if len(str(source_text or "")) > threshold:
            return False
        return not self._chat_style_text_is_structural(source_text)

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
        if not self._chat_style_enabled():
            if event is not None:
                setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, [])
            return False
        if (
            getattr(event, "_daily_life_semantic_segment_plan", None) is not None
            and self._semantic_segment_enabled()
        ):
            return False
        if event is None:
            return False
        pending = [
            segment
            for segment in list(
                getattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, []) or []
            )
            if isinstance(segment, _ChatStylePendingSegment) and segment.text
        ]
        if len(pending) < 2:
            return False
        setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, [])
        segments = [segment.text for segment in pending]
        result = getattr(event, "get_result", lambda: None)()
        scope = self._event_session_id(event)
        revision = self._semantic_segment_revision(scope)
        epoch = self._semantic_segment_epoch(scope)
        service = getattr(self, "reply_delivery", None) or ReplyDeliveryService(self)
        outcome = await service.send_event(
            EventDeliveryRequest(
                event=event,
                texts=tuple(segments),
                scope=scope,
                match="exact",
                text_from_item=lambda item: self._chat_style_text_component_text(
                    item
                ).strip(),
                build_message=lambda index, chain: self._chat_style_segment_chain(
                    event, result, chain[index]
                ),
                delay_seconds=lambda index: (
                    self._chat_style_natural_segment_delay_seconds(
                        pending[index - 1].text,
                        pending[index].text,
                        previous_break_kind=pending[index - 1].break_kind,
                    )
                ),
                sleep=asyncio.sleep,
                is_current=lambda: (
                    self._semantic_segment_revision(scope) == revision
                    and self._semantic_segment_epoch(scope) == epoch
                ),
            )
        )
        if outcome.status == "skipped":
            logger.debug(
                f"{LOG_PREFIX} 自然分段发送跳过：结果链已变化或被其他阶段改写。"
            )
            return False
        if outcome.status == "cancelled":
            marker = getattr(self, "mark_structured_pending_bot_text", None)
            if callable(marker) and outcome.sent_count > 0:
                marker(event, "\n".join(segments[: outcome.sent_count]))
            capture = getattr(self, "capture_chat_memory_bot_reply", None)
            if callable(capture) and outcome.sent_count > 0:
                try:
                    await capture(event)
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 已发送分段记忆入队失败：{exc}")
            logger.debug(
                f"{LOG_PREFIX} 自然分段后续分段已取消：已发送 "
                f"{outcome.sent_count}/{len(segments)} 条。"
            )
            return True
        if outcome.status == "failed":
            logger.debug(
                f"{LOG_PREFIX} 自然分段发送失败，默认发送：{outcome.error}",
            )
            return False
        try:
            marker = getattr(self, "mark_structured_pending_bot_text", None)
            if callable(marker):
                marker(event, "\n".join(segments))
            reaction = getattr(self, "note_tool_reaction_message_sent", None)
            if callable(reaction):
                await reaction(event)
            scheduler = getattr(self, "schedule_pending_chat_state_refresh", None)
            if callable(scheduler):
                scheduler(event)
            self._note_chat_style_segmented_send(event)
            capture = getattr(self, "capture_chat_memory_bot_reply", None)
            if callable(capture):
                try:
                    await capture(event)
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 分段回复记忆入队失败：{exc}")
            logger.debug(
                f"{LOG_PREFIX} 自然分段发送：{len(segments)} 段；"
                f"{self._chat_style_pending_trace(pending)}"
            )
            return True
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 自然分段发送后处理失败：{exc}", exc_info=True)
            # 投递已经完成，后处理失败不应让调用方误判为未发送而重试。
            return True

    def apply_chat_style_before_send(self, event: Any) -> bool:
        if not self._chat_style_enabled():
            if event is not None:
                setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, [])
            return False
        if getattr(event, "_daily_life_natural_fallback_active", False):
            return False
        if (
            getattr(event, "_daily_life_semantic_segment_plan", None) is not None
            and self._semantic_segment_enabled()
        ):
            return False
        if event is not None:
            setattr(event, self._CHAT_STYLE_PENDING_SEGMENTS_ATTR, [])
        style = getattr(getattr(self, "config", None), "chat_style", None)
        if not style:
            return False
        reply_text = self._voice_switch_reply_text_from_event(event)
        if not reply_text:
            return False
        context = self._chat_style_context(event)
        if event is not None:
            setattr(event, "_daily_life_chat_style_context", context)
        if self._chat_style_should_keep_default_send(event, reply_text):
            self.log_chat_style_trace(event, reply_text, context, changed=False)
            return False
        changed = False
        plans = self._plan_chat_style_natural_segments(
            reply_text,
            self._chat_style_limit_for_event(event),
        )
        plans = self._chat_style_clean_segment_plans(event, plans, reply_text)
        if self._replace_text_result_with_segments(event, plans):
            changed = True
            reply_text = "\n".join(segment.text for segment in plans)
        elif plans and self._chat_style_punctuation_cleaning_active(event, reply_text):
            cleaned_text = plans[0].text
            if cleaned_text != reply_text:
                if self._replace_text_result(event, cleaned_text):
                    changed = True
                    reply_text = cleaned_text
        self.log_chat_style_trace(event, reply_text, context, changed=changed)
        return changed

    def log_chat_style_trace(
        self,
        event: Any,
        reply_text: str,
        context: dict[str, Any] | None = None,
        *,
        changed: bool = False,
    ) -> None:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        if not self._chat_style_enabled() or not style:
            return
        context = context if isinstance(context, dict) else {}
        scope = context.get("scope") or (
            "group"
            if event is not None and self._event_is_group_message(event)
            else "private"
        )
        logger.debug(
            f"{LOG_PREFIX} 表达节奏：通道={'群聊' if scope == 'group' else '私聊'}；"
            f"自然分段={'是' if changed else '否'}；"
            f"长度={len(self._chat_style_compact_text(reply_text))}"
        )
