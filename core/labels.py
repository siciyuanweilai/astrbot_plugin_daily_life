from __future__ import annotations

from .config.vocab import TIME_PERIOD_CN, WEEKDAY_CN, WEEKDAY_NAMES

WEEKDAY_LABELS = dict(zip(WEEKDAY_NAMES, WEEKDAY_CN))

TEMPLATE_LABELS = {
    "random": "随机",
    "regular": "常规",
    "sprint": "冲刺",
    "relax": "放松",
    "social": "社交",
    "recovery": "恢复",
    "holiday": "假期",
    "study": "学习",
    "gaming": "游戏",
}

LIFE_MODE_LABELS = {
    "awake": "清醒",
    "sleeping": "睡眠中",
    "late_night": "熬夜",
    "all_nighter": "通宵",
    "resting": "休息",
    "relax": "放松",
    "relaxing": "放松",
    "going_out": "外出",
    "mixed": "混合节奏",
}

SCHEDULE_TONE_LABELS = {
    **LIFE_MODE_LABELS,
    "awake": "正常活动",
    "sleeping": "低活动休息",
    "late_night": "夜间清醒",
    "all_nighter": "通宵节奏",
    "resting": "低强度休息",
    "going_out": "外出节奏",
}

SLEEP_MODE_LABELS = {
    "normal": "正常睡眠",
    "late_night": "熬夜",
    "all_nighter": "通宵",
    "nap": "小睡",
    "early_sleep": "早睡",
    "awake": "清醒",
    "asleep": "已入睡",
    "mixed": "混合",
}

SLEEP_DEPTH_LABELS = {
    "awake": "清醒",
    "light_rest": "浅休息",
    "light_sleep": "浅睡眠",
    "deep_sleep": "深度睡眠",
}

OUTFIT_DECISION_LABELS = {
    "keep": "保持当前穿搭",
    "keep_current": "保持当前穿搭",
    "unchanged": "不更换",
    "update": "更新穿搭",
    "change": "更换穿搭",
    "partial_change": "局部调整",
    "half_change": "局部调整",
    "adjust": "局部调整",
    "outdoor": "外出",
    "switch_home": "换成居家状态",
    "switch_outdoor": "换成外出状态",
    "sleepwear": "换成睡眠/居家穿搭",
    "sleep": "进入睡眠状态",
    "late_night": "熬夜状态",
    "all_nighter": "通宵状态",
    "unknown": "未知",
}

PLAN_OUTFIT_DECISION_LABELS = {
    **OUTFIT_DECISION_LABELS,
    "keep": "预计保持",
    "keep_current": "预计保持",
    "unchanged": "预计不更换",
    "update": "预计更新",
    "change": "预计更换",
    "partial_change": "预计局部调整",
    "half_change": "预计局部调整",
    "adjust": "预计局部调整",
    "outdoor": "预计外出",
    "switch_home": "预计居家",
    "switch_outdoor": "预计外出",
    "sleepwear": "预计睡眠/居家",
    "sleep": "预计睡眠",
    "late_night": "预计夜间状态",
    "all_nighter": "预计通宵状态",
}

SCHEDULE_INTENT_LABELS = {
    "home": "居家",
    "work": "工作",
    "study": "学习",
    "social": "社交",
    "rest": "休息",
    "relax": "放松",
    "relaxing": "放松",
    "outing": "外出",
    "mixed": "混合安排",
    "active": "活跃",
    "normal": "常规",
}

BIAS_LABELS = {
    "rest": "休息",
    "normal": "正常",
    "active": "活跃",
    "avoid": "回避",
    "light": "轻量",
    "social": "社交",
}

SOURCE_LABELS = {
    "daily": "每日生成",
    "context": "上下文刷新",
    "chat": "聊天触发",
    "manual": "手动刷新",
    "dashboard": "面板刷新",
    "idle": "自动刷新",
    "invite": "邀约",
    "command": "指令",
    "memo": "备忘录",
    "commitment": "承诺",
    "chat_memory": "聊天记忆",
    "daily_review": "每日复盘",
    "life_event": "生活事件",
    "learning": "偏好学习",
    "custom": "自定义",
    "builtin": "内置",
    "event": "事件",
}

PAGE_STATUS_REASON_LABELS = {
    "state": "实时状态更新",
    "daily_refresh": "每日生活背景刷新",
    "nightly_review": "夜间复盘",
    "weekly_refresh": "每周生活主题刷新",
    "weather": "天气更新",
    "autonomous_life_update": "自主生活状态与穿搭更新",
    "chat_state_refresh": "聊天触发状态巡检",
    "invite_outfit_update": "邀约后的穿搭判断",
    "private_revisit": "私聊回访",
    "proactive_reply": "闲时回复",
    "proactive_reply_decision": "闲时回复裁定",
    "expression_decision": "表达方式裁定",
}

VISIBILITY_LABELS = {
    "unseen": "未看见",
    "scanned": "扫到",
    "seen_but_ignored": "看见但略过",
    "seen": "已留意",
    "focused": "重点留意",
    "skimmed": "粗略看过",
    "ignored": "已忽略",
    "missed": "未注意",
}

PREFERENCE_CATEGORY_LABELS = {
    "activity": "活动",
    "outfit": "穿搭",
    "social": "社交",
    "sleep": "睡眠",
    "place": "地点",
    "style": "风格",
    "other": "其他",
    "general": "综合",
}

