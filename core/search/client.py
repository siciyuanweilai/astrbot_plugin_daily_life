from __future__ import annotations

import asyncio
import email.utils
import json
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import aiohttp

from .evidence import normalize_image_assets
from .model import SearchAnswer, SearchSource


X_SEARCH_SYSTEM_PROMPT = (
    "使用 x_search 工具获取 X 上当前且可验证的信息。"
    "直接依据检索到的帖子和来源回答。没有完成 x_search 调用时，不要声称已经搜索 X。"
)

PLAN_SYSTEM_PROMPT = (
    "只返回一个 JSON 对象，其中 queries 数组最多包含 {limit} 个简洁、相互独立的后续网页搜索，"
    "用于解决剩余的不确定性。查询使用原问题的主要语言，不重复已经确认的内容。只能返回 JSON。"
)


class SearchRequestTimeout(RuntimeError):
    pass


class TavilyExtractError(RuntimeError):
    def __init__(
        self, message: str, failed_results: list[dict[str, str]] | None = None
    ):
        super().__init__(message)
        self.failed_results = list(failed_results or [])


@dataclass(frozen=True, slots=True)
class TavilySearchOptions:
    search_depth: str = "advanced"
    topic: str = "general"
    time_range: str = ""
    start_date: str = ""
    end_date: str = ""
    include_answer: bool | str = True
    include_raw_content: bool = False
    include_images: bool = False
    include_image_descriptions: bool = False
    include_favicon: bool = False
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()
    country: str = ""
    auto_parameters: bool = False
    exact_match: bool = False


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _target_url(value: Any) -> str:
    target = str(value or "").strip()
    if not target:
        raise ValueError("网页地址不能为空")
    return target


def _source(value: Any, provider: str) -> SearchSource | None:
    if not isinstance(value, dict):
        return None
    candidate = (
        value.get("url_citation")
        if isinstance(value.get("url_citation"), dict)
        else value
    )
    url = _text(candidate.get("url") or candidate.get("uri"))
    if not url:
        return None
    score_value = candidate.get("score")
    try:
        score = float(score_value) if score_value is not None else None
    except (TypeError, ValueError):
        score = None
    return SearchSource(
        url=url,
        title=_text(candidate.get("title")),
        snippet=_text(
            candidate.get("snippet")
            or candidate.get("description")
            or candidate.get("content")
            or candidate.get("raw_content")
        ),
        provider=provider,
        score=score,
        images=normalize_image_assets(
            candidate.get("images") or [],
            source_url=url,
            source_title=_text(candidate.get("title")),
        ),
        favicon=_text(candidate.get("favicon")),
    )


def _responses_text(data: dict[str, Any]) -> str:
    values: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict) or part.get("type") not in {
                "output_text",
                "text",
            }:
                continue
            text = str(part.get("text") or "").strip()
            if text:
                values.append(text)
    return "\n".join(values).strip()


def _responses_sources(data: dict[str, Any], provider: str) -> list[SearchSource]:
    values: list[Any] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"web_search_call", "x_search_call"}:
            action = item.get("action")
            if isinstance(action, dict) and isinstance(action.get("sources"), list):
                values.extend(action["sources"])
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("annotations"), list):
                    values.extend(part["annotations"])
    for key in ("annotations", "citations", "sources"):
        current = data.get(key)
        if isinstance(current, list):
            values.extend(current)

    result: list[SearchSource] = []
    seen: set[str] = set()
    for value in values:
        item = _source(value, provider) if isinstance(value, dict) else None
        if item is None or item.url in seen:
            continue
        seen.add(item.url)
        result.append(item)
    return result


def _responses_search_calls(data: dict[str, Any], call_type: str) -> int:
    return sum(
        1
        for item in data.get("output") or []
        if isinstance(item, dict) and item.get("type") == call_type
    )


def _retry_delay(response: aiohttp.ClientResponse, attempt: int) -> float:
    header = response.headers.get("Retry-After", "").strip()
    if header:
        try:
            return max(float(header), 0.0)
        except ValueError:
            try:
                target = email.utils.parsedate_to_datetime(header)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max((target - datetime.now(timezone.utc)).total_seconds(), 0.0)
            except (TypeError, ValueError):
                pass
    return min((2**attempt) + random.random(), 10.0)


class GrokClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_base: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
    ):
        self.session = session
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @property
    def endpoint(self) -> str:
        if self.api_base.endswith("/chat/completions"):
            return self.api_base
        if self.api_base.endswith("/responses"):
            return f"{self.api_base[: -len('/responses')]}/chat/completions"
        return f"{self.api_base}/chat/completions"

    @property
    def responses_endpoint(self) -> str:
        if self.api_base.endswith("/responses"):
            return self.api_base
        if self.api_base.endswith("/chat/completions"):
            return f"{self.api_base[: -len('/chat/completions')]}/responses"
        return f"{self.api_base}/responses"

    async def _request_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        label: str,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error = ""
        timed_out = False
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        for attempt in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SearchRequestTimeout(
                    f"{label} 请求超过 {self.timeout_seconds} 秒仍未完成"
                )
            try:
                timeout = aiohttp.ClientTimeout(total=remaining)
                async with self.session.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                ) as response:
                    body = await response.text()
                    if response.status in {429, 500, 502, 503, 504} and attempt < 2:
                        delay = _retry_delay(response, attempt)
                        remaining = deadline - time.monotonic()
                        if delay >= remaining:
                            raise SearchRequestTimeout(
                                f"{label} 请求超过 {self.timeout_seconds} 秒仍未完成"
                            )
                        await asyncio.sleep(delay)
                        continue
                    if response.status != 200:
                        raise RuntimeError(
                            f"{label} 请求失败（HTTP {response.status}）：{body[:300]}"
                        )
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(f"{label} 返回内容不是有效的 JSON") from exc
                    if not isinstance(data, dict):
                        return {}
                    data["_daily_life_request_meta"] = {
                        "attempts": attempt + 1,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    }
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = str(exc)
                timed_out = isinstance(exc, asyncio.TimeoutError)
                if attempt < 2:
                    delay = min((2**attempt) + random.random(), 10.0)
                    remaining = deadline - time.monotonic()
                    if delay >= remaining:
                        break
                    await asyncio.sleep(delay)
                    continue
                break
        if time.monotonic() >= deadline or timed_out:
            raise SearchRequestTimeout(
                f"{label} 请求超过 {self.timeout_seconds} 秒仍未完成"
            )
        raise RuntimeError(f"{label} 网络请求失败：{last_error or '未知错误'}")

    async def complete(self, system: str, user: str) -> tuple[str, dict[str, Any]]:
        data = await self._request_json(
            self.endpoint,
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
            label="Grok",
        )
        choices = data.get("choices") or []
        message = (
            choices[0].get("message")
            if choices and isinstance(choices[0], dict)
            else {}
        )
        if not isinstance(message, dict):
            message = {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            )
        return str(content or "").strip(), {"data": data, "message": message}

    async def search(
        self,
        query: str,
        *,
        source_scope: str = "x",
        platform: str = "",
        time_context: str = "",
        start_date: str = "",
        end_date: str = "",
        image_search: bool = False,
        image_understanding: bool = False,
    ) -> SearchAnswer:
        if str(source_scope or "").strip().lower() != "x":
            raise ValueError("Grok 仅支持 X 平台搜索")
        source_scope = "x"
        tool_type = "x_search"
        call_type = "x_search_call"
        provider = "grok-x"
        system_prompt = X_SEARCH_SYSTEM_PROMPT
        scope = f"\n限定平台或站点范围：{platform}。" if platform else ""
        date_scope = (
            f"\n搜索日期范围：{start_date or '不限'} 至 {end_date or '不限'}。"
            if start_date or end_date
            else ""
        )
        prompt = (
            f"{system_prompt}\n{time_context}{date_scope}\n搜索查询：{query}{scope}"
        ).strip()
        search_tool: dict[str, Any] = {"type": tool_type}
        if start_date:
            search_tool["from_date"] = start_date
        if end_date:
            search_tool["to_date"] = end_date
        if image_understanding:
            search_tool["enable_image_understanding"] = True
        data = await self._request_json(
            self.responses_endpoint,
            {
                "model": self.model,
                "input": prompt,
                "tools": [search_tool],
                "tool_choice": "auto",
            },
            label="Grok X 搜索",
        )
        output = data.get("output")
        sources = _responses_sources(data, provider)
        search_calls = _responses_search_calls(data, call_type)
        content = _responses_text(data)
        searched = bool(search_calls or sources)
        invalid_reason = ""
        if not isinstance(output, list):
            invalid_reason = "响应中缺少 output 数组"
        elif not content and not searched:
            invalid_reason = "正文为空且没有搜索调用或引用"
        elif not content:
            invalid_reason = "正文为空"
        elif not searched:
            invalid_reason = "没有搜索调用或引用"
        request_meta = data.get("_daily_life_request_meta")
        if not isinstance(request_meta, dict):
            request_meta = {}
        return SearchAnswer(
            content=content,
            sources=sources,
            searched=searched,
            search_calls=search_calls,
            source_scope=source_scope,
            elapsed_ms=int(request_meta.get("elapsed_ms") or 0),
            attempts=int(request_meta.get("attempts") or 0),
            providers=[provider],
            invalid_reason=invalid_reason,
        )

    async def plan(self, query: str, context: str, *, limit: int = 2) -> list[str]:
        bounded_limit = max(1, min(int(limit), 3))
        content, _raw = await self.complete(
            PLAN_SYSTEM_PROMPT.format(limit=bounded_limit),
            f"原始问题：{query}\n已有结果：{context[:3000]}",
        )
        candidate = content.strip()
        if candidate.startswith("```") and candidate.endswith("```"):
            lines = candidate.splitlines()
            candidate = "\n".join(lines[1:-1]).strip()
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            return []
        values = data.get("queries") if isinstance(data, dict) else []
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values:
            item = _text(value)[:500]
            if item and item not in result and item != query:
                result.append(item)
        return result[:bounded_limit]


class TavilyClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_key: str,
        timeout_seconds: int,
    ):
        self.session = session
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(
            total=timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with self.session.post(
            f"https://api.tavily.com/{path.lstrip('/')}",
            headers=headers,
            json=payload,
            timeout=timeout,
        ) as response:
            body = await response.text()
            if response.status not in {200, 201}:
                raise RuntimeError(
                    f"Tavily 请求失败（HTTP {response.status}）：{body[:300]}"
                )
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Tavily 返回内容不是有效的 JSON") from exc
            return data if isinstance(data, dict) else {}

    async def _get(
        self, path: str, *, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(
            total=timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        )
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with self.session.get(
            f"https://api.tavily.com/{path.lstrip('/')}",
            headers=headers,
            timeout=timeout,
        ) as response:
            body = await response.text()
            if response.status != 200:
                raise RuntimeError(
                    f"Tavily 请求失败（HTTP {response.status}）：{body[:300]}"
                )
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Tavily 返回内容不是有效的 JSON") from exc
            return data if isinstance(data, dict) else {}

    async def search(
        self,
        query: str,
        max_results: int = 8,
        *,
        search_depth: str = "advanced",
        topic: str = "general",
        time_range: str = "",
        start_date: str = "",
        end_date: str = "",
        include_answer: bool | str = True,
        include_raw_content: bool = False,
        include_images: bool = False,
        include_image_descriptions: bool = False,
        include_favicon: bool = False,
        include_domains: tuple[str, ...] = (),
        exclude_domains: tuple[str, ...] = (),
        country: str = "",
        auto_parameters: bool = False,
        exact_match: bool = False,
    ) -> SearchAnswer:
        options = TavilySearchOptions(
            search_depth=search_depth,
            topic=topic,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date,
            include_answer=include_answer,
            include_raw_content=include_raw_content,
            include_images=include_images,
            include_image_descriptions=include_image_descriptions,
            include_favicon=include_favicon,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            country=country,
            auto_parameters=auto_parameters,
            exact_match=exact_match,
        )
        payload = self._search_payload(query, max_results, options)
        started = time.monotonic()
        data = await self._post("search", payload)
        return self._search_answer(data, started)

    @classmethod
    def _search_payload(
        cls,
        query: str,
        max_results: int,
        options: TavilySearchOptions,
    ) -> dict[str, Any]:
        request_start, request_end = cls._normalize_search_dates(
            options.start_date, options.end_date
        )
        payload: dict[str, Any] = {
            "query": query,
            "search_depth": options.search_depth
            if options.search_depth in {"basic", "fast", "advanced", "ultra-fast"}
            else "advanced",
            "include_answer": options.include_answer,
            "max_results": max(0, min(int(max_results), 20)),
        }
        if options.topic in {"general", "news", "finance"}:
            payload["topic"] = options.topic
        if options.time_range in {
            "day",
            "week",
            "month",
            "year",
            "d",
            "w",
            "m",
            "y",
        }:
            payload["time_range"] = options.time_range
        if request_start:
            payload["start_date"] = request_start
        if request_end:
            payload["end_date"] = request_end
        if options.include_raw_content:
            payload["include_raw_content"] = True
        if options.include_images:
            payload["include_images"] = True
        if options.include_image_descriptions:
            payload["include_image_descriptions"] = True
        if options.include_favicon:
            payload["include_favicon"] = True
        if options.include_domains:
            payload["include_domains"] = list(options.include_domains)[:300]
        if options.exclude_domains:
            payload["exclude_domains"] = list(options.exclude_domains)[:150]
        if options.country:
            payload["country"] = options.country
        if options.auto_parameters:
            payload["auto_parameters"] = True
        if options.exact_match:
            payload["exact_match"] = True
        return payload

    @staticmethod
    def _search_answer(data: dict[str, Any], started: float) -> SearchAnswer:
        sources: list[SearchSource] = []
        raw_images = data.get("images") or []
        for value in data.get("results") or []:
            item = _source(value, "tavily")
            if item is not None:
                sources.append(item)
        images = normalize_image_assets(raw_images)
        for source in sources:
            images = normalize_image_assets(
                [*images, *source.images],
                limit=20,
            )
        return SearchAnswer(
            content=_text(data.get("answer")),
            sources=sources,
            images=images,
            image_candidates=(
                len(raw_images)
                + sum(
                    len(value.get("images") or [])
                    for value in data.get("results") or []
                    if isinstance(value, dict)
                )
            ),
            searched=True,
            search_calls=1,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            attempts=1,
            providers=["tavily"],
        )

    @staticmethod
    def _normalize_search_dates(
        start_date: str = "", end_date: str = ""
    ) -> tuple[str, str]:
        """将 Tavily 不接受的单日范围转换为次日结束的半开区间。"""
        start = str(start_date or "").strip()
        end = str(end_date or "").strip()
        if start and start == end:
            try:
                end = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
            except ValueError:
                pass
        return start, end

    @staticmethod
    def _failed_extract_results(
        data: dict[str, Any], target: str
    ) -> list[dict[str, str]]:
        values = data.get("failed_results") or []
        if not isinstance(values, list):
            values = [values]
        result = []
        for value in values:
            if isinstance(value, dict):
                url = _text(value.get("url") or value.get("uri") or target)
                error = _text(
                    value.get("error")
                    or value.get("message")
                    or value.get("reason")
                    or value.get("status")
                )
            else:
                url = target
                error = _text(value)
            item = {"url": url[:500], "error": error[:500]}
            if item["url"] or item["error"]:
                result.append(item)
        return result[:10]

    async def extract(
        self,
        urls: str | list[str] | tuple[str, ...],
        *,
        query: str = "",
        chunks_per_source: int = 3,
        extract_depth: str = "advanced",
        include_images: bool = False,
        include_favicon: bool = False,
        format: str = "markdown",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        values = [urls] if isinstance(urls, str) else list(urls or [])
        targets = [_target_url(item) for item in values if str(item or "").strip()]
        if not targets:
            raise ValueError("网页地址不能为空")
        payload: dict[str, Any] = {
            "urls": targets,
            "format": format if format in {"markdown", "text"} else "markdown",
            "extract_depth": extract_depth
            if extract_depth in {"basic", "advanced"}
            else "advanced",
        }
        if query:
            payload["query"] = query[:1000]
            payload["chunks_per_source"] = max(1, min(int(chunks_per_source), 5))
        if include_images:
            payload["include_images"] = True
        if include_favicon:
            payload["include_favicon"] = True
        return await self._post("extract", payload, timeout_seconds=timeout)

    async def fetch(self, url: str) -> str:
        target = _target_url(url)
        data = await self.extract(target)
        results = data.get("results") or []
        failed_results = self._failed_extract_results(data, target)
        if not results or not isinstance(results[0], dict):
            reason = failed_results[0].get("error", "") if failed_results else ""
            message = "Tavily 未返回网页正文"
            if reason:
                message = f"{message}：{reason}"
            raise TavilyExtractError(message, failed_results)
        content = str(results[0].get("raw_content") or "").strip()
        if not content:
            raise TavilyExtractError("Tavily 返回的网页正文为空", failed_results)
        return content

    async def map(
        self,
        url: str,
        *,
        instructions: str,
        max_depth: int,
        max_breadth: int = 20,
        limit: int,
        select_paths: list[str] | tuple[str, ...] = (),
        select_domains: list[str] | tuple[str, ...] = (),
        exclude_paths: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        allow_external: bool = True,
        timeout: float | None = None,
    ) -> list[str]:
        target = _target_url(url)
        payload: dict[str, Any] = {
            "url": target,
            "max_depth": max(1, min(int(max_depth), 5)),
            "max_breadth": max(1, min(int(max_breadth), 500)),
            "limit": max(int(limit), 1),
            "allow_external": bool(allow_external),
        }
        if instructions:
            payload["instructions"] = instructions
        for key, values in (
            ("select_paths", select_paths),
            ("select_domains", select_domains),
            ("exclude_paths", exclude_paths),
            ("exclude_domains", exclude_domains),
        ):
            items = [str(item).strip() for item in values or [] if str(item).strip()]
            if items:
                payload[key] = items
        data = await self._post("map", payload, timeout_seconds=timeout)
        values = data.get("results") or []
        return [str(item).strip() for item in values if str(item).strip()][:limit]

    async def crawl(
        self,
        url: str,
        *,
        instructions: str = "",
        chunks_per_source: int = 3,
        max_depth: int = 1,
        max_breadth: int = 20,
        limit: int = 50,
        select_paths: list[str] | tuple[str, ...] = (),
        select_domains: list[str] | tuple[str, ...] = (),
        exclude_paths: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        allow_external: bool = True,
        include_images: bool = False,
        include_favicon: bool = False,
        extract_depth: str = "advanced",
        format: str = "markdown",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        target = _target_url(url)
        payload: dict[str, Any] = {
            "url": target,
            "max_depth": max(1, min(int(max_depth), 5)),
            "max_breadth": max(1, min(int(max_breadth), 500)),
            "limit": max(int(limit), 1),
            "allow_external": bool(allow_external),
            "extract_depth": extract_depth
            if extract_depth in {"basic", "advanced"}
            else "advanced",
            "format": format if format in {"markdown", "text"} else "markdown",
        }
        if instructions:
            payload["instructions"] = instructions[:2000]
            payload["chunks_per_source"] = max(1, min(int(chunks_per_source), 5))
        if include_images:
            payload["include_images"] = True
        if include_favicon:
            payload["include_favicon"] = True
        for key, values in (
            ("select_paths", select_paths),
            ("select_domains", select_domains),
            ("exclude_paths", exclude_paths),
            ("exclude_domains", exclude_domains),
        ):
            items = [str(item).strip() for item in values or [] if str(item).strip()]
            if items:
                payload[key] = items
        return await self._post("crawl", payload, timeout_seconds=timeout)

    async def research_create(
        self,
        input_text: str,
        *,
        model: str = "auto",
        stream: bool = False,
        output_schema: dict[str, Any] | None = None,
        citation_format: str = "numbered",
        include_domains: list[str] | tuple[str, ...] = (),
        exclude_domains: list[str] | tuple[str, ...] = (),
        output_length: str = "standard",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "input": input_text[:12000],
            "model": model if model in {"mini", "pro", "auto"} else "auto",
            "stream": bool(stream),
            "citation_format": citation_format
            if citation_format in {"numbered", "mla", "apa", "chicago"}
            else "numbered",
            "output_length": output_length
            if output_length in {"short", "standard", "long"}
            else "standard",
        }
        if isinstance(output_schema, dict) and output_schema.get("properties"):
            payload["output_schema"] = output_schema
        if include_domains:
            payload["include_domains"] = list(include_domains)[:20]
        if exclude_domains:
            payload["exclude_domains"] = list(exclude_domains)[:20]
        return await self._post("research", payload, timeout_seconds=30)

    async def research_status(
        self, request_id: str, *, timeout: float | None = None
    ) -> dict[str, Any]:
        target = str(request_id or "").strip()
        if not target:
            raise ValueError("研究任务编号不能为空")
        return await self._get(
            f"research/{target}", timeout_seconds=timeout
        )
