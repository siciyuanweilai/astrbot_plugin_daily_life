"""依据已记录的事实，按日期查询外观。"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .appearance import current_appearance_values, reference_outfit
from .wardrobe import format_outfit_components, normalize_outfit_components

_PREFIX = "appearance_snapshot:"
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


def record_appearance_snapshot(day: Any, observed_at: dt.datetime) -> None:
    """保存每次实际外观变化，包括同一时段内的变化。"""
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
    target: str, now: dt.datetime, *, period: str = "", time: str = ""
) -> tuple[str, tuple[int, int] | None, int | None]:
    """校验模型明确提供的日期、时段和具体时间。"""
    target = str(target or "").strip()
    if target != "last":
        try:
            date = dt.date.fromisoformat(target)
        except ValueError:
            return "ambiguous", None, None
        if date > now.date():
            return "ambiguous", None, None
    windows = {
        **_PERIODS,
        "daytime": (6, 18),
        "evening_to_night": (18, 24),
    }
    period_value = str(period or "").strip().lower()
    if period_value and period_value not in windows:
        return "ambiguous", None, None
    window = windows.get(period_value)
    minute = None
    if time:
        try:
            clock = dt.time.fromisoformat(str(time))
        except ValueError:
            return "ambiguous", window, None
        minute = clock.hour * 60 + clock.minute
    return target, window, minute


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
            # 粗略时段不能确定其中的具体分钟。
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
    archive: Any, target: str, now: dt.datetime, current_day: Any = None,
    *, period: str = "", time: str = ""
) -> str:
    target, period, minute = appearance_query(target, now, period=period, time=time)
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
