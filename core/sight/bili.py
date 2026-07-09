from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp
from astrbot.api import logger

from ..runtime.markers import LOG_PREFIX
from .auth import cookie_header


SHORT_DOMAINS = {"b23.tv", "bili2233.cn", "bili22.cn", "bili23.cn", "bili33.cn"}
BILI_DOMAINS = {
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
    "space.bilibili.com",
    "live.bilibili.com",
    "t.bilibili.com",
    *SHORT_DOMAINS,
}
URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
BVID_RE = re.compile(r"(?<![0-9A-Za-z])BV[0-9A-Za-z]{10}(?![0-9A-Za-z])")
CQ_JSON_RE = re.compile(r"\[CQ:json,data=(.*?)\]", re.DOTALL)
TRAILING_URL_CHARS = "\"'`}>]),，。)、）！!？?；;：:"


@dataclass(slots=True)
class BiliTarget:
    bvid: str = ""
    url: str = ""
    source: str = "text"
    page: int = 0

    @property
    def canonical_url(self) -> str:
        if self.bvid:
            url = f"https://www.bilibili.com/video/{self.bvid}"
            if self.page > 0:
                return f"{url}?p={self.page}"
            return url
        return self.url

    @property
    def identity(self) -> str:
        return self.bvid or self.url


@dataclass(slots=True)
class BiliMetadata:
    title: str = ""
    author: str = ""
    duration: int = 0
    bvid: str = ""
    aid: int = 0
    cid: int = 0
    page: int = 1
    url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "uploader": self.author,
            "owner_name": self.author,
            "duration": self.duration,
            "platform": "bilibili",
            "bvid": self.bvid,
            "aid": self.aid,
            "cid": self.cid,
            "page": self.page,
            "url": self.url,
        }


def find_bili_target(event: Any) -> BiliTarget | None:
    for payload in _event_payloads(event):
        target = target_from_value(payload)
        if target:
            return target
    return None


async def resolve_bili_target(target: BiliTarget, *, timeout_seconds: int = 10) -> BiliTarget | None:
    if not target:
        return None
    if target.bvid:
        canonical_url = target.canonical_url
        if target.url == canonical_url:
            return target
        return BiliTarget(bvid=target.bvid, url=canonical_url, source=target.source, page=target.page)
    if not is_short_url(target.url):
        return target if target.url else None
    resolved = await follow_redirect(target.url, timeout_seconds=timeout_seconds)
    return target_from_text(resolved, source=target.source) or target


async def fetch_bili_metadata(
    target: BiliTarget,
    *,
    timeout_seconds: int = 10,
    cookies: dict[str, str] | None = None,
) -> BiliMetadata | None:
    if not target or not target.bvid:
        return None
    timeout = aiohttp.ClientTimeout(total=max(3, min(int(timeout_seconds or 10), 20)))
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://api.bilibili.com/x/web-interface/view",
                params={"bvid": target.bvid},
                headers=_headers(cookies),
            ) as response:
                if int(getattr(response, "status", 0) or 0) >= 400:
                    logger.debug(f"{LOG_PREFIX} B站视频元数据请求失败：HTTP {getattr(response, 'status', '')}；{target.bvid}")
                    return None
                payload = await response.json(content_type=None)
    except Exception as exc:
        logger.debug(f"{LOG_PREFIX} B站视频元数据请求异常：{target.bvid}；{type(exc).__name__}: {exc}")
        return None
    if isinstance(payload, dict) and payload.get("code") not in (None, 0):
        logger.debug(
            f"{LOG_PREFIX} B站视频元数据请求失败："
            f"code={payload.get('code')}；message={payload.get('message') or payload.get('msg') or ''}；{target.bvid}"
        )
        return None
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}
    if not data:
        logger.debug(f"{LOG_PREFIX} B站视频元数据请求失败：响应缺少 data；{target.bvid}")
        return None
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    page = target.page or 1
    cid = _pick_cid(data, page)
    return BiliMetadata(
        title=str(data.get("title") or "").strip(),
        author=str(owner.get("name") or "").strip(),
        duration=_int(data.get("duration")),
        bvid=str(data.get("bvid") or target.bvid or "").strip(),
        aid=_int(data.get("aid")),
        cid=cid,
        page=page,
        url=target.canonical_url,
    )


async def follow_redirect(url: str, *, timeout_seconds: int = 10) -> str:
    timeout = aiohttp.ClientTimeout(total=max(3, min(int(timeout_seconds or 10), 20)))
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com",
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                return str(getattr(response, "url", "") or url)
    except Exception:
        return ""


