import asyncio
import json
import time
import unittest
from unittest.mock import patch

from support import LifeSettings
from testsearch import FakeContext, FakeResponse
from core.search.client import GrokClient
from core.search.model import SearchAnswer, SearchPlan, SearchSource
from core.search.query import SearchInput
from core.search.service import SearchService


class PlanningSession:
    def __init__(self, answer, status="completed"):
        self.answer, self.status, self.calls = answer, status, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs["json"]))
        return FakeResponse({"status": self.status, "output": [{
            "type": "message", "content": [{"type": "output_text", "text": self.answer}],
        }]})


class SearchPlanningTest(unittest.IsolatedAsyncioTestCase):
    def sources(self):
        return [SearchSource(url=f"https://x.com/official/status/{i}", provider="grok-x") for i in (1, 2)]

    def service(self):
        settings = LifeSettings.from_dict({"search_config": {
            "enabled": True, "provider": "grok-provider", "timeout_seconds": 30,
            "deep_max_followups": 2, "cache_ttl_seconds": 0,
        }}).search
        return SearchService(FakeContext(), settings)

    def request(self, service):
        return service._normalize_search_request(SearchInput(
            query="最近两条官方帖子及链接", depth="deep", source_scope="x",
            start_date="2026-10-01", end_date="2026-10-31",
        ))

    async def plan(self, answer, status="completed"):
        session = PlanningSession(json.dumps(answer) if not isinstance(answer, str) else answer, status)
        client = GrokClient(session, api_base="https://example.com/v1/responses", api_key="test", model="test", timeout_seconds=30)
        result = await client.plan("query", "verified findings", limit=2, sources=self.sources(), constraints={"source_scope": "x"})
        return result, session

    async def test_same_domain_answer_can_be_complete_via_responses(self):
        result, session = await self.plan({"status": "sufficient", "queries": [], "missing_aspects": [], "evidence_ids": [1, "2"]})
        self.assertEqual(result.status, "sufficient")
        self.assertEqual(result.evidence_urls, [item.url for item in self.sources()])
        url, payload = session.calls[0]
        self.assertTrue(url.endswith("/responses"))
        self.assertNotIn("messages", payload)
        self.assertNotIn("tools", payload)
        data = json.loads(payload["input"][1]["content"])
        self.assertEqual(data["constraints"], {"source_scope": "x"})
        self.assertEqual(len(data["sources"]), 2)

    async def test_invalid_assessments_do_not_claim_completion(self):
        for value in (
            "not JSON", [], {"status": []},
            {"status": "sufficient", "queries": [], "missing_aspects": [], "evidence_ids": [99]},
            {"status": "sufficient", "queries": [], "missing_aspects": [], "evidence_ids": []},
            {"status": "sufficient", "queries": [], "missing_aspects": ["gap"], "evidence_ids": [1]},
            {"status": "needs_search", "queries": ["q"], "missing_aspects": [], "evidence_ids": [1]},
            {"status": "sufficient", "queries": [], "missing_aspects": [], "evidence_ids": [True]},
            {"status": "needs_search", "queries": [123], "missing_aspects": ["gap"], "evidence_ids": [1]},
        ):
            with self.subTest(value=value):
                result, _ = await self.plan(value)
                self.assertEqual(result.status, "unknown")
                self.assertEqual(result.queries, [])

    async def test_unfinished_response_is_rejected(self):
        with self.assertRaises(ValueError):
            await self.plan({"status": "sufficient", "queries": [], "missing_aspects": [], "evidence_ids": [1]}, status="incomplete")

    async def test_request_satisfied_stops_and_returns_coverage(self):
        service = self.service()
        owner = self

        class Planner:
            async def plan(self, query, context, **kwargs):
                owner.assertEqual(kwargs["constraints"]["source_scope"], "x")
                owner.assertEqual(kwargs["constraints"]["start_date"], "2026-10-01")
                return SearchPlan(status="sufficient", evidence_urls=[item.url for item in kwargs["sources"]])

        calls = []
        async def initial(*args, **kwargs):
            calls.append(args[1])
            return SearchAnswer(content="two official posts", sources=self.sources(), searched=True)

        service._search_once_request = initial
        result = await service._execute_search(self.request(service), Planner(), deadline=time.monotonic() + 90)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.missing_aspects, [])
        self.assertEqual(result.quality, "partial")  # Diversity is not task completion.
        payload = service._deep_tool_payload(result)
        self.assertEqual(payload["assessment"]["status"], "sufficient")
        self.assertIn("停止重复搜索", payload["search_guidance"])

    async def test_diverse_sources_do_not_hide_unanswered_question(self):
        service = self.service()
        sources = [SearchSource(url=f"https://site{i}.example/report", score=0.9) for i in range(3)]
        owner = self

        class Planner:
            calls = 0
            async def plan(self, query, context, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return SearchPlan(status="needs_search", queries=["missing date"], missing_aspects=["date"])
                return SearchPlan(status="sufficient", evidence_urls=[kwargs["sources"][-1].url])

        calls = []
        async def followup(_client, query, *_args, **kwargs):
            calls.append(query)
            return SearchAnswer(content="date verified", sources=[SearchSource(url="https://site4.example/date")], searched=True)
        service._search_once_request = followup
        assessment = {}
        sections, _ = await service._deep_search_sections(Planner(), self.request(service), "partial answer", sources, deadline=time.monotonic() + 90, assessment=assessment)
        self.assertEqual(calls, ["missing date"])
        self.assertEqual(assessment["status"], "sufficient")
        self.assertEqual(len(sections), 1)

    async def test_budget_uses_config_and_reserves_followup_time(self):
        service = self.service()
        class Planner:
            async def plan(self, *args, **kwargs):
                return SearchPlan(status="unknown")
        budgets = []
        async def record(coro, *, timeout):
            budgets.append(timeout)
            return await coro
        with patch("core.search.service.asyncio.wait_for", record):
            await service._plan_deep_followups(Planner(), self.request(service), "findings", self.sources(), limit=2, deadline=time.monotonic() + 180)
            await service._plan_deep_followups(Planner(), self.request(service), "findings", self.sources(), limit=2, deadline=time.monotonic() + 25)
        self.assertEqual(budgets[0], 30)
        self.assertGreater(budgets[1], 4)
        self.assertLessEqual(budgets[1], 5)

    async def test_timeout_preserves_initial_result_and_is_not_no_followups(self):
        service = self.service()
        service.settings.timeout_seconds = 0.03
        cancelled = []
        class Planner:
            async def plan(self, *args, **kwargs):
                try:
                    await asyncio.sleep(10)
                finally:
                    cancelled.append(True)
        async def initial(*args, **kwargs):
            return SearchAnswer(content="kept", sources=self.sources(), searched=True)
        service._search_once_request = initial
        result = await service._execute_search(self.request(service), Planner(), deadline=time.monotonic() + 2)
        self.assertEqual(result.content, "kept")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.metadata["search_assessment"]["reason"], "timeout")
        self.assertEqual(cancelled, [True])
        self.assertIn("不要自动重复", service._tool_search_guidance(result))

    async def test_insufficient_budget_skips_planner(self):
        service = self.service()
        class Planner:
            async def plan(self, *args, **kwargs):
                raise AssertionError("no budget")
        result = await service._plan_deep_followups(Planner(), self.request(service), "kept", self.sources(), limit=2, deadline=time.monotonic() + 10)
        self.assertEqual(result.reason, "insufficient_budget")

    async def test_no_extra_assessment_after_followup_budget_is_used(self):
        service = self.service()
        service.settings.deep_max_followups = 1
        class Planner:
            calls = 0
            async def plan(self, *args, **kwargs):
                self.calls += 1
                if self.calls > 1:
                    raise AssertionError("No additional planning without search slots")
                return SearchPlan(status="needs_search", queries=["verify date"], missing_aspects=["date"])
        planner = Planner()
        async def followup(*args, **kwargs):
            return SearchAnswer(content="date verified", sources=[SearchSource(url="https://example.com/date")], searched=True)
        service._search_once_request = followup
        assessment = {}
        sections, _ = await service._deep_search_sections(planner, self.request(service), "initial", self.sources(), deadline=time.monotonic() + 90, assessment=assessment)
        self.assertEqual(planner.calls, 1)
        self.assertEqual(assessment["status"], "supplemented")
        self.assertEqual(len(sections), 1)

    async def test_cancellation_propagates(self):
        service = self.service()
        class Planner:
            async def plan(self, *args, **kwargs):
                raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await service._plan_deep_followups(Planner(), self.request(service), "kept", self.sources(), limit=2, deadline=time.monotonic() + 90)
