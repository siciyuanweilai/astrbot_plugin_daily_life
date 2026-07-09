from __future__ import annotations

import asyncio
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

    async def _resolve_life_context_day(self, now: datetime.datetime) -> tuple[Any | None, str, bool, str]:
        today_str = now.strftime("%Y-%m-%d")
        yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        business_now = resolve_business_now(self.config.schedule_time, now)
        is_extended_night = business_now.date() < now.date()

        if is_extended_night:
            data = await self.archive.get_day(yesterday_str)
            if data:
                logger.debug("[生活上下文] 凌晨模式，使用昨日数据")
                return data, yesterday_str, True, ""
            return None, "", True, "当前暂无日程记录 (休息中)"

        data = await self.archive.get_day(today_str)
        if data:
            return data, today_str, False, ""

        data = await self.archive.get_day(yesterday_str)
        if data:
            return data, yesterday_str, True, ""
        return None, "", False, "当前暂无日程记录"

    def _life_context_status_line(self, data: Any, now: datetime.datetime, is_extended_night: bool) -> str:
        meta = data.meta
        if not is_extended_night:
            return f"时段状态：{self._get_time_status(now)}"
        life_mode = meta.get("life_mode") or ""
        sleep_mode = meta.get("sleep_mode") or ""
        label = life_mode or sleep_mode or "延续昨日记录"
        suffix = f" · {sleep_mode}" if sleep_mode and sleep_mode != label else ""
        return f"时段状态：深夜/凌晨，日程基调 {label}{suffix}"

    @staticmethod
    def _life_context_meta_line(meta: dict[str, Any]) -> str:
        tags: list[str] = []
        fields = [
            ("theme", "🏷️ {}"),
            ("mood", "🎨 {}"),
            ("schedule_intent", "📅 {}"),
            ("life_mode", "日程基调:{}"),
            ("sleep_mode", "睡眠倾向:{}"),
            ("plan_outfit_decision", "日程穿搭:{}"),
            ("outfit_decision", "当前穿搭:{}"),
        ]
        for key, template in fields:
            value = meta.get(key)
            if value:
                tags.append(template.format(value))
        return " | ".join(tags)

    @staticmethod
    def _life_context_weather(data: Any) -> str:
        weather = data.weather or "未知"
        weather_info = data.weather_info
        if weather_info.temp_desc:
            weather += f" (感受: {weather_info.temp_desc})"
        return weather

    async def _life_context_schedule(self, data: Any, now: datetime.datetime, is_extended_night: bool) -> str:
        parts = [self._life_context_status_line(data, now, is_extended_night)]
        meta_line = self._life_context_meta_line(data.meta)
        if meta_line:
            parts.append(meta_line)
        timeline = format_timeline_to_text(data.timeline)
        parts.append(f"(昨日记录) {timeline}" if is_extended_night else timeline)

        rich_parts = await self._get_rich_context_parts(data, now, is_extended_night)
        if data.memo:
            rich_parts.append(f"📌 [今日备忘录] {data.memo}")
        if rich_parts:
            parts.append("\n".join(rich_parts))
        return "\n".join(parts)

    @staticmethod
    def _life_context_subject(state_dict: dict[str, Any], interrupt: dict[str, Any]) -> dict[str, Any]:
        sleep = state_dict.get("sleep") if isinstance(state_dict.get("sleep"), dict) else {}
        return {
            "watch_state": state_dict.get("watch_state", ""),
            "boredom": state_dict.get("boredom"),
            "fishing": state_dict.get("fishing"),
            "attention_openness": state_dict.get("attention_openness"),
            "interrupt_level": state_dict.get("interrupt_level", ""),
            "interrupt_reason": state_dict.get("interrupt_reason", ""),
            "sleep_depth": sleep.get("depth", ""),
            "default_interrupt_signal": interrupt,
            "can_interrupt_default": message_can_interrupt(state_dict, interrupt),
        }

    async def _life_context_archive_snapshot(self) -> dict[str, Any]:
        await self._settle_stale_reply_effects()
        (
            relationships,
            places,
            events,
            summaries,
            commitments,
            episodes,
            focus_targets,
            feedback,
            physiological_rhythm_logs,
            physiological_rhythm_trend,
            reply_effects,
            memory_corrections,
            expression_profiles,
            expression_reviews,
            behavior_patterns,
            behavior_scenes,
            mid_summaries,
            temporary_expression_states,
            focus_slots,
            expression_intents,
            terms,
            boundaries,
            health,
        ) = await asyncio.gather(
            self.archive.get_recent_relationships(5),
            self.archive.get_recent_places(8),
            self.archive.get_recent_events(8),
            self.archive.get_recent_chat_summaries(5),
            self.archive.get_commitments(status="active", limit=8),
            self.archive.get_life_episodes(limit=8),
            self.archive.get_focus_targets(limit=8),
            self.archive.get_behavior_feedback(limit=8),
            self.archive.get_physiological_rhythm_logs(limit=8),
            self.archive.get_physiological_rhythm_trend(days=7, limit=8),
            self.archive.get_reply_effects(limit=8),
            self.archive.get_memory_corrections(limit=8, unapplied_only=True),
            self.archive.get_expression_profiles(limit=8),
            self.archive.get_expression_reviews(limit=8),
            self.archive.get_behavior_patterns(limit=8),
            self.archive.get_behavior_scenes(limit=8),
            self.archive.get_session_mid_summaries(limit=8),
            self.archive.get_temporary_expression_states(limit=8),
            self.archive.get_focus_slots(limit=8),
            self.archive.get_expression_intents(limit=8),
            self.archive.get_life_terms(limit=8),
            self.archive.get_memory_boundaries(limit=8),
            self.archive.get_life_health_report(self.config.storage),
        )
        return {
            "relationships": relationships,
            "places": places,
            "events": events,
            "summaries": summaries,
            "commitments": commitments,
            "experience": {
                "episodes": episodes,
                "focus_targets": focus_targets,
                "feedback": feedback,
                "physiological_rhythm_logs": physiological_rhythm_logs,
                "physiological_rhythm_trend": physiological_rhythm_trend,
                "reply_effects": reply_effects,
                "memory_corrections": memory_corrections,
                "expression_profiles": expression_profiles,
                "expression_reviews": expression_reviews,
                "behavior_patterns": behavior_patterns,
                "behavior_scenes": behavior_scenes,
                "mid_summaries": mid_summaries,
                "temporary_expression_states": temporary_expression_states,
                "focus_slots": focus_slots,
                "expression_intents": expression_intents,
                "terms": terms,
                "boundaries": boundaries,
                "health": health,
            },
        }

    async def get_life_context(self) -> dict[str, Any]:
        """向其他插件暴露当前生活状态。"""
        now = life_now()
        data, target_date_str, is_extended_night, missing_schedule = await self._resolve_life_context_day(now)
        if missing_schedule:
            return {"schedule": missing_schedule}
        if not data or not target_date_str:
            return {}
        self._schedule_context_state_refresh(target_date_str, data, now)

        meta = data.meta
        memo = data.memo
        schedule, archive_snapshot = await asyncio.gather(
            self._life_context_schedule(data, now, is_extended_night),
            self._life_context_archive_snapshot(),
        )
        state_dict = data.state.as_dict() if data.state else {}
        interrupt = classify_message_interrupt()
        return {
            "weather": self._life_context_weather(data),
            "outfit": data.outfit,
            "schedule": schedule,
            "meta": meta,
            "is_extended_night": is_extended_night,
            "timeline": [item.as_dict() for item in data.timeline],
            "memo": memo,
            "state": state_dict,
            "subject": self._life_context_subject(state_dict, interrupt),
            "relationships": [item.as_dict() for item in archive_snapshot["relationships"]],
            "chat_summaries": [item.as_dict() for item in archive_snapshot["summaries"]],
            "places": [item.as_dict() for item in archive_snapshot["places"]],
            "events": [item.as_dict() for item in archive_snapshot["events"]],
            "commitments": [item.as_dict() for item in archive_snapshot["commitments"]],
            "experience": {
                "episodes": [item.as_dict() for item in archive_snapshot["experience"]["episodes"]],
                "focus_targets": [item.as_dict() for item in archive_snapshot["experience"]["focus_targets"]],
                "feedback": [item.as_dict() for item in archive_snapshot["experience"]["feedback"]],
                "physiological_rhythm_logs": [
                    item.as_dict() for item in archive_snapshot["experience"]["physiological_rhythm_logs"]
                ],
                "physiological_rhythm_trend": archive_snapshot["experience"]["physiological_rhythm_trend"],
                "reply_effects": [item.as_dict() for item in archive_snapshot["experience"]["reply_effects"]],
                "memory_corrections": [item.as_dict() for item in archive_snapshot["experience"]["memory_corrections"]],
                "expression_profiles": [item.as_dict() for item in archive_snapshot["experience"]["expression_profiles"]],
                "expression_reviews": [item.as_dict() for item in archive_snapshot["experience"]["expression_reviews"]],
                "behavior_patterns": [item.as_dict() for item in archive_snapshot["experience"]["behavior_patterns"]],
                "behavior_scenes": [item.as_dict() for item in archive_snapshot["experience"]["behavior_scenes"]],
                "mid_summaries": [item.as_dict() for item in archive_snapshot["experience"]["mid_summaries"]],
                "temporary_expression_states": [
                    item.as_dict() for item in archive_snapshot["experience"]["temporary_expression_states"]
                ],
                "focus_slots": [item.as_dict() for item in archive_snapshot["experience"]["focus_slots"]],
                "expression_intents": [item.as_dict() for item in archive_snapshot["experience"]["expression_intents"]],
                "terms": [item.as_dict() for item in archive_snapshot["experience"]["terms"]],
                "boundaries": [item.as_dict() for item in archive_snapshot["experience"]["boundaries"]],
                "health": archive_snapshot["experience"]["health"],
            },
        }
