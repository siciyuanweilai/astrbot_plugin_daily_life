from __future__ import annotations

import datetime
from typing import Any

from astrbot.api import logger

from ..life.condition import state_is_stale
from ..models import DayRecord
from .markers import LOG_PREFIX


class RefreshMixin:
    def _auto_life_check_due(self, data: DayRecord, now: datetime.datetime) -> bool:
        interval = max(5, int(self.config.state.refresh_minutes or 30))
        checked_at = (data.meta or {}).get("auto_life_last_checked_at", "")
        if not checked_at:
            return True
        try:
            last = datetime.datetime.strptime(checked_at, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return True
        return (now - last).total_seconds() >= interval * 60

    def _state_refresh_in_quiet_hours(self, now: datetime.datetime) -> bool:
        quiet_hours = str(getattr(self.config.state, "quiet_hours", "") or "").strip()
        if not quiet_hours:
            return False
        start_text, sep, end_text = quiet_hours.partition("-")
        if not sep:
            return False
        try:
            start_hour, start_minute = map(int, start_text.split(":", 1))
            end_hour, end_minute = map(int, end_text.split(":", 1))
        except (TypeError, ValueError):
            return False
        current = now.hour * 60 + now.minute
        start = start_hour * 60 + start_minute
        end = end_hour * 60 + end_minute
        if start < end:
            return start <= current < end
        if start > end:
            return current >= start or current < end
        return False

    async def _run_autonomous_life_check(
        self,
        target_date_str: str,
        now: datetime.datetime,
        *,
        source: str,
        detail: str,
        status_reason: str,
        respect_quiet_hours: bool = True,
        update_weather: bool = True,
        log_trigger: str = "",
        source_event: Any = None,
    ) -> DayRecord | None:
        if not self.config.state.enabled:
            return await self.archive.get_day(target_date_str)
        if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
            return await self.archive.get_day(target_date_str)
        if respect_quiet_hours and self._state_refresh_in_quiet_hours(now):
            logger.debug(
                f"{LOG_PREFIX} 实时状态巡检处于静默时段 {self.config.state.quiet_hours}，跳过本次巡检"
            )
            return await self.archive.get_day(target_date_str)
        if update_weather:
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return await self.archive.get_day(target_date_str)
            await self.try_update_weather(target_date_str)

        data = await self.archive.get_day(target_date_str)
        if not data:
            return None
        if not self._auto_life_check_due(data, now):
            return data

        async with self.generation_lock:
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return await self.archive.get_day(target_date_str)
            data = await self.archive.get_day(target_date_str)
            if not data or not self._auto_life_check_due(data, now):
                return data
            if log_trigger:
                logger.info(f"{LOG_PREFIX} 触发大语言模型自主生活状态/穿搭检查：{log_trigger}")
            state_kwargs: dict[str, Any] = {
                "source": source,
                "detail": detail,
                "force": False,
                "notify_page": False,
            }
            if source_event is not None:
                state_kwargs["source_event"] = source_event
            data = await self.refresh_state_for_day(target_date_str, data, now, **state_kwargs)
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return data
            current_period = self._get_curr_period(now)
            outfit_kwargs: dict[str, Any] = {"current_time": now}
            if source_event is not None and self._event_message_id(source_event):
                outfit_kwargs["should_abort"] = lambda: self.event_was_recalled(source_event, log_skip=True)
            updated = await self.composer.update_outfit(target_date_str, current_period, **outfit_kwargs)
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return data
            data = updated or data or await self.archive.get_day(target_date_str)
            if data:
                data.meta["auto_life_last_checked_at"] = now.strftime("%Y-%m-%d %H:%M")
                await self.archive.save_day(data)
            if source_event is not None and self.event_was_recalled(source_event, log_skip=True):
                return data
            await self.mark_page_status_changed(status_reason)
            return data

    async def check_autonomous_life_update(self) -> None:
        if not self.config.state.enabled:
            return

        now = self._runtime_now()
        target_date_str, _ = await self.resolve_injection_target(now)
        await self._run_autonomous_life_check(
            target_date_str,
            now,
            source="auto",
            detail="后台自动检查：请根据当前时间、时间轴、天气、睡眠债和近期状态，自主判断此刻生活状态。",
            status_reason="autonomous_life_update",
            respect_quiet_hours=True,
            update_weather=True,
            log_trigger="后台巡检",
        )

    async def check_period_transition(self) -> None:
        await self.check_autonomous_life_update()

    def _schedule_context_state_refresh(
        self,
        target_date_str: str,
        data: DayRecord,
        now: datetime.datetime,
    ) -> None:
        if not self.config.state.enabled:
            return
        if not state_is_stale(data.state, now, self.config.state.refresh_minutes):
            return
        self._schedule_background_task(
            self.refresh_state_for_day(
                target_date_str,
                data,
                now,
                source="context",
                detail="外部读取生活上下文：按刷新间隔在后台检查实时状态。",
            ),
            label="上下文状态刷新",
            key=f"context_state:{target_date_str}",
        )
