from __future__ import annotations

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

    def _persist_schedule_time(self, schedule_time: str) -> bool:
        if isinstance(self.raw_config, dict):
            rhythm_config = self.raw_config.setdefault("rhythm_config", {})
            if isinstance(rhythm_config, dict):
                rhythm_config["schedule_time"] = schedule_time
        save_config = getattr(self.raw_config, "save_config", None)
        if callable(save_config):
            save_config()
            return True
        return False

    async def apply_config(self, next_config: dict[str, Any]) -> LifeSettings:
        if not isinstance(next_config, dict):
            raise ValueError("配置必须是对象")
        if not isinstance(self.raw_config, dict):
            raise ValueError("当前配置对象不支持面板保存")

        payload = copy.deepcopy(next_config)
        parsed = LifeSettings.from_dict(payload)
        previous_config = copy.deepcopy(dict(self.raw_config))

        async with self.generation_lock:
            self.raw_config.clear()
            self.raw_config.update(copy.deepcopy(payload))
            save_config = getattr(self.raw_config, "save_config", None)
            if callable(save_config):
                try:
                    save_config()
                except Exception:
                    self.raw_config.clear()
                    self.raw_config.update(previous_config)
                    raise

            old_weather_client = getattr(self, "weather_client", None)
            old_media = getattr(self, "media", None)
            self.rhythm.stop()
            self._bind_runtime(parsed)
            self._injection_snapshot_cache = {}
            self.rhythm.start()
            if old_weather_client:
                try:
                    await old_weather_client.close()
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 关闭原天气客户端失败：{exc}")
            if old_media:
                try:
                    await old_media.close()
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 关闭原媒体服务失败：{exc}")

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
        return target_dt.replace(hour=now.hour, minute=now.minute, second=0, microsecond=0)
