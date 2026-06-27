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
    ratio = str(aspect_ratio or "16:9").strip() or "16:9"
    res = str(resolution or "720p").strip().lower() or "720p"
    sizes = _RATIO_SIZE_BY_RESOLUTION.get(res) or _RATIO_SIZE_BY_RESOLUTION["720p"]
    return sizes.get(ratio) or sizes["16:9"]
