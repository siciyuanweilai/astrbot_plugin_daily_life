from __future__ import annotations

import datetime
import json
import uuid

from astrbot.api import logger

from ..clock import now as life_now
from ..archive.timeline import rebind_planned_actions
from ..models import DayRecord, TimelineItem, WeatherInfo, normalize_timeline_day_offsets
from .appearance import current_appearance_values, persona_appearance_values
from .calendar import format_calendar_context, format_season_context
from .dressing import wardrobe_conditions
from .people import DAILY_PERSON_TEXT_PATHS
from .tools import (
    analyze_weather,
    build_time_context,
    extract_json_from_text,
    parse_schedule_time,
    resolve_daily_hint,
    resolve_daily_suggested,
    timeline_item_datetime,
)
from .wardrobe import (
    format_outfit_components,
    normalize_outfit_components,
    normalize_outfit_decision,
    normalize_outfit_scene_category,
    outfit_action_components,
    outfit_action_issue,
    reconcile_outfit_components_for_scene,
    resolve_outfit_style_pool,
    scene_category_for_place_kind,
    serialize_outfit_components,
)

from typing import Any, Protocol

_CURRENT_APPEARANCE_META_KEYS = (
    "outfit_decision",
    "outfit_scene_category",
    "outfit_style_pool",
    "outfit_components",
    "style_catalog_reference_ids",
    "outing_outfit_reserve",
    "style",
    "hair_style",
    "hair",
    "makeup_style",
    "makeup",
    "nails_style",
    "nails",
    "outfit_reason",
    "style_usage_context",
)

_PLANNED_APPEARANCE_META_KEYS = {
    "outfit_scene_category": "plan_outfit_scene_category",
    "outfit_style_pool": "plan_outfit_style_pool",
    "outfit_components": "plan_outfit_components",
    "style": "plan_outfit_style",
    "hair_style": "plan_hair_style",
    "hair": "plan_hair",
    "makeup_style": "plan_makeup_style",
    "makeup": "plan_makeup",
    "nails_style": "plan_nails_style",
    "nails": "plan_nails",
    "outfit_reason": "plan_outfit_reason",
}


class DailyEngineComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _action_timeline_index: Any
    _append_daily_location_context: Any
    _apply_lifecycle_to_day: Any
    _audit_person_payload: Any
    _audit_persona_appearance: Any
    _build_autonomous_life_context: Any
    _build_daily_replacement_context: Any
    _build_history_schedule_summary: Any
    _build_life_inertia_context: Any
    _build_lifecycle_context: Any
    _build_person_fact_context: Any
    _build_previous_life_context: Any
    _build_repair_prompt: Any
    _build_timeline_prompt: Any
    _build_weather_sections: Any
    _build_world_context: Any
    _call_llm_text: Any
    _classify_weather_condition: Any
    _cleanup_conversation: Any
    _collect_recent_chat_context: Any
    _daily_decision_outcome: Any
    _daily_decision_text: Any
    _day_from_generation: Any
    _ensure_week_plan: Any
    _format_commitment_prompt: Any
    _gen_lock: Any
    _generation_provider_id: Any
    _get_persona: Any
    _get_provider: Any
    _last_validation_issue_code: Any
    _mark_style_catalog_references: Any
    _meta_from_generation: Any
    _normalize_extra: Any
    _persist_generated_day: Any
    _prepare_daily_location_context: Any
    _repeat_generation_issue: Any
    _save_life_decision_record: Any
    _set_validation_issue: Any
    _style_catalog_context: Any
    _style_catalog_new_outfit_selection: Any
    _style_catalog_reference_appearance: Any
    _style_catalog_reference_ids: Any
    _style_catalog_resolve_new_outfit_reference_ids: Any
    _validate_daily_payload: Any
    archive: Any
    config: Any
    domains: Any
    weather_client: Any


