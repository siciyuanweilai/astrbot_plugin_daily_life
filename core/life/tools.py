import datetime
import json
import random
from dataclasses import dataclass
from typing import Any

import chinese_calendar

from ..clock import TIMEZONE
from ..clock import now as life_now
from ..config.vocab import TIME_PERIOD_CN, WEEKDAY_NAMES
from ..models import TIMELINE_EXECUTION_STATES


# ==================== 节假日及调休感知 ====================
def get_holiday_info(date: datetime.date) -> str:
    """获取中国节假日及调休信息"""
    if not chinese_calendar:
        return ""

    try:
        on_holiday, holiday_name = chinese_calendar.get_holiday_detail(date)
        is_workday = chinese_calendar.is_workday(date)

        if on_holiday:
            return (
                f"今天是法定节假日 {holiday_name}"
                if holiday_name
                else "今天是周末休息日"
            )
        if is_workday:
            if date.weekday() >= 5:
                return "今天是调休工作日，原本是周末但需要苦逼上班！"
            return "今天是普通工作日"
    except Exception:
        return ""
    return ""


def get_time_period_cn(period: str) -> str:
    """获取时间段的中文名称"""
    return TIME_PERIOD_CN.get(period, "未知时段")


def parse_time_minutes(time_str: str) -> int:
    try:
        h, m = map(int, time_str.split(":"))
        return h * 60 + m
    except (AttributeError, TypeError, ValueError):
        return 0


