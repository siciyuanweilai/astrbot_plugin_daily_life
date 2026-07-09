from __future__ import annotations

from typing import Any


def sight_provider_id(runtime: Any, field: str = "") -> str:
    settings = getattr(getattr(runtime, "config", None), "sight", None)
    specific = str(getattr(settings, field, "") or "").strip() if field else ""
    if specific:
        return specific
    if field == "frame_provider":
        vision = getattr(getattr(runtime, "config", None), "vision", None)
        return str(getattr(vision, "provider", "") or "").strip()
    return ""


async def get_sight_provider(runtime: Any, field: str = "") -> Any:
    composer = getattr(runtime, "composer", None)
    getter = getattr(composer, "_get_provider", None)
    if not callable(getter):
        return None
    return await getter(sight_provider_id(runtime, field))
