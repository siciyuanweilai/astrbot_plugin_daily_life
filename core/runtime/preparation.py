"""普通回复的后台加工与投递；不占用 AstrBot 当前消息流水线。"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger

from .markers import LOG_PREFIX


class ReplyPreparationService:
    def __init__(
        self,
        *,
        schedule: Callable[..., bool],
        prepare: Callable[[Any], Awaitable[None]],
        delivered: Callable[[Any], Awaitable[None]],
        is_current: Callable[[Any], bool],
        complete: Callable[[Any], None],
    ) -> None:
        self._schedule = schedule
        self._prepare = prepare
        self._delivered = delivered
        self._is_current = is_current
        self._complete = complete

    def defer(self, event: Any) -> bool:
        if getattr(event, "_daily_life_reply_deferred", False):
            return True
        result = event.get_result()
        if result is None:
            return False
        snapshot = copy.copy(event)
        detached = copy.copy(result)
        detached.chain = [copy.copy(item) for item in result.chain]
        snapshot.set_result(detached)
        # 平台连接和发送器继续共用，事件加工状态独立。
        for name, value in vars(event).items():
            if name.endswith("extras") and isinstance(value, dict):
                setattr(snapshot, name, dict(value))
        snapshot._daily_life_reply_is_current = lambda: self._is_current(snapshot)
        finished = False

        def finish() -> None:
            nonlocal finished
            if not finished:
                finished = True
                self._complete(snapshot)

        scope = str(getattr(event, "unified_msg_origin", "") or "")
        job = self._run(snapshot, finish)
        if not self._schedule(
            job, label="回复表达加工", key=f"reply_prepare:{scope}:{id(event)}",
            category="reply", on_cancel=finish,
        ):
            return False
        event._daily_life_reply_deferred = True
        event.clear_result()
        return True

    async def _run(self, event: Any, finish: Callable[[], None]) -> None:
        stage = "表达加工"
        sent = False
        try:
            if not self._is_current(event):
                return
            await self._prepare(event)
            if not self._is_current(event):
                return
            result = event.get_result()
            if result is not None and getattr(result, "chain", None):
                stage = "消息发送"
                await event.send(result)
                sent = True
                stage = "投递记录"
                await self._delivered(event)
        except Exception as exc:
            status = "回复已发送，投递记录更新失败" if sent else "回复投递未完成"
            logger.warning(
                f"{LOG_PREFIX} {status}（阶段：{stage}）：{type(exc).__name__}: {exc}",
                exc_info=True,
            )
        finally:
            event.clear_result()
            finish()
