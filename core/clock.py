from __future__ import annotations

import datetime
import zoneinfo

TIMEZONE_NAME = "Asia/Shanghai"
try:
    TIMEZONE = zoneinfo.ZoneInfo(TIMEZONE_NAME)
except zoneinfo.ZoneInfoNotFoundError:
    TIMEZONE = datetime.timezone(datetime.timedelta(hours=8), name=TIMEZONE_NAME)


def now() -> datetime.datetime:
    return datetime.datetime.now(TIMEZONE).replace(tzinfo=None)


def today() -> datetime.date:
    return now().date()


def timestamp() -> int:
    return int(datetime.datetime.now(TIMEZONE).timestamp())




def local_datetime(
    value: object, *, naive_timezone: datetime.tzinfo = TIMEZONE
) -> datetime.datetime | None:
    """解析时间并转换到生活时区；无时区值由字段的存储契约指定来源。"""
    try:
        parsed = (
            value if isinstance(value, datetime.datetime)
            else datetime.datetime.fromisoformat(str(value or "").strip())
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=naive_timezone)
        return parsed.astimezone(TIMEZONE).replace(tzinfo=None)
    except (ValueError, TypeError, OverflowError):
        return None


def local_timestamp(value: object) -> float | None:
    """供 SQLite 比较生活时间，不修改已有历史记录。"""
    parsed = local_datetime(value)
    return parsed.replace(tzinfo=TIMEZONE).timestamp() if parsed is not None else None
