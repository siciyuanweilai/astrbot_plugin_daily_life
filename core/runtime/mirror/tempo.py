from __future__ import annotations

import datetime

from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest

from ...clock import now as life_now
from ...life.tools import get_time_period_cn, resolve_business_now
from ..markers import INTERNAL_SESSION_PREFIXES


class SnapshotTempoMixin:
    def _get_time_status(self, now: datetime.datetime | None = None) -> str:
        now = now or life_now()
        period = self._get_curr_period(now)
        period_cn = get_time_period_cn(period)
        now_mins = now.hour * 60 + now.minute

        if now_mins < 3 * 60:
            detail = "深夜/凌晨"
        elif now_mins < 8 * 60:
            detail = "清晨前后"
        elif now_mins < 12 * 60:
            detail = "上午"
        elif now_mins < 14 * 60:
            detail = "中午"
        elif now_mins < 18 * 60:
            detail = "下午"
        elif now_mins < 23 * 60:
            detail = "傍晚/夜间"
        else:
            detail = "深夜"
        return f"当前时间线索：{now.strftime('%H:%M')}，时段标签：{period_cn}（{detail}）；当前是否清醒以实时状态和时间轴为准，life_mode/sleep_mode 只表示今日生成的日程基调与睡眠倾向"

    @staticmethod
    def is_internal_llm_session(req: ProviderRequest) -> bool:
        session_id = getattr(req, "session_id", "")
        return bool(session_id) and session_id.startswith(INTERNAL_SESSION_PREFIXES)

    async def resolve_injection_target(self, now: datetime.datetime) -> tuple[str, bool]:
        today_str = now.strftime("%Y-%m-%d")
        business_now = resolve_business_now(self.config.schedule_time, now)
        target_date_str = business_now.strftime("%Y-%m-%d")
        using_extended_night = business_now.date() < now.date()
        if not using_extended_night:
            return target_date_str, False

        yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if await self.archive.get_day(yesterday_str):
            logger.debug(
                f"[上下文注入] 凌晨时段 ({now.strftime('%H:%M')} < {self.config.schedule_time})，"
                f"延续昨日数据: {yesterday_str}"
            )
            return yesterday_str, True

        logger.debug("[上下文注入] 凌晨时段但无昨日数据，准备生成今日数据")
        return today_str, False
