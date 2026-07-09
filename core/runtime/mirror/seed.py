from __future__ import annotations

import datetime

from astrbot.api import logger

from ...models import DayRecord


class SnapshotSeedMixin:
    async def ensure_injection_day_data(
        self,
        target_date_str: str,
        now: datetime.datetime,
    ) -> DayRecord | None:
        data = await self.archive.get_day(target_date_str)
        today_str = now.strftime("%Y-%m-%d")
        if not hasattr(self, "failed_dates"):
            self.failed_dates = {}
        failed_at = self.failed_dates.get(target_date_str)
        can_retry_generation = not failed_at or (now - failed_at).total_seconds() >= 600
        if target_date_str != today_str or data or not can_retry_generation:
            return data

        self._schedule_background_task(
            self._generate_injection_day_background(target_date_str, now),
            label="日常生活即时生成",
            key=f"injection_day:{target_date_str}",
        )
        return None

    async def _generate_injection_day_background(
        self,
        target_date_str: str,
        now: datetime.datetime,
    ) -> None:
        if not hasattr(self, "failed_dates"):
            self.failed_dates = {}

        async with self.generation_lock:
            data = await self.archive.get_day(target_date_str)
            if data:
                return

            logger.info(f"[上下文注入] 正在为 {target_date_str} 即时生成日程……")
            data = await self.composer.generate_daily(now)
            if data:
                self.failed_dates.pop(target_date_str, None)
                await self.mark_page_status_changed("daily_background_generation")
            else:
                self.failed_dates[target_date_str] = now

    async def maybe_update_injection_outfit(
        self,
        today_str: str,
        data: DayRecord | None,
        using_extended_night: bool,
    ) -> DayRecord | None:
        if using_extended_night:
            return data

        today_data = await self.archive.get_day(today_str)
        if not today_data:
            return data

        data = today_data
        return data
