"""Date-aware appearance lookup, using only recorded appearance facts."""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

try:
    from lunardate import LunarDate
except ImportError:  # pragma: no cover - 仅在依赖尚未安装时触发
    LunarDate = None  # type: ignore[assignment,misc]

from .appearance import current_appearance_values, reference_outfit
from .wardrobe import format_outfit_components, normalize_outfit_components

_PREFIX = "appearance_snapshot:"
_FESTIVAL_LUNAR_DATES = {
    "春节": (1, 1),
    "元宵": (1, 15),
    "端午": (5, 5),
    "七夕": (7, 7),
    "中秋": (8, 15),
    "重阳": (9, 9),
}
_PERIODS = {
    "dawn": (0, 6),
    "morning": (6, 9),
    "forenoon": (9, 12),
    "noon": (12, 14),
    "afternoon": (14, 18),
    "evening": (18, 20),
    "night": (20, 23),
    "late_night": (23, 24),
}


def _festival_solar_date(year: int, month: int, day: int) -> dt.date | None:
    if LunarDate is None:
        return None
    try:
        lunar = LunarDate(year, month, day)
        converter = getattr(lunar, "to_solar_date", None)
        return converter() if callable(converter) else lunar.toSolarDate()
    except (ValueError, OverflowError):
        return None


def record_appearance_snapshot(day: Any, observed_at: dt.datetime) -> None:
    """Preserve each actual appearance change, including changes within a period."""
    values = current_appearance_values(day)
    components = normalize_outfit_components(day.meta.get("outfit_components"))
    if components.get("main_clothing", {}).get("state") in {
        "worn",
        "carried",
        "staged",
        "removed",
    }:
        values["outfit"] = format_outfit_components(components)
    if not values["outfit"]:
        return
    values["outfit_components"] = components
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True)
    snapshots = sorted(
        (key, value) for key, value in day.meta.items() if key.startswith(_PREFIX)
    )
    if snapshots and snapshots[-1][1] == encoded:
        return
    day.meta[_PREFIX + observed_at.isoformat(sep=" ", timespec="microseconds")] = (
        encoded
    )


def _snapshots(day: Any) -> list[tuple[dt.datetime, dict]]:
    rows = []
    for key, value in (getattr(day, "meta", {}) or {}).items():
        if not key.startswith(_PREFIX):
            continue
        try:
            at = dt.datetime.fromisoformat(key[len(_PREFIX) :])
            item = json.loads(value)
        except (ValueError, TypeError):
            continue
        if isinstance(item, dict) and item.get("outfit"):
            rows.append((at, item))
    return sorted(rows, key=lambda item: item[0])


def appearance_query(
    text: str, now: dt.datetime
) -> tuple[str, tuple[int, int] | None, int | None]:
    """Return a date/last/ambiguous target, a period and an optional minute."""
    target = ""
    explicit = re.search(
        r"(?:(20\d{2})[-年/])?(\d{1,2})[-月/](\d{1,2})(?:日|号)?", text
    )
    if explicit:
        try:
            date = dt.date(
                int(explicit[1] or now.year), int(explicit[2]), int(explicit[3])
            )
            if not explicit[1] and date > now.date():
                date = date.replace(year=date.year - 1)
            target = date.isoformat() if date <= now.date() else "ambiguous"
        except ValueError:
            return "ambiguous", None, None
    else:
        for markers, offset in (
            (("大前天",), 3),
            (("前天", "前两天", "前日", "前晚"), 2),
            (("昨天", "昨日", "前一天", "昨晚", "昨夜"), 1),
            (("今天", "今日"), 0),
        ):
            if any(marker in text for marker in markers):
                target = (now.date() - dt.timedelta(days=offset)).isoformat()
                break
    if not target:
        festival = re.search(
            r"(?:(20\d{2})年|(去年|前年|今年))?\s*"
            r"(春节|元宵|端午|七夕|中秋|重阳)(?:节)?",
            text,
        )
        if festival:
            lunar_month, lunar_day = _FESTIVAL_LUNAR_DATES[festival[3]]
            year = int(festival[1]) if festival[1] else now.year
            year -= {"去年": 1, "前年": 2}.get(festival[2], 0)
            date = _festival_solar_date(year, lunar_month, lunar_day)
            if date and not festival[1] and not festival[2] and date > now.date():
                date = _festival_solar_date(year - 1, lunar_month, lunar_day)
            target = date.isoformat() if date and date <= now.date() else "ambiguous"
    if not target:
        target = "last" if "上次" in text else "ambiguous"
    period = None
    for markers, window in (
        (("深夜",), (23, 24)),
        (("凌晨",), (0, 6)),
        (("昨晚", "昨夜", "前晚", "晚上", "夜里"), (18, 24)),
        (("傍晚",), (18, 20)),
        (("下午",), (14, 18)),
        (("中午",), (12, 14)),
        (("上午",), (9, 12)),
        (("早上", "早晨"), (6, 9)),
        (("白天",), (6, 18)),
    ):
        if any(marker in text for marker in markers):
            period = window
            break
    clock = re.search(
        r"(?<!\d)([01]?\d|2[0-3])(?:[:：](\d{2})|点(?:(\d{1,2})分?)?)", text
    )
    minute = None
    if clock:
        hour, minutes = int(clock[1]), int(clock[2] or clock[3] or 0)
        if minutes >= 60:
            return "ambiguous", period, None
        if period and period[0] >= 12 and hour < 12:
            hour += 12
        minute = hour * 60 + minutes
    return target, period, minute


