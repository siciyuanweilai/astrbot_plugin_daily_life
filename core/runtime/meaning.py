from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from typing import Any

from astrbot.api import logger

from ..life.tools import extract_json_from_text
from ..prompts import CORE_JSON_OUTPUT_RULES, cache_friendly_prompt
from .markers import LOG_PREFIX


class MeaningRuntimeMixin:
    _SEMANTIC_RANK_TIMEOUT_SECONDS = 4.0

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
        getter = getattr(getattr(self, "context", None), "get_all_embedding_providers", None)
        if not callable(getter):
            return None
        try:
            providers = list(getter() or [])
        except Exception:
            return None
        if not providers:
            return None
        configured_id = str(
            getattr(getattr(self.config, "memory", None), "embedding_provider", "")
            or ""
        ).strip()
        if configured_id:
            for provider in providers:
                if self._meaning_provider_id(provider) == configured_id:
                    return provider
        return providers[0]

    @staticmethod
    def _meaning_provider_id(provider: Any) -> str:
        try:
            meta = provider.meta()
            return str(getattr(meta, "id", "") or getattr(meta, "model", "") or "embedding")
        except Exception:
            return str(getattr(provider, "model_name", "") or "embedding")

    async def _meaning_store_vectors(self, rows: list[dict[str, Any]]) -> None:
        saver = getattr(self.archive, "upsert_memory_vectors", None)
        if callable(saver) and rows:
            await saver(rows)

    async def _meaning_rank_with_embeddings(
        self,
        query: str,
        groups: dict[str, list[tuple[str, str, Any]]],
        limits: dict[str, int],
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
        provider_id = self._meaning_provider_id(provider)
        cached_by_kind: dict[str, dict[str, dict[str, Any]]] = {}
        for kind, values in groups.items():
            getter = getattr(self.archive, "get_memory_vectors", None)
            cached_by_kind[kind] = (
                await getter(kind, [target_id for target_id, _, _ in values])
                if callable(getter)
                else {}
            )

        vectors: dict[tuple[str, str], list[float]] = {}
        missing: list[tuple[str, str, str]] = []
        for kind, target_id, text, _ in flat:
            cached = cached_by_kind.get(kind, {}).get(target_id) or {}
            if (
                cached.get("content_hash") == self._meaning_hash(text)
                and cached.get("provider_id") == provider_id
            ):
                try:
                    vector = [float(value) for value in json.loads(cached["vector_json"])]
                except (TypeError, ValueError, json.JSONDecodeError):
                    vector = []
                if vector:
                    vectors[(kind, target_id)] = vector
                    continue
            missing.append((kind, target_id, text))

        async def embed_all() -> tuple[list[float], list[list[float]]]:
            query_vector = await provider.get_embedding(query)
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
            if not normalized:
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
            scheduler = getattr(self, "_schedule_background_task", None)
            if callable(scheduler):
                scheduler(
                    self._meaning_store_vectors(save_rows),
                    label="语义记忆索引",
                    key=f"meaning_vectors:{self._meaning_hash(query)[:12]}",
                    category="memory",
                )
            else:
                await self._meaning_store_vectors(save_rows)

        ranked: dict[str, list[Any]] = {}
        normalized_query = [float(value) for value in query_vector]
        for kind, values in groups.items():
            scored = [
                (
                    self._meaning_cosine(normalized_query, vectors.get((kind, target_id), [])),
                    index,
                    item,
                )
                for index, (target_id, _, item) in enumerate(values)
            ]
            scored.sort(key=lambda row: (-row[0], row[1]))
            ranked[kind] = [item for _, _, item in scored[: max(0, limits.get(kind, 0))]]
        return ranked

    async def _meaning_rank_with_model(
        self,
        query: str,
        groups: dict[str, list[tuple[str, str, Any]]],
        limits: dict[str, int],
    ) -> dict[str, list[Any]] | None:
        getter = getattr(self, "_get_memory_provider", None)
        if not callable(getter) or not str(query or "").strip():
            return None
        provider = await getter()
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
        provider_id = str(getattr(self.config.memory, "provider", "") or "")
        try:
            raw = await asyncio.wait_for(
                self.call_text_model(
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
            await self.close_text_session(session_id)

    async def rank_semantic_groups(
        self,
        query: str,
        groups: dict[str, list[tuple[str, str, Any]]],
        limits: dict[str, int],
    ) -> dict[str, list[Any]]:
        fallback = {
            kind: [item for _, _, item in values[: max(0, limits.get(kind, 0))]]
            for kind, values in groups.items()
        }
        try:
            embedded = await self._meaning_rank_with_embeddings(query, groups, limits)
            if embedded is not None:
                return embedded
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 向量记忆排序跳过：{type(exc).__name__}: {exc}")
        if not bool(
            getattr(
                getattr(self.config, "memory", None),
                "semantic_ranking_model_enabled",
                False,
            )
        ):
            return fallback
        try:
            modeled = await self._meaning_rank_with_model(query, groups, limits)
            return modeled if modeled is not None else fallback
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


__all__ = ["MeaningRuntimeMixin"]
