from __future__ import annotations

import datetime
import json
import re
from typing import Any

from ..models import DayRecord, TIMELINE_TERMINAL_STATES
from .tools import (
    parse_life_datetime,
    reconcile_timeline_execution,
    timeline_deferred_until,
    timeline_item_datetime,
)

REST_DELAY_MAX_MINUTES = 120
_REST_ACTIVITY = re.compile(r"睡觉|睡眠|入睡|睡前|准备睡|上床|午睡|补觉|休息|熄灯|关灯")
_DELAY_REQUEST = re.compile(
    r"晚(?:点|一点|些|一会儿?)(?:再)?(?:睡|休息)|"
    r"(?:推迟|延后)(?:睡|休息)|(?:先别|先不|别急着|不要现在)(?:睡|休息)|"
    r"(?:睡|休息)(?:得)?这么早|(?:不要|别|不想)这么早(?:睡|休息)|"
    r"再(?:陪我)?(?:聊|玩|待|坐).{0,6}(?:会儿?|分钟|小时|一下)"
)
_CHINESE_DIGITS = dict(zip("零一二两三四五六七八九", (0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))


def is_rest_activity(item: Any) -> bool:
    activity = str(getattr(item, "activity", "") or "")
    # 困倦状态、回屋、晨间洗漱或躺着看电影，本身都不是入睡安排。
    return bool(_REST_ACTIVITY.search(activity)) and not bool(
        re.search(r"休息日|睡醒|起床|不睡|不想睡", activity)
    )


def _number(text: str) -> int:
    if text.isdigit():
        return int(text)
    if "十" in text:
        tens, _, ones = text.partition("十")
        return _CHINESE_DIGITS.get(tens, 1) * 10 + _CHINESE_DIGITS.get(ones, 0)
    return _CHINESE_DIGITS.get(text, 1)


def parse_rest_delay_request(text: Any) -> int | None:
    """只解析明确的当下请求；自述、转述、否定和未来计划不改角色作息。"""
    value = str(text or "").strip()
    if not value or len(value) > 300:
        return None
    if re.search(
        r"[“”「」『』\"‘’]|(?:他说|她说|说过|昨晚|昨天|明天|以后|如果|假如)", value
    ):
        return None
    if re.search(
        r"(?:不要|别|不用)(?:再)?晚(?:点|一点|些)|(?:别|不想|不要)再聊", value
    ):
        return None
    if "不能" in value and not re.search(r"不能.*(?:吗|嘛|么|？|\?)", value):
        return None
    if re.match(
        r"我(?:今晚|今天|等会儿?|想|打算|准备|要|先|也|就|再|会|可能|不想|自己|晚|不睡)",
        value,
    ):
        return None
    if not _DELAY_REQUEST.search(value):
        return None
    duration = re.search(r"([0-9一二两三四五六七八九十]+)(?:个)?(小时|分钟|分)", value)
    if duration:
        minutes = _number(duration[1]) * (60 if duration[2] == "小时" else 1)
    elif "半小时" in value or "半个小时" in value:
        minutes = 30
    else:
        minutes = 30 if re.search(r"会儿?|一下", value) else 60
    return max(1, min(REST_DELAY_MAX_MINUTES, minutes))


def rest_timing_request(text: Any) -> int | None:
    value = str(text or "").strip()
    minutes = parse_rest_delay_request(value)
    if minutes is not None:
        return minutes
    if re.fullmatch(
        r"(?:好[吧的]?|那|嗯|行|你|我们|咱们|还是|也|都|，|,|\s)*"
        r"(?:现在去睡吧|现在睡吧|去睡吧|早点睡吧?|早点休息吧?|赶紧睡吧?|"
        r"快去睡吧?|现在休息吧?|不聊了|不熬了)[，,。.!！~～\s]*",
        value,
    ):
        return 0
    return None


def _rest_targets(data: DayRecord, now: datetime.datetime) -> list[Any]:
    timed = sorted(
        (time, index, item)
        for index, item in enumerate(data.timeline)
        if (time := timeline_item_datetime(item, data.date)) is not None
    )
    due = [index for index, (time, _, _) in enumerate(timed) if time <= now]
    pos = due[-1] if due else 0
    if not timed:
        return []
    if not is_rest_activity(timed[pos][2]):
        pos += 1 if due else 0
    if pos >= len(timed):
        return []
    time, _, item = timed[pos]
    if not is_rest_activity(item) or item.execution_state in TIMELINE_TERMINAL_STATES:
        return []
    if time > now + datetime.timedelta(minutes=60) or time < now - datetime.timedelta(
        hours=6
    ):
        return []
    # 同一段休息准备/入睡节点共同顺延，避免前一项仍被说成正在睡。
    start = pos
    while start and is_rest_activity(timed[start - 1][2]):
        start -= 1
    targets = []
    for _, _, candidate in timed[start:]:
        if not is_rest_activity(candidate):
            break
        if candidate.execution_state not in TIMELINE_TERMINAL_STATES:
            targets.append(candidate)
    return targets


def activate_rest_delay(
    data: DayRecord, text: Any, now: datetime.datetime, *, event_key: str = ""
) -> bool:
    minutes = rest_timing_request(text)
    if minutes is None or not data.timeline:
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
        targets = _rest_targets(data, now)
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
    meta["rest_delay_evidence"] = str(text or "").strip()[:240]
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
