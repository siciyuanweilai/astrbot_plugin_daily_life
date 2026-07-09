from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

from .clip import SightClip


VIDEO_SUFFIXES = {
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".flv",
    ".ts",
}


def clean_source(value: Any) -> str:
    text = str(value or "").strip()
    for prefix in ("range:", "proxy:", "cache:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    if text.lower().startswith("file://"):
        parsed = urlparse(text)
        path = unquote(parsed.path or "")
        if parsed.netloc and path:
            path = f"//{parsed.netloc}{path}"
        elif parsed.netloc:
            path = unquote(parsed.netloc)
        if re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]
        return path
    if text.lower().startswith("file:"):
        return unquote(text[5:])
    return text


def component_kind(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("type") or item.get("kind") or "").strip().lower()
    explicit = str(getattr(item, "type", "") or getattr(item, "kind", "") or "").strip().lower()
    return explicit or item.__class__.__name__.strip().lower()


def component_data(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        data = item.get("data")
        return {**item, **data} if isinstance(data, dict) else dict(item)
    data: dict[str, Any] = {}
    nested = getattr(item, "data", None)
    if isinstance(nested, dict):
        data.update(nested)
    for key in (
        "file",
        "path",
        "url",
        "src",
        "video",
        "video_url",
        "file_id",
        "fileid",
        "id",
        "name",
        "file_name",
        "filename",
        "message_id",
        "target_message_id",
        "message",
        "messages",
        "chain",
        "items",
        "segments",
        "components",
    ):
        value = getattr(item, key, None)
        if value not in (None, ""):
            data[key] = value
    return data


def source_from_value(value: Any) -> str:
    if isinstance(value, str):
        return clean_source(value)
    if isinstance(value, dict):
        for key in ("url", "src", "path", "file_path", "file", "file_", "video_url", "video"):
            source = source_from_value(value.get(key))
            if source:
                return source
        data = value.get("data")
        if isinstance(data, dict):
            return source_from_value(data)
    return ""


def payload_from_item(item: Any) -> dict[str, str]:
    data = component_data(item)
    values: dict[str, str] = {}
    for key in ("file", "path", "url", "src", "video", "video_url"):
        source = clean_source(data.get(key))
        if source:
            values[key] = source
    for key in ("file_id", "fileid", "id", "name", "file_name", "filename"):
        text = str(data.get(key) or "").strip()
        if text:
            values[key] = text
    return values


def _suffix(value: str) -> str:
    text = clean_source(value)
    if text.lower().startswith(("http://", "https://")):
        text = urlparse(text).path
    return Path(text).suffix.lower()


def looks_like_video(kind: str, payload: dict[str, str]) -> bool:
    kind = str(kind or "").lower()
    source = source_from_value(payload)
    name = payload.get("name") or payload.get("file_name") or payload.get("filename") or source
    if "video" in kind:
        return True
    if "file" in kind and _suffix(name) in VIDEO_SUFFIXES:
        return True
    return bool(source and _suffix(source) in VIDEO_SUFFIXES)


def clip_from_item(
    item: Any,
    *,
    scope: str,
    message_id: str,
    origin: str,
    text: str = "",
) -> SightClip | None:
    kind = component_kind(item)
    payload = payload_from_item(item)
    if not looks_like_video(kind, payload):
        return None
    source = (
        payload.get("path")
        or payload.get("file")
        or payload.get("url")
        or payload.get("src")
        or payload.get("video_url")
        or payload.get("video")
        or ""
    )
    file_id = payload.get("file_id") or payload.get("fileid") or ""
    name = payload.get("name") or payload.get("file_name") or payload.get("filename") or ""
    return SightClip(
        scope=scope,
        message_id=message_id,
        source=source,
        file_id=file_id,
        name=name,
        origin=origin,
        text=text,
    )


def clips_from_items(
    items: Iterable[Any],
    *,
    scope: str,
    message_id: str,
    origin: str = "current",
    text: str = "",
) -> list[SightClip]:
    clips: list[SightClip] = []
    for item in items or []:
        clip = clip_from_item(
            item,
            scope=scope,
            message_id=message_id,
            origin=origin,
            text=text,
        )
        if clip:
            clips.append(clip)
    return dedupe_clips(clips)


def clips_from_value(
    value: Any,
    *,
    scope: str,
    message_id: str,
    origin: str,
    text: str = "",
    depth: int = 0,
) -> list[SightClip]:
    if value is None or depth > 4:
        return []

    if isinstance(value, str):
        return clips_from_cq_text(value, scope=scope, message_id=message_id, origin=origin, text=text)

    direct = clip_from_item(value, scope=scope, message_id=message_id, origin=origin, text=text)
    clips = [direct] if direct else []
    data = component_data(value)
    nested_message_id = str(data.get("target_message_id") or data.get("message_id") or message_id or "").strip()
    for key in ("chain", "message", "messages", "items", "segments", "components", "nodes"):
        nested = data.get(key)
        if isinstance(nested, list):
            for item in nested:
                clips.extend(
                    clips_from_value(
                        item,
                        scope=scope,
                        message_id=nested_message_id,
                        origin=origin,
                        text=text,
                        depth=depth + 1,
                    )
                )
    nested_data = data.get("data")
    if isinstance(nested_data, dict):
        clips.extend(
            clips_from_value(
                nested_data,
                scope=scope,
                message_id=nested_message_id,
                origin=origin,
                text=text,
                depth=depth + 1,
            )
        )
    return dedupe_clips(clip for clip in clips if clip)


def clips_from_cq_text(
    raw_message: str,
    *,
    scope: str,
    message_id: str,
    origin: str,
    text: str = "",
) -> list[SightClip]:
    clips: list[SightClip] = []
    for match in re.finditer(r"\[CQ:([^,\]]+)(?:,([^\]]*))?\]", raw_message or ""):
        kind = html.unescape(match.group(1)).strip()
        data: dict[str, str] = {"type": kind}
        for part in (match.group(2) or "").split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            data[html.unescape(key).strip()] = html.unescape(value).strip()
        clip = clip_from_item(data, scope=scope, message_id=message_id, origin=origin, text=text)
        if clip:
            clips.append(clip)
    return dedupe_clips(clips)


def clips_from_text_links(
    text: str,
    *,
    scope: str,
    message_id: str,
    origin: str = "text",
) -> list[SightClip]:
    clips: list[SightClip] = []
    for source in re.findall(r"https?://[^\s<>\"'，。！？；、]+", str(text or "")):
        if _suffix(source) in VIDEO_SUFFIXES:
            clips.append(
                SightClip(
                    scope=scope,
                    message_id=message_id,
                    source=source,
                    origin=origin,
                    text=text,
                )
            )
    return dedupe_clips(clips)


def explicit_clip(value: str, *, scope: str, message_id: str, text: str = "") -> SightClip | None:
    source = clean_source(value)
    if not source:
        return None
    return SightClip(scope=scope, message_id=message_id, source=source, origin="explicit", text=text)


def dedupe_clips(values: Iterable[SightClip | None]) -> list[SightClip]:
    seen: set[str] = set()
    result: list[SightClip] = []
    for clip in values:
        if not clip:
            continue
        key = clip.key
        if key in seen:
            continue
        seen.add(key)
        result.append(clip)
    return result

