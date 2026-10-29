from __future__ import annotations

import random

from typing import Any, Protocol


class ChatDelayComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _chat_style_compact_text: Any
    _chat_style_enabled: Any
    _chat_style_segment_delay_seconds: Any
    _chat_style_typing_weight: Any
    config: Any


class ChatDelayComponent:
    def __init__(self, context: ChatDelayComponentContext) -> None:
        self.context = context

    _CHAT_STYLE_MAX_DELAY_SECONDS = 3.5


    def _chat_style_natural_segment_delay_seconds(
        self,
        previous_segment: str = "",
        next_segment: str = "",
        *,
        previous_break_kind: str = "",
    ) -> float:
        pause = "short" if previous_break_kind == "soft" else "normal"
        return self.context._chat_style_segment_delay_seconds(
            previous_segment, next_segment, pause=pause
        )

    def _chat_style_segment_delay_seconds(
        self,
        previous_segment: str = "",
        next_segment: str = "",
        *,
        pause: str = "normal",
    ) -> float:
        checker = getattr(self.context, "_chat_style_enabled", None)
        if callable(checker) and not checker():
            return 0.0
        settings = getattr(getattr(self.context, "config", None), "chat_style", None)
        if not callable(checker) and not bool(
            settings and getattr(settings, "enabled", False)
        ):
            return 0.0
        minimum = max(
            0.0,
            min(
                float(getattr(settings, "segment_min_delay_seconds", 1.5) or 0.0),
                8.0,
            ),
        )
        maximum = max(
            0.0,
            min(
                float(getattr(settings, "segment_max_delay_seconds", 3.5) or 0.0),
                8.0,
            ),
        )
        if maximum <= 0:
            return 0.0
        minimum, maximum = sorted((minimum, maximum))
        if maximum == minimum:
            return round(minimum, 2)
        ranges = {
            "none": (0.0, 0.15),
            "short": (0.1, 0.3),
            "normal": (0.25, 0.55),
            "long": (0.5, 0.85),
        }
        low_ratio, high_ratio = ranges.get(pause, ranges["normal"])
        weight = max(
            self.context._chat_style_typing_weight(previous_segment),
            self.context._chat_style_typing_weight(next_segment),
        )
        adjustment = min(0.15, weight * 0.006)
        span = maximum - minimum
        low = min(maximum, round(minimum + span * (low_ratio + adjustment), 2))
        high = min(maximum, round(minimum + span * (high_ratio + adjustment), 2))
        if high <= low:
            return low
        return round(random.uniform(low, high), 2)
