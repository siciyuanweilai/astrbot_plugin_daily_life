from __future__ import annotations

import ast
from typing import Any


def _literal_collection(value: str) -> Any:
    raw = value.strip()
    if not raw or raw[0] not in "[{(" or raw[-1] not in "]})":
        return None
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return None
    return parsed if isinstance(parsed, (list, tuple, set, dict)) else None


def _compact_parts(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            label = compact_text(key, 40)
            text = compact_text(item, 160)
            if text:
                result.append(f"{label}：{text}" if label else text)
        return result
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        items = sorted(value, key=lambda item: str(item)) if isinstance(value, set) else value
        for item in items:
            for part in _compact_parts(item):
                if part and part not in result:
                    result.append(part)
        return result

    raw = str(value or "").strip()
    if not raw:
        return []
    parsed = _literal_collection(raw)
    if parsed is not None:
        return _compact_parts(parsed)

    segments = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "；").replace(";", "；").split("；")
    if len(segments) > 1:
        result: list[str] = []
        for segment in segments:
            for part in _compact_parts(segment):
                if part and part not in result:
                    result.append(part)
        return result
    return [" ".join(raw.split())]


def compact_text(value: Any, limit: int = 240) -> str:
    text = "；".join(_compact_parts(value))
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
