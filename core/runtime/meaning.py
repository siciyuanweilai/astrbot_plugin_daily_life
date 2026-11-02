from __future__ import annotations

import asyncio
import hashlib
import json
import math
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger

from ..life.tools import extract_json_from_text
from ..prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from ..search.cache import SingleFlight, TimedCache
from .markers import LOG_PREFIX
from .recallrank import fuse_memory_routes, memory_identity, memory_vector_text


class MeaningService:
    _SEMANTIC_RANK_TIMEOUT_SECONDS = 4.0
    _SEMANTIC_SESSION_CLOSE_TIMEOUT_SECONDS = 1.0
    # 共享查询允许迟到结果进入缓存，但不能在调用方离开后无限挂起。
    _MEANING_QUERY_REQUEST_TIMEOUT_SECONDS = 3 * _SEMANTIC_RANK_TIMEOUT_SECONDS
    _MEMORY_INDEX_BATCH_SIZE = 8
    # 历史索引允许适配器完成较慢的请求；聊天召回使用独立的短预算。
    _MEMORY_INDEX_TIMEOUT_SECONDS = 60.0
    _MEMORY_INDEX_REQUEST_INTERVAL_SECONDS = 1.0
    _MEMORY_INDEX_MAX_PASSES = 3
    _MEMORY_INDEX_RETRY_SECONDS = 5 * 60
    _MEANING_QUERY_VECTOR_CACHE_TTL_SECONDS = 5 * 60
    _MEANING_QUERY_VECTOR_CACHE_LIMIT = 256

    def __init__(
        self,
        *,
        embedding_providers: Callable[[], list[Any]],
        memory_config: Callable[[], Any],
        archive: Callable[[], Any],
        text_provider: Callable[[str], Awaitable[Any]],
        call_text_model: Callable[..., Awaitable[str]],
        close_text_session: Callable[[str], Awaitable[None]],
        schedule_background: Callable[..., bool],
    ) -> None:
        self._embedding_providers = embedding_providers
        self._memory_config = memory_config
        self._archive = archive
        self._text_provider = text_provider
        self._call_text_model = call_text_model
        self._close_text_session = close_text_session
        self._schedule_background = schedule_background
        self._query_vector_cache = TimedCache[list[float]](
            max_items=self._MEANING_QUERY_VECTOR_CACHE_LIMIT,
            ttl_seconds=self._MEANING_QUERY_VECTOR_CACHE_TTL_SECONDS,
        )
        self._query_vector_flight = SingleFlight[list[float]]()
        self._closed = False
        self._index_task: asyncio.Task | None = None
        self._index_retry_handle: asyncio.TimerHandle | None = None
        self._index_failure_counts: dict[int, int] = {}

    @staticmethod
    def _meaning_hash(text: str) -> str:
        return hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _meaning_cosine(left: list[float], right: list[float]) -> float:
        if not left or len(left) != len(right):
            return -1.0
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm <= 0 or right_norm <= 0:
            return -1.0
        return numerator / (left_norm * right_norm)

    def _meaning_embedding_provider(self) -> Any | None:
        try:
            providers = list(self._embedding_providers() or [])
        except Exception:
            return None
        if not providers:
            return None
        configured_id = str(
            getattr(self._memory_config(), "embedding_provider", "")
            or ""
        ).strip()
        if configured_id:
            for provider in providers:
                if self._meaning_provider_id(provider) == configured_id:
                    return provider
        return providers[0]

    @staticmethod
    def _meaning_query_vector_key(provider_id: str, query: str) -> str:
        normalized = unicodedata.normalize(
            "NFKC", " ".join(str(query or "").split())
        )
        return hashlib.sha256(
            f"{str(provider_id or '').strip()}\0{normalized}".encode()
        ).hexdigest()

    async def _meaning_query_vector(
        self,
        provider: Any,
        provider_id: str,
        query: str,
    ) -> list[float]:
        key = self._meaning_query_vector_key(provider_id, query)
        cache = self._query_vector_cache
        cached = await cache.get(key)
        if cached is not None:
            return list(cached)

        async def load() -> list[float]:
            recent = await cache.get(key)
            if recent is not None:
                return list(recent)
            async with asyncio.timeout(self._MEANING_QUERY_REQUEST_TIMEOUT_SECONDS):
                raw = await provider.get_embedding(query)
            vector = [float(value) for value in list(raw or [])]
            if not vector or not any(vector) or not all(math.isfinite(value) for value in vector):
                raise ValueError("查询 Embedding 返回了无效向量")
            if vector:
                await cache.set(key, vector)
            return vector

        vector, shared = await self._query_vector_flight.run(key, load)
        if shared:
            logger.debug(f"{LOG_PREFIX} 查询向量合并了同键并发请求")
        return list(vector)

    async def close(self) -> None:
        self._closed = True
        if self._index_retry_handle is not None:
            self._index_retry_handle.cancel()
            self._index_retry_handle = None
        if self._index_task is not None:
            self._index_task.cancel()
            await asyncio.gather(self._index_task, return_exceptions=True)
        self._index_failure_counts.clear()
        await self._query_vector_flight.close()
        await self._query_vector_cache.clear()

    @staticmethod
    def _meaning_provider_id(provider: Any) -> str:
        try:
            meta = provider.meta()
            return str(getattr(meta, "id", "") or getattr(meta, "model", "") or "embedding")
        except Exception:
            return str(getattr(provider, "model_name", "") or "embedding")

    @classmethod
    def _meaning_provider_cache_id(cls, provider: Any) -> str:
        provider_id = cls._meaning_provider_id(provider)
        try:
            meta = provider.meta()
            model = str(getattr(meta, "model", "") or "").strip()
        except Exception:
            model = str(getattr(provider, "model_name", "") or "").strip()
        # 部分 Embedding 适配器的 meta.model 为空，实际模型保存在 model 属性。
        model = model or str(getattr(provider, "model", "") or getattr(provider, "model_name", "") or "").strip()
        if model and model != provider_id:
            return f"{provider_id}:{model}"
        return provider_id

    async def _meaning_store_vectors(self, rows: list[dict[str, Any]]) -> None:
        if rows and not self._closed:
            await self._archive().upsert_memory_vectors(rows)

    @classmethod
    def _cached_vector(cls, cached: dict[str, Any], text: str, provider_id: str) -> list[float]:
        if cached.get("content_hash") != cls._meaning_hash(text) or cached.get("provider_id") != provider_id:
            return []
        try:
            vector = [float(value) for value in json.loads(cached["vector_json"])]
            dimensions = int(cached["dimensions"])
        except (KeyError, TypeError, ValueError):
            return []
        return vector if vector and any(vector) and len(vector) == dimensions and all(math.isfinite(value) for value in vector) else []

    @staticmethod
    def _memory_vector_text(item: Any) -> str:
        return memory_vector_text(item)

    @staticmethod
    def _memory_source_key(item: Any) -> tuple:
        return memory_identity(item)

    def start_memory_index(self) -> bool:
        """后台分批补齐历史记忆向量；不占用本轮聊天的召回预算。"""
        provider = self._meaning_embedding_provider()
        if self._closed or provider is None or not getattr(self._memory_config(), "enabled", True):
            return False
        scheduled = self._schedule_background(
            self._warm_memory_index(provider, self._meaning_provider_cache_id(provider)),
            label="长期记忆历史索引", key="meaning_long_term_index", category="memory",
        )
        if scheduled and self._index_retry_handle is not None:
            self._index_retry_handle.cancel()
            self._index_retry_handle = None
        return scheduled

    def _schedule_memory_index_retry(self) -> None:
        if self._closed or self._index_retry_handle is not None:
            return

        def resume() -> None:
            self._index_retry_handle = None
            if not self.start_memory_index() and getattr(self._memory_config(), "enabled", True):
                self._schedule_memory_index_retry()

        # 冷却期间释放后台任务名额；关闭服务时取消定时器。
        self._index_retry_handle = asyncio.get_running_loop().call_later(
            self._MEMORY_INDEX_RETRY_SECONDS, resume,
        )

    async def _warm_memory_index(self, provider: Any, provider_id: str) -> None:
        if self._closed:
            return
        self._index_task = asyncio.current_task()
        saved_count = 0
        remaining_count = 0
        try:
            archive = self._archive()
            stalled_passes = 0
            failure_counts = self._index_failure_counts
            while stalled_passes < self._MEMORY_INDEX_MAX_PASSES:
                saved_before = saved_count
                consecutive_failures = 0
                interrupted = False
                memories = await archive.list_recent_long_term_memories(limit=0)
                active_ids = {item.id for item in memories}
                for identifier in list(failure_counts):
                    if identifier not in active_ids:
                        failure_counts.pop(identifier, None)
                # 续跑先处理尚未尝试的记录，避免队首失败项让后面的历史一直饿死。
                memories.sort(key=lambda item: failure_counts.get(item.id, 0))
                for offset in range(0, len(memories), self._MEMORY_INDEX_BATCH_SIZE):
                    current = self._meaning_embedding_provider()
                    if (
                        self._closed or current is None
                        or not getattr(self._memory_config(), "enabled", True)
                        or self._meaning_provider_cache_id(current) != provider_id
                    ):
                        return
                    batch = memories[offset:offset + self._MEMORY_INDEX_BATCH_SIZE]
                    cached = await archive.get_memory_vectors("long_term_memory", [str(item.id) for item in batch])
                    missing = [item for item in batch if not self._cached_vector(
                        cached.get(str(item.id), {}), self._memory_vector_text(item), provider_id,
                    )]
                    for item in missing:
                        if self._closed:
                            return
                        # 与框架模型测试使用相同的单条接口；每条完成即落盘。
                        await asyncio.sleep(self._MEMORY_INDEX_REQUEST_INTERVAL_SECONDS)
                        try:
                            async with asyncio.timeout(self._MEMORY_INDEX_TIMEOUT_SECONDS):
                                raw = await provider.get_embedding(self._memory_vector_text(item))
                            vector = [float(value) for value in raw]
                            if not vector or not any(vector) or not all(math.isfinite(value) for value in vector):
                                raise ValueError("Embedding 返回了无效向量")
                        except Exception:
                            failure_counts[item.id] = failure_counts.get(item.id, 0) + 1
                            consecutive_failures += 1
                            if consecutive_failures >= 3:
                                interrupted = True
                                break
                            continue
                        consecutive_failures = 0
                        failure_counts.pop(item.id, None)
                        row = {
                            "target_type": "long_term_memory", "target_id": str(item.id),
                            "content_hash": self._meaning_hash(self._memory_vector_text(item)),
                            "provider_id": provider_id, "dimensions": len(vector),
                            "vector_json": json.dumps(vector, separators=(",", ":")),
                        }
                        if not self._closed:
                            saved_count += await archive.upsert_memory_vectors([row])
                            if saved_count and saved_count % 200 == 0:
                                logger.debug(f"{LOG_PREFIX} 长期记忆历史索引本轮已保存 {saved_count} 条")
                    if interrupted:
                        break

                # 每轮重新检查有效历史，不能用成功请求数推断已经补齐。
                memories = await archive.list_recent_long_term_memories(limit=0)
                cached = await archive.get_memory_vectors("long_term_memory", [str(item.id) for item in memories])
                def count_missing() -> int:
                    return sum(not self._cached_vector(
                        cached.get(str(item.id), {}), self._memory_vector_text(item), provider_id,
                    ) for item in memories)

                remaining_count = await asyncio.to_thread(count_missing)
                if not remaining_count:
                    logger.debug(f"{LOG_PREFIX} 长期记忆历史索引全部补齐：{len(memories)} 条有效记忆；本轮保存 {saved_count} 条")
                    return
                # 有实际进度就继续补齐；仅连续多轮毫无进展时暂停。
                # 总轮数限制会让大量历史在接口已恢复后仍提前停止。
                stalled_passes = stalled_passes + 1 if saved_count == saved_before else 0
                if stalled_passes < self._MEMORY_INDEX_MAX_PASSES:
                    logger.debug(f"{LOG_PREFIX} 长期记忆历史索引剩余 {remaining_count} 条，将在后台接续")
                    await asyncio.sleep(2 ** stalled_passes)
            logger.warning(f"{LOG_PREFIX} 长期记忆历史索引暂未完成：剩余 {remaining_count} 条；已保存 {saved_count} 条，冷却后自动续跑")
            self._schedule_memory_index_retry()
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 长期记忆历史索引暂未完成：{type(exc).__name__}；已保存 {saved_count} 条")
            self._schedule_memory_index_retry()
        finally:
            self._index_task = None

    @classmethod
    def _score_vectors(cls, query_vector, groups, vectors, limits, minimum_similarity):
        ranked = {}
        for kind, values in groups.items():
            minimum = minimum_similarity.get(kind) if isinstance(minimum_similarity, dict) else minimum_similarity
            scored = [
                (cls._meaning_cosine(query_vector, vectors.get((kind, target_id), [])), index, item)
                for index, (target_id, _, item) in enumerate(values)
            ]
            scored.sort(key=lambda row: (-row[0], row[1]))
            ranked[kind] = [
                item for score, _, item in scored
                if minimum is None or score >= minimum
            ][:max(0, limits.get(kind, 0))]
        return ranked

    async def _meaning_rank_with_embeddings(
        self,
        query: str,
        groups: dict[str, list[tuple[str, str, Any]]],
        limits: dict[str, int],
        *,
        minimum_similarity: float | dict[str, float] | None = None,
        cached_only: bool = False,
    ) -> dict[str, list[Any]] | None:
        provider = self._meaning_embedding_provider()
        if not provider or not str(query or "").strip():
            return None
        flat = [
            (kind, target_id, text, item)
            for kind, values in groups.items()
            for target_id, text, item in values
            if str(text or "").strip()
        ]
        if not flat:
            return {kind: [] for kind in groups}
        provider_id = self._meaning_provider_cache_id(provider)
        cached_by_kind: dict[str, dict[str, dict[str, Any]]] = {}
        archive = self._archive()
        for kind, values in groups.items():
            cached_by_kind[kind] = await archive.get_memory_vectors(
                kind, [target_id for target_id, _, _ in values]
            )

        def read_vectors():
            vectors = {}
            missing = []
            for kind, target_id, text, _ in flat:
                vector = self._cached_vector(cached_by_kind.get(kind, {}).get(target_id) or {}, text, provider_id)
                if vector:
                    vectors[(kind, target_id)] = vector
                else:
                    missing.append((kind, target_id, text))
            return vectors, missing

        vectors, missing = await asyncio.to_thread(read_vectors)
        if cached_only:
            if missing:
                self.start_memory_index()
            if not vectors:
                return {kind: [] for kind in groups}
            missing = []

        async def embed_all() -> tuple[list[float], list[list[float]]]:
            query_vector = await self._meaning_query_vector(
                provider,
                provider_id,
                query,
            )
            missing_vectors = (
                await provider.get_embeddings([text for _, _, text in missing])
                if missing
                else []
            )
            return query_vector, missing_vectors

        query_vector, missing_vectors = await asyncio.wait_for(
            embed_all(), timeout=self._SEMANTIC_RANK_TIMEOUT_SECONDS
        )
        save_rows = []
        for (kind, target_id, text), vector in zip(missing, missing_vectors):
            normalized = [float(value) for value in vector]
            if not normalized or not any(normalized) or not all(math.isfinite(value) for value in normalized):
                continue
            vectors[(kind, target_id)] = normalized
            save_rows.append(
                {
                    "target_type": kind,
                    "target_id": target_id,
                    "content_hash": self._meaning_hash(text),
                    "provider_id": provider_id,
                    "dimensions": len(normalized),
                    "vector_json": json.dumps(normalized, separators=(",", ":")),
                }
            )
        if save_rows:
            self._schedule_background(
                self._meaning_store_vectors(save_rows),
                label="语义记忆索引",
                key=f"meaning_vectors:{self._meaning_hash(json.dumps(save_rows, sort_keys=True))}",
                category="memory",
            )

        normalized_query = [float(value) for value in query_vector]
        return await asyncio.to_thread(
            self._score_vectors, normalized_query, groups, vectors, limits, minimum_similarity,
        )

    async def search_long_term_memories(
        self,
        query: str,
        *,
        lexical: list[Any],
        scopes: list[str],
        categories: list[str],
        limit: int,
    ) -> list[Any] | None:
        """在当前会话可见的全部有效记忆中召回，不受最近条数限制。"""
        if limit <= 0:
            return []
        if self._closed or self._meaning_embedding_provider() is None:
            return None
        candidates = await self._archive().list_recent_long_term_memories(
            scopes=scopes, categories=categories, limit=0,
        )
        seen: set[str] = set()
        by_source = {}
        for item in [*lexical, *candidates]:
            identity = str(getattr(item, "id", "") or "")
            if not identity or identity in seen:
                continue
            seen.add(identity)
            key = self._memory_source_key(item)
            previous = by_source.get(key)
            if previous is None or item.id > previous.id:
                by_source[key] = item
        unique = list(by_source.values())
        if not unique:
            return lexical
        groups = {
            "long_term_memory": [
                (
                    str(item.id),
                    self._memory_vector_text(item),
                    item,
                )
                for item in unique
            ]
        }
        try:
            ranked = await self._meaning_rank_with_embeddings(
                query, groups, {"long_term_memory": min(36, limit * 3)}, minimum_similarity=0.62, cached_only=True,
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 长期记忆向量检索降级：{type(exc).__name__}")
            return None
        lexical_versions = [by_source[self._memory_source_key(item)] for item in lexical]
        semantic = ranked["long_term_memory"]
        related = []
        reader = getattr(self._archive(), "get_related_long_term_memories", None)
        if callable(reader) and scopes:
            anchors = fuse_memory_routes([(1.0, lexical_versions), (1.0, semantic)], min(limit, 8))
            related = await reader(
                [item.id for item in anchors], scopes=scopes, categories=categories,
                limit=min(24, limit * 2),
            )
        return fuse_memory_routes(
            [(1.0, lexical_versions), (1.0, semantic), (0.35, related)], limit,
        )

    async def _meaning_rank_with_model(
        self,
        query: str,
        groups: dict[str, list[tuple[str, str, Any]]],
        limits: dict[str, int],
    ) -> dict[str, list[Any]] | None:
        if not str(query or "").strip():
            return None
        provider_id = self._memory_config().ranking_provider
        provider = await self._text_provider(provider_id)
        if not provider:
            return None
        candidates = {
            kind: [
                {"index": index, "content": text}
                for index, (_, text, _) in enumerate(values)
            ]
            for kind, values in groups.items()
        }
        fixed = f"""根据当前对话语义，从各类候选记忆中选择真正有助于理解和回复的条目。

JSON 输出要求：
{CORE_JSON_OUTPUT_RULES}

只输出 JSON：{{"selected":{{"类别":[候选 index]}}}}。
每类最多选择给定数量；没有相关内容就返回空数组。按整体语义、人物、时间和事件关系判断，不做字面词语匹配。"""
        dynamic = f"当前对话：{query}\n每类上限：{json.dumps(limits, ensure_ascii=False)}\n候选：{json.dumps(candidates, ensure_ascii=False)}"
        session_id = f"daily_life_memory_rank_{uuid.uuid4().hex[:8]}"
        try:
            raw = await asyncio.wait_for(
                self._call_text_model(
                    provider,
                    cache_friendly_prompt(fixed, dynamic),
                    session_id,
                    empty_retries=0,
                    primary_provider_id=provider_id,
                ),
                timeout=self._SEMANTIC_RANK_TIMEOUT_SECONDS,
            )
            payload = extract_json_from_text(raw)
            selected = payload.get("selected") if isinstance(payload, dict) else None
            if not isinstance(selected, dict):
                return None
            result: dict[str, list[Any]] = {}
            for kind, values in groups.items():
                limit = max(0, limits.get(kind, 0))
                if limit == 0:
                    result[kind] = []
                    continue
                indexes = selected.get(kind) if isinstance(selected.get(kind), list) else []
                valid: list[Any] = []
                seen: set[int] = set()
                for value in indexes:
                    try:
                        index = int(value)
                    except (TypeError, ValueError):
                        continue
                    if index in seen or index < 0 or index >= len(values):
                        continue
                    seen.add(index)
                    valid.append(values[index][2])
                    if len(valid) >= limit:
                        break
                result[kind] = valid
            return result
        finally:
            try:
                async with asyncio.timeout(self._SEMANTIC_SESSION_CLOSE_TIMEOUT_SECONDS):
                    await self._close_text_session(session_id)
            except Exception as exc:
                logger.debug(f"{LOG_PREFIX} 记忆排序会话清理未完成：{type(exc).__name__}")

    async def rank_semantic_groups(
        self,
        query: str,
        groups: dict[str, list[tuple[str, str, Any]]],
        limits: dict[str, int],
        *,
        optional_groups: set[str] | None = None,
        cached_only: bool = False,
    ) -> dict[str, list[Any]]:
        if self._closed:
            raise RuntimeError("语义检索服务已关闭")
        optional_groups = optional_groups or set()
        fallback = {
            kind: [] if kind in optional_groups else [item for _, _, item in values[: max(0, limits.get(kind, 0))]]
            for kind, values in groups.items()
        }
        try:
            # 阶段预算覆盖读取索引、查询向量和评分，而不只限制网络请求。
            async with asyncio.timeout(self._SEMANTIC_RANK_TIMEOUT_SECONDS):
                embedded = await self._meaning_rank_with_embeddings(
                    query, groups, limits,
                    minimum_similarity={kind: 0.62 for kind in optional_groups},
                    cached_only=cached_only,
                )
            if embedded is not None:
                return embedded
        except TimeoutError:
            logger.debug(
                f"{LOG_PREFIX} 向量记忆排序超时（预算 {self._SEMANTIC_RANK_TIMEOUT_SECONDS:g}s），"
                "本轮继续降级；共享查询仅缓存迟到结果"
            )
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 向量记忆排序跳过：{type(exc).__name__}: {exc}")
        if not bool(
            getattr(
                self._memory_config(),
                "semantic_ranking_model_enabled",
                False,
            )
        ):
            return fallback
        try:
            async with asyncio.timeout(self._SEMANTIC_RANK_TIMEOUT_SECONDS):
                modeled = await self._meaning_rank_with_model(query, groups, limits)
            return modeled if modeled is not None else fallback
        except TimeoutError:
            logger.debug(
                f"{LOG_PREFIX} 模型记忆排序超时（预算 {self._SEMANTIC_RANK_TIMEOUT_SECONDS:g}s），"
                "本轮使用已有上下文"
            )
            return fallback
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 模型记忆排序跳过：{type(exc).__name__}: {exc}")
            return fallback

    async def rank_embedding_groups(
        self,
        query: str,
        groups: dict[str, list[tuple[str, str, Any]]],
        limits: dict[str, int],
    ) -> dict[str, list[Any]]:
        """仅使用向量排序；不可用时保持来源顺序。

        自行负责最终模型决策的调用方使用此方法，避免准备候选项时额外消耗一次模型请求。
        """
        if self._closed:
            raise RuntimeError("语义检索服务已关闭")
        fallback = {
            kind: [item for _, _, item in values[: max(0, limits.get(kind, 0))]]
            for kind, values in groups.items()
        }
        try:
            embedded = await self._meaning_rank_with_embeddings(query, groups, limits)
            return embedded if embedded is not None else fallback
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 向量记忆排序跳过："
                f"{type(exc).__name__}: {exc}"
            )
            return fallback


__all__ = ["MeaningService"]
