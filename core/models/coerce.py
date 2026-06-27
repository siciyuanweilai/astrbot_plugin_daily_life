from __future__ import annotations

from typing import Any


def compact_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:limit]


def compact_texts(value: Any, limit: int = 12, item_limit: int = 80) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = compact_text(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result
