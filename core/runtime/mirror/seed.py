from __future__ import annotations

import datetime

from astrbot.api import logger

from ...models import DayRecord


class SnapshotSeedMixin:
    async def ensure_startup_day_data(
        self, now: datetime.datetime | None = None
    ) -> None:
        """插件启动后若没有当前生活日记录，则在后台补充生成。"""
        # 插件初始化可能早于 OneBot/WebSocket 建连。先等待平台真正可用，
        # 否则首轮历史和联系人查询会在适配器尚未连接时被错误跳过。
        wait_for_platform = getattr(self, "wait_for_platform_ready", None)
        if callable(wait_for_platform):
            await wait_for_platform()
        now = now or self._runtime_now()
        target_date_str, _ = await self.resolve_injection_target(now)
        if await self.archive.get_day(target_date_str):
            return
        await self._generate_missing_day_background(
            target_date_str,
            now,
            source="startup_seed",
            log_scope="首次启动",
        )

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
        await self._generate_missing_day_background(
            target_date_str,
            now,
            source="injection_seed",
            log_scope="上下文注入",
        )

    async def _generate_missing_day_background(
        self,
        target_date_str: str,
        now: datetime.datetime,
        *,
        source: str,
        log_scope: str,
    ) -> None:
        if not hasattr(self, "failed_dates"):
            self.failed_dates = {}

        data = await self.archive.get_day(target_date_str)
        if data:
            return

        logger.info(f"[{log_scope}] 正在为 {target_date_str} 补全当天生活……")
        try:
            target_time = now
            if target_date_str != now.strftime("%Y-%m-%d"):
                target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d")
                target_time = target_date.replace(
                    hour=now.hour,
                    minute=now.minute,
                    second=now.second,
                    microsecond=now.microsecond,
                )
            result = await self.run_daily_generation(
                date=target_time,
                source=source,
                force=False,
            )
            data = result.day
        except Exception as exc:
            logger.warning(
                f"[{log_scope}] 补全 {target_date_str} 当天生活失败，"
                f"将在 10 分钟后允许重试：{type(exc).__name__}: {exc}"
            )
            data = None
        if data:
            self.failed_dates.pop(target_date_str, None)
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
