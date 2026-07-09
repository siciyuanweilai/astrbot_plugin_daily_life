import datetime
from typing import Any


def _field(item: Any, key: str) -> str:
    if hasattr(item, key):
        return str(getattr(item, key) or "").strip()
    if isinstance(item, dict):
        return str(item.get(key) or "").strip()
    return ""


def _minutes(value: object) -> int | None:
    raw = str(value or "").strip()
    try:
        hour, minute = raw.split(":", 1)
        hour_int = int(hour)
        minute_int = int(minute)
    except (TypeError, ValueError):
        return None
    if 0 <= hour_int <= 23 and 0 <= minute_int <= 59:
        return hour_int * 60 + minute_int
    return None


def _date(value: object) -> datetime.date | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.datetime.strptime(str(value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _datetime_on_date(item: Any, date_value: object) -> datetime.datetime | None:
    date = _date(date_value)
    minutes = _minutes(_field(item, "time"))
    if date is None or minutes is None:
        return None
    return datetime.datetime.combine(date, datetime.time(minutes // 60, minutes % 60))


def _normalize_text(value: object) -> str:
    text = str(value or "")
    return "".join(char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _longest_common_run(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            value = previous[index - 1] + 1 if left_char == right_char else 0
            current.append(value)
            if value > best:
                best = value
        previous = current
    return best


def _outfit_overlap_threshold(left: str, right: str) -> int:
    base = min(len(left), len(right))
    if base < 8:
        return 0
    return max(8, min(18, int(base * 0.45)))


def _looks_like_same_outfit(context: str, outfit: str) -> bool:
    fragment_text = _normalize_text(context)
    outfit_text = _normalize_text(outfit)
    if not fragment_text or not outfit_text:
        return False
    threshold = _outfit_overlap_threshold(fragment_text, outfit_text)
    if not threshold:
        return False
    if len(outfit_text) >= threshold and outfit_text in fragment_text:
        return True
    if len(fragment_text) >= threshold and fragment_text in outfit_text:
        return True

    return _longest_common_run(fragment_text, outfit_text) >= threshold


def future_outfit_timing_issue(
    outfit: str,
    timeline: list,
    current_minutes: int | None = None,
    *,
    current_time: datetime.datetime | None = None,
    timeline_date: object = None,
) -> str:
    if not str(outfit or "").strip() or not isinstance(timeline, list):
        return ""
    for item in timeline:
        if current_time is not None and timeline_date is not None:
            item_time = _datetime_on_date(item, timeline_date)
            if item_time is None or item_time <= current_time:
                continue
        else:
            if current_minutes is None:
                return ""
            item_minutes = _minutes(_field(item, "time"))
            if item_minutes is None or item_minutes <= current_minutes:
                continue
        activity = _field(item, "activity")
        if activity and _looks_like_same_outfit(activity, outfit):
            time_text = _field(item, "time") or "未来"
            short_fragment = " ".join(activity.split())[:40]
            return f"当前穿搭疑似提前使用了 {time_text} 尚未发生的换装内容：{short_fragment}"
    return ""
