"""普通回复的后台加工与投递；不占用 AstrBot 当前消息流水线。"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import Any

from astrbot.api import logger

from .markers import LOG_PREFIX
from .replyhistory import suppress_reply_draft


class ReplyPreparationService:
    def __init__(
        self,
        *,
        schedule: Callable[..., bool],
        prepare: Callable[[Any], Awaitable[None]],
        delivered: Callable[[Any], Awaitable[None]],
        is_current: Callable[[Any], bool],
        complete: Callable[[Any], None],
        transaction: Callable[..., Any] = nullcontext,
        commit: Callable[[Any], Awaitable[None]] | None = None,
    ) -> None:
        self._schedule = schedule
        self._prepare = prepare
        self._delivered = delivered
        self._is_current = is_current
        self._complete = complete
        self._transaction = transaction
        self._commit = commit

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
        snapshot._daily_life_reply_sent_count = 0
        send = snapshot.send

        async def send_with_receipt(result, *args, **kwargs):
            returned = await send(result, *args, **kwargs)
            snapshot._daily_life_reply_sent_count += 1
            receipt = getattr(snapshot, "_daily_life_reply_history", None)
            if receipt is not None:
                receipt.record(snapshot, result)
            return returned

        snapshot.send = send_with_receipt
        finished = False

        def finish() -> None:
            nonlocal finished
            if not finished:
                finished = True
                if snapshot._daily_life_reply_sent_count:
                    self._complete(snapshot)

        scope = str(getattr(event, "unified_msg_origin", "") or "")
        job = self._run(snapshot, finish)
        if not self._schedule(
            job, label="回复表达加工", key=f"reply_prepare:{scope}:{id(event)}",
            category="reply", on_cancel=finish,
        ):
            return False
        event._daily_life_reply_deferred = True
        suppress_reply_draft(event)
        event.clear_result()
        return True

    async def _run(self, event: Any, finish: Callable[[], None]) -> None:
        try:
            async with self._transaction():
                await self._run_locked(event)
        finally:
            event.clear_result()
            finish()

    async def _run_locked(self, event: Any) -> None:
        stage = "表达加工"
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
        except Exception as exc:
            status = "回复部分已发送" if event._daily_life_reply_sent_count else "回复投递未完成"
            logger.warning(
                f"{LOG_PREFIX} {status}（阶段：{stage}）：{type(exc).__name__}: {exc}",
                exc_info=True,
            )
        finally:
            # 分段发送会清空 result，也可能只成功发送前几段；按实际回执结算。
            if event._daily_life_reply_sent_count:
                try:
                    if self._commit is not None:
                        await self._commit(event)
                    await self._delivered(event)
                except Exception as exc:
                    logger.warning(
                        f"{LOG_PREFIX} 回复已发送，投递记录更新失败：{type(exc).__name__}: {exc}",
                        exc_info=True,
                    )
