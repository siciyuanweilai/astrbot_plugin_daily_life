from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelCallOptions:
    empty_retries: int = 1
    primary_provider_id: str = ""
    propagate_non_retryable: bool = False
    timeout_seconds: float | None = None


class ModelGateway:
    def __init__(self, composer: Any):
        if composer is None:
            raise ValueError("模型网关需要可用的生活上下文编排器")
        self._composer = composer

    async def provider(self, provider_id: str = "") -> Any:
        return await self._composer._get_provider(provider_id)

    @staticmethod
    def _provider_key(provider: Any) -> str:
        direct = str(getattr(provider, "provider_id", "") or "").strip()
        if direct:
            return direct
        meta = getattr(provider, "meta", None)
        if callable(meta):
            try:
                return str(getattr(meta(), "id", "") or "").strip()
            except Exception:
                return ""
        return ""

    async def provider_candidates(self, provider_id: str = "") -> AsyncIterator[Any]:
        """按指定模型、当前默认模型的顺序惰性返回去重候选。"""
        provider_id = str(provider_id or "").strip()
        candidates: list[Any] = []
        lookup_ids = [provider_id, ""] if provider_id else [""]
        for lookup_id in lookup_ids:
            try:
                provider = await self.provider(lookup_id)
            except Exception:
                provider = None
            provider_key = self._provider_key(provider) if provider is not None else ""
            if provider is not None and not any(
                provider is existing
                or (provider_key and provider_key == self._provider_key(existing))
                for existing in candidates
            ):
                candidates.append(provider)
                yield provider

    async def call(
        self,
        provider: Any,
        prompt: str,
        session_id: str,
        options: ModelCallOptions,
    ) -> str:
        kwargs = {
            "empty_retries": options.empty_retries,
            "primary_provider_id": options.primary_provider_id,
        }
        if options.propagate_non_retryable:
            kwargs["propagate_non_retryable"] = True
        if options.timeout_seconds is not None:
            kwargs["timeout_seconds"] = options.timeout_seconds
        return await self._composer._call_llm_text(
            provider,
            prompt,
            session_id,
            **kwargs,
        )

    async def close(self, session_id: str) -> None:
        await self._composer._cleanup_conversation(session_id)

    async def persona(self, scope: str = "") -> str:
        getter = getattr(self._composer, "_get_persona", None)
        if not callable(getter):
            return ""
        try:
            return await getter(scope)
        except TypeError:
            return await getter()


__all__ = ["ModelCallOptions", "ModelGateway"]