def coerce_date(value: Any) -> datetime.date | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_life_datetime(value: Any) -> datetime.datetime | None:
    """解析生活记录中的本地时间，带时区值统一转换为生活时区。"""
    if isinstance(value, datetime.datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(TIMEZONE).replace(tzinfo=None)
    return parsed


def timeline_deferred_until(
    item: Any, timeline_date: Any, meta: dict | None = None
) -> datetime.datetime | None:
    """读取匹配具体节点的临时顺延时间，日程重排后不会套用到别的节点。"""
    values = meta or {}
    if values.get("rest_delay_date") != str(timeline_date)[:10]:
        return None
    if _timeline_field(item, "execution_state", "planned") in {
        "completed", "expired", "skipped", "cancelled"
    }:
        return None
    try:
        targets = json.loads(values.get("rest_delay_targets") or "[]")
    except (TypeError, ValueError):
        return None
    if not isinstance(targets, list):
        return None
    identity = [_timeline_field(item, "time"), _timeline_field(item, "activity")]
    if identity not in targets:
        return None
    until = parse_life_datetime(values.get("rest_delay_until"))
    planned = parse_life_datetime(f"{str(timeline_date)[:10]} {_timeline_field(item, 'time')}")
    return until if until and planned and until > planned else None


def timeline_item_datetime(
    item: Any, timeline_date: Any, *, meta: dict | None = None
) -> datetime.datetime | None:
    date = coerce_date(timeline_date)
    if date is None:
        return None
    try:
        hour, minute = map(int, _timeline_field(item, "time", "").split(":", 1))
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    planned = datetime.datetime.combine(date, datetime.time(hour, minute))
    deferred = timeline_deferred_until(item, timeline_date, meta)
    return max(planned, deferred) if deferred else planned


def parse_schedule_time(
    schedule_time: str | None, default: str = "07:00"
) -> tuple[int, int]:
    try:
        h, m = map(int, str(schedule_time or default).split(":", 1))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    dh, dm = map(int, default.split(":", 1))
    return dh, dm


def resolve_business_now(
    schedule_time: str | None, now: datetime.datetime | None = None
) -> datetime.datetime:
    """返回业务日期锚点；早于日程刷新时间的时刻归属到昨天。"""
    now = now or life_now()
    h, m = parse_schedule_time(schedule_time)
    boundary = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < boundary:
        return now - datetime.timedelta(days=1)
    return now


def coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        value = value.values()
    try:
        iterator = iter(value)
    except TypeError:
        iterator = None
    if iterator is not None:
        result = []
        for item in iterator:
            result.extend(coerce_text_list(item))
        return result
    text = str(value).strip()
    return [text] if text else []


def format_text_list(
    value: Any, default: str = "", max_items: int | None = None
) -> str:
    seen = set()
    items = []
    for text in coerce_text_list(value):
        if text in seen:
            continue
        seen.add(text)
        items.append(text)
        if max_items is not None and len(items) >= max_items:
            break
    return ", ".join(items) if items else default


def _daily_plan_keys(date: datetime.date | datetime.datetime) -> list[str]:
    if isinstance(date, datetime.datetime):
        date = date.date()
    return [
        date.strftime("%Y-%m-%d"),
        date.strftime("%m-%d"),
        WEEKDAY_NAMES[date.weekday()],
        "weekend" if date.weekday() >= 5 else "weekday",
    ]


def _value_field(value: Any, key: str, default: Any = None) -> Any:
    if hasattr(value, key):
        return getattr(value, key)
    if isinstance(value, dict):
        return value.get(key, default)
    return default


def resolve_daily_hint(
    plan: Any, date: datetime.date | datetime.datetime, default: str = "按周主题安排"
) -> str:
    hints = _value_field(plan, "daily_hints", {})
    if isinstance(hints, dict):
        for key in _daily_plan_keys(date):
            text = format_text_list(hints.get(key), default="")
            if text:
                return text
        return default
    return format_text_list(hints, default=default)


def resolve_daily_suggested(
    plan: Any, date: datetime.date | datetime.datetime, default: str = "无"
) -> str:
    activities = _value_field(plan, "suggested_activities", {})
    if isinstance(activities, dict):
        for key in _daily_plan_keys(date):
            text = format_text_list(activities.get(key), default="")
            if text:
                return text
        return format_text_list(activities.values(), default=default, max_items=3)
    return format_text_list(activities, default=default)


def get_time_period(current_time=None) -> str:
    if current_time is None:
        current_time = life_now()
    now_minutes = current_time.hour * 60 + current_time.minute
    if now_minutes < 6 * 60:
        return "dawn"
    if now_minutes < 9 * 60:
        return "morning"
    if now_minutes < 12 * 60:
        return "forenoon"
    if now_minutes < 14 * 60:
        return "noon"
    if now_minutes < 16 * 60:
        return "afternoon"
    if now_minutes < 19 * 60:
        return "evening"
    if now_minutes < 22 * 60:
        return "night"
    return "late_night"


@dataclass(frozen=True, slots=True)
class LifeTimeContext:
    """一次生活判断中共用的本地时间快照。"""

    now: datetime.datetime
    period: str
    period_cn: str
    minute_of_day: int
    business_now: datetime.datetime
    business_date: datetime.date
    extended_night: bool
    schedule_time: str = ""

    @property
    def now_text(self) -> str:
        return self.now.strftime("%Y-%m-%d %H:%M")

    @property
    def business_date_text(self) -> str:
        return self.business_date.strftime("%Y-%m-%d")

    def prompt_text(self) -> str:
        relation = "延续前一生活日" if self.extended_night else "属于当前生活日"
        return (
            f"实际时间={self.now_text}；时段={self.period_cn}；"
            f"生活日={self.business_date_text}（{relation}）；"
            "时间轴进度按实际时刻和结构化执行状态判断，时间经过不等于动作已完成"
        )


def build_time_context(
    current_time: datetime.datetime | None = None,
    schedule_time: str | None = None,
) -> LifeTimeContext:
    """构造单次判断可复用的时间上下文，避免模块各自重新解释时间。"""

    current = current_time or life_now()
    period = get_time_period(current)
    business = (
        resolve_business_now(schedule_time, current)
        if str(schedule_time or "").strip()
        else current
    )
    return LifeTimeContext(
        now=current,
        period=period,
        period_cn=get_time_period_cn(period),
        minute_of_day=current.hour * 60 + current.minute,
        business_now=business,
        business_date=business.date(),
        extended_night=business.date() < current.date(),
        schedule_time=str(schedule_time or "").strip(),
    )


def extract_json_from_text(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    start = text.find("{")
    if start == -1:
        return None
    level, in_str, esc = 0, False, False
    for i, c in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                level += 1
            elif c == "}":
                level -= 1
                if level == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def extract_pure_json_object(text):
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw.startswith("{") or not raw.endswith("}"):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _first_integer(text: str) -> int | None:
    sign = 1
    digits = []
    reading = False
    for char in str(text or ""):
        if not reading and char == "-":
            sign = -1
            reading = True
            continue
        if char.isdigit():
            digits.append(char)
            reading = True
            continue
        if digits:
            return sign * int("".join(digits))
        sign = 1
        reading = False
    if digits:
        return sign * int("".join(digits))
    return None


def analyze_weather(weather_data: Any) -> dict:
    result = {
        "raw": "",
        "temp": None,
        "condition": "",
        "is_hot": False,
        "is_warm": False,
        "is_cool": False,
        "is_cold": False,
        "is_rainy": False,
        "is_sunny": False,
        "is_cloudy": False,
        "is_foggy": False,
        "outfit_hint": "",
        "activity_hint": "",
        "temp_desc": "",
    }

    if isinstance(weather_data, str):
        result["raw"] = weather_data
        temp = _first_integer(weather_data)
        if temp is not None:
            result["temp"] = temp
        return result

    if isinstance(weather_data, dict) and weather_data.get("ok") is False:
        result["raw"] = str(weather_data.get("message") or "天气查询失败").strip()
        return result

    if isinstance(weather_data, dict) and "data" in weather_data:
        data = weather_data["data"]
        w = data.get("weather", {})
        indices = data.get("life_indices", [])
        aqi = data.get("air_quality", {})

        temp = w.get("temperature")
        cond = w.get("condition", "")
        result["temp"] = temp
        result["condition"] = cond
        result["raw"] = (
            f"{data.get('location', {}).get('city', '')} {cond} {temp}°C (AQI: {aqi.get('aqi', '?')} {aqi.get('quality', '')})"
        )

        if temp is not None:
            if temp >= 30:
                result["is_hot"] = True
                result["temp_desc"] = "炎热"
            elif temp >= 22:
                result["is_warm"] = True
                result["temp_desc"] = "温暖"
            elif temp >= 15:
                result["is_cool"] = True
                result["temp_desc"] = "凉爽"
            elif temp >= 5:
                result["is_cold"] = True
                result["temp_desc"] = "寒冷"
            else:
                result["is_cold"] = True
                result["temp_desc"] = "严寒"

        cond_str = str(cond)
        result["is_rainy"] = any(x in cond_str for x in ["雨", "雷", "雪"])
        result["is_sunny"] = "晴" in cond_str
        result["is_cloudy"] = any(x in cond_str for x in ["阴", "云"])
        result["is_foggy"] = any(x in cond_str for x in ["雾", "霾"])

        idx_map = {
            item["key"]: item
            for item in indices
            if isinstance(item, dict) and item.get("key")
        }
        outfit_hints, activity_hints = [], []

        if "clothes" in idx_map:
            outfit_hints.append(
                f"穿衣建议：{idx_map['clothes'].get('description', '')}"
            )
        if "makeup" in idx_map:
            outfit_hints.append(f"美妆：{idx_map['makeup'].get('description', '')}")
        uv = (
            idx_map.get("sunscreen")
            or idx_map.get("ultraviolet")
            or idx_map.get("sunglasses")
        )
        if uv and "弱" not in str(uv.get("level", "")):
            outfit_hints.append(f"防晒：{uv.get('description', '')}")
        if "sports" in idx_map:
            activity_hints.append(f"运动：{idx_map['sports'].get('description', '')}")
        if "umbrella" in idx_map:
            if "不带" not in idx_map["umbrella"].get("level", ""):
                activity_hints.append(
                    f"携带雨具：{idx_map['umbrella'].get('description', '')}"
                )
        chill = idx_map.get("chill")
        cold = idx_map.get("cold")
        if chill and "冷" in str(chill.get("level", "")):
            activity_hints.append(f"注意：{chill.get('description', '')}")
        elif cold and "易发" in str(cold.get("level", "")):
            activity_hints.append(f"健康：{cold.get('description', '')}")

        result["outfit_hint"] = "；".join(outfit_hints)
        result["activity_hint"] = "；".join(activity_hints)
    return result


def get_weather_outfit_constraint(weather_info: dict, enabled: bool = True) -> str:
    if not enabled:
        return ""
    constraints = []
    if weather_info.get("is_hot"):
        constraints.append(
            "高温建议优先考虑轻薄透气材质（雪纺/棉麻/真丝），颜色可多样化"
        )
    elif weather_info.get("is_cold"):
        constraints.append(
            "低温建议优先考虑保暖衣物（毛衣/厚外套/羽绒服），颜色可多样化，可配围巾手套"
        )
    elif weather_info.get("is_cool"):
        constraints.append("凉爽天气穿薄外套/卫衣/长袖，颜色多样化，可内搭短袖备用")
    if weather_info.get("is_rainy"):
        constraints.append("雨天建议考虑耐脏面料、防水鞋和雨具")
    if weather_info.get("is_sunny") and weather_info.get("is_hot"):
        constraints.append("晴热天气注意防晒，可戴帽子墨镜")
    return "；".join(constraints) if constraints else ""


def get_weather_activity_constraint(weather_info: dict, enabled: bool = True) -> str:
    if not enabled:
        return ""
    constraints = []
    if weather_info.get("is_hot") and weather_info.get("temp", 0) >= 35:
        constraints.append("高温预警，避免户外活动")
    elif weather_info.get("is_hot"):
        constraints.append("天气炎热，户外活动选择清晨或傍晚")
    if weather_info.get("is_rainy"):
        if "大雨" in weather_info.get("condition", "") or "暴雨" in weather_info.get(
            "condition", ""
        ):
            constraints.append("暴雨天气，取消户外活动，待在室内")
        else:
            constraints.append("雨天优先室内活动（商场、咖啡厅、电影院）")
    if weather_info.get("is_foggy"):
        constraints.append("雾霾天气，减少户外，优先室内活动")
    return "；".join(constraints) if constraints else ""


def get_matching_hairstyle(
    style_name: str,
    style_map: dict[str, list[str]],
    night_styles: list[str],
    is_night=False,
):
    """根据穿搭风格获取匹配的发型"""
    if is_night:
        return random.choice(night_styles) if night_styles else "自然披发"

    def normalize(value: str) -> str:
        text = "".join(str(value or "").strip().lower().split())
        return text.replace("（", "(").replace("）", ")")

    def pick(items: list[str]) -> str:
        return random.choice(items) if items else ""

    raw_name = str(style_name or "").strip()
    if not raw_name:
        return "自然披发"

    exact = style_map.get(raw_name) or []
    if exact:
        return pick(exact)

    normalized_name = normalize(raw_name)
    if not normalized_name:
        return "自然披发"

    normalized_items: list[tuple[str, list[str]]] = []
    for key, hairstyles in style_map.items():
        if not hairstyles:
            continue
        normalized_key = normalize(key)
        if not normalized_key:
            continue
        normalized_items.append((normalized_key, hairstyles))

    for normalized_key, hairstyles in normalized_items:
        if normalized_key == normalized_name:
            return pick(hairstyles)

    contains_matches: list[list[str]] = []
    for normalized_key, hairstyles in normalized_items:
        if normalized_name in normalized_key or normalized_key in normalized_name:
            contains_matches.append(hairstyles)
    if contains_matches:
        return pick(random.choice(contains_matches))

    best_overlap = 0
    best_matches: list[list[str]] = []
    name_parts = [
        part
        for part in normalized_name.replace("·", " ").replace("-", " ").split()
        if part
    ]
    if not name_parts:
        name_parts = [normalized_name]
    for normalized_key, hairstyles in normalized_items:
        key_parts = [
            part
            for part in normalized_key.replace("·", " ").replace("-", " ").split()
            if part
        ]
        if not key_parts:
            key_parts = [normalized_key]
        overlap = sum(
            1
            for part in name_parts
            if any(part in key_part or key_part in part for key_part in key_parts)
        )
        if overlap <= 0:
            continue
        if overlap > best_overlap:
            best_overlap = overlap
            best_matches = [hairstyles]
        elif overlap == best_overlap:
            best_matches.append(hairstyles)
    if best_matches:
        return pick(random.choice(best_matches))

    return "自然披发"


def get_week_id(date=None):
    if date is None:
        date = life_now()
    return date.strftime("%Y-W%W")


def get_monday_of_week(date=None):
    if date is None:
        date = life_now()
    return (date - datetime.timedelta(days=date.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


# ==================== 时间轴处理 ====================
def _timeline_field(item: Any, key: str, default: str = "") -> str:
    if hasattr(item, key):
        return str(getattr(item, key) or default)
    if isinstance(item, dict):
        return str(item.get(key) or default)
    return default


def _timeline_duration_minutes(item: Any) -> int:
    try:
        return max(0, int(float(_timeline_field(item, "duration_minutes", "0"))))
    except (TypeError, ValueError):
        return 0


def _timeline_window_boundary(
    value: Any,
    timeline_date: datetime.date,
    *,
    after: datetime.datetime,
) -> datetime.datetime | None:
    """Resolve a daily window boundary, including an after-midnight endpoint."""
    parsed = parse_life_datetime(value)
    if parsed is not None:
        return parsed
    try:
        hour, minute = map(int, str(value or "").strip().split(":", 1))
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    boundary = datetime.datetime.combine(timeline_date, datetime.time(hour, minute))
    if boundary <= after:
        boundary += datetime.timedelta(days=1)
    return boundary


def _timeline_item_window_end(
    timed_items: list[tuple[datetime.datetime, Any]],
    index: int,
    *,
    timeline_end: Any = None,
    timeline_date: datetime.date,
) -> datetime.datetime | None:
    item_time, item = timed_items[index]
    duration_minutes = _timeline_duration_minutes(item)
    if duration_minutes:
        return item_time + datetime.timedelta(minutes=duration_minutes)
    if index + 1 < len(timed_items):
        return timed_items[index + 1][0]
    return _timeline_window_boundary(
        timeline_end,
        timeline_date,
        after=item_time,
    )


def get_current_timeline_status(
    timeline: list,
    current_time: datetime.datetime = None,
    timeline_date: Any = None,
    *,
    meta: dict | None = None,
) -> tuple:
    if not current_time:
        current_time = life_now()
    if not timeline or not isinstance(timeline, list):
        return None, None

    current_item = None
    next_item = None
    timeline_date_value = coerce_date(timeline_date)

    timed_items = []
    for item in timeline:
        if timeline_date_value:
            item_time = timeline_item_datetime(item, timeline_date_value, meta=meta)
            if item_time:
                timed_items.append((item_time, item))
            continue
        try:
            h, m = map(int, _timeline_field(item, "time", "00:00").split(":"))
            timed_items.append((h * 60 + m, item))
        except (TypeError, ValueError):
            continue

    current_key = (
        current_time
        if timeline_date_value
        else current_time.hour * 60 + current_time.minute
    )
    for item_mins, item in sorted(timed_items, key=lambda x: x[0]):
        if item_mins <= current_key:
            current_item = item
        else:
            next_item = item
            break

    if current_item is not None:
        execution_state = _timeline_field(
            current_item, "execution_state", "planned"
        ).lower()
        if execution_state in {
            "elapsed",
            "completed",
            "expired",
            "skipped",
            "cancelled",
        }:
            current_item = None

    return current_item, next_item


def reconcile_timeline_execution(
    timeline: list,
    current_time: datetime.datetime,
    timeline_date: Any,
    *,
    evidence: str = "时间轴时钟",
    timeline_end: Any = None,
    meta: dict | None = None,
) -> bool:
    """推进时间轴观察态；时钟经过只代表计划已过，不代表动作已完成。"""
    date = coerce_date(timeline_date)
    if not timeline or date is None:
        return False

    now_key = current_time
    changed = False
    timed_items = []
    for item in timeline:
        item_time = timeline_item_datetime(item, date, meta=meta)
        if item_time is not None:
            timed_items.append((item_time, item))
    timed_items.sort(key=lambda entry: entry[0])
    if not timed_items:
        return False

    updated_at = current_time.strftime("%Y-%m-%d %H:%M")
    for index, (item_time, item) in enumerate(timed_items):
        previous = _timeline_field(item, "execution_state", "planned").lower()
        previous = previous if previous in TIMELINE_EXECUTION_STATES else "planned"
        if previous in {"skipped", "cancelled", "expired"}:
            continue
        window_end = _timeline_item_window_end(
            timed_items,
            index,
            timeline_end=timeline_end,
            timeline_date=date,
        )
        deferred_until = timeline_deferred_until(item, date, meta)
        if deferred_until and index == len(timed_items) - 1 and not _timeline_duration_minutes(item):
            original_time = timeline_item_datetime(item, date)
            original_end = _timeline_window_boundary(timeline_end, date, after=original_time)
            if original_end is not None:
                window_end = original_end + (item_time - original_time)
        if deferred_until is not None and now_key < deferred_until:
            target = "planned"
            reason = "休息已顺延：用户希望晚点再休息"
        elif item_time.date() < current_time.date():
            target = "elapsed"
            reason = "计划日期已过，尚未收到执行证据"
        elif item_time > now_key:
            target = "planned"
            reason = "等待计划开始"
        elif window_end is not None and now_key >= window_end:
            target = "elapsed"
            reason = "计划时段已过，尚未收到执行证据"
        else:
            target = "active"
            reason = "已到达计划开始时间"
        if previous in {"completed", "expired"} and target != previous:
            continue
        was_deferred = _timeline_field(item, "execution_reason", "").startswith("休息已顺延")
        if previous == target and not (was_deferred and not deferred_until):
            continue
        setattr(item, "execution_state", target)
        setattr(item, "execution_reason", reason)
        setattr(item, "execution_evidence", evidence)
        setattr(item, "execution_updated_at", updated_at)
        changed = True
    return changed


def format_timeline_to_text(
    timeline: list, *, timeline_date: Any = None, meta: dict | None = None
) -> str:
    if not timeline:
        return "暂无详细日程"
    lines = []
    previous_place = ""
    for item in timeline:
        time_str = _timeline_field(item, "time")
        act = _timeline_field(item, "activity")
        status = _timeline_field(item, "status")
        status_str = f" [{status}]" if status else ""
        execution = _timeline_field(item, "execution_state", "planned")
        execution_str = f" [执行:{execution}]" if execution else ""
        deferred = timeline_deferred_until(item, timeline_date, meta)
        if deferred:
            time_str += f" → 顺延 {deferred:%m-%d %H:%M}"
        lines.append(f"{time_str} - {act}{status_str}{execution_str}")
        travel_text = format_timeline_travel(item, previous_place=previous_place)
        if travel_text:
            lines.append(f"  出行：{travel_text}")
        place = _timeline_field(item, "place")
        if place:
            previous_place = place
    return "\n".join(lines)


def format_timeline_travel(
    item: Any,
    *,
    previous_place: str = "",
    include_provider: bool = True,
) -> str:
    """格式化时间轴节点中已经核验的交通事实。

    Args:
        item: 时间轴节点对象或字典。
        previous_place: 节点未保存出发地时使用的上一地点。
        include_provider: 是否附带地图或估算来源。

    Returns:
        可供展示或模型上下文使用的交通摘要；没有交通事实时返回空字符串。
    """

    mode = _timeline_field(item, "travel_mode")
    detail = _timeline_field(item, "travel_detail")
    minutes = _non_negative_timeline_number(item, "travel_minutes")
    distance = _non_negative_timeline_number(item, "travel_distance_meters")
    if not mode and minutes <= 0 and distance <= 0:
        return ""

    origin = _timeline_field(item, "travel_origin") or previous_place
    destination = _timeline_field(item, "place")
    parts = []
    if origin and destination and origin != destination:
        parts.append(f"从{origin}前往{destination}")

    mode_label = (
        detail
        if mode == "transit" and detail
        else {
            "walking": "步行",
            "cycling": "骑行",
            "driving": "驾车",
            "transit": "公共交通",
        }.get(mode, mode or "出行")
    )
    parts.append(f"{mode_label}约 {int(minutes)} 分钟" if minutes > 0 else mode_label)
    if distance > 0:
        parts.append(_format_travel_distance(distance))

    if include_provider:
        provider = _timeline_field(item, "travel_provider")
        provider_label = {
            "amap": "高德地图",
            "tencent": "腾讯地图",
            "baidu": "百度地图",
            "coordinate_estimate": "坐标估算",
            "default_estimate": "默认估算",
        }.get(provider, "")
        if provider_label:
            parts.append(provider_label)
    return " · ".join(parts)


def _non_negative_timeline_number(item: Any, field: str) -> float:
    try:
        return max(0.0, float(_timeline_field(item, field, 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def _format_travel_distance(distance_meters: float) -> str:
    if distance_meters < 1000:
        return f"{int(round(distance_meters))} 米"
    kilometers = distance_meters / 1000
    digits = 0 if kilometers >= 10 else 1
    return f"{kilometers:.{digits}f} 公里"