def target_from_value(value: Any, *, source: str = "event", depth: int = 0) -> BiliTarget | None:
    if value is None or depth > 6:
        return None
    if isinstance(value, str):
        return target_from_text(value, source=source)
    if isinstance(value, dict):
        for key in ("qqdocurl", "jumpUrl", "jump_url", "url", "sourceUrl", "source_url", "appUrl", "appurl"):
            target = target_from_text(value.get(key), source=key)
            if target:
                return target
        for key in ("raw_message", "message_str", "text", "content", "data"):
            target = target_from_value(value.get(key), source=key, depth=depth + 1)
            if target:
                return target
        for nested in value.values():
            target = target_from_value(nested, source=source, depth=depth + 1)
            if target:
                return target
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            target = target_from_value(item, source=source, depth=depth + 1)
            if target:
                return target
    for attr in ("raw_message", "message_str", "text", "content", "data", "url"):
        if hasattr(value, attr):
            target = target_from_value(getattr(value, attr, None), source=attr, depth=depth + 1)
            if target:
                return target
    return None


def target_from_text(value: Any, *, source: str = "text") -> BiliTarget | None:
    text = _normalize_text(value)
    if not text:
        return None
    card = _target_from_json_text(text, source=source)
    if card:
        return card
    cq_card = _target_from_cq_json(text, source=source)
    if cq_card:
        return cq_card
    for match in URL_RE.finditer(text):
        target = target_from_url(match.group(0), source=source)
        if target:
            return target
    bvid_match = BVID_RE.search(text)
    if bvid_match:
        return BiliTarget(bvid=bvid_match.group(0), source=source)
    return None


def target_from_url(value: Any, *, source: str = "url") -> BiliTarget | None:
    url = clean_url(value)
    if not url or not is_bili_url(url):
        return None
    bvid = extract_bvid(url)
    page = extract_page(url)
    if bvid:
        canonical = f"https://www.bilibili.com/video/{bvid}"
        if page > 0:
            canonical = f"{canonical}?p={page}"
        return BiliTarget(bvid=bvid, url=canonical, source=source, page=page)
    return BiliTarget(url=url, source=source, page=page)


def clean_url(value: Any) -> str:
    text = _normalize_text(value)
    return text.strip().strip("<>").rstrip(TRAILING_URL_CHARS)


def is_bili_url(value: Any) -> bool:
    try:
        host = (urlparse(clean_url(value)).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in BILI_DOMAINS)


def is_short_url(value: Any) -> bool:
    try:
        host = (urlparse(clean_url(value)).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return host in SHORT_DOMAINS


def extract_bvid(value: Any) -> str:
    match = BVID_RE.search(str(value or ""))
    return match.group(0) if match else ""


def extract_page(value: Any) -> int:
    try:
        query = parse_qs(urlparse(str(value or "")).query)
    except Exception:
        return 0
    for key in ("p", "page"):
        raw = (query.get(key) or [""])[0]
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page > 0:
            return page
    return 0


def _event_payloads(event: Any) -> list[Any]:
    payloads: list[Any] = []
    for source in _event_sources(event):
        payloads.extend(
            [
                getattr(source, "message_str", None),
                getattr(source, "raw_message", None),
                getattr(getattr(source, "message_obj", None), "raw_message", None),
                getattr(getattr(source, "message_obj", None), "message", None),
            ]
        )
        getter = getattr(source, "get_messages", None)
        if callable(getter):
            try:
                payloads.append(getter())
            except Exception:
                pass
    return payloads


def _event_sources(event: Any) -> list[Any]:
    if event is None:
        return []
    sources = [event]
    message_obj = getattr(event, "message_obj", None)
    if message_obj is not None:
        sources.append(message_obj)
    return sources


def _target_from_cq_json(text: str, *, source: str) -> BiliTarget | None:
    for match in CQ_JSON_RE.finditer(text or ""):
        payload = (
            match.group(1)
            .replace("&#44;", ",")
            .replace("&#91;", "[")
            .replace("&#93;", "]")
        )
        target = _target_from_json_text(payload, source=source)
        if target:
            return target
    return None


def _target_from_json_text(text: str, *, source: str) -> BiliTarget | None:
    value = str(text or "").strip()
    if not value.startswith(("{", "[")):
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return target_from_value(payload, source=source, depth=1)


def _normalize_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\\/", "/")
    try:
        text = unquote(text)
    except Exception:
        pass
    return text.strip()


def _headers(cookies: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com",
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
    }
    text = cookie_header(cookies)
    if text:
        headers["Cookie"] = text
    return headers


def _pick_cid(data: dict[str, Any], page: int = 1) -> int:
    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    if pages:
        index = max(0, min((page or 1) - 1, len(pages) - 1))
        try:
            return int(pages[index].get("cid") or 0)
        except (TypeError, ValueError):
            return 0
    return _int(data.get("cid"))


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
