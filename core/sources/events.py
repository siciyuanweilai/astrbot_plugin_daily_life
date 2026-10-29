from collections.abc import Mapping
from typing import Any


def safe_invoke(value: Any) -> Any:
    if not callable(value):
        return None
    try:
        return value()
    except Exception:
        return None


def iter_event_sources(event: Any, limit: int = 4) -> list[Any]:
    sources: list[Any] = []
    current = event
    seen: set[int] = set()
    for _ in range(limit):
        if current is None:
            break
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        sources.append(current)

        nested = getattr(current, "event", None)
        if nested is not None and nested is not current:
            current = nested
            continue

        context = getattr(current, "context", None)
        nested = getattr(context, "event", None) if context is not None else None
        if nested is not None and nested is not current:
            current = nested
            continue
        break
    return sources


def is_chat_message_event(event: Any) -> bool:
    """排除明确的平台通知和请求，保留未提供事件类型的平台消息。"""
    if event is None:
        return False
    for source in iter_event_sources(event):
        message_obj = getattr(source, "message_obj", None)
        for raw in (
            getattr(message_obj, "raw_message", None),
            getattr(source, "raw_message", None),
        ):
            if not isinstance(raw, Mapping):
                continue
            post_type = str(raw.get("post_type") or "").strip()
            if post_type and post_type not in {"message", "message_sent"}:
                return False
    return True


def event_call(event: Any, method_name: str) -> str:
    for source in iter_event_sources(event):
        value = str(safe_invoke(getattr(source, method_name, None)) or "").strip()
        if value:
            return value
    return ""


def event_attr(event: Any, attr_name: str) -> str:
    for source in iter_event_sources(event):
        value = str(getattr(source, attr_name, "") or "").strip()
        if value:
            return value
    return ""


def has_event_call(value: Any, name: str) -> bool:
    return callable(getattr(value, name, None))
