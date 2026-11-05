"""复用完整内容的向量；调用方离开后，有限的在途请求仍可补齐缓存。"""
from __future__ import annotations

import asyncio
import hashlib
import math
from typing import Any

from astrbot.api import logger

from ..search.cache import TimedCache
from .parallel import gather_owned


class CandidateVectorCache:
    def __init__(self, *, max_items: int = 1024, ttl_seconds: int = 600) -> None:
        self.cache = TimedCache[tuple[float, ...]](max_items=max_items, ttl_seconds=ttl_seconds)
        self._pending: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._gate = asyncio.Semaphore(2)
        self.batch_size = 32
        self.max_pending_batches = 16
        self.timeout_seconds = 12.0
        self.closed = False
        self.stats = dict.fromkeys(("hits", "shared", "requests", "failures"), 0)

    @property
    def busy(self) -> bool:
        return bool(self._tasks)

    @staticmethod
    def key(provider_id: str, text: str) -> str:
        return hashlib.sha256(f"{provider_id}\0{text}".encode()).hexdigest()

    async def _load(self, provider: Any, texts: dict[str, str]) -> dict[str, tuple[float, ...]]:
        started = asyncio.get_running_loop().time()
        try:
            # 排队时间也计算在内；不会因前台取消而无限驻留。
            async with asyncio.timeout(self.timeout_seconds):
                async with self._gate:
                    self.stats["requests"] += 1
                    raw = list(await provider.get_embeddings(list(texts.values())))
                    if len(raw) != len(texts):
                        raise ValueError("候选 Embedding 数量与输入不一致")
                    vectors = [tuple(float(x) for x in row) for row in raw]
                    if any(not v or not any(v) or not all(math.isfinite(x) for x in v) for v in vectors):
                        raise ValueError("候选 Embedding 返回了无效向量")
                    if len({len(v) for v in vectors}) != 1:
                        raise ValueError("候选 Embedding 维度不一致")
                    found = dict(zip(texts, vectors))
                    if not self.closed:
                        for key, vector in found.items():
                            await self.cache.set(key, vector)
                    return found
        except Exception:
            self.stats["failures"] += 1
            raise
        finally:
            elapsed = asyncio.get_running_loop().time() - started
            if elapsed >= 1.0:
                logger.debug(f"[日常生活] 候选向量请求耗时 {elapsed:.2f}s（{len(texts)} 条，含排队）")

    def _finished(self, task: asyncio.Task, keys: tuple[str, ...]) -> None:
        self._tasks.discard(task)
        for key in keys:
            if self._pending.get(key) is task:
                self._pending.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def get_many(self, provider: Any, provider_id: str, texts: list[str]) -> list[list[float]]:
        if self.closed:
            raise RuntimeError("候选向量服务已关闭")
        values: dict[str, tuple[float, ...]] = {}
        missing: dict[str, str] = {}
        tasks: set[asyncio.Task] = set()
        keys = [self.key(provider_id, text) for text in texts]
        for key, text in dict(zip(keys, texts)).items():
            cached = await self.cache.get(key)
            if cached is not None:
                self.stats["hits"] += 1
                values[key] = cached
            elif key in self._pending:
                self.stats["shared"] += 1
                tasks.add(self._pending[key])
            else:
                missing[key] = text
        chunks = [dict(list(missing.items())[i:i + self.batch_size])
                  for i in range(0, len(missing), self.batch_size)]
        if len(self._tasks) + len(chunks) > self.max_pending_batches:
            raise RuntimeError("候选向量等待队列已满")
        for chunk in chunks:
            task = asyncio.create_task(self._load(provider, chunk))
            self._tasks.add(task)
            tasks.add(task)
            self._pending.update(dict.fromkeys(chunk, task))
            task.add_done_callback(lambda done, chunk_keys=tuple(chunk): self._finished(done, chunk_keys))
        if tasks:
            loaded = await gather_owned(*(asyncio.shield(task) for task in tasks))
            for batch in loaded:
                values.update(batch)
        return [list(values[key]) for key in keys]

    async def close(self) -> None:
        self.closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._pending.clear()
        await self.cache.clear()
