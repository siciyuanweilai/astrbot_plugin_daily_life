from __future__ import annotations

_RATIO_SIZE_BY_RESOLUTION: dict[str, dict[str, str]] = {
    "720p": {
        "1:1": "1280x720",
        "16:9": "1280x720",
        "9:16": "720x1280",
        "4:3": "1280x720",
        "3:4": "720x1280",
    },
    "1080p": {
        "1:1": "1792x1024",
        "16:9": "1792x1024",
        "9:16": "1024x1792",
        "4:3": "1792x1024",
        "3:4": "1024x1792",
    },
}


def video_size(aspect_ratio: str, resolution: str) -> str:
    ratio = _video_supported_ratio(aspect_ratio)
    res = str(resolution or "720p").strip().lower() or "720p"
    sizes = _RATIO_SIZE_BY_RESOLUTION.get(res) or _RATIO_SIZE_BY_RESOLUTION["720p"]
    return sizes[ratio]


def _video_supported_ratio(aspect_ratio: str) -> str:
    ratio = str(aspect_ratio or "1:1").strip() or "1:1"
    if ratio in _RATIO_SIZE_BY_RESOLUTION["720p"]:
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
    return "9:16" if width < height else "16:9"