async def _resolve_planned_outfit_catalog(
    host: DailyEngineComponentContext,
    result: dict,
    *,
    manual_extra: bool,
    conditions: dict | None = None,
) -> str:
    """校验并展开计划换装的衣橱引用；不写数据库。"""
    for raw_action in result.get("planned_actions") or []:
        if not isinstance(raw_action, dict):
            continue
        if str(raw_action.get("action_type") or "").strip() != "change_outfit":
            continue
        payload = (
            raw_action.get("payload")
            if isinstance(raw_action.get("payload"), dict)
            else {}
        )
        action_reference_ids = host._style_catalog_reference_ids(
            payload.get("catalog_reference_ids")
        )
        timeline_index = host._action_timeline_index(raw_action)
        timeline = result.get("timeline")
        timeline_item = (
            timeline[timeline_index]
            if isinstance(timeline, list)
            and timeline_index is not None
            and 0 <= timeline_index < len(timeline)
            and isinstance(timeline[timeline_index], dict)
            else {}
        )
        place_kind = str(timeline_item.get("place_kind") or "").strip()
        action_scene_category = normalize_outfit_scene_category(
            payload.get("scene_category"),
            default=scene_category_for_place_kind(place_kind),
        )
        action_components = normalize_outfit_components(
            payload.get("outfit_components")
        )
        current_components = normalize_outfit_components(
            result.get("outfit_components")
        )
        _, full_change = outfit_action_components(
            current_components, action_components, str(raw_action.get("target") or "")
        )
        # 计划中的未知状态仍需由衣橱补全，不能据此认定主体衣物没有变化。
        main = action_components.get("main_clothing") or {}
        full_change = full_change or bool(
            main.get("state") == "unknown"
            and main.get("description")
            and main.get("description")
            != (current_components.get("main_clothing") or {}).get("description")
        )
        partial_action = bool(action_components and not full_change)
        if manual_extra or partial_action:
            action_appearance = await host._style_catalog_reference_appearance(
                action_reference_ids,
                scene_category=(
                    "" if manual_extra else action_scene_category
                ),
            )
            if partial_action:
                action_appearance.pop("outfit", None)
                partial_action_components = normalize_outfit_components(
                    action_appearance.get("outfit_components")
                )
                partial_action_components.pop("main_clothing", None)
                if partial_action_components:
                    action_appearance["outfit_components"] = (
                        serialize_outfit_components(partial_action_components)
                    )
                else:
                    action_appearance.pop("outfit_components", None)
            action_issue = ""
        else:
            selection_kwargs = {"scene_category": action_scene_category}
            if conditions is not None:
                selection_kwargs["conditions"] = conditions
            (
                action_appearance,
                action_issue,
            ) = await host._style_catalog_new_outfit_selection(
                action_reference_ids,
                **selection_kwargs,
            )
        if action_issue:
            host._set_validation_issue("style_catalog_required")
            return f"计划换装未采用衣橱候选：{action_issue}"
        component_ledger = serialize_outfit_components(
            reconcile_outfit_components_for_scene(
                payload.get("outfit_components"),
                action_scene_category,
                catalog_components=action_appearance.get("outfit_components"),
                catalog_selected=bool(
                    action_reference_ids and action_appearance.get("outfit")
                ),
            )
        )
        component_issue = outfit_action_issue(component_ledger)
        if component_issue:
            host._set_validation_issue("style_catalog_required")
            return component_issue
        if component_ledger:
            payload["outfit_components"] = component_ledger
        payload["scene_category"] = action_scene_category
        payload["catalog_reference_ids"] = action_reference_ids
        for fields in (
            ("style",), ("hair_style", "hair"),
            ("makeup_style", "makeup"), ("nails_style", "nails"),
        ):
            if any(field in payload for field in fields):
                continue
            for field in fields:
                if action_appearance.get(field):
                    payload[field] = action_appearance[field]
        if action_appearance.get("outfit"):
            action_visible_outfit = format_outfit_components(
                json.loads(component_ledger) if component_ledger else {}
            )
            raw_action["target"] = (
                action_visible_outfit
                if action_visible_outfit
                else action_appearance["outfit"]
            )
            reserve = str(action_appearance.get("outing_reserve") or "").strip()
            if reserve:
                payload["outing_outfit_reserve"] = reserve
        raw_action["payload"] = payload
    return ""


