from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .markers import LOG_PREFIX


class DailyGenerationBusy(RuntimeError):
    pass


@dataclass(slots=True)
class DailyGenerationOperation:
    operation_id: str
    date: str
    source: str
    source_label: str
    phase: str = "queued"
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)
    started_monotonic: float = field(default_factory=time.monotonic, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "date": self.date,
            "source": self.source,
            "source_label": self.source_label,
            "phase": self.phase,
            "running": self.phase not in {"completed", "failed"},
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class DailyGenerationResult:
    day: Any
    web_inspiration: str
    operation_id: str
    reused: bool = False


class DailyGenerationMixin:
    _DAILY_GENERATION_SOURCE_LABELS = {
        "dashboard_reset": "面板重生",
        "command_reset": "命令重生",
        "outfit_seed": "穿搭补全",
        "injection_seed": "即时补全",
        "startup_seed": "首次启动补全",
        "daily_refresh": "每日刷新",
    }

    def _init_daily_generation_state(self) -> None:
        self._daily_generation_guard = asyncio.Lock()
        self._daily_generation_active: dict[str, DailyGenerationOperation] = {}
        self._daily_generation_last: dict[str, DailyGenerationOperation] = {}

    def _ensure_daily_generation_state(self) -> None:
        if not hasattr(self, "_daily_generation_guard"):
            self._init_daily_generation_state()

    def daily_generation_status(self, date_str: str = "") -> dict[str, Any]:
        self._ensure_daily_generation_state()
        key = str(date_str or "").strip()
        operation = self._daily_generation_active.get(key)
        if operation is None:
            operation = self._daily_generation_last.get(key)
        return operation.as_dict() if operation is not None else {}

    async def _notify_daily_generation_status(self, reason: str) -> None:
        notify = getattr(self, "mark_page_status_changed", None)
        if callable(notify):
            await notify(reason)

    async def _set_daily_generation_phase(
        self,
        operation: DailyGenerationOperation,
        phase: str,
    ) -> None:
        operation.phase = phase
        await self._notify_daily_generation_status(f"daily_generation_{phase}")

    @staticmethod
    def _consume_daily_generation_task(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            pass

    async def run_daily_generation(
        self,
        *,
        date: datetime.datetime,
        source: str,
        force: bool,
        extra: str = "",
        target_hour: int | None = None,
        delete_existing: bool = False,
        use_web: bool = False,
        search_keyword: str = "今日生活",
        search_prompt: str = "",
        search_category: str = "今日生活背景",
        reject_if_busy: bool = False,
    ) -> DailyGenerationResult:
        self._ensure_daily_generation_state()
        date_str = date.strftime("%Y-%m-%d")
        async with self._daily_generation_guard:
            current = self._daily_generation_active.get(date_str)
            if (
                current is not None
                and current.task is not None
                and not current.task.done()
            ):
                if reject_if_busy:
                    raise DailyGenerationBusy(f"{date_str} 的生活安排正在重生，请稍等")
                task = current.task
                reused = True
            else:
                operation = DailyGenerationOperation(
                    operation_id=uuid.uuid4().hex[:8],
                    date=date_str,
                    source=source,
                    source_label=self._DAILY_GENERATION_SOURCE_LABELS.get(
                        source, source
                    ),
                    started_at=datetime.datetime.now()
                    .astimezone()
                    .isoformat(timespec="seconds"),
                )
                task = asyncio.create_task(
                    self._execute_daily_generation(
                        operation,
                        date=date,
                        force=force,
                        extra=extra,
                        target_hour=target_hour,
                        delete_existing=delete_existing,
                        use_web=use_web,
                        search_keyword=search_keyword,
                        search_prompt=search_prompt,
                        search_category=search_category,
                    )
                )
                operation.task = task
                self._daily_generation_active[date_str] = operation
                task.add_done_callback(self._consume_daily_generation_task)
                reused = False

        result = await asyncio.shield(task)
        if reused:
            return DailyGenerationResult(
                day=result.day,
                web_inspiration=result.web_inspiration,
                operation_id=result.operation_id,
                reused=True,
            )
        return result

    async def _execute_daily_generation(
        self,
        operation: DailyGenerationOperation,
        *,
        date: datetime.datetime,
        force: bool,
        extra: str,
        target_hour: int | None,
        delete_existing: bool,
        use_web: bool,
        search_keyword: str,
        search_prompt: str,
        search_category: str,
    ) -> DailyGenerationResult:
        task_id = operation.operation_id
        logger.info(
            f"{LOG_PREFIX} 日程任务开始：任务={task_id}；日期={operation.date}；"
            f"来源={operation.source_label}"
        )
        web_inspiration = ""
        try:
            if use_web:
                await self._set_daily_generation_phase(operation, "searching")
                logger.debug(f"{LOG_PREFIX} 日程任务进入联网搜索：任务={task_id}")
                web_inspiration = await self.composer.search.inspiration(
                    search_keyword,
                    search_prompt,
                    category=search_category,
                    persona=await self.get_persona_text(),
                    today=operation.date,
                    trace_id=task_id,
                )
                logger.debug(f"{LOG_PREFIX} 日程任务联网搜索结束：任务={task_id}")

            await self._set_daily_generation_phase(operation, "waiting")
            if delete_existing:
                await self.archive.delete_day(operation.date)
            if not force:
                existing = await self.archive.get_day(operation.date)
                if existing is not None:
                    day = existing
                else:
                    await self._set_daily_generation_phase(operation, "generating")
                    day = await self.composer.generate_daily(
                        date=date,
                        force=False,
                        target_hour=target_hour,
                        extra=extra,
                        web_inspiration=web_inspiration,
                    )
            else:
                await self._set_daily_generation_phase(operation, "generating")
                day = await self.composer.generate_daily(
                    date=date,
                    force=True,
                    target_hour=target_hour,
                    extra=extra,
                    web_inspiration=web_inspiration,
                )

            if day is None:
                raise RuntimeError("日程模型未生成有效生活安排")
            failed_dates = getattr(self, "failed_dates", None)
            if isinstance(failed_dates, dict):
                failed_dates.pop(operation.date, None)
            operation.phase = "completed"
            operation.finished_at = (
                datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            )
            logger.info(
                f"{LOG_PREFIX} 日程任务完成：任务={task_id}；"
                f"节点={len(getattr(day, 'timeline', []) or [])}；"
                f"总耗时={time.monotonic() - operation.started_monotonic:.2f} 秒"
            )
            return DailyGenerationResult(day, web_inspiration, task_id)
        except asyncio.CancelledError:
            operation.phase = "failed"
            operation.error = "日程生成已取消"
            operation.finished_at = (
                datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            )
            raise
        except Exception as exc:
            operation.phase = "failed"
            operation.error = str(exc) or "日程生成失败"
            operation.finished_at = (
                datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            )
            logger.warning(
                f"{LOG_PREFIX} 日程任务失败：任务={task_id}；原因={operation.error}；"
                f"总耗时={time.monotonic() - operation.started_monotonic:.2f} 秒"
            )
            raise
        finally:
            async with self._daily_generation_guard:
                current = self._daily_generation_active.get(operation.date)
                if current is operation:
                    self._daily_generation_active.pop(operation.date, None)
                self._daily_generation_last[operation.date] = operation
            await self._notify_daily_generation_status(
                "daily_generation_completed"
                if operation.phase == "completed"
                else "daily_generation_failed"
            )

    async def _cancel_daily_generation_tasks(self) -> None:
        self._ensure_daily_generation_state()
        async with self._daily_generation_guard:
            tasks = [
                operation.task
                for operation in self._daily_generation_active.values()
                if operation.task is not None and not operation.task.done()
            ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "DailyGenerationBusy",
    "DailyGenerationMixin",
    "DailyGenerationOperation",
    "DailyGenerationResult",
]
