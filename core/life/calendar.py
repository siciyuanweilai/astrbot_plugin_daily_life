"""供聊天与生活规划共用的精简确定性日历事实。"""

from __future__ import annotations

import datetime
from typing import Any

import chinese_calendar

try:  # 旧安装环境升级依赖期间仍应允许插件被导入。
    from lunardate import LunarDate
except ImportError:  # pragma: no cover - 仅在依赖尚未安装时触发
    LunarDate = None  # type: ignore[assignment,misc]


_SEASONS = (
    (1, "冬季"),
    (2, "冬季"),
    (3, "春季"),
    (4, "春季"),
    (5, "春季"),
    (6, "夏季"),
    (7, "夏季"),
    (8, "夏季"),
    (9, "秋季"),
    (10, "秋季"),
    (11, "秋季"),
    (12, "冬季"),
)

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

_LUNAR_FESTIVALS = {
    (1, 1): "春节",
    (1, 15): "元宵节",
    (2, 2): "龙抬头",
    (5, 5): "端午节",
    (7, 7): "七夕",
    (7, 15): "中元节",
    (8, 15): "中秋节",
    (9, 9): "重阳节",
    (12, 8): "腊八节",
    (12, 23): "小年",
    (12, 24): "小年",
}


def _solar_term_context(value: datetime.date) -> tuple[str, str]:
    getter = getattr(chinese_calendar, "get_solar_terms", None)
    if not callable(getter):
        return "", ""
    try:
        terms = getter(
            datetime.date(value.year, 1, 1),
            datetime.date(value.year, 12, 31),
        )
    except Exception:
        return "", ""
    previous = ""
    upcoming = ""
    for term_date, term_name in terms or ():
        if term_date <= value:
            previous = str(term_name or "").strip()
        elif not upcoming:
            upcoming = str(term_name or "").strip()
    return previous, upcoming


def _lunar_festival(value: datetime.date) -> str:
    if LunarDate is None:
        return ""
    try:
        converter = getattr(LunarDate, "from_solar_date", None)
        lunar = (
            converter(value.year, value.month, value.day)
            if callable(converter)
            else LunarDate.fromSolarDate(value.year, value.month, value.day)
        )
        is_leap_month = getattr(lunar, "is_leap_month", None)
        if is_leap_month is None:
            is_leap_month = getattr(lunar, "isLeapMonth", False)
        if bool(is_leap_month):
            return ""
        return _LUNAR_FESTIVALS.get(
            (int(getattr(lunar, "month", 0)), int(getattr(lunar, "day", 0))),
            "",
        )
    except Exception:
        return ""


def _legal_calendar_context(value: datetime.date) -> str:
    try:
        on_holiday, holiday_name = chinese_calendar.get_holiday_detail(value)
        if on_holiday:
            return f"法定节假日：{holiday_name}" if holiday_name else "法定节假日"
        if chinese_calendar.is_workday(value) and value.weekday() >= 5:
            return "调休工作日"
        if not chinese_calendar.is_workday(value) and value.weekday() >= 5:
            return "周末休息日"
    except Exception:
        pass
    return "普通工作日" if value.weekday() < 5 else "周末休息日"


def _coerce_date(value: datetime.date | datetime.datetime | Any) -> datetime.date | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    return value if isinstance(value, datetime.date) else None


def format_calendar_context(value: datetime.date | datetime.datetime | Any) -> str:
    """返回供提示词使用的日期、工作日与节日事实。"""
    value = _coerce_date(value)
    if value is None:
        return ""
    festival = _lunar_festival(value)
    parts = [
        f"日期：{value.isoformat()}",
        _WEEKDAYS[value.weekday()],
        _legal_calendar_context(value),
    ]
    if festival:
        parts.append(f"传统节日：{festival}")
    return "；".join(parts)


def format_season_context(value: datetime.date | datetime.datetime | Any) -> str:
    """返回与日历及节日分离的季节事实。

    这些内容只作为事实背景，不预设固定活动，避免模型把每个节日都套入模板。
    """
    value = _coerce_date(value)
    if value is None:
        return ""
    _, season = _SEASONS[value.month - 1]
    previous_term, upcoming_term = _solar_term_context(value)
    parts = [season]
    if previous_term:
        parts.append(f"当前节气阶段：{previous_term}")
    if upcoming_term:
        parts.append(f"下一个节气：{upcoming_term}")
    parts.append(
        "使用原则：节日和季节只作生活氛围参考；涉及温度、衣物和洗漱时，"
        "以实时天气、室内外场景和用户明确描述为准，不凭季节臆测。"
    )
    return "；".join(parts)


__all__ = ["format_calendar_context", "format_season_context"]
