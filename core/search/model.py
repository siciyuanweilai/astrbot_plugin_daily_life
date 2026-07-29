from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def normalize_providers(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    try:
        items = list(values or [])
    except TypeError:
        items = []
    result = []
    for value in items:
        provider = str(value or "").strip().lower()
        if provider == "grok-web":
            continue
        if provider and provider not in result:
            result.append(provider)
    order = {"grok-x": 0, "tavily": 1}
    return sorted(result, key=lambda item: (order.get(item, 99), item))


def provider_mode(values: Any) -> str:
    providers = normalize_providers(values)
    if not providers:
        return "none"
    families = {"tavily" if item == "tavily" else "grok" for item in providers}
    if len(families) > 1:
        return "mixed"
    if families == {"tavily"}:
        return "tavily"
    return "grok-x" if providers == ["grok-x"] else "grok"


def provider_label(values: Any) -> str:
    providers = normalize_providers(values)
    mode = provider_mode(providers)
    if mode == "mixed":
        return "Grok+Tavily"
    if mode == "tavily":
        return "Tavily"
    if mode == "grok-x":
        return "Grok X"
    if mode == "grok":
        return "Grok X"
    return "无"


@dataclass(slots=True)
class SearchSource:
    url: str
    title: str = ""
    snippet: str = ""
    provider: str = ""
    score: float | None = None
    images: list[dict[str, Any]] = field(default_factory=list)
    favicon: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider": self.provider,
        }
        if self.score is not None:
            payload["score"] = self.score
        if self.images:
            payload["images"] = list(self.images)
        if self.favicon:
            payload["favicon"] = self.favicon
        return payload


@dataclass(slots=True)
class SearchAnswer:
    content: str = ""
    sources: list[SearchSource] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    image_candidates: int = 0
    searched: bool = False
    search_calls: int = 0
    source_scope: str = "web"
    elapsed_ms: int = 0
    attempts: int = 0
    providers: list[str] = field(default_factory=list)
    invalid_reason: str = ""

    def effective_providers(self) -> list[str]:
        return normalize_providers(
            [*self.providers, *(item.provider for item in self.sources)]
        )


@dataclass(slots=True)
class SearchResult:
    status: str
    query: str
    content: str = ""
    sources: list[SearchSource] = field(default_factory=list)
    session_id: str = ""
    depth: str = "quick"
    source_scope: str = "web"
    start_date: str = ""
    end_date: str = ""
    error: str = ""
    cached: bool = False
    quality: str = "weak"
    missing_aspects: list[str] = field(default_factory=list)
    queries_executed: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    image_candidates: int = 0
    image_quality: str = "not_requested"
    image_missing: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def effective_providers(self) -> list[str]:
        return normalize_providers(
            [*self.providers, *(item.provider for item in self.sources)]
        )

    def as_dict(self) -> dict[str, Any]:
        providers = self.effective_providers()
        payload: dict[str, Any] = {
            "status": self.status,
            "query": self.query,
            "content": self.content,
            "sources": [item.as_dict() for item in self.sources],
            "sources_count": len(self.sources),
            "session_id": self.session_id,
            "depth": self.depth,
            "source_scope": self.source_scope,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "cached": self.cached,
            "quality": self.quality,
            "missing_aspects": list(self.missing_aspects),
            "queries_executed": list(self.queries_executed),
            "providers": providers,
            "provider_mode": provider_mode(providers),
            "images": list(self.images),
            "image_count": len(self.images),
            "image_candidates": self.image_candidates,
            "image_quality": self.image_quality,
            "image_missing": list(self.image_missing),
            **({"metadata": dict(self.metadata)} if self.metadata else {}),
        }
        if self.error:
            payload["error"] = self.error
        return payload
