import datetime
from typing import Any

from ...config.options.basis import format_chat_style_prompt
from ...life.appearance import format_current_appearance_context
from ...life.calendar import format_calendar_context, format_season_context
from ...life.condition import format_physiological_rhythm_prompt
from ...life.restdelay import format_rest_delay_hint, is_rest_activity
from ...life.tools import (
    build_time_context,
    format_timeline_travel,
    get_current_timeline_status,
    timeline_deferred_until,
    timeline_item_datetime,
)
from ...models import CommitmentRecord, DayRecord
from ...prompts import CORE_HIDDEN_CONTEXT_RULES


class LayerTextMixin:
    def build_hidden_chat_style_hint(self) -> str:
        style = getattr(getattr(self, "config", None), "chat_style", None)
        checker = getattr(self, "_chat_style_enabled", None)
        if not style or (callable(checker) and not checker()):
            return ""
        if not callable(checker) and not bool(getattr(style, "enabled", False)):
            return ""

        if self._semantic_segment_enabled():
            style = getattr(getattr(self, "config", None), "chat_style", None)
            try:
                casual_limit = int(getattr(style, "casual_max_chars", 50) or 50)
            except (TypeError, ValueError):
                casual_limit = 0
            prompt = format_chat_style_prompt(
                getattr(style, "casual_short_prompt", "")
            )
            prompt_hint = f"\n- {self._hidden_text(prompt, 360)}" if prompt else ""
            length_hint = (
                f"\n- 日常闲聊参考长度约 {casual_limit} 字左右；只作节奏参考，"
                "普通闲聊整轮保持简短，不因分条而扩写；必要的解释和情绪支持说完整，不按字数删减。"
                if casual_limit > 0
                else ""
            )
            return (
                "\n[HiddenChatStyle] 回复先判断目标、表达动作和信息深度，再自然组织成完整表达；"
                "不要按固定字数截断，不要为了制造停顿拆开一个完整意思。"
                "一句只放一个主要意思，能自然停住就停住。"
                "客观事实先看依据；信息会变化或拿不准时先核验，不能确认就坦白边界。"
                + prompt_hint
                + length_hint
            )

        lines = ["\n[HiddenChatStyle] 默认聊天表达节奏:"]
        prompt = format_chat_style_prompt(
            getattr(style, "casual_short_prompt", "")
        )
        if prompt:
            lines.append(f"- {self._hidden_text(prompt, 360)}")

        casual_max = getattr(style, "casual_max_chars", None)
        try:
            casual_max_int = int(casual_max)
        except (TypeError, ValueError):
            casual_max_int = 0
        if casual_max_int > 0:
            lines.append(
                f"- 日常闲聊参考长度约 {casual_max_int} 字左右；只作节奏参考，"
                "普通闲聊整轮保持简短，不因分条而扩写；必要的解释和情绪支持说完整，不按字数删减。"
            )

        lines.append("- 轻松接话保持短气口；一句只放一个主要意思，能自然停住就停住。")
        lines.append(
            "- 客观事实先看依据；信息会变化或拿不准时先核验，不能确认就坦白边界。"
        )

        return "\n".join(lines)

    def _format_hidden_state_compact(self, state: Any) -> str:
        state_dict = (
            state.as_dict()
            if hasattr(state, "as_dict")
            else (state if isinstance(state, dict) else {})
        )
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
        sleep = (
            state_dict.get("sleep") if isinstance(state_dict.get("sleep"), dict) else {}
        )
        sleep_depth = self._hidden_text(sleep.get("depth"), 24)
        sleep_summary = self._hidden_text(sleep.get("summary"), 80)
        rhythm = (
            state_dict.get("physiological_rhythm")
            if isinstance(state_dict.get("physiological_rhythm"), dict)
            else {}
        )
        rhythm_text = (
            self._hidden_text(format_physiological_rhythm_prompt(rhythm), 180)
            if rhythm
            else ""
        )

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
        if rhythm_text:
            lines.append(f"- 生理节律：{rhythm_text}")

        return "[HiddenState]\n" + "\n".join(lines) if lines else ""

    def _format_timeline_item_compact(
        self,
        item: Any,
        limit: int = 44,
        *,
        previous_place: str = "",
        include_travel: bool = False,
        timeline_date: Any = None,
        meta: dict | None = None,
    ) -> str:
        time_text = self._hidden_text(getattr(item, "time", ""), 8)
        deferred = timeline_deferred_until(item, timeline_date, meta)
        if deferred:
            time_text += f" → 顺延 {deferred:%m-%d %H:%M}"
        activity = self._hidden_text(getattr(item, "activity", ""), limit)
        status = self._hidden_text(getattr(item, "status", ""), 16)
        status_text = f" [{status}]" if status else ""
        text = f"{time_text} {activity}{status_text}".strip()
        if include_travel:
            travel = format_timeline_travel(
                item,
                previous_place=previous_place,
                include_provider=False,
            )
            if travel:
                text += f"；出行：{travel}"
        return text

    def _format_hidden_schedule_window(
        self, timeline: list[Any], now: datetime.datetime,
        *, timeline_date: Any = None, meta: dict | None = None,
    ) -> str:
        if not timeline:
            return ""

        date = timeline_date or now.date()
        timed = sorted(
            (time, index, item) for index, item in enumerate(timeline)
            if (time := timeline_item_datetime(item, date, meta=meta)) is not None
        )
        if not timed:
            return ""

        current, _ = get_current_timeline_status(timeline, now, date, meta=meta)
        current_pos = -1
        for pos, (time, _, _) in enumerate(timed):
            if time <= now:
                current_pos = pos
            else:
                break

        lines = []
        index_text = "；".join(
            part
            for part in (
                self._format_timeline_item_compact(item, limit=24, timeline_date=date, meta=meta)
                for _, _, item in timed
            )
            if part
        )
        if index_text:
            lines.append(f"- 全天索引: {self._hidden_text(index_text, 420)}")

        for pos in range(max(0, current_pos - 1), min(len(timed), current_pos + 3)):
            item = timed[pos][2]
            if item is current:
                label = "当前计划" if is_rest_activity(item) else "当前"
            else:
                label = "已过计划" if timed[pos][0] <= now else "接下来"
            previous_item = timed[pos - 1][2] if pos > 0 else None
            previous_place = (
                str(getattr(previous_item, "place", "") or "").strip()
                if previous_item is not None
                else ""
            )
            lines.append(
                f"- {label}: {self._format_timeline_item_compact(item, previous_place=previous_place, include_travel=True, timeline_date=date, meta=meta)}"
            )

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
        time_context = build_time_context(
            now, getattr(self.config, "schedule_time", "07:00")
        )
        period_cn = time_context.period_cn
        if using_extended_night:
            meta = data.meta or {}
            life_mode = meta.get("life_mode", "")
            sleep_mode = meta.get("sleep_mode", "")
            delay_hint = format_rest_delay_hint(data, now)
            if life_mode in {
                "awake",
                "late_night",
                "all_nighter",
                "mixed",
            } or sleep_mode in {"late_night", "all_nighter"}:
                activity = (
                    f"🌙 今日生成基调: {life_mode or sleep_mode}，当前是否清醒仍按实时状态和时间轴判断"
                )
                if delay_hint:
                    activity += f"；{delay_hint}"
                return (
                    f"深夜/凌晨，日程基调 {life_mode or sleep_mode}",
                    activity,
                    period_cn,
                )
            activity = (
                "💤 今日生成基调偏休息/低活动，结合实时状态与时间线自然判断是否清醒、困倦或已休息"
            )
            if delay_hint:
                activity += f"；{delay_hint}"
            return (
                f"深夜/凌晨，日程基调 {life_mode or sleep_mode or '延续昨日状态'}",
                activity,
                period_cn,
            )

        status_desc = self._get_time_status(now)
        curr_act, next_act = get_current_timeline_status(data.timeline, now, data.date, meta=data.meta)
        next_time = timeline_item_datetime(next_act, data.date, meta=data.meta)
        next_hint = f" | 🔜 待办: {next_time:%m-%d %H:%M} {next_act.activity}" if next_act and next_time else ""
        if curr_act:
            if is_rest_activity(curr_act):
                activity = f"当前休息计划: {curr_act.activity}；是否已入睡应结合实时状态和最新对话，不能按钟点断言"
            else:
                activity = f"📍 正在: {curr_act.activity} (状态: {curr_act.status or '平和'})"
        else:
            activity = "⏳ 碎片时间 (无特定安排)"
        activity += next_hint
        delay_hint = format_rest_delay_hint(data, now)
        if delay_hint:
            activity += f" | 🕒 {delay_hint}"
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
        recent_video: str = "",
        expression_event: Any = None,
    ) -> str:
        meta = data.meta or {}
        residence_context_stale = (
            str(meta.get("residence_context_stale") or "").strip().lower() == "true"
        )
        if residence_context_stale:
            period_cn = build_time_context(
                now, getattr(self.config, "schedule_time", "07:00")
            ).period_cn
            status_desc = "居住地已变化，当前生活状态等待新记录确认"
            activity = "当前地点、穿搭、天气和时间轴暂不引用旧记录"
        else:
            status_desc, activity, period_cn = self.build_hidden_activity_hint(
                data,
                now,
                using_extended_night,
            )
        parts = [
            "\n\n<daily_life>",
            "\n[UseRule] 以下内容是角色日常生活背景。按当前话题自然引用有依据的细节，"
            "无需逐项汇报，也不要把未来计划说成已经发生。",
            f"\n[HiddenContextRules] {CORE_HIDDEN_CONTEXT_RULES}",
        ]
        style_hint = self.build_hidden_chat_style_hint()
        if style_hint:
            parts.append(style_hint)

        if residence_context_stale:
            parts.append(
                "\n[HiddenResidenceRefresh] 居住地刚刚变化，旧记录仍作为历史保留，"
                "但不得把其中的地点、天气、穿搭、心情、状态或日程视为当前事实；"
                "新生活记录完成前，对这些问题只能自然表示暂未确定。"
            )
        else:
            appearance = format_current_appearance_context(data)
            if appearance:
                parts.append(
                    f"\n[HiddenAppearanceHint]\n{appearance}\n"
                    "(结合当前话题按需参考，不逐项介绍；仅以当前已确认外观为事实)"
                )

            if meta:
                parts.append(
                    f"\n[HiddenMoodHint] 主题<{meta.get('theme')}> | 心情<{meta.get('mood')}>"
                )

            if data.timeline:
                schedule_window = self._format_hidden_schedule_window(
                    data.timeline, now, timeline_date=data.date, meta=data.meta
                )
                if schedule_window:
                    parts.append(f"\n{schedule_window}")

            delay_hint = format_rest_delay_hint(data, now)
            if delay_hint:
                parts.append(
                    "\n[HiddenRestDelay] "
                    + delay_hint
                    + "；这是当前对话形成的临时安排，不能把原定节点当成已经发生。"
                )

            weather_info = data.weather_info
            weather_str = data.weather or "未知"
            if weather_info.temp is not None:
                weather_str = f"{weather_str} (体感: {weather_info.temp_desc})"
            parts.append(f"\n[HiddenWeather] {weather_str}")

        if data.memo:
            parts.append(
                f"\n[HiddenMemoHint] 今日重要备忘录: {data.memo} (涉及相关话题时自然参考，计划不等于已经发生)"
            )

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

        if world_context:
            parts.append(
                "\n[HiddenWorldMemory] 关系、地点与事件记忆，按话题相关性自然引用，旧事不可当作当前事实:\n"
                f"{world_context}"
            )
        if experience_context:
            parts.append(
                "\n[HiddenLifeExperience] 生活片段、关注目标、行为反馈、语言和记忆边界，仅用于长期一致性与智能判断；"
                "禁止直接暴露为系统字段或后台记录:\n"
                f"{experience_context}"
            )
        if memos_context:
            parts.append(
                "\n[HiddenExternalMemory] MemOS 外部长期记忆参考，只用于补足长期事实和偏好；"
                "若与当前人设线索或本插件已校准记忆冲突，以人设线索和本插件当前结构化记忆为准:\n"
                f"{memos_context}"
            )

        parts.append(f"\n[HiddenStatusHint] {status_desc}")
        parts.append(f"\n[HiddenActivityHint] {activity}")
        parts.append(f"\n[HiddenTime] {now.strftime('%Y-%m-%d %H:%M')} ({period_cn})")
        parts.append(f"\n[HiddenCalendar] {format_calendar_context(now)}")
        parts.append(f"\n[HiddenSeason] {format_season_context(now)}")
        if not residence_context_stale:
            state_context = self._format_hidden_state_compact(data.state)
            if state_context:
                parts.append(f"\n{state_context}")

        if group_awareness_context:
            parts.append(
                "\n[HiddenGroupChatAwareness] 最近群聊感知、消息留意和动作裁定，仅用于判断是否自然参与、是否需要观察或深度分析；"
                "禁止把分数、标签或内心旁白直接说给用户:\n"
                f"{group_awareness_context}"
            )
        if recent_video:
            parts.append(
                "\n[HiddenRecentVideoUnderstanding] 近期真实视频理解结果，优先级高于生活背景和长期记忆；"
                "用户追问视频内容、画面、动作或引用视频时优先参考，不确定处要自然说明:\n"
                f"{recent_video}"
            )
        if structured:
            parts.append(
                "\n[HiddenStructuredConversation] 最近真实消息结构，优先级高于长期记忆；"
                "用于判断群聊里谁在对谁说话、是否引用或@了我，避免把别人之间的对话误当成问我:\n"
                f"{structured}"
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
        recent_video: str = "",
    ) -> str:
        period_cn = build_time_context(
            now, getattr(self.config, "schedule_time", "07:00")
        ).period_cn
        date_hint = "凌晨延续时段" if using_extended_night else "当前日期"
        external = (
            "\n[HiddenExternalMemory] MemOS 外部长期记忆参考，只用于补足长期事实和偏好；"
            "若与当前人设线索或本插件已校准记忆冲突，以人设线索和本插件当前结构化记忆为准:\n"
            f"{memos_context}"
            if memos_context
            else ""
        )
        video_context = (
            "\n[HiddenRecentVideoUnderstanding] 近期真实视频理解结果，优先级高于生活背景和长期记忆；"
            "用户追问视频内容、画面、动作或引用视频时优先参考，不确定处要自然说明:\n"
            f"{recent_video}"
            if recent_video
            else ""
        )
        return (
            "\n\n<daily_life>"
            "\n[UseRule] 当前还没有可用的日常生活记录；这只是一条防止编造的隐藏约束，不是聊天话题。"
            "\n[AntiFabricationRule] 在确认记录形成前，禁止编造今天正在做什么、穿什么、在哪里、天气如何、睡眠如何或接下来有什么安排；"
            "如果用户明确询问这些内容，只能用角色口吻自然表示今天的安排还没整理清楚或暂时不确定，不要提及后台、系统或记录生成。"
            "普通闲聊时不要主动提及这段缺失。"
            f"\n[HiddenContextRules] {CORE_HIDDEN_CONTEXT_RULES}"
            f"{self.build_hidden_chat_style_hint()}"
            f"\n[HiddenScheduleUnavailable] {date_hint} {target_date_str} 暂无已确认的日程、穿搭、地点、天气、生活状态或时间轴。"
            f"{external}"
            f"{video_context}"
            f"\n[HiddenTime] {now.strftime('%Y-%m-%d %H:%M')} ({period_cn})"
            f"\n[HiddenCalendar] {format_calendar_context(now)}"
            f"\n[HiddenSeason] {format_season_context(now)}"
            "\n</daily_life>"
            f"{self.build_hidden_expression_channel_hint(event)}"
        )
