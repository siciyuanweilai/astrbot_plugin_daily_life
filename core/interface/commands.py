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


class _PlainTextEventProxy:
    def __init__(self, event: Any):
        self._event = event

    def plain_result(self, text: Any) -> str:
        return str(text or "")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._event, name)


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
            "清空": self._clear,
            "存储": self._storage,
        }

    async def dispatch(self, event: Any) -> AsyncIterator[Any]:
        req = await self._build_request(event)
        handler = self.handlers.get(req.action)
        if handler:
            async for item in handler(event, req):
                yield item
            return

        yield event.plain_result("未知指令，使用 /生活 帮助 查看帮助")

    async def _build_request(self, event: Any) -> CommandRequest:
        msg = str(getattr(event, "message_str", "") or "").strip()
        parts = msg.split()
        return await self._make_request(parts)

    async def _make_request(self, parts: list[str], *, target_date: str = "") -> CommandRequest:
        now = life_now()
        period = self.runtime._get_curr_period()
        target_date_str, _ = await self.runtime._resolve_command_target_date(now)
        if target_date:
            target_date_str = str(target_date).strip()[:10]
        return CommandRequest(
            parts=parts,
            action=parts[1] if len(parts) > 1 else "帮助",
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

    async def _run_text_handler(self, event: Any, handler: CommandHandler, req: CommandRequest) -> str:
        proxy = _PlainTextEventProxy(event)
        chunks: list[str] = []
        async for item in handler(proxy, req):
            text = str(item or "").strip()
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip() or "已处理。"

    async def query_life(
        self,
        event: Any,
        target: str = "status",
        *,
        days: int = 7,
        date: str = "",
    ) -> str:
        target_key = str(target or "status").strip().lower()
        target_map: dict[str, tuple[CommandHandler, list[str]]] = {
            "status": (self._status, ["/生活", "状态"]),
            "状态": (self._status, ["/生活", "状态"]),
            "today": (self._show, ["/生活", "显示"]),
            "show": (self._show, ["/生活", "显示"]),
            "schedule": (self._show, ["/生活", "显示"]),
            "今日": (self._show, ["/生活", "显示"]),
            "week": (self._show, ["/生活", "显示", "周计划"]),
            "weekly_plan": (self._show, ["/生活", "显示", "周计划"]),
            "周计划": (self._show, ["/生活", "显示", "周计划"]),
            "future": (self._spoiler, ["/生活", "剧透"]),
            "后续": (self._spoiler, ["/生活", "剧透"]),
            "history": (self._history, ["/生活", "历史", str(max(int(days or 7), 1))]),
            "历史": (self._history, ["/生活", "历史", str(max(int(days or 7), 1))]),
            "world": (self._world, ["/生活", "世界"]),
            "世界": (self._world, ["/生活", "世界"]),
            "timeline": (self._timeline, ["/生活", "时间轴"]),
            "时间轴": (self._timeline, ["/生活", "时间轴"]),
            "preferences": (self._preferences, ["/生活", "偏好"]),
            "preference": (self._preferences, ["/生活", "偏好"]),
            "偏好": (self._preferences, ["/生活", "偏好"]),
            "events": (self._life_events, ["/生活", "事件"]),
            "event": (self._life_events, ["/生活", "事件"]),
            "事件": (self._life_events, ["/生活", "事件"]),
            "config": (self._config, ["/生活", "配置"]),
            "配置": (self._config, ["/生活", "配置"]),
        }
        pair = target_map.get(target_key)
        if not pair:
            return "未能确认要查看的生活信息类型。"
        handler, parts = pair
        req = await self._make_request(parts, target_date=date)
        return await self._run_text_handler(event, handler, req)

    async def adjust_life(
        self,
        event: Any,
        action: str,
        *,
        detail: str = "",
        period: str = "",
        schedule_time: str = "",
        date: str = "",
    ) -> str:
        action_key = str(action or "").strip().lower()
        if action_key in {"refresh_state", "刷新状态"}:
            parts = ["/生活", "刷新状态", str(detail or "").strip()]
            req = await self._make_request(parts, target_date=date)
            return await self._run_text_handler(event, self._refresh_state, req)
        if action_key in {"reset_day", "regenerate", "重生成", "重置"}:
            parts = ["/生活", "重置"]
            if period:
                parts.append(str(period).strip())
            if detail:
                parts.extend(str(detail).strip().split())
            req = await self._make_request(parts, target_date=date)
            return await self._run_text_handler(event, self._reset, req)
        if action_key in {"update_outfit", "outfit", "换装", "更新穿搭"}:
            parts = ["/生活", "重置", str(period or "保持").strip()]
            if detail:
                parts.extend(str(detail).strip().split())
            req = await self._make_request(parts, target_date=date)
            return await self._run_text_handler(event, self._reset, req)
        if action_key in {"set_schedule_time", "schedule_time", "生成时间", "时间"}:
            parts = ["/生活", "时间", str(schedule_time or detail or "").strip()]
            req = await self._make_request(parts, target_date=date)
            return await self._run_text_handler(event, self._time, req)
        return "未能确认要执行的生活调整动作。"

    async def manage_commitment(
        self,
        event: Any,
        action: str,
        *,
        content: str = "",
        commitment_id: int = 0,
        target_date: str = "",
    ) -> str:
        action_key = str(action or "list").strip().lower()
        if action_key in {"memo_tomorrow", "tomorrow_memo", "明日备忘"}:
            parts = ["/生活", "备忘录", str(content or "").strip()]
            req = await self._make_request(parts)
            return await self._run_text_handler(event, self._memo, req)
        if action_key in {"list", "列表", "query", "查询"}:
            parts = ["/生活", "承诺"]
        elif action_key in {"add", "新增", "添加"}:
            parts = ["/生活", "承诺", "添加", str(content or "").strip()]
        elif action_key in {"done", "complete", "完成"}:
            parts = ["/生活", "承诺", "完成", str(int(commitment_id or 0))]
        elif action_key in {"cancel", "取消"}:
            parts = ["/生活", "承诺", "取消", str(int(commitment_id or 0))]
        elif action_key in {"reschedule", "delay", "延期", "推迟"}:
            parts = ["/生活", "承诺", "延期", str(int(commitment_id or 0)), str(target_date or content or "").strip()]
        else:
            return "未能确认要执行的承诺处理动作。"
        req = await self._make_request(parts)
        return await self._run_text_handler(event, self._commitments, req)

    async def query_weather(self, event: Any, city: str = "") -> str:
        parts = ["/生活", "天气"]
        if city:
            parts.append(str(city).strip())
        req = await self._make_request(parts)
        return await self._run_text_handler(event, self._weather, req)

    async def review_life(self, event: Any, action: str = "show", date: str = "") -> str:
        action_key = str(action or "show").strip().lower()
        parts = ["/生活", "复盘"]
        if action_key in {"generate", "refresh", "生成", "刷新", "重做"}:
            parts.append("生成")
        if date:
            parts.append(str(date).strip()[:10])
        req = await self._make_request(parts, target_date=date)
        return await self._run_text_handler(event, self._review, req)
