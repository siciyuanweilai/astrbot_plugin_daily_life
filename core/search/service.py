from __future__ import annotations

import asyncio
import hashlib
import inspect
import ipaddress
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlparse

import aiohttp

from astrbot.api import logger

from ..clock import now as life_now
from ..config.options import SearchSettings
from .cache import SingleFlight, TimedCache
from .client import GrokClient, SearchRequestTimeout, TavilyClient, TavilyExtractError
from .evidence import (
    bounded_answer as _bounded_answer,
)
from .evidence import (
    compact as _compact,
)
from .evidence import (
    evidence_quality as _evidence_quality,
)
from .evidence import (
    has_search_evidence as _has_search_evidence,
)
from .evidence import (
    has_searched_content as _has_searched_content,
)
from .evidence import (
    merge_image_assets as _merge_image_assets,
)
from .evidence import (
    merge_sources as _merge_sources,
)
from .evidence import (
    missing_aspects as _missing_aspects,
)
from .evidence import (
    normalize_image_assets as _normalize_image_assets,
)
from .evidence import (
    query_key as _query_key,
)
from .evidence import (
    source_key as _source_key,
)
from .model import (
    SearchAnswer,
    SearchResult,
    SearchSource,
    normalize_providers,
    provider_label,
    provider_mode,
)
from .query import (
    SearchInput,
    SearchRequest,
    build_external_evidence_request,
    normalize_domains,
    normalize_topic,
    resolve_search_dates,
)
from .query import (
    normalize_source_scope as _normalize_source_scope,
)

LOG_PREFIX = "[日常生活]"
SEARCH_TOOL_NAMES = {
    "life_web_search",
    "life_web_fetch",
    "life_web_map",
    "life_web_crawl",
    "life_web_research",
    "life_web_research_status",
}
QUICK_TOOL_ANSWER_MAX_CHARS = 2000
QUICK_TOOL_SOURCE_LIMIT = 5
IMAGE_TOOL_LIMIT = 5
IMAGE_ENRICH_SOURCE_LIMIT = 2
DEEP_TOOL_ANSWER_MAX_CHARS = 6000
DEEP_TOOL_SOURCE_LIMIT = 8
RESEARCH_TOOL_MAX_CHARS = 12000
RESEARCH_TASK_TTL_SECONDS = 3600.0
RESEARCH_TASK_MAX_ITEMS = 128
TOOL_SOURCE_SNIPPET_MAX_CHARS = 400
TOOL_SESSION_TTL_SECONDS = 300.0
DEEP_PLANNING_TIMEOUT_SECONDS = 8.0
DEEP_MIN_FOLLOWUP_SECONDS = 20.0
WEEKDAY_NAMES = (
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期日",
)


@dataclass(slots=True)
class ToolSearchSession:
    session_id: str
    turn_id: str
    umo: str
    created_at: float
    remaining_seconds: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    signatures: dict[str, str] = field(default_factory=dict)
    strong_results: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ResearchTask:
    task_id: str
    request_id: str
    created_at: float
    umo: str = ""
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    finished_at: float = 0.0


