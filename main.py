from __future__ import annotations

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

from functools import cached_property

from .core.composition import ComponentCall
from .core.interface import (
    DailyLifeCommandCenter,
    LifeAccessPolicy,
    LifeActionProposal,
    LifeActionScope,
)
from .core.interface.panel import DailyLifeDashboardComponent
from .core.interface.portal.asset import PortalReferenceComponent
from .core.interface.portal.closet import PortalClosetComponent
from .core.interface.portal.entry import PortalBaseComponent
from .core.interface.portal.line import PortalLineComponent
from .core.interface.portal.memo import PortalMemoryComponent
from .core.interface.portal.operation import PortalActionComponent
from .core.interface.portal.stickers import PortalEmojiComponent
from .core.interface.view import PageViewComponent
from .core.logguard import ProviderLogGuard
from .core.runtime import PLUGIN_ID, DailyLifeRuntime
from .core.runtime.external import generate_text, model_route
from .core.runtime.markers import LOG_PREFIX
from .core.runtime.preparation import ReplyPreparationService
from .core.runtime.sender import install_expressive_send_message_tool
from .core.runtime.tooling import (
    ArgumentCheckedTool,
    guard_tool_arguments,
    prepare_tool_argument_checks,
)
from .core.sources.events import is_chat_message_event
from .core.sources.location import enrich_location_message
from .core.telemetry import cache_prefix_version

EXTERNAL_LEASE_SHUTDOWN_TIMEOUT_SECONDS = 10.0
MAP_LLM_TOOL_NAMES = (
    "life_place_search",
    "life_route_plan",
    "life_place_detail",
    "life_outing_plan",
)


