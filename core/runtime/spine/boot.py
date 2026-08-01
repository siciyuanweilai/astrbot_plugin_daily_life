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
from ...config.options import LifeSettings
from ...life import LifeBackgroundComposer, WeatherClient
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

_DURABLE_TASK_LABELS = {
    "daily_refresh": "每日生活刷新",
    "daily_review": "夜间生活复盘",
    "private_revisit": "私聊回访检查",
    "proactive_idle": "闲时主动检查",
}


@dataclass(slots=True)
class RuntimeServices:
    config: LifeSettings
    media: Any
    memos: Any
    contact_resolver: Any
    weather_client: Any
    search: Any
    composer: Any
    model_gateway: Any
    rhythm: LifeRhythmClock


class SpineBootMixin:
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
        self._bind_runtime(LifeSettings.from_dict(raw_config))
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
        await self.archive.recover_leased_durable_tasks()
        friend_look_loader = getattr(self, "_load_friend_daily_looks", None)
        if callable(friend_look_loader):
            await friend_look_loader()
        self._start_chat_memory_batcher()
        self.rhythm.start()
        self._runtime_initialized = True
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
        )
        weather_client = WeatherClient(config.weather)
        search = SearchService(self.context, config.search)
        composer = LifeBackgroundComposer(
            self.context,
            config,
            self.archive,
            weather_client,
            contact_resolver,
            search,
        )
        return RuntimeServices(
            config=config,
            media=media,
            memos=memos,
            contact_resolver=contact_resolver,
            weather_client=weather_client,
            search=search,
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

    async def _run_durable_tasks_once(self) -> int:
        """租用并执行一批白名单生活任务，禁止持久化任意可执行代码。"""
        owner = getattr(self, "_durable_task_owner", f"runtime:{id(self)}")
        await self.archive.recover_expired_durable_tasks()
        tasks = await self.archive.lease_durable_tasks(
            owner,
            limit=8,
            lease_seconds=1800,
        )
        completed = 0
        for task in tasks:
            handler = getattr(self, "_durable_runtime_handlers", {}).get(task.kind)
            if not callable(handler):
                await self.archive.fail_durable_task(
                    task.id,
                    f"未知持久任务类型：{task.kind}",
                    owner=owner,
                )
                continue
            try:
                await handler()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.archive.fail_durable_task(
                    task.id,
                    str(exc),
                    owner=owner,
                )
                task_label = _DURABLE_TASK_LABELS.get(task.kind, "未知生活任务")
                logger.warning(f"{LOG_PREFIX} 持久生活任务失败（{task_label}）：{exc}")
            else:
                await self.archive.complete_durable_task(
                    task.id,
                    {
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
            await self._run_durable_tasks_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 持久生活任务恢复失败：{exc}")

    def _build_rhythm(self, config: LifeSettings | None = None) -> LifeRhythmClock:
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
            await condition.wait_for(
                lambda: not bool(getattr(self, "_service_swap_pending", False))
            )
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
            await condition.wait_for(
                lambda: int(getattr(self, "_service_users", 0)) == 0
            )

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
        await self._close_runtime_services(
            RuntimeServices(
                config=getattr(self, "config", None),
                media=getattr(self, "media", None),
                memos=getattr(self, "memos", None),
                contact_resolver=getattr(self, "contact_resolver", None),
                weather_client=getattr(self, "weather_client", None),
                search=getattr(self, "search", None),
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
