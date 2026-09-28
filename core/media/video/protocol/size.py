from __future__ import annotations

import math

_VIDEO_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "2:3", "3:2")


def video_aspect_ratio(aspect_ratio: str) -> str:
    ratio = str(aspect_ratio or "1:1").strip() or "1:1"
    if ratio in _VIDEO_RATIOS:
        return ratio
    if ":" not in ratio:
        return "16:9"
    left, right = ratio.split(":", 1)
    try:
        width = int(left)
        height = int(right)
    except ValueError:
        return "16:9"
    if width <= 0 or height <= 0:
        return "16:9"
    target = width / height
    return min(
        _VIDEO_RATIOS,
        key=lambda candidate: abs(math.log(target / _ratio_value(candidate))),
    )


def _ratio_value(ratio: str) -> float:
    width, height = ratio.split(":", 1)
    return int(width) / int(height)
