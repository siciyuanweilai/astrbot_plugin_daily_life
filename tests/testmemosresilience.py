"""外部记忆受理、取消、去重和诊断；不连接真实服务或写入用户数据。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from support import LifeSettings
from core.memos import HostedMemOSClient, HostedMemOSService, MemOSClientResult
from core.runtime.spine.boot import SpineBootComponent


class MemOSHTTPTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return HostedMemOSClient(base_url="https://example.invalid", api_key="private-token", timeout_seconds=30)

    def session(self, *, error=None, body=None, status=200, waiting=False):
        response = SimpleNamespace(status=status, request_info=None, history=())

        async def read():
            if waiting:
                await asyncio.Event().wait()
            return body if body is not None else {"code": 0, "data": {"success": True, "status": "running"}}

        response.json = AsyncMock(side_effect=read)

        @asynccontextmanager
        async def request(*args, **kwargs):
            if error:
                raise error
            yield response

        return SimpleNamespace(post=Mock(side_effect=request), closed=False, close=AsyncMock())

    async def test_empty_timeout_has_operation_budget_and_phase(self):
        client = self.client()
        client._session = self.session(error=TimeoutError())
        result = await client.add_message({"messages": [{"content": "private-memory"}]})
        self.assertFalse(result.success)
        for value in ("TimeoutError", "/add/message", "timeout=10s", "elapsed=", "phase=connect_or_response_headers"):
            self.assertIn(value, result.error)
        self.assertNotIn("private", result.error)
        self.assertEqual(client._session.post.call_count, 1)
        await client.close()

    async def test_response_body_is_in_total_deadline(self):
        client = self.client()
        client.timeout_seconds = 0.01
        client._session = self.session(waiting=True)
        result = await asyncio.wait_for(client.add_message({}), 0.3)
        self.assertFalse(result.success)
        self.assertIn("TimeoutError", result.error)
        self.assertIn("phase=response_json", result.error)
        await client.close()

    async def test_error_details_do_not_echo_credentials_or_response_body(self):
        for session in (
            self.session(error=RuntimeError("private-token private-memory")),
            self.session(status=401, body={"message": "private-memory"}),
            self.session(body={"code": 402, "message": "private-token"}),
            self.session(body={"code": 0, "data": {"success": False, "message": "private-memory"}}),
            self.session(body=["private-memory"]),
        ):
            client = self.client()
            client._session = session
            result = await client.add_message({})
            self.assertFalse(result.success)
            self.assertNotIn("private", result.error)
            self.assertIn("/add/message", result.error)
            await client.close()

    async def test_http_status_is_available_without_reading_error_body(self):
        client = self.client()
        session = self.session(status=503)
        client._session = session
        result = await client.add_message({})
        self.assertIn("HTTP=503", result.error)
        await client.close()

    async def test_reuses_session_and_releases_it_on_close(self):
        client = self.client()
        session = self.session()
        with (
            patch("core.memos.connector.aiohttp.ClientSession", return_value=session) as create,
            patch("core.memos.connector.aiohttp.TCPConnector", create=True),
            patch("core.memos.connector.aiohttp.DummyCookieJar", create=True),
        ):
            self.assertTrue((await client.add_message({})).success)
            self.assertTrue((await client.search_memory({})).success)
            self.assertEqual(create.call_count, 1)
        await client.close()
        session.close.assert_awaited_once()
        self.assertFalse((await client.add_message({})).success)

    async def test_cancellation_propagates_without_retry(self):
        client = self.client()
        session = self.session(waiting=True)
        client._session = session
        task = asyncio.create_task(client.add_message({}))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(session.post.call_count, 1)
        await client.close()


class MemOSSubmissionTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        service = HostedMemOSService(LifeSettings.from_dict({"memos_config": {
            "enabled": True, "api_key": "key", "sync_selected_memory": True,
        }}).memos)
        service.client = SimpleNamespace(
            available=True,
            add_message=AsyncMock(return_value=MemOSClientResult(True, data={"status": "running"})),
            close=AsyncMock(),
        )
        return service

    async def submit(self, service, user="one", items=None):
        return await service.sync_selected_items(
            meta={"session_id": user}, reason="confirmed",
            items=items or ["already saved locally"],
        )

    async def test_same_payload_shares_submission_but_other_scopes_do_not(self):
        service = self.service()
        result = await asyncio.gather(self.submit(service), self.submit(service))
        self.assertEqual(result, [True, True])
        self.assertTrue(await self.submit(service))
        self.assertEqual(service.client.add_message.await_count, 1)
        self.assertTrue(service.client.add_message.await_args.args[0]["async_mode"])
        self.assertTrue(await self.submit(service, user="two"))
        self.assertTrue(await self.submit(service, items=["different fact"]))
        self.assertEqual(service.client.add_message.await_count, 3)
        await service.close()

    async def test_failure_backoff_expires_and_success_resets_it(self):
        service = self.service()
        service.client.add_message.return_value = MemOSClientResult(False, error="TimeoutError")
        with patch("core.memos.hosted.time.monotonic", return_value=100):
            self.assertFalse(await self.submit(service))
            self.assertFalse(await self.submit(service))
            self.assertEqual(service.client.add_message.await_count, 1)
        with patch("core.memos.hosted.time.monotonic", return_value=131):
            self.assertFalse(await self.submit(service))
        with patch("core.memos.hosted.time.monotonic", return_value=180):
            self.assertFalse(await self.submit(service))
            self.assertEqual(service.client.add_message.await_count, 2)
        with patch("core.memos.hosted.time.monotonic", return_value=192):
            service.client.add_message.return_value = MemOSClientResult(True)
            self.assertTrue(await self.submit(service))
            self.assertEqual(service.client.add_message.await_count, 3)
        await service.close()

    async def test_cancelled_waiter_does_not_cancel_shared_write(self):
        service = self.service()
        started, release = asyncio.Event(), asyncio.Event()

        async def write(payload):
            started.set()
            await release.wait()
            return MemOSClientResult(True)

        service.client.add_message.side_effect = write
        first = asyncio.create_task(self.submit(service))
        await started.wait()
        second = asyncio.create_task(self.submit(service))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertTrue(await second)
        self.assertEqual(service.client.add_message.await_count, 1)
        self.assertEqual(service._pending_writes, {})
        await service.close()

    async def test_runtime_close_cancels_writes_and_closes_client(self):
        service = self.service()
        async def slow(payload):
            await asyncio.Event().wait()

        service.client.add_message.side_effect = slow
        task = asyncio.create_task(self.submit(service))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await SpineBootComponent._close_runtime_component(service, "MemOS")
        with self.assertRaises(asyncio.CancelledError):
            await task
        service.client.close.assert_awaited_once()
        self.assertEqual(service._pending_writes, {})

    async def test_recent_submission_cache_is_bounded(self):
        service = self.service()
        for index in range(270):
            await self.submit(service, user=str(index))
        self.assertLessEqual(len(service._recent_writes), 256)
        await service.close()
