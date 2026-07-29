from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


SEARCH_SOURCE_SCOPES = {"web", "x", "both"}
SEARCH_TIME_RANGE_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
SEARCH_TOPICS = {"general", "news", "finance"}


@dataclass(frozen=True, slots=True)
class ExternalEvidenceRequest:
    category: str
    query: str
    topic: str
    time_range: str
    trace_id: str


def build_external_evidence_request(
    query: Any,
    category: Any,
) -> ExternalEvidenceRequest:
    """构建外部插件所需的搜索证据请求。"""
    keyword = str(query or "").strip()
    if not keyword:
        raise ValueError("检索内容不能为空")

    category_key = str(category or "").strip().lower()
    policies = {
        "news": ("news", "week"),
        "knowledge": ("general", ""),
        "recommendation": ("general", ""),
    }
    policy = policies.get(category_key)
    if policy is None:
        raise ValueError(f"不支持的分享检索类别: {category_key or '空'}")
    topic, time_range = policy
    return ExternalEvidenceRequest(
        category=category_key,
        query=keyword,
        topic=topic,
        time_range=time_range,
        trace_id=f"daily-share-{category_key}",
    )


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    depth: str
    source_scope: str
    platform: str
    start_date: str
    end_date: str
    image_search: bool
    image_understanding: bool
    umo: str
    topic: str = "general"
    include_raw_content: bool = False
    include_images: bool = False
    include_image_descriptions: bool = False
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()
    country: str = ""
    auto_parameters: bool = False
    exact_match: bool = False


@dataclass(frozen=True, slots=True)
class SearchInput:
    query: str
    depth: str = "quick"
    source_scope: str = "web"
    platform: str = ""
    time_range: str = ""
    start_date: str = ""
    end_date: str = ""
    image_search: bool = False
    image_understanding: bool = False
    umo: str = ""
    topic: str = "general"
    include_raw_content: bool = False
    include_images: bool = False
    include_image_descriptions: bool = False
    include_domains: list[str] | tuple[str, ...] = ()
    exclude_domains: list[str] | tuple[str, ...] = ()
    country: str = ""
    auto_parameters: bool = False
    exact_match: bool = False


def normalize_source_scope(value: Any) -> str:
    source_scope = str(value or "web").strip().lower() or "web"
    if source_scope not in SEARCH_SOURCE_SCOPES:
        raise ValueError("搜索来源仅支持 web、x 或 both")
    return source_scope


def normalize_topic(value: Any) -> str:
    topic = str(value or "general").strip().lower() or "general"
    if topic not in SEARCH_TOPICS:
        raise ValueError("搜索主题仅支持 general、news 或 finance")
    return topic


def normalize_domains(value: Any, limit: int = 300) -> tuple[str, ...]:
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        return ()
    result: list[str] = []
    for item in values:
        text = str(item or "").strip().lower()
        if text and text not in result:
            result.append(text[:255])
        if len(result) >= limit:
            break
    return tuple(result)


def resolve_search_dates(
    time_range: Any,
    start_date: Any,
    end_date: Any,
    *,
    today: date,
) -> tuple[str, str]:
    start_text = str(start_date or "").strip()
    end_text = str(end_date or "").strip()
    if start_text or end_text:
        try:
            start = date.fromisoformat(start_text) if start_text else None
            end = date.fromisoformat(end_text) if end_text else None
        except ValueError as exc:
            raise ValueError("搜索日期必须使用 YYYY-MM-DD 格式") from exc
        if (start is not None and start.isoformat() != start_text) or (
            end is not None and end.isoformat() != end_text
        ):
            raise ValueError("搜索日期必须使用 YYYY-MM-DD 格式")
        if start is not None and end is not None and start > end:
            raise ValueError("搜索开始日期不能晚于结束日期")
        return (
            start.isoformat() if start is not None else "",
            end.isoformat() if end is not None else "",
        )

    range_name = str(time_range or "").strip().lower()
    if not range_name:
        return "", ""
    days = SEARCH_TIME_RANGE_DAYS.get(range_name)
    if days is None:
        raise ValueError("搜索时间范围仅支持 day、week、month 或 year")
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()