class DailyEngineComponent:
    def __init__(self, context: DailyEngineComponentContext) -> None:
        self.context = context

    @staticmethod
    def _normalize_extra(extra: str | None) -> str:
        return str(extra or "").strip()

    def _daily_generation_check_time(
        self,
        date: datetime.datetime,
        *,
        target_hour: int | None = None,
    ) -> datetime.datetime:
        if target_hour is not None:
            return date.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        hour, minute = parse_schedule_time(
            getattr(self.context.config, "schedule_time", "07:00")
        )
        boundary = date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if date < boundary:
            return boundary
        return date.replace(second=0, microsecond=0)

    async def _daily_generation_context(
        self,
        date: datetime.datetime,
        *,
        target_hour: int | None = None,
        extra: str | None = None,
        web_inspiration: str = "",
        regenerate_existing: bool = False,
    ) -> dict:
        date_str = date.strftime("%Y-%m-%d")
        check_time = self._daily_generation_check_time(date, target_hour=target_hour)
        time_context = build_time_context(
            check_time,
            getattr(self.context.config, "schedule_time", "07:00")
            if target_hour is None
            else "",
        )
        current_minutes = time_context.minute_of_day
        period = time_context.period
        period_cn = time_context.period_cn

        persona = await self.context._get_persona()
        city_resolver = getattr(self.context.domains, "resolve_weather_city", None)
        city = await city_resolver() if callable(city_resolver) else ""
        weather_data = (
            await self.context.weather_client.get_weather(city)
            if city
            else "居住地尚未配置或无法通过高德确认现实城市，天气不可用"
        )
        weather_info = analyze_weather(await self.context._classify_weather_condition(weather_data))
        weather_section, constraint_section = self.context._build_weather_sections(weather_info)
        calendar_context = format_calendar_context(date)
        season_context = format_season_context(date)
        week_plan = await self.context._ensure_week_plan()
        today_hint = resolve_daily_hint(week_plan, date)
        today_suggested = resolve_daily_suggested(week_plan, date)

        history_schedules_str = await self.context._build_history_schedule_summary(date)
        previous_context = await self.context._build_previous_life_context(
            date, include_outfit=False
        )
        recent_chats = await self.context._collect_recent_chat_context(persona)
        replaced_day = (
            await self.context.archive.get_day(date_str) if regenerate_existing else None
        )
        replacement_context = self.context._build_daily_replacement_context(replaced_day)
        person_facts = await self.context._build_person_fact_context(
            persona=persona,
            explicit_instruction=self.context._normalize_extra(extra),
        )
        memo_str = await self._daily_generation_memo_text(
            date_str, extra=extra, web_inspiration=web_inspiration
        )
        due_commitments = await self.context.archive.get_due_commitments(date_str)
        commitment_text = self.context._format_commitment_prompt(due_commitments)
        if commitment_text:
            memo_str += (
                "\n\n【已答应过的承诺/约定】\n"
                "这些不是普通灵感，而是未来约定池里到期的事项。"
                "请优先自然安排进 timeline、提醒或聊天延续，不要遗漏：\n"
                f"{commitment_text}"
            )

        world_context = await self.context._build_world_context(
            date,
            "自主生活决策",
            weather_info,
            [today_hint, today_suggested, memo_str, recent_chats],
        )
        domain_context_builder = getattr(
            getattr(self.context, "domains", None), "format_context", None
        )
        if callable(domain_context_builder):
            domain_context = await domain_context_builder()
            if domain_context:
                world_context = f"{world_context}\n\n{domain_context}".strip()
        wardrobe_day = await self.context.archive.get_day(date_str) or DayRecord(date=date_str)
        wardrobe_day.weather_info = WeatherInfo.from_value(weather_info)
        wardrobe_weather = wardrobe_conditions(wardrobe_day, check_time, residence=str(getattr(getattr(self.context.config, 'domains', None), 'home_address', '') or ''))
        style_catalog_context = await self.context._style_catalog_context(limit=14, conditions=wardrobe_weather)
        if style_catalog_context:
            world_context = f"{world_context}\n\n{style_catalog_context}".strip()
        prompt = self.context._build_timeline_prompt(
            date_str,
            period_cn,
            weather_section,
            constraint_section,
            await self.context._build_life_inertia_context(date),
            previous_context,
            history_schedules_str,
            memo_str,
            calendar_context=calendar_context,
            season_context=season_context,
            persona=persona,
            week_plan=week_plan,
            today_hint=today_hint,
            today_suggested=today_suggested,
            recent_chats=recent_chats,
            schedule_intent="由 LLM 自主决定",
            world_context=world_context,
            lifecycle_context=await self.context._build_lifecycle_context(
                date,
                exclude_daily_plan_date=date_str if regenerate_existing else "",
            ),
            autonomy_context=await self.context._build_autonomous_life_context(date),
            person_fact_context=person_facts.format_for_generation(),
            replacement_context=replacement_context,
            expected_coverage="target_period"
            if target_hour is not None
            else "full_day",
            current_time_text=check_time.strftime("%Y-%m-%d %H:%M"),
            temporal_context_text=time_context.prompt_text(),
        )
        return {
            "date_str": date_str,
            "check_time": check_time,
            "current_minutes": current_minutes,
            "period": period,
            "period_cn": period_cn,
            "time_context": time_context,
            "weather_info": weather_info,
            "weather_str_for_prompt": weather_info["raw"],
            "calendar_context": calendar_context,
            "season_context": season_context,
            "week_plan": week_plan,
            "today_hint": today_hint,
            "today_suggested": today_suggested,
            "memo_str": memo_str,
            "recent_chats": recent_chats,
            "world_context": world_context,
            "style_catalog_context": style_catalog_context,
            "wardrobe_conditions": wardrobe_weather,
            "due_commitments": due_commitments,
            "prompt": prompt,
            "manual_extra": self.context._normalize_extra(extra),
            "web_inspiration": self.context._normalize_extra(web_inspiration),
            "expected_coverage": "target_period"
            if target_hour is not None
            else "full_day",
            "person_facts": person_facts,
            "persona": str(persona or "").strip(),
            "replaced_day": replaced_day,
        }

    @staticmethod
    def _build_daily_replacement_context(day) -> str:
        if day is None:
            return ""
        meta = getattr(day, "meta", {}) or {}
        old_outfit = str(getattr(day, "outfit", "") or "").strip()
        old_style = str(meta.get("style") or "").strip()
        return (
            "## ♻️ 同日重生成边界\n"
            "本次替换同一天尚未执行的日程草稿，已发生的活动和当前穿着仍以真实回执为准。"
            "结合本次要求重排计划，是否换衣由天气、活动、可用衣物与实际穿着记录共同决定。\n"
            f"- 旧记录穿搭（是否已穿以回执为准）：{old_outfit or '未记录'}\n"
            f"- 旧记录风格：{old_style or '未记录'}"
        )

    async def _daily_generation_memo_text(
        self,
        date_str: str,
        *,
        extra: str | None = None,
        web_inspiration: str = "",
    ) -> str:
        today_data_temp = await self.context.archive.get_day(date_str)
        memo_str = today_data_temp.memo if today_data_temp else ""
        if extra:
            memo_str += f"\n- 用户实时指令：{extra}"
        web_inspiration = self.context._normalize_extra(web_inspiration)
        if web_inspiration:
            memo_str += (
                "\n\n【联网灵感参考】\n"
                "以下内容只用于给今日生活背景提供新鲜参考，不是必须执行的事项：\n"
                f"{web_inspiration}"
            )
        return memo_str

    async def _persist_daily_generation_success(
        self,
        result: dict,
        *,
        date: datetime.datetime,
        context: dict,
        provider=None,
        provider_id: str = "",
    ):
        decision = (
            result.get("life_decision")
            if isinstance(result.get("life_decision"), dict)
            else {}
        )
        outfit_decision = (
            decision.get("outfit") if isinstance(decision.get("outfit"), dict) else {}
        )
        outfit_choice = normalize_outfit_decision(outfit_decision.get("decision"))
        current_scene_category = str(
            outfit_decision.get("scene_category") or ""
        ).strip()
        check_time = context.get("check_time")
        timeline = result.get("timeline")
        if isinstance(timeline, list) and check_time is not None:
            normalize_timeline_day_offsets(timeline)
            occurred = []
            for item in timeline:
                if not isinstance(item, dict):
                    continue
                item_time = timeline_item_datetime(item, context.get("date_str"))
                if item_time is not None and item_time <= check_time:
                    occurred.append(item)
            if occurred:
                current_scene_category = scene_category_for_place_kind(
                    occurred[-1].get("place_kind"), default=current_scene_category
                )
        current_reference_ids = self.context._style_catalog_reference_ids(
            outfit_decision.get("catalog_reference_ids")
        )
        full_outfit_change = outfit_choice in {"change", "sleepwear", "outdoor"}
        if full_outfit_change and not context["manual_extra"]:
            current_reference_ids = (
                await self.context._style_catalog_resolve_new_outfit_reference_ids(
                    current_reference_ids,
                    scene_category=current_scene_category,
                    conditions=context.get("wardrobe_conditions"),
                )
            )
            # 将自动修复后的编号回写到结构化结果，确保日程元数据和后续
            # 穿搭连续性记录的引用与实际采用的候选一致。
            outfit_decision["catalog_reference_ids"] = current_reference_ids
            (
                catalog_appearance,
                catalog_issue,
            ) = await self.context._style_catalog_new_outfit_selection(
                current_reference_ids,
                scene_category=current_scene_category,
                conditions=context.get("wardrobe_conditions"),
            )
            if catalog_issue:
                self.context._set_validation_issue("style_catalog_required")
                return None, catalog_issue
        else:
            catalog_appearance = await self.context._style_catalog_reference_appearance(
                current_reference_ids,
                scene_category=""
                if context["manual_extra"]
                else current_scene_category,
            )
            if outfit_choice == "partial_change":
                    # 局部调整可以采用明确指定的鞋履、外搭或配饰，
                    # 但必须保留当前主体服装。
                catalog_appearance.pop("outfit", None)
                partial_components = normalize_outfit_components(
                    catalog_appearance.get("outfit_components")
                )
                partial_components.pop("main_clothing", None)
                if partial_components:
                    catalog_appearance["outfit_components"] = (
                        serialize_outfit_components(partial_components)
                    )
                else:
                    catalog_appearance.pop("outfit_components", None)

        planned_issue = await _resolve_planned_outfit_catalog(
            self.context,
            result,
            manual_extra=context["manual_extra"],
            conditions=context.get("wardrobe_conditions"),
        )
        if planned_issue:
            return None, planned_issue

        day = self.context._day_from_generation(
            result,
            date_str=context["date_str"],
            period=context["period"],
            weather_str=context["weather_str_for_prompt"],
            weather_info=context["weather_info"],
            meta=self.context._meta_from_generation(result),
            memo="",
        )
        if current_scene_category:
            day.meta["outfit_scene_category"] = normalize_outfit_scene_category(
                current_scene_category
            )
            day.meta["outfit_style_pool"] = resolve_outfit_style_pool(
                current_scene_category,
                decision=outfit_choice,
                requested=outfit_decision.get("style_pool"),
            )
        catalog_outfit = catalog_appearance.pop("outfit", "")
        outing_reserve = catalog_appearance.pop("outing_reserve", "")
        day_components = reconcile_outfit_components_for_scene(
            day.meta.get("outfit_components"),
            current_scene_category,
            catalog_components=catalog_appearance.get("outfit_components"),
            catalog_selected=bool(
                current_reference_ids and catalog_outfit and not context["manual_extra"]
            ),
        )
        if day_components:
            day.meta["outfit_components"] = serialize_outfit_components(day_components)
            visible_outfit = format_outfit_components(day_components)
            if visible_outfit:
                day.outfit = visible_outfit
        if not day_components and catalog_outfit and outfit_choice != "keep":
            day.outfit = catalog_outfit
        if outing_reserve:
            day.meta["outing_outfit_reserve"] = outing_reserve
        else:
            day.meta.pop("outing_outfit_reserve", None)
        for key, value in catalog_appearance.items():
            if key == "outfit_components":
                if not str(day.meta.get(key) or "").strip():
                    day.meta[key] = serialize_outfit_components(value)
            elif not str(day.meta.get(key) or "").strip():
                day.meta[key] = value
        repeat_issue = await self.context._repeat_generation_issue(
            day,
            date,
            result,
            manual_extra=context["manual_extra"],
            replaced_day=context.get("replaced_day"),
        )
        if repeat_issue:
            return None, repeat_issue

        logger.debug("[日程生成] 成功解析结构化数据")
        day = await self._ground_generated_current_appearance(day, context=context)
        day = self._preserve_regenerated_actual_timeline(
            day, context.get("replaced_day"), context.get("check_time")
        )
        # 只在旧记录被标为用户确认、且本轮会续接它时做二次审计。
        # 普通首轮生成仍由主日程提示词完成，避免为每次生成增加模型调用。
        appearance_audit_required = bool(
            context.get("replaced_day") is not None
            and str(day.meta.get("outfit_fact_source") or "").strip()
            == "user_instruction"
        )
        if appearance_audit_required:
            slots = {
                "current": persona_appearance_values(day.meta),
            }
            planned = persona_appearance_values(
                {
                    "hair_style": day.meta.get("plan_hair_style"),
                    "hair": day.meta.get("plan_hair"),
                    "makeup_style": day.meta.get("plan_makeup_style"),
                    "makeup": day.meta.get("plan_makeup"),
                    "nails_style": day.meta.get("plan_nails_style"),
                    "nails": day.meta.get("plan_nails"),
                }
            )
            if any(planned.values()):
                slots["planned"] = planned
            audited = await self.context._audit_persona_appearance(
                slots,
                persona=context.get("persona", ""),
                original_instruction=context.get("manual_extra", ""),
                provider=provider,
                provider_id=provider_id,
                subject="日程重生与当前外观续接",
            )
            for key, value in audited.get("current", {}).items():
                if value:
                    day.meta[key] = value
                else:
                    day.meta.pop(key, None)
            planned_keys = {
                "hair_style": "plan_hair_style",
                "hair": "plan_hair",
                "makeup_style": "plan_makeup_style",
                "makeup": "plan_makeup",
                "nails_style": "plan_nails_style",
                "nails": "plan_nails",
            }
            for key, value in audited.get("planned", {}).items():
                plan_key = planned_keys[key]
                if value:
                    day.meta[plan_key] = value
                else:
                    day.meta.pop(plan_key, None)
        day = await self.context._apply_lifecycle_to_day(day, date, result)
        await self.context._persist_generated_day(
            context["date_str"], day, context["due_commitments"]
        )
        await self.context._mark_style_catalog_references(current_reference_ids)
        if current_reference_ids:
            logger.debug(
                "[日程生成] 当前穿搭已采用视觉衣橱候选："
                + ",".join(str(item) for item in current_reference_ids)
            )
        decision_text, decision_reason, decision_evidence = self.context._daily_decision_text(
            result, day
        )
        await self.context._save_life_decision_record(
            kind="daily_plan",
            date=context["date_str"],
            subject=context["date_str"],
            decision=decision_text,
            reason=decision_reason,
            evidence=decision_evidence,
            outcome=self.context._daily_decision_outcome(result),
        )
        logger.debug(
            f"[日程生成] 生成成功：{context['date_str']}（{context['period_cn']}），时间轴节点数：{len(day.timeline)}"
        )
        return day, ""

    async def _ground_generated_current_appearance(self, day, *, context: dict):
        """避免把尚未发生的日程穿搭提前保存为当前穿搭。"""

        check_time = context["check_time"]
        replaced_day = context.get("replaced_day")
        if replaced_day is not None:
            previous_meta = getattr(replaced_day, "meta", {}) or {}
            previous_outfit = current_appearance_values(replaced_day)["outfit"]
            previous_confirmed_at = str(
                previous_meta.get("outfit_fact_confirmed_at") or ""
            ).strip()
            previous_source = str(previous_meta.get("outfit_fact_source") or "").strip()
            previous_has_actual_node = any(
                getattr(item, "execution_state", "") in {"active", "completed"}
                for item in (getattr(replaced_day, "timeline", []) or [])
            )
            if previous_outfit and (
                previous_confirmed_at
                or previous_source in {"user_instruction", "action_receipt", "continuous_life"}
                or previous_has_actual_node
            ):
                day.meta["plan_outfit"] = day.outfit
                for current_key, plan_key in _PLANNED_APPEARANCE_META_KEYS.items():
                    planned_value = str(day.meta.get(current_key) or "").strip()
                    if planned_value:
                        day.meta[plan_key] = planned_value
                for key in _CURRENT_APPEARANCE_META_KEYS:
                    if key in previous_meta:
                        day.meta[key] = previous_meta[key]
                    else:
                        day.meta.pop(key, None)
                day.outfit = previous_outfit
                day.outfit_history = {
                    **dict(getattr(day, "outfit_history", {}) or {}),
                    **dict(getattr(replaced_day, "outfit_history", {}) or {}),
                }
                for key in (
                    "current_place",
                    "place_fact_at",
                    "place_fact_action_id",
                    "previous_place",
                    "outfit_fact_source",
                    "outfit_fact_confirmed_at",
                    "outfit_fact_evidence",
                    "outfit_carried_from",
                ):
                    if key in previous_meta:
                        day.meta[key] = previous_meta[key]
                    else:
                        day.meta.pop(key, None)
                return day
        has_occurred_timeline = any(
            item_time is not None and item_time <= check_time
            for item in day.timeline
            if (item_time := timeline_item_datetime(item, day.date)) is not None
        )
        confirmed_at = check_time.strftime("%Y-%m-%d %H:%M:%S")
        previous_date = (check_time.date() - datetime.timedelta(days=1)).isoformat()
        previous_day = await self.context.archive.get_day(previous_date)
        if previous_day is not None:
            # 新日程中的仪容是计划；跨日延续实际使用，直到仪容动作结算。
            for kind in ("hair", "makeup", "nails"):
                for key in (kind, kind + "_style"):
                    if day.meta.get(key):
                        day.meta["plan_" + key] = day.meta[key]
                    if previous_day.meta.get(key):
                        day.meta[key] = previous_day.meta[key]
                    else:
                        day.meta.pop(key, None)
            if previous_day.meta.get("style_usage_context"):
                day.meta["style_usage_context"] = previous_day.meta["style_usage_context"]
        keep_previous = day.meta.get("plan_outfit_decision") == "keep"
        if has_occurred_timeline and not keep_previous:
            day.meta["outfit_fact_source"] = "daily_generation"
            day.meta["outfit_fact_confirmed_at"] = confirmed_at
            day.meta["outfit_fact_evidence"] = "当日日程已覆盖当前时刻"
            return day

        previous_outfit = current_appearance_values(previous_day)["outfit"]
        previous_outfit_period = "最终"
        if not previous_outfit:
            day.meta["outfit_fact_source"] = "daily_generation"
            day.meta["outfit_fact_confirmed_at"] = confirmed_at
            day.meta["outfit_fact_evidence"] = "无可延续穿搭，采用当日日程初始状态"
            return day

        previous_meta = getattr(previous_day, "meta", {}) or {}
        day.meta["plan_outfit"] = day.outfit
        for current_key, plan_key in _PLANNED_APPEARANCE_META_KEYS.items():
            planned_value = str(day.meta.get(current_key) or "").strip()
            if planned_value:
                day.meta.setdefault(plan_key, planned_value)
        for key in _CURRENT_APPEARANCE_META_KEYS:
            value = str(previous_meta.get(key) or "").strip()
            if value:
                day.meta[key] = value
            else:
                day.meta.pop(key, None)

        previous_source = str(previous_meta.get("outfit_fact_source") or "").strip()
        day.outfit = previous_outfit
        day.outfit_history = {context["period"]: previous_outfit}
        day.meta["outfit_fact_source"] = (
            "user_instruction"
            if previous_source == "user_instruction"
            else "carried_previous_day"
        )
        day.meta["outfit_fact_confirmed_at"] = str(
            previous_meta.get("outfit_fact_confirmed_at") or confirmed_at
        ).strip()
        day.meta["outfit_fact_evidence"] = (
            f"延续 {previous_date} {previous_outfit_period or '白天'}记录中尚未被已发生换装替代的穿搭"
        )
        day.meta["outfit_carried_from"] = previous_date
        logger.debug("[日程生成] 首个日程节点尚未发生，当前穿搭延续上一日记录")
        return day

    @staticmethod
    def _preserve_regenerated_actual_timeline(day, previous_day, check_time):
        """重生成当日计划时只保留有执行证据的旧节点，避免把计划改写成事实。"""
        if previous_day is None or check_time is None:
            return day
        previous_items = [
            TimelineItem.from_value(item)
            for item in (getattr(previous_day, "timeline", []) or [])
        ]
        generated_items = [
            TimelineItem.from_value(item) for item in (getattr(day, "timeline", []) or [])
        ]
        actual_items = []
        for item in previous_items:
            item_time = timeline_item_datetime(item, day.date, meta=getattr(previous_day, "meta", None))
            if item.execution_state == "active" or (
                item.execution_state == "completed"
                and item_time is not None
                and item_time <= check_time
            ):
                actual_items.append(item)

        def item_key(item):
            item_time = timeline_item_datetime(item, day.date, meta=getattr(day, "meta", None))
            return (
                item_time.isoformat() if item_time else item.time,
                item.activity,
                item.place,
                item.place_kind,
            )

        actual_keys = {item_key(item) for item in actual_items}
        future_items = []
        for item in generated_items:
            item_time = timeline_item_datetime(item, day.date, meta=getattr(day, "meta", None))
            if item_key(item) in actual_keys:
                continue
            if item_time is None or item_time > check_time:
                future_items.append(item)
        merged = actual_items + future_items
        merged.sort(
            key=lambda item: (
                timeline_item_datetime(item, day.date, meta=getattr(day, "meta", None))
                or datetime.datetime.max
            )
        )
        day.timeline = merged
        old_actions = []
        try:
            old_raw_actions = (getattr(previous_day, "meta", {}) or {}).get(
                "planned_life_actions"
            )
            old_actions = json.loads(old_raw_actions) if old_raw_actions else []
        except (TypeError, ValueError, json.JSONDecodeError):
            old_actions = []
        raw_actions = day.meta.get("planned_life_actions")
        try:
            actions = json.loads(raw_actions) if raw_actions else []
        except (TypeError, ValueError, json.JSONDecodeError):
            actions = []
        if isinstance(actions, list):
            original_indexes = {
                str(action.get("action_id")): action.get("timeline_index")
                for action in actions
                if isinstance(action, dict)
            }
            rebind_planned_actions(day.meta, generated_items, merged)
            actions = json.loads(day.meta.get("planned_life_actions") or "[]")
            actions = [
                action
                for action in actions
                if not (
                    isinstance(action, dict)
                    and action.get("timeline_index") is None
                    and isinstance(original_indexes.get(str(action.get("action_id"))), int)
                )
            ]
            day.meta["planned_life_actions"] = json.dumps(
                actions,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        else:
            actions = []
        committed_ids = set()
        previous_settlements = str(
            (getattr(previous_day, "meta", {}) or {}).get("life_action_settlements") or ""
        )
        if previous_settlements:
            try:
                old_settlements = json.loads(previous_settlements)
            except (TypeError, ValueError, json.JSONDecodeError):
                old_settlements = {}
            if isinstance(old_settlements, dict):
                try:
                    new_settlements = json.loads(day.meta.get("life_action_settlements") or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    new_settlements = {}
                if not isinstance(new_settlements, dict):
                    new_settlements = {}
                for action_id, outcome in old_settlements.items():
                    if isinstance(outcome, dict) and outcome.get("status") == "committed":
                        committed_ids.add(str(action_id))
                        new_settlements.setdefault(action_id, outcome)
                day.meta["life_action_settlements"] = json.dumps(
                    new_settlements, ensure_ascii=False, separators=(",", ":")
                )
        if isinstance(old_actions, list):
            known_ids = {
                str(action.get("action_id"))
                for action in actions
                if isinstance(action, dict) and action.get("action_id")
            }
            for old_action in old_actions:
                if not isinstance(old_action, dict):
                    continue
                action_id = str(old_action.get("action_id") or "")
                old_index = old_action.get("timeline_index")
                if (
                    not action_id
                    or action_id in known_ids
                    or not isinstance(old_index, int)
                    or not 0 <= old_index < len(previous_items)
                ):
                    continue
                if action_id not in committed_ids and previous_items[old_index].execution_state != "active":
                    continue
                old_key = item_key(previous_items[old_index])
                replacement = next(
                    (index for index, item in enumerate(merged) if item_key(item) == old_key),
                    None,
                )
                if replacement is None:
                    continue
                preserved_action = dict(old_action)
                preserved_action["timeline_index"] = replacement
                actions.append(preserved_action)
                known_ids.add(action_id)
            day.meta["planned_life_actions"] = json.dumps(
                actions, ensure_ascii=False, separators=(",", ":")
            )
        return day

    async def generate_daily(
        self,
        date=None,
        force=False,
        target_hour=None,
        extra=None,
        web_inspiration: str = "",
        regenerate_existing: bool = False,
    ):
        async with self.context._gen_lock:
            if date is None:
                date = life_now()
            date_str = date.strftime("%Y-%m-%d")

            if not force:
                existing = await self.context.archive.get_day(date_str)
                if existing:
                    return existing

            logger.debug(
                "[日程生成] 开始生成今日生活背景，依据近期记忆和生活惯性自主决策"
            )

            context = await self._daily_generation_context(
                date,
                target_hour=target_hour,
                extra=extra,
                web_inspiration=web_inspiration,
                regenerate_existing=regenerate_existing,
            )

            gen_session_id = f"daily_life_gen_{uuid.uuid4().hex[:8]}"
            location_session_id = f"{gen_session_id}_locations"
            try:
                provider_id = self.context._generation_provider_id()
                provider = await self.context._get_provider(provider_id)
                if not provider:
                    return None

                domain_service = getattr(self.context, "domains", None)
                location_auditor = getattr(
                    domain_service, "audit_daily_locations", None
                )
                map_tools_available = getattr(
                    domain_service, "map_tools_available", None
                )
                map_context_resolver = getattr(
                    domain_service, "ensure_map_context", None
                )
                map_context_ready = (
                    await map_context_resolver()
                    if callable(map_context_resolver)
                    else (
                        map_tools_available()
                        if callable(map_tools_available)
                        else False
                    )
                )
                location_validation_enabled = bool(
                    callable(location_auditor)
                    and callable(map_tools_available)
                    and map_context_ready
                    and map_tools_available()
                )
                location_context = ""
                preselected_places = []
                if location_validation_enabled:
                    logger.debug("[日程生成] 开始规划地点意图并通过地图预选候选……")
                    (
                        location_context,
                        preselected_places,
                    ) = await self.context._prepare_daily_location_context(
                        context=context,
                        provider=provider,
                        provider_id=provider_id,
                        session_id=location_session_id,
                    )
                logger.debug("[日程生成] 开始调用大语言模型生成最终日程……")
                current_prompt = self.context._append_daily_location_context(
                    context["prompt"], location_context
                )
                max_attempts = (
                    3
                    if context["manual_extra"]
                    or context["web_inspiration"]
                    or location_validation_enabled
                    else 2
                )
                for attempt in range(max_attempts):
                    completion_text = await self.context._call_llm_text(
                        provider,
                        current_prompt,
                        gen_session_id,
                        primary_provider_id=provider_id,
                    )
                    if not completion_text:
                        logger.error(
                            f"[日程生成] 大语言模型返回为空或失败（第 {attempt + 1} 次）"
                        )
                        continue

                    result = extract_json_from_text(completion_text)
                    ok, reason = self.context._validate_daily_payload(
                        result,
                        context["manual_extra"],
                        expected_coverage=context["expected_coverage"],
                        current_minutes=context["current_minutes"],
                    )
                    if ok:
                        audit = await self.context._audit_person_payload(
                            result,
                            context=context["person_facts"],
                            patterns=DAILY_PERSON_TEXT_PATHS,
                            provider=provider,
                            provider_id=provider_id,
                            subject="日程与事件记录",
                        )
                        if audit.unresolved:
                            ok = False
                            reason = audit.reason
                            self.context._set_validation_issue("person_fact_conflict")
                        else:
                            result = audit.payload
                            ok, reason = self.context._validate_daily_payload(
                                result,
                                context["manual_extra"],
                                expected_coverage=context["expected_coverage"],
                                current_minutes=context["current_minutes"],
                            )
                    if ok:
                        if callable(location_auditor):
                            audit_kwargs = {
                                "allow_safe_corrections": True,
                                "weather_info": context["weather_info"],
                            }
                            if preselected_places:
                                audit_kwargs["preselected_places"] = preselected_places
                            result, location_reason = await location_auditor(
                                result, **audit_kwargs
                            )
                            if location_reason:
                                ok = False
                                reason = location_reason
                                self.context._set_validation_issue("location_audit_invalid")
                            else:
                                ok, reason = self.context._validate_daily_payload(
                                    result,
                                    context["manual_extra"],
                                    expected_coverage=context["expected_coverage"],
                                    current_minutes=context["current_minutes"],
                                )
                    if ok:
                        (
                            day,
                            repeat_issue,
                        ) = await self._persist_daily_generation_success(
                            result,
                            date=date,
                            context=context,
                            provider=provider,
                            provider_id=provider_id,
                        )
                        if repeat_issue:
                            ok = False
                            reason = repeat_issue
                        else:
                            return day

                    logger.warning(
                        f"[日程生成] 生成结果未通过校验：{reason}（第 {attempt + 1} 次）"
                    )
                    if attempt < max_attempts - 1:
                        current_prompt = self.context._build_repair_prompt(
                            completion_text,
                            reason,
                            context["manual_extra"],
                            context["web_inspiration"],
                            expected_coverage=context["expected_coverage"],
                            issue_code=str(
                                getattr(self.context, "_last_validation_issue_code", "") or ""
                            ),
                            person_fact_context=context[
                                "person_facts"
                            ].format_for_generation(include_persona=True),
                            location_context=location_context,
                            style_catalog_context=context["style_catalog_context"],
                        )

                logger.error("[日程生成] 最终生成失败，重试次数耗尽")
            except Exception as e:
                logger.error(f"[日程生成] 生成失败：{e}")
            finally:
                await self.context._cleanup_conversation(location_session_id)
                await self.context._cleanup_conversation(gen_session_id)
            return None


__all__ = ["DailyEngineComponent"]
