from __future__ import annotations

import random


class ChatDelayMixin:
    _CHAT_STYLE_SEGMENT_DELAY_RANGE = (0.8, 1.8)
    _CHAT_STYLE_SHORT_DELAY_RANGE = (0.45, 0.9)
    _CHAT_STYLE_LONG_DELAY_RANGE = (1.1, 2.1)
    _CHAT_STYLE_LEAD_SHORT_DELAY_RANGE = (0.65, 1.05)
    _CHAT_STYLE_LEAD_MEDIUM_DELAY_RANGE = (0.85, 1.35)
    _CHAT_STYLE_LEAD_LONG_DELAY_RANGE = (1.0, 1.55)
    _CHAT_STYLE_MAX_DELAY_SECONDS = 3.5

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
                return self._CHAT_STYLE_LEAD_SHORT_DELAY_RANGE
            if next_length <= 16:
                return self._CHAT_STYLE_LEAD_MEDIUM_DELAY_RANGE
            return self._CHAT_STYLE_LEAD_LONG_DELAY_RANGE
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
        pause = "short" if previous_break_kind == "soft" else "normal"
        return self._chat_style_segment_delay_seconds(
            previous_segment, next_segment, pause=pause
        )

    def _chat_style_segment_delay_seconds(
        self,
        previous_segment: str = "",
        next_segment: str = "",
        *,
        pause: str = "normal",
    ) -> float:
        checker = getattr(self, "_chat_style_enabled", None)
        if callable(checker) and not checker():
            return 0.0
        settings = getattr(getattr(self, "config", None), "chat_style", None)
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
            self._chat_style_typing_weight(previous_segment),
            self._chat_style_typing_weight(next_segment),
        )
        adjustment = min(0.15, weight * 0.006)
        span = maximum - minimum
        low = min(maximum, round(minimum + span * (low_ratio + adjustment), 2))
        high = min(maximum, round(minimum + span * (high_ratio + adjustment), 2))
        if high <= low:
            return low
        return round(random.uniform(low, high), 2)
