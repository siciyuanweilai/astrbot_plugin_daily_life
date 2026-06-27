import datetime
import uuid

from astrbot.api import logger

from ..clock import now as life_now
from .tools import (
    analyze_weather,
    extract_city_from_persona,
    extract_pure_json_object,
    get_time_period_cn,
    resolve_daily_hint,
    resolve_daily_suggested,
)


class DailyEngineMixin:
    @staticmethod
    def _normalize_extra(extra: str | None) -> str:
        return str(extra or "").strip()


    async def generate_daily(self, date=None, force=False, target_hour=None, extra=None, web_inspiration: str = ""):
        async with self._gen_lock:
            if date is None:
                date = life_now()
            date_str = date.strftime("%Y-%m-%d")
            check_time = date.replace(hour=target_hour, minute=0) if target_hour is not None else date
            current_minutes = check_time.hour * 60 + check_time.minute
            period = self._get_curr_period(check_time)
            period_cn = get_time_period_cn(period)

            if not force:
                existing = await self.archive.get_day(date_str)
                if existing:
                    return existing

            catalog = await self._get_catalog_settings()
            logger.info("[日程生成] 开始生成今日生活背景，素材池仅作为灵感参考")

            persona = await self._get_persona()
            city = self.config.weather.default_city or extract_city_from_persona(persona) or "北京"
            weather_data = await self.weather_client.get_weather(city)
            weather_info = analyze_weather(weather_data)
            weather_str_for_prompt = weather_info["raw"]
            weather_section, constraint_section = self._build_weather_sections(weather_info)
            week_plan = await self._get_week_plan()
            today_hint = resolve_daily_hint(week_plan, date)
            today_suggested = resolve_daily_suggested(week_plan, date)

            history_schedules_str = await self._build_history_schedule_summary(date)
            previous_context = await self._build_previous_life_context(date)
            recent_chats = await self._collect_recent_chat_context(persona)
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
            lifecycle_context = await self._build_lifecycle_context(date)
            inspiration_section = self._build_catalog_inspiration(catalog)
            prompt = self._build_timeline_prompt(
                date_str,
                period_cn,
                weather_section,
                constraint_section,
                inspiration_section,
                previous_context,
                history_schedules_str,
                self._get_outfit_prompt(period),
                memo_str,
                persona=persona,
                week_plan=week_plan,
                today_hint=today_hint,
                today_suggested=today_suggested,
                recent_chats=recent_chats,
                schedule_intent="由 LLM 自主决定",
                world_context=world_context,
                lifecycle_context=lifecycle_context,
                expected_coverage="target_period" if target_hour is not None else "full_day",
                current_time_text=check_time.strftime("%Y-%m-%d %H:%M"),
            )

            gen_session_id = f"daily_life_gen_{uuid.uuid4().hex[:8]}"
            try:
                provider_id = self._generation_provider_id()
                provider = await self._get_provider(provider_id)
                if not provider:
                    return None

                logger.info("[日程生成] 开始调用大语言模型……")
                manual_extra = self._normalize_extra(extra)
                expected_coverage = "target_period" if target_hour is not None else "full_day"
                current_prompt = prompt
                max_attempts = 3 if manual_extra or web_inspiration else 2
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
                        manual_extra,
                        expected_coverage=expected_coverage,
                        current_minutes=current_minutes,
                    )
                    if ok:
                        logger.info("[日程生成] 成功解析结构化数据")
                        day = self._day_from_generation(
                            result,
                            date_str=date_str,
                            period=period,
                            weather_str=weather_str_for_prompt,
                            weather_info=weather_info,
                            meta=self._meta_from_generation(result),
                            memo="",
                        )
                        day = await self._apply_lifecycle_to_day(day, date, result)
                        await self._persist_generated_day(date_str, day, due_commitments)
                        logger.info(
                            f"[日程生成] 生成成功：{date_str}（{period_cn}），时间轴节点数：{len(day.timeline)}"
                        )
                        return day

                    logger.warning(f"[日程生成] 生成结果未通过校验：{reason}（第 {attempt + 1} 次）")
                    if attempt < max_attempts - 1:
                        current_prompt = self._build_repair_prompt(
                            completion_text,
                            reason,
                            manual_extra,
                            web_inspiration,
                            expected_coverage=expected_coverage,
                        )

                logger.error("[日程生成] 最终生成失败，重试次数耗尽")
            except Exception as e:
                logger.error(f"[日程生成] 生成失败：{e}")
            finally:
                await self._cleanup_conversation(gen_session_id)
            return None



__all__ = ["DailyEngineMixin"]
