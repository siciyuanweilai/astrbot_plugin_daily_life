import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from support import LifeSettings
from testsearch import FakeContext, FakeSession
from testsearchprotocol import response
from core.search.client import GrokClient
from core.search.model import SearchAnswer, SearchSource, provider_label
from core.search.service import SearchService


class GrokWebProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def parse(self, data, **kwargs):
        client = GrokClient(FakeSession(), api_base="https://grok.example/v1/responses",
                            api_key="test", model="grok-test", timeout_seconds=30)
        client._request_json = AsyncMock(return_value=data)
        answer = await client.search("官方网站", source_scope="web", **kwargs)
        return answer, client._request_json.call_args.args[1]

    async def test_web_request_and_all_structured_sources_are_preserved(self):
        data = response(output=[{
            "type": "web_search_call", "status": "completed",
            "action": {"sources": [{"url": "https://example.com", "title": "Official"}]},
        }], usage={"server_side_tool_usage_details": {"web_search_calls": 3}},
            citations=["https://example.com", "https://example.com/news"])
        answer, payload = await self.parse(data, start_date="2026-10-01", end_date="2026-10-31")
        self.assertEqual(payload["tools"], [{"type": "web_search"}])
        self.assertEqual(payload["include"], ["web_search_call.action.sources"])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["store"])
        self.assertIn("2026-10-01", payload["input"])
        self.assertIn("2026-10-31", payload["input"])
        self.assertTrue(answer.searched)
        self.assertEqual(answer.search_calls, 3)
        self.assertEqual(answer.effective_providers(), ["grok-web"])
        self.assertEqual([s.url for s in answer.sources], ["https://example.com", "https://example.com/news"])

    async def test_web_usage_or_native_tool_counts_without_invented_sources(self):
        for data in (
            response(usage={"server_side_tool_usage_details": {"web_search_calls": 4}}),
            response(output=[{"type": "custom_tool_call", "name": "web_search", "status": "completed"}]),
        ):
            answer, _ = await self.parse(data)
            self.assertTrue(answer.searched)
            self.assertGreater(answer.search_calls, 0)
            self.assertEqual(answer.sources, [])

    async def test_web_does_not_accept_unexecuted_or_other_scope_calls(self):
        for data in (
            response(text="I will search https://example.com"),
            response(output=[{"type": "x_search_call", "status": "completed",
                              "action": {"sources": [{"url": "https://x.com/a/status/1"}]}}],
                     usage={"server_side_tool_usage_details": {"x_search_calls": 3}}),
            response(output=[{"type": "web_search_call", "status": "failed"}]),
            response(citations=[{"url": "file:///private"}, {"url": "not a URL"}]),
        ):
            answer, _ = await self.parse(data)
            self.assertFalse(answer.searched)
            self.assertEqual(answer.sources, [])

    async def test_error_and_incomplete_response_cannot_be_accepted(self):
        for extra in ({"error": {"message": "failed"}}, {"status": "incomplete"}):
            data = response(citations=["https://example.com"])
            data.update(extra)
            answer, _ = await self.parse(data)
            self.assertFalse(answer.searched)
            self.assertTrue(answer.invalid_reason)


