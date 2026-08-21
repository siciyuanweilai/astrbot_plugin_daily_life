from __future__ import annotations

import asyncio
import datetime
import inspect
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.core.star.context import Context

from ...archive import LifeArchive
from ...config.options.basis import (
    DEFAULT_CHAT_STYLE_PROMPT,
    is_legacy_chat_style_prompt,
)
from ...config.options import LifeSettings
from ...life import LifeBackgroundComposer, LifeDomainService, WeatherClient
from ...life.reliability import NonRetryableProviderError
from ...media import LifeMediaService
from ...paths import runtime_data_path
from ...search import SearchService
from ...sources import ContactNameResolver
from ..context import ContextSnapshotRepository
from ..delivery import ReplyDeliveryService
from ..gateway import ModelGateway
from ..markers import LOG_PREFIX
from ..scopes import RuntimeScopeState
from ..timer import LifeRhythmClock
from ..voicecall import VoiceCallManager

_DURABLE_TASK_LABELS = {
    "daily_refresh": "每日生活刷新",
    "daily_review": "夜间生活复盘",
    "private_revisit": "私聊回访检查",
    "proactive_idle": "闲时主动检查",
    "media_delivery": "媒体投递恢复",
    "web_research": "网页研究报告",
    "proactive_commitment": "主动承诺履行",
}

# 平台管理器会先加载插件，再异步建立 IM 适配器连接。首次日程生成依赖
# 平台历史和联系人接口，因此不能只把“平台实例已创建”当成已就绪。
_PLATFORM_READY_TIMEOUT_SECONDS = 120.0
_PLATFORM_READY_POLL_SECONDS = 0.5
_RUNTIME_SERVICE_LEASE_TIMEOUT_SECONDS = 30.0
_RUNTIME_SERVICE_SWAP_DRAIN_TIMEOUT_SECONDS = 30.0


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


