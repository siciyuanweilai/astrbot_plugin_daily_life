from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

from ...clock import now as life_now
from ..markers import LOG_PREFIX


class SnapshotPackMixin:
    async def _gather_life_context_snapshot(
        self,
        event: Any = None,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        cache = getattr(self, "_injection_snapshot_cache", None)
        if cache is None:
            self._injection_snapshot_cache = {}
            cache = self._injection_snapshot_cache

        max_summaries = self.config.memory.max_injection_items
        experience_scope = ""
        session_id = ""
        group_id = ""
        if event is not None:
            session_id = self._event_session_id(event)
            group_id, _ = self._event_group_meta(event)
            experience_scope = group_id or session_id

        now_ts = life_now().timestamp()
        cache_key = f"{max_summaries}:{experience_scope}"
        if use_cache:
            cached = cache.get(cache_key)
            if (
                isinstance(cached, dict)
                and now_ts - float(cached.get("ts", 0.0) or 0.0) <= 8.0
            ):
                return dict(cached.get("data") or {})

        await self._settle_stale_reply_effects()

        started = time.perf_counter()
        repository = getattr(self, "context_snapshot", None)
        if repository is None:
            from ..context import ContextSnapshotRepository

            repository = ContextSnapshotRepository(self.archive)
        snapshot = await repository.read(
            max_summaries=max_summaries,
            experience_scope=experience_scope,
            session_id=session_id,
        )
        snapshot["experience_scope"] = experience_scope
        logger.debug(
            f"{LOG_PREFIX} 上下文快照读取完成，耗时 {time.perf_counter() - started:.3f} 秒"
        )

        cache[cache_key] = {"ts": now_ts, "data": snapshot}
        return dict(snapshot)
