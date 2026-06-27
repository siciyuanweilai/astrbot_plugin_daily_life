import datetime
from typing import Any, AsyncIterator

from ..runtime import DailyLifeRuntime
from ..life.tools import get_time_period_cn
from ..clock import now as life_now
from .display import DisplayCommandMixin
from .operate import OperateCommandMixin
from .preferences import SettingsCommandMixin
from .social import SocialCommandMixin
from .request import CommandHandler, CommandRequest


class DailyLifeCommandCenter(
    DisplayCommandMixin,
    SocialCommandMixin,
    SettingsCommandMixin,
    OperateCommandMixin,
):
    """日常生活背景指令路由器。"""

    def __init__(self, runtime: DailyLifeRuntime):
        self.runtime = runtime
        self.handlers: dict[str, CommandHandler] = {
            "帮助": self._help,
            "状态": self._status,
            "刷新状态": self._refresh_state,
            "剧透": self._spoiler,
            "备忘录": self._memo,
            "承诺": self._commitments,
            "约定": self._commitments,
            "邀约": self._invite,
            "时间": self._time,
            "配置": self._config,
            "模板": self._templates,
            "清空": self._clear,
            "天气": self._weather,
            "历史": self._history,
            "世界": self._world,
            "复盘": self._review,
            "偏好": self._preferences,
            "事件": self._life_events,
            "时间轴": self._timeline,
            "存储": self._storage,
        }

    async def dispatch(self, event: Any) -> AsyncIterator[Any]:
        req = await self._build_request(event)
        if req.action == "显示":
            async for item in self._show(event, req):
                yield item
            return
        if req.action == "重置":
            async for item in self._reset(event, req):
                yield item
            return

        handler = self.handlers.get(req.action)
        if handler:
            async for item in handler(event, req):
                yield item
            return

        yield event.plain_result("未知指令，使用 /生活 帮助 查看帮助")

    async def _build_request(self, event: Any) -> CommandRequest:
        msg = str(getattr(event, "message_str", "") or "").strip()
        parts = msg.split()
        now = life_now()
        period = self.runtime._get_curr_period()
        target_date_str, _ = await self.runtime._resolve_command_target_date(now)
        return CommandRequest(
            parts=parts,
            action=parts[1] if len(parts) > 1 else "状态",
            param1=parts[2] if len(parts) > 2 else "",
            param2=parts[3] if len(parts) > 3 else "",
            param_full=" ".join(parts[2:]) if len(parts) > 2 else "",
            now=now,
            today_str=now.strftime("%Y-%m-%d"),
            yesterday_str=(now - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
            period=period,
            period_cn=get_time_period_cn(period),
            target_date_str=target_date_str,
        )