class SearchService:
    def __init__(self, context: Any, settings: SearchSettings, task_store: Any = None):
        self.context = context
        self.settings = settings
        # 复用生活运行时已有的持久任务队列；没有队列时仍保持独立可测试。
        self.task_store = task_store
        self._session: aiohttp.ClientSession | None = None
        self._tavily_key_index = 0
        self._tavily_key_lock = asyncio.Lock()
        self._recent_tool_results: dict[str, tuple[float, str, str]] = {}
        self._tool_sessions: dict[str, ToolSearchSession] = {}
        self._cache = TimedCache[Any](
            max_items=settings.cache_max_items,
            ttl_seconds=settings.cache_ttl_seconds,
        )
        self._search_flight = SingleFlight[SearchResult]()
        self._fetch_flight = SingleFlight[dict[str, Any]]()
        self._research_tasks: dict[str, ResearchTask] = {}
        self._research_pollers: dict[str, asyncio.Task] = {}

    @staticmethod
    def _normalize_public_url(value: Any) -> str:
        """只允许公网 HTTP(S) 入口，避免把本地协议或空地址交给外部抓取服务。"""

        text = str(value or "").strip()
        try:
            parsed = urlparse(text)
            hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
        except ValueError:
            return ""
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        if parsed.username or parsed.password:
            return ""
        if not hostname or hostname == "localhost" or hostname.endswith(".local"):
            return ""
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private or address.is_loopback or address.is_link_local
        ):
            return ""
        return text

    @staticmethod
    def _bounded_positive(value: Any, default: int, upper: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(1, min(number, upper))

    async def restore_research_tasks(self) -> int:
        """从持久任务队列恢复未完成的研究任务。"""

        store = self.task_store
        getter = getattr(store, "get_durable_tasks", None)
        if not callable(getter):
            return 0
        try:
            records = await getter(kind="web_research", limit=RESEARCH_TASK_MAX_ITEMS)
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 恢复研究任务失败：{exc}")
            return 0
        restored = 0
        for record in records or []:
            payload = getattr(record, "payload", {})
            if not isinstance(payload, dict):
                continue
            task_id = str(payload.get("task_id") or "").strip()
            request_id = str(payload.get("request_id") or "").strip()
            if not task_id or not request_id:
                continue
            status = str(getattr(record, "status", "pending") or "pending").strip().lower()
            if status == "completed":
                task_status = "completed"
            elif status in {"failed", "cancelled", "dead"}:
                task_status = "failed"
            else:
                task_status = "pending"
            task = ResearchTask(
                task_id=task_id,
                request_id=request_id,
                created_at=time.monotonic(),
                umo=str(payload.get("umo") or "").strip(),
                status=task_status,
                result=(
                    dict((getattr(record, "result", {}) or {}).get("research") or {})
                    if isinstance(getattr(record, "result", {}), dict)
                    else {}
                ),
                error=str(getattr(record, "last_error", "") or "").strip(),
            )
            self._research_tasks[task_id] = task
            restored += 1
            if task_status == "pending":
                self._research_pollers[task_id] = asyncio.create_task(
                    self._poll_research_task(task_id, task.umo)
                )
        if restored:
            logger.info(f"{LOG_PREFIX} 已恢复研究任务：{restored} 个")
        return restored

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    async def search_external_evidence(
        self,
        query: str,
        *,
        category: str,
        umo: str = "",
    ) -> dict[str, Any]:
        """通过统一搜索策略向外部插件提供结构化证据。"""
        request = build_external_evidence_request(query, category)
        result = await self.search(
            request.query,
            depth="quick",
            source_scope="web",
            time_range=request.time_range,
            topic=request.topic,
            auto_parameters=True,
            umo=str(umo or "").strip(),
            trace_id=request.trace_id,
        )
        payload = result.as_dict()
        payload["category"] = request.category
        return payload

    @staticmethod
    def _tool_search_signature(
        query: str,
        depth: str,
        platform: str,
        source_scope: str,
        time_range: str,
        start_date: str,
        end_date: str,
        image_search: bool,
        image_understanding: bool,
        **options: Any,
    ) -> str:
        return json.dumps(
            {
                "query": _query_key(query),
                "depth": depth,
                "platform": platform,
                "source_scope": source_scope,
                "time_range": time_range,
                "start_date": start_date,
                "end_date": end_date,
                "image_search": image_search,
                "image_understanding": image_understanding,
                "options": options,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def _tool_search_context_signature(
        platform: str,
        source_scope: str,
        image_search: bool,
        image_understanding: bool,
        **options: Any,
    ) -> str:
        return json.dumps(
            {
                "platform": _query_key(platform),
                "source_scope": str(source_scope or "web").strip().casefold(),
                "image_search": bool(image_search),
                "image_understanding": bool(image_understanding),
                "options": options,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def _reused_strong_payload(payload: str, requested_query: str) -> str:
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return payload
        if not isinstance(data, dict):
            return payload
        data["reused"] = True
        data["requested_query"] = _compact(requested_query, 500)
        return json.dumps(data, ensure_ascii=False)

    def _recent_tool_result(self, umo: str, signature: str) -> str:
        if not umo:
            return ""
        value = self._recent_tool_results.get(umo)
        if value is None:
            return ""
        completed_at, previous_signature, result = value
        if time.monotonic() - completed_at > 1.0 or previous_signature != signature:
            self._recent_tool_results.pop(umo, None)
            return ""
        return result

    def _remember_tool_result(self, umo: str, signature: str, result: str) -> None:
        if umo:
            self._recent_tool_results[umo] = (time.monotonic(), signature, result)

    @staticmethod
    def _tool_source_payload(item: SearchSource) -> dict[str, Any]:
        payload = item.as_dict()
        payload["snippet"] = _bounded_answer(
            payload.get("snippet", ""), TOOL_SOURCE_SNIPPET_MAX_CHARS
        )
        return payload

    @staticmethod
    def _tool_search_guidance(result: SearchResult) -> str:
        quality = str(result.quality or "weak").strip().lower()
        missing = [
            str(item).strip() for item in result.missing_aspects if str(item).strip()
        ]
        if quality == "strong" and not missing:
            return "当前证据已充分：停止本轮搜索，不要重复查询同一问题。"
        if missing:
            return (
                "当前证据仍有缺口，仅针对这些缺口补充搜索："
                + "、".join(missing)
                + "。不要重复已经核实的内容。"
            )
        return "当前证据不足；如需继续，只补充尚未核实的方面，不要重复同一问题。"

    @staticmethod
    def _image_guidance(result: SearchResult) -> str:
        if result.image_quality == "not_requested":
            return "本次未请求图片，不要额外寻找图片。"
        if result.images:
            return "图片已准备好：下一步优先调用 send_message_to_user 发送图片，不要重复搜索同一内容。"
        return "当前没有可发送图片；如仍需图片，只读取少量高相关来源，不要声称已经找到图片。"

    @staticmethod
    def _image_state(
        *, requested: bool, images: list[dict[str, Any]], candidates: int
    ) -> tuple[str, list[str]]:
        if not requested:
            return "not_requested", []
        if len(images) >= 3:
            return "strong", []
        if images:
            return "partial", ["可用图片数量有限"]
        if candidates:
            return "weak", ["候选图片无法直接发送"]
        return "weak", ["没有可发送图片"]

    @classmethod
    def _quick_tool_payload(cls, result: SearchResult) -> dict[str, Any]:
        evidence = [
            cls._tool_source_payload(item)
            for item in result.sources[:QUICK_TOOL_SOURCE_LIMIT]
        ]
        providers = result.effective_providers()
        return {
            "status": result.status,
            "query": result.query,
            "answer": _bounded_answer(result.content, QUICK_TOOL_ANSWER_MAX_CHARS),
            "evidence": evidence,
            "evidence_count": len(evidence),
            "quality": result.quality,
            "missing_aspects": list(result.missing_aspects),
            "search_guidance": cls._tool_search_guidance(result),
            "images": list(result.images[:IMAGE_TOOL_LIMIT]),
            "image_count": len(result.images),
            "image_candidates": result.image_candidates,
            "image_quality": result.image_quality,
            "image_missing": list(result.image_missing),
            "image_guidance": cls._image_guidance(result),
            "queries_executed": list(result.queries_executed),
            "session_id": result.session_id,
            "depth": result.depth,
            "source_scope": result.source_scope,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "cached": result.cached,
            "providers": providers,
            "provider_mode": provider_mode(providers),
            **({"error": result.error} if result.error else {}),
        }

    @classmethod
    def _deep_tool_payload(cls, result: SearchResult) -> dict[str, Any]:
        sources = [
            cls._tool_source_payload(item)
            for item in result.sources[:DEEP_TOOL_SOURCE_LIMIT]
        ]
        providers = result.effective_providers()
        return {
            "status": result.status,
            "query": result.query,
            "content": _bounded_answer(result.content, DEEP_TOOL_ANSWER_MAX_CHARS),
            "sources": sources,
            "sources_count": len(sources),
            "quality": result.quality,
            "missing_aspects": list(result.missing_aspects),
            "search_guidance": cls._tool_search_guidance(result),
            "images": list(result.images[:IMAGE_TOOL_LIMIT]),
            "image_count": len(result.images),
            "image_candidates": result.image_candidates,
            "image_quality": result.image_quality,
            "image_missing": list(result.image_missing),
            "image_guidance": cls._image_guidance(result),
            "queries_executed": list(result.queries_executed),
            "session_id": result.session_id,
            "depth": result.depth,
            "source_scope": result.source_scope,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "cached": result.cached,
            "providers": providers,
            "provider_mode": provider_mode(providers),
            **({"error": result.error} if result.error else {}),
        }

    @classmethod
    def _tool_payload(cls, result: SearchResult) -> dict[str, Any]:
        return (
            cls._quick_tool_payload(result)
            if result.depth == "quick"
            else cls._deep_tool_payload(result)
        )

    def _prune_tool_sessions(self, now: float) -> None:
        stale = [
            key
            for key, session in self._tool_sessions.items()
            if now - session.created_at > TOOL_SESSION_TTL_SECONDS
        ]
        for key in stale:
            self._tool_sessions.pop(key, None)

    @staticmethod
    def _research_task_finished(task: ResearchTask) -> bool:
        return task.status in {"completed", "failed", "cancelled", "error", "timeout"}

    @staticmethod
    def _bounded_research_payload(value: Any) -> dict[str, Any]:
        """限制研究结果落库和回填长度，保留状态与引用字段。"""

        if not isinstance(value, dict):
            return {}
        payload = dict(value)
        for key in ("output", "content", "summary"):
            if isinstance(payload.get(key), str):
                payload[key] = payload[key][:RESEARCH_TOOL_MAX_CHARS]
        return payload

    def _prune_research_tasks(self, now: float) -> None:
        stale = [
            task_id
            for task_id, task in self._research_tasks.items()
            if self._research_task_finished(task)
            and now - (task.finished_at or task.created_at) > RESEARCH_TASK_TTL_SECONDS
        ]
        for task_id in stale:
            self._research_tasks.pop(task_id, None)

        overflow = len(self._research_tasks) - RESEARCH_TASK_MAX_ITEMS
        if overflow <= 0:
            return
        removable = sorted(
            (
                task
                for task in self._research_tasks.values()
                if self._research_task_finished(task)
                and task.task_id not in self._research_pollers
            ),
            key=lambda item: (item.finished_at or item.created_at, item.created_at),
        )
        for task in removable[:overflow]:
            self._research_tasks.pop(task.task_id, None)

    def _tool_session(self, turn_id: str, umo: str) -> ToolSearchSession:
        now = time.monotonic()
        self._prune_tool_sessions(now)
        normalized_turn = str(turn_id or "").strip()
        normalized_umo = str(umo or "").strip()
        key = f"{normalized_umo}\n{normalized_turn}"
        session = self._tool_sessions.get(key)
        if session is not None:
            return session
        session = ToolSearchSession(
            session_id=uuid.uuid4().hex[:12],
            turn_id=normalized_turn,
            umo=normalized_umo,
            created_at=now,
            remaining_seconds=float(self.settings.total_timeout_seconds),
        )
        self._tool_sessions[key] = session
        return session

    def _tavily_keys(self, umo: str = "") -> list[str]:
        del umo
        values = self.settings.tavily_api_keys
        result: list[str] = []
        for value in values:
            key = str(value or "").strip()
            if key and key not in result:
                result.append(key)
        return result

    def tavily_available(self, umo: str = "") -> bool:
        return bool(self.enabled and self._tavily_keys(umo))

    def prepare_tools(
        self,
        request: Any,
        builtin_names: list[str] | tuple[str, ...],
        *,
        umo: str = "",
    ) -> None:
        toolset = getattr(request, "func_tool", None)
        if toolset is None:
            return
        if self.enabled:
            for name in builtin_names:
                toolset.remove_tool(name)
        if not self.enabled:
            for name in SEARCH_TOOL_NAMES:
                toolset.remove_tool(name)
            return
        if not self.tavily_available(umo):
            toolset.remove_tool("life_web_fetch")
            toolset.remove_tool("life_web_map")
            toolset.remove_tool("life_web_crawl")
            toolset.remove_tool("life_web_research")
            toolset.remove_tool("life_web_research_status")

    async def close(self) -> None:
        pollers = list(self._research_pollers.values())
        self._research_pollers.clear()
        for task in pollers:
            task.cancel()
        if pollers:
            await asyncio.gather(*pollers, return_exceptions=True)
        self._research_tasks.clear()
        await self._search_flight.close()
        await self._fetch_flight.close()
        session = self._session
        self._session = None
        if session is not None and not session.closed:
            await session.close()
        await self._cache.clear()

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _provider(self):
        getter = getattr(self.context, "get_provider_by_id", None)
        if not callable(getter):
            raise RuntimeError("AstrBot 当前版本无法读取搜索模型")
        provider = getter(self.settings.provider)
        if inspect.isawaitable(provider):
            provider = await provider
        if provider is None:
            raise RuntimeError("搜索模型不存在或未启用")
        return provider

    async def _grok(self) -> GrokClient:
        provider = await self._provider()
        config = getattr(provider, "provider_config", None)
        if not isinstance(config, dict):
            raise RuntimeError("搜索模型不提供 OpenAI 兼容配置")
        api_base = str(config.get("api_base") or "").strip().rstrip("/")
        model = str(config.get("model") or "").strip()
        keys_getter = getattr(provider, "get_keys", None)
        keys = keys_getter() if callable(keys_getter) else config.get("key", [])
        if isinstance(keys, str):
            keys = [keys]
        api_key = next(
            (str(item).strip() for item in keys or [] if str(item).strip()), ""
        )
        if not api_base or not api_key or not model:
            raise RuntimeError("搜索模型缺少接口地址、密钥或模型名称")
        return GrokClient(
            await self._http(),
            api_base=api_base,
            api_key=api_key,
            model=model,
            timeout_seconds=self.settings.timeout_seconds,
        )

    async def _tavily(
        self, umo: str = "", *, timeout_seconds: int | float | None = None
    ) -> TavilyClient:
        keys = self._tavily_keys(umo)
        if not keys:
            raise RuntimeError("网页搜索未配置 Tavily API Key")
        async with self._tavily_key_lock:
            api_key = keys[self._tavily_key_index % len(keys)]
            self._tavily_key_index += 1
        return TavilyClient(
            await self._http(),
            api_key=api_key,
            timeout_seconds=timeout_seconds
            if timeout_seconds is not None
            else self.settings.fetch_timeout_seconds,
        )

    @staticmethod
    def _time_context() -> str:
        current = life_now().astimezone()
        return (
            f"当前时间：{current.isoformat(timespec='seconds')}；"
            f"{WEEKDAY_NAMES[current.weekday()]}；时区：{current.tzname() or '本地'}"
        )

    def _cache_key(
        self,
        query: str,
        depth: str,
        platform: str,
        *,
        source_scope: str,
        start_date: str,
        end_date: str,
        tavily_available: bool,
        image_search: bool,
        image_understanding: bool,
        topic: str = "general",
        include_raw_content: bool = False,
        include_images: bool = False,
        include_image_descriptions: bool = False,
        include_domains: tuple[str, ...] = (),
        exclude_domains: tuple[str, ...] = (),
        country: str = "",
        auto_parameters: bool = False,
        exact_match: bool = False,
    ) -> str:
        raw = json.dumps(
            {
                "provider": str(self.settings.provider or "").strip(),
                "query": _query_key(query),
                "depth": str(depth or "").strip().casefold(),
                "source_scope": str(source_scope or "").strip().casefold(),
                "platform": _query_key(platform),
                "start_date": str(start_date or "").strip(),
                "end_date": str(end_date or "").strip(),
                "tavily_available": bool(tavily_available),
                "image_search": bool(image_search),
                "image_understanding": bool(image_understanding),
                "topic": str(topic or "").strip().casefold(),
                "include_raw_content": bool(include_raw_content),
                "include_images": bool(include_images),
                "include_image_descriptions": bool(include_image_descriptions),
                "include_domains": list(include_domains),
                "exclude_domains": list(exclude_domains),
                "country": str(country or "").strip().casefold(),
                "auto_parameters": bool(auto_parameters),
                "exact_match": bool(exact_match),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _tavily_fallback(
        self,
        query: str,
        umo: str,
        *,
        source_scope: str,
        start_date: str,
        end_date: str,
        step_label: str = "",
        search_depth: str = "fast",
        topic: str = "general",
        time_range: str = "",
        include_raw_content: bool = False,
        include_images: bool = False,
        include_image_descriptions: bool = False,
        include_domains: tuple[str, ...] = (),
        exclude_domains: tuple[str, ...] = (),
        country: str = "",
        auto_parameters: bool = False,
        exact_match: bool = False,
        tavily_depth: str = "fast",
    ) -> SearchAnswer:
        if not self.tavily_available(umo):
            return SearchAnswer(source_scope=source_scope)
        started = time.monotonic()
        operation = f"Tavily {step_label}" if step_label else "Tavily 搜索"
        query_log = f"：查询={query[:80]}" if step_label else ""
        separator = " " if step_label else ""
        logger.debug(f"{LOG_PREFIX} {operation}{separator}开始{query_log}")
        try:
            if source_scope == "x":
                return SearchAnswer(source_scope=source_scope)
            answer = await (
                await self._tavily(umo, timeout_seconds=self.settings.timeout_seconds)
            ).search(
                query,
                self.settings.max_results,
                search_depth=search_depth,
                topic=topic,
                time_range=time_range,
                start_date=start_date,
                end_date=end_date,
                include_raw_content=include_raw_content,
                include_images=include_images,
                include_image_descriptions=include_image_descriptions,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                country=country,
                auto_parameters=auto_parameters,
                exact_match=exact_match,
            )
            answer.source_scope = source_scope
            if not answer.content and answer.sources:
                answer.content = "\n".join(
                    f"- {item.title or item.url}: {item.snippet}"
                    for item in answer.sources
                )
            image_log = (
                f"；图片候选={answer.image_candidates}；可用图片={len(answer.images)}"
                if include_images
                else ""
            )
            logger.debug(
                f"{LOG_PREFIX} {operation}{separator}完成："
                f"原始来源={len(answer.sources)}{image_log}；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
            return answer
        except asyncio.TimeoutError:
            logger.warning(
                f"{LOG_PREFIX} {operation}{separator}失败：原因=超时；"
                f"耗时={time.monotonic() - started:.2f} 秒；"
                f"上限={self.settings.timeout_seconds}秒"
            )
            return SearchAnswer(source_scope=source_scope)
        except asyncio.CancelledError:
            logger.debug(
                f"{LOG_PREFIX} {operation}{separator}已取消：已采用其他搜索来源；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
            raise
        except Exception as exc:
            logger.warning(
                f"{LOG_PREFIX} {operation}{separator}失败：原因={exc}；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
            return SearchAnswer(source_scope=source_scope)

    async def _grok_once(
        self,
        grok: GrokClient,
        query: str,
        *,
        source_scope: str,
        platform: str,
        start_date: str,
        end_date: str,
        image_search: bool,
        image_understanding: bool,
        step_label: str = "",
    ) -> SearchAnswer:
        operation = f"Grok X {step_label}" if step_label else "Grok X 搜索"
        result_operation = operation
        started = time.monotonic()
        query_log = f"：查询={query[:80]}" if step_label else ""
        separator = " " if step_label else ""
        if str(source_scope or "").strip().lower() != "x":
            return SearchAnswer(
                source_scope="x",
                invalid_reason="Grok 仅支持 X 平台原生搜索",
            )
        logger.debug(f"{LOG_PREFIX} {operation}{separator}开始{query_log}")
        try:
            answer = await grok.search(
                query,
                source_scope=source_scope,
                platform=platform,
                time_context=self._time_context(),
                start_date=start_date,
                end_date=end_date,
                image_search=image_search,
                image_understanding=image_understanding,
            )
            if answer.content and answer.searched:
                logger.debug(
                    f"{LOG_PREFIX} {result_operation}{separator}完成："
                    f"搜索调用={answer.search_calls}；来源={len(answer.sources)}；"
                    f"耗时={answer.elapsed_ms / 1000:.2f} 秒；尝试={answer.attempts}"
                )
                return answer
            reason = answer.invalid_reason or "没有可用正文或搜索依据"
            logger.debug(
                f"{LOG_PREFIX} {result_operation}{separator}未形成有效搜索结果："
                f"原因={reason}；耗时={time.monotonic() - started:.2f} 秒"
            )
        except SearchRequestTimeout:
            logger.warning(
                f"{LOG_PREFIX} {result_operation}{separator}失败：原因=超时；"
                f"耗时={time.monotonic() - started:.2f} 秒；"
                f"上限={grok.timeout_seconds}秒"
            )
        except asyncio.CancelledError:
            logger.debug(
                f"{LOG_PREFIX} {operation}{separator}已取消：已采用其他搜索来源；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
            raise
        except Exception as exc:
            logger.warning(
                f"{LOG_PREFIX} {result_operation}{separator}失败：原因={exc}；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
        return SearchAnswer(source_scope=source_scope)

    def _merge_provider_answers(
        self,
        *answers: tuple[str, SearchAnswer],
        source_scope: str,
    ) -> SearchAnswer:
        valid = [
            (label, answer) for label, answer in answers if _has_search_evidence(answer)
        ]
        if not valid:
            valid = [
                (label, answer)
                for label, answer in answers
                if _has_searched_content(answer)
            ]
        if not valid:
            return SearchAnswer(source_scope=source_scope)
        if len(valid) == 1:
            return valid[0][1]

        sections: list[str] = []
        seen_content: set[str] = set()
        for label, answer in valid:
            content_key = _query_key(answer.content)
            if not content_key or content_key in seen_content:
                continue
            seen_content.add(content_key)
            sections.append(f"{label}：\n{answer.content}" if label else answer.content)
        return SearchAnswer(
            content="\n\n".join(sections),
            sources=_merge_sources(
                *(answer.sources for _label, answer in valid),
                limit=self.settings.max_sources,
            ),
            images=_merge_image_assets(
                *(answer.images for _label, answer in valid),
                limit=20,
            ),
            image_candidates=sum(answer.image_candidates for _label, answer in valid),
            searched=True,
            search_calls=sum(answer.search_calls for _label, answer in valid),
            source_scope=source_scope,
            elapsed_ms=max(answer.elapsed_ms for _label, answer in valid),
            attempts=sum(answer.attempts for _label, answer in valid),
            providers=normalize_providers(
                [
                    provider
                    for _label, answer in valid
                    for provider in answer.effective_providers()
                ]
            ),
        )

    @staticmethod
    async def _within_search_budget(operation: Any, deadline: float | None) -> Any:
        if deadline is None:
            return await operation
        remaining = deadline - time.monotonic()
        if remaining > 0:
            return await asyncio.wait_for(operation, timeout=remaining)
        cancel = getattr(operation, "cancel", None)
        if callable(cancel):
            cancel()
        else:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
        raise asyncio.TimeoutError

    async def _single_search(
        self,
        grok: GrokClient | None,
        query: str,
        *,
        source_scope: str,
        platform: str,
        start_date: str,
        end_date: str,
        umo: str,
        image_search: bool,
        image_understanding: bool,
        deadline: float | None = None,
        step_label: str = "",
        topic: str = "general",
        time_range: str = "",
        include_raw_content: bool = False,
        include_images: bool = False,
        include_image_descriptions: bool = False,
        include_domains: tuple[str, ...] = (),
        exclude_domains: tuple[str, ...] = (),
        country: str = "",
        auto_parameters: bool = False,
        exact_match: bool = False,
        tavily_depth: str = "fast",
    ) -> SearchAnswer:
        tavily_operation = None
        if source_scope in {"web", "both"}:
            tavily_operation = self._tavily_fallback(
                query,
                umo,
                source_scope="web",
                start_date=start_date,
                end_date=end_date,
                step_label=step_label,
                search_depth=tavily_depth,
                topic=topic,
                time_range=time_range,
                include_raw_content=include_raw_content,
                include_images=include_images,
                include_image_descriptions=include_image_descriptions,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                country=country,
                auto_parameters=auto_parameters,
                exact_match=exact_match,
            )
        if source_scope == "web":
            assert tavily_operation is not None
            return await self._within_search_budget(tavily_operation, deadline)
        if source_scope == "x":
            if grok is None:
                return SearchAnswer(source_scope="x")
            return await self._within_search_budget(
                self._grok_once(
                    grok,
                    query,
                    source_scope="x",
                    platform=platform,
                    start_date=start_date,
                    end_date=end_date,
                    image_search=False,
                    image_understanding=image_understanding,
                    step_label=step_label,
                ),
                deadline,
            )
        if grok is None:
            assert tavily_operation is not None
            return await self._within_search_budget(tavily_operation, deadline)
        assert tavily_operation is not None
        web, x = await self._within_search_budget(
            asyncio.gather(
                tavily_operation,
                self._grok_once(
                    grok,
                    query,
                    source_scope="x",
                    platform=platform,
                    start_date=start_date,
                    end_date=end_date,
                    image_search=False,
                    image_understanding=image_understanding,
                    step_label=(f"{step_label}（X）" if step_label else ""),
                ),
            ),
            deadline,
        )
        return self._merge_provider_answers(
            ("Tavily 搜索结果", web),
            ("Grok X 搜索结果", x),
            source_scope="both",
        )

    def _normalize_search_request(self, raw: SearchInput) -> SearchRequest:
        normalized_start, normalized_end = resolve_search_dates(
            raw.time_range,
            raw.start_date,
            raw.end_date,
            today=life_now().astimezone().date(),
        )
        return SearchRequest(
            query=_compact(raw.query),
            depth="deep" if str(raw.depth or "").strip().lower() == "deep" else "quick",
            source_scope=_normalize_source_scope(raw.source_scope),
            platform=_compact(raw.platform, 120),
            start_date=normalized_start,
            end_date=normalized_end,
            image_search=bool(raw.image_search),
            image_understanding=bool(raw.image_understanding),
            umo=str(raw.umo or ""),
            topic=normalize_topic(getattr(raw, "topic", "general")),
            include_raw_content=bool(getattr(raw, "include_raw_content", False)),
            include_images=bool(
                getattr(raw, "include_images", False) or raw.image_search
            ),
            include_image_descriptions=bool(
                getattr(raw, "include_image_descriptions", False)
            ),
            include_domains=normalize_domains(getattr(raw, "include_domains", ()), 300),
            exclude_domains=normalize_domains(getattr(raw, "exclude_domains", ()), 150),
            country=_compact(getattr(raw, "country", ""), 80).lower(),
            auto_parameters=bool(getattr(raw, "auto_parameters", False)),
            exact_match=bool(getattr(raw, "exact_match", False)),
        )

    @staticmethod
    def _search_error(
        request: SearchRequest,
        error: str,
        status: str = "error",
    ) -> SearchResult:
        image_requested = bool(request.include_images)
        return SearchResult(
            status=status,
            query=request.query,
            depth=request.depth,
            source_scope=request.source_scope,
            start_date=request.start_date,
            end_date=request.end_date,
            error=error,
            image_quality="weak" if image_requested else "not_requested",
            image_missing=["图片搜索未完成"] if image_requested else [],
        )

    async def _search_grok_client(self) -> GrokClient | None:
        try:
            return await self._grok()
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} Grok X 搜索初始化失败：{exc}")
            return None

    async def _search_once_request(
        self,
        grok: GrokClient | None,
        query: str,
        request: SearchRequest,
        *,
        deadline: float | None = None,
        step_label: str = "",
    ) -> SearchAnswer:
        return await self._single_search(
            grok,
            query,
            source_scope=request.source_scope,
            platform=request.platform,
            start_date=request.start_date,
            end_date=request.end_date,
            umo=request.umo,
            image_search=request.image_search,
            image_understanding=request.image_understanding,
            topic=request.topic,
            time_range="",
            include_raw_content=request.include_raw_content,
            include_images=request.include_images,
            include_image_descriptions=request.include_image_descriptions,
            include_domains=request.include_domains,
            exclude_domains=request.exclude_domains,
            country=request.country,
            auto_parameters=request.auto_parameters,
            exact_match=request.exact_match,
            tavily_depth="advanced" if request.depth == "deep" else "fast",
            deadline=deadline,
            step_label=step_label,
        )

    async def _plan_deep_followups(
        self,
        grok: GrokClient,
        request: SearchRequest,
        findings: str,
        *,
        limit: int,
        deadline: float,
    ) -> list[str]:
        remaining = deadline - time.monotonic()
        if remaining < DEEP_MIN_FOLLOWUP_SECONDS:
            logger.debug(
                f"{LOG_PREFIX} 深度搜索结束：剩余时间不足以执行补充搜索；"
                f"剩余={max(0.0, remaining):.2f} 秒"
            )
            return []
        try:
            return await asyncio.wait_for(
                grok.plan(request.query, findings, limit=limit),
                timeout=min(remaining, DEEP_PLANNING_TIMEOUT_SECONDS),
            )
        except asyncio.TimeoutError:
            logger.debug(f"{LOG_PREFIX} 深度搜索补充查询规划超时，保留已有结果")
        except Exception as exc:
            logger.debug(f"{LOG_PREFIX} 深度搜索追问生成失败，保留已有结果：{exc}")
        return []

    @staticmethod
    def _new_deep_followups(
        followups: list[str], searched_queries: set[str], limit: int
    ) -> list[str]:
        pending: list[str] = []
        for followup in followups:
            key = _query_key(followup)
            if not key or key in searched_queries:
                continue
            searched_queries.add(key)
            pending.append(followup)
            if len(pending) >= limit:
                break
        return pending

    async def _execute_deep_followups(
        self,
        grok: GrokClient,
        request: SearchRequest,
        indexed: list[tuple[int, str]],
        *,
        deadline: float,
        max_followups: int,
    ) -> tuple[list[tuple[str, SearchAnswer]], bool]:
        tasks = [
            asyncio.create_task(
                self._search_once_request(
                    grok,
                    followup,
                    request,
                    deadline=deadline,
                    step_label=f"补充搜索 {index}/{max_followups}",
                )
            )
            for index, followup in indexed
        ]
        done, unfinished = await asyncio.wait(
            tasks, timeout=max(0.0, deadline - time.monotonic())
        )
        completed: list[tuple[str, SearchAnswer]] = []
        for (_index, followup), task in zip(indexed, tasks):
            if task not in done:
                continue
            try:
                completed.append((followup, task.result()))
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.debug(
                    f"{LOG_PREFIX} 深度搜索补充查询失败，保留其他结果：{exc}"
                )
        if unfinished:
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*unfinished, return_exceptions=True)
            logger.debug(
                f"{LOG_PREFIX} 深度搜索达到总时间限制，"
                f"保留已完成补充结果={len(completed)}"
            )
        return completed, bool(unfinished)

    def _merge_deep_followup_results(
        self,
        completed: list[tuple[str, SearchAnswer]],
        sources: list[SearchSource],
        images: list[dict[str, Any]],
        providers_used: set[str] | None,
    ) -> tuple[list[str], list[SearchSource], list[dict[str, Any]], int, int]:
        lines = [
            f"{followup}\n{answer.content}"
            for followup, answer in completed
            if answer.content
        ]
        previous_source_count = len(sources)
        previous_image_count = len(images)
        for _followup, answer in completed:
            if answer.content and providers_used is not None:
                providers_used.update(answer.effective_providers())
            sources = _merge_sources(
                sources, answer.sources, limit=self.settings.max_sources
            )
            images = _merge_image_assets(images, answer.images, limit=20)
        return (
            lines,
            sources,
            images,
            len(sources) - previous_source_count,
            len(images) - previous_image_count,
        )

    @staticmethod
    def _deep_followups_needed(
        findings: str, sources: list[SearchSource], max_followups: int
    ) -> bool:
        if max_followups <= 0:
            logger.debug(f"{LOG_PREFIX} 深度搜索结束：补充搜索数量为 0")
            return False
        if _evidence_quality(findings, sources) == "strong":
            logger.debug(
                f"{LOG_PREFIX} 深度搜索结束：首轮来源充分，来源={len(sources)}"
            )
            return False
        return True

    @staticmethod
    def _deep_round_should_stop(
        *,
        timed_out: bool,
        new_source_count: int,
        findings: str,
        sources: list[SearchSource],
    ) -> bool:
        if timed_out:
            return True
        if new_source_count <= 0:
            logger.debug(f"{LOG_PREFIX} 深度搜索结束：没有新增来源")
            return True
        if _evidence_quality(findings, sources) == "strong":
            logger.debug(f"{LOG_PREFIX} 深度搜索结束：已有来源充分")
            return True
        return False

    async def _deep_search_sections(
        self,
        grok: GrokClient,
        request: SearchRequest,
        findings: str,
        sources: list[SearchSource],
        images: list[dict[str, Any]] | None = None,
        image_candidates: list[int] | None = None,
        *,
        deadline: float,
        queries_executed: list[str] | None = None,
        providers_used: set[str] | None = None,
    ) -> tuple[list[str], list[SearchSource]]:
        sections: list[str] = []
        image_bucket = images if images is not None else []
        searched_queries = {_query_key(request.query)}
        followup_count = 0
        max_followups = self.settings.deep_max_followups
        if not self._deep_followups_needed(findings, sources, max_followups):
            return sections, sources
        for round_index in range(1, max_followups + 1):
            remaining_slots = max_followups - followup_count
            if remaining_slots <= 0:
                break
            followups = await self._plan_deep_followups(
                grok,
                request,
                findings,
                limit=remaining_slots,
                deadline=deadline,
            )
            pending = self._new_deep_followups(
                followups, searched_queries, remaining_slots
            )
            if not pending:
                logger.debug(f"{LOG_PREFIX} 深度搜索结束：没有新的追问")
                break
            remaining = deadline - time.monotonic()
            if remaining < DEEP_MIN_FOLLOWUP_SECONDS:
                logger.debug(
                    f"{LOG_PREFIX} 深度搜索结束：规划完成后剩余时间不足；"
                    f"剩余={max(0.0, remaining):.2f} 秒"
                )
                break

            indexed = [
                (followup_count + index, followup)
                for index, followup in enumerate(pending, start=1)
            ]
            if queries_executed is not None:
                queries_executed.extend(followup for _index, followup in indexed)
            followup_count += len(indexed)
            completed, timed_out = await self._execute_deep_followups(
                grok,
                request,
                indexed,
                deadline=deadline,
                max_followups=max_followups,
            )
            (
                round_lines,
                sources,
                image_bucket,
                new_source_count,
                new_image_count,
            ) = self._merge_deep_followup_results(
                completed, sources, image_bucket, providers_used
            )
            if image_candidates is not None:
                image_candidates[0] += sum(
                    answer.image_candidates for _followup, answer in completed
                )
            if not round_lines:
                logger.debug(f"{LOG_PREFIX} 深度搜索结束：没有新增有效内容")
                break
            section = "\n\n".join(round_lines)
            sections.append(section)
            findings = f"{findings}\n\n{section}"[-6000:]
            logger.debug(
                f"{LOG_PREFIX} 深度搜索完成第 {round_index + 1} 轮："
                f"查询={len(indexed)}；完成={len(completed)}；"
                f"新增来源={new_source_count}；新增图片={new_image_count}"
            )
            if self._deep_round_should_stop(
                timed_out=timed_out,
                new_source_count=new_source_count,
                findings=findings,
                sources=sources,
            ):
                break
        if images is not None:
            images[:] = image_bucket
        return sections, sources

    async def _enrich_images(
        self,
        request: SearchRequest,
        sources: list[SearchSource],
        images: list[dict[str, Any]],
        *,
        deadline: float,
    ) -> tuple[list[dict[str, Any]], int]:
        if not request.include_images or images or request.source_scope == "x":
            return images, 0
        urls = [item.url for item in sources if item.provider == "tavily" and item.url][
            :IMAGE_ENRICH_SOURCE_LIMIT
        ]
        if not urls:
            return images, 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return images, 0
        started = time.monotonic()
        timeout = min(self.settings.fetch_timeout_seconds, max(1.0, remaining))
        try:
            data = await asyncio.wait_for(
                (
                    await self._tavily(
                        request.umo,
                        timeout_seconds=timeout,
                    )
                ).extract(
                    urls,
                    query=request.query,
                    chunks_per_source=3,
                    extract_depth="advanced",
                    include_images=True,
                    format="markdown",
                    timeout=timeout,
                ),
                timeout=remaining,
            )
            candidates = 0
            enriched: list[dict[str, Any]] = []
            for item in data.get("results") or []:
                if not isinstance(item, dict):
                    continue
                raw_images = item.get("images") or []
                candidates += len(raw_images)
                enriched = _merge_image_assets(
                    enriched,
                    _normalize_image_assets(
                        raw_images,
                        source_url=str(item.get("url") or ""),
                    ),
                    limit=20,
                )
            logger.debug(
                f"{LOG_PREFIX} 图片页面提取完成：页面={len(urls)}；"
                f"候选={candidates}；新增可用={len(enriched)}；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
            return _merge_image_assets(images, enriched, limit=20), candidates
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                f"{LOG_PREFIX} 图片页面提取跳过：原因={_compact(str(exc), 300)}；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
            return images, 0

    async def _execute_search(
        self,
        request: SearchRequest,
        grok: GrokClient | None,
        *,
        deadline: float,
        session_id: str = "",
    ) -> SearchResult:
        queries_executed = [request.query]
        first = await self._search_once_request(
            grok, request.query, request, deadline=deadline
        )
        sections = [first.content] if first.content else []
        sources = list(first.sources)
        images = list(first.images)
        image_candidate_total = [first.image_candidates]
        providers_used = set(first.effective_providers() if first.content else [])
        if request.depth == "deep" and first.content and grok is not None:
            extra, sources = await self._deep_search_sections(
                grok,
                request,
                first.content,
                sources,
                images=images,
                image_candidates=image_candidate_total,
                deadline=deadline,
                queries_executed=queries_executed,
                providers_used=providers_used,
            )
            sections.extend(extra)
        content = "\n\n".join(item for item in sections if item).strip()
        if not content:
            raise RuntimeError("Tavily 与 Grok X 均未返回可用搜索结果")
        if request.include_images:
            images, enriched_candidates = await self._enrich_images(
                request,
                sources,
                images,
                deadline=deadline,
            )
            image_candidate_total[0] += enriched_candidates
        image_quality, image_missing = self._image_state(
            requested=request.include_images,
            images=images,
            candidates=image_candidate_total[0],
        )
        quality = _evidence_quality(content, sources)
        return SearchResult(
            status="ok",
            query=request.query,
            content=content,
            sources=sources[: self.settings.max_sources],
            session_id=session_id or uuid.uuid4().hex[:12],
            depth=request.depth,
            source_scope=request.source_scope,
            start_date=request.start_date,
            end_date=request.end_date,
            quality=quality,
            missing_aspects=_missing_aspects(quality),
            queries_executed=queries_executed,
            providers=normalize_providers(
                [*providers_used, *(item.provider for item in sources)]
            ),
            images=images,
            image_candidates=image_candidate_total[0],
            image_quality=image_quality,
            image_missing=image_missing,
        )

    async def _run_search_request(
        self,
        request: SearchRequest,
        *,
        cache_key: str,
        deadline: float,
        trace_id: str = "",
    ) -> SearchResult:
        started = time.monotonic()
        trace_text = f"任务={trace_id}；" if trace_id else ""
        logger.info(
            f"{LOG_PREFIX} 联网搜索开始：{trace_text}深度={request.depth}；"
            f"范围={request.source_scope}；总时间限制={self.settings.total_timeout_seconds}秒"
        )
        logger.debug(
            f"{LOG_PREFIX} 联网搜索查询：{trace_text}内容={_compact(request.query, 80)}"
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        grok = None
        if request.source_scope in {"x", "both"} or request.depth == "deep":
            grok = await asyncio.wait_for(
                self._search_grok_client(),
                timeout=remaining,
            )
        result = await self._execute_search(
            request,
            grok,
            deadline=deadline,
        )
        reusable = replace(result, cached=False, session_id="")
        if self.settings.cache_ttl_seconds and reusable.status == "ok":
            await self._cache.set(cache_key, reusable)
        date_log = (
            f"；时间={request.start_date or '不限'}~{request.end_date or '不限'}"
            if request.start_date or request.end_date
            else ""
        )
        image_log = (
            f"；可用图片={len(result.images)}" if request.include_images else ""
        )
        logger.info(
            f"{LOG_PREFIX} 联网搜索完成：{trace_text}"
            f"引擎={provider_label(result.effective_providers())}；"
            f"采用来源={len(result.sources)}{image_log}{date_log}；"
            f"总耗时={time.monotonic() - started:.2f} 秒"
        )
        return reusable

    async def search(
        self,
        query: str,
        *,
        depth: str = "quick",
        source_scope: str = "web",
        platform: str = "",
        time_range: str = "",
        start_date: str = "",
        end_date: str = "",
        image_search: bool = False,
        image_understanding: bool = False,
        topic: str = "general",
        include_raw_content: bool = False,
        include_images: bool = False,
        include_image_descriptions: bool = False,
        include_domains: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        country: str = "",
        auto_parameters: bool = False,
        exact_match: bool = False,
        umo: str = "",
        trace_id: str = "",
        deadline: float | None = None,
        session_id: str = "",
    ) -> SearchResult:
        try:
            request = self._normalize_search_request(
                SearchInput(
                    query=query,
                    depth=depth,
                    source_scope=source_scope,
                    platform=platform,
                    time_range=time_range,
                    start_date=start_date,
                    end_date=end_date,
                    image_search=image_search,
                    image_understanding=image_understanding,
                    topic=topic,
                    include_raw_content=include_raw_content,
                    include_images=include_images,
                    include_image_descriptions=include_image_descriptions,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                    country=country,
                    auto_parameters=auto_parameters,
                    exact_match=exact_match,
                    umo=umo,
                )
            )
        except ValueError as exc:
            return SearchResult(
                status="error",
                query=_compact(query),
                depth="deep" if str(depth).lower() == "deep" else "quick",
                error=str(exc),
            )
        if not self.enabled:
            return replace(
                self._search_error(
                    request, "联网搜索未启用或未选择搜索模型", status="disabled"
                ),
                session_id=session_id,
                queries_executed=[request.query] if request.query else [],
            )
        if not request.query:
            return replace(
                self._search_error(request, "搜索内容不能为空"),
                session_id=session_id,
            )

        cache_key = self._cache_key(
            request.query,
            request.depth,
            request.platform,
            source_scope=request.source_scope,
            start_date=request.start_date,
            end_date=request.end_date,
            tavily_available=self.tavily_available(request.umo),
            image_search=request.image_search,
            image_understanding=request.image_understanding,
            topic=request.topic,
            include_raw_content=request.include_raw_content,
            include_images=request.include_images,
            include_image_descriptions=request.include_image_descriptions,
            include_domains=request.include_domains,
            exclude_domains=request.exclude_domains,
            country=request.country,
            auto_parameters=request.auto_parameters,
            exact_match=request.exact_match,
        )
        namespaced_cache_key = f"search:{cache_key}"
        cached = (
            await self._cache.get(namespaced_cache_key)
            if self.settings.cache_ttl_seconds
            else None
        )
        if isinstance(cached, SearchResult):
            return replace(
                cached,
                cached=True,
                session_id=session_id or uuid.uuid4().hex[:12],
            )

        started = time.monotonic()
        deadline = deadline or (started + self.settings.total_timeout_seconds)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            result, shared = await asyncio.wait_for(
                self._search_flight.run(
                    cache_key,
                    lambda: self._run_search_request(
                        request,
                        cache_key=namespaced_cache_key,
                        deadline=deadline,
                        trace_id=trace_id,
                    ),
                ),
                timeout=remaining,
            )
            return replace(
                result,
                cached=shared,
                session_id=session_id or uuid.uuid4().hex[:12],
            )
        except asyncio.TimeoutError:
            error = f"联网搜索超过总时间限制（{self.settings.total_timeout_seconds}秒）"
            logger.warning(f"{LOG_PREFIX} {error}")
            return replace(
                self._search_error(request, error),
                session_id=session_id,
                queries_executed=[request.query],
            )
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 联网搜索失败：{exc}")
            return replace(
                self._search_error(request, str(exc)),
                session_id=session_id,
                queries_executed=[request.query],
            )

    async def fetch(self, url: str, *, umo: str = "") -> dict[str, Any]:
        raw_url = str(url or "").strip()
        url = self._normalize_public_url(raw_url)
        if not url:
            return {
                "status": "error",
                "url": raw_url,
                "mode": "page_extract",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "网页地址必须是公网 HTTP 或 HTTPS 地址",
            }
        if not self.tavily_available(umo):
            return {
                "status": "disabled",
                "url": url,
                "mode": "page_extract",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "网页读取需要启用联网搜索并配置 Tavily",
            }
        key = hashlib.sha256(f"fetch\n{_source_key(url)}".encode()).hexdigest()
        namespaced_cache_key = f"page:{key}"
        cached = (
            await self._cache.get(namespaced_cache_key)
            if self.settings.cache_ttl_seconds
            else None
        )
        if isinstance(cached, dict):
            return {
                **cached,
                "status": "ok",
                "url": url,
                "cached": True,
            }
        try:
            result, shared = await asyncio.wait_for(
                self._fetch_flight.run(
                    key,
                    lambda: self._fetch_uncached(
                        url,
                        umo=umo,
                        cache_key=namespaced_cache_key,
                    ),
                ),
                timeout=self.settings.total_timeout_seconds + 0.5,
            )
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "url": url,
                "mode": "page_extract",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "网页读取超过总时间限制",
            }
        return {
            **result,
            "url": url,
            "cached": bool(shared and result.get("status") == "ok"),
        }

    async def _fetch_uncached(
        self,
        url: str,
        *,
        umo: str,
        cache_key: str,
    ) -> dict[str, Any]:
        started = time.monotonic()
        deadline = started + self.settings.total_timeout_seconds
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            content = await asyncio.wait_for(
                (await self._tavily(umo)).fetch(url), timeout=remaining
            )
            content = content[: self.settings.max_page_chars]
            payload = {
                "mode": "page_extract",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "content": content,
            }
            if self.settings.cache_ttl_seconds:
                await self._cache.set(cache_key, payload)
            logger.debug(
                f"{LOG_PREFIX} 网页读取完成：方式=正文提取；引擎=Tavily；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
            return {"status": "ok", "url": url, **payload, "cached": False}
        except Exception as exc:
            extract_error = _compact(str(exc), 500) or "Tavily 网页正文提取失败"
            failed_results = (
                list(exc.failed_results) if isinstance(exc, TavilyExtractError) else []
            )
            logger.warning(
                f"{LOG_PREFIX} 网页读取失败：Tavily；地址={_compact(url, 160)}；"
                f"原因={extract_error}；"
                "改用网址定向搜索"
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "status": "error",
                "url": url,
                "mode": "search_fallback",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": extract_error,
                "failed_results": failed_results,
                "fallback_error": "网页读取总时间限制已用尽",
            }

        fallback_query = (
            "读取并核对这个网页的标题、作者、发布时间和主要正文内容；"
            f"如果无法直接读取，查找同一页面或同一事件的可靠资料：{url}"
        )
        fallback = await self.search(
            fallback_query,
            depth="quick",
            source_scope="web",
            umo=umo,
            deadline=deadline,
        )
        if fallback.status == "ok" and fallback.content:
            providers = fallback.effective_providers()
            mode = provider_mode(providers)
            payload = {
                "mode": "search_fallback",
                "provider": providers[0] if len(providers) == 1 else mode,
                "providers": providers,
                "provider_mode": mode,
                "content": fallback.content[: self.settings.max_page_chars],
                "sources": [
                    self._tool_source_payload(item)
                    for item in fallback.sources[:QUICK_TOOL_SOURCE_LIMIT]
                ],
                "extract_error": extract_error,
                "failed_results": failed_results,
            }
            if self.settings.cache_ttl_seconds:
                await self._cache.set(cache_key, payload)
            logger.debug(
                f"{LOG_PREFIX} 网页读取完成：方式=定向搜索；"
                f"引擎={provider_label(providers)}；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
            return {"status": "ok", "url": url, **payload, "cached": False}

        return {
            "status": "error",
            "url": url,
            "mode": "search_fallback",
            "provider": "tavily",
            "providers": ["tavily"],
            "provider_mode": "tavily",
            "error": extract_error,
            "failed_results": failed_results,
            "fallback_error": fallback.error or "网址定向搜索未返回可用内容",
        }

    async def extract(
        self,
        urls: list[str] | tuple[str, ...],
        *,
        query: str = "",
        chunks_per_source: int = 3,
        extract_depth: str = "advanced",
        include_images: bool = False,
        include_favicon: bool = False,
        format: str = "markdown",
        umo: str = "",
    ) -> dict[str, Any]:
        values = []
        invalid_values = []
        for item in urls or []:
            raw_value = str(item or "").strip()
            normalized = self._normalize_public_url(raw_value)
            if normalized:
                if normalized not in values:
                    values.append(normalized)
            elif raw_value:
                invalid_values.append(raw_value)
        if not values:
            return {
                "status": "error",
                "urls": invalid_values,
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "网页地址必须是公网 HTTP 或 HTTPS 地址",
            }
        if not self.tavily_available(umo):
            return {
                "status": "disabled",
                "urls": values,
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "网页读取需要启用联网搜索并配置 Tavily",
            }
        try:
            data = await (await self._tavily(umo)).extract(
                values[:20],
                query=_compact(query, 1000),
                chunks_per_source=chunks_per_source,
                extract_depth=extract_depth,
                include_images=include_images,
                include_favicon=include_favicon,
                format=format,
                timeout=self.settings.fetch_timeout_seconds,
            )
            results = []
            for item in data.get("results") or []:
                if not isinstance(item, dict):
                    continue
                entry = {
                    "url": str(item.get("url") or "").strip(),
                    "content": str(item.get("raw_content") or "").strip()[
                        : self.settings.max_page_chars
                    ],
                }
                if item.get("favicon"):
                    entry["favicon"] = str(item["favicon"])
                if item.get("images"):
                    entry["images"] = list(item["images"] or [])[:20]
                results.append(entry)
            failed = list(data.get("failed_results") or [])
            failed.extend(
                {"url": item, "error": "地址不是公网 HTTP(S)"}
                for item in invalid_values
            )
            return {
                "status": "ok" if results else "error",
                "urls": values,
                "results": results,
                "failed_results": failed,
                "results_count": len(results),
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                **({"error": "Tavily 未返回可用网页正文"} if not results else {}),
            }
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 批量网页提取失败：{exc}")
            return {
                "status": "error",
                "urls": values,
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": str(exc),
            }

    async def map(
        self,
        url: str,
        *,
        instructions: str = "",
        max_depth: int = 1,
        max_breadth: int | None = None,
        limit: int | None = None,
        select_paths: list[str] | tuple[str, ...] = (),
        select_domains: list[str] | tuple[str, ...] = (),
        exclude_paths: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        allow_external: bool = True,
        umo: str = "",
    ) -> dict[str, Any]:
        normalized_url = self._normalize_public_url(url)
        if not normalized_url:
            return {
                "status": "error",
                "url": str(url or ""),
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "网站地址必须是公网 HTTP 或 HTTPS 地址",
            }
        if not self.tavily_available(umo):
            return {
                "status": "disabled",
                "url": normalized_url,
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "站点映射需要启用联网搜索并配置 Tavily",
            }
        try:
            bounded_depth = self._bounded_positive(
                max_depth, 1, self.settings.map_max_depth
            )
            bounded_breadth = self._bounded_positive(
                max_breadth,
                self.settings.map_max_breadth,
                self.settings.map_max_breadth,
            )
            bounded_limit = self._bounded_positive(
                limit,
                self.settings.map_max_results,
                self.settings.map_max_results,
            )
            values = await (await self._tavily(umo)).map(
                normalized_url,
                instructions=_compact(instructions, 500),
                max_depth=bounded_depth,
                max_breadth=bounded_breadth,
                limit=bounded_limit,
                select_paths=select_paths,
                select_domains=select_domains,
                exclude_paths=exclude_paths,
                exclude_domains=exclude_domains,
                allow_external=allow_external,
                timeout=self.settings.fetch_timeout_seconds,
            )
            values = list(
                dict.fromkeys(
                    str(item).strip() for item in values if str(item).strip()
                )
            )
            return {
                "status": "ok",
                "url": normalized_url,
                "results": values,
                "results_count": len(values),
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "parameters": {
                    "max_depth": bounded_depth,
                    "max_breadth": bounded_breadth,
                    "limit": bounded_limit,
                    "select_paths": list(select_paths or []),
                    "select_domains": list(select_domains or []),
                    "exclude_paths": list(exclude_paths or []),
                    "exclude_domains": list(exclude_domains or []),
                    "allow_external": bool(allow_external),
                },
            }
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 站点映射失败：{exc}")
            return {
                "status": "error",
                "url": normalized_url,
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": str(exc),
            }

    async def crawl(
        self,
        url: str,
        *,
        instructions: str = "",
        max_depth: int = 1,
        max_breadth: int | None = None,
        limit: int | None = None,
        select_paths: list[str] | tuple[str, ...] = (),
        select_domains: list[str] | tuple[str, ...] = (),
        exclude_paths: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        allow_external: bool = True,
        include_images: bool = False,
        include_favicon: bool = False,
        extract_depth: str = "advanced",
        format: str = "markdown",
        umo: str = "",
    ) -> dict[str, Any]:
        normalized_url = self._normalize_public_url(url)
        if not normalized_url:
            return {
                "status": "error",
                "url": str(url or ""),
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "网站地址必须是公网 HTTP 或 HTTPS 地址",
            }
        if not self.tavily_available(umo):
            return {
                "status": "disabled",
                "url": normalized_url,
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "站点抓取需要启用联网搜索并配置 Tavily",
            }
        started = time.monotonic()
        try:
            bounded_depth = self._bounded_positive(
                max_depth, 1, self.settings.crawl_max_depth
            )
            bounded_breadth = self._bounded_positive(
                max_breadth,
                self.settings.crawl_max_breadth,
                self.settings.crawl_max_breadth,
            )
            bounded_limit = self._bounded_positive(
                limit,
                self.settings.crawl_max_results,
                self.settings.crawl_max_results,
            )
            data = await (await self._tavily(umo)).crawl(
                normalized_url,
                instructions=_compact(instructions, 2000),
                max_depth=bounded_depth,
                max_breadth=bounded_breadth,
                limit=bounded_limit,
                select_paths=select_paths,
                select_domains=select_domains,
                exclude_paths=exclude_paths,
                exclude_domains=exclude_domains,
                allow_external=allow_external,
                include_images=include_images,
                include_favicon=include_favicon,
                extract_depth=extract_depth,
                format=format,
                timeout=self.settings.crawl_timeout_seconds,
            )
            results = data.get("results") or []
            normalized = []
            seen_urls: set[str] = set()
            for item in results:
                if not isinstance(item, dict):
                    continue
                item_url = str(item.get("url") or "").strip()
                if not item_url or item_url in seen_urls:
                    continue
                seen_urls.add(item_url)
                normalized.append(
                    {
                        "url": item_url,
                        "content": str(item.get("raw_content") or "").strip()[
                            : self.settings.max_page_chars
                        ],
                        **(
                            {"images": list(item.get("images") or [])[:20]}
                            if item.get("images")
                            else {}
                        ),
                        **({"favicon": item["favicon"]} if item.get("favicon") else {}),
                    }
                )
            logger.debug(
                f"{LOG_PREFIX} 站点抓取完成：页面={len(normalized)}；"
                f"耗时={time.monotonic() - started:.2f} 秒"
            )
            return {
                "status": "ok",
                "url": normalized_url,
                "results": normalized,
                "results_count": len(normalized),
                "failed_results": list(data.get("failed_results") or []),
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "parameters": {
                    "max_depth": bounded_depth,
                    "max_breadth": bounded_breadth,
                    "limit": bounded_limit,
                    "select_paths": list(select_paths or []),
                    "select_domains": list(select_domains or []),
                    "exclude_paths": list(exclude_paths or []),
                    "exclude_domains": list(exclude_domains or []),
                    "allow_external": bool(allow_external),
                    "include_images": bool(include_images),
                    "include_favicon": bool(include_favicon),
                    "extract_depth": str(extract_depth or "advanced"),
                    "format": str(format or "markdown"),
                },
            }
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 站点抓取失败：{exc}")
            return {
                "status": "error",
                "url": normalized_url,
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": str(exc),
            }

    async def research(
        self,
        input_text: str,
        *,
        model: str = "auto",
        output_length: str = "standard",
        citation_format: str = "numbered",
        include_domains: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        output_schema: dict[str, Any] | None = None,
        umo: str = "",
    ) -> dict[str, Any]:
        normalized_input = _compact(input_text, 4000)
        if not normalized_input:
            return {
                "status": "error",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "研究问题不能为空",
            }
        if not self.tavily_available(umo):
            return {
                "status": "disabled",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "研究任务需要启用联网搜索并配置 Tavily",
            }
        self._prune_research_tasks(time.monotonic())
        if len(self._research_tasks) >= RESEARCH_TASK_MAX_ITEMS:
            return {
                "status": "error",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": "研究任务数量已达到上限，请等待正在执行的任务结束",
            }
        try:
            requested_model = str(model or "auto").strip().lower()
            if requested_model not in {"auto", "mini", "pro"}:
                requested_model = "auto"
            requested_length = str(output_length or "standard").strip().lower()
            if requested_length not in {"short", "standard", "long"}:
                requested_length = "standard"
            requested_citations = str(citation_format or "numbered").strip().lower()
            if requested_citations not in {"numbered", "mla", "apa", "chicago"}:
                requested_citations = "numbered"
            data = await (await self._tavily(umo)).research_create(
                normalized_input,
                model=requested_model,
                output_length=requested_length,
                citation_format=requested_citations,
                include_domains=normalize_domains(include_domains),
                exclude_domains=normalize_domains(exclude_domains),
                output_schema=output_schema,
            )
            request_id = str(data.get("request_id") or "").strip()
            if not request_id:
                raise RuntimeError("Tavily 未返回研究任务编号")
            task_id = uuid.uuid4().hex[:12]
            task = ResearchTask(
                task_id=task_id,
                request_id=request_id,
                created_at=time.monotonic(),
                umo=str(umo or "").strip(),
            )
            self._research_tasks[task_id] = task
            enqueue = getattr(self.task_store, "enqueue_durable_task", None)
            if callable(enqueue):
                try:
                    await enqueue(
                        f"web_research:{task_id}",
                        "web_research",
                        {
                            "task_id": task_id,
                            "request_id": request_id,
                            "umo": str(umo or "").strip(),
                            "input": normalized_input,
                        },
                        priority=60,
                        max_attempts=3,
                    )
                except Exception as exc:
                    logger.warning(f"{LOG_PREFIX} 研究任务持久化失败：{exc}")
            poller = asyncio.create_task(self._poll_research_task(task_id, umo))
            self._research_pollers[task_id] = poller
            return {
                "status": "pending",
                "task_id": task_id,
                "request_id": request_id,
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "response_timing": "after_delivery",
                "response_stance": "研究任务已开始；完成后再发送带引用的研究结果",
            }
        except Exception as exc:
            logger.warning(f"{LOG_PREFIX} 研究任务创建失败：{exc}")
            return {
                "status": "error",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "error": str(exc),
            }

    async def _poll_research_task(self, task_id: str, umo: str) -> None:
        task = self._research_tasks.get(task_id)
        if task is None:
            return
        started = time.monotonic()
        try:
            while time.monotonic() - started < self.settings.research_timeout_seconds:
                data = await (
                    await self._tavily(umo, timeout_seconds=30)
                ).research_status(task.request_id, timeout=30)
                status = str(data.get("status") or "pending").strip().lower()
                task.status = status
                task.result = data
                if status in {"completed", "failed", "cancelled", "error"}:
                    return
                await asyncio.sleep(self.settings.research_poll_interval_seconds)
            task.status = "timeout"
            task.error = "研究任务超过等待时间"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            task.status = "error"
            task.error = str(exc)
            logger.warning(f"{LOG_PREFIX} 研究任务状态查询失败：{exc}")
        finally:
            if self._research_task_finished(task):
                task.finished_at = time.monotonic()
                result = {
                    "status": task.status,
                    "research": self._bounded_research_payload(task.result),
                    "error": task.error,
                }
                complete = getattr(
                    self.task_store, "complete_durable_task_by_key", None
                )
                fail = getattr(self.task_store, "fail_durable_task_by_key", None)
                if task.status == "completed" and callable(complete):
                    try:
                        await complete(f"web_research:{task_id}", result)
                    except Exception as exc:
                        logger.warning(f"{LOG_PREFIX} 研究任务结果持久化失败：{exc}")
                elif task.status != "completed" and callable(fail):
                    try:
                        await fail(f"web_research:{task_id}", task.error or task.status, result)
                    except Exception as exc:
                        logger.warning(f"{LOG_PREFIX} 研究任务失败状态持久化失败：{exc}")
                elif callable(complete):
                    # 兼容旧版测试存储或外部存储实现；实际归档使用上面的失败接口。
                    try:
                        await complete(f"web_research:{task_id}", result)
                    except Exception as exc:
                        logger.warning(f"{LOG_PREFIX} 研究任务结果持久化失败：{exc}")
            self._research_pollers.pop(task_id, None)

    async def research_status(self, task_id: str, *, umo: str = "") -> dict[str, Any]:
        self._prune_research_tasks(time.monotonic())
        task = self._research_tasks.get(str(task_id or "").strip())
        if task is None:
            return {"status": "error", "error": "研究任务不存在或已过期"}
        requested_umo = str(umo or "").strip()
        if requested_umo and task.umo and requested_umo != task.umo:
            return {"status": "error", "error": "无权查看其他会话的研究任务"}
        payload: dict[str, Any] = {
            "status": task.status,
            "task_id": task.task_id,
            "request_id": task.request_id,
            "provider": "tavily",
            "providers": ["tavily"],
            "provider_mode": "tavily",
        }
        if task.result:
            data = self._bounded_research_payload(task.result)
            if isinstance(data.get("output"), str):
                data["output"] = str(data["output"] or "")[:RESEARCH_TOOL_MAX_CHARS]
            payload["research"] = data
        if task.error:
            payload["error"] = task.error
        return payload

    async def inspiration(
        self,
        keyword: str,
        prompt_template: str,
        *,
        category: str = "",
        persona: str = "",
        today: str = "",
        trace_id: str = "",
    ) -> str:
        if not self.enabled or not self.settings.inspiration_enabled:
            return ""
        values = {
            "keyword": _compact(keyword, 300),
            "category": _compact(category, 100),
            "persona": _compact(persona, 180),
            "today": _compact(today, 180),
            "date": life_now().strftime("%Y年%m月%d日"),
        }
        try:
            query = str(prompt_template or "{keyword}").format_map(values)
        except (KeyError, ValueError):
            query = values["keyword"]
        result = await self.search(query, depth="quick", trace_id=trace_id)
        if result.status != "ok":
            return ""
        quality = str(result.quality or "weak").strip().lower()
        quality_label = {
            "strong": "充分",
            "partial": "有限",
            "weak": "不足",
        }.get(quality, "不足")
        logger.debug(
            f"{LOG_PREFIX} 日程联网参考：质量={quality_label}；"
            f"引擎={provider_label(result.effective_providers())}；"
            f"来源={len(result.sources)}；类别={_compact(category, 40) or '未分类'}"
        )
        if quality == "weak" or not result.sources:
            logger.debug(f"{LOG_PREFIX} 日程联网参考已忽略：缺少可核验来源")
            return ""
        source_lines = [
            f"- {item.title or item.url}：{item.url}" for item in result.sources[:3]
        ]
        source_text = f"\n来源：\n{chr(10).join(source_lines)}" if source_lines else ""
        heading = (
            "联网灵感参考（证据有限，仅作生活背景参考）："
            if quality == "partial"
            else "联网灵感参考："
        )
        return f"{heading}\n{result.content[:1200]}{source_text}"

    async def tool_search(
        self,
        query: str,
        depth: str,
        platform: str,
        *,
        source_scope: str = "web",
        time_range: str = "",
        start_date: str = "",
        end_date: str = "",
        image_search: bool = False,
        image_understanding: bool = False,
        topic: str = "general",
        include_raw_content: bool = False,
        include_images: bool = False,
        include_image_descriptions: bool = False,
        include_domains: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        country: str = "",
        auto_parameters: bool = False,
        exact_match: bool = False,
        umo: str = "",
        turn_id: str = "",
    ) -> str:
        signature = self._tool_search_signature(
            query,
            depth,
            platform,
            source_scope,
            time_range,
            start_date,
            end_date,
            image_search,
            image_understanding,
            topic=topic,
            include_raw_content=include_raw_content,
            include_images=include_images,
            include_image_descriptions=include_image_descriptions,
            include_domains=list(include_domains or []),
            exclude_domains=list(exclude_domains or []),
            country=country,
            auto_parameters=auto_parameters,
            exact_match=exact_match,
        )
        context_signature = self._tool_search_context_signature(
            platform,
            source_scope,
            image_search,
            image_understanding,
            topic=topic,
            include_raw_content=include_raw_content,
            include_images=include_images,
            include_domains=list(include_domains or []),
            exclude_domains=list(exclude_domains or []),
            country=country,
            auto_parameters=auto_parameters,
            exact_match=exact_match,
        )
        normalized_turn_id = str(turn_id or "").strip()
        if not normalized_turn_id:
            if reused := self._recent_tool_result(umo, signature):
                logger.debug(f"{LOG_PREFIX} 重复搜索已复用：查询词和参数相同")
                return reused
            result = await self.search(
                query,
                depth=depth,
                source_scope=source_scope,
                platform=platform,
                time_range=time_range,
                start_date=start_date,
                end_date=end_date,
                image_search=image_search,
                image_understanding=image_understanding,
                topic=topic,
                include_raw_content=include_raw_content,
                include_images=include_images,
                include_image_descriptions=include_image_descriptions,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                country=country,
                auto_parameters=auto_parameters,
                exact_match=exact_match,
                umo=umo,
            )
            payload = json.dumps(self._tool_payload(result), ensure_ascii=False)
            self._remember_tool_result(umo, signature, payload)
            return payload

        session = self._tool_session(normalized_turn_id, umo)
        async with session.lock:
            if signature in session.signatures:
                logger.debug(f"{LOG_PREFIX} 同轮搜索已复用：查询词和参数相同")
                return session.signatures[signature]
            if context_signature in session.strong_results:
                logger.debug(f"{LOG_PREFIX} 同轮搜索已停止：当前搜索证据已经充分")
                return self._reused_strong_payload(
                    session.strong_results[context_signature], query
                )

            execution_started = time.monotonic()
            deadline = execution_started + max(session.remaining_seconds, 0.0)
            try:
                result = await self.search(
                    query,
                    depth=depth,
                    source_scope=source_scope,
                    platform=platform,
                    time_range=time_range,
                    start_date=start_date,
                    end_date=end_date,
                    image_search=image_search,
                    image_understanding=image_understanding,
                    topic=topic,
                    include_raw_content=include_raw_content,
                    include_images=include_images,
                    include_image_descriptions=include_image_descriptions,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                    country=country,
                    auto_parameters=auto_parameters,
                    exact_match=exact_match,
                    umo=umo,
                    deadline=deadline,
                    session_id=session.session_id,
                )
            finally:
                elapsed = time.monotonic() - execution_started
                session.remaining_seconds = max(
                    0.0, session.remaining_seconds - elapsed
                )
            payload = json.dumps(self._tool_payload(result), ensure_ascii=False)
            session.signatures[signature] = payload
            if (
                result.status == "ok"
                and str(result.quality or "").strip().lower() == "strong"
                and not result.missing_aspects
            ):
                session.strong_results[context_signature] = payload
            return payload

    async def tool_fetch(
        self,
        url: str = "",
        *,
        urls: list[str] | tuple[str, ...] = (),
        query: str = "",
        chunks_per_source: int = 3,
        extract_depth: str = "advanced",
        include_images: bool = False,
        include_favicon: bool = False,
        format: str = "markdown",
        umo: str = "",
    ) -> str:
        values = list(urls or [])
        if values or (url and len(values) != 1):
            values = values or [url]
        if (
            len(values) > 1
            or query
            or include_images
            or include_favicon
            or format != "markdown"
        ):
            return json.dumps(
                await self.extract(
                    values,
                    query=query,
                    chunks_per_source=chunks_per_source,
                    extract_depth=extract_depth,
                    include_images=include_images,
                    include_favicon=include_favicon,
                    format=format,
                    umo=umo,
                ),
                ensure_ascii=False,
            )
        return json.dumps(await self.fetch(url, umo=umo), ensure_ascii=False)

    async def tool_map(
        self,
        url: str,
        instructions: str,
        max_depth: int,
        *,
        max_breadth: int = 50,
        limit: int = 100,
        select_paths: list[str] | tuple[str, ...] = (),
        select_domains: list[str] | tuple[str, ...] = (),
        exclude_paths: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        allow_external: bool = True,
        umo: str = "",
    ) -> str:
        return json.dumps(
            await self.map(
                url,
                instructions=instructions,
                max_depth=max_depth,
                max_breadth=max_breadth,
                limit=limit,
                select_paths=select_paths,
                select_domains=select_domains,
                exclude_paths=exclude_paths,
                exclude_domains=exclude_domains,
                allow_external=allow_external,
                umo=umo,
            ),
            ensure_ascii=False,
        )

    async def tool_crawl(self, url: str, *, umo: str = "", **options: Any) -> str:
        return json.dumps(await self.crawl(url, umo=umo, **options), ensure_ascii=False)

    async def tool_research(
        self, input_text: str, *, umo: str = "", **options: Any
    ) -> str:
        return json.dumps(
            await self.research(input_text, umo=umo, **options), ensure_ascii=False
        )

    async def tool_research_status(self, task_id: str, *, umo: str = "") -> str:
        return json.dumps(
            await self.research_status(task_id, umo=umo), ensure_ascii=False
        )