EVENT_STATUS_LABELS = {
    "open": "进行中",
    "done": "已完成",
    "closed": "已关闭",
    "cancelled": "已取消",
    "expired": "已过期",
    "active": "进行中",
    "scheduled": "已计划",
}

ACTION_LABELS = {
    "ignore": "忽略",
    "reply": "回复",
    "observe": "观察",
    "remember": "记录",
    "save": "保存",
    "save_memory": "保存记忆",
    "skip_memory": "跳过记忆",
    "need_deep_analysis": "需要深度分析",
    "voice_expression": "表达方式：语音",
    "text_expression": "表达方式：文字",
    "comfort": "安抚",
    "push_back": "反驳",
    "join_ritual": "跟仪式",
    "eat_melon": "吃瓜围观",
    "none": "无动作",
}

UNDERSTANDING_LABELS = {
    "understood": "已理解",
    "partial": "部分理解",
    "unclear": "不明确",
    "unknown": "未知",
}

SCENE_TYPE_LABELS = {
    "chat": "普通闲聊",
    "casual": "普通闲聊",
    "casual_chat": "普通闲聊",
    "profile": "群友档案",
    "relationship": "群友档案",
    "group_profile": "群友档案",
    "environment": "群环境",
    "group_environment": "群环境",
    "invite": "邀约线索",
    "invitation": "邀约线索",
    "conflict": "争论",
    "argument": "争论",
    "meme": "玩梗",
    "joke": "玩梗",
    "welcome": "欢迎",
    "spam": "刷屏",
    "repetition": "复读",
    "eat_melon": "吃瓜",
    "discussing_bot": "提到我",
    "other": "其他",
}

BOT_WATCH_STATE_LABELS = {
    "blackout": "未关注",
    "peek": "偶尔看一眼",
    "skim_window": "窗口扫读",
    "active_watch": "持续关注",
    "engaged": "已参与",
}

INTERRUPT_LEVEL_LABELS = {
    "ordinary": "普通消息可打断",
    "medium": "中等相关才打断",
    "high": "强信号才打断",
}

ATMOSPHERE_LABELS = {
    "quiet": "冷清",
    "calm": "平稳",
    "normal": "平稳",
    "active": "活跃",
    "busy": "活跃",
    "spam": "刷屏",
    "argument": "争论",
    "conflict": "争论",
    "meme": "玩梗",
    "joke": "玩梗",
    "welcome": "欢迎",
    "other": "其他",
}

COMMITMENT_KIND_LABELS = {
    "plan": "计划",
    "reminder": "提醒",
    "followup": "后续话题",
    "promise": "承诺",
    "todo": "待办",
    "other": "其他",
}

TIME_WINDOW_LABELS = {
    "weekend": "周末",
    "next_chat": "下次聊天",
    "next_time": "下次合适时间",
    "morning": "早晨",
    "daytime": "日间",
    "night": "晚上",
}


def display_label(value: object, labels: dict[str, str], default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return labels.get(text) or labels.get(text.lower()) or text


def weekday_label(value: object) -> str:
    return display_label(value, WEEKDAY_LABELS)


def template_label(value: object) -> str:
    return display_label(value, TEMPLATE_LABELS)


def time_period_label(value: object) -> str:
    return display_label(value, TIME_PERIOD_CN)


def life_mode_label(value: object) -> str:
    return display_label(value, LIFE_MODE_LABELS)


def schedule_tone_label(value: object) -> str:
    return display_label(value, SCHEDULE_TONE_LABELS)


def sleep_mode_label(value: object) -> str:
    return display_label(value, SLEEP_MODE_LABELS)


def outfit_decision_label(value: object) -> str:
    return display_label(value, OUTFIT_DECISION_LABELS)


def plan_outfit_decision_label(value: object) -> str:
    return display_label(value, PLAN_OUTFIT_DECISION_LABELS)


def schedule_intent_label(value: object) -> str:
    return display_label(value, SCHEDULE_INTENT_LABELS)


def source_label(value: object) -> str:
    return display_label(value, SOURCE_LABELS)


def page_status_reason_label(value: object) -> str:
    return display_label(value, PAGE_STATUS_REASON_LABELS)


def preference_category_label(value: object) -> str:
    return display_label(value, PREFERENCE_CATEGORY_LABELS)


def event_status_label(value: object) -> str:
    return display_label(value, EVENT_STATUS_LABELS)


def visibility_label(value: object) -> str:
    return display_label(value, VISIBILITY_LABELS)


def action_label(value: object) -> str:
    return display_label(value, ACTION_LABELS)


def understanding_label(value: object) -> str:
    return display_label(value, UNDERSTANDING_LABELS)


def scene_type_label(value: object) -> str:
    return display_label(value, SCENE_TYPE_LABELS)


def bot_watch_state_label(value: object) -> str:
    return display_label(value, BOT_WATCH_STATE_LABELS)


def interrupt_level_label(value: object) -> str:
    return display_label(value, INTERRUPT_LEVEL_LABELS)


def atmosphere_label(value: object) -> str:
    return display_label(value, ATMOSPHERE_LABELS)


def commitment_kind_label(value: object) -> str:
    return display_label(value, COMMITMENT_KIND_LABELS)


def time_window_label(value: object) -> str:
    return display_label(value, TIME_WINDOW_LABELS)


def sleep_depth_label(value: object) -> str:
    return display_label(value, SLEEP_DEPTH_LABELS)
