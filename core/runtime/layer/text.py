import datetime
from typing import Any

from ...life.tools import (
    get_current_timeline_status,
    get_time_period_cn,
    parse_time_minutes,
)
from ...models import CommitmentRecord, DayRecord
from ...prompts import CORE_HIDDEN_CONTEXT_RULES


class LayerTextMixin:
    def _format_hidden_state_compact(self, state: Any) -> str:
        state_dict = state.as_dict() if hasattr(state, "as_dict") else (state if isinstance(state, dict) else {})
        if not state_dict:
            return ""

        scores = []
        for label, key in (
            ("体力", "energy"),
            ("心情值", "mood_score"),
            ("忙碌", "busyness"),
            ("社交", "social"),
            ("压力", "stress"),
            ("困倦", "sleepiness"),
            ("互动", "interaction_capacity"),
            ("摸鱼", "fishing"),
            ("注意开放", "attention_openness"),
        ):
            value = state_dict.get(key)
            if value is not None and value != "":
                scores.append(f"{label} {value}/100")

        mood = self._hidden_text(state_dict.get("mood"), 32)
        summary = self._hidden_text(state_dict.get("summary"), 120)
        watch_state = self._hidden_text(state_dict.get("watch_state"), 32)
        interrupt_level = self._hidden_text(state_dict.get("interrupt_level"), 32)
        interrupt_reason = self._hidden_text(state_dict.get("interrupt_reason"), 100)
        sleep = state_dict.get("sleep") if isinstance(state_dict.get("sleep"), dict) else {}
        sleep_depth = self._hidden_text(sleep.get("depth"), 24)
        sleep_summary = self._hidden_text(sleep.get("summary"), 80)

        lines = []
        base = "；".join(scores)
        if mood:
            base = f"{base}；心情：{mood}" if base else f"心情：{mood}"
        if summary:
            base = f"{base}；整体：{summary}" if base else f"整体：{summary}"
        if base:
            lines.append(f"- 当前身体与情绪底色：{base}")

        attention = "；".join(
            part
            for part in (
                f"观看状态 {watch_state}" if watch_state else "",
                f"打断门槛 {interrupt_level}" if interrupt_level else "",
                f"睡眠层级 {sleep_depth}" if sleep_depth else "",
                f"睡眠 {sleep_summary}" if sleep_summary else "",
                f"打断原因：{interrupt_reason}" if interrupt_reason else "",
            )
            if part
        )
        if attention:
            lines.append(f"- 注意力与睡眠线索：{attention}")

        return "[HiddenState]\n" + "\n".join(lines) if lines else ""

    def _format_timeline_item_compact(self, item: Any, limit: int = 44) -> str:
        time_text = self._hidden_text(getattr(item, "time", ""), 8)
        activity = self._hidden_text(getattr(item, "activity", ""), limit)
        status = self._hidden_text(getattr(item, "status", ""), 16)
        status_text = f" [{status}]" if status else ""
        return f"{time_text} {activity}{status_text}".strip()

    def _format_hidden_schedule_window(self, timeline: list[Any], now: datetime.datetime) -> str:
        if not timeline:
            return ""

        timed = sorted(
            (
                parse_time_minutes(getattr(item, "time", "")),
                index,
                item,
            )
            for index, item in enumerate(timeline)
        )
        if not timed:
            return ""

        now_minute = now.hour * 60 + now.minute
        current_pos = 0
        for pos, (minute, _, _) in enumerate(timed):
            if minute <= now_minute:
                current_pos = pos
            else:
                break

        lines = []
        index_text = "；".join(
            part
            for part in (self._format_timeline_item_compact(item, limit=24) for _, _, item in timed)
            if part
        )
        if index_text:
            lines.append(f"- 全天索引: {self._hidden_text(index_text, 420)}")

        for pos in range(max(0, current_pos - 1), min(len(timed), current_pos + 3)):
            label = "当前" if pos == current_pos else ("上一段" if pos < current_pos else "接下来")
            lines.append(f"- {label}: {self._format_timeline_item_compact(timed[pos][2])}")

        return (
            "[HiddenScheduleWindow]\n"
            "普通聊天只参考当前窗口；用户明确询问全天安排、时间冲突或邀约时，再按全天索引自然回答。\n"
            + "\n".join(lines)
        )

    def build_hidden_activity_hint(
        self,
        data: DayRecord,
        now: datetime.datetime,
        using_extended_night: bool,
    ) -> tuple[str, str, str]:
        period = self._get_curr_period(now)
        period_cn = get_time_period_cn(period)
        if using_extended_night:
            meta = data.meta or {}
            life_mode = meta.get("life_mode", "")
            sleep_mode = meta.get("sleep_mode", "")
            if life_mode in {"awake", "late_night", "all_nighter", "mixed"} or sleep_mode in {"late_night", "all_nighter"}:
                return (
                    f"深夜/凌晨，日程基调 {life_mode or sleep_mode}",
                    f"🌙 今日生成基调: {life_mode or sleep_mode}，当前是否清醒仍按实时状态和时间轴判断",
                    period_cn,
                )
            return (
                f"深夜/凌晨，日程基调 {life_mode or sleep_mode or '延续昨日状态'}",
                "💤 今日生成基调偏休息/低活动，结合实时状态与时间线自然判断是否清醒、困倦或已休息",
                period_cn,
            )

        status_desc = self._get_time_status(now)
        curr_act, next_act = get_current_timeline_status(data.timeline, now, data.date)
        if curr_act:
            activity = f"📍 正在: {curr_act.activity} (状态: {curr_act.status or '平和'})"
            if next_act:
                activity += f" | 🔜 待办: {next_act.time} {next_act.activity}"
        else:
            activity = "⏳ 碎片时间 (无特定安排)"
            if next_act:
                activity += f" | 🔜 待办: {next_act.time} {next_act.activity}"
        return status_desc, activity, period_cn

    def build_hidden_life_context(
        self,
        data: DayRecord,
        now: datetime.datetime,
        using_extended_night: bool,
        world_context: str = "",
        group_awareness_context: str = "",
        commitments: list[CommitmentRecord] | None = None,
        experience_context: str = "",
        memos_context: str = "",
        structured: str = "",
        expression_event: Any = None,
    ) -> str:
        status_desc, activity, period_cn = self.build_hidden_activity_hint(
            data,
            now,
            using_extended_night,
        )
        parts = [
            "\n\n<daily_life>",
            "\n[UseRule] 以下内容是角色日常生活背景的隐藏上下文，不是当前聊天话题。"
            "除非用户明确询问时间、状态、穿搭、天气、日程、邀约或相关细节，"
            "否则禁止主动提及、复述、解释或暗示这些内容；普通闲聊时只用于维持处境一致。",
            f"\n[HiddenContextRules] {CORE_HIDDEN_CONTEXT_RULES}",
        ]

        outfit = data.outfit
        if outfit:
            parts.append(
                f"\n[HiddenAppearanceHint] {outfit} "
                "(仅在用户明确询问外貌、穿搭或相关视觉细节时参考，禁止主动介绍)"
            )

        meta = data.meta
        if meta:
            parts.append(f"\n[HiddenMoodHint] 主题<{meta.get('theme')}> | 心情<{meta.get('mood')}>")

        if data.timeline:
            schedule_window = self._format_hidden_schedule_window(data.timeline, now)
            if schedule_window:
                parts.append(f"\n{schedule_window}")

        weather_info = data.weather_info
        weather_str = data.weather or "未知"
        if weather_info.temp is not None:
            weather_str = f"{weather_str} (体感: {weather_info.temp_desc})"
        parts.append(f"\n[HiddenWeather] {weather_str}")

        if data.memo:
            parts.append(f"\n[HiddenMemoHint] 今日重要备忘录: {data.memo} (仅在用户询问安排、计划或相关事项时参考)")

        lines = [
            f"- #{item.id} {item.content}"
            for item in (commitments or [])[:5]
            if getattr(item, "content", "")
        ]
        if lines:
            parts.append(
                "\n[HiddenCommitmentHint] 未完成承诺/约定，仅在用户问起、涉及计划或自然续聊时参考:\n"
                + "\n".join(lines)
            )

        parts.append(f"\n[HiddenStatusHint] {status_desc}")
        parts.append(f"\n[HiddenActivityHint] {activity}")
        parts.append(f"\n[HiddenTime] {now.strftime('%Y-%m-%d %H:%M')} ({period_cn})")
        state_context = self._format_hidden_state_compact(data.state)
        if state_context:
            parts.append(f"\n{state_context}")

        if world_context:
            parts.append(
                "\n[HiddenWorldMemory] 关系、地点与事件记忆，仅用于维持长期一致性，禁止主动展开:\n"
                f"{world_context}"
            )
        if group_awareness_context:
            parts.append(
                "\n[HiddenGroupChatAwareness] 最近群聊感知、消息留意和动作裁定，仅用于判断是否自然参与、是否需要观察或深度分析；"
                "禁止把分数、标签或内心旁白直接说给用户:\n"
                f"{group_awareness_context}"
            )
        if structured:
            parts.append(
                "\n[HiddenStructuredConversation] 最近真实消息结构，优先级高于长期记忆；"
                "用于判断群聊里谁在对谁说话、是否引用或@了我，避免把别人之间的对话误当成问我:\n"
                f"{structured}"
            )
        if experience_context:
            parts.append(
                "\n[HiddenLifeExperience] 生活片段、关注目标、行为反馈、场景词和记忆边界，仅用于长期一致性与智能判断；"
                "禁止直接暴露为系统字段或后台记录:\n"
                f"{experience_context}"
            )
        if memos_context:
            parts.append(
                "\n[HiddenExternalMemory] MemOS 外部长期记忆参考，只用于补足长期事实和偏好；"
                "若与当前人设线索或本插件已校准记忆冲突，以人设线索和本插件当前结构化记忆为准:\n"
                f"{memos_context}"
            )

        parts.append("\n</daily_life>")
        channel_hint = self.build_hidden_expression_channel_hint(expression_event)
        if channel_hint:
            parts.append(channel_hint)
        return "".join(parts)

    def build_missing_life_context(
        self,
        now: datetime.datetime,
        target_date_str: str,
        using_extended_night: bool,
        event: Any = None,
        memos_context: str = "",
    ) -> str:
        period_cn = get_time_period_cn(self._get_curr_period(now))
        date_hint = "凌晨延续时段" if using_extended_night else "当前日期"
        external = (
            "\n[HiddenExternalMemory] MemOS 外部长期记忆参考，只用于补足长期事实和偏好；"
            "若与当前人设线索或本插件已校准记忆冲突，以人设线索和本插件当前结构化记忆为准:\n"
            f"{memos_context}"
            if memos_context
            else ""
        )
        return (
            "\n\n<daily_life>"
            "\n[UseRule] 当前还没有可用的日常生活记录；这只是一条防止编造的隐藏约束，不是聊天话题。"
            "\n[AntiFabricationRule] 在确认记录形成前，禁止编造今天正在做什么、穿什么、在哪里、天气如何、睡眠如何或接下来有什么安排；"
            "如果用户明确询问这些内容，只能用角色口吻自然表示今天的安排还没整理清楚或暂时不确定，不要提及后台、系统或记录生成。"
            "普通闲聊时不要主动提及这段缺失。"
            f"\n[HiddenContextRules] {CORE_HIDDEN_CONTEXT_RULES}"
            f"\n[HiddenScheduleUnavailable] {date_hint} {target_date_str} 暂无已确认的日程、穿搭、地点、天气、生活状态或时间轴。"
            f"\n[HiddenTime] {now.strftime('%Y-%m-%d %H:%M')} ({period_cn})"
            f"{external}"
            "\n</daily_life>"
            f"{self.build_hidden_expression_channel_hint(event)}"
        )
