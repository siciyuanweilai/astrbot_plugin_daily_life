import datetime
import uuid

from astrbot.api import logger

from ..clock import now as life_now
from .appearance import persona_appearance_values
from .calendar import format_calendar_context, format_season_context
from .people import DAILY_PERSON_TEXT_PATHS
from .tools import (
    analyze_weather,
    extract_json_from_text,
    get_time_period_cn,
    parse_schedule_time,
    resolve_daily_hint,
    resolve_daily_suggested,
    timeline_item_datetime,
)
from .wardrobe import (
    normalize_outfit_decision,
    normalize_outfit_scene_category,
    resolve_outfit_style_pool,
)

_CURRENT_APPEARANCE_META_KEYS = (
    "outfit_decision",
    "outfit_scene_category",
    "outfit_style_pool",
    "style",
    "hair_style",
    "hair",
    "makeup_style",
    "makeup",
    "nails_style",
    "nails",
    "outfit_reason",
)

_PLANNED_APPEARANCE_META_KEYS = {
    "outfit_scene_category": "plan_outfit_scene_category",
    "outfit_style_pool": "plan_outfit_style_pool",
    "style": "plan_outfit_style",
    "hair_style": "plan_hair_style",
    "hair": "plan_hair",
    "makeup_style": "plan_makeup_style",
    "makeup": "plan_makeup",
    "nails_style": "plan_nails_style",
    "nails": "plan_nails",
    "outfit_reason": "plan_outfit_reason",
}


