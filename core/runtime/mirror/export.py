from __future__ import annotations

import datetime
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...life.condition import classify_message_interrupt, message_can_interrupt
from ...life.tools import format_timeline_to_text, resolve_business_now, resolve_daily_hint


class SnapshotExportMixin:
    async def _get_rich_context_parts(
        self,
        data: Any,
        now: datetime.datetime,
        using_extended_night: bool,
    ) -> list[str]:
        parts: list[str] = []
        week_plan = await self.composer._get_week_plan()
        if week_plan.generated:
            parts.append(f"📅 [本周主题] {week_plan.theme or '未设定'}")
            hint = resolve_daily_hint(week_plan, now, default="")
            if hint:
                parts.append(f"💡 [今日提示] {hint}")
        return parts

    async def get_life_context(self) -> dict[str, Any]:
        """向其他插件暴露当前生活状态。"""
        now = life_now()
        today_str = now.strftime("%Y-%m-%d")
        yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        business_now = resolve_business_now(self.config.schedule_time, now)
        target_date_str = business_now.strftime("%Y-%m-%d")
        is_extended_night = business_now.date() < now.date()

        if is_extended_night:
            if await self.archive.get_day(yesterday_str):
                target_date_str = yesterday_str
                logger.debug("[生活上下文] 凌晨模式，使用昨日数据")
            else:
                return {"schedule": "当前暂无日程记录 (休息中)"}
        elif await self.archive.get_day(today_str):
            target_date_str = today_str
        elif await self.archive.get_day(yesterday_str):
            target_date_str = yesterday_str
            is_extended_night = True
        else:
            return {"schedule": "当前暂无日程记录"}

        data = await self.archive.get_day(target_date_str)
        if not data:
            return {}
        self._schedule_context_state_refresh(target_date_str, data, now)

        meta = data.meta
        if is_extended_night:
            life_mode = meta.get("life_mode") or ""
            sleep_mode = meta.get("sleep_mode") or ""
            label = life_mode or sleep_mode or "延续昨日记录"
            suffix = f" · {sleep_mode}" if sleep_mode and sleep_mode != label else ""
            status_line = f"时段状态：深夜/凌晨，日程基调 {label}{suffix}"
        else:
            status_line = f"时段状态：{self._get_time_status(now)}"
        weather_str = data.weather or "未知"
        w_info = data.weather_info
        if w_info.temp_desc:
            weather_str += f" (感受: {w_info.temp_desc})"

        meta_line = ""
        if meta:
            tags = []
            if meta.get("theme"):
                tags.append(f"🏷️ {meta['theme']}")
            if meta.get("mood"):
                tags.append(f"🎨 {meta['mood']}")
            if meta.get("schedule_intent"):
                tags.append(f"📅 {meta['schedule_intent']}")
            if meta.get("life_mode"):
                tags.append(f"日程基调:{meta['life_mode']}")
            if meta.get("sleep_mode"):
                tags.append(f"睡眠倾向:{meta['sleep_mode']}")
            if meta.get("plan_outfit_decision"):
                tags.append(f"日程穿搭:{meta['plan_outfit_decision']}")
            if meta.get("outfit_decision"):
                tags.append(f"当前穿搭:{meta['outfit_decision']}")
            if tags:
                meta_line = " | ".join(tags)

        rich_parts = await self._get_rich_context_parts(data, now, is_extended_night)
        memo = data.memo
        if memo:
            rich_parts.append(f"📌 [今日备忘录] {memo}")

        final_schedule = status_line
        if meta_line:
            final_schedule += f"\n{meta_line}"
        raw_schedule = format_timeline_to_text(data.timeline)
        final_schedule += f"\n(昨日记录) {raw_schedule}" if is_extended_night else f"\n{raw_schedule}"
        if rich_parts:
            final_schedule += "\n\n" + "\n".join(rich_parts)

        relationships = await self.archive.get_recent_relationships(5)
        places = await self.archive.get_recent_places(8)
        events = await self.archive.get_recent_events(8)
        await self._settle_stale_reply_effects()
        summaries = await self.archive.get_recent_chat_summaries(5)
        commitments = await self.archive.get_commitments(status="active", limit=8)
        episodes = await self.archive.get_life_episodes(limit=8)
        focus_targets = await self.archive.get_focus_targets(limit=8)
        feedback = await self.archive.get_behavior_feedback(limit=8)
        reply_effects = await self.archive.get_reply_effects(limit=8)
        memory_corrections = await self.archive.get_memory_corrections(limit=8, unapplied_only=True)
        expression_profiles = await self.archive.get_expression_profiles(limit=8)
        expression_reviews = await self.archive.get_expression_reviews(limit=8)
        behavior_patterns = await self.archive.get_behavior_patterns(limit=8)
        behavior_scenes = await self.archive.get_behavior_scenes(limit=8)
        mid_summaries = await self.archive.get_session_mid_summaries(limit=8)
        temporary_expression_states = await self.archive.get_temporary_expression_states(limit=8)
        focus_slots = await self.archive.get_focus_slots(limit=8)
        expression_intents = await self.archive.get_expression_intents(limit=8)
        terms = await self.archive.get_life_terms(limit=8)
        boundaries = await self.archive.get_memory_boundaries(limit=8)
        health = await self.archive.get_life_health_report(self.config.storage)
        state_dict = data.state.as_dict() if data.state else {}
        interrupt = classify_message_interrupt()
        return {
            "weather": weather_str,
            "outfit": data.outfit,
            "schedule": final_schedule,
            "meta": meta,
            "is_extended_night": is_extended_night,
            "timeline": [item.as_dict() for item in data.timeline],
            "memo": memo,
            "state": state_dict,
            "subject": {
                "watch_state": state_dict.get("watch_state", ""),
                "boredom": state_dict.get("boredom"),
                "fishing": state_dict.get("fishing"),
                "attention_openness": state_dict.get("attention_openness"),
                "interrupt_level": state_dict.get("interrupt_level", ""),
                "interrupt_reason": state_dict.get("interrupt_reason", ""),
                "sleep_depth": (state_dict.get("sleep") or {}).get("depth", "") if isinstance(state_dict.get("sleep"), dict) else "",
                "default_interrupt_signal": interrupt,
                "can_interrupt_default": message_can_interrupt(state_dict, interrupt),
            },
            "relationships": [item.as_dict() for item in relationships],
            "chat_summaries": [item.as_dict() for item in summaries],
            "places": [item.as_dict() for item in places],
            "events": [item.as_dict() for item in events],
            "commitments": [item.as_dict() for item in commitments],
            "experience": {
                "episodes": [item.as_dict() for item in episodes],
                "focus_targets": [item.as_dict() for item in focus_targets],
                "feedback": [item.as_dict() for item in feedback],
                "reply_effects": [item.as_dict() for item in reply_effects],
                "memory_corrections": [item.as_dict() for item in memory_corrections],
                "expression_profiles": [item.as_dict() for item in expression_profiles],
                "expression_reviews": [item.as_dict() for item in expression_reviews],
                "behavior_patterns": [item.as_dict() for item in behavior_patterns],
                "behavior_scenes": [item.as_dict() for item in behavior_scenes],
                "mid_summaries": [item.as_dict() for item in mid_summaries],
                "temporary_expression_states": [item.as_dict() for item in temporary_expression_states],
                "focus_slots": [item.as_dict() for item in focus_slots],
                "expression_intents": [item.as_dict() for item in expression_intents],
                "terms": [item.as_dict() for item in terms],
                "boundaries": [item.as_dict() for item in boundaries],
                "health": health,
            },
        }
