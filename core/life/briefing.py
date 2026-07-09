import datetime

from astrbot.api import logger

from ..models import CommitmentRecord
from .surroundings import (
    choose_place_candidates,
    format_world_prompt,
    select_relevant_world,
)


class DailyBriefingMixin:
    @staticmethod
    def _format_commitment_prompt(commitments: list[CommitmentRecord]) -> str:
        if not commitments:
            return ""
        lines = []
        for item in commitments:
            meta = []
            if item.kind:
                meta.append(item.kind)
            if item.trigger_time:
                meta.append(item.trigger_time)
            if item.time_window:
                meta.append(item.time_window)
            if item.people:
                meta.append("相关人物：" + "、".join(item.people))
            if item.place:
                meta.append("地点：" + item.place)
            suffix = f"（{'；'.join(meta)}）" if meta else ""
            lines.append(f"- #{item.id} {item.content}{suffix}")
        return "\n".join(lines)

    async def _build_life_inertia_context(self, date: datetime.datetime) -> str:
        builder = getattr(self, "_build_recent_pattern_context", None)
        text = await builder(date) if callable(builder) else ""
        if text:
            return (
                text
                + "\n这些内容来自近期已发生的生活记录，只是连续性参考，不是模板、素材池或硬性规则；"
                "可以延续，也可以根据今天的新条件自然改变。"
            )
        return (
            "\n\n## 🧭 近期生活惯性\n"
            "暂无可参考的近期生活记录。请根据角色人设、天气、聊天记忆、承诺和当前指令自主决定，不需要套用固定模板。"
        )


    async def _build_previous_life_context(self, date: datetime.datetime) -> str:
        previous_str = (date - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        previous_day = await self.archive.get_day(previous_str)
        if not previous_day:
            return "\n\n## 🌙 昨日状态参考\n无昨日记录，可以自由决定今天的起床、睡眠和换装状态。"
        sleep = previous_day.state.sleep if previous_day.state else None
        sleep_text = ""
        if sleep:
            sleep_text = f"\n- 昨日睡眠记录：质量 {sleep.quality if sleep.quality is not None else '未知'}；{sleep.summary or '无摘要'}"
        meta = previous_day.meta or {}
        timeline_hint = previous_day.timeline[-1].activity[:80] if previous_day.timeline else ""
        return (
            "\n\n## 🌙 昨日状态参考"
            f"\n- 昨日穿搭：{previous_day.outfit or '未知'}"
            f"\n- 昨日生活模式：{meta.get('life_mode') or meta.get('schedule_intent') or '未知'}"
            f"\n- 昨日换装决定：{meta.get('outfit_decision') or '未知'}"
            f"\n- 昨日最后片段：{timeline_hint or '无'}"
            f"{sleep_text}"
            "\n这些只是连续生活参考，不强制延续。"
        )


    async def _build_history_schedule_summary(self, date: datetime.datetime) -> str:
        history = []
        for i in range(1, self.config.reference_history_days + 1):
            previous_date = (date - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            previous_day = await self.archive.get_day(previous_date)
            if previous_day and previous_day.timeline:
                meta = previous_day.meta or {}
                first_act = previous_day.timeline[0].activity[:40]
                last_act = previous_day.timeline[-1].activity[:40]
                tags = [
                    meta.get("theme"),
                    meta.get("schedule_type"),
                    meta.get("mood"),
                    meta.get("outfit_style_pool") or meta.get("style"),
                ]
                tag_text = "；".join(str(item).strip() for item in tags if str(item or "").strip())
                history.append(
                    f"[{previous_date}] {tag_text or '无标签'} | 穿搭: {previous_day.outfit[:48]} | "
                    f"起点: {first_act} | 收束: {last_act}"
                )
        return "\n".join(history) if history else ""


    async def _collect_recent_chat_context(self, persona: str) -> str:
        chat_logs = []
        hours = self.config.history_hours
        max_count = self.config.history_max_count

        for group_id in self.config.reference_groups:
            if not group_id:
                continue
            content = await self._get_recent_chats(
                group_id,
                is_group=True,
                hours=hours,
                max_count=max_count,
                persona=persona,
            )
            if content and content != "无":
                chat_logs.append(f"【群聊 {group_id}】\n{content}")

        for user_id in self.config.reference_users:
            if not user_id:
                continue
            content = await self._get_recent_chats(
                user_id,
                is_group=False,
                hours=hours,
                max_count=max_count,
                persona=persona,
            )
            if content and content != "无":
                profile = await self._resolve_reference_user_profile(user_id, persona=persona)
                title = profile.get("name") or user_id
                chat_logs.append(f"【私聊 {title}】\n{content}")

        if chat_logs:
            logger.debug(f"[日程生成] 已成功注入 {len(chat_logs)} 个会话的聊天历史记录参考（回溯 {hours} 小时）。")
            return "\n\n".join(chat_logs)
        if self.config.reference_groups or self.config.reference_users:
            logger.warning("[日程生成] 未获取到符合条件的聊天历史记录参考。")
        return "无"


    async def _build_world_context(
        self,
        date: datetime.datetime,
        schedule_intent: str,
        weather_info: dict,
        hints: list[str],
    ) -> str:
        relationships = await self.archive.get_recent_relationships(6)
        saved_places = await self.archive.get_recent_places(12)
        recent_events = await self.archive.get_recent_events(8)
        chat_summaries = await self.archive.get_recent_chat_summaries(self.config.memory.max_generation_items)

        place_candidates = choose_place_candidates(
            saved_places,
            date.date(),
            schedule_intent=schedule_intent,
            weather_condition=weather_info.get("condition", ""),
        )
        memory_hints = [
            *hints,
            weather_info.get("condition", ""),
            *[item.get("name", "") for item in place_candidates if isinstance(item, dict)],
        ]
        selected_world = select_relevant_world(
            relationships,
            saved_places,
            recent_events,
            chat_summaries,
            hints=memory_hints,
            relationship_limit=6,
            place_limit=8,
            event_limit=8,
            summary_limit=getattr(self.config.memory, "max_generation_items", 8),
        )
        return format_world_prompt(
            selected_world["relationships"],
            selected_world["places"],
            selected_world["events"],
            place_candidates,
            selected_world["summaries"],
        )



__all__ = ["DailyBriefingMixin"]
