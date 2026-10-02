from __future__ import annotations

import datetime
import json
from typing import Any

from ..models import DayRecord, TIMELINE_TERMINAL_STATES
from .tools import (
    parse_life_datetime,
    reconcile_timeline_execution,
    timeline_deferred_until,
    timeline_item_datetime,
)

REST_DELAY_MAX_MINUTES = 120


def is_rest_activity(item: Any) -> bool:
    return str(getattr(item, "activity_kind", "") or "").strip().lower() == "rest"


def _rest_targets(data: DayRecord, now: datetime.datetime, target_times: list[str]) -> list[Any]:
    timed = sorted(
        (time, index, item)
        for index, item in enumerate(data.timeline)
        if (time := timeline_item_datetime(item, data.date)) is not None
    )
    selected = set(target_times)
    return [
        item for time, _, item in timed
        if item.time in selected
        and str(getattr(item, "activity_kind", "") or "").strip().lower() in {"", "rest"}
        and item.execution_state not in TIMELINE_TERMINAL_STATES
        and now - datetime.timedelta(hours=6) <= time <= now + datetime.timedelta(minutes=60)
    ]


def activate_rest_delay(
    data: DayRecord, minutes: int, now: datetime.datetime, *,
    target_times: list[str] | None = None, evidence: str = "", event_key: str = ""
) -> bool:
    if not isinstance(minutes, int) or not 0 <= minutes <= REST_DELAY_MAX_MINUTES or not data.timeline:
        return False
    meta = data.meta
    if event_key and meta.get("rest_delay_last_event") == event_key:
        return False
    until = parse_life_datetime(meta.get("rest_delay_until"))
    if minutes == 0:
        if until is None or until <= now or meta.get("rest_delay_date") != data.date:
            return False
        meta["rest_delay_until"] = now.isoformat(sep=" ", timespec="seconds")
    else:
        targets = _rest_targets(data, now, target_times or [])
        if not targets:
            return False
        identities = json.dumps(
            [[item.time, item.activity] for item in targets], ensure_ascii=False
        )
        same_targets = (
            meta.get("rest_delay_date") == data.date
            and meta.get("rest_delay_targets") == identities
        )
        started = (
            parse_life_datetime(meta.get("rest_delay_started_at"))
            if same_targets
            else None
        )
        started = started or now
        deadline = started + datetime.timedelta(minutes=REST_DELAY_MAX_MINUTES)
        if now >= deadline:
            return False
        # 临近固定的其他安排时不挤占它；明显疲惫时只给短缓冲，保留身体状态。
        state = data.state
        if state and (state.sleepiness >= 80 or state.energy <= 20):
            minutes = min(minutes, 15)
        future_constraints = [
            time
            for item in data.timeline
            if item not in targets
            and item.execution_state not in TIMELINE_TERMINAL_STATES
            and (time := timeline_item_datetime(item, data.date)) is not None
            and time > now
        ]
        if future_constraints:
            deadline = min(deadline, min(future_constraints))
        next_until = min(now + datetime.timedelta(minutes=minutes), deadline)
        if same_targets and until:
            next_until = min(max(until, next_until), deadline)
        if next_until <= now:
            return False
        # 不把尚未临近的后续睡眠节点提前到本次宽限结束。
        if all(
            timeline_item_datetime(item, data.date) >= next_until for item in targets
        ):
            return False
        meta["rest_delay_date"] = data.date
        meta["rest_delay_targets"] = identities
        meta["rest_delay_started_at"] = started.isoformat(sep=" ", timespec="seconds")
        meta["rest_delay_until"] = next_until.isoformat(sep=" ", timespec="seconds")
    meta["rest_delay_evidence"] = str(evidence or "").strip()[:240]
    if event_key:
        meta["rest_delay_last_event"] = event_key
    reconcile_timeline_execution(
        data.timeline,
        now,
        data.date,
        meta=meta,
        evidence=meta["rest_delay_evidence"],
        timeline_end=meta.get("life_window_end"),
    )
    return True


def format_rest_delay_hint(data: DayRecord, now: datetime.datetime) -> str:
    for item in data.timeline:
        until = timeline_deferred_until(item, data.date, data.meta)
        if until and until > now:
            return (
                f"用户希望晚点休息，原定休息暂缓，约 {until:%m-%d %H:%M} 再评估；"
                "当前不能按原钟点声称已经睡下。接续当前话题，困意和体力仍按真实状态判断；"
                "若确实疲惫可自然商量收尾，不要反复催睡；宽限结束也不等于已入睡。"
            )
    return ""
