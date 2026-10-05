import datetime
from typing import Any

from ..models import normalize_timeline_day_offsets, timeline_item_minutes
from .tools import timeline_item_datetime


def _field(item: Any, key: str) -> str:
    if hasattr(item, key):
        return str(getattr(item, key) or "").strip()
    if isinstance(item, dict):
        return str(item.get(key) or "").strip()
    return ""


def future_outfit_timing_issue(
    outfit: str,
    timeline: list,
    current_minutes: int | None = None,
    *,
    current_time: datetime.datetime | None = None,
    timeline_date: object = None,
    source_timeline_time: str = "",
) -> str:
    """校验明确引用的换装证据时间，不从衣服描述推断事件来源。"""
    if (
        not str(outfit or "").strip()
        or not source_timeline_time
        or not isinstance(timeline, list)
    ):
        return ""
    normalize_timeline_day_offsets(timeline)
    for item in timeline:
        if _field(item, "time") != source_timeline_time:
            continue
        if current_time is not None and timeline_date is not None:
            item_time = timeline_item_datetime(item, timeline_date)
            if item_time is None or item_time <= current_time:
                continue
        else:
            if current_minutes is None:
                return ""
            item_minutes = timeline_item_minutes(item)
            if item_minutes is None or item_minutes <= current_minutes:
                continue
        return f"当前穿搭引用了 {source_timeline_time} 尚未发生的换装证据"
    return ""
