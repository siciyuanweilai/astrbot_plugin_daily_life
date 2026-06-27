from __future__ import annotations

import asyncio
import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.star.context import Context

from ...archive import LifeArchive
from ...config.options import LifeSettings
from ...media import LifeMediaService
from ...sources import ContactNameResolver
from ...life import LifeBackgroundComposer, WeatherClient
from ..markers import LOG_PREFIX
from ..timer import LifeRhythmClock


class SpineBootMixin:
    def __init__(self, context: Context, raw_config: Any, data_path: Path):
        self.context = context
        self.raw_config = raw_config
        self.data_path = data_path
        self.generation_lock = asyncio.Lock()
        self._page_status_version = 0
        self._page_status_changed = asyncio.Condition()
        self._init_background_tasks()
        self._init_response_gate_state()
        self._injection_snapshot_cache: dict[str, Any] = {}
        self.archive = LifeArchive(self.data_path)
        self._bind_runtime(LifeSettings.from_dict(raw_config))
        self.failed_dates: dict[str, datetime.datetime] = {}
        self._proactive_last_reply_at: dict[str, datetime.datetime] = {}
        self._proactive_idle_candidates: dict[str, dict[str, Any]] = {}
        self._proactive_private_last_revisit_at: dict[str, datetime.datetime] = {}
        self._proactive_air_state: dict[str, dict[str, Any]] = {}
        self._proactive_feedback_watch: dict[str, dict[str, Any]] = {}
        self.rhythm.start()
        self._log_boot_summary()

    def _runtime_data_path(self) -> Path:
        return getattr(self, "data_path", Path("daily_life.db"))

    def _bind_runtime(self, config: LifeSettings) -> None:
        self.config = config
        self.media = LifeMediaService(self.config, self._runtime_data_path())
        self.memos = self._create_memos_service()
        self.contact_resolver = ContactNameResolver(
            self.context,
            self.raw_config,
            log_prefix=LOG_PREFIX,
        )
        self.weather_client = WeatherClient(self.config.weather)
        self.composer = LifeBackgroundComposer(
            self.context,
            self.config,
            self.archive,
            self.weather_client,
            self.contact_resolver,
        )
        self.rhythm = self._build_rhythm()

    def _build_rhythm(self) -> LifeRhythmClock:
        return LifeRhythmClock(
            config=self.config,
            daily_task=self.run_daily_refresh,
            weekly_task=self.run_weekly_refresh,
            auto_update_task=self.check_autonomous_life_update,
            review_task=self.run_nightly_review,
            proactive_revisit_task=self.run_private_revisit_check,
            proactive_idle_task=self.run_proactive_idle_check,
        )

    def _log_boot_summary(self) -> None:
        logger.info(f"{LOG_PREFIX} 「愿此朝夕陪伴你的生活」启动完成")

    async def terminate(self) -> None:
        self.rhythm.stop()
        await self._cancel_background_tasks()
        weather_client = getattr(self, "weather_client", None)
        if weather_client:
            await weather_client.close()
        media = getattr(self, "media", None)
        if media:
            await media.close()
        await self.close_memos_service()
        self.archive.close()
        logger.info(f"{LOG_PREFIX} 已卸载")
