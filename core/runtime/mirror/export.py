from __future__ import annotations

import asyncio
import datetime
import json
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...config.options.basis import chat_style_length_limit, format_chat_style_prompt
from ...life.calendar import format_calendar_context, format_season_context
from ...life.condition import classify_message_interrupt, message_can_interrupt
from ...life.tools import (
    build_time_context,
    format_timeline_to_text,
    resolve_daily_hint,
)
from ...sources.platforms import parse_unified_origin

from typing import Protocol


class SnapshotExportComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _get_time_status: Any
    _life_context_filter_people_records: Any
    _life_context_meta_line: Any
    _life_context_people_keys: Any
    _life_context_subject: Any
    _life_context_weather: Any
    _schedule_context_state_refresh: Any
    _settle_stale_reply_effects: Any
    _share_behavior_payload: Any
    _share_current_facts: Any
    _share_episode_payload: Any
    _share_exact_scope: Any
    _share_expression_payload: Any
    _share_interaction_payload: Any
    _share_scope_context: Any
    _share_terms_payload: Any
    _share_text: Any
    archive: Any
    composer: Any
    config: Any


class SnapshotExportComponent:
    def __init__(self, context: SnapshotExportComponentContext) -> None:
        self.context = context

    @staticmethod
    def _share_current_facts(data: Any) -> dict[str, Any]:
        """只导出自身当前事实，不暴露原始生命内核或私人目标数据。"""
        meta = data.meta or {}
        if str(meta.get("residence_context_stale", "")).lower() == "true":
            return {
                "version": 1,
                "valid": False,
                "reason": "居住地变化，等待新生活记录确认",
            }

        def object_value(key: str) -> dict:
            try:
                value = json.loads(meta.get(key) or "{}")
            except (ValueError, TypeError):
                return {}
            return value if isinstance(value, dict) else {}

        run = object_value("continuous_execution")
        action = run.get("action") or {}
        action = action if isinstance(action, dict) else {}
        current = {}
        if run.get("status") in {"running", "paused", "ready", "settling"}:
            from ...models import INTERNAL_SIMULATED_ACTION_TYPES

            if action.get("action_type") in INTERNAL_SIMULATED_ACTION_TYPES:
                current = {
                    "activity": str(action.get("target") or "")[:240],
                    "status": run["status"],
                    "source": "continuous_executor",
                    "action_id": str(action.get("action_id") or ""),
                    "started_at": str(run.get("started_at") or ""),
                    "observation_state": run.get("observation_state", "observed"),
                    "observed_at": str(run.get("observed_at") or ""),
                }
        body = object_value("continuous_body")
        return {
            "version": 1,
            "valid": True,
            "date": data.date,
            "current_action": current,
            "body": {
                key: body[key]
                for key in (
                    "energy",
                    "sleep_pressure",
                    "fatigue",
                    "hunger",
                    "thirst",
                    "social_battery",
                    "discomfort",
                    "updated_at",
                    "uncertain_minutes",
                )
                if key in body
            },
        }

    async def _get_rich_context_parts(
        self,
        data: Any,
        now: datetime.datetime,
        using_extended_night: bool,
    ) -> list[str]:
        parts: list[str] = []
        week_plan = await self.context.composer._get_week_plan()
        if week_plan.generated:
            parts.append(f"📅 [本周主题] {week_plan.theme or '未设定'}")
            hint = resolve_daily_hint(week_plan, now, default="")
            if hint:
                parts.append(f"💡 [今日提示] {hint}")
        return parts

    async def _resolve_life_context_day(
        self, now: datetime.datetime
    ) -> tuple[Any | None, str, bool, str]:
        time_context = build_time_context(now, self.context.config.schedule_time)
        today_str = time_context.now.strftime("%Y-%m-%d")
        yesterday_str = (time_context.now - datetime.timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        is_extended_night = time_context.extended_night

        if is_extended_night:
            data = await self.context.archive.get_day(yesterday_str)
            if data:
                logger.debug("[生活上下文] 凌晨模式，使用昨日数据")
                return data, yesterday_str, True, ""
            return None, "", True, "当前暂无日程记录 (休息中)"

        data = await self.context.archive.get_day(today_str)
        if data:
            return data, today_str, False, ""

        data = await self.context.archive.get_day(yesterday_str)
        if data:
            return data, yesterday_str, True, ""
        return None, "", False, "当前暂无日程记录"

    def _life_context_status_line(
        self, data: Any, now: datetime.datetime, is_extended_night: bool
    ) -> str:
        meta = data.meta
        if not is_extended_night:
            return f"时段状态：{self.context._get_time_status(now)}"
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

    async def _life_context_schedule(
        self, data: Any, now: datetime.datetime, is_extended_night: bool
    ) -> str:
        parts = [self._life_context_status_line(data, now, is_extended_night)]
        meta_line = self.context._life_context_meta_line(data.meta)
        if meta_line:
            parts.append(meta_line)
        timeline = format_timeline_to_text(
            data.timeline, timeline_date=data.date, meta=data.meta
        )
        parts.append(f"(昨日记录) {timeline}" if is_extended_night else timeline)

        rich_parts = await self._get_rich_context_parts(data, now, is_extended_night)
        if data.memo:
            rich_parts.append(f"📌 [今日备忘录] {data.memo}")
        if rich_parts:
            parts.append("\n".join(rich_parts))
        return "\n".join(parts)

    @staticmethod
    def _life_context_subject(
        state_dict: dict[str, Any], interrupt: dict[str, Any]
    ) -> dict[str, Any]:
        sleep = (
            state_dict.get("sleep") if isinstance(state_dict.get("sleep"), dict) else {}
        )
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
        await self.context._settle_stale_reply_effects()
        (
            relationships,
            places,
            events,
            summaries,
            commitments,
            episodes,
            feedback,
            physiological_rhythm_logs,
            physiological_rhythm_trend,
            reply_effects,
            memory_corrections,
            expression_profiles,
            expression_reviews,
            behavior_scenes,
            expression_intents,
            terms,
            boundaries,
            health,
        ) = await asyncio.gather(
            self.context.archive.get_recent_relationships(5),
            self.context.archive.get_recent_places(8),
            self.context.archive.get_recent_events(8),
            self.context.archive.get_recent_chat_summaries(5),
            self.context.archive.get_commitments(status="active", limit=8),
            self.context.archive.get_life_episodes(limit=8),
            self.context.archive.get_behavior_feedback(limit=8),
            self.context.archive.get_physiological_rhythm_logs(limit=8),
            self.context.archive.get_physiological_rhythm_trend(days=7, limit=8),
            self.context.archive.get_reply_effects(limit=8),
            self.context.archive.get_memory_corrections(limit=8, unapplied_only=True),
            self.context.archive.get_expression_context(limit=16, scope="self"),
            self.context.archive.get_expression_reviews(limit=8),
            self.context.archive.get_behavior_scenes(limit=8),
            self.context.archive.get_expression_intents(limit=8),
            self.context.archive.get_life_terms(limit=8),
            self.context.archive.get_memory_boundaries(limit=8),
            self.context.archive.get_life_health_report(self.context.config.storage),
        )
        return {
            "relationships": relationships,
            "places": places,
            "events": events,
            "summaries": summaries,
            "commitments": commitments,
            "experience": {
                "episodes": episodes,
                "feedback": feedback,
                "physiological_rhythm_logs": physiological_rhythm_logs,
                "physiological_rhythm_trend": physiological_rhythm_trend,
                "reply_effects": reply_effects,
                "memory_corrections": memory_corrections,
                "expression_profiles": expression_profiles,
                "expression_reviews": expression_reviews,
                "behavior_scenes": behavior_scenes,
                "expression_intents": expression_intents,
                "terms": terms,
                "boundaries": boundaries,
                "health": health,
            },
        }

    @staticmethod
    def _life_context_people_keys(relationships: list[Any]) -> set[str]:
        keys: set[str] = set()
        for relationship in relationships:

            def field_value(key: str, relationship: Any = relationship) -> Any:
                if isinstance(relationship, dict):
                    return relationship.get(key, "")
                return getattr(relationship, key, "")

            for value in (
                field_value("id"),
                field_value("name"),
                field_value("alias"),
                field_value("subjective_name"),
                field_value("user_id"),
            ):
                text = str(value or "").strip()
                if text:
                    keys.add(text)
        return keys

    @classmethod
    def _life_context_filter_people_records(
        cls, records: list[Any], people_keys: set[str], *, keep_without_people: bool
    ) -> list[Any]:
        filtered = []
        for item in records:
            people = {
                str(value or "").strip()
                for value in (getattr(item, "people", []) or [])
                if str(value or "").strip()
            }
            if (not people and keep_without_people) or people.intersection(people_keys):
                filtered.append(item)
        return filtered

    async def _life_context_target_archive_snapshot(
        self, target_umo: str
    ) -> dict[str, Any]:
        scope = str(target_umo or "").strip()
        if not scope:
            return await self._life_context_archive_snapshot()

        is_group = ":GroupMessage:" in scope
        relationship_task = (
            asyncio.sleep(0, result=[])
            if is_group
            else self.context.archive.get_relationships_for_target(scope, limit=1)
        )
        relationships, summaries, places, events, commitments = await asyncio.gather(
            relationship_task,
            self.context.archive.get_chat_summaries_for_session(scope, limit=5),
            self.context.archive.get_recent_places(8),
            self.context.archive.get_recent_events(8),
            self.context.archive.get_commitments(status="active", limit=20),
        )

        if not relationships and not is_group:
            _, real_id = parse_unified_origin(scope)
            relationship = await self.context.archive.get_relationship(real_id)
            relationships = [relationship] if relationship else []

        people_keys = self.context._life_context_people_keys(relationships)
        events = self.context._life_context_filter_people_records(
            events,
            people_keys,
            keep_without_people=True,
        )
        commitments = [
            item
            for item in commitments
            if str(getattr(item, "source_session", "") or "").strip() == scope
            or bool(
                {
                    str(value or "").strip()
                    for value in (getattr(item, "people", []) or [])
                    if str(value or "").strip()
                }.intersection(people_keys)
            )
        ][:8]
        return {
            "relationships": relationships,
            "places": places,
            "events": events,
            "summaries": summaries,
            "commitments": commitments,
            "experience": {},
        }

    @staticmethod
    def _share_record_value(item: Any, key: str, default: Any = "") -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    @classmethod
    def _share_text(cls, item: Any, key: str, limit: int = 160) -> str:
        value = cls._share_record_value(item, key)
        return " ".join(str(value or "").split())[:limit]

    @classmethod
    def _share_exact_scope(cls, records: list[Any], scope: str) -> list[Any]:
        return [
            item for item in records if cls._share_text(item, "scope", 180) == scope
        ]

    @classmethod
    def _share_scope_context(cls, target_umo: str) -> tuple[str, str, bool, bool]:
        scope = str(target_umo or "").strip()
        _, real_id = parse_unified_origin(scope)
        is_group = ":GroupMessage:" in scope
        is_private = ":FriendMessage:" in scope
        experience_scope = real_id if is_group and real_id else scope
        return scope, experience_scope, is_group, not (is_group or is_private)

    @classmethod
    def _share_episode_payload(
        cls,
        episodes: list[Any],
        people_keys: set[str],
        *,
        is_private: bool,
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for item in episodes:
            related_people = {
                str(value or "").strip()
                for value in (cls._share_record_value(item, "related_people", []) or [])
                if str(value or "").strip()
            }
            source = cls._share_text(item, "source", 40)
            if related_people:
                if not is_private or not related_people.intersection(people_keys):
                    continue
            elif source != "daily":
                continue
            payload = {
                "date": cls._share_text(item, "date", 20),
                "title": cls._share_text(item, "title", 100),
                "summary": cls._share_text(item, "summary", 240),
                "impact": cls._share_text(item, "impact", 120),
            }
            if payload["title"] or payload["summary"]:
                result.append(payload)
            if len(result) >= 3:
                break
        return result

    @classmethod
    def _share_expression_payload(
        cls, profiles: list[Any]
    ) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {
            "tones": [],
            "habits": [],
            "avoid": [],
        }

        def append_unique(key: str, value: Any, limit: int = 4) -> None:
            text = " ".join(str(value or "").split())[:100]
            if text and text not in result[key] and len(result[key]) < limit:
                result[key].append(text)

        for item in profiles:
            learning = cls._share_record_value(item, "learning", {}) or {}
            if learning.get("status") in {"cooling", "emerging"}:
                continue
            append_unique("tones", cls._share_record_value(item, "tone"))
            for value in cls._share_record_value(item, "habits", []) or []:
                append_unique("habits", value)
            for value in cls._share_record_value(item, "avoid", []) or []:
                append_unique("avoid", value)
        return {key: values for key, values in result.items() if values}

    @classmethod
    def _share_behavior_payload(
        cls, scenes: list[Any]
    ) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in scenes:
            payload = {
                "scene": cls._share_text(item, "scene", 80),
                "preferred": cls._share_text(item, "preferred_action", 100),
                "avoid": cls._share_text(item, "avoid_action", 100),
                "outcome": cls._share_text(item, "outcome_hint", 120),
            }
            key = (payload["scene"], payload["preferred"], payload["avoid"])
            if any(key) and key not in seen:
                seen.add(key)
                result.append(payload)
            if len(result) >= 4:
                break
        return result[:4]

    @classmethod
    def _share_terms_payload(cls, terms: list[Any]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for item in terms:
            term = cls._share_text(item, "term", 50)
            meaning = cls._share_text(item, "meaning", 120)
            if not term or not meaning:
                continue
            examples = cls._share_record_value(item, "examples", []) or []
            result.append(
                {
                    "term": term,
                    "meaning": meaning,
                    "scene": cls._share_text(item, "scene", 80),
                    "example": " ".join(str(examples[0] or "").split())[:100]
                    if examples
                    else "",
                }
            )
            if len(result) >= 3:
                break
        return result

    @classmethod
    def _share_interaction_payload(
        cls, effects: list[Any], feedback: list[Any]
    ) -> dict[str, Any]:
        outcomes = [
            cls._share_text(item, "outcome", 20)
            for item in effects
            if cls._share_text(item, "outcome", 20) != "pending"
        ]
        outcomes.extend(
            cls._share_text(item, "result", 20)
            for item in feedback
            if cls._share_text(item, "result", 20)
        )
        if not outcomes:
            return {}
        counts: dict[str, int] = {}
        for outcome in outcomes:
            key = outcome or "neutral"
            counts[key] = counts.get(key, 0) + 1
        positive = counts.get("positive", 0)
        negative = counts.get("negative", 0)
        if positive > negative:
            summary = "近期互动反馈整体积极，可保持当前自然表达方式"
        elif negative > positive:
            summary = "近期互动反馈偏弱，应缩短表达并减少主动推进"
        else:
            summary = "近期互动反馈平稳，保持轻量自然即可"
        return {
            "summary": summary,
            "positive": positive,
            "neutral": counts.get("neutral", 0),
            "negative": negative,
        }

    async def _share_guidance(
        self, target_umo: str, relationships: list[Any]
    ) -> dict[str, Any]:
        scope, experience_scope, is_group, is_public = self.context._share_scope_context(
            target_umo
        )
        is_private = bool(scope and not (is_group or is_public))
        people_keys = self.context._life_context_people_keys(relationships)
        profile_ids = [
            self.context._share_text(item, "id", 120) or self.context._share_text(item, "user_id", 120)
            for item in relationships
        ]
        profile_id = next((value for value in profile_ids if value), "")
        _, real_id = parse_unified_origin(scope)

        await self.context._settle_stale_reply_effects()
        (
            episodes,
            rhythm_trend,
            scoped_profiles,
            behavior_scenes,
            reply_effects,
            behavior_feedback,
            session_behavior_feedback,
            terms,
        ) = await asyncio.gather(
            self.context.archive.get_life_episodes(limit=16),
            self.context.archive.get_physiological_rhythm_trend(days=7, limit=8),
            self.context.archive.get_expression_context(
                limit=16, scope=experience_scope or "self", profile_id=profile_id
            ),
            self.context.archive.get_behavior_scenes(limit=4, scope=experience_scope)
            if experience_scope
            else asyncio.sleep(0, result=[]),
            self.context.archive.get_reply_effects(limit=8, scope=scope)
            if scope
            else asyncio.sleep(0, result=[]),
            self.context.archive.get_behavior_feedback(
                limit=8,
                target_id=(real_id if is_group else scope),
            )
            if scope
            else asyncio.sleep(0, result=[]),
            self.context.archive.get_behavior_feedback(limit=8, target_id=scope)
            if is_group and real_id and real_id != scope
            else asyncio.sleep(0, result=[]),
            self.context.archive.get_life_terms(limit=4, scope=experience_scope)
            if experience_scope
            else asyncio.sleep(0, result=[]),
        )

        profiles = []
        profile_keys: set[tuple[str, str, str]] = set()
        for item in scoped_profiles:
            key = (
                self.context._share_text(item, "scope", 160),
                self.context._share_text(item, "profile_id", 120),
                self.context._share_text(item, "label", 80),
            )
            if key not in profile_keys:
                profile_keys.add(key)
                profiles.append(item)

        scoped_profiles = self.context._share_exact_scope(scoped_profiles, experience_scope)
        behavior_scenes = self.context._share_exact_scope(behavior_scenes, experience_scope)
        terms = self.context._share_exact_scope(terms, experience_scope)

        profiles = [
            item
            for item in profiles
            if item in scoped_profiles
            or (
                self.context._share_text(item, "scope", 160) == "self"
                and not self.context._share_text(item, "profile_id", 120)
            )
            or (
                is_private
                and self.context._share_text(item, "scope", 160) == experience_scope
                and self.context._share_text(item, "profile_id", 120) == profile_id
            )
        ]

        trend_summary = ""
        if isinstance(rhythm_trend, dict):
            trend_summary = " ".join(str(rhythm_trend.get("summary") or "").split())[
                :240
            ]
        return {
            "version": 1,
            "episodes": self.context._share_episode_payload(
                episodes, people_keys, is_private=is_private
            ),
            "rhythm_trend": trend_summary,
            "expression": self.context._share_expression_payload(profiles),
            "behavior": self.context._share_behavior_payload(behavior_scenes),
            "interaction": self.context._share_interaction_payload(
                reply_effects, [*behavior_feedback, *session_behavior_feedback]
            ),
            "terms": self.context._share_terms_payload(terms),
        }

    def get_share_chat_style(self, *, scene: str = "") -> dict[str, Any]:
        """导出配置中的语气与长度软偏好，不携带会话记忆或发送规则。"""
        style = getattr(self.context.config, "chat_style", None)
        if not style or not bool(getattr(style, "enabled", False)):
            return {"enabled": False, "prompt": ""}
        prompt = format_chat_style_prompt(getattr(style, "casual_short_prompt", ""))
        if scene == "qzone_post":
            prompt += (
                " 公开说说沿用上述语气与表达节奏，像平时说话一样随手发一条状态；"
                "围绕本次在意的一点，用自己会说出口的话表达，意思说完就自然停住，"
                "不需要完整铺景、抒情或总结一天。按语意自然换行，不固定字数、行数或标题模板。"
                "这是单条公开文案的表达偏好，不采用私聊长度或聊天分段发送。正常生成标点，正文提交前按当前开关和字符表统一清洗。"
                "日常状态通常一句就够，只说最想说的那件小事；不顺带汇报下一项安排，"
                "不补一段氛围描写或升华，不为了显得随性硬加口头禅。"
            )
        elif scene in {"qzone_comment", "qzone_reply"}:
            limit = int(getattr(style, "casual_max_chars", 50) or 50)
            prompt += f" 公开互动沿用平时说话的语气，轻闲聊参考长度约 {limit} 字；只作节奏参考。"
        elif scene in {"share_private", "share_group"}:
            audience = "群友" if scene == "share_group" else "当前私聊对象"
            limit = chat_style_length_limit(style, is_group=scene == "share_group")
            prompt += (
                f" 这是面向{audience}的一条日常分享，沿用上述语气与表达节奏，意思说清楚就停。"
                + (
                    f"轻闲聊参考长度约 {limit} 字左右；只作表达节奏参考，整轮简短，不因分段而扩写，必要信息完整保留。"
                    if limit > 0
                    else ""
                )
                + "问候心情围绕当下一点，不铺景或凑感悟；新闻知识推荐保留主体和必要事实，不硬压成一句。"
                "正常生成标点，分享正文生成后由 daily_life 按当前开关和字符表统一清洗；不要提前删除。"
                "遵守分享插件自己的排版与事实要求，称呼和关系只用当前对象已确认的信息。"
            )
            if scene == "share_group":
                prompt += " 群聊只使用已授权的群内材料，不引用私人关系或私聊记忆。"
        return {"enabled": True, "prompt": prompt}

    async def get_share_context(self, target_umo: str = "") -> dict[str, Any]:
        """向分享类插件暴露目标隔离且已提炼的生活上下文。"""
        context = await self._build_share_base_context(target_umo)
        if not context:
            return {}
        browser = (getattr(self.context, "bili_browsing", None)
                   if callable(getattr(getattr(self.context, "archive", None), "_run_db", None)) else None)
        if browser is not None:
            context["bili_experiences"] = await browser.context(target_umo or "public", sharing=True)
        context["share_guidance"] = await self._share_guidance(
            target_umo, list(context.get("relationships") or [])
        )
        from .sharing import share_continuity, share_timing
        from ..contact import contact_rhythm

        try:
            context["conversation_timing"] = await share_timing(self.context, target_umo)
            context["contact_rhythm"] = await contact_rhythm(self.context, target_umo, life_now())
        except Exception as exc:
            context["contact_rhythm"] = {"ready": False}
            logger.debug(f"[分享上下文] 会话时间暂不可用：{type(exc).__name__}")
        if context.get("current_facts", {}).get("valid") is not False:
            try:
                context["continuity"] = await share_continuity(self.context, target_umo)
            except Exception as exc:
                logger.debug(f"[分享上下文] 连续生活摘要暂不可用：{type(exc).__name__}")
        return context

    async def _build_share_base_context(self, target_umo: str = "") -> dict[str, Any]:
        """组装分享专用上下文的基础生活状态。"""
        now = life_now()
        time_context = build_time_context(now, self.context.config.schedule_time)
        (
            data,
            target_date_str,
            is_extended_night,
            missing_schedule,
        ) = await self._resolve_life_context_day(now)
        if missing_schedule:
            return {"schedule": missing_schedule}
        if not data or not target_date_str:
            return {}
        self.context._schedule_context_state_refresh(target_date_str, data, now)

        meta = data.meta
        memo = data.memo
        schedule, archive_snapshot = await asyncio.gather(
            self._life_context_schedule(data, now, is_extended_night),
            self._life_context_target_archive_snapshot(target_umo),
        )
        state_dict = data.state.as_dict() if data.state else {}
        current_facts = self.context._share_current_facts(data)
        if not current_facts["valid"]:
            # 历史记录仍保存在数据库中，不进入当前分享视图。
            return {
                "current_facts": current_facts,
                "current_awareness": {
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "timezone": "Asia/Shanghai",
                },
                "relationships": [
                    item.as_dict() for item in archive_snapshot["relationships"]
                ],
            }
        interrupt = classify_message_interrupt()
        weekday_names = (
            "星期一",
            "星期二",
            "星期三",
            "星期四",
            "星期五",
            "星期六",
            "星期日",
        )
        current_awareness = {
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "Asia/Shanghai",
            "utc_offset": "UTC+08:00",
            "date": time_context.now.date().isoformat(),
            "business_date": time_context.business_date_text,
            "weekday": weekday_names[now.weekday()],
            "time_period": time_context.period_cn,
            "calendar": format_calendar_context(now),
            "season": format_season_context(now),
        }
        return {
            "current_facts": current_facts,
            "current_awareness": current_awareness,
            "weather": self.context._life_context_weather(data),
            "outfit": data.outfit,
            "schedule": schedule,
            "meta": meta,
            "is_extended_night": is_extended_night,
            "timeline": [item.as_dict() for item in data.timeline],
            "memo": memo,
            "state": state_dict,
            "subject": self.context._life_context_subject(state_dict, interrupt),
            "relationships": [
                item.as_dict() for item in archive_snapshot["relationships"]
            ],
            "chat_summaries": [
                item.as_dict() for item in archive_snapshot["summaries"]
            ],
            "places": [item.as_dict() for item in archive_snapshot["places"]],
            "events": [item.as_dict() for item in archive_snapshot["events"]],
            "commitments": [item.as_dict() for item in archive_snapshot["commitments"]],
            "experience": {
                "episodes": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get("episodes", [])
                ],
                "feedback": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get("feedback", [])
                ],
                "physiological_rhythm_logs": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get(
                        "physiological_rhythm_logs", []
                    )
                ],
                "physiological_rhythm_trend": archive_snapshot["experience"].get(
                    "physiological_rhythm_trend", {}
                ),
                "reply_effects": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get("reply_effects", [])
                ],
                "memory_corrections": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get(
                        "memory_corrections", []
                    )
                ],
                "expression_profiles": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get(
                        "expression_profiles", []
                    )
                ],
                "expression_reviews": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get(
                        "expression_reviews", []
                    )
                ],
                "behavior_scenes": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get(
                        "behavior_scenes", []
                    )
                ],
                "expression_intents": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get(
                        "expression_intents", []
                    )
                ],
                "terms": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get("terms", [])
                ],
                "boundaries": [
                    item.as_dict()
                    for item in archive_snapshot["experience"].get("boundaries", [])
                ],
                "health": archive_snapshot["experience"].get("health", {}),
            },
        }
