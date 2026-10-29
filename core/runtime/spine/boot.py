from __future__ import annotations

import asyncio
import datetime
import inspect
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from astrbot.api import logger
from astrbot.core.star.context import Context

from ...archive import LifeArchive
from ...clock import now as life_now
from ...config.options import LifeSettings
from ...life import (
    LifeBackgroundComposer,
    LifeDomainService,
    WeatherClient,
)
from ...life.reliability import NonRetryableProviderError
from ...media import LifeMediaService
from ...media.picture.polling import ImageTaskFailed
from ...media.video.errors import VideoTaskFailed
from ...paths import runtime_data_path
from ...search import SearchService
from ...sources import ContactNameResolver
from ...sources.platforms import get_platform_id
from ..context import ContextSnapshotRepository
from ..delivery import ReplyDeliveryService
from ..gateway import ModelGateway
from ..markers import LOG_PREFIX
from ..scopes import RuntimeScopeState
from ..timer import LifeRhythmClock
from ..voicecall import VoiceCallManager
from ..voicecall.voicegateway import voice_gateway_start_error_detail
from .providerrecovery import recover_provider_commitments, remember_provider_block

_DURABLE_TASK_LABELS = {
    "daily_refresh": "每日生活刷新",
    "daily_review": "夜间生活复盘",
    "private_revisit": "私聊回访检查",
    "proactive_idle": "闲时主动检查",
    "media_delivery": "媒体投递恢复",
    "image_generation": "图片原任务查询恢复",
    "share_image_generation": "外部分享图片原任务查询恢复",
    "share_video_generation": "外部分享视频原任务查询恢复",
    "web_research": "网页研究报告",
    "proactive_commitment": "主动承诺履行",
    "commitment_contact_review": "联系约定语义复核",
    "commitment_photo": "承诺拍照",
    "commitment_video": "承诺拍视频",
}
_DURABLE_MEDIA_KINDS = (
    "media_delivery",
    "image_generation",
    "share_image_generation",
    "share_video_generation",
    "commitment_photo",
    "commitment_video",
)

# 平台管理器会先加载插件，再异步建立 IM 适配器连接。首次日程生成依赖
# 平台历史和联系人接口，因此不能只把“平台实例已创建”当成已就绪。
_PLATFORM_READY_TIMEOUT_SECONDS = 120.0
_PLATFORM_READY_POLL_SECONDS = 0.5


@dataclass(slots=True)
class RuntimeServices:
    config: LifeSettings
    media: Any
    memos: Any
    contact_resolver: Any
    weather_client: Any
    search: Any
    domains: Any
    composer: Any
    model_gateway: Any
    rhythm: LifeRhythmClock


class RuntimeServiceField:
    """让已取得租约的任务继续使用所属版本的服务。"""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        binding = instance.__dict__.get("_service_binding")
        services = binding.get() if isinstance(binding, ContextVar) else None
        services = services or instance.__dict__.get("_runtime_services")
        if services is not None:
            return getattr(services, self.name)
        if self.name not in instance.__dict__:
            raise AttributeError(self.name)
        return instance.__dict__[self.name]

    def __set__(self, instance: Any, value: Any) -> None:
        services = instance.__dict__.get("_runtime_services")
        if services is None:
            instance.__dict__[self.name] = value
        else:
            setattr(services, self.name, value)


