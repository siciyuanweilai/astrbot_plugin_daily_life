import asyncio
import json
import time
import types
import unittest
from datetime import date
from unittest.mock import patch

from support import LifeSettings

from core.search.client import (
    GrokClient,
    SearchRequestTimeout,
    TavilyClient,
    TavilyExtractError,
)
from core.search.model import SearchAnswer, SearchResult, SearchSource
from core.search.evidence import evidence_quality
from core.search.query import build_external_evidence_request, resolve_search_dates
from core.search.service import (
    RESEARCH_TASK_MAX_ITEMS,
    RESEARCH_TASK_TTL_SECONDS,
    ResearchTask,
    SearchService,
)
from core.prompts import DEFAULT_WEB_TODAY_PROMPT


class FakeProvider:
    def __init__(self):
        self.provider_config = {
            "api_base": "https://grok.example/v1",
            "model": "grok-4.5",
        }

    def get_keys(self):
        return ["grok-key"]


class FakeContext:
    def __init__(self, tavily_keys=None, session_keys=None):
        self.provider = FakeProvider()
        self.tavily_keys = ["tvly-key"] if tavily_keys is None else list(tavily_keys)
        self.session_keys = dict(session_keys or {})

    def get_provider_by_id(self, provider_id):
        return self.provider if provider_id == "grok-provider" else None

    def get_config(self, umo=None):
        keys = self.session_keys.get(umo, self.tavily_keys)
        return {"provider_settings": {"websearch_tavily_key": keys}}


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def text(self):
        return json.dumps(self.payload, ensure_ascii=False)


class FakeSession:
    def __init__(
        self,
        *,
        responses_status=200,
        x_status=None,
        responses_without_search=False,
        responses_without_sources=False,
        extract_payload=None,
        search_payload=None,
    ):
        self.closed = False
        self.calls = []
        self.responses_status = responses_status
        self.x_status = responses_status if x_status is None else x_status
        self.responses_without_search = responses_without_search
        self.responses_without_sources = responses_without_sources
        self.extract_payload = extract_payload
        self.search_payload = search_payload

    async def close(self):
        self.closed = True

    @staticmethod
    def _grok_response(content, url, title, call_type="web_search_call"):
        usage_key = (
            "x_search_calls" if call_type == "x_search_call" else "web_search_calls"
        )
        return {
            "object": "response",
            "output": [
                {
                    "type": call_type,
                    "status": "completed",
                    "action": {
                        "type": "search",
                        "query": content,
                        "sources": [{"type": "url", "url": url, "title": title}],
                    },
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": content,
                            "annotations": [
                                {"type": "url_citation", "url": url, "title": title}
                            ],
                        }
                    ],
                },
            ],
            "usage": {"server_side_tool_usage_details": {usage_key: 1}},
        }

    def post(self, url, **kwargs):
        payload = kwargs.get("json") or {}
        headers = kwargs.get("headers") or {}
        self.calls.append((url, payload, headers))

        if url.endswith("/responses"):
            tools = payload.get("tools") or [{}]
            tool_type = str(tools[0].get("type") or "")
            status = self.x_status if tool_type == "x_search" else self.responses_status
            if status != 200:
                return FakeResponse(
                    {"error": {"message": "responses unavailable"}},
                    status=status,
                )
            prompt = str(payload.get("input") or "")
            if self.responses_without_search:
                return FakeResponse(
                    {
                        "object": "response",
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {"type": "output_text", "text": "Unverified answer"}
                                ],
                            }
                        ],
                    }
                )
            if self.responses_without_sources:
                return FakeResponse(
                    {
                        "object": "response",
                        "output": [
                            {
                                "type": f"{tool_type}_call"
                                if tool_type
                                else "web_search_call",
                                "status": "completed",
                                "action": {"type": "search", "query": prompt},
                            },
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "Searched answer without citations",
                                    }
                                ],
                            },
                        ],
                    }
                )
            if tool_type == "x_search":
                if "official update details" in prompt:
                    return FakeResponse(
                        self._grok_response(
                            "Official update details",
                            "https://x.com/example/status/official",
                            "Official X update",
                            call_type="x_search_call",
                        )
                    )
                if "community feedback" in prompt:
                    return FakeResponse(
                        self._grok_response(
                            "Community feedback",
                            "https://x.com/example/status/community",
                            "Community X feedback",
                            call_type="x_search_call",
                        )
                    )
                return FakeResponse(
                    self._grok_response(
                        "Current X search result",
                        "https://x.com/example/status/1",
                        "X post",
                        call_type="x_search_call",
                    )
                )
            call_type = (
                "x_search_call" if tool_type == "x_search" else "web_search_call"
            )
            if "official update details" in prompt:
                return FakeResponse(
                    self._grok_response(
                        "Official update details",
                        "https://official.example/update",
                        "Official update",
                        call_type=call_type,
                    )
                )
            if "community feedback" in prompt:
                return FakeResponse(
                    self._grok_response(
                        "Community feedback",
                        "https://community.example/report",
                        "Community report",
                        call_type=call_type,
                    )
                )
            return FakeResponse(
                self._grok_response(
                    "Current Grok search result",
                    "https://official.example/news",
                    "Official news",
                )
            )

        if url.endswith("/chat/completions"):
            system = payload["messages"][0]["content"]
            content = (
                '{"queries":["official update details","community feedback"]}'
                if "queries 数组" in system
                else "{}"
            )
            return FakeResponse({"choices": [{"message": {"content": content}}]})

        if url.endswith("/search"):
            return FakeResponse(
                self.search_payload
                if self.search_payload is not None
                else {
                    "answer": "Tavily fallback summary",
                    "results": [
                        {
                            "url": "https://other.example/report",
                            "title": "Fallback source",
                            "content": "Fallback content",
                        }
                    ],
                }
            )
        if url.endswith("/extract"):
            return FakeResponse(
                self.extract_payload
                if self.extract_payload is not None
                else {"results": [{"raw_content": "# Page content"}]}
            )
        if url.endswith("/map"):
            return FakeResponse(
                {"results": ["https://docs.example/start", "https://docs.example/api"]}
            )
        if url.endswith("/crawl"):
            return FakeResponse(
                {
                    "base_url": "docs.example",
                    "results": [
                        {
                            "url": "https://docs.example/start",
                            "raw_content": "# Start",
                        }
                    ],
                }
            )
        if url.endswith("/research"):
            return FakeResponse(
                {
                    "request_id": "research-1",
                    "status": "pending",
                    "model": "mini",
                },
                status=201,
            )
        raise AssertionError(f"unexpected URL: {url}")

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params", {}), kwargs.get("headers", {})))
        return FakeResponse(
            {
                "request_id": "research-1",
                "status": "completed",
                "output": "Research report",
                "citations": [],
            }
        )


class SequencedSession:
    def __init__(self, statuses):
        self.closed = False
        self.statuses = list(statuses)
        self.timeouts = []

    def post(self, url, **kwargs):
        timeout = kwargs["timeout"]
        self.timeouts.append(getattr(timeout, "total", timeout.kwargs.get("total")))
        status = self.statuses.pop(0)
        if status == 200:
            return FakeResponse(FakeSession._grok_response("done", "https://ok", "ok"))
        return FakeResponse(
            {"error": "retry"}, status=status, headers={"Retry-After": "0"}
        )


class FakeToolSet:
    def __init__(self, names):
        self.names = list(names)

    def remove_tool(self, name):
        self.names = [item for item in self.names if item != name]


class SearchServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_extract_empty_urls_returns_structured_error(self):
        service = SearchService.__new__(SearchService)
        result = await service.extract([])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["urls"], [])
        self.assertEqual(result["error"], "网页地址不能为空")

    def test_external_evidence_request_owns_share_search_policy(self):
        news = build_external_evidence_request("测试主题", "news")
        knowledge = build_external_evidence_request("测试知识", "knowledge")
        recommendation = build_external_evidence_request("测试作品", "recommendation")

        self.assertEqual(news.query, "测试主题")
        self.assertEqual((news.topic, news.time_range), ("news", "week"))
        self.assertEqual(knowledge.query, "测试知识")
        self.assertEqual((knowledge.topic, knowledge.time_range), ("general", ""))
        self.assertEqual(recommendation.query, "测试作品")
        self.assertEqual(recommendation.trace_id, "daily-share-recommendation")

    def test_external_evidence_request_rejects_invalid_input(self):
        with self.assertRaisesRegex(ValueError, "检索内容不能为空"):
            build_external_evidence_request("", "news")
        with self.assertRaisesRegex(ValueError, "不支持的分享检索类别"):
            build_external_evidence_request("测试主题", "other")

    async def test_external_evidence_search_uses_shared_search_service(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        calls = []

        async def search(query, **kwargs):
            calls.append((query, kwargs))
            return SearchResult(
                status="ok",
                query=query,
                content="可验证的搜索证据",
            )

        service.search = search
        result = await service.search_external_evidence(
            "测试主题",
            category="news",
            umo="bot-test:GroupMessage:group-test-a",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["category"], "news")
        self.assertEqual(calls[0][0], "测试主题")
        self.assertEqual(calls[0][1]["topic"], "news")
        self.assertEqual(calls[0][1]["time_range"], "week")
        self.assertEqual(calls[0][1]["umo"], "bot-test:GroupMessage:group-test-a")

    def settings(self, **overrides):
        data = {
            "enabled": True,
            "provider": "grok-provider",
            "tavily_api_keys": ["tvly-key"],
            "cache_ttl_seconds": 300,
            "deep_max_followups": 2,
            **overrides,
        }
        return LifeSettings.from_dict({"search_config": data}).search

    async def test_quick_search_uses_official_responses_and_cache_without_tavily(self):
        service = SearchService(FakeContext(), self.settings())
        session = FakeSession()
        service._session = session

        first = await service.search("today updates")
        second = await service.search("today updates")

        self.assertEqual(first.status, "ok")
        self.assertEqual(first.content, "Tavily fallback summary")
        self.assertEqual(
            [item.url for item in first.sources],
            ["https://other.example/report"],
        )
        self.assertEqual(first.effective_providers(), ["tavily"])
        self.assertEqual(first.as_dict()["provider_mode"], "tavily")
        self.assertTrue(second.cached)
        self.assertEqual(second.effective_providers(), ["tavily"])
        self.assertEqual(
            [call[0] for call in session.calls], ["https://api.tavily.com/search"]
        )
        await service.close()

    async def test_search_logs_separate_request_details_from_result_summary(self):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0, max_sources=1),
        )
        service._session = FakeSession(
            search_payload={
                "answer": "Tavily search summary",
                "results": [
                    {
                        "url": "https://one.example/report",
                        "title": "Source one",
                        "content": "First source",
                    },
                    {
                        "url": "https://two.example/report",
                        "title": "Source two",
                        "content": "Second source",
                    },
                ],
            }
        )
        query = "查询日期：2026年07月23日 目标类别：今日生活背景 角色背景：测试人设"

        with (
            patch("core.search.service.logger.info") as info_log,
            patch("core.search.service.logger.debug") as debug_log,
        ):
            result = await service.search(query, trace_id="task-1")

        self.assertEqual(len(result.sources), 1)
        info_messages = [str(call.args[0]) for call in info_log.call_args_list]
        debug_messages = [str(call.args[0]) for call in debug_log.call_args_list]
        start_log = next(item for item in info_messages if "联网搜索开始" in item)
        complete_log = next(item for item in info_messages if "联网搜索完成" in item)
        provider_log = next(item for item in debug_messages if "Tavily 搜索完成" in item)

        self.assertNotIn("查询=", start_log)
        self.assertIn("原始来源=2", provider_log)
        self.assertNotIn("图片候选=", provider_log)
        self.assertIn("采用来源=1", complete_log)
        self.assertIn("总耗时=", complete_log)
        self.assertNotIn("深度=", complete_log)
        self.assertNotIn("范围=", complete_log)
        self.assertNotIn("时间=不限~不限", complete_log)
        self.assertNotIn("查询=", complete_log)
        self.assertTrue(
            any("联网搜索查询：任务=task-1；内容=" in item for item in debug_messages)
        )

    async def test_search_cache_reuses_unicode_and_whitespace_equivalent_query(self):
        service = SearchService(FakeContext(), self.settings())
        session = FakeSession()
        service._session = session

        first = await service.search("Ｔｏｄａｙ\u3000  updates")
        second = await service.search("today updates")

        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(len(session.calls), 1)
        await service.close()

    async def test_concurrent_identical_searches_share_one_execution(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=60))
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def execute(request, grok, *, deadline, session_id=""):
            calls.append(request.query)
            started.set()
            await release.wait()
            return SearchResult(
                status="ok",
                query=request.query,
                content="共享搜索结果",
                providers=["tavily"],
            )

        service._execute_search = execute
        first_task = asyncio.create_task(
            service.search("same query", session_id="session-a")
        )
        await started.wait()
        second_task = asyncio.create_task(
            service.search("same query", session_id="session-b")
        )
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(first_task, second_task)

        self.assertEqual(calls, ["same query"])
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(first.session_id, "session-a")
        self.assertEqual(second.session_id, "session-b")
        await service.close()

    async def test_search_cache_does_not_reuse_session_id(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=60))
        service._session = FakeSession()

        first = await service.search("session isolation", session_id="session-a")
        second = await service.search("session isolation")

        self.assertEqual(first.session_id, "session-a")
        self.assertTrue(second.cached)
        self.assertTrue(second.session_id)
        self.assertNotEqual(second.session_id, "session-a")
        await service.close()

    async def test_search_cache_isolates_image_description_option(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=60))
        calls = []

        async def execute(request, grok, *, deadline, session_id=""):
            calls.append(request.include_image_descriptions)
            return SearchResult(
                status="ok",
                query=request.query,
                content=f"descriptions={request.include_image_descriptions}",
                providers=["tavily"],
            )

        service._execute_search = execute
        plain = await service.search("image query", include_images=True)
        described = await service.search(
            "image query",
            include_images=True,
            include_image_descriptions=True,
        )
        cached = await service.search(
            "image query",
            include_images=True,
            include_image_descriptions=True,
        )

        self.assertFalse(plain.cached)
        self.assertFalse(described.cached)
        self.assertTrue(cached.cached)
        self.assertEqual(calls, [False, True])
        await service.close()

    async def test_grok_timeout_is_one_budget_across_all_attempts(self):
        session = SequencedSession([503, 503, 200])
        client = GrokClient(
            session,
            api_base="https://grok.example/v1",
            api_key="key",
            model="grok-4.5",
            timeout_seconds=45,
        )

        result = await client.search("budgeted search", source_scope="x")

        self.assertEqual(len(session.timeouts), 3)
        self.assertLessEqual(session.timeouts[1], session.timeouts[0])
        self.assertLessEqual(session.timeouts[2], session.timeouts[1])
        self.assertEqual(result.attempts, 3)
        self.assertLess(result.elapsed_ms, 45000)

    async def test_grok_timeout_uses_structured_exception(self):
        client = GrokClient(
            FakeSession(),
            api_base="https://grok.example/v1",
            api_key="key",
            model="grok-4.5",
            timeout_seconds=0,
        )

        with self.assertRaises(SearchRequestTimeout):
            await client.search("timeout search", source_scope="x")

    async def test_immediate_same_round_search_reuses_first_result(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession()
        service._session = session

        first = await service.tool_search(
            "中文角色特征",
            "quick",
            "",
            umo="group:10001",
        )
        first_call_count = len(session.calls)
        second = await service.tool_search(
            "中文角色特征",
            "quick",
            "",
            umo="group:10001",
        )

        self.assertEqual(second, first)
        self.assertEqual(len(session.calls), first_call_count)

    async def test_same_round_search_with_different_query_is_not_reused(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession()
        service._session = session

        first = await service.tool_search(
            "中文人物特征",
            "quick",
            "",
            umo="group:10001",
        )
        first_call_count = len(session.calls)
        second = await service.tool_search(
            "English character traits",
            "quick",
            "",
            umo="group:10001",
        )

        self.assertNotEqual(json.loads(first)["query"], json.loads(second)["query"])
        self.assertGreater(len(session.calls), first_call_count)

    async def test_same_turn_different_query_executes_again(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession()
        service._session = session

        first = json.loads(
            await service.tool_search(
                "主问题",
                "quick",
                "",
                umo="group:10001",
                turn_id="message-42",
            )
        )
        first_call_count = len(session.calls)
        second = json.loads(
            await service.tool_search(
                "换一种问法继续查",
                "deep",
                "",
                umo="group:10001",
                turn_id="message-42",
            )
        )

        self.assertGreater(len(session.calls), first_call_count)
        self.assertEqual(first["query"], "主问题")
        self.assertEqual(second["query"], "换一种问法继续查")
        self.assertFalse(second.get("reused", False))
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(first["providers"], ["tavily"])
        self.assertEqual(second["providers"], first["providers"])
        self.assertEqual(second["provider_mode"], "tavily")

    async def test_same_turn_failed_search_retries_with_active_execution_budget(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        calls = []

        async def search_once(query, **kwargs):
            calls.append((query, kwargs["deadline"], kwargs["session_id"]))
            if len(calls) == 1:
                return SearchResult(
                    status="error",
                    query=query,
                    error="temporary failure",
                    session_id=kwargs["session_id"],
                )
            return SearchResult(
                status="ok",
                query=query,
                content="verified",
                sources=[SearchSource(url="https://example.com/verified")],
                session_id=kwargs["session_id"],
                quality="partial",
            )

        service.search = search_once
        await service.tool_search(
            "first query",
            "quick",
            "",
            umo="group:10001",
            turn_id="message-43",
        )
        await asyncio.sleep(0.03)
        await service.tool_search(
            "second query",
            "quick",
            "",
            umo="group:10001",
            turn_id="message-43",
        )

        self.assertEqual(len(calls), 2)
        self.assertGreater(calls[1][1], calls[0][1])
        self.assertEqual(calls[0][2], calls[1][2])

    async def test_same_turn_strong_result_stops_queued_search(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        calls = []

        async def search_once(query, **kwargs):
            calls.append(query)
            return SearchResult(
                status="ok",
                query=query,
                content="verified",
                sources=[
                    SearchSource(
                        url=f"https://source-{index}.example/report",
                        provider="grok-web",
                    )
                    for index in range(3)
                ],
                session_id=kwargs["session_id"],
                quality="strong",
                missing_aspects=[],
                providers=["grok-web"],
            )

        service.search = search_once
        first = json.loads(
            await service.tool_search(
                "main question",
                "deep",
                "",
                umo="group:10001",
                turn_id="message-strong",
                time_range="year",
            )
        )
        second = json.loads(
            await service.tool_search(
                "same question rephrased",
                "quick",
                "",
                umo="group:10001",
                turn_id="message-strong",
            )
        )

        self.assertEqual(calls, ["main question"])
        self.assertEqual(second["query"], first["query"])
        self.assertTrue(second["reused"])
        self.assertEqual(second["requested_query"], "same question rephrased")

    def test_evidence_quality_uses_relevance_and_source_diversity(self):
        low_score = [
            SearchSource(
                url=f"https://low-{index}.example/report",
                provider="tavily",
                score=0.2,
            )
            for index in range(5)
        ]
        high_score = [
            SearchSource(
                url=f"https://high-{index}.example/report",
                provider="tavily",
                score=0.8,
            )
            for index in range(3)
        ]
        native = [
            SearchSource(
                url=f"https://native-{index}.example/report",
                provider="grok-web",
            )
            for index in range(3)
        ]

        self.assertEqual(evidence_quality("answer", low_score), "partial")
        self.assertEqual(evidence_quality("answer", high_score), "strong")
        self.assertEqual(evidence_quality("answer", native), "strong")
        self.assertEqual(evidence_quality("", native), "weak")

    async def test_schedule_inspiration_applies_evidence_quality_gate(self):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0, inspiration_enabled=True),
        )
        results = [
            SearchResult(
                status="ok",
                query="weak",
                content="unverified",
                quality="weak",
            ),
            SearchResult(
                status="ok",
                query="partial",
                content="limited reference",
                sources=[
                    SearchSource(
                        url="https://partial.example/report",
                        title="Partial source",
                    )
                ],
                quality="partial",
            ),
            SearchResult(
                status="ok",
                query="strong",
                content="verified reference",
                sources=[
                    SearchSource(
                        url=f"https://strong-{index}.example/report",
                        title=f"Strong source {index}",
                    )
                    for index in range(3)
                ],
                quality="strong",
            ),
        ]

        async def search_once(*args, **kwargs):
            return results.pop(0)

        service.search = search_once
        with (
            patch("core.search.service.logger.info"),
            patch("core.search.service.logger.debug") as debug_log,
        ):
            weak = await service.inspiration("weak", "{keyword}", category="今日日程")
            partial = await service.inspiration(
                "partial", "{keyword}", category="今日日程"
            )
            strong = await service.inspiration(
                "strong", "{keyword}", category="今日日程"
            )

        self.assertEqual(weak, "")
        self.assertIn("证据有限，仅作生活背景参考", partial)
        self.assertIn("limited reference", partial)
        self.assertTrue(strong.startswith("联网灵感参考："))
        self.assertNotIn("证据有限", strong)
        messages = [str(call.args[0]) for call in debug_log.call_args_list]
        self.assertTrue(any("质量=不足" in item for item in messages))
        self.assertTrue(any("质量=有限" in item for item in messages))
        self.assertTrue(any("质量=充分" in item for item in messages))
        self.assertTrue(any("已忽略：缺少可核验来源" in item for item in messages))

    async def test_schedule_inspiration_renders_every_template_variable(self):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0, inspiration_enabled=True),
        )
        queries = []

        async def search_once(query, **kwargs):
            queries.append((query, kwargs))
            return SearchResult(status="error", query=query)

        service.search = search_once
        await service.inspiration(
            "雨天居家",
            DEFAULT_WEB_TODAY_PROMPT,
            category="今日生活背景",
            persona="喜欢清爽日常感的角色",
            today="2026-07-17",
        )

        query = queries[0][0]
        self.assertIn("雨天居家", query)
        self.assertIn("今日生活背景", query)
        self.assertIn("喜欢清爽日常感的角色", query)
        self.assertIn("2026-07-17", query)
        self.assertRegex(query, r"查询日期：\d{4}年\d{2}月\d{2}日")
        self.assertNotRegex(query, r"\{(?:keyword|category|date|persona|today)\}")

    async def test_web_search_starts_providers_concurrently_and_falls_back_after_priority_window(
        self,
    ):
        self.skipTest("网页搜索已固定使用 Tavily，不再竞争 Grok 网页搜索")
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        started = {}
        grok_cancelled = asyncio.Event()

        async def slow_grok(*args, **kwargs):
            started["grok"] = time.monotonic()
            try:
                await asyncio.sleep(1)
            finally:
                grok_cancelled.set()

        async def strong_tavily(*args, **kwargs):
            started["tavily"] = time.monotonic()
            return SearchAnswer(
                content="cross checked",
                sources=[
                    SearchSource(
                        url=f"https://source-{index}.example/report",
                        provider="tavily",
                        score=0.9,
                    )
                    for index in range(3)
                ],
                searched=True,
            )

        service._grok_once = slow_grok
        service._tavily_fallback = strong_tavily
        service.settings.timeout_seconds = 0.01
        answer = await service._single_search(
            object(),
            "current fact",
            source_scope="web",
            platform="",
            start_date="",
            end_date="",
            umo="group:10001",
            image_search=False,
            image_understanding=False,
            deadline=time.monotonic() + 1,
        )

        self.assertEqual(evidence_quality(answer.content, answer.sources), "strong")
        self.assertLess(abs(started["tavily"] - started["grok"]), 0.05)
        self.assertTrue(grok_cancelled.is_set())
        self.assertEqual(answer.effective_providers(), ["tavily"])

    async def test_tavily_finishes_first_but_valid_grok_still_has_priority(self):
        self.skipTest("网页搜索已固定使用 Tavily，不再保留 Grok 网页优先级")
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))

        async def delayed_grok(*args, **kwargs):
            await asyncio.sleep(0.02)
            return SearchAnswer(
                content="Grok verified",
                sources=[
                    SearchSource(
                        url="https://grok.example/report",
                        provider="grok-web",
                        score=0.95,
                    )
                ],
                searched=True,
            )

        async def fast_tavily(*args, **kwargs):
            await asyncio.sleep(0.005)
            return SearchAnswer(
                content="Tavily corroboration",
                sources=[
                    SearchSource(
                        url="https://tavily.example/report",
                        provider="tavily",
                        score=0.9,
                    )
                ],
                searched=True,
            )

        service._grok_once = delayed_grok
        service._tavily_fallback = fast_tavily
        service.settings.timeout_seconds = 0.1
        answer = await service._single_search(
            object(),
            "current fact",
            source_scope="web",
            platform="",
            start_date="",
            end_date="",
            umo="group:10001",
            image_search=False,
            image_understanding=False,
            deadline=time.monotonic() + 1,
        )

        self.assertEqual(answer.content, "Grok verified")
        self.assertNotIn("Tavily corroboration", answer.content)
        self.assertEqual(answer.effective_providers(), ["grok-web"])

    async def test_fast_valid_grok_result_cancels_tavily_standby(self):
        self.skipTest("网页搜索已固定使用 Tavily，不再启动 Grok 网页待命任务")
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        tavily_started = asyncio.Event()
        tavily_cancelled = asyncio.Event()

        async def strong_grok(*args, **kwargs):
            return SearchAnswer(
                content="verified",
                sources=[
                    SearchSource(
                        url=f"https://source-{index}.example/report",
                        provider="grok-web",
                    )
                    for index in range(3)
                ],
                searched=True,
            )

        async def slow_tavily(*args, **kwargs):
            tavily_started.set()
            try:
                await asyncio.sleep(1)
            finally:
                tavily_cancelled.set()

        service._grok_once = strong_grok
        service._tavily_fallback = slow_tavily
        answer = await service._single_search(
            object(),
            "current fact",
            source_scope="web",
            platform="",
            start_date="",
            end_date="",
            umo="group:10001",
            image_search=False,
            image_understanding=False,
            deadline=time.monotonic() + 1,
        )

        self.assertEqual(evidence_quality(answer.content, answer.sources), "strong")
        self.assertEqual(answer.effective_providers(), ["grok-web"])
        self.assertTrue(tavily_started.is_set())
        self.assertTrue(tavily_cancelled.is_set())

    async def test_quick_tool_search_returns_bounded_evidence_view(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        service._session = FakeSession()

        payload = json.loads(await service.tool_search("today updates", "quick", ""))

        self.assertEqual(payload["answer"], "Tavily fallback summary")
        self.assertEqual(payload["quality"], "partial")
        self.assertIn("search_guidance", payload)
        self.assertIn("证据仍有缺口", payload["search_guidance"])
        self.assertNotIn("confidence", payload)
        self.assertEqual(payload["evidence_count"], 1)
        self.assertEqual(payload["providers"], ["tavily"])
        self.assertEqual(payload["provider_mode"], "tavily")
        self.assertLessEqual(len(payload["evidence"]), 5)
        self.assertNotIn("content", payload)
        self.assertNotIn("sources", payload)

    async def test_quick_tool_answer_is_bounded_without_mutating_full_result(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=60))
        full_result = SearchResult(
            status="ok",
            query="long answer",
            content="A" * 2400,
            sources=[
                SearchSource(url=f"https://example.com/{index}") for index in range(6)
            ],
            depth="quick",
        )

        async def search_once(*args, **kwargs):
            return full_result

        service.search = search_once
        payload = json.loads(await service.tool_search("long answer", "quick", ""))

        self.assertLessEqual(len(payload["answer"]), 2000)
        self.assertEqual(len(payload["evidence"]), 5)
        self.assertEqual(len(full_result.content), 2400)
        self.assertEqual(len(full_result.sources), 6)

    async def test_deep_tool_search_returns_bounded_result_contract(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        service._session = FakeSession()

        payload = json.loads(await service.tool_search("full verification", "deep", ""))

        self.assertIn("content", payload)
        self.assertIn("sources", payload)
        self.assertNotIn("answer", payload)
        self.assertEqual(payload["depth"], "deep")
        self.assertLessEqual(len(payload["content"]), 6000)
        self.assertLessEqual(len(payload["sources"]), 8)
        self.assertTrue(all(len(item["snippet"]) <= 400 for item in payload["sources"]))
        self.assertIn("quality", payload)
        self.assertIn("missing_aspects", payload)
        self.assertIn("queries_executed", payload)
        self.assertIn("session_id", payload)
        self.assertEqual(payload["providers"], ["tavily"])
        self.assertEqual(payload["provider_mode"], "tavily")
        self.assertIn("search_guidance", payload)
        self.assertTrue(payload["search_guidance"])

    async def test_same_round_search_with_different_scope_does_not_reuse(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession()
        service._session = session

        await service.tool_search(
            "网页资料",
            "quick",
            "",
            source_scope="web",
            umo="group:10001",
        )
        first_call_count = len(session.calls)
        await service.tool_search(
            "X 讨论",
            "quick",
            "",
            source_scope="x",
            umo="group:10001",
        )

        self.assertGreater(len(session.calls), first_call_count)

    async def test_grok_failure_calls_tavily_only_as_fallback(self):
        self.skipTest("Grok 网页搜索已移除；Tavily 是网页主搜索")
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession(responses_status=400)
        service._session = session

        with (
            patch("core.search.service.logger.info") as info_log,
            patch("core.search.service.logger.warning") as warning_log,
        ):
            result = await service.search("fallback query")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.content, "Tavily fallback summary")
        self.assertEqual(result.sources[0].provider, "tavily")
        self.assertEqual(result.effective_providers(), ["tavily"])
        self.assertEqual(
            [call[0] for call in session.calls],
            ["https://grok.example/v1/responses", "https://api.tavily.com/search"],
        )
        info_messages = [str(call.args[0]) for call in info_log.call_args_list]
        warning_messages = [str(call.args[0]) for call in warning_log.call_args_list]
        self.assertTrue(any("联网搜索开始" in item for item in info_messages))
        self.assertTrue(any("Grok 搜索开始" in item for item in info_messages))
        self.assertTrue(any("Tavily 搜索开始" in item for item in info_messages))
        self.assertTrue(any("Tavily 搜索完成" in item for item in info_messages))
        self.assertTrue(
            any(
                "联网搜索完成" in item and "引擎=Tavily" in item
                for item in info_messages
            )
        )
        self.assertTrue(
            any(
                "Grok 网页搜索失败" in item and "耗时=" in item
                for item in warning_messages
            )
        )

    async def test_grok_timeout_log_includes_reason_and_elapsed(self):
        self.skipTest("Grok 网页搜索已移除；超时日志由 Tavily 请求负责")
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        service.settings.timeout_seconds = 0
        session = FakeSession()
        service._session = session

        with (
            patch("core.search.service.logger.info") as info_log,
            patch("core.search.service.logger.warning") as warning_log,
        ):
            result = await service.search("timeout fallback")

        self.assertEqual(result.status, "ok")
        warning_messages = [str(call.args[0]) for call in warning_log.call_args_list]
        self.assertTrue(
            any(
                "Grok 网页搜索失败" in item and "原因=超时" in item and "耗时=" in item
                for item in warning_messages
            )
        )
        self.assertTrue(
            any("联网搜索开始" in str(call.args[0]) for call in info_log.call_args_list)
        )

    async def test_missing_search_evidence_calls_tavily_fallback(self):
        self.skipTest("Grok 网页证据兜底已移除；Tavily 响应直接作为网页结果")
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession(responses_without_search=True)
        service._session = session

        with patch("core.search.service.logger.warning") as warning_log:
            result = await service.search("verify this")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.content, "Tavily fallback summary")
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(
            any(
                "未形成有效搜索结果" in str(call.args[0])
                and "原因=没有搜索调用或引用" in str(call.args[0])
                for call in warning_log.call_args_list
            )
        )

    async def test_search_call_without_citations_keeps_grok_priority(self):
        self.skipTest("Grok 网页搜索已移除；网页结果统一由 Tavily 提供")
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession(responses_without_sources=True)
        service._session = session

        result = await service.search("verify with sources")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.content, "Searched answer without citations")
        self.assertEqual(result.sources, [])
        self.assertEqual(result.effective_providers(), ["grok-web"])
        self.assertEqual(len(session.calls), 2)

    async def test_search_without_citations_keeps_grok_when_tavily_is_unavailable(self):
        self.skipTest("Grok 网页搜索已移除；未配置 Tavily 时网页搜索应明确失败")
        service = SearchService(
            FakeContext(tavily_keys=[]),
            self.settings(cache_ttl_seconds=0),
        )
        session = FakeSession(responses_without_sources=True)
        service._session = session

        result = await service.search("best available result")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.content, "Searched answer without citations")
        self.assertEqual(result.sources, [])

    async def test_deep_search_stops_when_followups_add_no_sources(self):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0, deep_max_followups=3),
        )

        class PlanningClient:
            def __init__(self):
                self.plan_calls = 0

            async def plan(self, query, context, *, limit=2):
                self.plan_calls += 1
                return [f"followup {self.plan_calls}"]

        client = PlanningClient()
        request = service._normalize_search_request(
            types.SimpleNamespace(
                query="main query",
                depth="deep",
                source_scope="web",
                platform="",
                time_range="",
                start_date="",
                end_date="",
                image_search=False,
                image_understanding=False,
                umo="",
            )
        )

        async def same_source(*args, **kwargs):
            return SearchAnswer(
                content="additional detail",
                sources=[SearchSource(url="https://example.com/source#section")],
                searched=True,
            )

        service._search_once_request = same_source
        sections, sources = await service._deep_search_sections(
            client,
            request,
            "initial",
            [SearchSource(url="https://example.com/source")],
            deadline=10**12,
        )

        self.assertEqual(client.plan_calls, 1)
        self.assertEqual(len(sections), 1)
        self.assertEqual(len(sources), 1)

    async def test_deep_search_ignores_already_executed_queries(self):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0, deep_max_followups=3),
        )

        class RepeatingClient:
            async def plan(self, query, context, *, limit=2):
                return [" main   query ", "MAIN QUERY"]

        request = service._normalize_search_request(
            types.SimpleNamespace(
                query="main query",
                depth="deep",
                source_scope="web",
                platform="",
                time_range="",
                start_date="",
                end_date="",
                image_search=False,
                image_understanding=False,
                umo="",
            )
        )

        async def unexpected_search(*args, **kwargs):
            self.fail("重复查询不应再次执行搜索")

        service._search_once_request = unexpected_search
        sections, sources = await service._deep_search_sections(
            RepeatingClient(),
            request,
            "initial",
            [SearchSource(url="https://example.com/source")],
            deadline=10**12,
        )

        self.assertEqual(sections, [])
        self.assertEqual(len(sources), 1)

    async def test_deep_search_skips_followups_when_initial_evidence_is_sufficient(
        self,
    ):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0, max_sources=8),
        )

        class UnexpectedPlanner:
            async def plan(self, query, context, *, limit=2):
                raise AssertionError("来源充分时不应继续规划补充搜索")

        request = service._normalize_search_request(
            types.SimpleNamespace(
                query="main query",
                depth="deep",
                source_scope="web",
                platform="",
                time_range="",
                start_date="",
                end_date="",
                image_search=False,
                image_understanding=False,
                umo="",
            )
        )
        sources = [
            SearchSource(
                url=f"https://source-{index}.example/report",
                provider="grok-web",
            )
            for index in range(3)
        ]

        sections, kept_sources = await service._deep_search_sections(
            UnexpectedPlanner(),
            request,
            "initial",
            sources,
            deadline=10**12,
        )

        self.assertEqual(sections, [])
        self.assertEqual(kept_sources, sources)

    async def test_deep_search_zero_followups_only_keeps_initial_result(self):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0, deep_max_followups=0),
        )

        class UnexpectedPlanner:
            async def plan(self, query, context, *, limit=2):
                raise AssertionError("补充搜索数量为 0 时不应调用规划模型")

        request = service._normalize_search_request(
            types.SimpleNamespace(
                query="main query",
                depth="deep",
                source_scope="web",
                platform="",
                time_range="",
                start_date="",
                end_date="",
                image_search=False,
                image_understanding=False,
                umo="",
            )
        )
        initial_sources = [SearchSource(url="https://example.com/initial")]

        sections, sources = await service._deep_search_sections(
            UnexpectedPlanner(),
            request,
            "initial",
            initial_sources,
            deadline=10**12,
        )

        self.assertEqual(sections, [])
        self.assertEqual(sources, initial_sources)

    async def test_deep_search_limits_followups_and_labels_each_search(self):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0, deep_max_followups=3),
        )

        class PlanningClient:
            def __init__(self):
                self.limits = []

            async def plan(self, query, context, *, limit=2):
                self.limits.append(limit)
                return ["followup one", "followup two", "followup three"]

        request = service._normalize_search_request(
            types.SimpleNamespace(
                query="main query",
                depth="deep",
                source_scope="web",
                platform="",
                time_range="",
                start_date="",
                end_date="",
                image_search=False,
                image_understanding=False,
                umo="",
            )
        )
        calls = []

        async def search_followup(_grok, query, _request, **kwargs):
            calls.append((query, kwargs.get("step_label")))
            return SearchAnswer(
                content=f"result for {query}",
                sources=[SearchSource(url=f"https://example.com/{len(calls)}")],
                searched=True,
            )

        planner = PlanningClient()
        service._search_once_request = search_followup
        sections, _sources = await service._deep_search_sections(
            planner,
            request,
            "initial",
            [SearchSource(url="https://example.com/initial")],
            deadline=10**12,
        )

        self.assertEqual(
            calls,
            [
                ("followup one", "补充搜索 1/3"),
                ("followup two", "补充搜索 2/3"),
                ("followup three", "补充搜索 3/3"),
            ],
        )
        self.assertEqual(planner.limits, [3])
        self.assertEqual(len(sections), 1)

    async def test_deep_search_keeps_completed_followup_when_peer_times_out(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))

        class PlanningClient:
            async def plan(self, query, context, *, limit=2):
                return ["fast followup", "slow followup"]

        request = service._normalize_search_request(
            types.SimpleNamespace(
                query="main query",
                depth="deep",
                source_scope="web",
                platform="",
                time_range="",
                start_date="",
                end_date="",
                image_search=False,
                image_understanding=False,
                umo="",
            )
        )

        async def timed_followup(_grok, query, _request, **_kwargs):
            if query == "slow followup":
                await asyncio.sleep(0.1)
            return SearchAnswer(
                content=f"result for {query}",
                sources=[SearchSource(url=f"https://example.com/{query}")],
                searched=True,
            )

        service._search_once_request = timed_followup
        with patch("core.search.service.DEEP_MIN_FOLLOWUP_SECONDS", 0.0):
            sections, sources = await service._deep_search_sections(
                PlanningClient(),
                request,
                "initial",
                [SearchSource(url="https://example.com/initial")],
                deadline=time.monotonic() + 0.03,
            )

        self.assertEqual(len(sections), 1)
        self.assertIn("fast followup", sections[0])
        self.assertNotIn("slow followup", sections[0])
        self.assertEqual(len(sources), 2)

    async def test_deep_total_timeout_keeps_initial_search_result(self):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0, deep_max_followups=3),
        )
        service.settings.total_timeout_seconds = 0.01

        class SlowPlanningClient:
            async def plan(self, query, context, *, limit=2):
                await asyncio.sleep(0.05)
                return ["late followup"]

        client = SlowPlanningClient()

        async def get_client():
            return client

        async def initial_search(grok, query, request, *, deadline=None):
            return SearchAnswer(
                content="initial verified result",
                sources=[SearchSource(url="https://example.com/initial")],
                searched=True,
            )

        service._search_grok_client = get_client
        service._search_once_request = initial_search

        started = time.monotonic()
        result = await service.search("time limited", depth="deep")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.content, "initial verified result")
        self.assertEqual(len(result.sources), 1)
        self.assertLess(time.monotonic() - started, 0.05)

    async def test_total_timeout_covers_search_provider_initialization(self):
        service = SearchService(
            FakeContext(),
            self.settings(cache_ttl_seconds=0),
        )
        service.settings.total_timeout_seconds = 0.01

        async def slow_client():
            await asyncio.sleep(0.05)
            return None

        service._search_grok_client = slow_client

        result = await service.search("provider timeout")

        self.assertEqual(result.status, "error")
        self.assertTrue(result.error)

    async def test_deep_search_uses_responses_for_each_followup(self):
        service = SearchService(
            FakeContext(tavily_keys=[]), self.settings(cache_ttl_seconds=0)
        )
        session = FakeSession()
        service._session = session

        with patch("core.search.service.logger.debug") as debug_log:
            result = await service.search(
                "full verification", depth="deep", source_scope="x"
            )

        self.assertEqual(result.status, "ok")
        self.assertIn("Current X search result", result.content)
        self.assertIn("Official update details", result.content)
        self.assertIn("Community feedback", result.content)
        self.assertEqual(result.depth, "deep")
        self.assertEqual(
            result.queries_executed,
            ["full verification", "official update details", "community feedback"],
        )
        self.assertEqual(
            [url.rsplit("/", 1)[-1] for url, _payload, _headers in session.calls],
            ["responses", "completions", "responses", "responses"],
        )
        messages = [str(call.args[0]) for call in debug_log.call_args_list]
        self.assertTrue(
            any(
                "Grok X 补充搜索 1/2 开始：查询=official update details" in item
                for item in messages
            )
        )
        self.assertTrue(
            any(
                "Grok X 补充搜索 2/2 开始：查询=community feedback" in item
                for item in messages
            )
        )

    async def test_image_flags_are_forwarded_without_domain_filters(self):
        client = TavilyClient(FakeSession(), api_key="tvly-key", timeout_seconds=10)

        result = await client.search(
            "find and inspect images",
            include_images=True,
            include_image_descriptions=True,
            include_raw_content=True,
        )
        payload = client.session.calls[0][1]

        self.assertTrue(result.searched)
        self.assertEqual(result.search_calls, 1)
        self.assertTrue(payload["include_images"])
        self.assertTrue(payload["include_image_descriptions"])
        self.assertTrue(payload["include_raw_content"])
        self.assertNotIn("include_domains", payload)
        self.assertNotIn("exclude_domains", payload)

    async def test_tavily_search_normalizes_top_level_images_and_filters_assets(self):
        session = FakeSession(
            search_payload={
                "answer": "图片结果",
                "images": [
                    {
                        "url": "https://images.example/photo.jpg",
                        "description": "现场照片",
                    },
                    "https://cdn.example/logo.svg",
                    "https://analytics.example/pixel.gif",
                ],
                "results": [
                    {
                        "url": "https://source.example/page",
                        "title": "来源页面",
                        "images": ["https://images.example/photo-2.jpg"],
                    }
                ],
            }
        )
        result = await TavilyClient(
            session, api_key="tvly-key", timeout_seconds=10
        ).search("现场照片", include_images=True)

        self.assertEqual(
            [item["url"] for item in result.images],
            [
                "https://images.example/photo.jpg",
                "https://images.example/photo-2.jpg",
            ],
        )
        self.assertEqual(result.image_candidates, 4)
        self.assertEqual(
            result.sources[0].images[0]["source_url"], "https://source.example/page"
        )

    async def test_image_search_returns_images_and_send_guidance(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        service._session = FakeSession(
            search_payload={
                "answer": "图片结果",
                "images": [
                    "https://images.example/one.jpg",
                    "https://images.example/two.jpg",
                    "https://images.example/three.jpg",
                ],
                "results": [
                    {
                        "url": "https://source.example/page",
                        "title": "图片来源",
                        "content": "相关内容",
                        "score": 0.9,
                    }
                ],
            }
        )

        payload = json.loads(
            await service.tool_search(
                "现场照片",
                "quick",
                "",
                image_search=True,
            )
        )

        self.assertEqual(payload["image_count"], 3)
        self.assertEqual(payload["image_quality"], "strong")
        self.assertTrue(payload["image_guidance"].startswith("图片已准备好"))
        self.assertEqual(len(payload["images"]), 3)

    async def test_image_search_enriches_top_sources_when_search_has_no_images(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession(
            extract_payload={
                "results": [
                    {
                        "url": "https://other.example/report",
                        "images": [
                            "https://images.example/photo.jpg",
                            "https://images.example/photo-2.jpg",
                        ],
                    }
                ]
            }
        )
        service._session = session

        result = await service.search("现场照片", image_search=True)

        self.assertEqual(result.image_quality, "partial")
        self.assertEqual(
            [item["url"] for item in result.images],
            [
                "https://images.example/photo.jpg",
                "https://images.example/photo-2.jpg",
            ],
        )
        self.assertEqual(
            [call[0] for call in session.calls],
            ["https://api.tavily.com/search", "https://api.tavily.com/extract"],
        )

    async def test_x_search_uses_official_tool_and_native_date_range(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession()
        service._session = session

        result = await service.search(
            "recent X discussion",
            source_scope="x",
            start_date="2026-07-01",
            end_date="2026-07-14",
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.source_scope, "x")
        self.assertEqual(result.start_date, "2026-07-01")
        self.assertEqual(result.end_date, "2026-07-14")
        self.assertEqual(result.sources[0].provider, "grok-x")
        tool = session.calls[0][1]["tools"][0]
        self.assertEqual(
            tool,
            {
                "type": "x_search",
                "from_date": "2026-07-01",
                "to_date": "2026-07-14",
            },
        )
        self.assertFalse(any(url.endswith("/search") for url, *_ in session.calls))

    async def test_x_search_failure_does_not_fallback_to_web(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession(x_status=400)
        service._session = session

        result = await service.search("X discussion", source_scope="x")

        self.assertEqual(result.status, "error")
        self.assertEqual(result.sources, [])
        tools = [call[1]["tools"][0]["type"] for call in session.calls]
        self.assertEqual(tools, ["x_search"])

    async def test_both_keeps_web_result_when_x_search_is_unavailable(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession(x_status=400)
        service._session = session

        result = await service.search("cross-source check", source_scope="both")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.sources[0].provider, "tavily")
        self.assertEqual(
            {
                call[1]["tools"][0]["type"]
                for call in session.calls
                if "tools" in call[1]
            },
            {"x_search"},
        )
        self.assertTrue(any(url.endswith("/search") for url, *_ in session.calls))

    async def test_both_merges_independent_web_and_x_evidence(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession()
        service._session = session

        result = await service.search("cross-source check", source_scope="both")

        self.assertEqual(result.status, "ok")
        self.assertIn("Tavily fallback summary", result.content)
        self.assertIn("X 搜索结果", result.content)
        self.assertEqual(
            {item.provider for item in result.sources},
            {"tavily", "grok-x"},
        )
        self.assertEqual(result.effective_providers(), ["grok-x", "tavily"])
        self.assertEqual(result.as_dict()["provider_mode"], "mixed")

    async def test_x_search_does_not_fallback_to_tavily(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession(responses_status=400, x_status=400)
        service._session = session

        result = await service.search(
            "X discussion",
            source_scope="x",
            start_date="2026-07-01",
            end_date="2026-07-14",
        )

        self.assertEqual(result.status, "error")
        self.assertEqual(result.sources, [])
        self.assertEqual(
            [call[1].get("tools", [{}])[0].get("type") for call in session.calls],
            ["x_search"],
        )

    async def test_web_date_range_is_a_request_constraint_not_a_tool_field(self):
        client = TavilyClient(FakeSession(), api_key="tvly-key", timeout_seconds=10)

        await client.search(
            "release notes",
            start_date="2026-07-01",
            end_date="2026-07-14",
        )

        payload = client.session.calls[0][1]
        self.assertEqual(payload["start_date"], "2026-07-01")
        self.assertEqual(payload["end_date"], "2026-07-14")
        self.assertNotIn("time_range", payload)

    async def test_relative_dates_and_explicit_dates_are_normalized_without_regex(self):
        today = date(2026, 7, 14)

        self.assertEqual(
            resolve_search_dates("week", "", "", today=today),
            ("2026-07-08", "2026-07-14"),
        )
        self.assertEqual(
            resolve_search_dates(
                "year",
                "2026-06-01",
                "2026-06-30",
                today=today,
            ),
            ("2026-06-01", "2026-06-30"),
        )
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            resolve_search_dates("", "2026/07/01", "", today=today)
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            resolve_search_dates("", "20260701", "", today=today)
        with self.assertRaisesRegex(ValueError, "不能晚于"):
            resolve_search_dates(
                "",
                "2026-07-14",
                "2026-07-01",
                today=today,
            )

    async def test_invalid_structured_scope_stops_before_network_access(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession()
        service._session = session

        result = await service.search("query", source_scope="social")

        self.assertEqual(result.status, "error")
        self.assertIn("web、x 或 both", result.error)
        self.assertEqual(session.calls, [])

    async def test_cache_is_isolated_by_source_and_resolved_dates(self):
        service = SearchService(FakeContext(), self.settings())
        session = FakeSession()
        service._session = session

        web = await service.search("same query")
        x = await service.search(
            "same query",
            source_scope="x",
            start_date="2026-07-01",
            end_date="2026-07-14",
        )
        cached_x = await service.search(
            "same query",
            source_scope="x",
            start_date="2026-07-01",
            end_date="2026-07-14",
        )

        self.assertFalse(web.cached)
        self.assertFalse(x.cached)
        self.assertTrue(cached_x.cached)
        self.assertEqual(len(session.calls), 2)

    async def test_responses_endpoint_is_derived_from_chat_endpoint(self):
        client = GrokClient(
            FakeSession(),
            api_base="https://grok.example/v1/chat/completions",
            api_key="key",
            model="grok-4.5",
            timeout_seconds=10,
        )

        self.assertEqual(client.endpoint, "https://grok.example/v1/chat/completions")
        self.assertEqual(client.responses_endpoint, "https://grok.example/v1/responses")

        responses_client = GrokClient(
            FakeSession(),
            api_base="https://grok.example/v1/responses",
            api_key="key",
            model="grok-4.5",
            timeout_seconds=10,
        )
        self.assertEqual(
            responses_client.endpoint,
            "https://grok.example/v1/chat/completions",
        )
        self.assertEqual(
            responses_client.responses_endpoint,
            "https://grok.example/v1/responses",
        )

    async def test_tavily_search_forwards_full_search_options(self):
        client = TavilyClient(FakeSession(), api_key="tvly-key", timeout_seconds=10)

        await client.search(
            "latest release",
            max_results=25,
            search_depth="advanced",
            topic="news",
            time_range="week",
            include_answer="advanced",
            include_raw_content=True,
            include_images=True,
            include_image_descriptions=True,
            include_favicon=True,
            include_domains=("example.com",),
            exclude_domains=("ads.example",),
            country="us",
            auto_parameters=True,
            exact_match=True,
        )

        payload = client.session.calls[0][1]
        self.assertEqual(payload["max_results"], 20)
        self.assertEqual(payload["search_depth"], "advanced")
        self.assertEqual(payload["topic"], "news")
        self.assertEqual(payload["time_range"], "week")
        self.assertEqual(payload["include_answer"], "advanced")
        self.assertTrue(payload["include_raw_content"])
        self.assertTrue(payload["include_images"])
        self.assertTrue(payload["include_image_descriptions"])
        self.assertTrue(payload["include_favicon"])
        self.assertEqual(payload["include_domains"], ["example.com"])
        self.assertEqual(payload["exclude_domains"], ["ads.example"])
        self.assertEqual(payload["country"], "us")
        self.assertTrue(payload["auto_parameters"])
        self.assertTrue(payload["exact_match"])

    async def test_crawl_and_research_are_tavily_only(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession()
        service._session = session

        crawl = json.loads(
            await service.tool_crawl(
                "https://docs.example",
                instructions="API reference",
                max_depth=2,
                max_breadth=7,
                limit=9,
                select_paths=["/api"],
                allow_external=False,
            )
        )
        self.assertEqual(crawl["status"], "ok")
        self.assertEqual(crawl["provider"], "tavily")
        crawl_payload = next(
            payload
            for url, payload, _headers in session.calls
            if url.endswith("/crawl")
        )
        self.assertEqual(crawl_payload["max_depth"], 2)
        self.assertEqual(crawl_payload["max_breadth"], 7)
        self.assertEqual(crawl_payload["limit"], 9)
        self.assertFalse(crawl_payload["allow_external"])
        self.assertEqual(crawl_payload["select_paths"], ["/api"])

        created = json.loads(
            await service.tool_research(
                "Compare the official API changes",
                model="pro",
                output_length="long",
                citation_format="apa",
                include_domains=["docs.example"],
            )
        )
        self.assertEqual(created["status"], "pending")
        self.assertEqual(created["provider"], "tavily")
        research_payload = next(
            payload
            for url, payload, _headers in session.calls
            if url.endswith("/research")
        )
        self.assertEqual(research_payload["model"], "pro")
        self.assertEqual(research_payload["output_length"], "long")
        self.assertEqual(research_payload["citation_format"], "apa")
        self.assertEqual(research_payload["include_domains"], ["docs.example"])

        status = json.loads(await service.tool_research_status(created["task_id"]))
        self.assertIn(status["status"], {"pending", "completed"})
        await service.close()

    def test_research_tasks_prune_expired_and_oldest_completed_results(self):
        service = SearchService(FakeContext(), self.settings())
        now = time.monotonic()
        expired = ResearchTask(
            task_id="expired",
            request_id="request-expired",
            created_at=now - RESEARCH_TASK_TTL_SECONDS - 10,
            status="completed",
            finished_at=now - RESEARCH_TASK_TTL_SECONDS - 1,
        )
        service._research_tasks[expired.task_id] = expired
        for index in range(RESEARCH_TASK_MAX_ITEMS + 1):
            task = ResearchTask(
                task_id=f"completed-{index}",
                request_id=f"request-{index}",
                created_at=now - 100 + index,
                status="completed",
                finished_at=now - 100 + index,
            )
            service._research_tasks[task.task_id] = task

        service._prune_research_tasks(now)

        self.assertNotIn("expired", service._research_tasks)
        self.assertEqual(len(service._research_tasks), RESEARCH_TASK_MAX_ITEMS)
        self.assertNotIn("completed-0", service._research_tasks)

    async def test_research_rejects_new_task_when_all_slots_are_running(self):
        service = SearchService(FakeContext(), self.settings())
        now = time.monotonic()
        for index in range(RESEARCH_TASK_MAX_ITEMS):
            task = ResearchTask(
                task_id=f"pending-{index}",
                request_id=f"request-{index}",
                created_at=now,
            )
            service._research_tasks[task.task_id] = task

        result = await service.research("需要研究的问题")

        self.assertEqual(result["status"], "error")
        self.assertIn("达到上限", result["error"])

    async def test_fetch_and_map_use_tavily_without_firecrawl(self):
        session = FakeSession()
        client = TavilyClient(session, api_key="tvly-key", timeout_seconds=10)
        content = await client.fetch("https://docs.example/page")
        links = await client.map(
            "https://docs.example",
            instructions="only API docs",
            max_depth=2,
            limit=10,
        )

        self.assertEqual(content, "# Page content")
        self.assertEqual(links[-1], "https://docs.example/api")
        self.assertFalse(
            any("firecrawl" in url.lower() for url, _payload, _headers in session.calls)
        )

    async def test_tavily_expands_same_day_range_for_provider_api(self):
        session = FakeSession()
        client = TavilyClient(session, api_key="tvly-key", timeout_seconds=10)

        await client.search(
            "today",
            max_results=5,
            start_date="2026-07-17",
            end_date="2026-07-17",
        )

        payload = next(
            payload
            for url, payload, _headers in session.calls
            if url.endswith("/search")
        )
        self.assertEqual(payload["start_date"], "2026-07-17")
        self.assertEqual(payload["end_date"], "2026-07-18")

    async def test_tavily_extract_error_keeps_failed_results(self):
        session = FakeSession(
            extract_payload={
                "results": [],
                "failed_results": [
                    {
                        "url": "https://news.example/article",
                        "error": "blocked by robots",
                    }
                ],
            }
        )
        client = TavilyClient(session, api_key="tvly-key", timeout_seconds=10)

        with self.assertRaises(TavilyExtractError) as raised:
            await client.fetch("https://news.example/article")

        self.assertIn("blocked by robots", str(raised.exception))
        self.assertEqual(
            raised.exception.failed_results,
            [
                {
                    "url": "https://news.example/article",
                    "error": "blocked by robots",
                }
            ],
        )

    async def test_fetch_cache_reuses_equivalent_url(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=60))
        session = FakeSession()
        service._session = session

        first = await service.fetch("https://NEWS.example:443/article/#section")
        call_count = len(session.calls)
        second = await service.fetch("https://news.example/article/")

        self.assertEqual(first["status"], "ok")
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(session.calls), call_count)
        await service.close()

    async def test_concurrent_equivalent_fetches_share_one_execution(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=60))
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def fetch_uncached(url, *, umo, cache_key):
            calls.append(url)
            started.set()
            await release.wait()
            return {
                "status": "ok",
                "url": url,
                "mode": "page_extract",
                "provider": "tavily",
                "providers": ["tavily"],
                "provider_mode": "tavily",
                "content": "共享正文",
                "cached": False,
            }

        service._fetch_uncached = fetch_uncached
        first_task = asyncio.create_task(
            service.fetch("https://NEWS.example:443/article/#part")
        )
        await started.wait()
        second_task = asyncio.create_task(
            service.fetch("https://news.example/article/")
        )
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(first_task, second_task)

        self.assertEqual(len(calls), 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["url"], "https://news.example/article/")
        await service.close()

    async def test_fetch_failure_automatically_uses_url_search_and_keeps_mode_in_cache(
        self,
    ):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=60))
        session = FakeSession(
            extract_payload={
                "results": [],
                "failed_results": [
                    {
                        "url": "https://news.example/article",
                        "error": "blocked by robots",
                    }
                ],
            }
        )
        service._session = session

        with (
            patch("core.search.service.logger.info"),
            patch("core.search.service.logger.debug") as debug_log,
            patch("core.search.service.logger.warning") as warning_log,
        ):
            first = await service.fetch("https://news.example/article")
            call_count = len(session.calls)
            second = await service.fetch("https://news.example/article")

        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["mode"], "search_fallback")
        self.assertEqual(first["providers"], ["tavily"])
        self.assertEqual(first["provider_mode"], "tavily")
        self.assertIn("Tavily fallback summary", first["content"])
        self.assertIn("blocked by robots", first["extract_error"])
        self.assertEqual(first["failed_results"][0]["error"], "blocked by robots")
        self.assertTrue(second["cached"])
        self.assertEqual(second["mode"], "search_fallback")
        self.assertEqual(second["providers"], first["providers"])
        self.assertEqual(len(session.calls), call_count)
        self.assertEqual(
            [call[0] for call in session.calls],
            [
                "https://api.tavily.com/extract",
                "https://api.tavily.com/search",
            ],
        )
        self.assertTrue(
            any(
                "网页读取失败：Tavily" in str(call.args[0])
                and "地址=https://news.example/article" in str(call.args[0])
                and "改用网址定向搜索" in str(call.args[0])
                for call in warning_log.call_args_list
            )
        )
        self.assertTrue(
            any(
                "网页读取完成：方式=定向搜索" in str(call.args[0])
                and "引擎=Tavily" in str(call.args[0])
                for call in debug_log.call_args_list
            )
        )
        await service.close()

    async def test_fetch_and_map_results_identify_tavily(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        service._session = FakeSession()

        fetched = await service.fetch("https://docs.example/page")
        mapped = await service.map("https://docs.example", instructions="API")

        self.assertEqual(fetched["mode"], "page_extract")
        self.assertEqual(fetched["provider"], "tavily")
        self.assertEqual(fetched["providers"], ["tavily"])
        self.assertEqual(fetched["provider_mode"], "tavily")
        self.assertEqual(mapped["provider"], "tavily")
        self.assertEqual(mapped["providers"], ["tavily"])
        self.assertEqual(mapped["provider_mode"], "tavily")
        await service.close()

    async def test_tavily_forwards_nonempty_urls_without_local_policy(self):
        session = FakeSession()
        client = TavilyClient(session, api_key="tvly-key", timeout_seconds=10)
        content = await client.fetch(" https://www.example.com/page ")

        self.assertTrue(content)
        self.assertEqual(
            session.calls[-1][1]["urls"],
            ["https://www.example.com/page"],
        )

    async def test_tavily_rejects_only_empty_urls_before_network_access(self):
        session = FakeSession()
        client = TavilyClient(session, api_key="tvly-key", timeout_seconds=10)

        with self.assertRaisesRegex(ValueError, "网页地址不能为空"):
            await client.fetch("  ")
        with self.assertRaisesRegex(ValueError, "网页地址不能为空"):
            await client.map("", instructions="", max_depth=1, limit=10)

        self.assertEqual(session.calls, [])

    async def test_service_forwards_non_public_urls_without_local_policy(self):
        service = SearchService(FakeContext(), self.settings(cache_ttl_seconds=0))
        session = FakeSession()
        service._session = session

        fetch_url = "http://127.0.0.1/private-page"
        map_url = "http://192.168.1.20/internal-site"
        fetched = await service.fetch(fetch_url)
        mapped = await service.map(map_url, limit=5)

        self.assertEqual(fetched["status"], "ok")
        self.assertEqual(mapped["status"], "ok")
        extract_payload = next(
            payload for url, payload, _headers in session.calls if url.endswith("/extract")
        )
        map_payload = next(
            payload for url, payload, _headers in session.calls if url.endswith("/map")
        )
        self.assertEqual(extract_payload["urls"], [fetch_url])
        self.assertEqual(map_payload["url"], map_url)
        await service.close()

    async def test_tool_replacement_only_applies_when_search_is_available(self):
        enabled = SearchService(FakeContext(), self.settings(tavily_api_keys=[]))
        toolset = FakeToolSet(
            [
                "web_search_tavily",
                "tavily_extract_web_page",
                "life_web_search",
                "life_web_fetch",
                "life_web_map",
            ]
        )
        enabled.prepare_tools(
            types.SimpleNamespace(func_tool=toolset),
            ["web_search_tavily", "tavily_extract_web_page"],
        )
        self.assertEqual(toolset.names, ["life_web_search"])

        disabled = SearchService(FakeContext(), self.settings(enabled=False))
        toolset = FakeToolSet(["web_search_tavily", "life_web_search"])
        disabled.prepare_tools(
            types.SimpleNamespace(func_tool=toolset),
            ["web_search_tavily"],
        )
        self.assertEqual(toolset.names, ["web_search_tavily"])

    async def test_tavily_keys_are_plugin_owned_and_rotate(self):
        service = SearchService(
            FakeContext(),
            self.settings(tavily_api_keys=["key-a", "key-b"]),
        )
        service._session = FakeSession()

        first = await service._tavily()
        second = await service._tavily()
        third = await service._tavily()

        self.assertEqual(first.api_key, "key-a")
        self.assertEqual(second.api_key, "key-b")
        self.assertEqual(third.api_key, "key-a")
        await service.close()

    async def test_grok_client_reads_responses_citations_without_text_parsing(self):
        client = GrokClient(
            FakeSession(),
            api_base="https://grok.example/v1",
            api_key="key",
            model="grok-4.5",
            timeout_seconds=10,
        )

        result = await client.search(
            "updates", source_scope="x", time_context="current time"
        )

        self.assertEqual(result.sources[0].title, "X post")
        self.assertEqual(result.sources[0].provider, "grok-x")
        self.assertEqual(result.search_calls, 1)
