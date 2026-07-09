from __future__ import annotations

from typing import Any


def as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enable", "enabled", "开启", "是"}:
        return True
    if text in {"0", "false", "no", "off", "disable", "disabled", "关闭", "否"}:
        return False
    return default


def as_int(value: Any, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        result = default
    if min_value is not None:
        result = max(result, min_value)
    if max_value is not None:
        result = min(result, max_value)
    return result


def as_float(value: Any, default: float, min_value: float | None = None, max_value: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if min_value is not None:
        result = max(result, min_value)
    if max_value is not None:
        result = min(result, max_value)
    return result


def as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        value = value.values()
    try:
        iterator = iter(value)
    except TypeError:
        text = str(value).strip()
        return [text] if text else []
    result = []
    for item in iterator:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def as_reference_image_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        path = as_str(raw.get("path", "")).strip()
        if not path:
            continue
        item: dict[str, Any] = {"path": path}
        name = as_str(raw.get("name", "")).strip()
        if name:
            item["name"] = name
        mime = as_str(raw.get("mime", "")).strip()
        if mime:
            item["mime"] = mime
        size = as_int(raw.get("size", 0), 0, 0)
        if size > 0:
            item["size"] = size
        result.append(item)
    return result[:12]


def as_str_map(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key).strip(): str(val).strip() for key, val in value.items() if str(key).strip()}
    result: dict[str, str] = {}
    for line in str(value or "").splitlines():
        text = line.strip()
        if not text:
            continue
        key, sep, raw = text.partition(":")
        if not sep:
            key, sep, raw = text.partition("：")
        key = key.strip()
        if key:
            result[key] = raw.strip() if sep else ""
    return result


def as_float_map(value: Any, default: dict[str, float]) -> dict[str, float]:
    result = dict(default)
    for key, raw in as_str_map(value).items():
        try:
            result[key] = max(0.25, min(4.0, float(raw)))
        except (TypeError, ValueError):
            continue
    return result


def normalize_time(value: Any, default: str) -> str:
    try:
        hour, minute = map(int, str(value or default).strip().split(":", 1))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        pass
    return default


def normalize_time_window(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for separator in ("-", "～", "~", "—", "–", "到"):
        if separator in text:
            start_raw, end_raw = text.split(separator, 1)
            start = normalize_time(start_raw, "")
            end = normalize_time(end_raw, "")
            if start and end and start != end:
                return f"{start}-{end}"
            return ""
    return ""


def dict_section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}