class SpineBootComponentContext(Protocol):
    """此组件读取或更新的宿主接口。"""

    _bind_runtime: Any
    _build_rhythm: Any
    _build_runtime_services: Any
    _cancel_background_tasks: Any
    _cancel_proactive_idle_tasks: Any
    _close_character_appearance_tasks: Any
    _close_injection_snapshot_flight: Any
    _close_runtime_component: Any
    _close_runtime_services: Any
    _create_memos_service: Any
    _drain_durable_tasks: Any
    _durable_retry_at: Any
    _durable_runtime_handlers: Any
    _durable_task_key: Any
    _durable_task_owner: Any
    _durable_worker_lock: Any
    _durable_media_lock: Any
    _evict_scope_services: Any
    _init_background_tasks: Any
    _init_chat_memory_batcher: Any
    _init_continuous_turn_state: Any
    _init_response_gate_state: Any
    _init_sight: Any
    _injection_snapshot_cache: Any
    _install_runtime_services: Any
    _is_platform_ready: Any
    _leased_rhythm_callback: Any
    _load_friend_daily_looks: Any
    _log_boot_summary: Any
    _manager_has_configured_platform: Any
    _model_cache_meter: Any
    _native_video_client: Any
    _operation_locks: Any
    _page_status_changed: Any
    _page_status_version: Any
    _platform_instances: Any
    _platform_type: Any
    _proactive_air_state: Any
    _proactive_feedback_watch: Any
    _proactive_idle_candidates: Any
    _proactive_idle_tasks: Any
    _proactive_last_reply_at: Any
    _proactive_private_last_revisit_at: Any
    _retired_runtime_services: Any
    _run_durable_handler: Any
    _run_durable_task_worker: Any
    _run_durable_tasks_once: Any
    _run_durable_lane: Any
    _schedule_durable_task_pump: Any
    _runtime_data_path: Any
    _runtime_initialized: Any
    _runtime_service_binding: Any
    _runtime_service_condition: Any
    _runtime_service_lease_depth: Any
    _runtime_service_lease_owner: Any
    _runtime_services: Any
    _schedule_background_task: Any
    _semantic_segment_init_state: Any
    _service_binding: Any
    _service_condition: Any
    _service_lease_depth: Any
    _service_lease_owner: Any
    _service_swap_pending: Any
    _service_task_bindings: Any
    _service_users: Any
    _service_version_users: Any
    _shutdown_chat_memory_batcher: Any
    _shutdown_started: Any
    _sight_flight: Any
    _sight_prepare_flight: Any
    _sight_reader: Any
    _start_chat_memory_batcher: Any
    _virtual_life_metrics: Any
    archive: Any
    begin_shutdown: Any
    check_autonomous_life_update: Any
    check_continuous_life: Any
    check_bili_browsing: Any
    check_wardrobe_life: Any
    close_memos_service: Any
    composer: Any
    config: Any
    contact_resolver: Any
    context: Any
    context_snapshot: Any
    conversation_timing: Any
    daily_generation: Any
    data_path: Any
    domains: Any
    ensure_startup_day_data: Any
    failed_dates: Any
    generation_lock: Any
    maintain_emoji_assets: Any
    media: Any
    memos: Any
    model_gateway: Any
    outbound_log: Any
    raw_config: Any
    reconcile_commitment_photo_tasks: Any
    reconcile_commitment_video_tasks: Any
    reconcile_scheduled_invite_contacts: Any
    reply_delivery: Any
    resume_durable_image_generation: Any
    resume_durable_media_delivery: Any
    resume_share_image_generation: Any
    resume_share_video_generation: Any
    rhythm: Any
    run_commitment_photo_task: Any
    run_commitment_video_task: Any
    run_daily_refresh: Any
    run_nightly_review: Any
    run_private_revisit_check: Any
    run_proactive_commitment_task: Any
    run_commitment_contact_review_task: Any
    run_proactive_idle_check: Any
    run_weekly_refresh: Any
    runtime_service_lease: Any
    runtime_services: Any
    scope_state: Any
    search: Any
    voice_call: Any
    wait_for_platform_ready: Any
    weather_client: Any


