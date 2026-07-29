from __future__ import annotations

import datetime
import zoneinfo
from .trail import TrailMixin


class RuntimeHistoryMixin(TrailMixin):
    _OBSERVED_USER_HISTORY_MARKER = "daily_life_observed_user"
    _WEEKDAY_NAMES = (
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
        "星期日",
    )

    def _astrbot_now_for_scope(self, scope: str) -> datetime.datetime:
        timezone_name = str(
            self._astrbot_config(scope).get("timezone") or "Asia/Shanghai"
        ).strip()
        if timezone_name:
            try:
                return datetime.datetime.now(zoneinfo.ZoneInfo(timezone_name))
            except Exception:
                return datetime.datetime.now().astimezone()
        return datetime.datetime.now().astimezone()
