from __future__ import annotations

import unicodedata
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .model import SearchAnswer, SearchSource


_IMAGE_REJECTED_HOST_MARKERS = (
    "adserver",
    "analytics",
    "doubleclick",
    "intentiq",
    "openx",
    "pixel",
    "pubmatic",
    "rubiconproject",
    "sonobi",
    "tracking",
)
_IMAGE_REJECTED_PATH_MARKERS = {
    "favicon",
    "icon",
    "logo",
    "pixel",
    "tracker",
}
_IMAGE_REJECTED_EXTENSIONS = {".ico", ".svg", ".svgz"}


def compact(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def query_key(value: Any) -> str:
    return unicodedata.normalize("NFKC", compact(value, 1000)).casefold()


def source_key(value: Any) -> str:
    url = unicodedata.normalize("NFKC", str(value or "").strip())
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url.casefold()
    if not parsed.netloc:
        return url.casefold()
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if not host:
        return url.casefold()
    try:
        port = parsed.port
    except ValueError:
        return url.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    host_port = host if port is None or default_port else f"{host}:{port}"
    userinfo = parsed.netloc.rsplit("@", 1)[0] if "@" in parsed.netloc else ""
    netloc = f"{userinfo}@{host_port}" if userinfo else host_port
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            scheme,
            netloc,
            path,
            parsed.query,
            "",
        )
    )


def _image_url_is_usable(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").casefold()
    if any(marker in host for marker in _IMAGE_REJECTED_HOST_MARKERS):
        return False
    path = parsed.path.casefold()
    if any(path.endswith(extension) for extension in _IMAGE_REJECTED_EXTENSIONS):
        return False
    filename = path.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0]
    if stem in _IMAGE_REJECTED_PATH_MARKERS:
        return False
    return True


def normalize_image_assets(
    values: Any,
    *,
    source_url: str = "",
    source_title: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """统一服务提供商的图片条目，并移除无法展示的素材。"""
    if isinstance(values, (str, dict)):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            url = value.get("url") or value.get("uri") or value.get("image_url")
            description = value.get("description") or value.get("alt") or value.get("title")
            item_source = value.get("source_url") or value.get("page_url") or source_url
            item_title = value.get("source_title") or source_title
        else:
            url = value
            description = ""
            item_source = source_url
            item_title = source_title
        url_text = str(url or "").strip()
        key = source_key(url_text)
        if not key or key in seen or not _image_url_is_usable(url_text):
            continue
        seen.add(key)
        item: dict[str, Any] = {"url": url_text}
        if str(description or "").strip():
            item["description"] = compact(description, 500)
        if str(item_source or "").strip():
            item["source_url"] = str(item_source).strip()
        if str(item_title or "").strip():
            item["source_title"] = compact(item_title, 200)
        result.append(item)
        if len(result) >= max(1, int(limit)):
            break
    return result


def merge_image_assets(*groups: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in normalize_image_assets(group, limit=limit):
            key = source_key(item.get("url"))
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= max(1, int(limit)):
                return merged
    return merged


def merge_sources(*groups: list[SearchSource], limit: int) -> list[SearchSource]:
    result: list[SearchSource] = []
    seen: dict[str, SearchSource] = {}
    for group in groups:
        for item in group:
            key = source_key(item.url)
            if not key:
                continue
            existing = seen.get(key)
            if existing is not None:
                if not existing.title and item.title:
                    existing.title = item.title
                if not existing.snippet and item.snippet:
                    existing.snippet = item.snippet
                if not existing.provider and item.provider:
                    existing.provider = item.provider
                if existing.score is None and item.score is not None:
                    existing.score = item.score
                existing.images = merge_image_assets(existing.images, item.images)
                if not existing.favicon and item.favicon:
                    existing.favicon = item.favicon
                continue
            source = SearchSource(
                url=item.url,
                title=item.title,
                snippet=item.snippet,
                provider=item.provider,
                score=item.score,
                images=merge_image_assets(item.images),
                favicon=item.favicon,
            )
            seen[key] = source
            result.append(source)
            if len(result) >= limit:
                return result
    return result


def has_searched_content(answer: SearchAnswer) -> bool:
    return bool(answer.content and answer.searched)


def has_search_evidence(answer: SearchAnswer) -> bool:
    return bool(has_searched_content(answer) and answer.sources)


def source_domain(value: Any) -> str:
    try:
        return urlsplit(str(value or "").strip()).netloc.casefold()
    except ValueError:
        return ""


def evidence_quality(content: str, sources: list[SearchSource]) -> str:
    if not str(content or "").strip() or not sources:
        return "weak"
    distinct_sources = {source_key(item.url) for item in sources}
    distinct_sources.discard("")
    domains = {source_domain(item.url) for item in sources}
    domains.discard("")
    relevant_scored = [
        item for item in sources if item.score is not None and item.score >= 0.5
    ]
    native_sources = [
        item for item in sources if str(item.provider or "").startswith("grok-")
    ]
    if len(domains) >= 2 and (
        len(relevant_scored) >= 3
        or (len(native_sources) >= 3 and len(distinct_sources) >= 3)
    ):
        return "strong"
    return "partial"


def missing_aspects(quality: str) -> list[str]:
    if quality == "strong":
        return []
    if quality == "partial":
        return ["现有证据尚未形成充分交叉验证"]
    return ["缺少可核验来源"]


def bounded_answer(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    content_limit = max(0, limit - 3)
    paragraphs = [item.strip() for item in text.splitlines() if item.strip()]
    selected: list[str] = []
    length = 0
    for paragraph in paragraphs:
        extra = len(paragraph) + (2 if selected else 0)
        if selected and length + extra > limit:
            break
        if not selected and len(paragraph) > limit:
            return paragraph[:content_limit].rstrip() + "..."
        selected.append(paragraph)
        length += extra
    answer = "\n\n".join(selected).strip()
    return (answer or text)[:content_limit].rstrip() + "..."
