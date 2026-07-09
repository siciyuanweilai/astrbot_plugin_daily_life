import datetime
from typing import Any

from ..labels import bot_watch_state_label, interrupt_level_label, sleep_depth_label, source_label
from ..clock import now as life_now


STATE_DEFAULTS = {
    "energy": 60,
    "mood": "平稳，愿意按今天的节奏慢慢来",
    "mood_score": 60,
    "busyness": 45,
    "social": 50,
    "stress": 35,
    "focus": 55,
    "sleepiness": 35,
    "outgoing": 45,
    "emotional_stability": 65,
    "interaction_capacity": 55,
    "boredom": 30,
    "fishing": 20,
    "attention_openness": 55,
    "watch_state": "peek",
    "interrupt_level": "ordinary",
    "interrupt_reason": "普通状态下允许自然留意聊天",
    "sleep": {
        "quality": 65,
        "depth": "awake",
        "summary": "睡眠还算正常，精神恢复到中等水平",
    },
    "summary": "今天状态平稳，适合按原计划自然推进。",
}

STATE_SCORE_FIELDS = (
    "energy",
    "mood_score",
    "busyness",
    "social",
    "stress",
    "focus",
    "sleepiness",
    "outgoing",
    "emotional_stability",
    "interaction_capacity",
    "boredom",
    "fishing",
    "attention_openness",
)

WATCH_STATES = {"blackout", "peek", "skim_window", "active_watch", "engaged"}
INTERRUPT_LEVELS = {"ordinary", "medium", "high"}
INTERRUPT_RANK = {"ordinary": 1, "medium": 2, "high": 3}
SLEEP_DEPTHS = {"awake", "light_rest", "light_sleep", "deep_sleep"}

PHYSIOLOGICAL_RHYTHM_DEFAULTS = {
    "energy_curve": "全天平稳",
    "body_condition": {
        "label": "无明显不适",
        "intensity": 0,
        "source": "生活状态",
        "expires_at": "",
    },
    "recovery_actions": [],
    "social_battery": 55,
    "attention_state": "自然留意",
    "optional_cycle": {
        "enabled": False,
        "label": "",
        "intensity": 0,
        "source": "",
    },
    "summary": "生理节律平稳，按今日安排自然推进",
}


def _compact_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    return text[:limit]


def _clamp_score(value: Any, default: int) -> int:
    try:
        score = int(float(value))
    except (TypeError, ValueError):
        score = default
    return max(0, min(100, score))


def _choice(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "y", "1", "是"}:
        return True
    if text in {"false", "no", "n", "0", "否"}:
        return False
    return False


def _timestamp(now: datetime.datetime | None = None) -> str:
    now = now or life_now()
    return now.strftime("%Y-%m-%d %H:%M")


def _state_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        data = as_dict()
        return data if isinstance(data, dict) else {}
    return {}


