from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelCallOptions:
    empty_retries: int = 1
    primary_provider_id: str = ""


class ModelGateway:
    def __init__(self, composer: Any):
        if composer is None:
            raise ValueError("模型网关需要可用的生活上下文编排器")
        self._composer = composer

    async def provider(self, provider_id: str = "") -> Any:
        return await self._composer._get_provider(provider_id)

    async def call(
        self,
        provider: Any,
        prompt: str,
        session_id: str,
        options: ModelCallOptions,
    ) -> str:
        return await self._composer._call_llm_text(
            provider,
            prompt,
            session_id,
            empty_retries=options.empty_retries,
            primary_provider_id=options.primary_provider_id,
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
