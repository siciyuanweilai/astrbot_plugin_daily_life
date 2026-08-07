import asyncio
import inspect
import time
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star_tools import StarTools

try:
    from astrbot.core.tools.web_search_tools import WEB_SEARCH_TOOL_NAMES
except (AttributeError, ImportError):  # AstrBot 精简运行时可能没有完整工具包
    WEB_SEARCH_TOOL_NAMES: tuple[str, ...] = ()

from .core.interface import DailyLifeCommandCenter, DailyLifeDashboardMixin
from .core.runtime import PLUGIN_ID, DailyLifeRuntime
from .core.runtime.markers import LOG_PREFIX
from .core.runtime.send_message_tool import install_expressive_send_message_tool

EXTERNAL_LEASE_SHUTDOWN_TIMEOUT_SECONDS = 10.0
MAP_LLM_TOOL_NAMES = (
    "life_place_search",
    "life_route_plan",
    "life_place_detail",
    "life_outing_plan",
)


class DailyLifePlugin(DailyLifeDashboardMixin, Star):
    """日常生活引擎适配器。"""

    _LLM_RESPONSE_SEEN_ATTR = "_daily_life_llm_response_seen"
    _AGENT_ERROR_SEEN_ATTR = "_daily_life_agent_error_seen"

    _SEND_PIPELINE_STOP_HOOKS = (
        "suppress_recalled_event_result",
        "suppress_intermediate_tool_result",
        "suppress_final_silent_tool_result",
        "suppress_sight_note_followup",
        "hold_life_video_final_text",
        "hold_life_photo_suite_final_text",
    )
    _SEND_PIPELINE_APPLY_HOOKS = (
        ("capture_t2i_source_before_send", False),
        ("apply_chat_plain_text_cleanup_before_send", False),
        ("apply_semantic_segment_before_send", True),
        ("apply_voice_switch_before_send", True),
        ("apply_chat_style_before_send", False),
        ("send_semantic_segments_if_needed", True),
        ("apply_group_addressing_before_send", False),
        ("send_chat_style_segments_if_needed", True),
    )
    _NATURAL_SEGMENTATION_HOOKS = {
        "apply_chat_style_before_send",
        "send_chat_style_segments_if_needed",
    }
    _AFTER_SENT_NOTE_HOOKS = (
        "note_t2i_image_sent",
        "note_structured_sent_result",
        "note_media_source_event",
    )
    _INCOMING_NOTE_HOOKS = (
        "note_structured_incoming_message",
        "schedule_emoji_capture_from_event",
        "schedule_visual_context_from_event",
        "schedule_video_context_from_event",
    )
    _REQUIRED_RUNTIME_METHODS = frozenset(
        _SEND_PIPELINE_STOP_HOOKS
        + tuple(name for name, _ in _SEND_PIPELINE_APPLY_HOOKS)
        + _AFTER_SENT_NOTE_HOOKS
        + _INCOMING_NOTE_HOOKS
        + (
            "note_recalled_message",
            "note_runtime_scope_activity",
            "note_continuous_turn_incoming",
            "settle_continuous_turn",
            "continuous_turn_intentional_wait_seconds",
            "prepare_continuous_turn_llm_request",
            "stop_stale_continuous_turn_event",
            "complete_continuous_turn",
            "note_semantic_segment_incoming_message",
            "capture_chat_memory_message",
            "capture_chat_memory_bot_reply",
            "schedule_bili_summary_from_event",
            "mark_alias_directed_event_as_wake",
            "note_proactive_activity",
            "apply_response_gate_for_event",
            "note_proactive_bot_reply",
            "note_voice_switch_text_result",
            "schedule_pending_chat_state_refresh",
            "_event_has_command_handler",
        )
    )
    _REQUIRED_RUNTIME_ATTRIBUTES = frozenset(
        {
            "archive",
            "config",
            "context_snapshot",
            "model_gateway",
            "reply_delivery",
            "scope_state",
        }
    )
    _SLOW_STAGE_SECONDS = 1.0
    _STAGE_LABELS = {
        "prepare_visual_media_from_event": "图片前置固化",
        "capture_t2i_source_before_send": "转图原文暂存",
        "apply_chat_plain_text_cleanup_before_send": "聊天格式清理",
        "apply_voice_switch_before_send": "语音切换",
        "apply_semantic_segment_before_send": "模型语义分段",
        "apply_chat_style_before_send": "自然分段",
        "send_semantic_segments_if_needed": "语义分段发送",
        "apply_group_addressing_before_send": "群聊寻址",
        "send_chat_style_segments_if_needed": "自然分段发送",
    }

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self._plugin_context = context
        self._plugin_config = config
        self.runtime = None
        self.commands = None
        self._initialize_lock = asyncio.Lock()
        self._external_condition = asyncio.Condition()
        self._external_users = 0
        self._external_tasks: dict[asyncio.Task, int] = {}
        self._terminating = False
        self._external_search_turns: dict[str, None] = {}

    async def initialize(self):
        async with self._initialize_lock:
            if self.runtime is not None and self.commands is not None:
                return
            data_path = await asyncio.to_thread(self._prepare_database)
            runtime = DailyLifeRuntime(
                self._plugin_context,
                self._plugin_config,
                data_path,
                defer_start=True,
            )
            registered_apis = getattr(self._plugin_context, "registered_web_apis", None)
            api_snapshot = (
                list(registered_apis) if isinstance(registered_apis, list) else None
            )
            try:
                self._terminating = False
                self._validate_runtime_contract(runtime)
                await runtime.initialize()
                commands = DailyLifeCommandCenter(runtime)
                self.runtime = runtime
                self.commands = commands
                self._register_page_web_apis()
            # 初始化取消也必须回滚已安装资源，清理后继续抛出原异常。
            except BaseException:
                self.runtime = None
                self.commands = None
                if api_snapshot is not None and isinstance(registered_apis, list):
                    registered_apis[:] = api_snapshot
                try:
                    await runtime.terminate()
                except Exception as cleanup_exc:
                    logger.error(f"[日常生活] 初始化回滚清理失败：{cleanup_exc}")
                raise

    def _prepare_database(self) -> Path:
        data_dir = StarTools.get_data_dir(PLUGIN_ID)
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "daily_life.db"

    async def terminate(self):
        runtime = self.runtime
        async with self._external_condition:
            self._terminating = True
        begin_shutdown = getattr(runtime, "begin_shutdown", None)
        if callable(begin_shutdown):
            await begin_shutdown()
        try:
            async with self._external_condition:
                await asyncio.wait_for(
                    self._external_condition.wait_for(
                        lambda: self._external_users == 0
                    ),
                    timeout=EXTERNAL_LEASE_SHUTDOWN_TIMEOUT_SECONDS,
                )
        except TimeoutError:
            logger.warning(
                f"[日常生活] 等待外部调用结束超时：仍有 {self._external_users} 个调用；"
                "将取消未完成的外部调用后继续关闭插件资源"
            )
            await self._cancel_external_calls()
        try:
            if runtime is not None:
                await runtime.terminate()
        finally:
            self.runtime = None
            self.commands = None

    def _validate_runtime_contract(self, runtime=None) -> None:
        target = runtime if runtime is not None else self.runtime
        missing_methods = sorted(
            name
            for name in self._REQUIRED_RUNTIME_METHODS
            if not callable(getattr(target, name, None))
        )
        missing_attributes = sorted(
            name
            for name in self._REQUIRED_RUNTIME_ATTRIBUTES
            if getattr(target, name, None) is None
        )
        problems = []
        if missing_methods:
            problems.append("方法=" + ", ".join(missing_methods))
        if missing_attributes:
            problems.append("服务=" + ", ".join(missing_attributes))
        if problems:
            raise RuntimeError("日常生活运行时缺少必要能力：" + "；".join(problems))

    @asynccontextmanager
    async def _external_runtime_lease(self):
        condition = getattr(self, "_external_condition", None)
        if not isinstance(condition, asyncio.Condition):
            # 轻量测试替身可能没有完整插件生命周期状态，保留直接单元调用能力。
            yield_runtime = getattr(self, "runtime", None)
            if yield_runtime is None:
                raise RuntimeError("日常生活插件尚未就绪或正在终止")
            service_lease = getattr(yield_runtime, "runtime_service_lease", None)
            if callable(service_lease):
                async with service_lease():
                    yield yield_runtime
            else:
                yield yield_runtime
            return
        async with self._external_condition:
            runtime = self.runtime
            if self._terminating or runtime is None or self.commands is None:
                raise RuntimeError("日常生活插件尚未就绪或正在终止")
            self._external_users += 1
            current_task = asyncio.current_task()
            if current_task is not None:
                self._external_tasks[current_task] = (
                    self._external_tasks.get(current_task, 0) + 1
                )
        try:
            service_lease = getattr(runtime, "runtime_service_lease", None)
            if callable(service_lease):
                async with service_lease():
                    yield runtime
            else:
                yield runtime
        finally:
            async with self._external_condition:
                self._external_users = max(0, self._external_users - 1)
                current_task = asyncio.current_task()
                if current_task is not None:
                    remaining = self._external_tasks.get(current_task, 0) - 1
                    if remaining > 0:
                        self._external_tasks[current_task] = remaining
                    else:
                        self._external_tasks.pop(current_task, None)
                if self._external_users == 0:
                    self._external_condition.notify_all()

    async def _cancel_external_calls(self) -> None:
        """超出排空期限后取消仍占用旧 runtime 的外部任务。"""

        current_task = asyncio.current_task()
        async with self._external_condition:
            tasks = [
                task
                for task in self._external_tasks
                if task is not current_task and not task.done()
            ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _runtime_guard(func):
        """防止运行时服务切换或卸载影响入口调用。"""

        if inspect.isasyncgenfunction(func):

            @wraps(func)
            async def guarded_generator(self, event, *args, **kwargs):
                condition = getattr(self, "_external_condition", None)
                if not isinstance(condition, asyncio.Condition):
                    async for item in func(self, event, *args, **kwargs):
                        yield item
                    return
                async with self._external_runtime_lease():
                    async for item in func(self, event, *args, **kwargs):
                        yield item

            return guarded_generator

        @wraps(func)
        async def guarded(self, event, *args, **kwargs):
            condition = getattr(self, "_external_condition", None)
            if not isinstance(condition, asyncio.Condition):
                return await func(self, event, *args, **kwargs)
            async with self._external_runtime_lease():
                return await func(self, event, *args, **kwargs)

        return guarded

    async def get_life_context(self, target_umo: str = "") -> dict:
        async with self._external_runtime_lease() as runtime:
            return await runtime.get_life_context(target_umo)

    async def search_share_evidence(
        self,
        query: str,
        *,
        category: str,
        target_umo: str = "",
    ) -> dict:
        """为外部分享插件提供统一的联网检索证据。"""
        async with self._external_runtime_lease() as runtime:
            return await runtime.search.search_external_evidence(
                query,
                category=category,
                umo=target_umo,
            )

    async def generate_share_image(
        self,
        event: AstrMessageEvent | None,
        prompt: str,
        *,
        contains_character: bool = False,
    ) -> str:
        async with self._external_runtime_lease() as runtime:
            result = await runtime.generate_life_image_asset(
                event,
                prompt,
                "",
                contains_character=contains_character,
                preserve_reference_ratio=False,
                trusted_identity=contains_character,
            )
            return str(getattr(result, "path", "") or "").strip()

    async def generate_share_video(
        self,
        event: AstrMessageEvent | None,
        prompt: str,
        *,
        reference_image: str = "",
    ) -> str:
        async with self._external_runtime_lease() as runtime:
            result = await runtime.generate_life_video_asset(
                event,
                prompt,
                str(reference_image or "").strip(),
            )
            return str(getattr(result, "url", "") or "").strip()

    async def generate_share_voice(
        self,
        text: str,
        *,
        emotion: str = "",
        emotion_category: str = "",
    ) -> str:
        async with self._external_runtime_lease() as runtime:
            result = await runtime.media.voice.synthesize(
                str(text or "").strip(),
                emotion=emotion,
                emotion_category=emotion_category,
            )
            return str(getattr(result, "path", "") or "").strip()

    async def record_external_activity(
        self,
        target_umo: str,
        content: str,
        *,
        image_description: str = "",
        image_sent: bool = False,
        media_kind: str = "",
        reason: str = "外部主动活动",
        sync_memory: bool = False,
    ) -> bool:
        async with self._external_runtime_lease() as runtime:
            return await runtime.record_external_activity(
                target_umo,
                content,
                image_description=image_description,
                image_sent=image_sent,
                media_kind=media_kind,
                reason=reason,
                sync_memory=sync_memory,
            )

    @staticmethod
    def _tool_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _tool_int(value: object, default: int = 0) -> int:
        try:
            return int(float(str(value or "").strip()))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _event_turn_id(event: AstrMessageEvent) -> str:
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        message_id = ""
        getter = getattr(event, "get_message_id", None)
        if callable(getter):
            try:
                message_id = str(getter() or "").strip()
            except (AttributeError, TypeError, ValueError):
                message_id = ""
        if not message_id:
            message_obj = getattr(event, "message_obj", None)
            message_id = str(getattr(message_obj, "message_id", "") or "").strip()
        if not message_id:
            message_id = str(getattr(event, "message_id", "") or "").strip()
        return f"{umo}:{message_id or f'event-{id(event)}'}"

    def _external_search_turn_store(self) -> dict[str, None]:
        store = getattr(self, "_external_search_turns", None)
        if not isinstance(store, dict):
            store = {}
            self._external_search_turns = store
        return store

    def _mark_external_search_turn(self, event: AstrMessageEvent) -> None:
        store = self._external_search_turn_store()
        store[self._event_turn_id(event)] = None
        while len(store) > 256:
            store.pop(next(iter(store)))

    def _has_external_search_turn(self, event: AstrMessageEvent) -> bool:
        return self._event_turn_id(event) in self._external_search_turn_store()

    @filter.llm_tool(name="accept_user_invite")
    @_runtime_guard
    async def tool_accept_user_invite(
        self,
        event: AstrMessageEvent,
        invite_details: str = "",
    ):
        """
        当对方邀请当前角色共同活动，提出陪伴、见面、一起行动的请求，或确认刚刚由当前角色提出的改约方案时调用。
        结合角色当前状态、已有安排和双方关系，决定自然接受、拒绝或提出其他时间。
        工具结果用于生成最终回复。调用前可以先用角色口吻说一句简短、自然的等待语，表示需要看看自己当下的安排；
        这句话不能提前答应或拒绝，也不能声称已经记录、更新或调整了安排。工具完成后再给出最终态度。

        Args:
            invite_details(string): 当前邀约或对上一条改约方案的确认，例如“晚上8点一起看电影”“现在上号双排”或“好，按你说的傍晚安排”。
        """
        return await self.runtime.accept_user_invite(
            event, str(invite_details or "").strip()
        )

    @filter.llm_tool(name="add_memo_for_tomorrow")
    @_runtime_guard
    async def tool_add_memo_for_tomorrow(
        self,
        event: AstrMessageEvent,
        memo_details: str = "",
    ):
        """
        当对方明确提出明天或未来一天需要记住的计划、约定或待办时调用。
        调用前可以先用角色口吻说一句简短、自然的等待语，表示自己会记一下；
        这句话不能声称已经记录成功。工具完成后，再自然确认已经记住，并按双方关系回应这项安排。

        Args:
            memo_details(string): 用户提到的明天或未来计划，例如“和用户去草坪野餐，负责做三明治”。
        """
        return await self.runtime.add_memo_for_tomorrow(
            event, str(memo_details or "").strip()
        )

    @filter.llm_tool(name="life_query")
    @_runtime_guard
    async def tool_life_query(
        self,
        event: AstrMessageEvent,
        target: str = "status",
        days: int = 7,
        date: str = "",
    ):
        """
        用户明确询问角色内部生活资料时调用，包括状态、今日安排、后续安排、历史、世界记忆、时间轴、偏好、生活事件和配置概览。
        外部事实检索完成后，不要为了组织回答额外查询 status。

        Args:
            target(string): 查询类型，只选一种：status 当前状态；today 今日安排；week 本周计划；future 后续安排；history 最近历史；world 世界记忆；timeline 今日时间轴；preferences 偏好；events 生活事件；config 配置概览。
            days(int): target=history 时查询最近几天，默认 7。
            date(string): 可选日期，格式 YYYY-MM-DD；留空使用当前生活日。
        """
        normalized_target = str(target or "status").strip()
        if normalized_target == "status" and self._has_external_search_turn(event):
            return "当前轮次已有联网搜索结果，无需重复读取角色状态；请直接依据搜索结果回答。"
        return await self.commands.query_life(
            event,
            normalized_target,
            days=self._tool_int(days, 7),
            date=str(date or "").strip(),
        )

    @filter.llm_tool(name="life_adjust")
    @_runtime_guard
    async def tool_life_adjust(
        self,
        event: AstrMessageEvent,
        action: str,
        detail: str = "",
        period: str = "",
        schedule_time: str = "",
        date: str = "",
    ):
        """
        调整当前角色的日常生活状态或生成节奏。

        Args:
            action(string): 调整动作：refresh_state 刷新实时状态；reset_day 重新生成当天；update_outfit 保持日程只更新穿搭；set_schedule_time 设置每日生成时间。
            detail(string): 用户补充的自然语言要求，例如“今天少出门”“刚刚聊了很久所以状态偏困”。
            period(string): 可选目标时段，例如 凌晨/早晨/上午/中午/下午/傍晚/晚上/深夜；用于重生成某时段状态或穿搭。
            schedule_time(string): action=set_schedule_time 时使用，格式 HH:MM。
            date(string): 可选日期，格式 YYYY-MM-DD；留空使用当前生活日。
        """
        return await self.commands.adjust_life(
            event,
            str(action or "").strip(),
            detail=str(detail or "").strip(),
            period=str(period or "").strip(),
            schedule_time=str(schedule_time or "").strip(),
            date=str(date or "").strip(),
        )

    @filter.llm_tool(name="life_commitment")
    @_runtime_guard
    async def tool_life_commitment(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        content: str = "",
        commitment_id: int = 0,
        target_date: str = "",
    ):
        """
        管理未来承诺、约定和明日备忘。

        Args:
            action(string): 操作：list 查看未完成承诺；add 新增承诺；done 标记完成；cancel 取消；reschedule 延期；memo_tomorrow 写入明日强制备忘。
            content(string): 新增承诺、明日备忘或延期说明。
            commitment_id(int): done/cancel/reschedule 时的承诺编号。
            target_date(string): add 或 reschedule 时的目标日期，格式 YYYY-MM-DD；留空时由内容与当前语境判断。
        """
        return await self.commands.manage_commitment(
            event,
            str(action or "list").strip(),
            content=str(content or "").strip(),
            commitment_id=self._tool_int(commitment_id, 0),
            target_date=str(target_date or "").strip(),
        )

    @filter.llm_tool(name="life_weather")
    @_runtime_guard
    async def tool_life_weather(
        self,
        event: AstrMessageEvent,
        city: str = "",
    ):
        """
        查询天气；查询默认居住地天气时会同步到当前生活日。

        Args:
            city(string): 可选城市名；留空使用当前地图服务从居住地解析出的城市。
        """
        return await self.commands.query_weather(event, str(city or "").strip())

    @filter.llm_tool(name="life_place_search")
    @_runtime_guard
    async def tool_life_place_search(
        self,
        event: AstrMessageEvent,
        query: str,
        near: str = "",
        category: str = "",
        radius_meters: int = 3000,
        limit: int = 5,
    ):
        """
        用户想寻找餐厅、咖啡店、书店、公园、商场、车站等现实地点时调用。
        只提交用户真正需要的一次搜索；“附近”必须结合当前生活上下文填写明确的 near，不能猜测用户本人位置。

        Args:
            query(string): 自然语言地点需求，例如“安静、适合看书的咖啡店”。
            near(string): 可选搜索中心，例如“祖庙地铁站”；留空时在居住地解析出的城市内搜索。
            category(string): 可选地点分类或类型编码；不确定时留空。
            radius_meters(int): near 不为空时的搜索半径，默认 3000，范围 100 到 50000。
            limit(int): 返回地点数，默认 5，最多 10。
        """
        del event
        return await self.runtime.domains.tool_place_search(
            str(query or "").strip(),
            near=str(near or "").strip(),
            category=str(category or "").strip(),
            radius_meters=self._tool_int(radius_meters, 3000),
            limit=self._tool_int(limit, 5),
        )

    @filter.llm_tool(name="life_route_plan")
    @_runtime_guard
    async def tool_life_route_plan(
        self,
        event: AstrMessageEvent,
        origin: str,
        destination: str,
        mode: str = "walking",
    ):
        """
        用户询问两个地点之间怎么走、多久能到、哪种方式更快，或日程需要核验出行时间时调用。
        起终点都必须是明确的自然语言地点，不要传递经纬度。

        Args:
            origin(string): 出发地，例如“家”或“祖庙地铁站”；上下文不能确认时应先询问用户。
            destination(string): 目的地名称或地址。
            mode(string): walking 步行；cycling 骑行；driving 驾车；transit 公交；compare 比较全部方式。
        """
        del event
        return await self.runtime.domains.tool_route_plan(
            str(origin or "").strip(),
            str(destination or "").strip(),
            mode=str(mode or "walking").strip(),
        )

    @filter.llm_tool(name="life_place_detail")
    @_runtime_guard
    async def tool_life_place_detail(
        self,
        event: AstrMessageEvent,
        poi_id: str,
    ):
        """
        用户追问地点搜索结果的地址、电话、营业信息、评分或照片时调用。
        仅使用前一次 life_place_search 返回的 POI ID，不要自行编造 ID。

        Args:
            poi_id(string): 地点搜索结果中的 POI ID。
        """
        del event
        return await self.runtime.domains.tool_place_detail(str(poi_id or "").strip())

    @filter.llm_tool(name="life_outing_plan")
    @_runtime_guard
    async def tool_life_outing_plan(
        self,
        event: AstrMessageEvent,
        request: str,
        stops: list[str] | None = None,
        start: str = "",
        mode: str = "walking",
        duration_minutes: int = 120,
        max_stops: int = 3,
    ):
        """
        用户希望组合多个现实地点形成半日、晚间或周末外出安排时调用。
        先语义理解用户需求，再把每个停靠目标写入 stops；不要把整句要求重复塞进多个停靠项。

        Args:
            request(string): 用户完整的自然语言外出需求。
            stops(list[string]): 按顺序排列的地点搜索目标，例如[“独立书店”, “广式糖水”]。
            start(string): 明确的出发地点；不能确认时先询问用户。
            mode(string): walking、cycling、driving 或 transit，默认 walking。
            duration_minutes(int): 总时间预算，默认 120，范围 30 到 1440。
            max_stops(int): 最多停靠数，默认 3，范围 1 到 5。
        """
        del event
        return await self.runtime.domains.tool_outing_plan(
            str(request or "").strip(),
            list(stops or []),
            start=str(start or "").strip(),
            mode=str(mode or "walking").strip(),
            duration_minutes=self._tool_int(duration_minutes, 120),
            max_stops=self._tool_int(max_stops, 3),
        )

    @filter.llm_tool(name="life_review")
    @_runtime_guard
    async def tool_life_review(
        self,
        event: AstrMessageEvent,
        action: str = "show",
        date: str = "",
    ):
        """
        查看或生成每日复盘。

        Args:
            action(string): show 查看已有复盘；generate 重新生成复盘。
            date(string): 可选日期，格式 YYYY-MM-DD；留空使用当前生活日。
        """
        return await self.commands.review_life(
            event,
            str(action or "show").strip(),
            date=str(date or "").strip(),
        )

    @filter.llm_tool(name="life_memory_search")
    @_runtime_guard
    async def tool_life_memory_search(
        self,
        event: AstrMessageEvent,
        query: str = "",
        mode: str = "search",
        category: str = "",
        limit: int = 5,
    ):
        """
        检索当前角色已经沉淀的长期生活记忆，用于回答用户追问、确认偏好、查找纠偏、人物关系、生活事件或最近影响判断的依据。
        Args:
            query(string): 自然语言检索内容，例如“最近别总出门”“他喜欢什么生活节奏”“好友相关记忆”。
            mode(string): search 按内容检索；recent 查看最近沉淀的长期记忆。
            category(string): 可选分类过滤，例如 correction、short_term、relationship、chat_summary、feedback、expression。
            limit(int): 返回条数，默认 5，最多 12。
        """
        return await self.runtime.life_memory_search(
            event,
            query=str(query or "").strip(),
            mode=str(mode or "search").strip(),
            category=str(category or "").strip(),
            limit=self._tool_int(limit, 5),
        )

    @filter.llm_tool(name="life_web_search")
    @_runtime_guard
    async def tool_life_web_search(
        self,
        event: AstrMessageEvent,
        query: str = "",
        depth: str = "quick",
        platform: str = "",
        source_scope: str = "web",
        time_range: str = "",
        start_date: str = "",
        end_date: str = "",
        image_search: bool = False,
        image_understanding: bool = False,
        topic: str = "general",
        include_raw_content: bool = False,
        include_images: bool = False,
        include_image_descriptions: bool = False,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        country: str = "",
        auto_parameters: bool = False,
        exact_match: bool = False,
    ):
        """
        需要查询当前或外部网络事实、新闻、资料、官方文档和实时信息时调用。
        普通闲聊、角色记忆和日常状态不需要调用。quick 适合单个问题，deep 适合需要多来源核验的问题。
        网页搜索由 Tavily 提供；source_scope=x 时使用 Grok X 搜索，both 会并发获取网页与 X 来源。
        优先只提交一个自包含问题；deep 会在证据不足时自行规划补充查询，不要在首次调用时同时批量提交多个近似问题。
        若工具结果 quality=strong 且 missing_aspects 为空，
        说明本轮证据已经充分，应停止搜索，不要重复查询同一问题；
        只有存在未覆盖的关键信息、来源冲突、用户追问或明确的新问题时才继续搜索。
        如果本轮请求了图片且工具结果 image_count 大于 0，说明图片已经准备好；下一步应优先调用 send_message_to_user 发送图片，
        不要继续重复搜索。image_quality 只表示图片可用程度，与文字证据 quality 独立。
        时间范围仅在用户明确要求时填写；它限制网页发布日期，不代表事件发生日期。
        Args:
            query(string): 自包含的自然语言搜索问题。
            depth(string): quick 或 deep。
            platform(string): 可选的网站或平台范围，例如 GitHub、Reddit 或官方站点。
            source_scope(string): web 搜索网页；x 搜索 X；both 同时搜索网页和 X。默认 web。
            time_range(string): 可选的来源发布日期范围：day、week、month 或 year；仅在问题明确要求近期信息时填写，否则留空。
            start_date(string): 可选网页发布开始日期，格式 YYYY-MM-DD；优先于 time_range。
            end_date(string): 可选网页发布结束日期，格式 YYYY-MM-DD；优先于 time_range。
            image_search(boolean): 是否需要搜索可直接展示的相关图片。
            image_understanding(boolean): 是否需要理解搜索网页中出现的图片内容。
            topic(string): Tavily 搜索主题，只能填 general、news 或 finance。
            include_raw_content(boolean): 是否返回搜索结果的清洗正文；需要核对页面内容时填写 true。
            include_images(boolean): 是否返回搜索结果中的相关图片。
            include_image_descriptions(boolean): 是否为返回的图片生成描述；只有 include_images=true 时有意义。
            include_domains(array[string]): 可选优先搜索的域名列表。
            exclude_domains(array[string]): 可选排除的域名列表。
            country(string): 可选国家或地区代码；仅在 general 主题下填写。
            auto_parameters(boolean): 是否让 Tavily 根据问题自动选择搜索参数；复杂问题可填写 true。
            exact_match(boolean): 是否只保留包含查询原文的结果。
        """
        async with self.runtime.runtime_service_lease():
            return await self.runtime.search.tool_search(
                str(query or "").strip(),
                str(depth or "quick").strip(),
                str(platform or "").strip(),
                source_scope=str(source_scope or "web").strip(),
                time_range=str(time_range or "").strip(),
                start_date=str(start_date or "").strip(),
                end_date=str(end_date or "").strip(),
                image_search=self._tool_bool(image_search),
                image_understanding=self._tool_bool(image_understanding),
                topic=str(topic or "general").strip(),
                include_raw_content=self._tool_bool(include_raw_content),
                include_images=self._tool_bool(include_images),
                include_image_descriptions=self._tool_bool(include_image_descriptions),
                include_domains=include_domains or [],
                exclude_domains=exclude_domains or [],
                country=str(country or "").strip(),
                auto_parameters=self._tool_bool(auto_parameters),
                exact_match=self._tool_bool(exact_match),
                umo=str(event.unified_msg_origin or ""),
                turn_id=self._event_turn_id(event),
            )

    @filter.llm_tool(name="life_web_fetch")
    @_runtime_guard
    async def tool_life_web_fetch(
        self,
        event: AstrMessageEvent,
        url: str = "",
        urls: list[str] | None = None,
        query: str = "",
        chunks_per_source: int = 3,
        extract_depth: str = "advanced",
        include_images: bool = False,
        include_favicon: bool = False,
        format: str = "markdown",
    ):
        """
        已经有明确网页地址、需要读取网页正文或核对原文时调用，不用于普通搜索。
        工具会优先提取网页正文；提取失败时会在同一时间预算内自动执行URL定向搜索，不需要再次调用搜索工具。
        返回 mode=page_extract 表示取得网页正文；mode=search_fallback 表示使用搜索资料补充，后者不能表述为逐字读完原文。
        Args:
            url(string): 要读取的单个网页地址。
            urls(array[string]): 可选多个网页地址；批量核验时使用，填写后优先于 url。
            query(string): 可选正文相关性问题，用于提取与问题最相关的片段。
            chunks_per_source(number): 每个来源最多返回的相关片段数，范围 1 到 5。
            extract_depth(string): basic 或 advanced；需要表格和嵌入内容时使用 advanced。
            include_images(boolean): 是否返回页面图片。
            include_favicon(boolean): 是否返回页面 favicon。
            format(string): markdown 或 text。
        """
        async with self.runtime.runtime_service_lease():
            return await self.runtime.search.tool_fetch(
                str(url or "").strip(),
                urls=urls or [],
                query=str(query or "").strip(),
                chunks_per_source=self._tool_int(chunks_per_source, 3),
                extract_depth=str(extract_depth or "advanced").strip(),
                include_images=self._tool_bool(include_images),
                include_favicon=self._tool_bool(include_favicon),
                format=str(format or "markdown").strip(),
                umo=str(event.unified_msg_origin or ""),
            )

    @filter.llm_tool(name="life_web_map")
    @_runtime_guard
    async def tool_life_web_map(
        self,
        event: AstrMessageEvent,
        url: str = "",
        instructions: str = "",
        max_depth: int = 1,
        max_breadth: int = 50,
        limit: int = 100,
        select_paths: list[str] | None = None,
        select_domains: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        allow_external: bool = True,
    ):
        """
        需要探索一个网站的文档、栏目或页面结构时调用，不用于普通网页搜索。
        Args:
            url(string): 网站首页或站点入口地址。
            instructions(string): 可选的站点内容筛选说明。
            max_depth(int): 映射深度，范围 1 到 5。
            max_breadth(int): 每层最多跟进链接数。
            limit(int): 总链接数上限。
            select_paths(array[string]): 只保留匹配路径规则的页面。
            select_domains(array[string]): 只保留匹配域名规则的页面。
            exclude_paths(array[string]): 排除匹配路径规则的页面。
            exclude_domains(array[string]): 排除匹配域名规则的页面。
            allow_external(boolean): 是否允许外部域名链接。
        """
        async with self.runtime.runtime_service_lease():
            return await self.runtime.search.tool_map(
                str(url or "").strip(),
                str(instructions or "").strip(),
                self._tool_int(max_depth, 1),
                max_breadth=self._tool_int(max_breadth, 50),
                limit=self._tool_int(limit, 100),
                select_paths=select_paths or [],
                select_domains=select_domains or [],
                exclude_paths=exclude_paths or [],
                exclude_domains=exclude_domains or [],
                allow_external=self._tool_bool(allow_external),
                umo=str(event.unified_msg_origin or ""),
            )

    @filter.llm_tool(name="life_web_crawl")
    @_runtime_guard
    async def tool_life_web_crawl(
        self,
        event: AstrMessageEvent,
        url: str = "",
        instructions: str = "",
        max_depth: int = 1,
        max_breadth: int = 20,
        limit: int = 50,
        select_paths: list[str] | None = None,
        select_domains: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        allow_external: bool = True,
        include_images: bool = False,
        include_favicon: bool = False,
        extract_depth: str = "advanced",
        format: str = "markdown",
    ):
        """
        需要批量读取网站多个页面、完整文档或项目资料时调用；普通单页读取使用 life_web_fetch，站点目录发现使用 life_web_map。
        Args:
            url(string): 网站入口地址。
            instructions(string): 可选抓取目标和内容筛选说明。
            max_depth(int): 抓取深度，范围 1 到 5。
            max_breadth(int): 每层最多跟进链接数。
            limit(int): 总页面上限。
            select_paths(array[string]): 可选保留路径规则。
            select_domains(array[string]): 可选保留域名规则。
            exclude_paths(array[string]): 可选排除路径规则。
            exclude_domains(array[string]): 可选排除域名规则。
            allow_external(boolean): 是否允许抓取外部域名。
            include_images(boolean): 是否返回页面图片。
            include_favicon(boolean): 是否返回页面 favicon。
            extract_depth(string): basic 或 advanced。
            format(string): markdown 或 text。
        """
        async with self.runtime.runtime_service_lease():
            return await self.runtime.search.tool_crawl(
                str(url or "").strip(),
                instructions=str(instructions or "").strip(),
                max_depth=self._tool_int(max_depth, 1),
                max_breadth=self._tool_int(max_breadth, 20),
                limit=self._tool_int(limit, 50),
                select_paths=select_paths or [],
                select_domains=select_domains or [],
                exclude_paths=exclude_paths or [],
                exclude_domains=exclude_domains or [],
                allow_external=self._tool_bool(allow_external),
                include_images=self._tool_bool(include_images),
                include_favicon=self._tool_bool(include_favicon),
                extract_depth=str(extract_depth or "advanced").strip(),
                format=str(format or "markdown").strip(),
                umo=str(event.unified_msg_origin or ""),
            )

    @filter.llm_tool(name="life_web_research")
    @_runtime_guard
    async def tool_life_web_research(
        self,
        event: AstrMessageEvent,
        input_text: str = "",
        model: str = "auto",
        output_length: str = "standard",
        citation_format: str = "numbered",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        output_schema: dict | None = None,
    ):
        """
        只有用户明确要求深入调查、全面研究、写研究报告或多角度分析时调用。
        工具会创建 Tavily Research 后台任务，返回任务编号；收到 pending 后等待并调用 life_web_research_status 查询，不要声称报告已经完成。
        Args:
            input_text(string): 完整研究问题和目标。
            model(string): mini、pro 或 auto；复杂跨主题问题使用 pro。
            output_length(string): short、standard 或 long。
            citation_format(string): numbered、mla、apa 或 chicago。
            include_domains(array[string]): 优先参考的域名。
            exclude_domains(array[string]): 排除的域名。
            output_schema(object): 可选 JSON Schema 结构化输出。
        """
        async with self.runtime.runtime_service_lease():
            return await self.runtime.search.tool_research(
                str(input_text or "").strip(),
                model=str(model or "auto").strip(),
                output_length=str(output_length or "standard").strip(),
                citation_format=str(citation_format or "numbered").strip(),
                include_domains=include_domains or [],
                exclude_domains=exclude_domains or [],
                output_schema=output_schema,
                umo=str(event.unified_msg_origin or ""),
            )

    @filter.llm_tool(name="life_web_research_status")
    @_runtime_guard
    async def tool_life_web_research_status(
        self, event: AstrMessageEvent, task_id: str = ""
    ):
        """
        查询 life_web_research 返回的研究任务状态；只有已有任务编号时调用。
        Args:
            task_id(string): 研究任务编号。
        """
        async with self.runtime.runtime_service_lease():
            return await self.runtime.search.tool_research_status(
                str(task_id or "").strip(),
                umo=str(event.unified_msg_origin or ""),
            )

    @filter.llm_tool(name="life_image_generate")
    @_runtime_guard
    async def tool_life_image_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        subject_route: str = "free",
        participants: list[str] | None = None,
        friend_outfit: str = "",
        friend_hair: str = "",
        friend_scene_category: str = "",
        current_outfit_change: bool = False,
        current_outfit_instruction: str = "",
        use_last_reverse_prompt: bool = False,
        resolution: str = "",
        provider: str = "",
    ):
        """
        根据当前角色生活场景生成并发送一张图片。
        适合用户想看当前状态、穿搭、环境、自拍/生活照，或普通聊天里用画面展示此刻更自然的时候。
        用户已经明确要图片时，调用前可以先用角色口吻说一句简短、自然的行动确认；不能提前声称图片已经完成，
        也不要提及模型、任务、缓存、图片导演、文生图或图生图等内部过程。图片发送后再根据结果自然补一句，也可以不补。
        如果用户本轮已经给出完整图片提示词且 current_outfit_change=false，除单独填写 provider 外，prompt 必须原样保留画面要求，不要改写、摘要或另想场景；不要把协议选择语句混入画面提示词。
        使用 subject_route 明确图片主体：current_character 当前角色本人入镜；group 当前角色与一位已配置好友合影；scene 环境/氛围/状态；object 物品/食物；free 不限定主体或完整自由提示词。
        current_character 场景中，用户没有另行指定穿搭、发型或造型风格时，应参考系统注入的当前外观状态补足可见细节；用户本轮明确要求始终优先，不能用生活背景覆盖。
        用户明确要求当前角色实际“换上、穿上、改成”某套穿搭时，设置 current_outfit_change=true，并把用户原始穿搭要求原样放入 current_outfit_instruction；工具会先更新真实生活穿搭状态，再使用更新后的同一套造型生图。
        仅要求生成、查看、试穿效果或创作某种穿搭图片时，不得设置 current_outfit_change；这类画面不会改变当前角色的真实生活穿搭状态。
        current_outfit_change=true 时，subject_route 只能填 current_character 或 group，prompt 只描述场景、动作、构图等画面要求，不要另外编造一套当前角色服装；插件会把已保存造型锁定到画面中。
        合影时 participants 必须填写系统上下文“可用于合影的好友参考档案”中对应的关系档案 ID，只选择一位好友；不要按姓名猜测或编造 ID。
        合影提示词中把当前角色作为人物 A、好友作为人物 B，分别描述两人的服装、发型、体态和外观呈现；未明确归属的单套穿搭默认只属于人物 A。
        人物 B 应根据好友参考图保持独立外观并选择符合场景的独立穿搭；不要根据姓名或昵称猜测性别。只有用户明确要求同款、情侣装或统一造型时才共享穿搭风格。
        group 模式每次都填写 friend_scene_category。好友已有当天造型且服装属性适合当前场景时，friend_outfit 和 friend_hair 留空以继续沿用；场景不适用时工具会要求更新。
        没有当天造型时必须同时填写人物 B 的完整穿搭和发型。人物 B 换装时填写 friend_outfit，改变发型时填写 friend_hair；服装属性和造型决定由插件根据场景推导。
        不能只把人物 B 的新造型写进 prompt，否则不会更新当天造型。外出服回家后可以继续穿，居家/睡眠服进入公共或外出场景时不能直接沿用。
        没有当前消息、引用消息或显式 reference_image 可作为真实参考图时，新增画面请求使用本工具，不要调用 edit_life_image。
        如果用户要求使用上一条图片反推结果生成，设置 use_last_reverse_prompt=true；插件会从本会话缓存读取上一条反推提示词原文，不会自动把反推原图作为图生图参考。
        有真实参考图时，如果用户明确要求按原图或参考图生成、改图或保持原图人物/构图，请调用 edit_life_image。

        Args:
            prompt(string): 图片画面要求；用户给出完整提示词时必须保留完整画面内容，例如“雨夜沙发上随手拍的一张生活照，暖色台灯，慵懒居家感，半身生活照”；协议选择单独填写 provider，use_last_reverse_prompt=true 时此参数不参与生成。
            subject_route(string): 图片主体路线，只能填 current_character、group、scene、object 或 free；用户想看当前角色本人、自拍、生活照或穿搭照时填 current_character，想与已配置好友合影时填 group。
            participants(list[string]): 合影参与者的关系档案 ID；subject_route=group 时必须且只能填写一位已配置好友，其他路线留空。
            friend_outfit(string): 人物 B 本次完整穿搭；首次合影必须与 friend_hair 同时填写，已有当天造型时仅在本轮换装时填写。
            friend_hair(string): 人物 B 本次发型；首次合影必须与 friend_outfit 同时填写，已有当天造型时仅在本轮改变发型时填写。
            friend_scene_category(string): 人物 B 当前画面场景，只能填 home、sleep、outdoor、public 或 mixed；subject_route=group 时每次填写。
            current_outfit_change(bool): 是否把用户本轮要求作为当前角色真实换装写入生活状态；只有用户明确要求实际换装时设为 true，仅看效果图时必须为 false。
            current_outfit_instruction(string): 当前角色真实换装要求；current_outfit_change=true 时填写用户原始要求，不得自行扩写成另一套服装，其他情况留空。
            use_last_reverse_prompt(bool): 是否使用本会话上一条图片反推提示词原文。
            resolution(string): 可选输出分辨率，只能填 1K、2K 或 4K；仅当用户明确要求输出分辨率时填写，“高清”等模糊描述不要推断，其他语境里的 1K、2K、4K 也不要误填。
            provider(string): 可选图片接口，只能填 auto、gpt 或 gemini；仅当用户明确要求使用 GPT 或 Gemini 时填写，否则留空。
        """
        use_reverse_cache = self._tool_bool(use_last_reverse_prompt)
        options = {
            "use_last_reverse_prompt": use_reverse_cache,
            "subject_route": str(subject_route or "free").strip(),
        }
        if participants:
            options["participants"] = participants
        if str(friend_outfit or "").strip():
            options["friend_outfit"] = str(friend_outfit).strip()
        if str(friend_hair or "").strip():
            options["friend_hair"] = str(friend_hair).strip()
        if str(friend_scene_category or "").strip():
            options["friend_scene_category"] = str(friend_scene_category).strip()
        apply_outfit_change = self._tool_bool(current_outfit_change)
        if apply_outfit_change:
            options["current_outfit_change"] = True
            options["current_outfit_instruction"] = str(
                current_outfit_instruction or ""
            ).strip()
        requested_resolution = str(resolution or "").strip().upper()
        if requested_resolution:
            options["resolution"] = requested_resolution
        if str(provider or "").strip():
            options["provider"] = str(provider).strip()
        return await self.runtime.life_image_generate(
            event,
            "" if use_reverse_cache else str(prompt or "").strip(),
            **options,
        )

    @filter.llm_tool(name="life_photo_suite_generate")
    @_runtime_guard
    async def tool_life_photo_suite_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        count: int = 3,
        reference_image: str = "",
        continue_last_result: bool = False,
        subject_route: str = "free",
        participants: list[str] | None = None,
        friend_outfit: str = "",
        friend_hair: str = "",
        friend_scene_category: str = "",
        retry_indexes: list[int] | None = None,
        resolution: str = "",
        provider: str = "",
    ):
        """
        仅当用户明确要求“拍一套”“来组照片”“多拍几张”等一组独立照片时调用；普通单张图片仍调用 life_image_generate。
        默认生成 3 张，可按用户要求生成 2 到 6 张。调用前可以先用角色口吻说一句简短、自然的行动确认；
        不能提前声称整组已经拍好，也不要提及模型、任务、并发、缓存或生成流程。工具会在后台规划并生成整组照片，
        一次发送成功图片，交付后再按实际结果自然补一句。不要为了生成套图而自行连续调用多次单图工具。
        同一组会保持人物身份、人数、发型、服装、场景、时间、光线和画面风格一致，只让景别、机位、姿势和动作产生变化。
        current_character 套图中，用户没有另行指定穿搭、发型或造型风格时，应参考系统注入的当前外观状态；用户本轮明确要求始终优先。
        current_character 用于当前角色本人套图；group 用于当前角色与一位已配置好友的合影套图，participants 必须且只能填写系统给出的关系档案 ID。
        合影套图中把当前角色作为人物 A、好友作为人物 B，分别固定两人的服装、发型、体态和外观呈现，不能把一人的穿搭复制给另一人。
        未明确归属的单套穿搭默认只属于人物 A；好友参考图只用于确认人物 B 的身份，人物 B 的本轮穿搭和发型通过结构化参数独立确定，不根据姓名或昵称猜测性别。只有明确要求同款、情侣装或统一造型时才共享穿搭风格。
        新合影套图每次填写 friend_scene_category。好友已有当天造型且适合当前场景时，friend_outfit 和 friend_hair 留空；没有当天造型时必须同时填写完整穿搭和发型，整组保持一致。
        人物 B 换装时填写 friend_outfit，改变发型时填写 friend_hair；服装属性和造型决定由插件根据场景推导。不能只把新造型写进 prompt。
        有当前消息或引用图片时，reference_image 可留空，插件会自动读取；需要沿用当前会话上一张生成结果时设置 continue_last_result=true。
        用户要求重拍上一组中的某几张时，retry_indexes 填写从 1 开始的位置序号；重拍不需要任务编号，也不要重复规划或重做其他成功照片。

        Args:
            prompt(string): 整组照片的主题、人物、场景和氛围要求；重拍指定位置时可以留空。
            count(int): 照片数量，默认 3，范围 2 到 6。
            reference_image(string): 可选参考图片路径或 URL；留空时自动尝试当前消息或引用消息里的图片。
            continue_last_result(bool): 没有新参考图时，是否把当前会话上一张成功生成结果作为整组画面参考。
            subject_route(string): 主体路线，只能填 current_character、group、scene、object 或 free。
            participants(list[string]): 合影好友的关系档案 ID；subject_route=group 时必须且只能填写一位，其他路线留空。
            friend_outfit(string): 人物 B 本次完整穿搭；首次合影套图必须与 friend_hair 同时填写，已有当天造型时仅在本轮换装时填写。
            friend_hair(string): 人物 B 本次发型；首次合影套图必须与 friend_outfit 同时填写，已有当天造型时仅在本轮改变发型时填写。
            friend_scene_category(string): 人物 B 当前画面场景，只能填 home、sleep、outdoor、public 或 mixed；新合影套图每次填写，重拍沿用清单无需填写。
            retry_indexes(list[int]): 可选重拍位置，使用从 1 开始的序号，例如 [2, 4]；普通新套图留空。
            resolution(string): 可选输出分辨率，只能填 1K、2K 或 4K；仅当用户明确要求整组照片的输出分辨率时填写，“高清”等模糊描述不要推断。
            provider(string): 可选图片接口，只能填 auto、gpt 或 gemini；仅当用户明确要求使用 GPT 或 Gemini 时填写，否则留空。
        """
        options = {
            "count": self._tool_int(count, 3),
            "reference_image": str(reference_image or "").strip(),
            "continue_last_result": self._tool_bool(continue_last_result),
            "subject_route": str(subject_route or "free").strip(),
            "retry_indexes": retry_indexes or [],
        }
        if participants:
            options["participants"] = participants
        if str(friend_outfit or "").strip():
            options["friend_outfit"] = str(friend_outfit).strip()
        if str(friend_hair or "").strip():
            options["friend_hair"] = str(friend_hair).strip()
        if str(friend_scene_category or "").strip():
            options["friend_scene_category"] = str(friend_scene_category).strip()
        requested_resolution = str(resolution or "").strip().upper()
        if requested_resolution:
            options["resolution"] = requested_resolution
        if str(provider or "").strip():
            options["provider"] = str(provider).strip()
        return await self.runtime.life_photo_suite_generate(
            event,
            str(prompt or "").strip(),
            **options,
        )

    @filter.llm_tool(name="edit_life_image")
    @_runtime_guard
    async def tool_edit_life_image(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        reference_image: str = "",
        continue_last_result: bool = False,
        generate_without_reference: bool = False,
        participants: list[str] | None = None,
        resolution: str = "",
        provider: str = "",
    ):
        """
        根据参考图生成并发送一张新的生活图片；适合用户发图、引用图或明确给出图片链接/路径后再改图。
        用户已经明确要修改图片时，调用前可以先用角色口吻说一句简短、自然的行动确认；不能提前声称已经改好，
        也不要说明参考图解析、模型调用或生成流程。图片发送后再根据结果自然补一句，也可以不补。
        reference_image 留空时会自动尝试当前消息或引用消息里的图片。
        用户说“继续改”“再改一下”“修改上一张”或要求在刚生成的版本上继续调整，并且本轮没有发送或引用新图片时，
        continue_last_result 必须设为 true，让工具使用当前会话上一张成功生成的图片；不要复用历史消息里的 AstrBot 临时图片路径。
        只有真实参考图存在时才优先使用本工具；如果没有真实参考图，默认只提醒用户先发送或引用图片。
        generate_without_reference 仅当已确定这次不是继续改图、而是允许按同一画面要求生成新图时才设为 true；
        连续修改失败时不能用它重新生成，否则会丢失上一张图片的画面连续性，通常应直接调用 life_image_generate。
        用户要求当前角色与已配置好友参考某张场景图合影时，participants 填写系统上下文给出的一个关系档案 ID；用户图片只作为场景、构图或姿态参考，不作为好友身份图。
        合影改图仍须把当前角色作为人物 A、好友作为人物 B，分别保持两人的服装、发型、体态和外观呈现；未归属的单套穿搭默认只属于人物 A，不得自动复制给人物 B。

        Args:
            prompt(string): 想要的图片效果，例如“保留人物姿势，改成午后咖啡店随手拍，暖色自然光，生活抓拍感”。
            reference_image(string): 可选参考图片路径或 URL；留空时会尝试使用当前消息或引用消息里的图片。
            continue_last_result(bool): 是否在没有新参考图时继续修改当前会话上一张成功生成的图片。
            generate_without_reference(bool): 未找到真实参考图时，是否允许改走 life_image_generate 生成新图。
            participants(list[string]): 可选合影好友的关系档案 ID，只能填写一位；普通改图留空。
            resolution(string): 可选输出分辨率，只能填 1K、2K 或 4K；仅当用户明确要求修改后图片的输出分辨率时填写，“高清”等模糊描述不要推断，不能把原图或画面内容里的分辨率当成输出要求。
            provider(string): 可选图片接口，只能填 auto、gpt 或 gemini；仅当用户明确要求使用 GPT 或 Gemini 时填写，否则留空。
        """
        options = {
            "continue_last_result": self._tool_bool(continue_last_result),
            "generate_without_reference": self._tool_bool(generate_without_reference),
        }
        if participants:
            options["participants"] = participants
        requested_resolution = str(resolution or "").strip().upper()
        if requested_resolution:
            options["resolution"] = requested_resolution
        if str(provider or "").strip():
            options["provider"] = str(provider).strip()
        return await self.runtime.edit_life_image(
            event,
            str(prompt or "").strip(),
            str(reference_image or "").strip(),
            **options,
        )

    @filter.llm_tool(name="life_image_reverse_prompt")
    @_runtime_guard
    async def tool_life_image_reverse_prompt(
        self,
        event: AstrMessageEvent,
        reference_image: str = "",
        source_prompt: str = "",
        profile: str = "",
    ):
        """
        根据用户当前发送、引用或指定的图片，反推出可复用的图片生成提示词。
        适合用户问“这张图提示词怎么写”“反推提示词”“按这张图写一段生图提示词”等场景。
        本工具返回可直接生图的提示词和画面拆解，不生成图片，也不改图。
        反推方案只调整分析重点，不会补充原图中不存在的画面内容。

        Args:
            reference_image(string): 可选图片路径或 URL；留空时自动尝试当前消息或引用消息里的图片。
            source_prompt(string): 可选参考重点；用户要求重点保留人物、穿搭、姿势、构图、场景、光线、文字或风格时填写对应要求。
            profile(string): 可选反推方案，例如“通用”“通用超详细”“生活照”“人像”“CCD人像”“棚拍”“棚拍人像”“古风”“古风特调”“商品”“视觉封面”“设计视觉”“插画”。
        """
        return await self.runtime.life_image_reverse_prompt(
            event,
            str(reference_image or "").strip(),
            str(source_prompt or "").strip(),
            str(profile or "").strip(),
        )

    @filter.llm_tool(name="life_video_generate")
    @_runtime_guard
    async def tool_life_video_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        subject_route: str = "free",
        participants: list[str] | None = None,
        friend_outfit: str = "",
        friend_hair: str = "",
        friend_scene_category: str = "",
        continue_last_result: bool = False,
    ):
        """
        根据当前角色生活场景生成并发送一段短视频。
        视频生成较慢且成本更高，只适合用户明确要视频、引用图转视频，或动作/镜头变化非常强的场景。
        用户已经明确要视频时，调用前可以先用角色口吻说一句简短、自然的行动确认；不能提前声称视频已经完成，
        也不要播报生成任务、首帧、模型或等待进度。工具调用后保持安静，真实视频发送后再自然补一句。
        当前消息或引用消息里带图片时会自动作为视频首帧/参考图；普通看状态、看穿搭、发照片应优先使用图片。
        用户明确要求当前角色与一位已配置好友拍同框视频时，subject_route 填 group，participants 必须填写系统给出的一个关系档案 ID。
        group 模式会优先把当前消息或引用消息里的现成合影直接作为首帧；没有现成合影时，才使用双方参考图生成双人首帧。
        普通“一起拍个视频”“拍段视频看看”属于新的拍摄：即使上一轮刚发送过合影，也不要设置 continue_last_result，插件会重新生成适合视频剧情的双人首帧。
        只有用户明确说“让刚才那张动起来”“用上一张照片生成视频”“继续拍上一张”等沿用上一张图片的要求时，才设置 continue_last_result=true。
        没有现成首帧时，把当前角色作为人物 A、好友作为人物 B，分别描述并保持两人的服装、发型、体态和外观呈现；未归属的单套穿搭默认只属于人物 A。
        好友参考图只用于确认人物 B 的身份；需要新生成首帧时，人物 B 的本轮穿搭和发型通过结构化参数独立确定，不根据姓名或昵称猜测性别。只有明确要求同款、情侣装或统一造型时才共享穿搭风格。视频必须沿用首帧中两人的独立属性。
        没有现成首帧时每次填写 friend_scene_category；已有好友当天造型且适合当前场景就让 friend_outfit 和 friend_hair 留空，没有当天造型时必须同时填写完整穿搭和发型。
        人物 B 换装时填写 friend_outfit，改变发型时填写 friend_hair；服装属性和造型决定由插件根据场景推导。不能只把新造型写进 prompt。使用现成合影首帧时所有好友造型参数均留空。
        用户要求让当前会话上一张已生成合影动起来，并且本轮没有发送或引用新图片时，设置 continue_last_result=true。
        当前或引用图片会被视为已经包含最终人物的完整首帧；如果只是空场景，应先生成合影图片，再基于生成结果制作视频。
        如果用户本轮已经给出完整视频提示词、分镜脚本或时间轴，prompt 必须原样保留用户全文，不要改写、摘要或翻译。

        Args:
            prompt(string): 视频画面要求；用户给出完整分镜或时间轴时必须填完整原文，例如“第1格【0-1.5秒】...第2格【1.5-3秒】...”。
            subject_route(string): 视频主体路线，只能填 current_character、group、scene、object 或 free；当前角色与一位好友同框时填 group。
            participants(list[string]): 合影好友的关系档案 ID；subject_route=group 且没有现成合影首帧时必须且只能填写一位，其他路线留空。
            friend_outfit(string): 人物 B 本次完整穿搭；新生成首帧且没有当天造型时必须与 friend_hair 同时填写，已有当天造型时仅在本轮换装时填写，现成首帧时留空。
            friend_hair(string): 人物 B 本次发型；新生成首帧且没有当天造型时必须与 friend_outfit 同时填写，已有当天造型时仅在本轮改变发型时填写，现成首帧时留空。
            friend_scene_category(string): 新生成首帧时人物 B 的当前场景，只能填 home、sleep、outdoor、public 或 mixed；使用现成首帧时留空。
            continue_last_result(bool): 本轮没有新图片且用户明确要求沿用上一张时，是否使用当前会话上一张成功生成的图片作为视频首帧；不能仅因上一轮刚发送过图片就设为 true。
        """
        options = {
            "subject_route": str(subject_route or "free").strip(),
            "continue_last_result": self._tool_bool(continue_last_result),
        }
        if participants:
            options["participants"] = participants
        if str(friend_outfit or "").strip():
            options["friend_outfit"] = str(friend_outfit).strip()
        if str(friend_hair or "").strip():
            options["friend_hair"] = str(friend_hair).strip()
        if str(friend_scene_category or "").strip():
            options["friend_scene_category"] = str(friend_scene_category).strip()
        return await self.runtime.life_video_generate(
            event,
            str(prompt or "").strip(),
            **options,
        )

    @filter.llm_tool(name="life_video_understand")
    @_runtime_guard
    async def tool_life_video_understand(
        self,
        event: AstrMessageEvent,
        target: str = "",
    ):
        """
        理解用户当前发送、引用或指定的视频，并把可确认的画面信息用于后续对话。
        适合用户问“这个视频里是什么”“刚才视频怎么看”“帮我看看这段视频”等场景；不要凭生活背景猜视频内容。
        调用前可以根据聊天语境先用角色口吻自然回应一句；工具结果返回后再基于可确认的视频内容回答用户。
        Args:
            target(string): 可选的视频文件路径或直链；留空时自动使用当前消息或引用消息里的视频。
        """
        return await self.runtime.life_video_understand(
            event, str(target or "").strip()
        )

    @filter.llm_tool(name="life_video_note")
    @_runtime_guard
    async def tool_life_video_note(
        self,
        event: AstrMessageEvent,
        target: str = "",
        style: str = "professional",
    ):
        """
        把用户当前发送、引用、指定或最近已理解的视频整理成专业 Markdown 长文总结，并交给 AstrBot 文转图发送。
        只在用户明确需要“专业总结、长文总结、详细分析、总结成图、转图总结”时调用；普通询问视频内容时使用 life_video_understand。
        专业总结按照视频转写配置使用本地 ASR 或必剪，并结合关键画面证据；按视频实际议题组织背景、论点、事实、数据、分析、风险和建议，以段落、重点列表和引用形成紧凑笔记。长内容会逐个处理全部时间窗，广告与无关片段不会生成章节；关键画面不会插入成品，没有音频但画面可确认时仍可生成。
        调用后不要再额外复述总结正文。

        Args:
            target(string): 可选的视频文件路径或直链；留空时自动使用当前消息、引用消息或最近视频。
            style(string): 可选总结风格，professional、detailed 或 concise。
        """
        return await self.runtime.life_video_note(
            event,
            str(target or "").strip(),
            str(style or "professional").strip(),
        )

    @filter.llm_tool(name="life_text_forward")
    @_runtime_guard
    async def tool_life_text_forward(
        self,
        event: AstrMessageEvent,
        index: int = 1,
    ):
        """
        当用户需要最近一条文本转图像回复的原文、文字版或可复制内容时调用。
        工具会把缓存的原始文字通过 QQ 合并转发发送，不进行识图、改写、清洗或重新生成。
        只在用户确实要取回近期转图回复的文字内容时调用；普通追问不需要调用。
        发送成功后可以自然确认一句，不要在最终回复里复述原文。

        Args:
            index(int): 要取回的近期记录序号，1 表示最新一条，2 表示上一条，默认 1。
        """
        return await self.runtime.forward_t2i_text(
            event,
            index=self._tool_int(index, 1),
        )

    @filter.llm_tool(name="life_voice_generate")
    @_runtime_guard
    async def tool_life_voice_generate(
        self,
        event: AstrMessageEvent,
        text: str = "",
        emotion: str = "",
        emotion_category: str = "",
        user_requested: bool = False,
        decision_reason: str = "",
    ):
        """
        把一句角色回复合成为语音并发送，作为本轮最终回复。
        仅当用户明确要求发语音、录一句或说给他听时调用，并设置 user_requested=true。
        普通聊天不要调用本工具；直接输出最终文字，由插件在发送前判断是否自动转成语音。
        需要语音时不要先输出同句文字。
        调用成功后，不要再用文字重复同一句内容；如果工具返回失败，再改用自然文字回复。

        Args:
            text(string): 本轮最终要说出口的回复文本。
            emotion(string): 可选自然情绪描述，例如“困倦”“小声吐槽”“无奈中带点宠溺”。
            emotion_category(string): 可选情绪分类，只能是 neutral、happy、sad、angry 之一。
            user_requested(bool): 用户明确要求发送语音时必须填 true；false 会保持文字回复。
            decision_reason(string): 简要说明用户本轮为什么需要语音；用于后台裁定记录，不会发给用户。
        """
        return await self.runtime.life_voice_generate(
            event,
            str(text or "").strip(),
            emotion=emotion,
            emotion_category=emotion_category,
            user_requested=self._tool_bool(user_requested),
            decision_reason=str(decision_reason or "").strip(),
        )

    @filter.llm_tool(name="life_emoji_send")
    @_runtime_guard
    async def tool_life_emoji_send(
        self,
        event: AstrMessageEvent,
        intent: str = "",
        emotion: str = "",
        emotion_category: str = "",
        decision_reason: str = "",
    ):
        """
        从本插件已收藏的表情素材池里选择一张合适表情并发送。
        用户明确要发表情，或当前文字需要配合一张已收藏表情完成语义时调用；不需要传图片路径或 URL。
        如果素材池里没有语义合适的表情，工具会直接不发送；不要为了发表情而硬调用。

        Args:
            intent(string): 本轮想发送表情的自然意图，例如“发送一张小丑自嘲表情”或“补一个调侃自嘲表情”。
            emotion(string): 可选自然情绪描述，例如“轻松调侃”“开心递上”“无奈自嘲”。
            emotion_category(string): 可选情绪分类，只能是 neutral、happy、sad、angry 之一。
            decision_reason(string): 为什么本轮适合直接发送表情，用第一人称短句说明；用于后台记录，不会发给用户。
        """
        return await self.runtime.life_emoji_send(
            event,
            intent=str(intent or "").strip(),
            emotion=str(emotion or "").strip(),
            emotion_category=str(emotion_category or "").strip(),
            decision_reason=str(decision_reason or "").strip(),
        )

    @filter.on_llm_request()
    @_runtime_guard
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        prepare_turn = self._runtime_hook("prepare_continuous_turn_llm_request")
        if prepare_turn and not prepare_turn(event, req):
            return
        gif_bridge = self._runtime_hook("bridge_animated_visual_for_llm_request")
        if gif_bridge:
            await gif_bridge(event, req)
        self.runtime.search.prepare_tools(
            req,
            WEB_SEARCH_TOOL_NAMES,
            umo=str(event.unified_msg_origin or ""),
        )
        toolset = getattr(req, "func_tool", None)
        life_config = getattr(self.runtime, "config", None)
        if toolset is not None and life_config is not None:
            install_expressive_send_message_tool(toolset, self.runtime)
            image_enabled = bool(
                getattr(
                    getattr(life_config, "image_generation", None), "enabled", False
                )
            )
            video_enabled = bool(
                getattr(
                    getattr(life_config, "video_generation", None), "enabled", False
                )
            )
            voice_enabled = bool(
                getattr(
                    getattr(life_config, "voice_generation", None), "enabled", False
                )
            )
            if not image_enabled:
                for name in (
                    "life_image_generate",
                    "life_photo_suite_generate",
                    "edit_life_image",
                    "life_image_reverse_prompt",
                ):
                    toolset.remove_tool(name)
            if not video_enabled:
                toolset.remove_tool("life_video_generate")
            if not voice_enabled:
                toolset.remove_tool("life_voice_generate")
            domains = getattr(self.runtime, "domains", None)
            map_available = getattr(domains, "map_tools_available", None)
            if not callable(map_available) or not map_available():
                for name in MAP_LLM_TOOL_NAMES:
                    toolset.remove_tool(name)
        await self.runtime.inject_life_context(req, event)

    @filter.on_llm_response()
    @_runtime_guard
    async def on_llm_response(self, event: AstrMessageEvent, response):
        if self._runtime_hook_bool("stop_stale_continuous_turn_event", event):
            return
        if self._response_is_agent_error(response):
            setattr(event, self._AGENT_ERROR_SEEN_ATTR, True)
        else:
            setattr(event, self._LLM_RESPONSE_SEEN_ATTR, True)
        note_tool_final = getattr(self.runtime, "note_tool_final_response", None)
        if callable(note_tool_final):
            note_tool_final(event, response)
        stop_recalled = getattr(
            self.runtime, "stop_recalled_event_before_history", None
        )
        if callable(stop_recalled):
            stop_recalled(event)

    @filter.on_agent_done()
    @_runtime_guard
    async def on_agent_done(self, event: AstrMessageEvent, run_context, response):
        del run_context
        if self._response_is_agent_error(response):
            setattr(event, self._AGENT_ERROR_SEEN_ATTR, True)
        reaction = getattr(self.runtime, "note_tool_reaction_agent_done", None)
        if callable(reaction):
            await reaction(event, response)

    @filter.on_using_llm_tool()
    @_runtime_guard
    async def on_using_llm_tool(
        self, event: AstrMessageEvent, tool, tool_args: dict | None = None
    ):
        tool_name = str(getattr(tool, "name", tool) or "").strip()
        if tool_name in {
            "life_web_search",
            "life_web_fetch",
            "life_web_map",
            "life_web_crawl",
            "life_web_research",
            "life_web_research_status",
        }:
            self._mark_external_search_turn(event)
        reaction = getattr(self.runtime, "note_tool_reaction_start", None)
        if callable(reaction):
            await reaction(event, tool, tool_args)
        handler = getattr(self.runtime, "handle_llm_tool_start", None)
        if callable(handler):
            await handler(event, tool, tool_args)

    @filter.on_llm_tool_respond()
    @_runtime_guard
    async def on_llm_tool_respond(
        self,
        event: AstrMessageEvent,
        tool,
        tool_args: dict | None = None,
        tool_result=None,
    ):
        handler = getattr(self.runtime, "handle_llm_tool_respond", None)
        if callable(handler):
            await handler(event, tool, tool_args, tool_result)
        reaction = getattr(self.runtime, "note_tool_reaction_result", None)
        if callable(reaction):
            await reaction(event, tool, tool_args, tool_result)

    def _runtime_hook(self, name: str):
        hook = getattr(self.runtime, name, None)
        return hook if callable(hook) else None

    def _runtime_hook_bool(self, name: str, event: AstrMessageEvent) -> bool:
        hook = self._runtime_hook(name)
        return bool(hook(event)) if hook else False

    def _runtime_hook_call(self, name: str, event: AstrMessageEvent):
        hook = self._runtime_hook(name)
        return hook(event) if hook else None

    async def _runtime_hook_apply(
        self, name: str, event: AstrMessageEvent, *, is_async: bool
    ) -> None:
        hook = self._runtime_hook(name)
        if not hook:
            return
        started_at = time.monotonic()
        try:
            if is_async:
                await hook(event)
            else:
                hook(event)
        finally:
            elapsed = time.monotonic() - started_at
            stage_label = self._STAGE_LABELS.get(name, "消息处理")
            if elapsed >= self._SLOW_STAGE_SECONDS:
                level = logger.info if name.startswith("send_") else logger.warning
                level(
                    f"{LOG_PREFIX} 消息阶段耗时：阶段={stage_label}；耗时={elapsed:.2f} 秒"
                )
            elif elapsed >= 0.1:
                logger.debug(
                    f"{LOG_PREFIX} 消息阶段耗时：阶段={stage_label}；耗时={elapsed:.2f} 秒"
                )

    def _log_message_entry_timing(
        self, event: AstrMessageEvent, started_at: float
    ) -> None:
        total_elapsed = max(0.0, time.monotonic() - started_at)
        intentional_wait = self._runtime_hook_call(
            "continuous_turn_intentional_wait_seconds", event
        )
        try:
            intentional_wait = max(0.0, float(intentional_wait or 0.0))
        except (TypeError, ValueError):
            intentional_wait = 0.0
        active_elapsed = max(0.0, total_elapsed - intentional_wait)
        if intentional_wait > 0:
            message = (
                f"{LOG_PREFIX} 消息入口处理耗时：有效耗时={active_elapsed:.2f} 秒；"
                f"总耗时={total_elapsed:.2f} 秒；连续消息等待={intentional_wait:.2f} 秒"
            )
        else:
            message = f"{LOG_PREFIX} 消息入口处理耗时：{total_elapsed:.2f} 秒"
        if active_elapsed >= self._SLOW_STAGE_SECONDS:
            logger.warning(message)
        elif total_elapsed >= 0.1:
            logger.debug(message)

    async def _capture_chat_memory(self, event: AstrMessageEvent) -> None:
        hook = self._runtime_hook("capture_chat_memory_message")
        if not hook:
            return
        result = hook(event)
        if not inspect.isawaitable(result):
            return
        scheduler = self._runtime_hook("_schedule_background_task")
        if not scheduler:
            await result
            return
        turn_id = self._event_turn_id(event) or f"event:{id(event)}"
        accepted = scheduler(
            result,
            label="聊天记忆提炼",
            key=f"chat_capture:{turn_id}",
            category="chat",
        )
        if accepted is False:
            logger.warning(
                f"{LOG_PREFIX} 聊天记忆提炼进入队列失败：队列已满，turn={turn_id}"
            )

    async def _capture_chat_memory_bot_reply(self, event: AstrMessageEvent) -> None:
        hook = self._runtime_hook("capture_chat_memory_bot_reply")
        scheduler = self._runtime_hook("_schedule_background_task")
        if not hook or not scheduler:
            return
        result = hook(event)
        if not inspect.isawaitable(result):
            return
        turn_id = self._event_turn_id(event) or f"event:{id(event)}"
        accepted = scheduler(
            result,
            label="Bot回复记忆采集",
            key=f"chat_capture_bot:{turn_id}",
            category="chat",
        )
        if accepted is False:
            logger.warning(
                f"{LOG_PREFIX} 机器人回复记忆采集进入队列失败：队列已满，turn={turn_id}"
            )

    def _send_pipeline_should_stop(self, event: AstrMessageEvent) -> bool:
        return any(
            self._runtime_hook_bool(name, event)
            for name in self._SEND_PIPELINE_STOP_HOOKS
        )

    def _reply_segmentation_enabled(self) -> bool:
        checker = self._runtime_hook("_semantic_segment_enabled")
        return bool(checker()) if checker else True

    def _send_pipeline_should_passthrough_command_result(
        self, event: AstrMessageEvent
    ) -> bool:
        if not self._runtime_hook_bool("_event_has_command_handler", event):
            return False
        return not bool(getattr(event, self._LLM_RESPONSE_SEEN_ATTR, False))

    @staticmethod
    def _response_is_agent_error(response) -> bool:
        role = getattr(response, "role", "")
        role = getattr(role, "value", role)
        return str(role or "").strip().lower() == "err" or bool(
            getattr(response, "is_error", False)
        )

    @staticmethod
    def _result_content_type_name(result) -> str:
        value = getattr(result, "result_content_type", None)
        name = getattr(value, "name", "")
        if name:
            return str(name).strip().upper()
        return str(value or "").strip().rsplit(".", 1)[-1].upper()

    @classmethod
    def _result_is_normal_llm_reply(cls, event: AstrMessageEvent) -> bool:
        result = getattr(event, "get_result", lambda: None)()
        if result is None:
            return False
        checker = getattr(result, "is_llm_result", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return cls._result_content_type_name(result) == "LLM_RESULT"

    @classmethod
    def _event_has_agent_error_result(cls, event: AstrMessageEvent) -> bool:
        if bool(getattr(event, cls._AGENT_ERROR_SEEN_ATTR, False)):
            return True
        result = getattr(event, "get_result", lambda: None)()
        return cls._result_content_type_name(result) == "AGENT_RUNNER_ERROR"

    @classmethod
    def _event_has_normal_llm_response(cls, event: AstrMessageEvent) -> bool:
        return bool(getattr(event, cls._LLM_RESPONSE_SEEN_ATTR, False)) and not (
            cls._event_has_agent_error_result(event)
        )

    async def _send_pipeline_apply(self, event: AstrMessageEvent) -> None:
        for name, is_async in self._SEND_PIPELINE_APPLY_HOOKS:
            if (
                name in self._NATURAL_SEGMENTATION_HOOKS
                and not self._reply_segmentation_enabled()
            ):
                continue
            await self._runtime_hook_apply(name, event, is_async=is_async)

    @filter.on_decorating_result(priority=-900)
    @_runtime_guard
    async def on_decorating_result(self, event: AstrMessageEvent):
        if self._runtime_hook_bool("stop_stale_continuous_turn_event", event):
            return
        if self._send_pipeline_should_stop(event):
            return
        if self._send_pipeline_should_passthrough_command_result(event):
            logger.debug(f"{LOG_PREFIX} 指令反馈保持原样发送，跳过表达加工。")
            return
        if not self._result_is_normal_llm_reply(event):
            logger.debug(f"{LOG_PREFIX} 非聊天模型结果保持原样发送，跳过表达加工。")
            return
        await self._send_pipeline_apply(event)
        if getattr(event, "get_result", lambda: None)() is None:
            self._runtime_hook_call("complete_continuous_turn", event)

    @filter.after_message_sent()
    @_runtime_guard
    async def after_message_sent(self, event: AstrMessageEvent):
        self._runtime_hook_call("complete_continuous_turn", event)
        reaction = getattr(self.runtime, "note_tool_reaction_message_sent", None)
        if callable(reaction):
            await reaction(event)
        for name in self._AFTER_SENT_NOTE_HOOKS:
            self._runtime_hook_call(name, event)
        if not self._event_has_normal_llm_response(event):
            if self._event_has_agent_error_result(event):
                logger.debug(f"{LOG_PREFIX} 智能体错误结果已发送，跳过聊天状态记录。")
            return
        await self._capture_chat_memory_bot_reply(event)
        regular_effect = self._runtime_hook("note_regular_reply_effect")
        if regular_effect:
            await regular_effect(event)
        self.runtime.note_proactive_bot_reply(event)
        self.runtime.note_voice_switch_text_result(event)
        self._runtime_hook_call("schedule_pending_chat_state_refresh", event)

    @filter.event_message_type(
        EventMessageType.GROUP_MESSAGE | EventMessageType.PRIVATE_MESSAGE,
        priority=-90,
    )
    @_runtime_guard
    async def on_message_for_proactive_reply(self, event: AstrMessageEvent):
        started_at = time.monotonic()
        if self._runtime_hook_call("note_recalled_message", event):
            return
        self._runtime_hook_call("note_runtime_scope_activity", event)
        self._runtime_hook_call("note_continuous_turn_incoming", event)
        await self._runtime_hook_apply(
            "prepare_visual_media_from_event", event, is_async=True
        )
        for name in self._INCOMING_NOTE_HOOKS:
            self._runtime_hook_call(name, event)
        self._runtime_hook_call("note_semantic_segment_incoming_message", event)
        await self._capture_chat_memory(event)
        if self._runtime_hook_call("schedule_bili_summary_from_event", event):
            self._runtime_hook_call("complete_continuous_turn", event)
            return
        settle_turn = self._runtime_hook("settle_continuous_turn")
        if settle_turn and not await settle_turn(event):
            return
        self._runtime_hook_call("mark_alias_directed_event_as_wake", event)
        self.runtime.note_proactive_activity(event)
        await self.runtime.apply_response_gate_for_event(event)
        self._log_message_entry_timing(event, started_at)

    @filter.event_message_type(
        getattr(
            EventMessageType,
            "ALL",
            EventMessageType.GROUP_MESSAGE | EventMessageType.PRIVATE_MESSAGE,
        ),
        priority=-95,
    )
    @_runtime_guard
    async def on_platform_event_for_sight_upload(self, event: AstrMessageEvent):
        self._runtime_hook_call("note_sight_group_upload_event", event)

    @filter.command("生活")
    @_runtime_guard
    async def life_command(self, event: AstrMessageEvent):
        async for result in self.commands.dispatch(event):
            yield result

    @filter.command("B站登录")
    @_runtime_guard
    async def bili_login_command(self, event: AstrMessageEvent):
        async for result in self.runtime.bili_login(event):
            yield result

    @filter.command("B站登出")
    @_runtime_guard
    async def bili_logout_command(self, event: AstrMessageEvent):
        yield await self.runtime.bili_logout(event)

    @filter.command("B站状态")
    @_runtime_guard
    async def bili_status_command(self, event: AstrMessageEvent):
        yield await self.runtime.bili_status(event)
