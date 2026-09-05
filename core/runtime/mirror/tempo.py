from __future__ import annotations

import datetime

from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest

from ...clock import now as life_now
from ...life.tools import build_time_context
from ..markers import INTERNAL_SESSION_PREFIXES


class SnapshotTempoMixin:
    def _get_time_status(self, now: datetime.datetime | None = None) -> str:
        context = build_time_context(
            now or life_now(), getattr(self.config, "schedule_time", "07:00")
        )
        return (
            f"当前时间线索：{context.now.strftime('%H:%M')}，时段标签：{context.period_cn}；"
            f"生活日：{context.business_date_text}；"
            "当前是否清醒以实时状态和时间轴为准，life_mode/sleep_mode 只表示今日生成的日程基调与睡眠倾向；"
            "时间经过不等于动作已完成"
        )

    @staticmethod
    def is_internal_llm_session(req: ProviderRequest) -> bool:
        session_id = getattr(req, "session_id", "")
        return bool(session_id) and session_id.startswith(INTERNAL_SESSION_PREFIXES)

    def _debug_injection_target_once(self, key: tuple[str, ...], message: str) -> None:
        if getattr(self, "_last_injection_target_log_key", None) == key:
            return
        self._last_injection_target_log_key = key
        logger.debug(message)

    async def resolve_injection_target(
        self, now: datetime.datetime
    ) -> tuple[str, bool]:
        context = build_time_context(now, self.config.schedule_time)
        today_str = context.now.strftime("%Y-%m-%d")
        target_date_str = context.business_date_text
        using_extended_night = context.extended_night
        if not using_extended_night:
            return target_date_str, False

        yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if await self.archive.get_day(yesterday_str):
            self._debug_injection_target_once(
                (
                    "extended_yesterday",
                    today_str,
                    yesterday_str,
                    str(self.config.schedule_time),
                ),
                f"[上下文注入] 凌晨时段 ({now.strftime('%H:%M')} < {self.config.schedule_time})，"
                f"延续昨日数据: {yesterday_str}",
            )
            return yesterday_str, True

        self._debug_injection_target_once(
            ("fallback_today", today_str, str(self.config.schedule_time)),
            f"[上下文注入] 凌晨时段 ({now.strftime('%H:%M')} < {self.config.schedule_time})，"
            f"未找到可延续的昨日记录，改用当前日期记录: {today_str}",
        )
        return today_str, False
