import datetime
from typing import Any, AsyncIterator

from astrbot.api import logger

from ..config.vocab import CN_PERIOD_MAP, PERIOD_HOURS
from ..life.tools import (
    analyze_weather,
    extract_city_from_persona,
    format_timeline_to_text,
    get_time_period_cn,
)
from ..models import DayRecord, WeatherInfo
from ..clock import timestamp as life_timestamp
from .request import CommandRequest, DailyResetPlan


class OperateCommandMixin:
    async def _clear(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        async with self.runtime.generation_lock:
            await self.runtime.archive.reset_all()
            self.runtime.failed_dates.clear()
            self.runtime.composer._reference_name_cache.clear()
        yield event.plain_result("✅ 已清空全部日常生活背景数据。")

    async def _reset(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        if req.param1 in ["清空", "全部", "数据"]:
            yield event.plain_result("清空全部数据请使用明确维护指令：/生活 清空")
            return

        plan = self._build_reset_daily_plan(req)
        yield event.plain_result(plan.progress)
        result = await self._execute_reset_daily(req, plan)
        if result:
            outfit_str = result.outfit or "无"
            tl_text = format_timeline_to_text(result.timeline)
            yield event.plain_result(f"✅ 生成成功！\n👔 穿搭：{outfit_str}\n\n📍 最新时间轴：\n{tl_text}")
        else:
            yield event.plain_result("❌ 生成失败")

    def _build_reset_daily_plan(self, req: CommandRequest) -> DailyResetPlan:
        input_period = CN_PERIOD_MAP.get(req.param1, req.param1)
        if input_period in PERIOD_HOURS:
            target_hour = PERIOD_HOURS[input_period]
            target_period = input_period
            target_period_cn = get_time_period_cn(target_period)
            extra_instruction = " ".join(req.parts[3:]) if len(req.parts) > 3 else None
            keep_schedule = not extra_instruction
        else:
            target_hour = None
            target_period = req.period
            target_period_cn = req.period_cn
            extra_instruction = " ".join(req.parts[2:]) if len(req.parts) > 2 and req.param1 != "保持" else None
            keep_schedule = req.param1 == "保持"

        desc_parts = [f"{target_period_cn}状态/穿搭"]
        if keep_schedule:
            desc_parts.append("(保留日程)")
        if extra_instruction:
            desc_parts.append(f"(指令: {extra_instruction})")
        progress = f"重新生成 {' '.join(desc_parts)}..."
        return DailyResetPlan(
            progress=progress,
            target_hour=target_hour,
            target_period=target_period,
            extra_instruction=extra_instruction,
            keep_schedule=keep_schedule,
        )

    async def _execute_reset_daily(self, req: CommandRequest, plan: DailyResetPlan) -> DayRecord | None:
        async with self.runtime.generation_lock:
            has_day = await self.runtime.archive.get_day(req.target_date_str)
            if plan.keep_schedule and has_day and not plan.extra_instruction:
                target_time = self.runtime._target_datetime_for_command(req.target_date_str, req.now)
                return await self.runtime.composer.update_outfit(
                    req.target_date_str,
                    plan.target_period,
                    current_time=target_time,
                )

            self.runtime.failed_dates.pop(req.target_date_str, None)
            if not plan.keep_schedule or plan.extra_instruction:
                await self.runtime.archive.delete_day(req.target_date_str)
            target_dt = self.runtime._target_datetime_for_command(req.target_date_str, req.now)
            return await self.runtime.composer.generate_daily(
                date=target_dt,
                force=True,
                target_hour=plan.target_hour,
                extra=plan.extra_instruction,
            )

    async def _weather(self, event: Any, req: CommandRequest) -> AsyncIterator[Any]:
        home_city = self.runtime.config.weather.default_city
        if not home_city:
            persona = await self.runtime.composer._get_persona()
            home_city = extract_city_from_persona(persona)

        query_city = req.param1.strip() if req.param1 else home_city
        if not query_city:
            yield event.plain_result("请告诉我要查询哪个城市的天气。")
            return

        weather_raw = await self.runtime.weather_client.get_weather(query_city)
        analyzed = analyze_weather(weather_raw)
        should_sync = (not req.param1) or (query_city == home_city)
        if should_sync and analyzed.get("temp") is not None:
            await self._sync_weather_to_day(req, analyzed)

        display = f"🌤️ {analyzed['raw']}"
        if analyzed.get("outfit_hint"):
            display += f"\n👔 穿衣建议: {analyzed['outfit_hint']}"
        if analyzed.get("activity_hint"):
            display += f"\n🏃 活动建议: {analyzed['activity_hint']}"
        yield event.plain_result(display)

    async def _sync_weather_to_day(self, req: CommandRequest, analyzed: dict) -> None:
        sync_date_str = req.target_date_str
        data = await self.runtime.archive.get_day(sync_date_str)
        if not data:
            sync_date_str = req.today_str
            data = await self.runtime.archive.get_day(sync_date_str)
        if not data:
            return
        data.weather = analyzed["raw"]
        data.weather_info = WeatherInfo.from_value(analyzed)
        data.weather_last_update = life_timestamp()
        await self.runtime.archive.save_day(data)
        logger.debug("[手动天气] 已更新居住地天气数据")