class GrokWebRoutingTest(unittest.IsolatedAsyncioTestCase):
    def service(self, *, session=None, **options):
        settings = LifeSettings.from_dict({"search_config": {
            "enabled": True, "provider": "grok-provider", "cache_ttl_seconds": 60,
            **options,
        }}).search
        service = SearchService(FakeContext(), settings)
        service._session = session or FakeSession()
        self.addAsyncCleanup(service.close)
        return service

    async def test_default_auto_searches_web_with_grok_without_tavily(self):
        service = self.service()
        result = await service.search("public query")
        cached = await service.search("public query")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.effective_providers(), ["grok-web"])
        self.assertEqual(result.as_dict()["provider_mode"], "grok-web")
        self.assertEqual(provider_label(result.effective_providers()), "Grok 网页")
        self.assertTrue(cached.cached)
        self.assertEqual(len(service._session.calls), 1)

    async def test_auto_falls_back_on_unavailable_or_unexecuted_grok(self):
        for session in (FakeSession(responses_status=400), FakeSession(responses_without_search=True)):
            service = self.service(session=session, tavily_api_keys=["test"])
            result = await service.search("public query")
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.effective_providers(), ["tavily"])
            self.assertEqual([url.rsplit("/", 1)[-1] for url, _, _ in session.calls], ["responses", "search"])

    async def test_auto_without_model_uses_tavily_and_explicit_engine_does_not_fallback(self):
        service = self.service(provider="", tavily_api_keys=["test"])
        result = await service.search("public query")
        self.assertEqual(result.effective_providers(), ["tavily"])
        fixed = self.service(web_provider="grok", tavily_api_keys=["test"], session=FakeSession(responses_status=400))
        result = await fixed.search("public query")
        self.assertEqual(result.status, "error")
        self.assertIn("Grok 网页", result.error)
        self.assertEqual(len(fixed._session.calls), 1)

    async def test_engine_change_does_not_reuse_other_engine_cache(self):
        service = self.service(tavily_api_keys=["test"])
        first = await service.search("public query")
        service.settings.web_provider = "tavily"
        second = await service.search("public query")
        self.assertEqual(first.effective_providers(), ["grok-web"])
        self.assertEqual(second.effective_providers(), ["tavily"])
        self.assertFalse(second.cached)

    async def test_both_uses_separate_tools_without_x_include(self):
        service = self.service()
        result = await service.search("public query", source_scope="both")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.effective_providers(), ["grok-x", "grok-web"])
        self.assertEqual(provider_label(result.effective_providers()), "Grok 网页+X")
        payloads = {p["tools"][0]["type"]: p for _, p, _ in service._session.calls}
        self.assertIn("include", payloads["web_search"])
        self.assertNotIn("include", payloads["x_search"])
        self.assertTrue(all(p["tool_choice"] == "required" for p in payloads.values()))

    async def test_x_only_never_substitutes_web_search(self):
        service = self.service(session=FakeSession(x_status=400), tavily_api_keys=["test"])
        result = await service.search("public query", source_scope="x")
        self.assertEqual(result.status, "error")
        self.assertEqual(len(service._session.calls), 1)
        self.assertEqual(service._session.calls[0][1]["tools"], [{"type": "x_search"}])

    async def test_tavily_structured_options_are_not_silently_ignored(self):
        for options in ({"include_images": True}, {"include_raw_content": True},
                        {"include_domains": "example.com"}, {"exact_match": True}):
            service = self.service(tavily_api_keys=["test"])
            result = await service.search("public query", **options)
            self.assertEqual(result.effective_providers(), ["tavily"])
            self.assertFalse(any(p.get("tools") for _, p, _ in service._session.calls))
            fixed = self.service(web_provider="grok", tavily_api_keys=["test"])
            rejected = await fixed.search("public query", **options)
            self.assertEqual(rejected.status, "error")
            self.assertIn("结构化搜索选项需要 Tavily", rejected.error)
            self.assertEqual(fixed._session.calls, [])

    async def test_both_keeps_completed_web_if_x_reaches_deadline(self):
        service = self.service()
        async def search(_client, _query, **kwargs):
            if kwargs["source_scope"] == "x":
                await asyncio.sleep(10)
            return SearchAnswer(content="verified", searched=True, search_calls=1, providers=["grok-web"],
                sources=[SearchSource(url="https://example.com", provider="grok-web")])
        service._grok_once = search
        result = await service._single_search(object(), "public query", source_scope="both", platform="",
            start_date="", end_date="", umo="", image_search=False, image_understanding=False,
            deadline=time.monotonic()+.03)
        self.assertEqual(result.content, "verified")
        self.assertEqual(result.effective_providers(), ["grok-web"])

    def test_invalid_or_missing_engine_keeps_backward_compatible_config(self):
        for value in (None, "invalid", [], {}):
            settings = LifeSettings.from_dict({"search_config": {"web_provider": value}}).search
            self.assertEqual(settings.web_provider, "auto")


if __name__ == "__main__":
    unittest.main()
