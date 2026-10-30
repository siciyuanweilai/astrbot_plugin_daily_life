from __future__ import annotations

import datetime
import copy
from collections.abc import AsyncIterator
from typing import Any

from ..clock import now as life_now
from ..life.tools import build_time_context
from ..runtime import DailyLifeRuntime
from ..runtime.generation import DailyGenerationBusy
from .policy import LifeAccessPolicy, LifeActionProposal, LifeActionScope
from .request import CommandHandler, CommandRequest

from .display import DisplayCommandComponent
from .social import SocialCommandComponent
from .preferences import SettingsCommandComponent
from .operate import OperateCommandComponent
from functools import cached_property
from ..composition import ComponentCall


class _PlainTextEventProxy:
    def __init__(self, event: Any):
        self._event = event

    def plain_result(self, text: Any) -> str:
        return str(text or "")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._event, name)


class DailyLifeCommandCenter:
    """日常生活背景指令路由器。"""

    _SENSITIVE_QUERY_TARGETS = {
        "history",
        "历史",
        "world",
        "世界",
        "preferences",
        "preference",
        "偏好",
        "events",
        "event",
        "事件",
        "config",
        "配置",
    }

    def __init__(
        self,
        runtime: DailyLifeRuntime,
        policy: LifeAccessPolicy | None = None,
    ):
        self.runtime = runtime
        self.policy = policy or LifeAccessPolicy()
        self.handlers: dict[str, CommandHandler] = {
            "帮助": self._help,
            "清空": self._clear,
            "存储": self._storage,
        }

    def permission_denial(
        self,
        event: Any,
        action: str,
        scope: LifeActionScope,
        *,
        payload: dict[str, Any] | None = None,
        resource_owner: str = "",
        reason: str = "",
    ) -> str:
        proposal = LifeActionProposal.build(
            action,
            scope,
            payload=payload,
            resource_owner=resource_owner,
            reason=reason,
        )
        return self.policy.denial(event, proposal)

    async def dispatch(self, event: Any) -> AsyncIterator[Any]:
        req = await self._build_request(event)
        handler = self.handlers.get(req.action)
        if handler:
            protected_scope = {
                "清空": LifeActionScope.ADMIN,
                "存储": LifeActionScope.ADMIN,
            }.get(req.action)
            if protected_scope:
                denial = self.permission_denial(
                    event,
                    f"command:{req.action}",
                    protected_scope,
                )
                if denial:
                    yield event.plain_result(denial)
                    return
            async for item in handler(event, req):
                yield item
            return

        yield event.plain_result("未知指令，使用 /生活 帮助 查看帮助")

    async def _build_request(self, event: Any) -> CommandRequest:
        msg = str(getattr(event, "message_str", "") or "").strip()
        parts = msg.split()
        return await self._make_request(parts)

    async def _make_request(
        self, parts: list[str], *, target_date: str = ""
    ) -> CommandRequest:
        now = life_now()
        time_context = build_time_context(
            now, getattr(self.runtime.config, "schedule_time", "07:00")
        )
        period = time_context.period
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
            today_str=time_context.now.strftime("%Y-%m-%d"),
            yesterday_str=(now - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
            period=period,
            period_cn=time_context.period_cn,
            target_date_str=target_date_str,
        )

    async def _run_text_handler(
        self, event: Any, handler: CommandHandler, req: CommandRequest
    ) -> str:
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
        if target_key in self._SENSITIVE_QUERY_TARGETS:
            denial = self.permission_denial(
                event,
                f"query:{target_key}",
                LifeActionScope.ADMIN,
                payload={"target": target_key},
            )
            if denial:
                return denial
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
        change_scope: str = "clothing",
    ) -> str:
        action_key = str(action or "").strip().lower()
        action_scope = (
            LifeActionScope.ADMIN
            if action_key
            in {
                "reset_day",
                "regenerate",
                "重生成",
                "重置",
                "set_schedule_time",
                "schedule_time",
                "生成时间",
                "时间",
            }
            else LifeActionScope.PRIVATE
        )
        if action_key in {
            "refresh_state",
            "review_action",
            "刷新状态",
            "reset_day",
            "regenerate",
            "重生成",
            "重置",
            "update_outfit",
            "outfit",
            "换装",
            "更新穿搭",
            "set_schedule_time",
            "schedule_time",
            "生成时间",
            "时间",
        }:
            denial = self.permission_denial(
                event,
                f"adjust:{action_key}",
                action_scope,
                payload={
                    "date": date,
                    "period": period,
                    "detail": detail,
                    "schedule_time": schedule_time,
                },
            )
            if denial:
                return denial
        if action_key in {"refresh_state", "刷新状态"}:
            parts = ["/生活", "刷新状态", str(detail or "").strip()]
            req = await self._make_request(parts, target_date=date)
            return await self._run_text_handler(event, self._refresh_state, req)
        if action_key == "review_action":
            return await self.runtime.request_conversation_action(event, detail)
        if action_key in {"reset_day", "regenerate", "重生成", "重置"}:
            parts = ["/生活", "重置"]
            if period:
                parts.append(str(period).strip())
            if detail:
                parts.extend(str(detail).strip().split())
            req = await self._make_request(parts, target_date=date)
            return await self._run_text_handler(event, self._reset, req)
        if action_key in {"update_outfit", "outfit", "换装", "更新穿搭"}:
            from ..outcome import ToolResultText

            change_scope = str(change_scope or "clothing").strip().lower()
            if change_scope not in {"clothing", "appearance"}:
                return ToolResultText(
                    "change_scope 只支持 clothing 或 appearance。",
                    status="failed", media="outfit",
                )
            req = await self._make_request(["/生活", "换装"], target_date=date)
            before = copy.deepcopy(await self.runtime.archive.get_day(req.target_date_str))
            wardrobe = getattr(self.runtime, "wardrobe_life", None)
            remember_request = getattr(wardrobe, "remember_outfit_request", None)
            request_pending = bool(
                before is not None and change_scope == "clothing" and str(detail or "").strip()
                and callable(remember_request)
                and await remember_request(req.target_date_str, str(detail).strip())
            )
            try:
                result, generated_day = await self._execute_outfit_update(
                    req,
                    detail=str(detail or "").strip(),
                    period=str(period or "").strip(),
                    instruction_source="autonomous",
                )
            except DailyGenerationBusy as exc:
                return str(exc)
            if not result:
                if request_pending:
                    await wardrobe.check_wardrobe_life(force_review=True)
                    return ToolResultText(
                        "本轮换装未完成，当前衣物尚未改变。需求已保留，衣橱正在复核可穿替代、洗护或新造型。",
                        status="pending", media="outfit",
                        facts={"clothing_changed": False, "request_pending": True},
                    )
                return "穿搭更新失败。"
            from ..life.outfitstate import outfit_update_feedback

            if before is not None:
                feedback = outfit_update_feedback(before, result)
                if feedback:
                    if request_pending:
                        await wardrobe.check_wardrobe_life(force_review=True)
                        feedback = ToolResultText(
                            str(feedback) + "\n穿搭需求已进入后台复核，尚无生成或换装完成回执。",
                            status="pending", media="outfit",
                            facts={**feedback.facts, "request_pending": True},
                        )
                    return feedback
            prefix = "当天尚无生活安排，已先生成基础日程。\n" if generated_day else ""
            suffix = "" if generated_day else "\n日程保持不变。"
            return f"{prefix}穿搭已更新：{result.outfit or '未记录'}{suffix}"
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
            denial = self.permission_denial(
                event,
                "commitment:memo_tomorrow",
                LifeActionScope.PRIVATE,
                payload={"content": content},
            )
            if denial:
                return denial
            parts = ["/生活", "备忘录", str(content or "").strip()]
            req = await self._make_request(parts)
            return await self._run_text_handler(event, self._memo, req)
        if action_key in {"list", "列表", "query", "查询"}:
            parts = ["/生活", "承诺"]
        elif action_key in {"add", "新增", "添加"}:
            denial = self.permission_denial(
                event,
                "commitment:add",
                LifeActionScope.PRIVATE,
                payload={"content": content, "target_date": target_date},
            )
            if denial:
                return denial
            parts = ["/生活", "承诺", "添加", str(content or "").strip()]
        elif action_key in {"done", "complete", "完成"}:
            parts = ["/生活", "承诺", "完成", str(int(commitment_id or 0))]
        elif action_key in {"cancel", "取消"}:
            parts = ["/生活", "承诺", "取消", str(int(commitment_id or 0))]
        elif action_key in {"reschedule", "delay", "延期", "推迟"}:
            parts = [
                "/生活",
                "承诺",
                "延期",
                str(int(commitment_id or 0)),
                str(target_date or content or "").strip(),
            ]
        else:
            return "未能确认要执行的承诺处理动作。"
        req = await self._make_request(parts)
        if action_key in {"add", "新增", "添加"}:
            req.commitment_target_date = str(target_date or "").strip()[:10]
        return await self._run_text_handler(event, self._commitments, req)

    async def query_weather(self, event: Any, city: str = "") -> str:
        parts = ["/生活", "天气"]
        if city:
            parts.append(str(city).strip())
        req = await self._make_request(parts)
        return await self._run_text_handler(event, self._weather, req)

    async def review_life(
        self, event: Any, action: str = "show", date: str = ""
    ) -> str:
        action_key = str(action or "show").strip().lower()
        denial = self.permission_denial(
            event,
            f"review:{action_key}",
            LifeActionScope.ADMIN,
            payload={"date": date},
        )
        if denial:
            return denial
        parts = ["/生活", "复盘"]
        if action_key in {"generate", "refresh", "生成", "刷新", "重做"}:
            parts.append("生成")
        if date:
            parts.append(str(date).strip()[:10])
        req = await self._make_request(parts, target_date=date)
        return await self._run_text_handler(event, self._review, req)

    _help = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_help")

    _status = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_status")

    _refresh_state = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_refresh_state")

    _show = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_show")

    _spoiler = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_spoiler")

    _timeline_minutes = staticmethod(DisplayCommandComponent._timeline_minutes)

    _history = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_history")

    _world = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_world")

    _timeline = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_timeline")

    _review = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_review")

    _preferences = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_preferences")

    _life_events = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_life_events")

    _storage = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_storage")

    _cleanup_emoji_cache = ComponentCall("_part_display_command_component", DisplayCommandComponent, "_cleanup_emoji_cache")

    _memo = ComponentCall("_part_social_command_component", SocialCommandComponent, "_memo")

    _commitments = ComponentCall("_part_social_command_component", SocialCommandComponent, "_commitments")

    _parse_commitment_id = staticmethod(SocialCommandComponent._parse_commitment_id)

    _infer_manual_commitment_date = staticmethod(SocialCommandComponent._infer_manual_commitment_date)

    _infer_manual_commitment_time = staticmethod(SocialCommandComponent._infer_manual_commitment_time)

    _invite = ComponentCall("_part_social_command_component", SocialCommandComponent, "_invite")

    _time = ComponentCall("_part_settings_command_component", SettingsCommandComponent, "_time")

    _config = ComponentCall("_part_settings_command_component", SettingsCommandComponent, "_config")

    _execute_outfit_update = ComponentCall("_part_operate_command_component", OperateCommandComponent, "_execute_outfit_update")

    _clear = ComponentCall("_part_operate_command_component", OperateCommandComponent, "_clear")

    _reset = ComponentCall("_part_operate_command_component", OperateCommandComponent, "_reset")

    _build_reset_daily_plan = ComponentCall("_part_operate_command_component", OperateCommandComponent, "_build_reset_daily_plan")

    _execute_reset_daily = ComponentCall("_part_operate_command_component", OperateCommandComponent, "_execute_reset_daily")

    _weather = ComponentCall("_part_operate_command_component", OperateCommandComponent, "_weather")

    _sync_weather_to_day = ComponentCall("_part_operate_command_component", OperateCommandComponent, "_sync_weather_to_day")

    @cached_property
    def _part_display_command_component(self):
        return DisplayCommandComponent(self)

    @cached_property
    def _part_social_command_component(self):
        return SocialCommandComponent(self)

    @cached_property
    def _part_settings_command_component(self):
        return SettingsCommandComponent(self)

    @cached_property
    def _part_operate_command_component(self):
        return OperateCommandComponent(self)