def _reference(
    day: Any, period: tuple[int, int] | None, minute: int | None, now: dt.datetime
) -> tuple[str, str, dict]:
    snapshots = [
        (at, values)
        for at, values in _snapshots(day)
        if at.replace(tzinfo=None) <= now.replace(tzinfo=None)
    ]
    if minute is not None:
        eligible = [
            (at, values)
            for at, values in snapshots
            if at.hour * 60 + at.minute <= minute
        ]
    elif period:
        eligible = [
            (at, values) for at, values in snapshots if period[0] <= at.hour < period[1]
        ]
        if not eligible:
            eligible = [(at, values) for at, values in snapshots if at.hour < period[0]]
    else:
        daytime = [(at, values) for at, values in snapshots if 6 <= at.hour < 18]
        eligible = daytime or snapshots
    if eligible:
        at, values = eligible[-1]
        return str(values["outfit"]), at.strftime("%H:%M"), values
    history = getattr(day, "outfit_history", {}) or {}
    if minute is None and period is None:
        outfit, label = reference_outfit(day)
        return outfit, label, {}
    candidates = []
    for key, outfit in history.items():
        window = _PERIODS.get(key)
        if window:
            # A coarse period cannot establish an exact minute within it.
            if minute is not None and window[1] * 60 > minute:
                continue
            if period and not (window[0] < period[1] and window[1] > period[0]):
                continue
            candidates.append((window[0] * 60, str(outfit), key))
        else:
            try:
                at = dt.datetime.fromisoformat(key)
            except (ValueError, TypeError):
                continue
            at_minute = at.hour * 60 + at.minute
            if at.date().isoformat() != day.date or at.replace(
                tzinfo=None
            ) > now.replace(tzinfo=None):
                continue
            if minute is not None and at_minute > minute:
                continue
            if period and not period[0] <= at.hour < period[1]:
                continue
            candidates.append((at_minute, str(outfit), at.strftime("%H:%M")))
    if candidates:
        _, outfit, label = sorted(candidates)[-1]
        return outfit, label, {}
    return "", "", {}


async def historical_appearance_context(
    archive: Any, text: str, now: dt.datetime, current_day: Any = None
) -> str:
    target, period, minute = appearance_query(text, now)
    if target == "ambiguous":
        return ""
    if target == "last":
        getter = getattr(archive, "get_recent_appearance_days", None)
        if not callable(getter):
            return ""
        current = current_appearance_values(current_day)["outfit"]
        days = await getter(now.date().isoformat(), limit=30)
        candidates = []
        for day in days:
            for at, values in reversed(_snapshots(day)):
                if at.replace(tzinfo=None) <= now.replace(tzinfo=None):
                    candidates.append(
                        (day.date, str(values["outfit"]), at.strftime("%H:%M"), values)
                    )
            for key, outfit in sorted(
                (day.outfit_history or {}).items(),
                key=lambda kv: _PERIODS.get(kv[0], (-1,))[0],
                reverse=True,
            ):
                if key in _PERIODS and (
                    day.date < now.date().isoformat() or _PERIODS[key][1] <= now.hour
                ):
                    candidates.append((day.date, str(outfit), key, {}))
        chosen = next(
            (item for item in candidates if item[1] and item[1] != current), None
        )
        if not chosen:
            return ""
        date, outfit, label, values = chosen
    else:
        day = await archive.get_day(target)
        if day is None:
            return ""
        outfit, label, values = _reference(day, period, minute, now)
        date = day.date
    if not outfit:
        return ""
    lines = [f"历史回现穿搭（{date} {label}生活记录）：{outfit}"]
    for field, name in (
        ("hair_style", "发型名称"),
        ("hair", "发型细节"),
        ("makeup", "妆容"),
        ("nails", "美甲"),
    ):
        if values.get(field):
            lines.append(f"同一时刻{name}：{values[field]}")
    lines.append(
        "仅复现上述有记录的外观；未记录的妆发细节不视为历史事实。本次回现不改变当前穿搭。"
    )
    return "\n".join(lines)