class DailyLifePlugin(Star):
    """日常生活引擎适配器。"""

    _LLM_RESPONSE_SEEN_ATTR = "_daily_life_llm_response_seen"
    _AGENT_ERROR_SEEN_ATTR = "_daily_life_agent_error_seen"
    _VIDEO_TOOL_USED_ATTR = "_daily_life_video_tool_used"

    @staticmethod
    def _split_voice_call_invite_message(message: str) -> tuple[str, str | None]:
        """拆分通话邀请提示与可单独复制的链接。"""

        lines = [
            line.strip() for line in str(message or "").splitlines() if line.strip()
        ]
        for index, line in enumerate(lines):
            if line.startswith(("https://", "http://")):
                notice = "\n".join(lines[:index]).strip()
                return notice or line, line
        return "\n".join(lines).strip(), None

    async def _deliver_call_invite(self, event: AstrMessageEvent, message: str) -> None:
        """一次性投递通话邀请，并阻止当前事件再次经过默认响应出口。"""

        notice, invite_link = self._split_voice_call_invite_message(message)
        parts = [part for part in (notice, invite_link) if part]
        sender = getattr(event, "send", None)
        if callable(sender):
            for part in parts:
                await sender(event.plain_result(part))
        else:
            # 测试替身或极简事件没有 send 时，仍保留一个待发送结果。
            if parts:
                event.set_result(event.plain_result("\n".join(parts)))
        clearer = getattr(event, "clear_result", None)
        if callable(clearer):
            clearer()
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()

    _SEND_PIPELINE_STOP_HOOKS = (
        "hold_background_tool_final_text",
        "suppress_recalled_event_result",
        "suppress_intermediate_tool_result",
        "suppress_final_silent_tool_result",
        "suppress_sight_note_followup",
        "hold_life_image_final_text",
        "hold_life_video_final_text",
        "hold_life_photo_suite_final_text",
        "suppress_media_agent_error",
        "suppress_photo_suite_agent_error",
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
            "note_conversation_incoming",
            "record_conversation_arrival",
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
            "send_semantic_emoji_if_needed",
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
        self._runtime_contract_validated = False
        self._log_guard = ProviderLogGuard()

    async def initialize(self):
        async with self._initialize_lock:
            if self.runtime is not None and self.commands is not None:
                return
            runtime = None
            registered_apis = getattr(self._plugin_context, "registered_web_apis", None)
            api_snapshot = (
                list(registered_apis) if isinstance(registered_apis, list) else None
            )
            try:
                self._log_guard.install()
                data_path = await asyncio.to_thread(self._prepare_database)
                runtime = DailyLifeRuntime(
                    self._plugin_context,
                    self._plugin_config,
                    data_path,
                    defer_start=True,
                )
                self._terminating = False
                self._validate_runtime_contract(runtime)
                await runtime.initialize()
                self._runtime_contract_validated = True
                commands = DailyLifeCommandCenter(runtime)
                self.runtime = runtime
                self.commands = commands
                self._register_page_web_apis()
            # 初始化取消也必须回滚已安装资源，清理后继续抛出原异常。
            except BaseException:
                self._runtime_contract_validated = False
                self.runtime = None
                self.commands = None
                if api_snapshot is not None and isinstance(registered_apis, list):
                    registered_apis[:] = api_snapshot
                try:
                    if runtime is not None:
                        await runtime.terminate()
                except Exception as cleanup_exc:
                    logger.error(f"[日常生活] 初始化回滚清理失败：{cleanup_exc}")
                finally:
                    self._log_guard.close()
                raise

    def _prepare_database(self) -> Path:
        data_dir = StarTools.get_data_dir(PLUGIN_ID)
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "daily_life.db"

    async def terminate(self):
        runtime = self.runtime
        try:
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
            if runtime is not None:
                await runtime.terminate()
        finally:
            self._log_guard.close()
            self._runtime_contract_validated = False
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
    async def _external_plugin_lease(self):
        """保护插件生命周期，但不阻止当前请求发起运行时服务切换。"""

        condition = getattr(self, "_external_condition", None)
        if not isinstance(condition, asyncio.Condition):
            # 轻量测试替身可能没有完整插件生命周期状态，保留直接单元调用能力。
            yield_runtime = getattr(self, "runtime", None)
            if yield_runtime is None:
                raise RuntimeError("日常生活插件尚未就绪或正在终止")
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

    @asynccontextmanager
    async def _external_runtime_lease(self):
        """同时保护插件生命周期与本次调用使用的运行时服务。"""

        async with self._external_plugin_lease() as runtime:
            service_lease = getattr(runtime, "runtime_service_lease", None)
            if callable(service_lease):
                async with service_lease():
                    yield runtime
            else:
                yield runtime

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

    async def get_external_model_route(self) -> dict:
        """仅返回模型 ID，不对外暴露凭据和 provider 实例。"""
        async with self._external_runtime_lease() as runtime:
            return await model_route(runtime)

    async def generate_external_text(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        target_umo: str = "",
        timeout_seconds: float = 20,
    ) -> str:
        """使用 daily_life 的模型选择与故障切换机制，在隔离会话中生成文本。"""
        async with self._external_runtime_lease() as runtime:
            return await generate_text(
                runtime,
                prompt,
                system_prompt=system_prompt,
                target_umo=target_umo,
                timeout_seconds=timeout_seconds,
            )

    page_browsing_list = ComponentCall(
        "_part_daily_life_dashboard_component", DailyLifeDashboardComponent, "page_browsing_list"
    )

    page_browsing_control = ComponentCall(
        "_part_daily_life_dashboard_component", DailyLifeDashboardComponent, "page_browsing_control"
    )

    async def get_share_context(self, target_umo: str = "") -> dict:
        """为分享类插件返回目标隔离且已提炼的生活上下文。"""
        async with self._external_runtime_lease() as runtime:
            return await runtime.get_share_context(target_umo)

    async def get_share_chat_style(self, *, scene: str = "") -> dict:
        """为外部分享插件返回场景表达软偏好，不导出私聊或发送设置。"""
        async with self._external_runtime_lease() as runtime:
            return runtime.get_share_chat_style(scene=scene)

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
        model: str = "",
        text_model: str = "",
        edit_model: str = "",
        contains_character: bool = False,
    ) -> str:
        model = str(model or "").strip()
        text_model = str(text_model or "").strip() or model
        edit_model = str(edit_model or "").strip() or model
        async with self._external_runtime_lease() as runtime:
            result = await runtime.generate_life_image_asset(
                event,
                prompt,
                "",
                contains_character=contains_character,
                preserve_reference_ratio=False,
                trusted_identity=contains_character,
                text_model=text_model,
                edit_model=edit_model,
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

    async def generate_share_image_task(
        self,
        event: AstrMessageEvent | None,
        prompt: str,
        *,
        task_key: str,
        contains_character: bool = False,
        target_umo: str = "",
    ) -> dict:
        async with self._external_runtime_lease() as runtime:
            return await runtime.generate_share_image_task(
                event,
                prompt,
                task_key=task_key,
                contains_character=contains_character,
                target_umo=target_umo,
            )

    async def get_share_image_task(self, task_key: str) -> dict:
        async with self._external_runtime_lease() as runtime:
            return await runtime.get_share_image_task(task_key)

    async def generate_share_video_task(
        self,
        event: AstrMessageEvent | None,
        prompt: str,
        *,
        task_key: str,
        reference_image: str = "",
        target_umo: str = "",
    ) -> dict:
        async with self._external_runtime_lease() as runtime:
            return await runtime.generate_share_video_task(
                event,
                prompt,
                task_key=task_key,
                reference_image=reference_image,
                target_umo=target_umo,
            )

    async def get_share_video_task(self, task_key: str) -> dict:
        async with self._external_runtime_lease() as runtime:
            return await runtime.get_share_video_task(task_key)

    async def generate_share_voice(
        self,
        text: str,
        *,
        emotion: str = "",
        emotion_category: str = "",
        voice_style: str = "",
    ) -> str:
        async with self._external_runtime_lease() as runtime:
            voice_kwargs = {
                "emotion": emotion,
                "emotion_category": emotion_category,
            }
            if voice_style:
                voice_kwargs["voice_style"] = voice_style
            result = await runtime.media.voice.synthesize(
                str(text or "").strip(), **voice_kwargs
            )
            return str(getattr(result, "path", "") or "").strip()

    async def send_share_expression(
        self, text: str, *, target_umo: str, source_event: AstrMessageEvent | None = None
    ) -> dict:
        """按当前聊天设置分段发送外部自然表达，返回实际送达的各段。"""
        async with self._external_runtime_lease() as runtime:
            return await runtime.send_share_expression(
                text, target_umo=target_umo, source_event=source_event
            )

    async def prepare_share_expression(
        self, text: str, *, scene: str = "", requirements: dict | None = None
    ) -> dict:
        async with self._external_runtime_lease() as runtime:
            options = {"requirements": requirements} if requirements is not None else {}
            return await runtime.prepare_share_expression(text, scene=scene, **options)

    async def clean_share_punctuation(self, text: str, *, scene: str = "") -> str:
        async with self._external_runtime_lease() as runtime:
            return runtime.clean_share_punctuation(text, scene=scene)

    async def prepare_share_image(self, source: str) -> dict:
        from .core.sight.sharemedia import prepare_share_image

        async with self._external_runtime_lease():
            return await prepare_share_image(str(source or ""))

    async def describe_share_video(self, source: str) -> dict:
        from .core.sight.sharemedia import describe_share_video

        async with self._external_runtime_lease() as runtime:
            return await describe_share_video(runtime, str(source or ""))

    async def plan_share_voice(self, text: str, target_umo: str = "") -> dict:
        async with self._external_runtime_lease() as runtime:
            return await runtime.plan_share_voice(
                str(text or ""), str(target_umo or "")
            )

    async def search_share_memories(
        self, query: str, target_umo: str = "", *, limit: int = 4
    ) -> list:
        async with self._external_runtime_lease() as runtime:
            return await runtime.search_share_memories(
                str(query or ""), str(target_umo or ""), limit=limit
            )

    async def coordinate_share_contact(
        self,
        target_umo: str,
        *,
        owner: str,
        action: str = "reserve",
        expected: str | None = None,
    ) -> dict:
        async with self._external_runtime_lease() as runtime:
            return await runtime.coordinate_share_contact(
                target_umo, owner=owner, action=action, expected=expected
            )

    async def record_external_activity_receipt(self, receipt: dict) -> bool:
        async with self._external_runtime_lease() as runtime:
            return await runtime.record_external_activity_receipt(dict(receipt))

    async def record_public_activity(self, receipt: dict) -> bool:
        async with self._external_runtime_lease() as runtime:
            return await runtime.record_public_activity(dict(receipt))

    async def record_share_delivery(self, receipt: dict) -> bool:
        async with self._external_runtime_lease() as runtime:
            return await runtime.record_share_delivery(dict(receipt))

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

    def _life_permission_denial(
        self,
        event: AstrMessageEvent,
        action: str,
        scope: LifeActionScope,
        *,
        payload: dict | None = None,
        resource_owner: str = "",
    ) -> str:
        commands = getattr(self, "commands", None)
        permission_denial = getattr(commands, "permission_denial", None)
        if callable(permission_denial):
            return permission_denial(
                event,
                action,
                scope,
                payload=payload,
                resource_owner=resource_owner,
            )
        proposal = LifeActionProposal.build(
            action,
            scope,
            payload=payload,
            resource_owner=resource_owner,
        )
        return LifeAccessPolicy().denial(event, proposal)

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
    @guard_tool_arguments
    @_runtime_guard
    async def tool_accept_user_invite(
        self,
        event: AstrMessageEvent,
        invite_details: str = "",
    ):
        """
        当对方邀请当前角色共同活动，提出陪伴、见面、一起行动的请求，或确认刚刚由当前角色提出的改约方案时调用。
        结合角色当前状态、已有安排和双方关系，决定自然接受、拒绝或提出其他时间。
        工具结果用于生成最终回复。调用前必须先用角色口吻说一句简短、自然的等待语，表示需要看看自己当下的安排；
        这句话不能提前答应或拒绝，也不能声称已经记录、更新或调整了安排。工具完成后再给出最终态度。

        Args:
            invite_details(string): 当前邀约或对上一条改约方案的确认，例如“晚上8点一起看电影”“现在上号双排”或“好，按你说的傍晚安排”。
        """
        denial = self._life_permission_denial(
            event, "invite:respond", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.accept_user_invite(
            event, str(invite_details or "").strip()
        )

    @filter.llm_tool(name="add_memo_for_tomorrow")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_add_memo_for_tomorrow(
        self,
        event: AstrMessageEvent,
        memo_details: str = "",
    ):
        """
        当对方明确提出明天或未来一天需要记住的计划、约定或待办时调用。
        直接记录，不先发等待语；等工具返回后，再根据实际结果自然回应一次。
        记录成功才确认已经记住，并按双方关系回应这项安排；失败时如实说明。

        Args:
            memo_details(string): 用户提到的明天或未来计划，例如“和用户去草坪野餐，负责做三明治”。
        """
        denial = self._life_permission_denial(
            event, "memo:add", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.add_memo_for_tomorrow(
            event, str(memo_details or "").strip()
        )

    @filter.llm_tool(name="life_query")
    @guard_tool_arguments
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

    @filter.llm_tool(name="life_shared_activity")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_shared_activity(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        activity_id: str = "",
        detail: str = "",
        progress: str = "",
        evidence: str = "",
        revision: int = 0,
    ):
        """管理已经确认的共同活动的准备、进行、暂停、恢复和结束状态。

        只有当前用户整条消息明确提供了进展时才更新，不能凭时间轴认定已经参与或完成。

        Args:
            action(string): list 查询；prepare 准备；start 开始；pause 暂停；resume 恢复；progress 更新进度；finish 结束；cancel 取消。
            activity_id(string): list 返回的共同活动编号；新邀约先用 accept_user_invite。
            detail(string): 本轮状态变化的明确依据，不能编造双方行为。
            progress(string): 用户明确确认的播放或活动进度；没有证据留空。
            evidence(string): 逐字引用当前用户完整原话，不能引用历史或截取片段。
            revision(int): 查询返回的当前 revision，防止覆盖并发变化。
        """
        denial = self._life_permission_denial(
            event, "shared_activity:update", LifeActionScope.PRIVATE
        )
        if denial and action != "list":
            return denial
        return await self.runtime.manage_shared_activity(
            event,
            action=str(action or "list").strip(),
            activity_id=str(activity_id or "").strip(),
            detail=str(detail or "").strip(),
            progress=str(progress or "").strip(),
            evidence=str(evidence or "").strip(),
            revision=self._tool_int(revision, 0),
        )

    @filter.llm_tool(name="life_object")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_object(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        object_id: str = "",
        title: str = "",
        state: str = "",
        location: str = "",
        progress: str = "",
        content: str = "",
        evidence: str = "",
        revision: int = 0,
        owner: str = "",
        quantity: float = -1,
        unit: str = "",
        available_at: str = "",
    ):
        """记录或查询已经真实出现的生活物品、作品、草稿和未完成事项。

        不替代衣橱和食材管理，不把计划中的物品、购买或生成请求当作实际拥有。

        Args:
            action(string): list 查询；get 读取完整作品；history 查询作品版本；record 新增明确物品；update 更新已存在物品。
            object_id(string): 已有物品或作品编号；新增留空。
            title(string): 明确名称；更新时可留空保留原值。
            state(string): available 可用；in_use 使用中；stored 收纳；unavailable 暂不可用；finished 完成；archived 归档。
            location(string): 有明确依据的位置；未知留空。
            progress(string): 已确认阅读或整理进度；不按经过时间推算。
            content(string): 明确保存的笔记或草稿正文；不能伪造外部文件或媒体。
            evidence(string): 当前用户整条原话；缺少明确证据时仅查询。
            revision(int): 已有记录当前 revision；新增为 0。
            owner(string): self 属于角色；user 属于用户；shared 共有；unknown 未知。只按明确证据填写。
            quantity(number): 明确数量；-1 表示未提供，不推测库存。
            unit(string): 数量单位，与已有记录一致。
            available_at(string): 明确的可用时间，ISO 日期时间；未知留空。
        """
        denial = self._life_permission_denial(
            event, "life_object:update", LifeActionScope.PRIVATE
        )
        if denial and action not in {"list", "get", "history"}:
            return denial
        return await self.runtime.manage_life_object(
            event,
            action=str(action or "list").strip(),
            object_id=str(object_id or "").strip(),
            title=str(title or "").strip(),
            state=str(state or "").strip(),
            location=str(location or "").strip(),
            progress=str(progress or "").strip(),
            content=str(content or "").strip(),
            evidence=str(evidence or "").strip(),
            revision=self._tool_int(revision, 0),
            owner=owner,
            quantity=quantity,
            unit=unit,
            available_at=available_at,
        )

    @filter.llm_tool(name="life_memory_correct")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_memory_correct(
        self,
        event: AstrMessageEvent,
        memory_id: int,
        correction: str,
        evidence: str,
    ):
        """用户明确纠正某条长期生活记忆时调用，保留原始历史并让后续召回采用新事实。

        Args:
            memory_id(int): life_memory_search 返回的记忆编号，禁止猜测。
            correction(string): 纠正后的完整事实，保留未被否定的内容和事件时间。
            evidence(string): 当前用户完整原话；普通话题变化不构成纠正。
        """
        denial = self._life_permission_denial(
            event, "memory:correct", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.correct_memory_from_event(
            event,
            memory_id=self._tool_int(memory_id, 0),
            correction=str(correction or "").strip(),
            evidence=str(evidence or "").strip(),
        )

    @filter.llm_tool(name="life_adjust")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_adjust(
        self,
        event: AstrMessageEvent,
        action: str,
        detail: str = "",
        period: str = "",
        schedule_time: str = "",
        date: str = "",
        change_scope: str = "clothing",
    ):
        """
        执行角色自主决定的生活调整；动作是否完成以返回结果为准。

        Args:
            action(string): update_outfit 执行当前换装决定；review_action 将当前行动意愿提交生活执行器；refresh_state 刷新实时状态；reset_day 重新生成当天；set_schedule_time 设置每日生成时间。
            detail(string): 角色自主决定或接受建议后想实际做的事及缘由，或用户补充的状态要求；行动意愿不代表已经完成。
            period(string): 可选目标时段，例如 凌晨/早晨/上午/中午/下午/傍晚/晚上/深夜；用于重生成某时段状态或穿搭。
            schedule_time(string): action=set_schedule_time 时使用，格式 HH:MM。
            date(string): 可选日期，格式 YYYY-MM-DD；留空使用当前生活日。
            change_scope(string): update_outfit 的目标范围：clothing 更换主体衣物，appearance 仅调整妆发、鞋饰等局部外观。
        """
        return await self.commands.adjust_life(
            event,
            str(action or "").strip(),
            detail=str(detail or "").strip(),
            period=str(period or "").strip(),
            schedule_time=str(schedule_time or "").strip(),
            date=str(date or "").strip(),
            change_scope=str(change_scope or "clothing").strip(),
        )

    @filter.llm_tool(name="life_rest_timing")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_rest_timing(
        self,
        event: AstrMessageEvent,
        minutes: int,
        target_times: list[str] | None = None,
    ):
        """
        用户明确要求当前角色推迟临近的休息，或结束已生效的推迟时调用。
        根据完整对话理解用户意图；自述、转述、假设、过去或未来计划不调用。
        从当前生活日程中选出确实属于本次休息的节点，不能修改其他活动。

        Args:
            minutes(int): 本次希望延后的分钟数，1 到 120；结束已生效的推迟时填 0。
            target_times(list[string]): 延后时填写当前日程中休息节点的 HH:MM 时间列表；结束推迟时留空。
        """
        denial = self._life_permission_denial(
            event, "adjust:rest_timing", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.apply_rest_timing(
            event, self._tool_int(minutes, -1), target_times or []
        )

    @filter.llm_tool(name="life_commitment")
    @guard_tool_arguments
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
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_weather(
        self,
        event: AstrMessageEvent,
        city: str = "",
    ):
        """
        查询当前天气；查询默认天气城市时会同步到当前生活日。
        只适合当前实时天气，不负责明天或未来预报；用户询问明天/未来天气时，
        应改用 life_web_search，并提交包含城市和日期的完整问题。

        Args:
            city(string): 可选城市名；留空使用天气设置中的独立城市，未设置时使用可确认的现实居住城市。
        """
        return await self.commands.query_weather(event, str(city or "").strip())

    @filter.llm_tool(name="life_place_search")
    @guard_tool_arguments
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
        denial = self._life_permission_denial(
            event, "map:place_search", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.domains.tool_place_search(
            str(query or "").strip(),
            scope=str(getattr(event, "unified_msg_origin", "") or ""),
            near=str(near or "").strip(),
            category=str(category or "").strip(),
            radius_meters=self._tool_int(radius_meters, 3000),
            limit=self._tool_int(limit, 5),
        )

    @filter.llm_tool(name="life_route_plan")
    @guard_tool_arguments
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
        denial = self._life_permission_denial(
            event, "map:route_plan", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.domains.tool_route_plan(
            str(origin or "").strip(),
            str(destination or "").strip(),
            mode=str(mode or "walking").strip(),
        )

    @filter.llm_tool(name="life_place_detail")
    @guard_tool_arguments
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
        denial = self._life_permission_denial(
            event, "map:place_detail", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.domains.tool_place_detail(
            str(poi_id or "").strip(),
            scope=str(getattr(event, "unified_msg_origin", "") or ""),
        )

    @filter.llm_tool(name="life_outing_plan")
    @guard_tool_arguments
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
        denial = self._life_permission_denial(
            event, "map:outing_plan", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.domains.tool_outing_plan(
            str(request or "").strip(),
            list(stops or []),
            start=str(start or "").strip(),
            mode=str(mode or "walking").strip(),
            duration_minutes=self._tool_int(duration_minutes, 120),
            max_stops=self._tool_int(max_stops, 3),
        )

    @filter.llm_tool(name="life_review")
    @guard_tool_arguments
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
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_memory_search(
        self,
        event: AstrMessageEvent,
        query: str = "",
        mode: str = "search",
        category: str = "",
        limit: int = 5,
        memory_id: int = 0,
        offset: int = 0,
    ):
        """
        检索当前角色已经沉淀的长期生活记忆，用于回答用户追问、确认偏好、查找纠偏、人物关系、生活事件或最近影响判断的依据。
        Args:
            query(string): 自然语言检索内容，例如“最近别总出门”“他喜欢什么生活节奏”“好友相关记忆”。
            mode(string): search 内容检索；recent 近期；evidence 记忆原话；graph 关联路径；chapters 阶段回忆；diary 日记；originals 压缩前原话（query 可填起始 ISO 日期时间，offset 分页）。
            category(string): 可选分类过滤，例如 correction、short_term、relationship、chat_summary、feedback、expression。
            limit(int): 返回条数，默认 5，最多 12。
            memory_id(int): evidence 模式使用检索返回的记忆编号，禁止猜测。
            offset(int): 原话分页起点，首次为 0，后续使用 next_offset。
        """
        return await self.runtime.life_memory_search(
            event,
            query=str(query or "").strip(),
            mode=str(mode or "search").strip(),
            category=str(category or "").strip(),
            limit=self._tool_int(limit, 5),
            memory_id=self._tool_int(memory_id, 0),
            offset=self._tool_int(offset, 0),
        )

    @filter.llm_tool(name="life_knowledge")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_knowledge(self, event: AstrMessageEvent, action: str = "list", query: str = "", offset: int = 0, source_id: int = 0, passage_id: int = 0, title: str = "", content: str = "", origin: str = "", quote: str = "", summary: str = "", reflection: str = "", question: str = ""):
        """读取有来源的学习资料，保留理解、疑问和看法变化；资料不等于亲历。

        Args:
            action(string): list 列表；search 语义选资料；read 读原文；note 记录已读理解；notes 阅读手记；import 保存用户提供的原文到本会话资料库。
            query(string): search 对原文片段的自然语言问题。
            offset(int): list/search 分页起点；资料未扫描完时按 next_offset 继续。
            source_id(int): 已返回的资料编号，不可猜测。
            passage_id(int): 已返回的片段编号；read 留零续读，note 必填。
            title(string): import 的原文标题。
            content(string): import 的实际原文，不能虚构、用摘要冒充原文。
            origin(string): 原文来源，未知可留空。
            quote(string): note 的逐字原文依据。
            summary(string): note 对该片段的理解。
            reflection(string): 自己的感受或看法变化，可为空。
            question(string): 尚未弄清的问题，可为空。
        """
        return await self.runtime.memory_life.knowledge_tool(event, action=action, query=query, offset=self._tool_int(offset, 0), source_id=self._tool_int(source_id, 0), passage_id=self._tool_int(passage_id, 0), title=title, content=content, origin=origin, quote=quote, summary=summary, reflection=reflection, question=question)

    @filter.llm_tool(name="life_web_search")
    @guard_tool_arguments
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
    @guard_tool_arguments
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
    @guard_tool_arguments
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
    @guard_tool_arguments
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
    @guard_tool_arguments
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
    @guard_tool_arguments
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
    @guard_tool_arguments
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
        historical_target: str = "",
        historical_period: str = "",
        historical_time: str = "",
    ):
        """生成并发送单张生活图片，用于明确图片请求或角色自然想分享的瞬间。
        完整画面提示词原样保留；默认是画面创作，只有明确要求实际改变当前外观时才更新生活状态。
        当前外观来自当前事实，历史回现来自对应时刻记录；稳定人物特征以人设和档案为准，不由昵称猜性别。合影人物 A 是角色、B 是所选好友，外观分别归属，单套服装默认属于 A。
        有真实参考图且要求保留或修改原图时使用 edit_life_image；反推提示词可由 use_last_reverse_prompt 读取。
        调用前自然确认行动；pending 表示受理，结束本轮工具调用，实际送达后才谈结果。

        Args:
            prompt(string): 完整画面要求；实际换装时只描述动作、场景和构图，由已保存造型补足外观；其他完整提示词原样保留。
            subject_route(string): 主体枚举 current_character、group、scene、object、free；角色本人用 current_character，角色与好友同框用 group。
            participants(list[string]): 好友关系档案 ID；group 合影选择一位好友，省略时仅采用与当前私聊身份唯一匹配的档案，其他主体留空。
            friend_outfit(string): 好友完整穿搭；首次建立造型时与 friend_hair 同填，已有适用造型则留空，仅实际变化时更新。
            friend_hair(string): 好友发型；首次建立造型时与 friend_outfit 同填，已有适用造型则留空，仅实际变化时更新。
            friend_scene_category(string): 合影时按画面真实场景填写 home、sleep、outdoor、public 或 mixed。
            current_outfit_change(bool): 是否明确要求实际改变角色当前外观；仅看效果图为 false，true 时主体限 current_character 或 group。
            current_outfit_instruction(string): 实际外观变化的用户原始要求；可附所选衣橱整组细节，稳定人设特征仅在明确要求改变时更新。
            use_last_reverse_prompt(bool): 是否读取本会话上次反推提示词原文；不自动沿用原图。
            resolution(string): 用户明确指定的输出分辨率 1K、2K、4K，否则留空。
            provider(string): 用户明确指定的图片接口 auto、gpt、gemini、grok，否则留空。
            historical_target(string): 历史日期 YYYY-MM-DD 或 last（上次不同造型）；当前创作留空。
            historical_period(string): 历史时段 dawn、morning、forenoon、noon、afternoon、evening、night、late_night、daytime、evening_to_night。
            historical_time(string): 明确历史钟点 HH:MM，否则留空。
        """
        if str(subject_route or "free").strip() == "group":
            denial = self._life_permission_denial(
                event,
                "image:friend_reference",
                LifeActionScope.PRIVATE,
            )
            if denial:
                return denial
        use_reverse_cache = self._tool_bool(use_last_reverse_prompt)
        changes_life_state = bool(
            self._tool_bool(current_outfit_change)
            or str(friend_outfit or "").strip()
            or str(friend_hair or "").strip()
        )
        if changes_life_state:
            denial = self._life_permission_denial(
                event,
                "image:update_appearance",
                LifeActionScope.PRIVATE,
            )
            if denial:
                return denial
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
        options["historical_target"] = str(historical_target or "").strip()
        options["historical_period"] = str(historical_period or "").strip()
        options["historical_time"] = str(historical_time or "").strip()
        return await self.runtime.life_image_generate(
            event,
            "" if use_reverse_cache else str(prompt or "").strip(),
            **options,
        )

    @filter.llm_tool(name="life_photo_suite_generate")
    @guard_tool_arguments
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
        historical_target: str = "",
        historical_period: str = "",
        historical_time: str = "",
    ):
        """用户明确请求一组照片时生成 2 至 10 张，默认 3 张；普通接话、感谢和催进度不构成新拍摄请求。
        同组保持人物、造型、场景与光线连续，以景别和动作形成变化；使用 retry_indexes 仅重拍指定照片。
        合影人物 A 是角色、B 是所选好友，外观独立；当前事实与历史回现分别使用对应时间的资料。
        当前消息或引用中的图片自动作为参考；明确沿用上一张成功图片时设置 continue_last_result。
        调用前自然确认行动；pending 表示已受理，结束本轮工具调用，由后台交付后按结果自然回应。

        Args:
            prompt(string): 完整画面要求；完整提示词、分镜或时间轴原样保留。
            count(int): 本组张数，2 至 10，默认 3。
            reference_image(string): 参考图片路径或 URL；留空读取当前消息或引用图。
            continue_last_result(bool): 没有新图且明确沿用会话上一张成功图片时为 true；新拍摄为 false。
            subject_route(string): 主体枚举 current_character、group、scene、object、free；角色本人用 current_character，角色与好友同框用 group。
            participants(list[string]): 好友关系档案 ID；group 合影选择一位好友，省略时仅采用与当前私聊身份唯一匹配的档案，其他主体留空。
            friend_outfit(string): 好友完整穿搭；首次建立造型时与 friend_hair 同填，已有适用造型则留空，仅实际变化时更新。
            friend_hair(string): 好友发型；首次建立造型时与 friend_outfit 同填，已有适用造型则留空，仅实际变化时更新。
            friend_scene_category(string): 合影时按画面真实场景填写 home、sleep、outdoor、public 或 mixed。
            retry_indexes(list[int]): 重拍上一组的位置序号，从 1 开始；其他情况留空。
            resolution(string): 用户明确指定的输出分辨率 1K、2K、4K，否则留空。
            provider(string): 用户明确指定的图片接口 auto、gpt、gemini、grok，否则留空。
            historical_target(string): 历史日期 YYYY-MM-DD 或 last（上次不同造型）；当前创作留空。
            historical_period(string): 历史时段 dawn、morning、forenoon、noon、afternoon、evening、night、late_night、daytime、evening_to_night。
            historical_time(string): 明确历史钟点 HH:MM，否则留空。
        """
        if str(subject_route or "free").strip() == "group":
            denial = self._life_permission_denial(
                event,
                "photo_suite:friend_reference",
                LifeActionScope.PRIVATE,
            )
            if denial:
                return denial
        changes_friend_state = bool(
            str(friend_outfit or "").strip() or str(friend_hair or "").strip()
        )
        if changes_friend_state:
            denial = self._life_permission_denial(
                event,
                "photo_suite:update_friend_appearance",
                LifeActionScope.PRIVATE,
            )
            if denial:
                return denial
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
        options["historical_target"] = str(historical_target or "").strip()
        options["historical_period"] = str(historical_period or "").strip()
        options["historical_time"] = str(historical_time or "").strip()
        return await self.runtime.life_photo_suite_generate(
            event,
            str(prompt or "").strip(),
            **options,
        )

    @filter.llm_tool(name="edit_life_image")
    @guard_tool_arguments
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
        """依据真实参考图片改图、保持人物或构图，支持继续修改会话上一张成功结果。
        reference_image 留空时读取当前消息或引用图；无新图且明确继续上次修改时设置 continue_last_result，不复用历史临时路径。
        无参考图时告知缺少素材；只有明确允许独立新创作才设置 generate_without_reference，不能以重生成替代连续改图。
        合影参与者只用已给出的关系档案 ID；用户图片是画面参考，好友档案是身份依据，人物 A、B 的外观独立。
        调用前自然确认行动；pending 表示已受理，结束本轮工具调用，实际送达后才谈结果。

        Args:
            prompt(string): 完整画面要求；完整提示词、分镜或时间轴原样保留。
            reference_image(string): 参考图片路径或 URL；留空读取当前消息或引用图。
            continue_last_result(bool): 没有新图且明确沿用会话上一张成功图片时为 true；新拍摄为 false。
            generate_without_reference(bool): 明确允许无参考图的新创作时才为 true；连续改图保持 false。
            participants(list[string]): 已提供的关系档案 ID；合影选择一位好友，无需新建好友首帧或普通创作时留空。
            resolution(string): 用户明确指定的输出分辨率 1K、2K、4K，否则留空。
            provider(string): 用户明确指定的图片接口 auto、gpt、gemini、grok，否则留空。
        """
        if participants:
            denial = self._life_permission_denial(
                event,
                "image_edit:friend_reference",
                LifeActionScope.PRIVATE,
            )
            if denial:
                return denial
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
    @guard_tool_arguments
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

    @filter.llm_tool(name="life_style_learn")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_style_learn(
        self,
        event: AstrMessageEvent,
        reference_image: str = "",
        reference_images: list[str] | None = None,
        source_url: str = "",
        note: str = "",
        kind: str = "auto",
    ):
        """
        把用户发送、引用或明确指定的服装商品图、穿搭图、发型图、妆容图或美甲图学习为视觉衣橱候选。
        完整套装、上装、下装、鞋袜、配饰、发型、妆容与美甲会按图片可见内容分别保存；候选只用于以后需要新造型时参考，不会改变当前真实穿搭。
        用户只是询问图片内容、要求生成图片或真实换装时不要调用本工具。
        不会搜索或抓取网页；用户需要自行发送、引用或明确提供可读取的图片。
        工具会独立完成图片识别和入库；返回 pending 时结束本次工具轮次，不要等待、重复调用或声称已学习完成。后续消息正常聊天，实际候选结果会独立发送。

        Args:
            reference_image(string): 一张可学习图片的路径或直链；留空时自动使用当前或引用消息里的图片。
            reference_images(array[string]): 可选多张图片路径或直链；适合用户明确提供一组商品图时使用。
            source_url(string): 可选商品页或原始网页地址，只用于来源追溯，不会当作图片理解。
            note(string): 用户指定的学习重点或商品说明；只辅助取舍，不能覆盖图片可见事实。
            kind(string): 学习范围，可选 auto、outfit、top、bottom、footwear、accessory、hair、makeup、nails 或 both；默认 auto。
        """
        denial = self._life_permission_denial(
            event, "style:learn", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.life_style_learn(
            event,
            str(reference_image or "").strip(),
            reference_images=reference_images or [],
            source_url=str(source_url or "").strip(),
            note=str(note or "").strip(),
            kind=str(kind or "auto").strip(),
        )

    @filter.llm_tool(name="life_style_generate")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_style_generate(
        self,
        event: AstrMessageEvent,
        requirement: str = "",
        generation_mode: str = "",
        count: int = 1,
    ):
        """
        当用户明确提出穿搭、发型、妆容、美甲或生活场景需求，并希望生成新的生活化造型图片加入视觉衣橱时调用。
        本次要求优先，结合现实天气、活动与角色自身审美在后台生成；提交后可继续聊天。
        生成图经视觉核对后自动收入衣橱，衣物记为已拥有；实际穿戴随生活需要安排，生成图保存在衣橱中。
        文生图绝不使用角色参考图；图生图必须有可用图生图接口和已启用的当前角色参考图，缺少条件时必须如实失败，不能切换到另一种方式。

        Args:
            requirement(string): 用户本次希望生成的完整需求，例如“适合春天去书店的浅色通勤穿搭，带自然锁骨发和裸色短甲”。可留空，让模型自由构思。
            generation_mode(string): 可选 text_to_image 或 image_to_image；留空使用创意衣橱配置的默认方式。
            count(int): 要生成的图片数量，默认 1；每张图独立视觉核对后自动收入衣橱。
        """
        denial = self._life_permission_denial(
            event, "style:generate", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.life_style_generate(
            event,
            requirement=str(requirement or "").strip(),
            generation_mode=str(generation_mode or "").strip(),
            count=self._tool_int(count, 1),
        )

    @filter.llm_tool(name="life_wardrobe_manage")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_wardrobe_manage(
        self,
        event: AstrMessageEvent,
        operation: str = "query",
        item_ids: list[int] | None = None,
        reason: str = "",
        duration_minutes: int = 30,
    ):
        """查询数字衣橱审美与衣物状态，或执行本轮明确的纳入和洗护操作。
        灵感候选不等于拥有；adopt 正式纳入数字衣橱，不等于现实购物或已穿上。
        dirty 标为待洗，washing 开始洗护，stored 收纳，clean 从收纳取出。
        洗护依据后台实际观测推进，不能提前说洗好或晾干；无法操作时保留当前事实。

        Args:
            operation(string): query、adopt、dirty、washing、stored 或 clean。
            item_ids(array[int]): 从衣橱查询结果明确选定的编号，不猜编号。
            reason(string): 本轮明确要求和具体操作理由。
            duration_minutes(int): washing 的预计清洗分钟，默认30；预计时长不等于完成。
        """
        denial = self._life_permission_denial(
            event, "style:manage", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.manage_wardrobe(
            event,
            operation=operation,
            item_ids=self.runtime._style_item_ids(item_ids or []),
            reason=reason,
            duration_minutes=self._tool_int(duration_minutes, 30),
        )

    @filter.llm_tool(name="life_style_catalog")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_style_catalog(
        self,
        event: AstrMessageEvent,
        kind: str = "",
        limit: int = 20,
    ):
        """
        查看当前已学习且可用的视觉衣橱候选及编号，供用户检查或后续反馈。
        用户问已经学了哪些衣橱候选、要查看候选、候选数量或需要候选编号时调用。
        用户指定或追问套装、上装、下装、鞋袜、配饰、发型、妆容或美甲时，必须重新调用本工具并传入对应 kind；不得从此前留空 kind 的混合展示子集中推断该分类总数。
        本工具本身不会新增候选；用户明确要求生成新的候选时调用 life_style_generate，用户提供图片时调用 life_style_learn。

        Args:
            kind(string): 可选 outfit、top、bottom、footwear、accessory、hair、makeup 或 nails；留空查看全部类别。
            limit(int): 最多返回多少条候选，默认 20，最大 50；总库存数量不受此限制。
        """
        denial = self._life_permission_denial(
            event, "style:catalog", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.life_style_catalog_list(
            event,
            kind=str(kind or "").strip(),
            limit=self._tool_int(limit, 20),
        )

    @filter.llm_tool(name="life_style_feedback")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_style_feedback(
        self,
        event: AstrMessageEvent,
        feedback: str = "",
        item_ids: list[int] | None = None,
    ):
        """
        处理用户对视觉衣橱候选的自然语言反馈，例如喜欢、不适合、太厚、颜色不喜欢或不要再用。
        工具会语义判断候选分数和状态，只把稳定、可复用的意见沉淀为长期偏好；不使用关键词替换。
        item_ids 应填写候选结果中的编号；留空时只处理当前会话最近学习的一条候选，不能猜其他编号。

        Args:
            feedback(string): 用户对候选的完整原始反馈，不要改写成简单关键词。
            item_ids(array[int]): 需要调整的视觉衣橱候选编号。
        """
        denial = self._life_permission_denial(
            event, "style:feedback", LifeActionScope.PRIVATE
        )
        if denial:
            return denial
        return await self.runtime.life_style_feedback(
            event,
            str(feedback or "").strip(),
            item_ids=item_ids or [],
        )

    @filter.llm_tool(name="life_video_generate")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_video_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        duration: int = 0,
        subject_route: str = "free",
        participants: list[str] | None = None,
        friend_outfit: str = "",
        friend_hair: str = "",
        friend_scene_category: str = "",
        continue_last_result: bool = False,
        replace_reference_image: bool = False,
        historical_target: str = "",
        historical_period: str = "",
        historical_time: str = "",
    ):
        """用户明确请求视频时生成并发送短视频。
        用户给出的完整提示词、分镜和时间轴原样保留。只有用户明确指定总时长时填写 duration，否则为 0 并沿用视频设置。当前或引用图片自动作为完整首帧；无首帧时按主体路线生成。
        新拍摄默认 continue_last_result=false；明确沿用会话上一张成功图片才设 true。后补图片仅在明确更换参考来源时使用 replace_reference_image。
        合影人物 A 是角色、B 是所选好友，分别保持身份与造型。现成首帧沿用画面属性，无首帧时通过好友参数确定独立外观；历史回现使用对应时刻事实。
        调用前自然确认行动；pending 表示受理，结束本轮工具调用，视频实际送达后才谈结果。

        Args:
            prompt(string): 完整画面要求；完整提示词、分镜或时间轴原样保留。
            duration(int): 用户明确要求的视频总时长，单位秒，范围 1-15；未明确要求时填 0，使用视频设置默认值。
            subject_route(string): 主体枚举 current_character、group、scene、object、free；角色本人用 current_character，角色与好友同框用 group。
            participants(list[string]): 好友关系档案 ID；新建合影首帧选择一位好友，省略时仅采用与当前私聊身份唯一匹配的档案；现成首帧或普通创作可留空。
            friend_outfit(string): 好友完整穿搭；首次建立造型时与 friend_hair 同填，已有适用造型则留空，仅实际变化时更新。
            friend_hair(string): 好友发型；首次建立造型时与 friend_outfit 同填，已有适用造型则留空，仅实际变化时更新。
            friend_scene_category(string): 新建合影画面时填写 home、sleep、outdoor、public 或 mixed；现成首帧时留空。
            continue_last_result(bool): 没有新图且明确沿用会话上一张成功图片时为 true；新拍摄为 false。
            replace_reference_image(bool): 明确用后补图片替换视频参考来源或重新制作时为 true，否则 false。
            historical_target(string): 历史日期 YYYY-MM-DD 或 last（上次不同造型）；当前创作留空。
            historical_period(string): 历史时段 dawn、morning、forenoon、noon、afternoon、evening、night、late_night、daytime、evening_to_night。
            historical_time(string): 明确历史钟点 HH:MM，否则留空。
        """
        if str(subject_route or "free").strip() == "group" or participants:
            denial = self._life_permission_denial(
                event,
                "video:friend_reference",
                LifeActionScope.PRIVATE,
            )
            if denial:
                return denial
        options = {
            "duration": max(0, min(15, int(duration or 0))),
            "subject_route": str(subject_route or "free").strip(),
            "continue_last_result": self._tool_bool(continue_last_result),
        }
        if self._tool_bool(replace_reference_image):
            options["replace_reference_image"] = True
        if participants:
            options["participants"] = participants
        if str(friend_outfit or "").strip():
            options["friend_outfit"] = str(friend_outfit).strip()
        if str(friend_hair or "").strip():
            options["friend_hair"] = str(friend_hair).strip()
        if str(friend_scene_category or "").strip():
            options["friend_scene_category"] = str(friend_scene_category).strip()
        options["historical_target"] = str(historical_target or "").strip()
        options["historical_period"] = str(historical_period or "").strip()
        options["historical_time"] = str(historical_time or "").strip()
        return await self.runtime.life_video_generate(
            event,
            str(prompt or "").strip(),
            **options,
        )

    @filter.llm_tool(name="life_bili_browse")
    @guard_tool_arguments
    @_runtime_guard
    async def life_bili_browse(self, event: AstrMessageEvent, operation: str = "history", record_id: str = "") -> str:
        """查看真实的B站浏览经历，或继续了解用户分享的视频；不会主动发送消息。

        Args:
            operation(string): history 查看记录；understand 理解本轮分享的视频；pause 暂停；resume 续看；abandon 不再看；continue_later 留待以后；favorite 收藏；unfavorite 取消收藏；revisit 重看收藏。
            record_id(string): 记录 id；history 与 understand 可留空。仅允许访问当前会话或角色自己的记录。
        """
        import json

        service = self.runtime.bili_browsing
        scope = self.runtime._event_session_id(event)
        if operation == "understand":
            from .core.sight.bili import find_bili_target

            target = find_bili_target(event)
            if not target:
                return "本轮没有识别到B站视频链接。"
            await service._shared_video(event, target, scope)
            error = getattr(event, "_daily_life_bili_error", "")
            if error:
                return error
            return await service.prompt(event) or "视频内容暂时无法确认，不要凭标题猜测。"
        if operation == "history":
            records = await service.archive.list(scope=scope, limit=12)
            return json.dumps([service.card(item) for item in records], ensure_ascii=False)
        if operation in {"favorite", "unfavorite", "revisit"}:
            denial = self._life_permission_denial(event, "bilibili:" + operation, LifeActionScope.ADMIN)
            if denial:
                return denial
        record = await service.archive.get(record_id)
        if not record or record.get("scope") not in {"global", scope}:
            return "当前会话没有这条浏览记录。"
        return json.dumps(await service.control(record_id, operation), ensure_ascii=False)

    @filter.llm_tool(name="life_bili_message")
    @guard_tool_arguments
    @_runtime_guard
    async def life_bili_message(self, event: AstrMessageEvent, operation: str = "history", text: str = "") -> str:
        """与当前已验证身份仅交流B站相关内容，发送必须以真实回执为准。

        Args:
            operation(string): history 查看此人的B站话题私信；send 发送B站相关私信。
            text(string): send 时围绕B站内容或站内事务的自然话语；不用于生活闲聊或其他平台事务。
        """
        import json
        if event.get_group_id():
            return "请在自己的私聊中使用B站私信。"
        from .core.browsing.feed import BiliReadError
        try:
            result = await self.runtime.bili_browsing.social.message(
                self.runtime._event_session_id(event), operation, text,
                str(getattr(event.message_obj, "message_id", "") or ""))
            return json.dumps(result, ensure_ascii=False)
        except (ValueError, BiliReadError) as exc:
            return str(exc)

    @filter.llm_tool(name="life_video_understand")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_video_understand(
        self,
        event: AstrMessageEvent,
        target: str = "",
    ):
        """
        理解用户当前发送、引用或指定的视频，并把可确认的画面信息用于后续对话。
        适合用户问“这个视频里是什么”“刚才视频怎么看”“帮我看看这段视频”等场景；不要凭生活背景猜视频内容。
        调用前必须根据聊天语境先用角色口吻自然回应一句。
        Gemini 原生视频分析在后台处理；返回 pending 时结束本次工具轮次，结果会独立送达，后续消息可正常聊天。
        只有已完成的真实分析可以用于回答，不提前猜测视频内容。
        Args:
            target(string): 可选的视频文件路径或直链；留空时自动使用当前消息或引用消息里的视频。
        """
        return await self.runtime.life_video_understand(
            event, str(target or "").strip()
        )

    @filter.llm_tool(name="life_video_note")
    @guard_tool_arguments
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
        工具会在后台识别、转写、生成并发送总结；返回 pending 时结束本次工具轮次，不要等待、重复调用或声称总结已完成，后续消息正常聊天。
        Gemini 识别普通视频时使用完整视频及音轨证据；其他模型与 B站长视频按照转写配置使用本地 ASR 或必剪，并结合关键画面证据。按视频实际议题组织背景、论点、事实、数据、分析、风险和建议，以段落、重点列表和引用形成紧凑笔记。长内容会逐个处理全部时间窗，广告与无关片段不会生成章节；关键画面不会插入成品，没有音频但画面可确认时仍可生成。
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
    @guard_tool_arguments
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
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_voice_generate(
        self,
        event: AstrMessageEvent,
        text: str = "",
        emotion: str = "",
        emotion_category: str = "",
        voice_style: str = "",
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
            emotion(string): 可选自然情绪描述，用于记录表达意图，不参与语音风格推断。
            emotion_category(string): 可选情绪分类，只能是 neutral、happy、sad、angry 之一。
            voice_style(string): 可选语音风格枚举，只能是 neutral、happy、light、soft（轻柔低声）、sad、angry 之一。
            user_requested(bool): 用户明确要求发送语音时必须填 true；false 会保持文字回复。
            decision_reason(string): 简要说明用户本轮为什么需要语音；用于后台裁定记录，不会发给用户。
        """
        voice_kwargs = {
            "emotion": emotion,
            "emotion_category": emotion_category,
            "user_requested": self._tool_bool(user_requested),
            "decision_reason": str(decision_reason or "").strip(),
        }
        if voice_style:
            voice_kwargs["voice_style"] = voice_style
        return await self.runtime.life_voice_generate(
            event, str(text or "").strip(), **voice_kwargs
        )

    @filter.command("实时语音通话")
    @_runtime_guard
    async def command_voice_call(self, event: AstrMessageEvent):
        """创建一次性实时语音通话邀请。"""
        try:
            message = await self.runtime.create_voice_call_invite(event)
        except Exception as exc:
            message = f"暂时不能发起实时语音通话：{str(exc)[:240]}"
        await self._deliver_call_invite(event, message)

    @filter.llm_tool(name="life_voice_call_invite")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_voice_call_invite(
        self,
        event: AstrMessageEvent,
        greeting: str = "",
    ):
        """
        创建一次性实时语音通话邀请。
        仅当用户明确要求打电话、语音通话或实时说话时调用，不要因为用户只要求发送一条语音而调用。
        返回邀请链接后，先让用户点击接受；不要声称通话已经接通。

        Args:
            greeting(string): 可选的自然开场白。结合当前角色、时间、状态和最近话题判断：
                这次确实有合适由头先开口时才填写一句上下文相关的话；没有合适由头必须留空，
                让通话接通后先听用户说话。不要每次都填写“你好，听得到吗”等固定寒暄。
        """
        message = await self.runtime.create_voice_call_invite(
            event, greeting=str(greeting or "").strip()
        )
        await self._deliver_call_invite(event, message)

    @filter.command("AI音视频通话")
    @_runtime_guard
    async def command_video_call(self, event: AstrMessageEvent):
        """创建一次性 AI 音视频通话邀请。"""
        try:
            message = await self.runtime.create_video_call_invite(event)
        except Exception as exc:
            message = f"暂时不能发起 AI 音视频通话：{str(exc)[:240]}"
        await self._deliver_call_invite(event, message)

    @filter.llm_tool(name="life_video_call_invite")
    @guard_tool_arguments
    @_runtime_guard
    async def tool_life_video_call_invite(
        self,
        event: AstrMessageEvent,
        greeting: str = "",
    ):
        """
        创建一次性 AI 音视频通话邀请。
        仅当用户明确要求视频通话、AI 音视频通话或打开摄像头实时交流时调用；
        不要因为用户只要求普通语音或发送视频而调用。返回邀请链接后，先让用户点击接受。

        Args:
            greeting(string): 可选的自然开场白。只有当前话题确实适合接通后先开口时填写，
                没有合适由头时留空，让通话接通后先听用户说话。
        """
        message = await self.runtime.create_video_call_invite(
            event, greeting=str(greeting or "").strip()
        )
        await self._deliver_call_invite(event, message)

    @filter.llm_tool(name="life_emoji_send")
    @guard_tool_arguments
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
        调用成功后本轮表情发送已经完成，必须停止调用本工具，直接生成最终回复；不要重复调用。

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
        enrich_location_message(event, req)
        prepare_turn = self._runtime_hook("prepare_continuous_turn_llm_request")
        if prepare_turn and not prepare_turn(event, req):
            return
        inject_history_reminder = self._runtime_hook("inject_astrbot_history_reminder")
        if inject_history_reminder:
            inject_history_reminder(req, event)
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
        video_enabled = False
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
            realtime_call_enabled = bool(
                getattr(
                    getattr(life_config, "realtime_voice_call", None), "enabled", False
                )
            )
            if not realtime_call_enabled:
                for name in ("life_voice_call_invite", "life_video_call_invite"):
                    toolset.remove_tool(name)
            domains = getattr(self.runtime, "domains", None)
            ensure_map_context = getattr(domains, "ensure_map_context", None)
            map_available = getattr(domains, "map_tools_available", None)
            if not callable(map_available) or not map_available():
                resolution_due = getattr(domains, "home_location_resolution_due", None)
                schedule_background = getattr(
                    self.runtime, "_schedule_background_task", None
                )
                if (
                    callable(ensure_map_context)
                    and callable(resolution_due)
                    and resolution_due()
                    and callable(schedule_background)
                ):
                    schedule_background(
                        ensure_map_context(),
                        label="居住地定位预热",
                        key="map_context:home",
                    )
                for name in MAP_LLM_TOOL_NAMES:
                    toolset.remove_tool(name)
        await self.runtime.inject_life_context(req, event)
        setattr(
            event,
            "_daily_life_prefix_version",
            cache_prefix_version(getattr(req, "system_prompt", "")),
        )
        await prepare_tool_argument_checks(getattr(req, "func_tool", None))
        capture_identity = getattr(self.runtime, "capture_chat_model_identity", None)
        if callable(capture_identity):
            setattr(event, "_daily_life_model_identity", await capture_identity(event))
        setattr(event, "_daily_life_model_started", time.perf_counter())

    @filter.on_llm_response()
    @_runtime_guard
    async def on_llm_response(self, event: AstrMessageEvent, response):
        response_failed = self._response_is_agent_error(response)
        note_usage = getattr(self.runtime, "note_chat_model_usage", None)
        if callable(note_usage):
            started = getattr(event, "_daily_life_model_started", None)
            elapsed = (
                (time.perf_counter() - started) * 1000 if started is not None else None
            )
            note_usage(
                response,
                elapsed_ms=elapsed,
                failed=response_failed,
                model_identity=getattr(event, "_daily_life_model_identity", ""),
                prefix_version=getattr(event, "_daily_life_prefix_version", ""),
            )
        if self._runtime_hook_bool("stop_stale_continuous_turn_event", event):
            return
        if response_failed:
            setattr(event, self._AGENT_ERROR_SEEN_ATTR, True)
        else:
            setattr(event, self._LLM_RESPONSE_SEEN_ATTR, True)
            completion = str(getattr(response, "completion_text", "") or "").strip()
            marker = getattr(self.runtime, "mark_structured_pending_bot_text", None)
            if completion and callable(marker):
                marker(event, completion)
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
        agent_error = self._response_is_agent_error(response)
        if agent_error:
            setattr(event, self._AGENT_ERROR_SEEN_ATTR, True)
            suppress_media_error = getattr(
                self.runtime, "suppress_media_agent_error", None
            )
            suppressed = bool(
                callable(suppress_media_error) and suppress_media_error(event, response)
            )
            suppress_suite_error = getattr(
                self.runtime, "suppress_photo_suite_agent_error", None
            )
            if (
                not suppressed
                and callable(suppress_suite_error)
                and suppress_suite_error(event, response)
            ):
                logger.debug(
                    f"{LOG_PREFIX} 已抑制套图后续确认语错误，继续等待后台交付。"
                )
        reaction = getattr(self.runtime, "note_tool_reaction_agent_done", None)
        if callable(reaction):
            await reaction(event, response)

    @filter.on_using_llm_tool()
    @_runtime_guard
    async def on_using_llm_tool(
        self, event: AstrMessageEvent, tool, tool_args: dict | None = None
    ):
        if isinstance(tool, ArgumentCheckedTool) and tool.argument_error(
            {} if tool_args is None else tool_args
        ):
            return
        tool_name = str(getattr(tool, "name", tool) or "").strip()
        if tool_name == "life_video_generate":
            setattr(event, self._VIDEO_TOOL_USED_ATTR, True)
            capture_reference = getattr(
                self.runtime, "note_life_video_reference_source", None
            )
            if callable(capture_reference):
                capture_reference(
                    event,
                    continue_last_result=self._tool_bool(
                        (tool_args or {}).get("continue_last_result", False)
                    ),
                    replace=self._tool_bool(
                        (tool_args or {}).get("replace_reference_image", False)
                    ),
                )
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
        if isinstance(tool, ArgumentCheckedTool) and tool.argument_error(
            {} if tool_args is None else tool_args
        ):
            return
        handler = getattr(self.runtime, "handle_llm_tool_respond", None)
        if callable(handler):
            await handler(event, tool, tool_args, tool_result)
        reaction = getattr(self.runtime, "note_tool_reaction_result", None)
        if callable(reaction):
            await reaction(event, tool, tool_args, tool_result)

    def _runtime_hook(self, name: str):
        hook = getattr(self.runtime, name, None)
        if callable(hook):
            return hook
        if (
            getattr(self, "_runtime_contract_validated", False)
            and name in self._REQUIRED_RUNTIME_METHODS
        ):
            raise RuntimeError(f"日常生活运行时必要方法不可用：{name}")
        return None

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
            if elapsed >= 0.1:
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
        if total_elapsed >= 0.1:
            logger.debug(message)

    def _log_response_gate_outcome(
        self,
        event: AstrMessageEvent,
        decision: dict | None = None,
        *,
        action: str = "",
        reason: str = "",
        trigger: str = "",
    ) -> None:
        """记录非正常放行的入口动作及其触发来源。"""
        payload = decision if isinstance(decision, dict) else {}
        action = str(action or payload.get("action") or "unknown").strip().lower()
        if payload.get("superseded"):
            action = "superseded"
        if action == "reply":
            return
        reason = str(reason or payload.get("reason") or "未提供").strip()
        action_labels = {
            "reply": "正常放行",
            "wait": "等待聚合",
            "observe": "观察不回复",
            "superseded": "后续消息接管",
            "handled": "后台任务接管",
            "recalled": "撤回消息跳过",
        }
        if not trigger:
            if action == "superseded":
                trigger = "连续话轮收束"
            elif payload.get("semantic"):
                trigger = "随心回复·语义裁定"
            elif action == "wait":
                trigger = "随心回复·等待聚合"
            elif action == "observe":
                trigger = "随心回复·状态裁定"
            else:
                trigger = "随心回复·入口门控"
        logger.info(
            f"{LOG_PREFIX} 触发={trigger}；"
            f"动作={action_labels.get(action, action)}；"
            f"原因={reason}"
        )

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
            current = getattr(event, "_daily_life_reply_is_current", None)
            if callable(current) and not current():
                event.clear_result()
                return
            if (
                name in self._NATURAL_SEGMENTATION_HOOKS
                and not self._reply_segmentation_enabled()
            ):
                continue
            await self._runtime_hook_apply(name, event, is_async=is_async)
            if self._runtime_hook_bool("stop_stale_continuous_turn_event", event):
                return
            if self._send_pipeline_should_stop(event):
                return

    def _defer_reply_preparation(self, event: AstrMessageEvent) -> bool:
        # WebChat 在当前流水线结束时关闭请求队列，须在请求内完成投递。
        if event.get_platform_name() == "webchat":
            return False
        schedule = getattr(self.runtime, "_schedule_background_task", None)
        if not callable(schedule):
            return False
        result = event.get_result()
        chain = getattr(result, "chain", None)
        if not chain or not all(
            str(getattr(item, "text", "") or "").strip() for item in chain
        ):
            return False
        keeper = getattr(self.runtime, "_chat_style_should_keep_default_send", None)
        if callable(keeper) and keeper(event, "\n".join(item.text for item in chain)):
            return False
        runtime = self.runtime

        def current(snapshot):
            return (
                not snapshot.is_stopped()
                and not runtime.event_was_recalled(snapshot)
                and runtime.continuous_turn_event_is_current(snapshot)
                and runtime.media_request_is_current_turn(snapshot)
            )

        ReplyPreparationService(
            schedule=schedule,
            prepare=self._send_pipeline_apply,
            delivered=self._record_reply_delivery,
            is_current=current,
            complete=runtime.complete_continuous_turn,
        ).defer(event)
        # 队列满时保留原文字交给框架发送，不在前台等待模型或 TTS。
        return True

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
        if self._defer_reply_preparation(event):
            return
        await self._send_pipeline_apply(event)
        if getattr(event, "get_result", lambda: None)() is None:
            self._runtime_hook_call("complete_continuous_turn", event)

    @filter.after_message_sent()
    @_runtime_guard
    async def after_message_sent(self, event: AstrMessageEvent):
        if getattr(event, "_daily_life_reply_deferred", False):
            return
        await self._record_reply_delivery(event)

    async def _record_reply_delivery(self, event: AstrMessageEvent):
        outbound_logger = getattr(self.runtime, "log_outbound_result_async", None)
        if not callable(outbound_logger):
            outbound_logger = getattr(self.runtime, "log_outbound_result", None)
        if callable(outbound_logger):
            outbound_result = outbound_logger(event, source="reply")
            if inspect.isawaitable(outbound_result):
                await outbound_result
        await self._runtime_hook_apply("note_bili_browse_delivered", event, is_async=True)
        voice_receipt = self._runtime_hook("note_voice_switch_message_sent")
        if voice_receipt:
            await voice_receipt(event)
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
        emoji_sender = self._runtime_hook("send_semantic_emoji_if_needed")
        if emoji_sender:
            await emoji_sender(event)
        await self._capture_chat_memory_bot_reply(event)
        life_exchange = self._runtime_hook("note_continuous_chat_exchange")
        if life_exchange:
            await life_exchange(event)
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
            self._log_response_gate_outcome(
                event,
                action="recalled",
                reason="原消息已撤回",
                trigger="撤回事件",
            )
            return
        if not is_chat_message_event(event):
            return
        self._runtime_hook_call("note_runtime_scope_activity", event)
        self._runtime_hook_call("note_conversation_incoming", event)
        await self._runtime_hook_apply("note_bili_browse_incoming", event, is_async=True)
        enrich_location_message(event)
        self._runtime_hook_call("note_continuous_turn_incoming", event)
        try:
            await self._runtime_hook_apply(
                "record_conversation_arrival", event, is_async=True
            )
            await self._runtime_hook_apply(
                "prepare_visual_media_from_event", event, is_async=True
            )
        finally:
            self._runtime_hook_call("note_continuous_turn_media_ready", event)
        for name in self._INCOMING_NOTE_HOOKS:
            self._runtime_hook_call(name, event)
        self._runtime_hook_call("note_semantic_segment_incoming_message", event)
        await self._capture_chat_memory(event)
        if self._runtime_hook_call("schedule_bili_summary_from_event", event):
            self._runtime_hook_call("complete_continuous_turn", event)
            self._log_response_gate_outcome(
                event,
                action="handled",
                reason="已由B站视频总结后台任务接管",
                trigger="B站视频总结",
            )
            return
        settle_turn = self._runtime_hook("settle_continuous_turn")
        if settle_turn and not await settle_turn(event):
            self._log_response_gate_outcome(
                event,
                action="superseded",
                reason="连续话轮已由后续消息接管",
                trigger="连续话轮收束",
            )
            return
        self._runtime_hook_call("mark_alias_directed_event_as_wake", event)
        self.runtime.note_proactive_activity(event)
        decision = await self.runtime.apply_response_gate_for_event(event)
        self._runtime_hook_call("_continuous_turn_restart_for_image", event)
        self._log_response_gate_outcome(event, decision)
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

    @filter.command("B站绑定")
    @_runtime_guard
    async def bili_binding_command(self, event: AstrMessageEvent, uid: str = ""):
        if event.get_group_id():
            yield event.plain_result("请在自己的私聊中绑定B站身份。")
            return
        try:
            result = await self.runtime.bili_browsing.social.binding(self.runtime._event_session_id(event), str(uid or "").strip())
        except Exception as exc:
            from .core.browsing.feed import BiliReadError
            if not isinstance(exc, (ValueError, BiliReadError)):
                raise
            result = str(exc)
        yield event.plain_result(result)

    @filter.command("B站解绑")
    @_runtime_guard
    async def bili_unbinding_command(self, event: AstrMessageEvent):
        if event.get_group_id():
            yield event.plain_result("请在原私聊会话中解绑。")
            return
        yield event.plain_result(await self.runtime.bili_browsing.social.binding(self.runtime._event_session_id(event), remove=True))

    @filter.command("B站登录")
    @_runtime_guard
    async def bili_login_command(self, event: AstrMessageEvent):
        denial = self._life_permission_denial(
            event, "bilibili:login", LifeActionScope.ADMIN
        )
        if denial:
            yield event.plain_result(denial)
            return
        async for result in self.runtime.bili_login(event):
            yield result

    @filter.command("B站登出")
    @_runtime_guard
    async def bili_logout_command(self, event: AstrMessageEvent):
        denial = self._life_permission_denial(
            event, "bilibili:logout", LifeActionScope.ADMIN
        )
        if denial:
            yield event.plain_result(denial)
            return
        yield await self.runtime.bili_logout(event)

    @filter.command("B站状态")
    @_runtime_guard
    async def bili_status_command(self, event: AstrMessageEvent):
        denial = self._life_permission_denial(
            event, "bilibili:status", LifeActionScope.ADMIN
        )
        if denial:
            yield event.plain_result(denial)
            return
        yield await self.runtime.bili_status(event)

    _register_page_web_apis = ComponentCall(
        "_part_daily_life_dashboard_component",
        DailyLifeDashboardComponent,
        "_register_page_web_apis",
    )

    _page_archive_records = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_archive_records"
    )

    _page_records_for_date = staticmethod(PageViewComponent._page_records_for_date)

    _build_page_config = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_build_page_config"
    )

    _page_reference_relationships = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_reference_relationships"
    )

    _load_page_config_schema = staticmethod(PageViewComponent._load_page_config_schema)

    @classmethod
    async def _page_config_schema(cls) -> dict:
        return await PageViewComponent._page_config_schema.__func__(
            cls,
        )

    _page_current_config = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_current_config"
    )

    _page_provider_options = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_provider_options"
    )

    _build_page_status = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_build_page_status"
    )

    _page_world_snapshot = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_world_snapshot"
    )

    _page_experience_snapshot = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_experience_snapshot"
    )

    _page_runtime_snapshot = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_runtime_snapshot"
    )

    _page_compact = staticmethod(PageViewComponent._page_compact)

    _page_internal_reference_segments = staticmethod(
        PageViewComponent._page_internal_reference_segments
    )

    _page_is_evidence_reference = staticmethod(
        PageViewComponent._page_is_evidence_reference
    )

    _page_readable_evidence = classmethod(
        PageViewComponent._page_readable_evidence.__func__
    )

    _page_readable_evidence_record = classmethod(
        PageViewComponent._page_readable_evidence_record.__func__
    )

    _page_strip_same_day_prefix = staticmethod(
        PageViewComponent._page_strip_same_day_prefix
    )

    _page_decision_display = classmethod(
        PageViewComponent._page_decision_display.__func__
    )

    _page_current_decision = staticmethod(PageViewComponent._page_current_decision)

    _page_daily_decision_candidates = ComponentCall(
        "_part_page_view_component",
        PageViewComponent,
        "_page_daily_decision_candidates",
    )

    _page_memo_status = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_memo_status"
    )

    _page_memo_lines = staticmethod(PageViewComponent._page_memo_lines)

    _page_memo_items = classmethod(PageViewComponent._page_memo_items.__func__)

    _page_memo_payload = staticmethod(PageViewComponent._page_memo_payload)

    _page_decision_sources = staticmethod(PageViewComponent._page_decision_sources)

    _page_observatory = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_observatory"
    )

    _page_unique_records = staticmethod(PageViewComponent._page_unique_records)

    _page_feedback_records = staticmethod(PageViewComponent._page_feedback_records)

    _page_group_environments = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_group_environments"
    )

    _page_domain_snapshot = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_domain_snapshot"
    )

    _page_session_display_name = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_session_display_name"
    )

    _page_action_decisions = staticmethod(PageViewComponent._page_action_decisions)

    _page_raw_action_decisions = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_raw_action_decisions"
    )

    _page_relationships = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_relationships"
    )

    _resolve_relationship_display_name = ComponentCall(
        "_part_page_view_component",
        PageViewComponent,
        "_resolve_relationship_display_name",
    )

    _is_generic_page_name = staticmethod(PageViewComponent._is_generic_page_name)

    _page_memory_evidence = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_memory_evidence"
    )

    _page_day = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_day"
    )

    _page_week_plan = ComponentCall(
        "_part_page_view_component", PageViewComponent, "_page_week_plan"
    )

    _PAGE_UPLOAD_CHUNK_SIZE = PortalBaseComponent._PAGE_UPLOAD_CHUNK_SIZE

    _page_public_error = staticmethod(PortalBaseComponent._page_public_error)

    _page_response = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "_page_response"
    )

    _page_json = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "_page_json"
    )

    _page_json_body = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "_page_json_body"
    )

    _page_receive_upload = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "_page_receive_upload"
    )

    _page_copy_upload = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "_page_copy_upload"
    )

    _page_copy_upload_sync = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "_page_copy_upload_sync"
    )

    _page_send_download = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "_page_send_download"
    )

    _page_request_method = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "_page_request_method"
    )

    _page_bool = staticmethod(PortalBaseComponent._page_bool)

    _page_query_args = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "_page_query_args"
    )

    page_status = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "page_status"
    )

    page_status_wait = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "page_status_wait"
    )

    page_config = ComponentCall(
        "_part_portal_base_component", PortalBaseComponent, "page_config"
    )

    _page_gallery_spec = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "_page_gallery_spec",
    )

    _page_friend_reference_key = staticmethod(
        PortalReferenceComponent._page_friend_reference_key
    )

    _page_gallery_dir = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "_page_gallery_dir",
    )

    _page_reference_dir = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "_page_reference_dir",
    )

    _page_safe_gallery_path = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "_page_safe_gallery_path",
    )

    _page_safe_reference_path = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "_page_safe_reference_path",
    )

    _page_reference_mime = staticmethod(PortalReferenceComponent._page_reference_mime)

    _page_reference_image_upload = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "_page_reference_image_upload",
    )

    _page_reference_image_delete = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "_page_reference_image_delete",
    )

    _page_reference_image_preview = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "_page_reference_image_preview",
    )

    page_character_reference_upload = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "page_character_reference_upload",
    )

    page_character_reference_delete = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "page_character_reference_delete",
    )

    page_character_reference_preview = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "page_character_reference_preview",
    )

    page_friend_reference_upload = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "page_friend_reference_upload",
    )

    page_friend_reference_delete = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "page_friend_reference_delete",
    )

    page_friend_reference_preview = ComponentCall(
        "_part_portal_reference_component",
        PortalReferenceComponent,
        "page_friend_reference_preview",
    )

    page_closet_list = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_list"
    )

    page_closet_import = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_import"
    )

    page_closet_generate = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_generate"
    )

    page_closet_preview = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_preview"
    )

    page_closet_status = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_status"
    )

    page_closet_feedback = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_feedback"
    )

    page_closet_review = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_review"
    )

    page_closet_delete = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_delete"
    )

    page_closet_backup = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_backup"
    )

    page_closet_restore = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "page_closet_restore"
    )

    _page_closet_payload = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "_page_closet_payload"
    )

    _page_closet_generation_timeouts = ComponentCall(
        "_part_portal_closet_component",
        PortalClosetComponent,
        "_page_closet_generation_timeouts",
    )

    _page_closet_item = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "_page_closet_item"
    )

    _page_closet_record = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "_page_closet_record"
    )

    _page_closet_path = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "_page_closet_path"
    )

    _page_safe_closet_path = ComponentCall(
        "_part_portal_closet_component", PortalClosetComponent, "_page_safe_closet_path"
    )

    _page_delete_closet_files = ComponentCall(
        "_part_portal_closet_component",
        PortalClosetComponent,
        "_page_delete_closet_files",
    )

    _page_write_closet_backup = ComponentCall(
        "_part_portal_closet_component",
        PortalClosetComponent,
        "_page_write_closet_backup",
    )

    _page_read_closet_backup = ComponentCall(
        "_part_portal_closet_component",
        PortalClosetComponent,
        "_page_read_closet_backup",
    )

    _page_closet_transfer_path = ComponentCall(
        "_part_portal_closet_component",
        PortalClosetComponent,
        "_page_closet_transfer_path",
    )

    _page_schedule_closet_cleanup = ComponentCall(
        "_part_portal_closet_component",
        PortalClosetComponent,
        "_page_schedule_closet_cleanup",
    )

    _page_closet_kind = staticmethod(PortalClosetComponent._page_closet_kind)

    _page_closet_text = staticmethod(PortalClosetComponent._page_closet_text)

    _page_closet_ids = staticmethod(PortalClosetComponent._page_closet_ids)

    _page_closet_mime = staticmethod(PortalClosetComponent._page_closet_mime)

    _page_closet_event = staticmethod(PortalClosetComponent._page_closet_event)

    page_emoji_list = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "page_emoji_list"
    )

    page_emoji_import = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "page_emoji_import"
    )

    page_emoji_preview = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "page_emoji_preview"
    )

    page_emoji_delete = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "page_emoji_delete"
    )

    page_emoji_sendable = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "page_emoji_sendable"
    )

    page_emoji_backup = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "page_emoji_backup"
    )

    page_emoji_restore = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "page_emoji_restore"
    )

    _page_emoji_payload = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_emoji_payload"
    )

    _page_emoji_payload_from_assets = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_emoji_payload_from_assets",
    )

    _page_emoji_item = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_emoji_item"
    )

    _page_emoji_asset_by_id = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_emoji_asset_by_id"
    )

    _page_emoji_assets_by_ids = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_emoji_assets_by_ids",
    )

    _page_emoji_ids = staticmethod(PortalEmojiComponent._page_emoji_ids)

    _page_cached_emoji_path = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_cached_emoji_path"
    )

    _page_emoji_is_animated = staticmethod(PortalEmojiComponent._page_emoji_is_animated)

    _page_safe_emoji_path = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_safe_emoji_path"
    )

    _page_delete_emoji_files = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_delete_emoji_files"
    )

    _page_build_emoji_backup = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_build_emoji_backup"
    )

    _page_build_emoji_backup_file = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_build_emoji_backup_file",
    )

    _page_write_emoji_backup = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_write_emoji_backup"
    )

    _page_emoji_backup_item = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_emoji_backup_item"
    )

    _page_backup_file_allowed = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_backup_file_allowed",
    )

    _page_backup_asset_name = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_backup_asset_name"
    )

    _page_restore_emoji_backup_records = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_restore_emoji_backup_records",
    )

    _page_emoji_transfer_path = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_emoji_transfer_path",
    )

    _page_schedule_emoji_transfer_cleanup = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_schedule_emoji_transfer_cleanup",
    )

    _page_merge_restored_emoji_asset = staticmethod(
        PortalEmojiComponent._page_merge_restored_emoji_asset
    )

    _page_validate_emoji_backup_zip = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_validate_emoji_backup_zip",
    )

    _page_read_emoji_backup_manifest = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_read_emoji_backup_manifest",
    )

    _page_restore_emoji_backup_file = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_restore_emoji_backup_file",
    )

    _page_safe_backup_member = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_safe_backup_member"
    )

    _page_backup_member_suffix = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_backup_member_suffix",
    )

    _page_emoji_cache_filename = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_emoji_cache_filename",
    )

    _page_emoji_max_bytes = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_emoji_max_bytes"
    )

    _page_receive_emoji = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_receive_emoji"
    )

    _page_imported_emoji_record = staticmethod(
        PortalEmojiComponent._page_imported_emoji_record
    )

    _page_schedule_emoji_import_vision = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_schedule_emoji_import_vision",
    )

    _page_image_mime = staticmethod(PortalEmojiComponent._page_image_mime)

    _page_emoji_preview_data = ComponentCall(
        "_part_portal_emoji_component", PortalEmojiComponent, "_page_emoji_preview_data"
    )

    _page_static_emoji_preview_data = ComponentCall(
        "_part_portal_emoji_component",
        PortalEmojiComponent,
        "_page_static_emoji_preview_data",
    )

    _page_static_emoji_preview_path = staticmethod(
        PortalEmojiComponent._page_static_emoji_preview_path
    )

    _page_emoji_placeholder_data_url = classmethod(
        PortalEmojiComponent._page_emoji_placeholder_data_url.__func__
    )

    _page_emoji_placeholder_bytes = staticmethod(
        PortalEmojiComponent._page_emoji_placeholder_bytes
    )

    page_refresh_state = ComponentCall(
        "_part_portal_action_component", PortalActionComponent, "page_refresh_state"
    )

    page_reset_day = ComponentCall(
        "_part_portal_action_component", PortalActionComponent, "page_reset_day"
    )

    page_generate_week = ComponentCall(
        "_part_portal_action_component", PortalActionComponent, "page_generate_week"
    )

    _page_validate_timeline = staticmethod(PortalLineComponent._page_validate_timeline)

    page_timeline_save = ComponentCall(
        "_part_portal_line_component", PortalLineComponent, "page_timeline_save"
    )

    page_memory_center = ComponentCall("_part_portal_memory_component", PortalMemoryComponent, "page_memory_center")
    page_memory_manage = ComponentCall("_part_portal_memory_component", PortalMemoryComponent, "page_memory_manage")

    page_experience_episode_correct = ComponentCall(
        "_part_portal_memory_component",
        PortalMemoryComponent,
        "page_experience_episode_correct",
    )

    page_experience_episode_protect = ComponentCall(
        "_part_portal_memory_component",
        PortalMemoryComponent,
        "page_experience_episode_protect",
    )

    page_experience_focus = ComponentCall(
        "_part_portal_memory_component", PortalMemoryComponent, "page_experience_focus"
    )

    page_experience_boundary = ComponentCall(
        "_part_portal_memory_component",
        PortalMemoryComponent,
        "page_experience_boundary",
    )

    page_experience_feedback = ComponentCall(
        "_part_portal_memory_component",
        PortalMemoryComponent,
        "page_experience_feedback",
    )

    @cached_property
    def _part_daily_life_dashboard_component(self):
        return DailyLifeDashboardComponent(self)

    @cached_property
    def _part_page_view_component(self):
        return PageViewComponent(self)

    @cached_property
    def _part_portal_base_component(self):
        return PortalBaseComponent(self)

    @cached_property
    def _part_portal_reference_component(self):
        return PortalReferenceComponent(self)

    @cached_property
    def _part_portal_closet_component(self):
        return PortalClosetComponent(self)

    @cached_property
    def _part_portal_emoji_component(self):
        return PortalEmojiComponent(self)

    @cached_property
    def _part_portal_action_component(self):
        return PortalActionComponent(self)

    @cached_property
    def _part_portal_line_component(self):
        return PortalLineComponent(self)

    @cached_property
    def _part_portal_memory_component(self):
        return PortalMemoryComponent(self)
