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


def format_now(fmt: str = "%Y-%m-%d %H:%M") -> str:
    return now().strftime(fmt)
