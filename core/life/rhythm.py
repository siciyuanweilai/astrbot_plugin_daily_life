import asyncio
import datetime
import json
import uuid
from typing import Any

from astrbot.api import logger

from ..archive import DayRevisionConflict
from ..clock import now as life_now
from ..models import (
    TIMELINE_TERMINAL_STATES,
    DailyReviewRecord,
    EventRecord,
    LifeEventRecord,
    PreferenceRecord,
)
from ..prompts import (
    CORE_MEMORY_RULES,
    LIFE_PREFERENCE_CATEGORY_ENUM,
    cache_friendly_prompt,
    json_output_section,
)
from .appearance import format_life_preference_context
from .evolution import LifeEvolutionService
from .tools import (
    extract_json_from_text,
    format_timeline_to_text,
    reconcile_timeline_execution,
)


def _clamp_float(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _compact(value: object, limit: int = 120) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


DAILY_REVIEW_TIMELINE_SETTLED_AT = "daily_review_timeline_settled_at"
DAILY_REVIEW_COMPLETED_AT = "daily_review_completed_at"
DAILY_REVIEW_REVISION_ATTEMPTS = 6


class LifecycleMixin:
    @staticmethod
    def _meta_float(meta: dict[str, str], key: str, default: float = 0.0) -> float:
        try:
            return float(str(meta.get(key, "")).strip())
        except (TypeError, ValueError):
            return default

    def _build_preference_consistency_prompt(
        self,
        preferences: list[PreferenceRecord],
    ) -> str:
        fixed = f"""审阅已沉淀的生活偏好，将语义上完全相同、只是换了说法的记录合并。

{json_output_section()}

只输出 JSON 对象：
{{
  "merge_groups": [
    {{
      "canonical_id": "保留的偏好编号",
      "merge_ids": ["要并入该记录的偏好编号"],
      "content": "合并后准确、简洁、可长期复用的偏好",
      "evidence": "合并依据的简短概括"
    }}
  ]
}}

规则：
- 只有表达同一个稳定偏好、可以互相替代的记录才能合并；只是主题相关、场景相邻或部分重叠时必须保留为不同记录。
- 条件、对象、地点、时间、人物、穿着类别、颜色、材质或态度存在独立差异时，不得合并。
- 肯定、否定、接受、回避等方向不同的偏好不得合并。
- 每个编号最多出现一次；canonical_id 不能同时出现在 merge_ids。
- content 必须覆盖被合并记录的共同事实，不添加原记录没有的新偏好。
- 没有真正同义的记录时返回空数组。"""
        payload = [
            {
                "id": int(item.id or 0),
                "category": item.category,
                "content": item.content,
                "weight": float(item.weight or 0.0),
                "evidence": item.evidence,
                "last_seen": item.last_seen,
            }
            for item in preferences
            if int(item.id or 0) > 0 and str(item.content or "").strip()
        ]
        return cache_friendly_prompt(
            fixed,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            dynamic_title="待整理生活偏好",
        )

    async def maintain_preference_consistency(self, *, force: bool = False) -> int:
        """在后台合并同义偏好，避免重复证据放大同一生活倾向。"""

        if bool(getattr(self, "_preference_maintenance_done", False)) and not force:
            return 0
        if not force:
            self._preference_maintenance_done = True
        preferences = await self.archive.get_preferences(80)
        category_counts: dict[str, int] = {}
        for item in preferences:
            category = str(item.category or "general")
            category_counts[category] = category_counts.get(category, 0) + 1
        if not any(count >= 2 for count in category_counts.values()):
            return 0
        merger = getattr(self.archive, "merge_preferences", None)
        if not callable(merger):
            return 0
        provider_id = self._task_provider_id(self.config.lifecycle.provider)
        provider = await self._get_provider(provider_id)
        if not provider:
            return 0
        session_id = f"daily_life_preference_consistency_{uuid.uuid4().hex[:8]}"
        try:
            text = await self._call_llm_text(
                provider,
                self._build_preference_consistency_prompt(preferences),
                session_id,
                empty_retries=0,
                primary_provider_id=provider_id,
            )
            payload = extract_json_from_text(text)
            groups = payload.get("merge_groups") if isinstance(payload, dict) else None
            if not isinstance(groups, list) or not groups:
                return 0
            category_by_id = {
                int(item.id): str(item.category or "general")
                for item in preferences
                if int(item.id or 0) > 0
            }
            allowed_ids = set(category_by_id)
            claimed_ids: set[int] = set()
            sanitized = []
            for raw in groups[:40]:
                if not isinstance(raw, dict):
                    continue
                try:
                    canonical_id = int(raw.get("canonical_id") or 0)
                except (TypeError, ValueError):
                    continue
                if canonical_id not in allowed_ids or canonical_id in claimed_ids:
                    continue
                merge_ids = []
                for value in raw.get("merge_ids") or []:
                    try:
                        preference_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if (
                        preference_id not in allowed_ids
                        or preference_id == canonical_id
                        or category_by_id[preference_id]
                        != category_by_id[canonical_id]
                        or preference_id in claimed_ids
                        or preference_id in merge_ids
                    ):
                        continue
                    merge_ids.append(preference_id)
                if not merge_ids:
                    continue
                sanitized.append(
                    {
                        "canonical_id": canonical_id,
                        "merge_ids": merge_ids,
                        "content": _compact(raw.get("content"), 240),
                        "evidence": _compact(raw.get("evidence"), 240),
                    }
                )
                claimed_ids.add(canonical_id)
                claimed_ids.update(merge_ids)
            merged = await merger(sanitized)
            return len(merged or {})
        except Exception as exc:
            logger.debug(
                f"[日常生活] 生活偏好语义整理跳过：{type(exc).__name__}: {exc}"
            )
            return 0
        finally:
            await self._cleanup_conversation(session_id)

    def _compute_sleep_continuity(
        self,
        previous_day,
        day,
    ) -> tuple[float, float, float]:
        previous_debt = self._meta_float(
            previous_day.meta if previous_day else {}, "sleep_debt", 0.0
        )
        state = day.state
        sleep_quality = (
            state.sleep.quality if state and state.sleep.quality is not None else 65
        )
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
        previous_day = await self.archive.get_day(
            (date - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        )
        debt, delta, carryover = self._compute_sleep_continuity(previous_day, day)
        day.meta["sleep_debt"] = f"{debt:.2f}".rstrip("0").rstrip(".")
        day.meta["sleep_debt_delta"] = f"{delta:.2f}".rstrip("0").rstrip(".")
        day.meta["energy_carryover"] = f"{carryover:.1f}".rstrip("0").rstrip(".")
        life_decision = result.get("life_decision") if isinstance(result, dict) else {}
        if isinstance(life_decision, dict):
            event_seed = life_decision.get("life_event")
            event = (
                LifeEventRecord.from_value(event_seed, date=day.date, source="daily")
                if event_seed
                else None
            )
            if event:
                await self.archive.add_life_event(event)
        extract_anchors = getattr(self, "extract_schedule_anchors", None)
        if callable(extract_anchors):
            anchors = extract_anchors(day)
            day.meta["schedule_planning_mode"] = "hierarchical"
            day.meta["schedule_anchor_count"] = str(len(anchors))
        return day

    async def _build_lifecycle_context(
        self,
        date: datetime.datetime,
        *,
        exclude_daily_plan_date: str = "",
    ) -> str:
        sections = []
        previous_day = await self.archive.get_day(
            (date - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        )
        if previous_day:
            debt = self._meta_float(previous_day.meta or {}, "sleep_debt", 0.0)
            carryover = self._meta_float(
                previous_day.meta or {}, "energy_carryover", 60.0
            )
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
        preferences = (
            await self.archive.get_preferences(preference_limit)
            if preference_limit
            else []
        )
        preference_context = format_life_preference_context(
            preferences,
            self.config,
            limit=preference_limit,
        )
        if preference_context:
            sections.append("## 🧭 长期审美与生活偏好\n" + preference_context)

        reviews = await self.archive.get_recent_daily_reviews(3)
        if reviews:
            lines = [
                f"- {item.date}: {item.summary}" for item in reviews if item.summary
            ]
            if lines:
                sections.append("## 🌙 近期每日复盘\n" + "\n".join(lines))

        events = await self.archive.get_life_events(status="open", limit=8)
        if events:
            lines = [
                f"- {item.date or '未定'}｜{item.title}：{item.effect or item.detail or '等待自然影响'}"
                for item in events
            ]
            sections.append("## ✨ 生活事件池\n" + "\n".join(lines))

        episodes = await self.archive.get_life_episodes(limit=20)
        if exclude_daily_plan_date:
            episodes = [
                item
                for item in episodes
                if not (
                    item.date == exclude_daily_plan_date
                    and item.kind == "daily_plan"
                    and item.source == "daily"
                )
            ]
        episodes = episodes[:5]
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
            parts = [
                body,
                f"社交电量 {social}/100" if social is not None else "",
                lifecycle,
            ]
            text = "；".join(part for part in parts if part)
            if text:
                lines.append(f"- {date or '近期'}：{text}")
        return "\n".join(lines)

    def _build_daily_review_prompt(
        self,
        day,
        preferences: list[PreferenceRecord],
        life_events: list[LifeEventRecord],
        decisions: list[Any],
        feedback: list[Any],
        reply_effects: list[Any],
    ) -> str:
        state = day.state.as_dict() if day.state else {}
        pref_text = (
            "\n".join(
                f"- [{item.category}] {item.content} (权重 {item.weight:.1f})"
                for item in preferences[:12]
            )
            or "无"
        )
        event_text = (
            "\n".join(
                f"- [event_id={item.id}] {item.title}: {item.effect or item.detail}"
                for item in life_events[:8]
            )
            or "无"
        )
        fixed = f"""为当前角色的日常生活做夜间复盘与记忆沉淀。

通用记忆原则：
{CORE_MEMORY_RULES}

{json_output_section()}

返回结构：
{{
  "summary": "用第一人称‘我’写一句话，复盘今天的生活质感和状态变化",
  "memory_points": ["以后生成生活背景值得引用的稳定记忆"],
  "preference_points": [
    {{"category": "{LIFE_PREFERENCE_CATEGORY_ENUM}", "content": "可复用偏好", "weight": 0.1-2.0, "evidence": "来自今天哪件事"}}
  ],
  "sleep_debt_delta": -3.0 到 3.0,
  "energy_carryover": 0-100,
  "life_events": [
    {{"title": "新的生活事件", "detail": "事件细节", "effect": "未来几天可能怎样影响日程/穿搭/社交", "status": "open"}}
  ],
  "event_updates": [
    {{"event_id": 现有开放事件编号, "status": "open|completed|cancelled", "reason": "根据今天实际进展判断"}}
  ],
  "decision_outcomes": [
    {{"decision_id": 当天已有生活决策编号, "outcome": "根据实际时间轴和已结算互动得出的真实结果"}}
  ],
  "timeline_updates": [
    {{"item_index": 时间轴数组下标, "status": "completed|skipped|cancelled", "reason": "为什么这样收束", "evidence": "当天状态、互动或事件中的具体依据"}}
  ],
  "reflection_score": {{"novelty": 0.0-1.0, "emotional_intensity": 0.0-1.0, "goal_impact": 0.0-1.0, "social_impact": 0.0-1.0}},
  "reflection": {{
    "summary": "只有高价值时才填写的反思",
    "evidence_ids": ["只能引用下方证据清单里的完整编号"],
    "assertion": {{"subject": "可选主体", "predicate": "可选关系", "object": "可选结构化值"}}
  }},
  "affect_updates": [
    {{"layer": "transient|daily|relationship", "label": "自然情绪标签", "valence": -1.0-1.0, "arousal": 0.0-1.0, "intensity": 0.0-1.0, "evidence_ids": ["完整证据编号"], "source": "daily_review"}}
  ],
  "relationship_updates": [
    {{"profile_id": "已有关系档案编号", "familiarity_delta": -0.08-0.08, "trust_delta": -0.08-0.08, "affinity_delta": -0.08-0.08, "evidence_ids": ["完整证据编号"], "reason": "有证据的缓慢变化"}}
  ],
  "grounded_diary": {{"title": "简短标题", "summary": "用第一人称‘我’记录真实发生的内容", "evidence_ids": ["完整证据编号"], "mood_label": "当日心境"}}
}}

要求：
- preference_points 必须是稳定、可复用偏好；不确定就少写。
- 已学习偏好中已经存在同一含义时，不要换一种说法再次输出；一天内重复发生的同一习惯只更新证据，不产生多条偏好。
- life_events 是能自然延续几天的小事件，不要编造重大剧情。
- event_updates 只更新输入中已有的开放事件；仍会继续影响后续生活就保持 open，已经完成或取消时及时收束。
- decision_outcomes 只更新输入中已有的当天生活决策。它必须描述实际发生的结果，不能复述原计划或编造未发生事项。
- timeline_updates 只在有具体依据时把活动标成 skipped/cancelled；正常随时间推进的活动标成 completed，不得用关键词猜测执行结果。
- 根据 state.sleep、sleep_debt 和时间轴判断睡眠债增减；不要用固定文本匹配活动文字。
- reflection_score 必须按四个数值维度独立评分；低价值日常允许 reflection 留空，系统会在阈值以下跳过模型反思沉淀。
- affect_updates 和 relationship_updates 只能引用“可引用证据编号”，没有证据就返回空数组；关系数值只能小步变化。
- grounded_diary 必须使用第一人称“我”，只能写可引用证据明确支持的内容；证据不足就返回空对象。
- 不得从活动文案的词语、标点或固定句式猜测情绪、关系和执行结果。
"""
        evidence_lines = []
        for prefix, items in (
            ("event", life_events),
            ("decision", decisions),
            ("feedback", feedback),
            ("reply_effect", reply_effects),
        ):
            for item in items:
                item_id = int(getattr(item, "id", 0) or 0)
                if item_id > 0:
                    evidence_lines.append(f"- {prefix}:{item_id}")
        evidence_inventory = "\n".join(evidence_lines) or "无可引用证据"
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
{event_text}
当天生活决策：
{json.dumps([item.as_dict() for item in decisions], ensure_ascii=False)}
当天已结算行为反馈：
{json.dumps([item.as_dict() for item in feedback], ensure_ascii=False)}
近期已结算互动效果：
{json.dumps([item.as_dict() for item in reply_effects], ensure_ascii=False)}
可引用证据编号：
{evidence_inventory}"""
        return cache_friendly_prompt(fixed, dynamic, dynamic_title="今日复盘资料")

    def _fallback_daily_review(self, day) -> DailyReviewRecord:
        state_summary = day.state.summary if day.state and day.state.summary else ""
        first = day.timeline[0].activity if day.timeline else ""
        summary = state_summary or first or "我今天按自己的自然节奏生活。"
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

        existing = None
        if not force:
            existing = await self.archive.get_daily_review(date_str)
        day = await self.archive.get_day(date_str)
        if not day:
            return existing
        if existing and str(
            (day.meta or {}).get(DAILY_REVIEW_COMPLETED_AT) or ""
        ).strip():
            return existing

        preferences, events, decisions, feedback, reply_effects = await asyncio.gather(
            self.archive.get_preferences(12),
            self.archive.get_life_events(status="open", limit=8),
            self.archive.get_life_decisions(limit=8, date=date_str),
            self.archive.get_behavior_feedback(limit=12),
            self.archive.get_reply_effects(limit=12),
        )
        feedback = [
            item for item in feedback if str(getattr(item, "date", "")) == date_str
        ]
        reply_effects = [
            item
            for item in reply_effects
            if str(getattr(item, "created_at", ""))[:10] == date_str
            and str(getattr(item, "outcome", "")) != "pending"
        ]
        review = existing
        review_payload: dict[str, Any] = dict(existing.payload) if existing else {}
        if review is None or force:
            provider_id = self._task_provider_id(self.config.lifecycle.provider)
            provider = await self._get_provider(provider_id)
            review = None
            review_payload = {}
            session_id = f"daily_life_review_{uuid.uuid4().hex[:8]}"
            try:
                if provider:
                    text = await self._call_llm_text(
                        provider,
                        self._build_daily_review_prompt(
                            day, preferences, events, decisions, feedback, reply_effects
                        ),
                        session_id,
                        primary_provider_id=provider_id,
                    )
                    payload = extract_json_from_text(text)
                    if isinstance(payload, dict):
                        review_payload = payload
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
            review.payload = dict(review_payload)
            saved = await self.archive.save_daily_review(review)
        else:
            saved = review
        await self._apply_life_event_review_updates(
            events, review_payload, date_str=date_str
        )
        await self._apply_life_decision_review_outcomes(decisions, review_payload)
        await self._apply_timeline_review_updates(day, review_payload)
        evolution = LifeEvolutionService(self.archive)
        await evolution.settle_review(
            review_payload,
            date=date_str,
            events=events,
            decisions=decisions,
            feedback=feedback,
            reply_effects=reply_effects,
            now=life_now(),
        )

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
        if await self.maintain_preference_consistency(force=True):
            refreshed = await self.archive.get_daily_review(date_str)
            if refreshed:
                saved = refreshed
        await self._mark_daily_review_completed(date_str)
        return saved

    async def _mark_daily_review_completed(self, date_str: str) -> None:
        completed_at = (
            datetime.datetime.strptime(date_str, "%Y-%m-%d")
            + datetime.timedelta(days=1)
        ).strftime("%Y-%m-%d %H:%M:%S")

        def mark_completed(day) -> bool:
            if str(
                (day.meta or {}).get(DAILY_REVIEW_COMPLETED_AT) or ""
            ).strip() == completed_at:
                return False
            day.meta[DAILY_REVIEW_COMPLETED_AT] = completed_at
            return True

        await self.archive.mutate_day(date_str, mark_completed)

    async def _apply_timeline_review_updates(
        self, day, payload: dict[str, Any]
    ) -> None:
        raw_items = payload.get("timeline_updates") if isinstance(payload, dict) else []
        updated_at = f"{day.date} 23:59"
        review_end = datetime.datetime.strptime(
            day.date, "%Y-%m-%d"
        ) + datetime.timedelta(days=1)

        def settle_timeline(current_day) -> bool:
            before = [item.as_dict() for item in current_day.timeline]
            if isinstance(raw_items, list):
                for raw in raw_items[: len(current_day.timeline)]:
                    if not isinstance(raw, dict):
                        continue
                    try:
                        item_index = int(raw.get("item_index"))
                    except (TypeError, ValueError):
                        continue
                    status = str(raw.get("status") or "").strip().lower()
                    reason = _compact(raw.get("reason"), 160)
                    evidence = _compact(raw.get("evidence"), 200)
                    if not (
                        0 <= item_index < len(current_day.timeline)
                    ) or status not in {
                        "completed",
                        "skipped",
                        "cancelled",
                    }:
                        continue
                    if status in {"skipped", "cancelled"} and not evidence:
                        continue
                    item = current_day.timeline[item_index]
                    if item.execution_state in TIMELINE_TERMINAL_STATES:
                        continue
                    item.execution_state = status
                    item.execution_reason = reason or "夜间复盘收束"
                    item.execution_evidence = evidence or "夜间复盘"
                    item.execution_updated_at = updated_at

            reconcile_timeline_execution(
                current_day.timeline,
                review_end,
                current_day.date,
                evidence="夜间复盘：时间轴收束",
            )
            return before != [item.as_dict() for item in current_day.timeline]

        settled_day = await self.archive.mutate_day(day.date, settle_timeline)
        if settled_day is None:
            return

        settle_actions = getattr(self, "settle_completed_planned_actions", None)
        if callable(settle_actions):
            settled_at = review_end.strftime("%Y-%m-%d %H:%M:%S")
            for attempt in range(DAILY_REVIEW_REVISION_ATTEMPTS):
                try:
                    await settle_actions(settled_day, now=review_end)
                    break
                except DayRevisionConflict:
                    if attempt >= DAILY_REVIEW_REVISION_ATTEMPTS - 1:
                        raise
                    await asyncio.sleep(min(0.05 * (2**attempt), 0.4))
                    latest = await self.archive.get_day(day.date)
                    if latest is None:
                        return
                    if str(
                        (latest.meta or {}).get(DAILY_REVIEW_TIMELINE_SETTLED_AT)
                        or ""
                    ).strip() == settled_at:
                        return
                    settled_day = latest

        def mark_settled(current_day) -> bool:
            settled_at = review_end.strftime("%Y-%m-%d %H:%M:%S")
            if str(
                (current_day.meta or {}).get(DAILY_REVIEW_TIMELINE_SETTLED_AT) or ""
            ).strip() == settled_at:
                return False
            current_day.meta[DAILY_REVIEW_TIMELINE_SETTLED_AT] = settled_at
            return True

        await self.archive.mutate_day(day.date, mark_settled)

    async def _apply_life_decision_review_outcomes(
        self, decisions: list[Any], payload: dict[str, Any]
    ) -> None:
        updater = getattr(self.archive, "update_life_decision_outcome", None)
        raw_items = (
            payload.get("decision_outcomes") if isinstance(payload, dict) else None
        )
        if not callable(updater) or not isinstance(raw_items, list):
            return
        allowed = {int(getattr(item, "id", 0) or 0) for item in decisions}
        for item in raw_items[:8]:
            if not isinstance(item, dict):
                continue
            try:
                decision_id = int(item.get("decision_id") or 0)
            except (TypeError, ValueError):
                continue
            outcome = str(item.get("outcome") or "").strip()
            if decision_id in allowed and outcome:
                await updater(decision_id, outcome[:300])

    async def _apply_life_event_review_updates(
        self,
        open_events: list[LifeEventRecord],
        payload: dict,
        *,
        date_str: str,
    ) -> None:
        allowed_ids = {int(item.id) for item in open_events if int(item.id or 0) > 0}
        updates = payload.get("event_updates") if isinstance(payload, dict) else []
        if isinstance(updates, list):
            for raw in updates[: len(allowed_ids)]:
                if not isinstance(raw, dict):
                    continue
                try:
                    event_id = int(raw.get("event_id") or 0)
                except (TypeError, ValueError):
                    continue
                status = str(raw.get("status") or "").strip().lower()
                if event_id not in allowed_ids or status not in {
                    "open",
                    "completed",
                    "cancelled",
                }:
                    continue
                await self.archive.set_life_event_status(event_id, status)
        try:
            review_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return
        cutoff = (review_date - datetime.timedelta(days=14)).isoformat()
        await self.archive.close_stale_life_events(cutoff)

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
