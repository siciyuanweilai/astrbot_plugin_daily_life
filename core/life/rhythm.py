import datetime
import json
import uuid
from typing import Any

from ..models import DailyReviewRecord, EventRecord, LifeEventRecord, PreferenceRecord
from ..prompts import CORE_MEMORY_RULES, LIFE_PREFERENCE_CATEGORY_ENUM, cache_friendly_prompt, json_output_section
from ..clock import now as life_now
from .appearance import format_life_preference_context
from .tools import extract_json_from_text, format_timeline_to_text


def _clamp_float(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _compact(value: object, limit: int = 120) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


class LifecycleMixin:
    @staticmethod
    def _meta_float(meta: dict[str, str], key: str, default: float = 0.0) -> float:
        try:
            return float(str(meta.get(key, "")).strip())
        except (TypeError, ValueError):
            return default

    def _compute_sleep_continuity(
        self,
        previous_day,
        day,
    ) -> tuple[float, float, float]:
        previous_debt = self._meta_float(previous_day.meta if previous_day else {}, "sleep_debt", 0.0)
        state = day.state
        sleep_quality = state.sleep.quality if state and state.sleep.quality is not None else 65
        energy = state.energy if state and state.energy is not None else 60
        sleep_mode = (day.meta or {}).get("sleep_mode", "")

        quality_delta = (65 - sleep_quality) / 20.0
        mode_delta = {
            "all_nighter": 2.8,
            "late_night": 1.1,
            "early_sleep": -0.7,
            "normal": -0.2,
            "nap": -0.4,
        }.get(sleep_mode, 0.0)
        if sleep_quality >= 82:
            quality_delta -= 0.6
        elif sleep_quality <= 35:
            quality_delta += 0.7

        delta = round(quality_delta + mode_delta, 2)
        debt = round(_clamp_float(previous_debt + delta, 0.0, 10.0), 2)
        carryover = round(_clamp_float(float(energy) - debt * 3.6, 0.0, 100.0), 1)
        return debt, delta, carryover

    async def _apply_lifecycle_to_day(
        self,
        day,
        date: datetime.datetime,
        result: dict | None = None,
    ):
        previous_day = await self.archive.get_day((date - datetime.timedelta(days=1)).strftime("%Y-%m-%d"))
        debt, delta, carryover = self._compute_sleep_continuity(previous_day, day)
        day.meta["sleep_debt"] = f"{debt:.2f}".rstrip("0").rstrip(".")
        day.meta["sleep_debt_delta"] = f"{delta:.2f}".rstrip("0").rstrip(".")
        day.meta["energy_carryover"] = f"{carryover:.1f}".rstrip("0").rstrip(".")
        life_decision = result.get("life_decision") if isinstance(result, dict) else {}
        if isinstance(life_decision, dict):
            event_seed = life_decision.get("life_event")
            event = LifeEventRecord.from_value(event_seed, date=day.date, source="daily") if event_seed else None
            if event:
                await self.archive.add_life_event(event)
        return day

    async def _build_lifecycle_context(self, date: datetime.datetime) -> str:
        sections = []
        previous_day = await self.archive.get_day((date - datetime.timedelta(days=1)).strftime("%Y-%m-%d"))
        if previous_day:
            debt = self._meta_float(previous_day.meta or {}, "sleep_debt", 0.0)
            carryover = self._meta_float(previous_day.meta or {}, "energy_carryover", 60.0)
            sections.append(
                "## 🔋 连续体力与睡眠债\n"
                f"- 昨日睡眠债：{debt:.1f}/10\n"
                f"- 昨日体力延续值：{carryover:.0f}/100\n"
                "- 今日生成必须把这当作连续身体状态参考，而不是每天从零开始。"
            )

        trend_getter = getattr(self.archive, "get_physiological_rhythm_trend", None)
        if callable(trend_getter):
            trend = await trend_getter(days=7, limit=8)
            rhythm_lines = self._format_physiological_rhythm_trend_context(trend)
            if rhythm_lines:
                sections.append("## 🫧 近期生理节律\n" + rhythm_lines)

        preference_limit = max(0, self.config.lifecycle.max_preferences)
        preferences = await self.archive.get_preferences(preference_limit) if preference_limit else []
        preference_context = format_life_preference_context(
            preferences,
            self.config,
            limit=preference_limit,
        )
        if preference_context:
            sections.append("## 🧭 长期审美与生活偏好\n" + preference_context)

        reviews = await self.archive.get_recent_daily_reviews(3)
        if reviews:
            lines = [f"- {item.date}: {item.summary}" for item in reviews if item.summary]
            if lines:
                sections.append("## 🌙 近期每日复盘\n" + "\n".join(lines))

        events = await self.archive.get_life_events(status="open", limit=8)
        if events:
            lines = [
                f"- {item.date or '未定'}｜{item.title}：{item.effect or item.detail or '等待自然影响'}"
                for item in events
            ]
            sections.append("## ✨ 生活事件池\n" + "\n".join(lines))

        episodes = await self.archive.get_life_episodes(limit=5)
        if episodes:
            lines = [
                f"- {item.date or '未定'}｜{item.title}：{item.correction or item.summary or item.impact}"
                for item in episodes
            ]
            sections.append("## 🧩 生活片段记忆\n" + "\n".join(lines))

        focus_targets = await self.archive.get_focus_targets(limit=6)
        if focus_targets:
            lines = [
                f"- [{item.target_type}] {item.label or item.target_id}：优先级 {item.priority}；{item.reason or '近期自然多留意'}"
                for item in focus_targets
            ]
            sections.append("## 🎯 近期关注目标\n" + "\n".join(lines))

        feedback = await self.archive.get_behavior_feedback(limit=5)
        if feedback:
            lines = [
                f"- {item.scene or '未分场景'}｜{item.action or '动作'}：{item.feedback or item.result}（分值 {item.score:g}；{item.reason or item.source}）"
                for item in feedback
            ]
            sections.append("## 🧪 行为反馈学习\n" + "\n".join(lines))

        terms = await self.archive.get_life_terms(limit=8)
        if terms:
            lines = [
                f"- {item.term}：{item.meaning}（范围：{item.scope or '通用'}）"
                for item in terms
            ]
            sections.append("## 🗣️ 语言与群聊黑话\n" + "\n".join(lines))

        boundaries = await self.archive.get_memory_boundaries(limit=6)
        if boundaries:
            lines = [
                f"- {item.source_scope} -> {item.target_scope}: {item.policy}；{item.reason or '按上下文谨慎判断'}"
                for item in boundaries
            ]
            sections.append(
                "## 🧱 记忆边界\n"
                "这些是跨群/私聊引用记忆的边界提示。deny 不应跨域引用；ask 表示只在用户明确引导或上下文必要时谨慎使用。\n"
                + "\n".join(lines)
            )
        return "\n\n".join(sections)

    @staticmethod
    def _format_physiological_rhythm_trend_context(trend: dict[str, Any]) -> str:
        if not isinstance(trend, dict):
            return ""
        summary = _compact(trend.get("summary"), 240)
        logs = trend.get("logs") if isinstance(trend.get("logs"), list) else []
        lines = [f"- {summary}"] if summary else []
        for item in logs[:3]:
            if not isinstance(item, dict):
                continue
            date = _compact(item.get("date"), 20)
            body = _compact(item.get("body_label") or item.get("summary"), 80)
            social = item.get("social_battery")
            lifecycle = _compact(item.get("lifecycle_kind"), 40)
            parts = [body, f"社交电量 {social}/100" if social is not None else "", lifecycle]
            text = "；".join(part for part in parts if part)
            if text:
                lines.append(f"- {date or '近期'}：{text}")
        return "\n".join(lines)

    def _build_daily_review_prompt(
        self,
        day,
        preferences: list[PreferenceRecord],
        life_events: list[LifeEventRecord],
    ) -> str:
        state = day.state.as_dict() if day.state else {}
        pref_text = "\n".join(
            f"- [{item.category}] {item.content} (权重 {item.weight:.1f})"
            for item in preferences[:12]
        ) or "无"
        event_text = "\n".join(
            f"- {item.title}: {item.effect or item.detail}"
            for item in life_events[:8]
        ) or "无"
        fixed = f"""为当前角色的日常生活做夜间复盘与记忆沉淀。

通用记忆原则：
{CORE_MEMORY_RULES}

{json_output_section()}

返回结构：
{{
  "summary": "一句话复盘今天的生活质感和状态变化",
  "memory_points": ["以后生成生活背景值得引用的稳定记忆"],
  "preference_points": [
    {{"category": "{LIFE_PREFERENCE_CATEGORY_ENUM}", "content": "可复用偏好", "weight": 0.1-2.0, "evidence": "来自今天哪件事"}}
  ],
  "sleep_debt_delta": -3.0 到 3.0,
  "energy_carryover": 0-100,
  "life_events": [
    {{"title": "新的生活事件", "detail": "事件细节", "effect": "未来几天可能怎样影响日程/穿搭/社交", "status": "open"}}
  ]
}}

要求：
- preference_points 必须是稳定、可复用偏好；不确定就少写。
- life_events 是能自然延续几天的小事件，不要编造重大剧情。
- 根据 state.sleep、sleep_debt 和时间轴判断睡眠债增减；不要用固定文本匹配活动文字。
"""
        dynamic = f"""日期：{day.date}
穿搭：{day.outfit or "无"}
状态：{json.dumps(state, ensure_ascii=False)}
生活标签：{json.dumps(day.meta or {}, ensure_ascii=False)}
时间轴：
{format_timeline_to_text(day.timeline)}
今日地点：{json.dumps([item.as_dict() for item in day.places], ensure_ascii=False)}
今日事件：{json.dumps([item.as_dict() for item in day.new_events], ensure_ascii=False)}
已学习偏好：
{pref_text}
开放生活事件：
{event_text}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="今日复盘资料")

    def _fallback_daily_review(self, day) -> DailyReviewRecord:
        state_summary = day.state.summary if day.state and day.state.summary else ""
        first = day.timeline[0].activity if day.timeline else ""
        summary = state_summary or first or "今天以自然节奏度过。"
        debt = self._meta_float(day.meta or {}, "sleep_debt_delta", 0.0)
        carryover = self._meta_float(day.meta or {}, "energy_carryover", 60.0)
        return DailyReviewRecord(
            date=day.date,
            summary=summary,
            memory_points=[summary],
            sleep_debt_delta=debt,
            energy_carryover=carryover,
        )

    async def compose_daily_review(
        self,
        date: datetime.date | datetime.datetime | str | None = None,
        *,
        force: bool = False,
    ) -> DailyReviewRecord | None:
        if date is None:
            now = life_now()
            date_str = now.strftime("%Y-%m-%d")
        elif isinstance(date, str):
            date_str = date[:10]
        else:
            date_str = date.strftime("%Y-%m-%d")

        if not force:
            existing = await self.archive.get_daily_review(date_str)
            if existing:
                return existing

        day = await self.archive.get_day(date_str)
        if not day:
            return None

        preferences = await self.archive.get_preferences(12)
        events = await self.archive.get_life_events(status="open", limit=8)
        provider_id = self._task_provider_id(self.config.lifecycle.provider)
        provider = await self._get_provider(provider_id)
        review = None
        session_id = f"daily_life_review_{uuid.uuid4().hex[:8]}"
        try:
            if provider:
                text = await self._call_llm_text(
                    provider,
                    self._build_daily_review_prompt(day, preferences, events),
                    session_id,
                    primary_provider_id=provider_id,
                )
                payload = extract_json_from_text(text)
                if isinstance(payload, dict):
                    review = DailyReviewRecord.from_value(
                        {
                            **payload,
                            "date": date_str,
                            "source": "daily_review",
                        }
                    )
        finally:
            await self._cleanup_conversation(session_id)

        fallback_review = self._fallback_daily_review(day)
        review = review or fallback_review
        if not review.summary:
            review.summary = fallback_review.summary
        saved = await self.archive.save_daily_review(review)

        if review.memory_points:
            await self.archive.add_events(
                date_str,
                [
                    EventRecord(
                        date=date_str,
                        summary=f"夜间复盘：{point}",
                        importance="normal",
                        source="daily_review",
                    )
                    for point in review.memory_points[:5]
                ],
            )
        if review.life_events:
            await self.archive.add_events(
                date_str,
                [
                    EventRecord(
                        date=event.date or date_str,
                        summary=event.title,
                        importance="normal",
                        source="life_event",
                    )
                    for event in review.life_events[:5]
                ],
            )
        return saved

    async def learn_preferences_from_payload(
        self,
        payload: dict,
        *,
        date_str: str,
        source: str,
    ) -> list[PreferenceRecord]:
        raw = payload.get("preference_points") or []
        if not isinstance(raw, list):
            return []
        preferences = [
            item
            for item in (
                PreferenceRecord.from_value(pref, date=date_str, source=source)
                for pref in raw
            )
            if item is not None
        ]
        if not preferences:
            return []
        return await self.archive.upsert_preferences(preferences, date_str)

    async def persist_life_events_from_payload(
        self,
        payload: dict,
        *,
        date_str: str,
        source: str,
    ) -> list[LifeEventRecord]:
        raw = payload.get("life_events") or []
        if not isinstance(raw, list):
            return []
        saved = []
        for event in raw:
            item = LifeEventRecord.from_value(event, date=date_str, source=source)
            if item:
                stored = await self.archive.add_life_event(item)
                if stored:
                    saved.append(stored)
        return saved