class SpineBootComponent:
    def __init__(self, context: SpineBootComponentContext) -> None:
        self.context = context

    config = RuntimeServiceField()
    media = RuntimeServiceField()
    memos = RuntimeServiceField()
    contact_resolver = RuntimeServiceField()
    weather_client = RuntimeServiceField()
    search = RuntimeServiceField()
    domains = RuntimeServiceField()
    composer = RuntimeServiceField()
    model_gateway = RuntimeServiceField()
    rhythm = RuntimeServiceField()

    def initialize_runtime(
        self,
        context: Context,
        raw_config: Any,
        data_path: Path,
        *,
        defer_start: bool = False,
    ):
        self.context.context = context
        self.context.raw_config = raw_config
        self.context.data_path = data_path
        self.context.generation_lock = asyncio.Lock()
        self.context._operation_locks: dict[str, Any] = {}
        self.context._page_status_version = 0
        self.context._page_status_changed = asyncio.Condition()
        self.context._init_background_tasks()
        self.context._init_response_gate_state()
        self.context._init_continuous_turn_state()
        self.context._semantic_segment_init_state()
        self.context.reply_delivery = ReplyDeliveryService(self.context)
        self.context._injection_snapshot_cache: dict[str, Any] = {}
        self.context.scope_state = RuntimeScopeState(
            self.context, evict_services=self.context._evict_scope_services
        )
        self.context._service_condition = asyncio.Condition()
        self.context._service_users = 0
        self.context._service_swap_pending = False
        self.context._service_lease_depth = ContextVar(
            f"daily_life_service_lease_{id(self.context)}", default=0
        )
        self.context._service_lease_owner = ContextVar(
            f"daily_life_service_lease_owner_{id(self.context)}", default=None
        )
        self.context._durable_runtime_handlers: dict[str, Any] = {}
        self.context._durable_task_owner = f"runtime:{id(self.context)}"
        self.context.archive = LifeArchive(self.context.data_path, initialize=not defer_start)
        self.context.context_snapshot = ContextSnapshotRepository(self.context.archive)
        self.context._init_sight()
        self.context._bind_runtime(LifeSettings.from_dict(raw_config))
        self.context.voice_call = VoiceCallManager(self.context)
        self.context._init_chat_memory_batcher()
        self.context.failed_dates: dict[str, datetime.datetime] = {}
        self.context._proactive_last_reply_at: dict[str, datetime.datetime] = {}
        self.context._proactive_idle_candidates: dict[str, dict[str, Any]] = {}
        self.context._proactive_idle_tasks: dict[str, asyncio.Task] = {}
        self.context._virtual_life_metrics: dict[str, int] = {}
        self.context._proactive_private_last_revisit_at: dict[str, datetime.datetime] = {}
        self.context._proactive_air_state: dict[str, dict[str, Any]] = {}
        self.context._proactive_feedback_watch: dict[str, dict[str, Any]] = {}
        self.context._runtime_initialized = not defer_start
        if not defer_start:
            self.context._start_chat_memory_batcher()
            self.context.rhythm.start()
        self.context._log_boot_summary()

    async def initialize(self) -> None:
        if self.context._runtime_initialized:
            return
        await self.context.archive.initialize()
        domain_initializer = getattr(getattr(self.context, "domains", None), "initialize", None)
        if callable(domain_initializer):
            await domain_initializer()
        await self.context.archive.recover_leased_durable_tasks()
        search_service = getattr(
            getattr(self.context, "runtime_services", None), "search", None
        )
        if search_service is None:
            search_service = getattr(self.context, "search", None)
        restore_research = getattr(search_service, "restore_research_tasks", None)
        if callable(restore_research):
            await restore_research()
        friend_look_loader = getattr(self.context, "_load_friend_daily_looks", None)
        if callable(friend_look_loader):
            await friend_look_loader()
        self.context._start_chat_memory_batcher()
        self.context.rhythm.start()
        self.context._runtime_initialized = True
        self.context.meaning.start_memory_index()
        consolidator = getattr(self.context.archive, "consolidate_living_memories", None)
        if callable(consolidator):
            self.context._schedule_background_task(
                consolidator(), label="记忆脉络整理", key="startup_memory_consolidation",
                category="memory",
            )
        voice_call = getattr(self.context, "voice_call", None)
        start_voice_call = getattr(voice_call, "start_if_enabled", None)
        if callable(start_voice_call):
            try:
                await start_voice_call()
            except Exception as exc:
                # 网关启动失败不应阻断普通聊天，但必须留下明确的服务告警；
                # 用户下次创建邀请时仍会再次尝试启动并返回可读错误。
                settings = getattr(voice_call, "settings", None)
                logger.error(
                    "%s 实时语音通话网关启动失败：%s",
                    LOG_PREFIX,
                    voice_gateway_start_error_detail(exc, settings),
                )
        self.context._schedule_background_task(
            self.context.ensure_startup_day_data(),
            label="首次生活初始化",
            key="startup_life_day",
        )
        self.context._schedule_background_task(
            self.context.maintain_emoji_assets(),
            label="表情素材维护",
            key="startup_emoji_asset_maintenance",
        )
        self.context._schedule_background_task(
            self.context._run_durable_task_worker(),
            label="可恢复生活任务队列",
            key="durable_task_worker",
        )

    def _platform_instances(self) -> list[Any]:
        """返回当前上下文中的平台实例；测试替身或早期启动阶段可为空。"""
        manager = getattr(getattr(self.context, "context", None), "platform_manager", None)
        if manager is None:
            return []
        get_insts = getattr(manager, "get_insts", None)
        if callable(get_insts):
            try:
                return list(get_insts() or [])
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 读取平台实例失败：{type(exc).__name__}")
                return []
        try:
            return list(getattr(manager, "platform_insts", []) or [])
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 读取平台实例列表失败：{type(exc).__name__}")
            return []

    @staticmethod
    def _platform_type(instance: Any) -> str:
        meta = getattr(instance, "meta", None)
        if callable(meta):
            try:
                metadata = meta()
            except Exception:
                metadata = None
            name = str(getattr(metadata, "name", "") or "").strip().lower()
            if name:
                return name
        config = getattr(instance, "config", {}) or {}
        return str(config.get("type", "") or "").strip().lower()

    @staticmethod
    def _platform_client(instance: Any) -> Any:
        get_client = getattr(instance, "get_client", None)
        if callable(get_client):
            try:
                return get_client()
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 获取平台客户端失败，尝试备用属性："
                    f"{type(exc).__name__}"
                )
        return getattr(instance, "bot", None)

    @classmethod
    def _is_platform_ready(cls, instance: Any) -> bool:
        """判断平台是否已经可以执行联系人和历史查询。

        OneBot 的 PlatformStatus 会在任务启动时提前变成 running，不能单独
        用它判断反向 WebSocket 已建立；其客户端集合出现后才算真正就绪。
        其他适配器优先使用显式 ready/connected 标志，最后回退到运行状态。
        """
        platform_type = cls._platform_type(instance)
        if platform_type in {"webchat", "web_chat"}:
            return True

        client = cls._platform_client(instance)
        if platform_type in {"aiocqhttp", "onebot", "cqhttp"}:
            event_clients = getattr(client, "_wsr_event_clients", None)
            api_clients = getattr(client, "_wsr_api_clients", None)
            if isinstance(api_clients, (set, list, tuple, dict)):
                return bool(api_clients)
            if isinstance(event_clients, (set, list, tuple, dict)):
                return bool(event_clients)
            # OneBot 的运行状态会先于反向 WebSocket 连接建立，不能回退到
            # status=running，否则启动期联系人查询仍会抢跑。
            return False

        for attr in ("is_ready", "ready", "is_connected", "connected"):
            value = getattr(client, attr, None)
            if isinstance(value, bool):
                return value
            value = getattr(instance, attr, None)
            if isinstance(value, bool):
                return value

        status = getattr(instance, "status", None)
        status_value = str(getattr(status, "value", status) or "").lower()
        return status_value in {"running", "connected", "ready"}

    @staticmethod
    def _manager_has_configured_platform(manager: Any) -> bool:
        """判断是否存在已启用的真实平台配置，但实例尚未加载完成。"""
        configs = getattr(manager, "platforms_config", None)
        if not isinstance(configs, (list, tuple)):
            return False
        return any(
            bool((config or {}).get("enable", True))
            and str((config or {}).get("type", "") or "").lower()
            not in {"webchat", "web_chat"}
            for config in configs
            if isinstance(config, dict)
        )

    async def wait_for_platform_ready(
        self,
        *,
        timeout: float = _PLATFORM_READY_TIMEOUT_SECONDS,
        adapter_id: str = "",
        platform_type: str = "",
    ) -> bool:
        """等待已配置的平台具备联系人和历史查询能力。

        没有可枚举平台时直接放行，保证测试、仅网页聊天和无平台部署仍能
        生成日程。真实平台在超时后也会继续生成，但会明确记录降级原因。
        """
        manager = getattr(getattr(self.context, "context", None), "platform_manager", None)
        onebot_types = {"aiocqhttp", "onebot", "cqhttp"}

        def matches(platform_id: str, kind: str) -> bool:
            if adapter_id:
                return platform_id == adapter_id
            if platform_type in onebot_types:
                return kind in onebot_types
            return not platform_type or kind == platform_type

        def configured_target() -> bool:
            return any(
                isinstance(config, dict)
                and config.get("enable", True)
                and matches(str(config.get("id", "")), str(config.get("type", "")))
                for config in (getattr(manager, "platforms_config", None) or [])
            )

        instances = self.context._platform_instances()
        scoped = bool(adapter_id or platform_type)
        if not instances and not (
            configured_target() if scoped else self.context._manager_has_configured_platform(manager)
        ):
            return True

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout))
        while True:
            # 平台管理器可能在插件任务启动后才把已配置实例放入列表。
            instances = self.context._platform_instances()
            if scoped:
                instances = [
                    instance for instance in instances
                    if matches(get_platform_id(instance), self.context._platform_type(instance))
                ]
            if not instances:
                if not (
                    configured_target() if scoped else self.context._manager_has_configured_platform(manager)
                ):
                    return True
                pending_count = 1
            else:
                pending_count = sum(
                    1 for instance in instances if not self.context._is_platform_ready(instance)
                )
            if not pending_count:
                return True
            if loop.time() >= deadline:
                logger.warning(
                    "[日常生活] 等待目标平台连接超时，继续使用已有资料；"
                    f"未就绪平台={pending_count}"
                )
                return False
            await asyncio.sleep(_PLATFORM_READY_POLL_SECONDS)

    def _runtime_data_path(self) -> Path:
        return runtime_data_path(getattr(self.context, "data_path", None))

    def _build_runtime_services(
        self, config: LifeSettings, raw_config: Any | None = None
    ) -> RuntimeServices:
        media = LifeMediaService(config, self.context._runtime_data_path())
        memos = self.context._create_memos_service(config)
        contact_resolver = ContactNameResolver(
            self.context.context,
            self.context.raw_config if raw_config is None else raw_config,
            log_prefix=LOG_PREFIX,
            platform_ready_waiter=getattr(self.context, "wait_for_platform_ready", None),
        )
        weather_client = WeatherClient(config.weather)
        search = SearchService(self.context.context, config.search, task_store=self.context.archive)
        domains = LifeDomainService(
            config.domains,
            self.context.archive,
        )
        composer = LifeBackgroundComposer(
            self.context.context,
            config,
            self.context.archive,
            weather_client,
            contact_resolver,
            search,
            domains,
            wardrobe_shortage_callback=lambda: self.context.wardrobe_life.check_wardrobe_life(
                force_review=True,
            ),
        )
        composer.model_cache_metrics = self.context._model_cache_meter()
        return RuntimeServices(
            config=config,
            media=media,
            memos=memos,
            contact_resolver=contact_resolver,
            weather_client=weather_client,
            search=search,
            domains=domains,
            composer=composer,
            model_gateway=ModelGateway(composer),
            rhythm=self.context._build_rhythm(config),
        )

    def _install_runtime_services(self, services: RuntimeServices) -> None:
        self.context._runtime_services = services
        self.context._sight_reader = None

    def _current_runtime_services(self) -> RuntimeServices:
        installed = getattr(self.context, "_runtime_services", None)
        if installed is not None:
            return installed
        return RuntimeServices(
            config=self.context.config,
            media=getattr(self.context, "media", None),
            memos=getattr(self.context, "memos", None),
            contact_resolver=getattr(self.context, "contact_resolver", None),
            weather_client=getattr(self.context, "weather_client", None),
            search=getattr(self.context, "search", None),
            domains=getattr(self.context, "domains", None),
            composer=getattr(self.context, "composer", None),
            model_gateway=getattr(self.context, "model_gateway", None),
            rhythm=self.context.rhythm,
        )

    def _bind_runtime(self, config: LifeSettings) -> None:
        self.context._install_runtime_services(self.context._build_runtime_services(config))

    def _leased_rhythm_callback(self, callback, *, durable_kind: str = ""):
        handlers = getattr(self.context, "_durable_runtime_handlers", None)
        if not isinstance(handlers, dict):
            handlers = {}
            self.context._durable_runtime_handlers = handlers
        if durable_kind:
            handlers[durable_kind] = callback

        async def run():
            async with self.context.runtime_service_lease():
                if durable_kind:
                    now = life_now()
                    task_key = self.context._durable_task_key(durable_kind, now)
                    await self.context.archive.enqueue_durable_task(
                        task_key,
                        durable_kind,
                        {"scheduled_at": now.strftime("%Y-%m-%d %H:%M:%S")},
                        priority=80,
                    )
                    await self.context._run_durable_tasks_once()
                else:
                    await callback()

        return run

    async def _schedule_durable_task_pump(self) -> None:
        """调度器只提交后台执行，避免长任务占住下一分钟的扫描入口。"""
        for media_only, key, label, category in (
            (False, "durable_life_lane", "生活持久任务", "normal"),
            (True, "durable_media_lane", "媒体持久任务", "durable"),
        ):
            self.context._schedule_background_task(
                self.context._run_durable_lane(media_only=media_only),
                label=label,
                key=key,
                category=category,
            )

    @staticmethod
    def _durable_task_key(kind: str, now: datetime.datetime) -> str:
        """为定时入口生成稳定的持久任务键。"""
        if kind in {"daily_refresh", "daily_review"}:
            return f"{kind}:{now.strftime('%Y-%m-%d')}"
        return f"{kind}:{now.strftime('%Y-%m-%d-%H-%M')}"

    @staticmethod
    def _durable_retry_at(task: Any) -> str:
        """为可重试的持久任务提供有上限的指数退避时间。"""

        attempts = max(1, int(getattr(task, "attempts", 1) or 1))
        maximum = max(1, int(getattr(task, "max_attempts", 1) or 1))
        if attempts >= maximum:
            return ""
        delay = min(900, 30 * (2 ** max(0, attempts - 1)))
        return (life_now() + datetime.timedelta(seconds=delay)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    async def _run_durable_tasks_once(self) -> int:
        """并行处理生活和媒体队列；每条队列内部仍保持租约顺序。"""
        results = await asyncio.gather(
            self.context._run_durable_lane(media_only=False),
            self.context._run_durable_lane(media_only=True),
        )
        return sum(results)

    async def _run_durable_lane(self, *, media_only: bool) -> int:
        lock_name = "_durable_media_lock" if media_only else "_durable_worker_lock"
        lock = getattr(self.context, lock_name, None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(self.context, lock_name, lock)
        if lock.locked():
            return 0
        async with lock:
            return await self.context._drain_durable_tasks(media_only=media_only)

    async def _run_durable_handler(self, task: Any, handler: Any, owner: str) -> Any:
        async def invoke():
            if task.kind in {
                "media_delivery",
                "proactive_commitment",
                "commitment_contact_review",
                "commitment_photo",
                "commitment_video",
                "image_generation",
                "share_image_generation",
                "share_video_generation",
            }:
                return await handler(task)
            return await handler()

        async def renew():
            while True:
                await asyncio.sleep(60)
                if not await self.context.archive.renew_durable_task_lease(task.id, owner):
                    raise RuntimeError("持久任务租约已失效，停止本次执行")

        execution = asyncio.create_task(invoke())
        heartbeat = asyncio.create_task(renew())
        try:
            done, _ = await asyncio.wait(
                (execution, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                await heartbeat
            return await execution
        finally:
            for pending in (execution, heartbeat):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(execution, heartbeat, return_exceptions=True)

    async def _drain_durable_tasks(self, *, media_only: bool = False) -> int:
        if not media_only:
            await recover_provider_commitments(self.context)
            for name in (
                "reconcile_commitment_photo_tasks",
                "reconcile_commitment_video_tasks",
                "reconcile_commitment_contact_tasks",
                "reconcile_scheduled_invite_contacts",
            ):
                reconcile = getattr(self.context, name, None)
                if callable(reconcile):
                    await reconcile()
        await self.context.archive.recover_expired_durable_tasks()
        completed = 0
        processed_ids = []
        for _ in range(8):
            owner = (
                f"{getattr(self.context, '_durable_task_owner', id(self.context))}:{uuid.uuid4().hex}"
            )
            tasks = await self.context.archive.lease_durable_tasks(
                owner,
                limit=1,
                lease_seconds=1800,
                include_kinds=_DURABLE_MEDIA_KINDS if media_only else (),
                exclude_kinds=() if media_only else (*_DURABLE_MEDIA_KINDS, "web_research"),
                exclude_task_ids=processed_ids,
            )
            if not tasks:
                break
            task = tasks[0]
            processed_ids.append(task.id)
            if task.kind == "media_delivery":
                handler = getattr(self.context, "resume_durable_media_delivery", None)
            elif task.kind == "image_generation":
                handler = getattr(self.context, "resume_durable_image_generation", None)
            elif task.kind == "share_image_generation":
                handler = getattr(self.context, "resume_share_image_generation", None)
            elif task.kind == "share_video_generation":
                handler = getattr(self.context, "resume_share_video_generation", None)
            else:
                handler = getattr(self.context, "_durable_runtime_handlers", {}).get(task.kind)
            if not callable(handler):
                await self.context.archive.fail_durable_task(
                    task.id,
                    f"未知持久任务类型：{task.kind}",
                    owner=owner,
                    retry_at=self.context._durable_retry_at(task),
                )
                continue
            try:
                result = await self.context._run_durable_handler(task, handler, owner)
            except asyncio.CancelledError:
                raise
            except (NonRetryableProviderError, ImageTaskFailed, VideoTaskFailed) as exc:
                if isinstance(exc, NonRetryableProviderError):
                    try:
                        await remember_provider_block(self.context, task, exc, owner)
                    except Exception:
                        # 恢复依据保存失败不妨碍原任务释放租约并记录失败。
                        pass
                await self.context.archive.fail_durable_task(
                    task.id,
                    str(exc),
                    owner=owner,
                    permanent=True,
                )
                task_label = _DURABLE_TASK_LABELS.get(task.kind, "未知生活任务")
                logger.warning(
                    f"{LOG_PREFIX} 持久生活任务不可重试（{task_label}）"
                )
            except Exception as exc:
                await self.context.archive.fail_durable_task(
                    task.id,
                    str(exc),
                    owner=owner,
                    retry_at=self.context._durable_retry_at(task),
                )
                task_label = _DURABLE_TASK_LABELS.get(task.kind, "未知生活任务")
                logger.warning(f"{LOG_PREFIX} 持久生活任务失败（{task_label}）：{exc}")
                if int(getattr(task, "attempts", 0) or 0) >= int(
                    getattr(task, "max_attempts", 0) or 0
                ):
                    logger.error(
                        f"{LOG_PREFIX} 持久生活任务已进入死信终态（{task_label}），"
                        "请在任务面板检查 last_error。"
                    )
            else:
                if isinstance(result, dict) and result.get("retry_at"):
                    await self.context.archive.defer_durable_task(
                        task.id,
                        str(result.get("retry_at") or ""),
                        owner=owner,
                        reason=str(result.get("reason") or "等待任务条件成立"),
                        progress=result.get("progress"),
                    )
                    continue
                committed = await self.context.archive.complete_durable_task(
                    task.id,
                    result
                    if isinstance(result, dict)
                    else {
                        "kind": task.kind,
                        "completed_at": life_now().isoformat(),
                    },
                    owner=owner,
                )
                completed += int(committed)
        return completed

    async def _run_durable_task_worker(self) -> None:
        """启动时收束重启前遗留的任务；任务类型由运行时显式注册。"""
        try:
            await self.context._run_durable_tasks_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 持久生活任务恢复失败：{exc}")

    def _build_rhythm(self, config: LifeSettings | None = None) -> LifeRhythmClock:
        handlers = getattr(self.context, "_durable_runtime_handlers", None)
        if not isinstance(handlers, dict):
            handlers = {}
            self.context._durable_runtime_handlers = handlers
        handlers["proactive_commitment"] = self.context.run_proactive_commitment_task
        contact_review = getattr(self.context, "run_commitment_contact_review_task", None)
        if callable(contact_review):
            handlers["commitment_contact_review"] = contact_review
        handlers["commitment_photo"] = self.context.run_commitment_photo_task
        handlers["commitment_video"] = self.context.run_commitment_video_task
        return LifeRhythmClock(
            loop_delay_reporter=self.context._model_cache_meter().record_loop_delay,
            config=config or self.context.config,
            daily_task=self.context._leased_rhythm_callback(
                self.context.run_daily_refresh, durable_kind="daily_refresh"
            ),
            auto_update_task=self.context._leased_rhythm_callback(
                self.context.check_autonomous_life_update
            ),
            review_task=self.context._leased_rhythm_callback(
                self.context.run_nightly_review, durable_kind="daily_review"
            ),
            weekly_task=self.context._leased_rhythm_callback(self.context.run_weekly_refresh),
            proactive_revisit_task=self.context._leased_rhythm_callback(
                self.context.run_private_revisit_check, durable_kind="private_revisit"
            ),
            proactive_idle_task=self.context._leased_rhythm_callback(
                self.context.run_proactive_idle_check, durable_kind="proactive_idle"
            ),
            durable_task=self.context._leased_rhythm_callback(
                self.context._schedule_durable_task_pump
            ),
            wardrobe_task=self.context._leased_rhythm_callback(self.context.check_wardrobe_life),
            browsing_task=(self.context._leased_rhythm_callback(self.context.check_bili_browsing)
                           if callable(getattr(self.context, "check_bili_browsing", None)) else None),
            continuous_life_task=self.context._leased_rhythm_callback(
                self.context.check_continuous_life
            ),
        )

    def _runtime_service_condition(self) -> asyncio.Condition:
        condition = getattr(self.context, "_service_condition", None)
        if isinstance(condition, asyncio.Condition):
            return condition
        condition = asyncio.Condition()
        self.context._service_condition = condition
        self.context._service_users = 0
        self.context._service_swap_pending = False
        return condition

    def _runtime_service_lease_depth(self) -> ContextVar[int]:
        depth = getattr(self.context, "_service_lease_depth", None)
        if isinstance(depth, ContextVar):
            return depth
        depth = ContextVar(f"daily_life_service_lease_{id(self.context)}", default=0)
        self.context._service_lease_depth = depth
        return depth

    def _runtime_service_lease_owner(self) -> ContextVar[asyncio.Task | None]:
        owner = getattr(self.context, "_service_lease_owner", None)
        if isinstance(owner, ContextVar):
            return owner
        owner = ContextVar(f"daily_life_service_lease_owner_{id(self.context)}", default=None)
        self.context._service_lease_owner = owner
        return owner

    def _runtime_service_binding(self) -> ContextVar[RuntimeServices | None]:
        binding = getattr(self.context, "_service_binding", None)
        if not isinstance(binding, ContextVar):
            binding = ContextVar(f"daily_life_services_{id(self.context)}", default=None)
            self.context._service_binding = binding
        return binding

    async def _retire_runtime_services(self, services: RuntimeServices) -> None:
        users = getattr(self.context, "_service_version_users", {})
        if users.get(id(services), 0):
            retired = getattr(self.context, "_retired_runtime_services", None)
            if retired is None:
                retired = {}
                self.context._retired_runtime_services = retired
            retired[id(services)] = services
            return
        await self.context._close_runtime_services(services)

    @asynccontextmanager
    async def runtime_service_lease(self):
        depth = self.context._runtime_service_lease_depth()
        owner = self.context._runtime_service_lease_owner()
        current_task = asyncio.current_task()
        current_depth = depth.get()
        if current_depth > 0 and owner.get() is current_task:
            token = depth.set(current_depth + 1)
            try:
                yield
            finally:
                depth.reset(token)
            return

        binding = self.context._runtime_service_binding()
        services = binding.get() or getattr(self.context, "_runtime_services", None)
        task_bindings = getattr(self.context, "_service_task_bindings", None)
        if task_bindings is None:
            task_bindings = {}
            self.context._service_task_bindings = task_bindings
        inherited_owner = owner.get()
        if (
            inherited_owner is not None
            and inherited_owner is not current_task
            and inherited_owner not in task_bindings
        ):
            services = getattr(self.context, "_runtime_services", None)
        condition = self.context._runtime_service_condition()
        async with condition:
            self.context._service_users = int(getattr(self.context, "_service_users", 0)) + 1
            users = getattr(self.context, "_service_version_users", None)
            if users is None:
                users = {}
                self.context._service_version_users = users
            if services is not None:
                users[id(services)] = users.get(id(services), 0) + 1
        depth_token = depth.set(1)
        owner_token = owner.set(current_task)
        service_token = binding.set(services)
        task_bindings[current_task] = services
        try:
            yield
        finally:
            depth.reset(depth_token)
            owner.reset(owner_token)
            binding.reset(service_token)
            task_bindings.pop(current_task, None)
            retired_services = None
            async with condition:
                self.context._service_users = max(
                    0, int(getattr(self.context, "_service_users", 0)) - 1
                )
                if self.context._service_users == 0:
                    condition.notify_all()
                if services is not None:
                    remaining = users[id(services)] - 1
                    if remaining:
                        users[id(services)] = remaining
                    else:
                        users.pop(id(services), None)
                        retired_services = getattr(
                            self.context, "_retired_runtime_services", {}
                        ).pop(id(services), None)
            if retired_services is not None:
                await self.context._close_runtime_services(retired_services)

    async def _begin_runtime_service_swap(self) -> None:
        condition = self.context._runtime_service_condition()
        async with condition:
            self.context._service_swap_pending = True

    async def _end_runtime_service_swap(self) -> None:
        condition = self.context._runtime_service_condition()
        async with condition:
            self.context._service_swap_pending = False
            condition.notify_all()

    @staticmethod
    async def _close_runtime_services(services: RuntimeServices) -> None:
        for label, service in (
            ("联系人服务", services.contact_resolver),
            ("天气服务", services.weather_client),
            ("搜索服务", services.search),
            ("媒体服务", services.media),
            ("MemOS 服务", services.memos),
        ):
            await SpineBootComponent._close_runtime_component(service, label)

    @staticmethod
    async def _close_runtime_component(
        service: Any,
        label: str,
        *,
        method_names: tuple[str, ...] = ("close",),
        thread_sync: bool = False,
    ) -> None:
        if service is None:
            return
        close = next(
            (
                method
                for name in method_names
                if callable(method := getattr(service, name, None))
            ),
            None,
        )
        if close is None:
            return
        try:
            if thread_sync and not inspect.iscoroutinefunction(close):
                await asyncio.to_thread(close)
                return
            result = close()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 关闭{label}失败：{exc}")

    def _log_boot_summary(self) -> None:
        logger.info(f"{LOG_PREFIX} 「愿此朝夕陪伴你的生活」启动完成")

    async def begin_shutdown(self) -> None:
        if bool(getattr(self.context, "_shutdown_started", False)):
            return
        self.context._shutdown_started = True
        voice_call = getattr(self.context, "voice_call", None)
        if voice_call is not None:
            await voice_call.close()
        self.context.rhythm.stop()
        cancel_idle_tasks = getattr(self.context, "_cancel_proactive_idle_tasks", None)
        if callable(cancel_idle_tasks):
            await cancel_idle_tasks()
        generation = getattr(self.context, "daily_generation", None)
        if generation is not None:
            await generation.close()
        close_appearance_tasks = getattr(
            self.context, "_close_character_appearance_tasks", None
        )
        if callable(close_appearance_tasks):
            await close_appearance_tasks()
        close_snapshot_flight = getattr(self.context, "_close_injection_snapshot_flight", None)
        if callable(close_snapshot_flight):
            await close_snapshot_flight()
        close_visual_flight = getattr(self.context, "_close_visual_context_flights", None)
        if callable(close_visual_flight):
            await close_visual_flight()
        browsing = self.context.__dict__.get("bili_browsing")
        if browsing is not None:
            await browsing.close()
        continuous = self.context.__dict__.get("continuous_life")
        if continuous is not None:
            await continuous.close()
        meaning = self.context.__dict__.get("meaning")
        if meaning is not None:
            await meaning.close()
        await self.context._shutdown_chat_memory_batcher()
        await self.context._cancel_background_tasks()

    async def terminate(self) -> None:
        await self.context.begin_shutdown()
        self.context.scope_state.clear()
        timing = getattr(self.context, "conversation_timing", None)
        if timing is not None:
            timing.clear_conversation_timing()
        outbound = getattr(self.context, "outbound_log", None)
        if outbound is not None:
            outbound.clear()
        forward = self.context.__dict__.get("text_forward")
        if forward is not None:
            forward.clear()
        recall = self.context.__dict__.get("recall")
        if recall is not None:
            recall.clear()
        addressing = self.context.__dict__.get("addressing")
        if addressing is not None:
            addressing.clear()
        reactions = self.context.__dict__.get("tool_reactions")
        if reactions is not None:
            reactions.clear()
        operation_locks = getattr(self.context, "_operation_locks", None)
        if operation_locks is not None:
            operation_locks.clear()
        flight = getattr(self.context, "_sight_flight", None)
        await self.context._close_runtime_component(flight, "视频任务")
        prepare_flight = getattr(self.context, "_sight_prepare_flight", None)
        await self.context._close_runtime_component(prepare_flight, "视频素材准备任务")
        await self.context._close_runtime_component(getattr(self.context, "_native_video_client", None), "Gemini 视频识别服务")
        await self.context._close_runtime_services(
            RuntimeServices(
                config=getattr(self.context, "config", None),
                media=getattr(self.context, "media", None),
                memos=getattr(self.context, "memos", None),
                contact_resolver=getattr(self.context, "contact_resolver", None),
                weather_client=getattr(self.context, "weather_client", None),
                search=getattr(self.context, "search", None),
                domains=getattr(self.context, "domains", None),
                composer=getattr(self.context, "composer", None),
                model_gateway=getattr(self.context, "model_gateway", None),
                rhythm=getattr(self.context, "rhythm", None),
            )
        )
        try:
            await self.context.close_memos_service()
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 关闭 MemOS 服务失败：{exc}")
        archive = getattr(self.context, "archive", None)
        await self.context._close_runtime_component(
            archive,
            "数据库",
            method_names=("aclose", "close"),
            thread_sync=True,
        )
        logger.info(f"{LOG_PREFIX} 已卸载")
