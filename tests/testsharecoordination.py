"""结构化分享投影、跨插件主动联系及成品等待边界。"""
import json
import types
import unittest
from unittest.mock import AsyncMock, patch

import support
from core.models import DayRecord, TimelineItem
from core.runtime.mirror.sharefacts import project
from core.runtime.sharegate import ShareContactGate
from core.runtime.sharepacing import coordinate
import testproactivedelivery as delivery_fixtures
import testbilidynamic as dynamic_fixtures
from core.browsing.dynamic import DynamicPublisher


class ProjectionTest(unittest.TestCase):
    def setUp(self):
        self.steps = [
            {"action_id": "a", "timeline_index": 0, "payload": {"execution_chain_id": "trip"}},
            {"action_id": "b", "timeline_index": 1, "payload": {"execution_chain_id": "trip", "depends_on_action_ids": ["a"]}},
        ]
        self.day = DayRecord(date="2026-11-01", outfit="蓝色外套", timeline=[
            TimelineItem(activity="准备", execution_state="completed"), TimelineItem(activity="出发")])
        self.day.meta["planned_life_actions"] = json.dumps(self.steps)

    def test_only_all_successful_receipts_complete_an_episode(self):
        self.day.meta["life_action_settlements"] = json.dumps({"a": {"status": "committed"}, "b": {"status": "failed"}})
        value = project(self.day)
        self.assertEqual(len(value["action_progress"]), 1)
        self.assertEqual(value["action_progress"][0]["state"], "pending")
        self.assertEqual(value["action_progress"][0]["completed_steps"], 1)
        self.day.meta["life_action_settlements"] = json.dumps({key: {"status": "committed"} for key in ("a", "b")})
        self.assertEqual(project(self.day)["action_progress"][0]["state"], "completed")

    def test_reorder_rename_and_receipt_timestamp_keep_identity(self):
        first = project(self.day)
        self.steps.reverse()
        self.day.meta["planned_life_actions"] = json.dumps(self.steps)
        self.day.timeline[0].activity = "另一个描述"
        self.day.timeline[0].execution_updated_at = "2026-11-01T12:00:00"
        self.assertEqual(first["action_progress_version"], project(self.day)["action_progress_version"])
        self.assertEqual(first["action_episodes"], project(self.day)["action_episodes"])

    def test_dependency_and_timeline_links_merge_without_chain_label(self):
        self.steps[0]["payload"] = {}
        self.steps[1]["payload"] = {"depends_on_action_ids": ["a"]}
        self.steps.append({"action_id": "c", "timeline_index": 1, "payload": {"execution_chain_id": "different"}})
        self.day.meta["planned_life_actions"] = json.dumps(self.steps)
        self.assertEqual(len(project(self.day)["action_progress"]), 1)

    def test_cancelled_or_unobserved_does_not_claim_completion(self):
        self.day.meta["continuous_execution"] = json.dumps({"action": {"action_id": "b"}, "status": "running", "observation_state": "unknown"})
        self.assertEqual(project(self.day)["action_progress"][0]["state"], "pending")
        self.day.timeline[1].execution_state = "cancelled"
        self.assertEqual(project(self.day)["action_progress"][0]["state"], "cancelled")

    def test_corrupt_optional_receipts_and_run_are_not_facts(self):
        self.day.meta.update(life_action_settlements='{"a": "bad"}', continuous_execution='{"action": "bad"}')
        value = project(self.day)
        self.assertEqual(value["action_progress"][0]["completed_steps"], 0)
        self.assertNotIn("payload", json.dumps(value["action_progress"]))

    def test_planned_style_reason_and_clock_do_not_change_actual_appearance(self):
        before = project(self.day)["appearance_version"]
        self.day.meta.update(style="清新", outfit_reason="新的理由", planned_outfit="红裙", updated_at="later")
        self.assertEqual(before, project(self.day)["appearance_version"])
        for key in ("hair", "makeup", "nails"):
            old = project(self.day)["appearance_version"]
            self.day.meta[key] = "实际变化"
            self.assertNotEqual(old, project(self.day)["appearance_version"])
        old = project(self.day)["appearance_version"]
        self.day.outfit = "灰色外套"
        self.assertNotEqual(old, project(self.day)["appearance_version"])


class PublicContractTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.method = AsyncMock(return_value={"ready": True, "retry_after": 0})
        self.runtime = types.SimpleNamespace(context=types.SimpleNamespace(get_all_stars=lambda: [
            types.SimpleNamespace(name="astrbot_plugin_daily_share", activated=True,
                star_cls=types.SimpleNamespace(coordinate_autonomous_contact=self.method))]))

    async def test_only_contact_time_crosses_private_boundary(self):
        await coordinate(self.runtime, "qq:FriendMessage:test", rhythm={"last_received_at": 123, "private_text": "excluded"})
        self.assertEqual(self.method.await_args.kwargs["rhythm"], {"last_received_at": 123})
        await coordinate(self.runtime, "bilibili_broadcast", rhythm={"last_received_at": 123})
        self.assertEqual(self.method.await_args.kwargs["rhythm"], {})

    async def test_optional_service_absence_and_temporary_failure(self):
        self.assertTrue((await coordinate(types.SimpleNamespace(), "private:a"))["ready"])
        self.method.side_effect = RuntimeError("unavailable")
        self.assertFalse((await coordinate(self.runtime, "private:a", rhythm={}))["ready"])
        self.method.side_effect = None
        self.method.return_value = {"unexpected": True}
        self.assertFalse((await coordinate(self.runtime, "private:a", rhythm={}))["ready"])

    async def test_actual_appearance_invalidates_snapshot_but_planned_style_does_not(self):
        day = DayRecord(date="2026-11-01", outfit="蓝外套")
        self.runtime.archive = types.SimpleNamespace(get_day=AsyncMock(return_value=day),
            get_continuous_life=AsyncMock(return_value={}), get_conversation_receipt_before=AsyncMock(return_value=0))
        gate = ShareContactGate(self.runtime)
        first, _ = await gate.snapshot("private:a")
        day.meta["planned_outfit"] = "红外套"
        self.assertEqual(first, (await gate.snapshot("private:a"))[0])
        day.outfit = "红外套"
        permit = await gate.coordinate("private:a", owner="share:1", expected=first)
        self.assertTrue(permit["stale"])


class DeliveryContractTest(unittest.IsolatedAsyncioTestCase):
    setUp = delivery_fixtures.ProactiveDeliveryTest.setUp
    send = delivery_fixtures.ProactiveDeliveryTest.send

    async def test_explicit_commitment_bypasses_autonomy_gate(self):
        with patch("core.runtime.proactive.send.coordinate_autonomy", new=AsyncMock(return_value={"ready": False})) as gate:
            await self.send()
        gate.assert_not_awaited()
        self.context._send_segmented_proactive_message.assert_awaited_once()

    async def test_deferred_new_contact_does_not_start_transport(self):
        self.payload["source"] = "private_revisit"
        with patch("core.runtime.proactive.send.coordinate_autonomy", new=AsyncMock(return_value={"ready": False})):
            self.assertFalse(await self.send())
        self.assertEqual(self.payload["_delivery"]["status"], "deferred")
        self.context._send_segmented_proactive_message.assert_not_awaited()

    async def test_segments_claim_once(self):
        self.payload["source"] = "proactive_reply"
        async def deliver(*args, on_sent, **kwargs):
            on_sent("第一段")
            on_sent("第二段")
            return True
        self.context._send_segmented_proactive_message.side_effect = deliver
        with patch("core.runtime.proactive.send.coordinate_autonomy", new=AsyncMock(return_value={"ready": True})) as gate:
            self.assertTrue(await self.send())
        gate.assert_awaited_once()

    async def test_voice_invitation_is_also_coordinated(self):
        self.context.send_background_text = AsyncMock(return_value=True)
        with patch("core.runtime.proactive.send.coordinate_autonomy", new=AsyncMock(return_value={"ready": False})):
            self.assertFalse(await self.component._send_proactive_voice_call_invite("private:a", "一起聊聊", "https://example.test/call"))
        self.context.send_background_text.assert_not_awaited()


class UploadWaitTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = dynamic_fixtures.DynamicTest.asyncSetUp
    asyncTearDown = dynamic_fixtures.DynamicTest.asyncTearDown
    picture = dynamic_fixtures.DynamicTest.picture

    async def test_upload_wait_resumes_same_images_without_duplicate_upload(self):
        self.platform.upload_dynamic_picture = AsyncMock(return_value={"img_src": "https://i0.hdslb.com/bfs/new_dyn/wait.jpg", "img_width": 24, "img_height": 32})
        draft = {"intent": "wait:1", "source_id": "source", "text": "真实近况", "images": [self.picture()], "autonomous": True}
        with patch("core.browsing.dynamic.BiliPlatform", return_value=self.platform), patch(
                "core.browsing.dynamic.coordinate_autonomy", new=AsyncMock(side_effect=[{"ready": False}, {"ready": True}])):
            first = await DynamicPublisher(self.social).publish(self.store, draft, AsyncMock())
            self.assertEqual((first["status"], first["phase"]), ("sending", "preparing"))
            self.platform.dynamic.assert_not_awaited()
            second = await DynamicPublisher(self.social).publish(self.store, draft, AsyncMock())
        self.assertEqual(second["status"], "accepted")
        self.platform.upload_dynamic_picture.assert_awaited_once()
        self.platform.dynamic.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
