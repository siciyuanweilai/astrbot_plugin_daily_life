import unittest
from unittest.mock import AsyncMock

from support import LifeSettings
from testsearch import FakeContext, FakeSession
from core.search.client import GrokClient
from core.search.service import SearchService


def response(*, text="Retrieved result", output=None, usage=None, citations=None, status="completed"):
    data = {
        "status": status,
        "output": [*(output or []), {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
    }
    if usage is not None:
        data["usage"] = usage
    if citations is not None:
        data["citations"] = citations
    return data


class GrokProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def parse(self, data):
        client = GrokClient(FakeSession(), api_base="https://grok.example/v1",
                            api_key="test", model="grok-test", timeout_seconds=30)
        client._request_json = AsyncMock(return_value=data)
        answer = await client.search("public query")
        return answer, client

    async def test_search_auto_choice_still_rejects_unexecuted_text(self):
        answer, client = await self.parse(response(text="invoke x_search with query is public query"))
        client._request_json.assert_awaited_once()
        payload = client._request_json.call_args.args[1]
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertEqual(payload["tools"], [{"type": "x_search"}])
        self.assertFalse(answer.searched)

    async def test_usage_only_execution_is_recognized_without_inventing_sources(self):
        answer, _ = await self.parse(response(usage={
            "server_side_tool_usage_details": {"x_search_calls": 4},
        }))
        self.assertTrue(answer.searched)
        self.assertEqual(answer.search_calls, 4)
        self.assertEqual(answer.sources, [])

    async def test_usage_and_output_counts_are_not_added(self):
        answer, _ = await self.parse(response(
            output=[{"type": "x_search_call", "status": "completed"}],
            usage={"server_side_tool_usage_details": {"x_search_calls": 1}},
        ))
        self.assertEqual(answer.search_calls, 1)

    async def test_native_x_execution_items_are_recognized(self):
        for name in ["x_keyword_search", "x_semantic_search", "x_user_search", "x_thread_fetch"]:
            with self.subTest(name=name):
                answer, _ = await self.parse(response(output=[{
                    "type": "custom_tool_call", "name": name, "status": "completed",
                }]))
                self.assertTrue(answer.searched)
                self.assertEqual(answer.search_calls, 1)

    async def test_other_tools_and_invalid_counts_are_not_x_execution(self):
        for count in [None, -1, True, "4", 1.5]:
            with self.subTest(count=count):
                answer, _ = await self.parse(response(
                    text="invoke x_search with query is public query https://x.com/example/status/123",
                    output=[{"type": "custom_tool_call", "name": "web_search", "status": "completed"}],
                    usage={"num_server_side_tools_used": 4, "server_side_tool_usage_details": {
                        "x_search_calls": count, "web_search_calls": 4,
                    }},
                ))
                self.assertFalse(answer.searched)
                self.assertEqual(answer.sources, [])
                self.assertTrue(answer.invalid_reason)

    async def test_failed_tool_calls_are_not_success(self):
        for kind, name in [("x_search_call", None), ("custom_tool_call", "x_keyword_search")]:
            with self.subTest(kind=kind):
                answer, _ = await self.parse(response(output=[{
                    "type": kind, "name": name, "status": "failed",
                }]))
                self.assertFalse(answer.searched)

        answer, _ = await self.parse(response(output=[{
            "type": "x_search_call", "status": "failed",
            "action": {"sources": [{"url": "https://x.com/example/status/123"}]},
        }]))
        self.assertFalse(answer.searched)
        self.assertEqual(answer.sources, [])

    async def test_string_citations_are_structured_evidence_and_deduplicated(self):
        url = "https://x.com/example/status/123"
        answer, _ = await self.parse(response(citations=[url, {"url": url}, "not a URL", "file:///private"]))
        self.assertTrue(answer.searched)
        self.assertEqual([source.url for source in answer.sources], [url])

    async def test_incomplete_response_is_rejected_despite_execution(self):
        answer, _ = await self.parse(response(status="incomplete", usage={
            "server_side_tool_usage_details": {"x_search_calls": 2},
        }))
        self.assertFalse(answer.searched)
        self.assertIn("incomplete", answer.invalid_reason)

    async def test_empty_answer_is_not_usable_even_after_execution(self):
        answer, _ = await self.parse(response(text="", usage={
            "server_side_tool_usage_details": {"x_search_calls": 2},
        }))
        self.assertFalse(answer.searched)
        self.assertEqual(answer.invalid_reason, "正文为空")

    async def test_errors_identify_selected_scope_and_preserve_cause(self):
        for scope, present, absent in [("x", "Grok X", "Tavily"), ("web", "Tavily", "Grok X"), ("both", "Grok X", None)]:
            with self.subTest(scope=scope):
                settings = LifeSettings.from_dict({"search_config": {
                    "enabled": True, "provider": "grok-provider", "cache_ttl_seconds": 0,
                }}).search
                service = SearchService(FakeContext(), settings)
                service._session = FakeSession(responses_without_search=True, search_payload={"results": []})
                result = await service.search("query", source_scope=scope)
                self.assertEqual(result.status, "error")
                self.assertIn(present, result.error)
                if absent:
                    self.assertNotIn(absent, result.error)
                if scope in {"x", "both"}:
                    self.assertIn("搜索执行凭据", result.error)
                self.assertEqual(result.content, "")
                await service.close()


if __name__ == "__main__":
    unittest.main()