class SpineBootMixin:
    def _migrate_legacy_chat_style_prompt(self) -> None:
        """仅迁移旧版内置默认文案，保留用户填写的自定义表达偏好。"""
        raw_config = self.raw_config
        if not isinstance(raw_config, dict):
            return
        chat_style = raw_config.get("chat_style_config")
        if not isinstance(chat_style, dict):
            return
        if not is_legacy_chat_style_prompt(chat_style.get("casual_short_prompt")):
            return

        chat_style["casual_short_prompt"] = DEFAULT_CHAT_STYLE_PROMPT
        save_config = getattr(raw_config, "save_config", None)
        if not callable(save_config):
            return
        try:
            save_config()
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 迁移短句风格默认文案失败：{exc}")

    def __init__(
        self,
        context: Context,
        raw_config: Any,
        data_path: Path,
        *,
        defer_start: bool = False,
    ):
        self.context = context
        self.raw_config = raw_config
        self.data_path = data_path
        self.generation_lock = asyncio.Lock()
        self._operation_locks: dict[str, Any] = {}
        self._init_daily_generation_state()
        self._page_status_version = 0
        self._page_status_changed = asyncio.Condition()
        self._init_background_tasks()
        self._init_response_gate_state()
        self._init_continuous_turn_state()
        self._semantic_segment_init_state()
        self._init_t2i_forward_cache()
        self.reply_delivery = ReplyDeliveryService(self)
        self._injection_snapshot_cache: dict[str, Any] = {}
        self.scope_state = RuntimeScopeState(self)
        self._service_condition = asyncio.Condition()
        self._service_users = 0
        self._service_swap_pending = False
        self._service_lease_depth = ContextVar(
            f"daily_life_service_lease_{id(self)}", default=0
        )
        self._service_lease_owner = ContextVar(
            f"daily_life_service_lease_owner_{id(self)}", default=None
        )
        self._durable_runtime_handlers: dict[str, Any] = {}
        self._durable_task_owner = f"runtime:{id(self)}"
        self.archive = LifeArchive(self.data_path, initialize=not defer_start)
        self.context_snapshot = ContextSnapshotRepository(self.archive)
        self._init_sight()
        self._migrate_legacy_chat_style_prompt()
        self._bind_runtime(LifeSettings.from_dict(raw_config))
        self.voice_call = VoiceCallManager(self)
        self._init_chat_memory_batcher()
        self.failed_dates: dict[str, datetime.datetime] = {}
        self._proactive_last_reply_at: dict[str, datetime.datetime] = {}
        self._proactive_idle_candidates: dict[str, dict[str, Any]] = {}
        self._proactive_idle_tasks: dict[str, asyncio.Task] = {}
        self._virtual_life_metrics: dict[str, int] = {}
        self._proactive_private_last_revisit_at: dict[str, datetime.datetime] = {}
        self._proactive_air_state: dict[str, dict[str, Any]] = {}
        self._proactive_feedback_watch: dict[str, dict[str, Any]] = {}
        self._runtime_initialized = not defer_start
        if not defer_start:
            self._start_chat_memory_batcher()
            self.rhythm.start()
        self._log_boot_summary()

    async def initialize(self) -> None:
        if self._runtime_initialized:
            return
        await self.archive.initialize()
        domain_initializer = getattr(getattr(self, "domains", None), "initialize", None)
        if callable(domain_initializer):
            await domain_initializer()
        await self.archive.recover_leased_durable_tasks()
        search_service = getattr(
            getattr(self, "runtime_services", None), "search", None
        )
        if search_service is None:
            search_service = getattr(self, "search", None)
        restore_research = getattr(search_service, "restore_research_tasks", None)
        if callable(restore_research):
            await restore_research()
        friend_look_loader = getattr(self, "_load_friend_daily_looks", None)
        if callable(friend_look_loader):
            await friend_look_loader()
        self._start_chat_memory_batcher()
        self.rhythm.start()
        self._runtime_initialized = True
        voice_call = getattr(self, "voice_call", None)
        start_voice_call = getattr(voice_call, "start_if_enabled", None)
        if callable(start_voice_call):
            try:
                await start_voice_call()
            except Exception as exc:
                # 网关启动失败不应阻断普通聊天，但必须留下明确的服务告警；
                # 用户下次创建邀请时仍会再次尝试启动并返回可读错误。
                logger.error(
                    f"{LOG_PREFIX} 实时语音通话网关启动失败：{type(exc).__name__}"
                )
        self._schedule_background_task(
            self.ensure_startup_day_data(),
            label="首次生活初始化",
            key="startup_life_day",
        )
        self._schedule_background_task(
            self.maintain_emoji_assets(),
            label="表情素材维护",
            key="startup_emoji_asset_maintenance",
        )
        self._schedule_background_task(
            self._run_durable_task_worker(),
            label="可恢复生活任务队列",
            key="durable_task_worker",
        )

    def _platform_instances(self) -> list[Any]:
        """返回当前上下文中的平台实例；测试替身或早期启动阶段可为空。"""
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
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
            # 兼容旧版或测试替身客户端；只有客户端明确暴露了反向
            # WebSocket 状态时，才按连接集合进行严格判断。
            if event_clients is None and api_clients is None:
                return bool(client)
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
    ) -> bool:
        """等待已配置的平台具备联系人和历史查询能力。

        没有可枚举平台时直接放行，保证测试、仅网页聊天和无平台部署仍能
        生成日程。真实平台在超时后也会继续生成，但会明确记录降级原因。
        """
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        instances = self._platform_instances()
        if not instances and not self._manager_has_configured_platform(manager):
            return True

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout))
        while True:
            # 平台管理器可能在插件任务启动后才把已配置实例放入列表。
            instances = self._platform_instances()
            if not instances:
                if not self._manager_has_configured_platform(manager):
                    return True
                pending_count = 1
            else:
                pending_count = sum(
                    1 for instance in instances if not self._is_platform_ready(instance)
                )
            if not pending_count:
                return True
            if loop.time() >= deadline:
                logger.warning(
                    "[日常生活] 等待平台适配器连接超时，先继续生活初始化；"
                    f"未就绪平台={pending_count}"
                )
                return False
            await asyncio.sleep(_PLATFORM_READY_POLL_SECONDS)

    def _runtime_data_path(self) -> Path:
        return runtime_data_path(getattr(self, "data_path", None))

    def _build_runtime_services(
        self, config: LifeSettings, raw_config: Any | None = None
    ) -> RuntimeServices:
        media = LifeMediaService(config, self._runtime_data_path())
        memos = self._create_memos_service(config)
        contact_resolver = ContactNameResolver(
            self.context,
            self.raw_config if raw_config is None else raw_config,
            log_prefix=LOG_PREFIX,
            platform_ready_waiter=getattr(self, "wait_for_platform_ready", None),
        )
        weather_client = WeatherClient(config.weather)
        search = SearchService(self.context, config.search, task_store=self.archive)
        domains = LifeDomainService(
            config.domains,
            self.archive,
        )
        composer = LifeBackgroundComposer(
            self.context,
            config,
            self.archive,
            weather_client,
            contact_resolver,
            search,
            domains,
        )
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
            rhythm=self._build_rhythm(config),
        )

    def _install_runtime_services(self, services: RuntimeServices) -> None:
        self.config = services.config
        self.media = services.media
        self.memos = services.memos
        self.contact_resolver = services.contact_resolver
        self.weather_client = services.weather_client
        self.search = services.search
        self.domains = getattr(services, "domains", None)
        self.composer = services.composer
        self.model_gateway = services.model_gateway
        self.rhythm = services.rhythm
        self._sight_reader = None

    def _current_runtime_services(self) -> RuntimeServices:
        return RuntimeServices(
            config=self.config,
            media=getattr(self, "media", None),
            memos=getattr(self, "memos", None),
            contact_resolver=getattr(self, "contact_resolver", None),
            weather_client=getattr(self, "weather_client", None),
            search=getattr(self, "search", None),
            domains=getattr(self, "domains", None),
            composer=getattr(self, "composer", None),
            model_gateway=getattr(self, "model_gateway", None),
            rhythm=self.rhythm,
        )

    def _bind_runtime(self, config: LifeSettings) -> None:
        self._install_runtime_services(self._build_runtime_services(config))

    def _leased_rhythm_callback(self, callback, *, durable_kind: str = ""):
        handlers = getattr(self, "_durable_runtime_handlers", None)
        if not isinstance(handlers, dict):
            handlers = {}
            self._durable_runtime_handlers = handlers
        if durable_kind:
            handlers[durable_kind] = callback

        async def run():
            async with self.runtime_service_lease():
                if durable_kind:
                    now = datetime.datetime.now()
                    task_key = self._durable_task_key(durable_kind, now)
                    await self.archive.enqueue_durable_task(
                        task_key,
                        durable_kind,
                        {"scheduled_at": now.strftime("%Y-%m-%d %H:%M:%S")},
                        priority=80,
                    )
                    await self._run_durable_tasks_once()
                else:
                    await callback()

        return run

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
        return (
            datetime.datetime.now()
            + datetime.timedelta(seconds=delay)
        ).strftime("%Y-%m-%d %H:%M:%S")

    async def _run_durable_tasks_once(self) -> int:
        """租用并执行一批白名单生活任务，禁止持久化任意可执行代码。"""
        owner = getattr(self, "_durable_task_owner", f"runtime:{id(self)}")
        await self.archive.recover_expired_durable_tasks()
        tasks = await self.archive.lease_durable_tasks(
            owner,
            limit=8,
            lease_seconds=1800,
            exclude_kinds=("web_research",),
        )
        completed = 0
        for task in tasks:
            if task.kind == "media_delivery":
                handler = getattr(self, "resume_durable_media_delivery", None)
            else:
                handler = getattr(self, "_durable_runtime_handlers", {}).get(task.kind)
            if not callable(handler):
                await self.archive.fail_durable_task(
                    task.id,
                    f"未知持久任务类型：{task.kind}",
                    owner=owner,
                    retry_at=self._durable_retry_at(task),
                )
                continue
            try:
                result = (
                    await handler(task)
                    if task.kind in {"media_delivery", "proactive_commitment"}
                    else await handler()
                )
            except asyncio.CancelledError:
                raise
            except NonRetryableProviderError as exc:
                await self.archive.fail_durable_task(
                    task.id,
                    str(exc),
                    owner=owner,
                    permanent=True,
                )
                task_label = _DURABLE_TASK_LABELS.get(task.kind, "未知生活任务")
                logger.warning(
                    f"{LOG_PREFIX} 持久生活任务不可重试（{task_label}）：{exc}"
                )
            except Exception as exc:
                await self.archive.fail_durable_task(
                    task.id,
                    str(exc),
                    owner=owner,
                    retry_at=self._durable_retry_at(task),
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
                    await self.archive.defer_durable_task(
                        task.id,
                        str(result.get("retry_at") or ""),
                        owner=owner,
                        reason=str(result.get("reason") or "等待任务条件成立"),
                    )
                    continue
                await self.archive.complete_durable_task(
                    task.id,
                    result
                    if isinstance(result, dict)
                    else {
                        "kind": task.kind,
                        "completed_at": datetime.datetime.now().isoformat(),
                    },
                    owner=owner,
                )
                completed += 1
        return completed

    async def _run_durable_task_worker(self) -> None:
        """启动时收束重启前遗留的任务；任务类型由运行时显式注册。"""
        try:
            reconcile = getattr(self, "reconcile_scheduled_invite_contacts", None)
            if callable(reconcile):
                await reconcile()
            await self._run_durable_tasks_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 持久生活任务恢复失败：{exc}")

    def _build_rhythm(self, config: LifeSettings | None = None) -> LifeRhythmClock:
        handlers = getattr(self, "_durable_runtime_handlers", None)
        if not isinstance(handlers, dict):
            handlers = {}
            self._durable_runtime_handlers = handlers
        handlers["proactive_commitment"] = self.run_proactive_commitment_task
        return LifeRhythmClock(
            config=config or self.config,
            daily_task=self._leased_rhythm_callback(
                self.run_daily_refresh, durable_kind="daily_refresh"
            ),
            auto_update_task=self._leased_rhythm_callback(
                self.check_autonomous_life_update
            ),
            review_task=self._leased_rhythm_callback(
                self.run_nightly_review, durable_kind="daily_review"
            ),
            proactive_revisit_task=self._leased_rhythm_callback(
                self.run_private_revisit_check, durable_kind="private_revisit"
            ),
            proactive_idle_task=self._leased_rhythm_callback(
                self.run_proactive_idle_check, durable_kind="proactive_idle"
            ),
            durable_task=self._leased_rhythm_callback(self._run_durable_tasks_once),
        )

    def _runtime_service_condition(self) -> asyncio.Condition:
        condition = getattr(self, "_service_condition", None)
        if isinstance(condition, asyncio.Condition):
            return condition
        condition = asyncio.Condition()
        self._service_condition = condition
        self._service_users = 0
        self._service_swap_pending = False
        return condition

    def _runtime_service_lease_depth(self) -> ContextVar[int]:
        depth = getattr(self, "_service_lease_depth", None)
        if isinstance(depth, ContextVar):
            return depth
        depth = ContextVar(f"daily_life_service_lease_{id(self)}", default=0)
        self._service_lease_depth = depth
        return depth

    def _runtime_service_lease_owner(self) -> ContextVar[asyncio.Task | None]:
        owner = getattr(self, "_service_lease_owner", None)
        if isinstance(owner, ContextVar):
            return owner
        owner = ContextVar(f"daily_life_service_lease_owner_{id(self)}", default=None)
        self._service_lease_owner = owner
        return owner

    @asynccontextmanager
    async def runtime_service_lease(self):
        depth = self._runtime_service_lease_depth()
        owner = self._runtime_service_lease_owner()
        current_task = asyncio.current_task()
        current_depth = depth.get()
        if current_depth > 0 and owner.get() is current_task:
            token = depth.set(current_depth + 1)
            try:
                yield
            finally:
                depth.reset(token)
            return

        condition = self._runtime_service_condition()
        async with condition:
            try:
                await asyncio.wait_for(
                    condition.wait_for(
                        lambda: not bool(getattr(self, "_service_swap_pending", False))
                    ),
                    timeout=_RUNTIME_SERVICE_LEASE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                raise RuntimeError("运行时服务正在切换，等待租约超时") from exc
            self._service_users = int(getattr(self, "_service_users", 0)) + 1
        depth_token = depth.set(1)
        owner_token = owner.set(current_task)
        try:
            yield
        finally:
            depth.reset(depth_token)
            owner.reset(owner_token)
            async with condition:
                self._service_users = max(
                    0, int(getattr(self, "_service_users", 0)) - 1
                )
                if self._service_users == 0:
                    condition.notify_all()

    async def _begin_runtime_service_swap(self) -> None:
        condition = self._runtime_service_condition()
        async with condition:
            self._service_swap_pending = True
            try:
                await asyncio.wait_for(
                    condition.wait_for(
                        lambda: int(getattr(self, "_service_users", 0)) == 0
                    ),
                    timeout=_RUNTIME_SERVICE_SWAP_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                self._service_swap_pending = False
                condition.notify_all()
                raise RuntimeError("运行时服务切换排空租约超时，配置未生效") from exc

    async def _end_runtime_service_swap(self) -> None:
        condition = self._runtime_service_condition()
        async with condition:
            self._service_swap_pending = False
            condition.notify_all()

    @staticmethod
    async def _close_runtime_services(services: RuntimeServices) -> None:
        for label, service in (
            ("联系人服务", services.contact_resolver),
            ("天气服务", services.weather_client),
            ("搜索服务", services.search),
            ("媒体服务", services.media),
        ):
            await SpineBootMixin._close_runtime_component(service, label)

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
        if bool(getattr(self, "_shutdown_started", False)):
            return
        self._shutdown_started = True
        voice_call = getattr(self, "voice_call", None)
        if voice_call is not None:
            await voice_call.close()
        self.rhythm.stop()
        cancel_idle_tasks = getattr(self, "_cancel_proactive_idle_tasks", None)
        if callable(cancel_idle_tasks):
            await cancel_idle_tasks()
        cancel_generations = getattr(self, "_cancel_daily_generation_tasks", None)
        if callable(cancel_generations):
            await cancel_generations()
        close_appearance_tasks = getattr(
            self, "_close_character_appearance_tasks", None
        )
        if callable(close_appearance_tasks):
            await close_appearance_tasks()
        close_snapshot_flight = getattr(
            self, "_close_injection_snapshot_flight", None
        )
        if callable(close_snapshot_flight):
            await close_snapshot_flight()
        close_query_vector_flight = getattr(
            self, "_close_meaning_query_vector_flight", None
        )
        if callable(close_query_vector_flight):
            await close_query_vector_flight()
        await self._shutdown_chat_memory_batcher()
        await self._cancel_background_tasks()

    async def terminate(self) -> None:
        await self.begin_shutdown()
        self.scope_state.clear()
        operation_locks = getattr(self, "_operation_locks", None)
        if operation_locks is not None:
            operation_locks.clear()
        flight = getattr(self, "_sight_flight", None)
        await self._close_runtime_component(flight, "视频任务")
        prepare_flight = getattr(self, "_sight_prepare_flight", None)
        await self._close_runtime_component(prepare_flight, "视频素材准备任务")
        await self._close_runtime_services(
            RuntimeServices(
                config=getattr(self, "config", None),
                media=getattr(self, "media", None),
                memos=getattr(self, "memos", None),
                contact_resolver=getattr(self, "contact_resolver", None),
                weather_client=getattr(self, "weather_client", None),
                search=getattr(self, "search", None),
                domains=getattr(self, "domains", None),
                composer=getattr(self, "composer", None),
                model_gateway=getattr(self, "model_gateway", None),
                rhythm=getattr(self, "rhythm", None),
            )
        )
        try:
            await self.close_memos_service()
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 关闭 MemOS 服务失败：{exc}")
        archive = getattr(self, "archive", None)
        await self._close_runtime_component(
            archive,
            "数据库",
            method_names=("aclose", "close"),
            thread_sync=True,
        )
        logger.info(f"{LOG_PREFIX} 已卸载")
