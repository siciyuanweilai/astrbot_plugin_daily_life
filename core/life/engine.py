import datetime
import uuid

from astrbot.api import logger

from ..clock import now as life_now
from .tools import (
    analyze_weather,
    extract_city_from_persona,
    extract_pure_json_object,
    get_time_period_cn,
    parse_schedule_time,
    resolve_daily_hint,
    resolve_daily_suggested,
)


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
        hour, minute = parse_schedule_time(getattr(self.config, "schedule_time", "07:00"))
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
    ) -> dict:
        date_str = date.strftime("%Y-%m-%d")
        check_time = self._daily_generation_check_time(date, target_hour=target_hour)
        current_minutes = check_time.hour * 60 + check_time.minute
        period = self._get_curr_period(check_time)
        period_cn = get_time_period_cn(period)

        persona = await self._get_persona()
        city = self.config.weather.default_city or extract_city_from_persona(persona) or "北京"
        weather_data = await self.weather_client.get_weather(city)
        weather_info = analyze_weather(weather_data)
        weather_section, constraint_section = self._build_weather_sections(weather_info)
        week_plan = await self._ensure_week_plan()
        today_hint = resolve_daily_hint(week_plan, date)
        today_suggested = resolve_daily_suggested(week_plan, date)

        history_schedules_str = await self._build_history_schedule_summary(date)
        previous_context = await self._build_previous_life_context(date)
        recent_chats = await self._collect_recent_chat_context(persona)
        memo_str = await self._daily_generation_memo_text(date_str, extra=extra, web_inspiration=web_inspiration)
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
        prompt = self._build_timeline_prompt(
            date_str,
            period_cn,
            weather_section,
            constraint_section,
            await self._build_life_inertia_context(date),
            previous_context,
            history_schedules_str,
            memo_str,
            persona=persona,
            week_plan=week_plan,
            today_hint=today_hint,
            today_suggested=today_suggested,
            recent_chats=recent_chats,
            schedule_intent="由 LLM 自主决定",
            world_context=world_context,
            lifecycle_context=await self._build_lifecycle_context(date),
            autonomy_context=await self._build_autonomous_life_context(date),
            expected_coverage="target_period" if target_hour is not None else "full_day",
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
            "due_commitments": due_commitments,
            "prompt": prompt,
            "manual_extra": self._normalize_extra(extra),
            "web_inspiration": self._normalize_extra(web_inspiration),
            "expected_coverage": "target_period" if target_hour is not None else "full_day",
        }

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
    ):
        day = self._day_from_generation(
            result,
            date_str=context["date_str"],
            period=context["period"],
            weather_str=context["weather_str_for_prompt"],
            weather_info=context["weather_info"],
            meta=self._meta_from_generation(result),
            memo="",
        )
        repeat_issue = await self._repeat_generation_issue(day, date, result, manual_extra=context["manual_extra"])
        if repeat_issue:
            return None, repeat_issue

        logger.info("[日程生成] 成功解析结构化数据")
        day = await self._apply_lifecycle_to_day(day, date, result)
        await self._persist_generated_day(context["date_str"], day, context["due_commitments"])
        decision_text, decision_reason, decision_evidence = self._daily_decision_text(result, day)
        await self._save_life_decision_record(
            kind="daily_plan",
            date=context["date_str"],
            subject=context["date_str"],
            decision=decision_text,
            reason=decision_reason,
            evidence=decision_evidence,
            outcome=self._daily_decision_outcome(result),
        )
        logger.info(
            f"[日程生成] 生成成功：{context['date_str']}（{context['period_cn']}），时间轴节点数：{len(day.timeline)}"
        )
        return day, ""

    async def generate_daily(self, date=None, force=False, target_hour=None, extra=None, web_inspiration: str = ""):
        async with self._gen_lock:
            if date is None:
                date = life_now()
            date_str = date.strftime("%Y-%m-%d")

            if not force:
                existing = await self.archive.get_day(date_str)
                if existing:
                    return existing

            logger.info("[日程生成] 开始生成今日生活背景，依据近期记忆和生活惯性自主决策")

            context = await self._daily_generation_context(
                date,
                target_hour=target_hour,
                extra=extra,
                web_inspiration=web_inspiration,
            )

            gen_session_id = f"daily_life_gen_{uuid.uuid4().hex[:8]}"
            try:
                provider_id = self._generation_provider_id()
                provider = await self._get_provider(provider_id)
                if not provider:
                    return None

                logger.info("[日程生成] 开始调用大语言模型……")
                current_prompt = context["prompt"]
                max_attempts = 3 if context["manual_extra"] or context["web_inspiration"] else 2
                for attempt in range(max_attempts):
                    completion_text = await self._call_llm_text(
                        provider,
                        current_prompt,
                        gen_session_id,
                        primary_provider_id=provider_id,
                    )
                    if not completion_text:
                        logger.error(f"[日程生成] 大语言模型返回为空或失败（第 {attempt + 1} 次）")
                        continue

                    result = extract_pure_json_object(completion_text)
                    ok, reason = self._validate_daily_payload(
                        result,
                        context["manual_extra"],
                        expected_coverage=context["expected_coverage"],
                        current_minutes=context["current_minutes"],
                    )
                    if ok:
                        day, repeat_issue = await self._persist_daily_generation_success(
                            result,
                            date=date,
                            context=context,
                        )
                        if repeat_issue:
                            ok = False
                            reason = repeat_issue
                        else:
                            return day

                    logger.warning(f"[日程生成] 生成结果未通过校验：{reason}（第 {attempt + 1} 次）")
                    if attempt < max_attempts - 1:
                        current_prompt = self._build_repair_prompt(
                            completion_text,
                            reason,
                            context["manual_extra"],
                            context["web_inspiration"],
                            expected_coverage=context["expected_coverage"],
                        )

                logger.error("[日程生成] 最终生成失败，重试次数耗尽")
            except Exception as e:
                logger.error(f"[日程生成] 生成失败：{e}")
            finally:
                await self._cleanup_conversation(gen_session_id)
            return None



__all__ = ["DailyEngineMixin"]