class DailyEngineMixin:
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
            getattr(self.config, "schedule_time", "07:00")
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
        current_minutes = check_time.hour * 60 + check_time.minute
        period = self._get_curr_period(check_time)
        period_cn = get_time_period_cn(period)

        persona = await self._get_persona()
        city_resolver = getattr(self.domains, "resolve_weather_city", None)
        city = await city_resolver() if callable(city_resolver) else ""
        weather_data = (
            await self.weather_client.get_weather(city)
            if city
            else "未配置可由当前地图服务解析的居住地，当前天气不可用"
        )
        weather_info = analyze_weather(weather_data)
        weather_section, constraint_section = self._build_weather_sections(weather_info)
        calendar_context = format_calendar_context(date)
        season_context = format_season_context(date)
        week_plan = await self._ensure_week_plan()
        today_hint = resolve_daily_hint(week_plan, date)
        today_suggested = resolve_daily_suggested(week_plan, date)

        history_schedules_str = await self._build_history_schedule_summary(date)
        previous_context = await self._build_previous_life_context(date)
        recent_chats = await self._collect_recent_chat_context(persona)
        replaced_day = (
            await self.archive.get_day(date_str) if regenerate_existing else None
        )
        replacement_context = self._build_daily_replacement_context(replaced_day)
        person_facts = await self._build_person_fact_context(
            persona=persona,
            explicit_instruction=self._normalize_extra(extra),
        )
        memo_str = await self._daily_generation_memo_text(
            date_str, extra=extra, web_inspiration=web_inspiration
        )
        due_commitments = await self.archive.get_due_commitments(date_str)
        commitment_text = self._format_commitment_prompt(due_commitments)
        if commitment_text:
            memo_str += (
                "\n\n【已答应过的承诺/约定】\n"
                "这些不是普通灵感，而是未来约定池里到期的事项。"
                "请优先自然安排进 timeline、提醒或聊天延续，不要遗漏：\n"
                f"{commitment_text}"
            )

        world_context = await self._build_world_context(
            date,
            "自主生活决策",
            weather_info,
            [today_hint, today_suggested, memo_str, recent_chats],
        )
        domain_context_builder = getattr(
            getattr(self, "domains", None), "format_context", None
        )
        if callable(domain_context_builder):
            domain_context = await domain_context_builder()
            if domain_context:
                world_context = f"{world_context}\n\n{domain_context}".strip()
        style_catalog_context = await self._style_catalog_context(limit=14)
        if style_catalog_context:
            world_context = f"{world_context}\n\n{style_catalog_context}".strip()
        prompt = self._build_timeline_prompt(
            date_str,
            period_cn,
            weather_section,
            constraint_section,
            await self._build_life_inertia_context(date),
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
            lifecycle_context=await self._build_lifecycle_context(
                date,
                exclude_daily_plan_date=date_str if regenerate_existing else "",
            ),
            autonomy_context=await self._build_autonomous_life_context(date),
            person_fact_context=person_facts.format_for_generation(),
            replacement_context=replacement_context,
            expected_coverage="target_period"
            if target_hour is not None
            else "full_day",
            current_time_text=check_time.strftime("%Y-%m-%d %H:%M"),
        )
        return {
            "date_str": date_str,
            "check_time": check_time,
            "current_minutes": current_minutes,
            "period": period,
            "period_cn": period_cn,
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
            "due_commitments": due_commitments,
            "prompt": prompt,
            "manual_extra": self._normalize_extra(extra),
            "web_inspiration": self._normalize_extra(web_inspiration),
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
            "本次是在原子替换同一天的旧日程草稿，不是继续旧草稿。旧日程、旧生活决策和旧穿搭"
            "只用于识别重复，不能作为 keep 的当前事实；请根据天气、活动和角色审美重新决定穿搭，"
            "并主动改变服装类别、主色、版型或层次组合。\n"
            f"- 待替换穿搭：{old_outfit or '未记录'}\n"
            f"- 待替换风格：{old_style or '未记录'}"
        )

    async def _daily_generation_memo_text(
        self,
        date_str: str,
        *,
        extra: str | None = None,
        web_inspiration: str = "",
    ) -> str:
        today_data_temp = await self.archive.get_day(date_str)
        memo_str = today_data_temp.memo if today_data_temp else ""
        if extra:
            memo_str += f"\n- 用户实时指令：{extra}"
        web_inspiration = self._normalize_extra(web_inspiration)
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
            decision.get("outfit")
            if isinstance(decision.get("outfit"), dict)
            else {}
        )
        outfit_choice = normalize_outfit_decision(outfit_decision.get("decision"))
        current_scene_category = str(
            outfit_decision.get("scene_category") or ""
        ).strip()
        check_time = context.get("check_time")
        timeline = result.get("timeline")
        if isinstance(timeline, list) and check_time is not None:
            occurred = []
            for item in timeline:
                if not isinstance(item, dict):
                    continue
                item_time = timeline_item_datetime(item, context.get("date_str"))
                if item_time is not None and item_time <= check_time:
                    occurred.append(item)
            if occurred:
                place_kind = str(occurred[-1].get("place_kind") or "").strip()
                if place_kind == "home":
                    current_scene_category = "home"
                elif place_kind == "transit":
                    current_scene_category = "outdoor"
                elif place_kind in {"poi", "generic"}:
                    current_scene_category = "public"
        current_reference_ids = self._style_catalog_reference_ids(
            outfit_decision.get("catalog_reference_ids")
        )
        if outfit_choice != "keep" and not context["manual_extra"]:
            current_reference_ids = (
                await self._style_catalog_resolve_new_outfit_reference_ids(
                    current_reference_ids,
                    scene_category=current_scene_category,
                )
            )
            # 将自动修复后的编号回写到结构化结果，确保日程元数据和后续
            # 穿搭连续性记录的引用与实际采用的候选一致。
            outfit_decision["catalog_reference_ids"] = current_reference_ids
            (
                catalog_appearance,
                catalog_issue,
            ) = await self._style_catalog_new_outfit_selection(
                current_reference_ids,
                scene_category=current_scene_category,
            )
            if catalog_issue:
                self._set_validation_issue("style_catalog_required")
                return None, catalog_issue
        else:
            catalog_appearance = await self._style_catalog_reference_appearance(
                current_reference_ids,
                scene_category=""
                if context["manual_extra"]
                else current_scene_category,
            )

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
            action_reference_ids = self._style_catalog_reference_ids(
                payload.get("catalog_reference_ids")
            )
            timeline_index = self._action_timeline_index(raw_action)
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
            action_scene_category = (
                "home"
                if place_kind == "home"
                else "outdoor"
                if place_kind == "transit"
                else "public"
                if place_kind in {"poi", "generic"}
                else ""
            )
            if context["manual_extra"]:
                action_appearance = await self._style_catalog_reference_appearance(
                    action_reference_ids,
                    scene_category="",
                )
                action_issue = ""
            else:
                action_reference_ids = (
                    await self._style_catalog_resolve_new_outfit_reference_ids(
                        action_reference_ids,
                        scene_category=action_scene_category,
                    )
                )
                (
                    action_appearance,
                    action_issue,
                ) = await self._style_catalog_new_outfit_selection(
                    action_reference_ids,
                    scene_category=action_scene_category,
                )
            if action_issue:
                self._set_validation_issue("style_catalog_required")
                return None, f"计划换装未采用衣橱候选：{action_issue}"
            if action_appearance.get("outfit"):
                raw_action["target"] = action_appearance["outfit"]
                payload["catalog_reference_ids"] = action_reference_ids
                reserve = str(action_appearance.get("outing_reserve") or "").strip()
                if reserve:
                    payload["outing_outfit_reserve"] = reserve
                raw_action["payload"] = payload

        day = self._day_from_generation(
            result,
            date_str=context["date_str"],
            period=context["period"],
            weather_str=context["weather_str_for_prompt"],
            weather_info=context["weather_info"],
            meta=self._meta_from_generation(result),
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
        if catalog_outfit and outfit_choice != "keep":
            day.outfit = catalog_outfit
        if outing_reserve:
            day.meta["outing_outfit_reserve"] = outing_reserve
        else:
            day.meta.pop("outing_outfit_reserve", None)
        for key, value in catalog_appearance.items():
            if not str(day.meta.get(key) or "").strip():
                day.meta[key] = value
        repeat_issue = await self._repeat_generation_issue(
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
            audited = await self._audit_persona_appearance(
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
        day = await self._apply_lifecycle_to_day(day, date, result)
        await self._persist_generated_day(
            context["date_str"], day, context["due_commitments"]
        )
        await self._mark_style_catalog_references(current_reference_ids)
        if current_reference_ids:
            logger.debug(
                "[日程生成] 当前穿搭已采用视觉衣橱候选："
                + ",".join(str(item) for item in current_reference_ids)
            )
        decision_text, decision_reason, decision_evidence = self._daily_decision_text(
            result, day
        )
        await self._save_life_decision_record(
            kind="daily_plan",
            date=context["date_str"],
            subject=context["date_str"],
            decision=decision_text,
            reason=decision_reason,
            evidence=decision_evidence,
            outcome=self._daily_decision_outcome(result),
        )
        logger.debug(
            f"[日程生成] 生成成功：{context['date_str']}（{context['period_cn']}），时间轴节点数：{len(day.timeline)}"
        )
        return day, ""

    async def _ground_generated_current_appearance(self, day, *, context: dict):
        """避免把尚未发生的日程穿搭提前保存为当前穿搭。"""

        check_time = context["check_time"]
        has_occurred_timeline = any(
            item_time is not None and item_time <= check_time
            for item in day.timeline
            if (item_time := timeline_item_datetime(item, day.date)) is not None
        )
        confirmed_at = check_time.strftime("%Y-%m-%d %H:%M:%S")
        if has_occurred_timeline:
            day.meta["outfit_fact_source"] = "daily_generation"
            day.meta["outfit_fact_confirmed_at"] = confirmed_at
            day.meta["outfit_fact_evidence"] = "当日日程已覆盖当前时刻"
            return day

        previous_date = (
            check_time.date() - datetime.timedelta(days=1)
        ).isoformat()
        previous_day = await self.archive.get_day(previous_date)
        previous_outfit = str(
            getattr(previous_day, "outfit", "") if previous_day else ""
        ).strip()
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
                day.meta[plan_key] = planned_value
        for key in _CURRENT_APPEARANCE_META_KEYS:
            value = str(previous_meta.get(key) or "").strip()
            if value:
                day.meta[key] = value
            else:
                day.meta.pop(key, None)

        previous_source = str(
            previous_meta.get("outfit_fact_source") or ""
        ).strip()
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
            f"延续 {previous_date} 尚未被已发生换装替代的当前穿搭"
        )
        day.meta["outfit_carried_from"] = previous_date
        logger.debug("[日程生成] 首个日程节点尚未发生，当前穿搭延续上一日记录")
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
        async with self._gen_lock:
            if date is None:
                date = life_now()
            date_str = date.strftime("%Y-%m-%d")

            if not force:
                existing = await self.archive.get_day(date_str)
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
                provider_id = self._generation_provider_id()
                provider = await self._get_provider(provider_id)
                if not provider:
                    return None

                domain_service = getattr(self, "domains", None)
                location_auditor = getattr(
                    domain_service, "audit_daily_locations", None
                )
                map_tools_available = getattr(
                    domain_service, "map_tools_available", None
                )
                location_validation_enabled = bool(
                    callable(location_auditor)
                    and callable(map_tools_available)
                    and map_tools_available()
                )
                location_context = ""
                preselected_places = []
                if location_validation_enabled:
                    logger.debug("[日程生成] 开始规划地点意图并通过地图预选候选……")
                    (
                        location_context,
                        preselected_places,
                    ) = await self._prepare_daily_location_context(
                        context=context,
                        provider=provider,
                        provider_id=provider_id,
                        session_id=location_session_id,
                    )
                logger.debug("[日程生成] 开始调用大语言模型生成最终日程……")
                current_prompt = self._append_daily_location_context(
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
                    completion_text = await self._call_llm_text(
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
                    ok, reason = self._validate_daily_payload(
                        result,
                        context["manual_extra"],
                        expected_coverage=context["expected_coverage"],
                        current_minutes=context["current_minutes"],
                    )
                    if ok:
                        audit = await self._audit_person_payload(
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
                            self._set_validation_issue("person_fact_conflict")
                        else:
                            result = audit.payload
                            ok, reason = self._validate_daily_payload(
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
                                self._set_validation_issue("location_audit_invalid")
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
                        current_prompt = self._build_repair_prompt(
                            completion_text,
                            reason,
                            context["manual_extra"],
                            context["web_inspiration"],
                            expected_coverage=context["expected_coverage"],
                            issue_code=str(
                                getattr(self, "_last_validation_issue_code", "") or ""
                            ),
                            person_fact_context=context[
                                "person_facts"
                            ].format_for_generation(include_persona=True),
                            location_context=location_context,
                            style_catalog_context=context[
                                "style_catalog_context"
                            ],
                        )

                logger.error("[日程生成] 最终生成失败，重试次数耗尽")
            except Exception as e:
                logger.error(f"[日程生成] 生成失败：{e}")
            finally:
                await self._cleanup_conversation(location_session_id)
                await self._cleanup_conversation(gen_session_id)
            return None


__all__ = ["DailyEngineMixin"]