def _list_texts(value: Any, limit: int = 5, item_limit: int = 40) -> list[str]:
    raw_items = value if isinstance(value, list) else ([value] if value else [])
    items: list[str] = []
    for item in raw_items:
        text = _compact_text(item, item_limit)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _raw_rhythm(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _expired_date(value: Any, now: datetime.datetime | None = None) -> bool:
    text = _compact_text(value, 32)
    if not text:
        return False
    today = (now or life_now()).date()
    for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(text, pattern).date() < today
        except ValueError:
            continue
    return False


def _normalize_optional_cycle(raw_cycle: dict) -> dict:
    enabled = _bool_flag(raw_cycle.get("enabled"))
    label = _compact_text(raw_cycle.get("label"), 40)
    source = _compact_text(raw_cycle.get("source"), 40)
    intensity = _clamp_score(raw_cycle.get("intensity"), 0)
    if not (enabled and (label or source or intensity > 0)):
        return {"enabled": False, "label": "", "intensity": 0, "source": ""}
    return {
        "enabled": True,
        "label": label,
        "intensity": intensity,
        "source": source,
    }


def normalize_physiological_rhythm(
    raw_value: Any,
    *,
    previous: Any = None,
    now: datetime.datetime | None = None,
    default_social_battery: int = 55,
) -> dict:
    raw = _raw_rhythm(raw_value)
    prev = _raw_rhythm(previous)
    defaults = PHYSIOLOGICAL_RHYTHM_DEFAULTS
    raw_condition = _raw_rhythm(raw.get("body_condition"))
    prev_condition = _raw_rhythm(prev.get("body_condition"))
    if _expired_date(raw_condition.get("expires_at"), now):
        raw_condition = {}
    if _expired_date(prev_condition.get("expires_at"), now):
        prev_condition = {}

    condition_default = defaults["body_condition"]
    condition = {
        "label": _compact_text(
            raw_condition.get("label")
            or prev_condition.get("label")
            or condition_default["label"],
            60,
        ),
        "intensity": _clamp_score(
            raw_condition.get("intensity"),
            _clamp_score(prev_condition.get("intensity"), int(condition_default["intensity"])),
        ),
        "source": _compact_text(
            raw_condition.get("source")
            or prev_condition.get("source")
            or condition_default["source"],
            60,
        ),
        "expires_at": _compact_text(
            raw_condition.get("expires_at") or prev_condition.get("expires_at") or "",
            32,
        ),
    }

    optional_cycle = _normalize_optional_cycle(_raw_rhythm(raw.get("optional_cycle")))

    actions = _list_texts(raw.get("recovery_actions") or prev.get("recovery_actions"), limit=5)
    social_default = _clamp_score(default_social_battery, int(defaults["social_battery"]))
    return {
        "energy_curve": _compact_text(raw.get("energy_curve") or prev.get("energy_curve") or defaults["energy_curve"], 80),
        "body_condition": condition,
        "recovery_actions": actions,
        "social_battery": _clamp_score(raw.get("social_battery"), _clamp_score(prev.get("social_battery"), social_default)),
        "attention_state": _compact_text(raw.get("attention_state") or prev.get("attention_state") or defaults["attention_state"], 60),
        "optional_cycle": optional_cycle,
        "summary": _compact_text(raw.get("summary") or prev.get("summary") or defaults["summary"], 120),
    }


def format_physiological_rhythm_prompt(value: Any) -> str:
    rhythm = normalize_physiological_rhythm(value)
    condition = rhythm.get("body_condition") if isinstance(rhythm.get("body_condition"), dict) else {}
    optional_cycle = rhythm.get("optional_cycle") if isinstance(rhythm.get("optional_cycle"), dict) else {}
    parts = [
        f"精力曲线：{rhythm.get('energy_curve')}",
        f"身体状态：{condition.get('label')}({condition.get('intensity')}/100)",
        f"社交电量：{rhythm.get('social_battery')}/100",
        f"注意力：{rhythm.get('attention_state')}",
    ]
    actions = rhythm.get("recovery_actions") if isinstance(rhythm.get("recovery_actions"), list) else []
    if actions:
        parts.append("恢复动作：" + "、".join(str(item) for item in actions if item))
    if optional_cycle.get("enabled"):
        label = optional_cycle.get("label") or "可选周期"
        parts.append(f"可选周期：{label}({optional_cycle.get('intensity')}/100)")
    summary = _compact_text(rhythm.get("summary"), 120)
    if summary:
        parts.append(f"摘要：{summary}")
    return "；".join(part for part in parts if part)


def normalize_state(
    raw_state: Any,
    *,
    now: datetime.datetime | None = None,
    source: str = "daily",
    previous: dict | None = None,
) -> dict:
    raw = _state_dict(raw_state)
    prev = _state_dict(previous)
    prev_sleep = prev.get("sleep") if isinstance(prev.get("sleep"), dict) else {}
    raw_sleep = raw.get("sleep") if isinstance(raw.get("sleep"), dict) else {}
    default_sleep = STATE_DEFAULTS["sleep"]

    sleep_quality = _clamp_score(
        raw_sleep.get("quality"),
        _clamp_score(prev_sleep.get("quality"), int(default_sleep["quality"])),
    )
    sleep_summary = _compact_text(
        raw_sleep.get("summary")
        or prev_sleep.get("summary")
        or str(default_sleep["summary"]),
        120,
    )
    sleep_depth = _choice(
        raw_sleep.get("depth") or prev_sleep.get("depth"),
        SLEEP_DEPTHS,
        str(default_sleep["depth"]),
    )

    scores = {
        key: _clamp_score(raw.get(key), _clamp_score(prev.get(key), STATE_DEFAULTS[key]))
        for key in STATE_SCORE_FIELDS
    }
    rhythm = normalize_physiological_rhythm(
        raw.get("physiological_rhythm"),
        previous=prev.get("physiological_rhythm"),
        now=now,
        default_social_battery=scores["interaction_capacity"],
    )
    watch_state = _choice(
        raw.get("watch_state") or prev.get("watch_state"),
        WATCH_STATES,
        str(STATE_DEFAULTS["watch_state"]),
    )
    interrupt_level = _choice(
        raw.get("interrupt_level") or prev.get("interrupt_level"),
        INTERRUPT_LEVELS,
        str(STATE_DEFAULTS["interrupt_level"]),
    )
    return {
        **scores,
        "mood": _compact_text(raw.get("mood") or prev.get("mood") or STATE_DEFAULTS["mood"], 120),
        "watch_state": watch_state,
        "interrupt_level": interrupt_level,
        "interrupt_reason": _compact_text(
            raw.get("interrupt_reason")
            or prev.get("interrupt_reason")
            or STATE_DEFAULTS["interrupt_reason"],
            120,
        ),
        "sleep": {
            "quality": sleep_quality,
            "depth": sleep_depth,
            "summary": sleep_summary,
        },
        "physiological_rhythm": rhythm,
        "summary": _compact_text(raw.get("summary") or prev.get("summary") or STATE_DEFAULTS["summary"], 160),
        "updated_at": _compact_text(raw.get("updated_at") or _timestamp(now), 32),
        "source": _compact_text(raw.get("source") or source, 32),
    }


def state_is_stale(state: dict | None, now: datetime.datetime | None = None, minutes: int = 30) -> bool:
    state_data = _state_dict(state)
    if not state_data.get("updated_at"):
        return True
    try:
        updated_at = datetime.datetime.strptime(str(state_data["updated_at"]), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return True
    now = now or life_now()
    return (now - updated_at).total_seconds() >= max(1, int(minutes or 30)) * 60


def format_state_prompt(state: dict | None) -> str:
    normalized = normalize_state(state)
    sleep = normalized.get("sleep", {})
    watch_state = bot_watch_state_label(normalized["watch_state"])
    interrupt_level = interrupt_level_label(normalized["interrupt_level"])
    sleep_depth = sleep_depth_label(sleep.get("depth", "awake"))
    rhythm_text = format_physiological_rhythm_prompt(normalized.get("physiological_rhythm"))
    return (
        f"体力 {normalized['energy']}/100；"
        f"心情值 {normalized['mood_score']}/100；"
        f"忙碌度 {normalized['busyness']}/100；"
        f"社交意愿 {normalized['social']}/100；"
        f"压力感 {normalized['stress']}/100；"
        f"专注度 {normalized['focus']}/100；"
        f"困倦度 {normalized['sleepiness']}/100；"
        f"外出意愿 {normalized['outgoing']}/100；"
        f"情绪稳定 {normalized['emotional_stability']}/100；"
        f"互动意愿 {normalized['interaction_capacity']}/100；"
        f"无聊值 {normalized['boredom']}/100；"
        f"摸鱼值 {normalized['fishing']}/100；"
        f"注意力开放度 {normalized['attention_openness']}/100；"
        f"观看状态 {watch_state}；"
        f"消息打断等级 {interrupt_level}；"
        f"睡眠层级 {sleep_depth}；"
        f"睡眠质量 {sleep.get('quality', 0)}/100；"
        f"心情：{normalized['mood']}；"
        f"睡眠：{sleep.get('summary', '')}；"
        f"生理节律：{rhythm_text}；"
        f"整体：{normalized['summary']}"
    )


def format_state_hidden(state: dict | None) -> str:
    if not _state_dict(state):
        return ""
    normalized = normalize_state(state)
    watch_state = bot_watch_state_label(normalized["watch_state"])
    interrupt_level = interrupt_level_label(normalized["interrupt_level"])
    return (
        "[HiddenState]\n"
        f"- 当前身体与情绪底色：{format_state_prompt(normalized)}\n"
        f"- 群聊观看姿态：{watch_state}；无聊值 {normalized['boredom']}/100；"
        f"摸鱼值 {normalized['fishing']}/100；注意力开放度 {normalized['attention_openness']}/100；"
        f"当前打断门槛：{interrupt_level}。"
        f"打断原因：{normalized['interrupt_reason']}\n"
        "- 回复风格约束：把这个状态当作今天的内在惯性。体力低时少主动提出高强度外出；"
        "社交意愿低时回复更慢热、更低负担；忙碌度高时避免显得随时很闲；"
        "摸鱼值高时普通消息只轻扫或不进入主体注意，除非被明确提及、引用、提到我或高相关事件打断。"
    )


def classify_message_interrupt(
    message: str = "",
    *,
    directed: bool = False,
    quoted: bool = False,
    discussing_bot: bool = False,
    familiar_or_relevant: bool = False,
    high_risk: bool = False,
) -> dict[str, str]:
    text = str(message or "").strip()
    level = "ordinary"
    reasons: list[str] = []
    if text:
        reasons.append("有新消息")
    if familiar_or_relevant:
        level = "medium"
        reasons.append("来自熟悉对象或与近期关注相关")
    if directed:
        level = "high"
        reasons.append("明确提及或指向我")
    if quoted:
        level = "high"
        reasons.append("引用/回复链指向当前对话")
    if discussing_bot:
        level = "high"
        reasons.append("正在提到我")
    if high_risk:
        level = "high"
        reasons.append("存在冲突或高风险事件")
    return {"level": level, "reason": "；".join(reasons) or "无明显打断信号"}


def message_can_interrupt(state: dict | None, interrupt: dict[str, str] | str) -> bool:
    normalized = normalize_state(state)
    required = normalized.get("interrupt_level") or "ordinary"
    required_rank = INTERRUPT_RANK.get(str(required), 2)
    if isinstance(interrupt, dict):
        level = interrupt.get("level", "ordinary")
    else:
        level = str(interrupt or "ordinary")
    return INTERRUPT_RANK.get(level, 1) >= required_rank


def format_state_display(state: dict | None) -> str:
    if not _state_dict(state):
        return "🫧 今日状态：暂无记录"
    normalized = normalize_state(state)
    sleep = normalized.get("sleep", {})
    watch_state = bot_watch_state_label(normalized["watch_state"])
    interrupt_level = interrupt_level_label(normalized["interrupt_level"])
    sleep_depth = sleep_depth_label(sleep.get("depth", "awake"))
    rhythm_text = format_physiological_rhythm_prompt(normalized.get("physiological_rhythm"))
    return (
        "🫧 今日状态\n"
        f"体力：{normalized['energy']}/100\n"
        f"心情值：{normalized['mood_score']}/100\n"
        f"心情：{normalized['mood']}\n"
        f"忙碌度：{normalized['busyness']}/100\n"
        f"社交意愿：{normalized['social']}/100\n"
        f"压力感：{normalized['stress']}/100\n"
        f"专注度：{normalized['focus']}/100\n"
        f"困倦度：{normalized['sleepiness']}/100\n"
        f"外出意愿：{normalized['outgoing']}/100\n"
        f"情绪稳定：{normalized['emotional_stability']}/100\n"
        f"互动意愿：{normalized['interaction_capacity']}/100\n"
        f"无聊值：{normalized['boredom']}/100\n"
        f"摸鱼值：{normalized['fishing']}/100\n"
        f"注意力开放度：{normalized['attention_openness']}/100\n"
        f"观看状态：{watch_state}\n"
        f"打断等级：{interrupt_level}（{normalized['interrupt_reason']}）\n"
        f"睡眠层级：{sleep_depth}\n"
        f"睡眠质量：{sleep.get('quality', 0)}/100（{sleep.get('summary', '')}）\n"
        f"生理节律：{rhythm_text}\n"
        f"整体：{normalized['summary']}\n"
        f"更新：{normalized.get('updated_at', '')} · {source_label(normalized.get('source', ''))}"
    )


def state_log_entry(state: dict | None, now: datetime.datetime | None = None) -> str:
    normalized = normalize_state(state, now=now)
    time_text = (now or life_now()).strftime("%H:%M")
    return f"{time_text} {normalized['summary']}"
