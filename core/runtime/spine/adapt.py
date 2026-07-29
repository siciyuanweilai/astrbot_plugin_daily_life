from __future__ import annotations

import asyncio
import copy
import datetime
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ...config.options import LifeSettings
from ...life.tools import get_time_period, resolve_business_now
from ..markers import LOG_PREFIX


class SpineAdaptMixin:
    def _get_curr_period(self, target_dt: datetime.datetime | None = None) -> str:
        return get_time_period(target_dt)

    @staticmethod
    def _runtime_now() -> datetime.datetime:
        return life_now()

    async def _persist_schedule_time(self, schedule_time: str) -> bool:
        if isinstance(self.raw_config, dict):
            rhythm_config = self.raw_config.setdefault("rhythm_config", {})
            if isinstance(rhythm_config, dict):
                rhythm_config["schedule_time"] = schedule_time
        save_config = getattr(self.raw_config, "save_config", None)
        if callable(save_config):
            await asyncio.to_thread(save_config)
            return True
        return False

    async def _write_runtime_config(self, payload: dict[str, Any]) -> None:
        self.raw_config.clear()
        self.raw_config.update(copy.deepcopy(payload))
        save_config = getattr(self.raw_config, "save_config", None)
        if callable(save_config):
            await asyncio.to_thread(save_config)

    async def _restore_runtime_config(self, payload: dict[str, Any]) -> None:
        self.raw_config.clear()
        self.raw_config.update(payload)
        save_config = getattr(self.raw_config, "save_config", None)
        if not callable(save_config):
            return
        try:
            await asyncio.to_thread(save_config)
        except Exception as exc:
            logger.error(f"{LOG_PREFIX} 恢复旧配置文件失败：{exc}")

    @staticmethod
    def _rhythm_running(services: Any) -> bool:
        scheduler = getattr(getattr(services, "rhythm", None), "scheduler", None)
        return bool(getattr(scheduler, "running", False))

    async def _swap_runtime_services(
        self,
        candidate: Any,
        previous: Any,
        *,
        previous_rhythm_running: bool,
    ) -> None:
        await self._begin_runtime_service_swap()
        try:
            previous.rhythm.stop()
            self._install_runtime_services(candidate)
            self._prune_disabled_proactive_candidates()
            for key in list(getattr(self, "_proactive_idle_candidates", {})):
                self._schedule_proactive_idle_evaluation(key)
            self._injection_snapshot_cache = {}
        except BaseException:
            candidate.rhythm.stop()
            self._install_runtime_services(previous)
            if previous_rhythm_running:
                previous.rhythm.start()
            raise
        finally:
            await self._end_runtime_service_swap()

    async def apply_config(self, next_config: dict[str, Any]) -> LifeSettings:
        if not isinstance(next_config, dict):
            raise ValueError("配置必须是对象")
        if not isinstance(self.raw_config, dict):
            raise ValueError("当前配置对象不支持面板保存")

        payload = copy.deepcopy(next_config)
        parsed = LifeSettings.from_dict(payload)
        previous_config = copy.deepcopy(dict(self.raw_config))

        async with self.generation_lock:
            previous_services = self._current_runtime_services()
            previous_rhythm_running = self._rhythm_running(previous_services)
            candidate = self._build_runtime_services(parsed, payload)
            try:
                candidate.rhythm.start()
            except Exception:
                await self._close_runtime_services(candidate)
                raise

            try:
                await self._write_runtime_config(payload)
                await self._swap_runtime_services(
                    candidate,
                    previous_services,
                    previous_rhythm_running=previous_rhythm_running,
                )
            except BaseException:
                candidate.rhythm.stop()
                await self._restore_runtime_config(previous_config)
                await self._close_runtime_services(candidate)
                raise

            await self._close_runtime_services(previous_services)

        logger.info(f"{LOG_PREFIX} 已从设置页重新加载配置")
        return self.config

    async def _resolve_command_target_date(
        self,
        now: datetime.datetime,
    ) -> tuple[str, bool]:
        today_str = now.strftime("%Y-%m-%d")
        yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        business_now = resolve_business_now(self.config.schedule_time, now)
        using_extended_night = business_now.date() < now.date()

        if using_extended_night:
            if await self.archive.get_day(yesterday_str):
                return yesterday_str, True
            return today_str, False

        if await self.archive.get_day(today_str):
            return today_str, False
        if await self.archive.get_day(yesterday_str):
            return yesterday_str, True
        return today_str, False

    @staticmethod
    def _target_datetime_for_command(
        date_str: str,
        now: datetime.datetime,
    ) -> datetime.datetime:
        try:
            target_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return now
        return target_dt.replace(
            hour=now.hour, minute=now.minute, second=0, microsecond=0
        )
