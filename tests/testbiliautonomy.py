"""真实 SQLite 验证内容发现与独立重评；所有网络和账号写入均为模拟。"""
import datetime as dt
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

import testbilibrowsing as browsing_fixture
import testbilisocial as social_fixture
from core.browsing.bookmarks import record_id
from core.browsing.feed import BiliReadError
from core.browsing.opportunities import due, observation, review_patch
from core.browsing.socializing import BiliInteractionDeferred
from core.browsing.surfing import stamp


class DiscoveryTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = browsing_fixture.BrowsingTest.asyncSetUp
    asyncTearDown = browsing_fixture.BrowsingTest.asyncTearDown
    metadata = browsing_fixture.BrowsingTest.metadata
    record = browsing_fixture.BrowsingTest.record

    async def test_can_choose_real_candidate_in_first_decision_without_claiming_viewing(self):
        self.service.reader.candidates.return_value = [self.metadata()]
        self.service.reader.metadata = AsyncMock(return_value=self.metadata())
        self.service._model.return_value = {"action": "watch", "id": record_id(self.metadata()["bvid"])}
        await self.service.tick()
        self.service._model.assert_awaited_once()
        context = self.service._model.await_args.args[1]
        self.assertEqual(context["candidates"][0]["title"], self.metadata()["title"])
        record = (await self.store.list())[0]
        self.assertEqual((record["position"], record["status"], record["evidence"]), (0, "watching", []))
        self.assertEqual(record["impression"], "")

    async def test_wait_respects_choice_and_stable_candidates_do_not_wake_it(self):
        self.service.reader.candidates.return_value = [self.metadata()]
        self.service._model.return_value = {"action": "wait", "reason": "想先看书", "review_minutes": 120}
        await self.service.tick()
        self.now += dt.timedelta(minutes=6)
        await self.service.tick()
        self.service.reader.candidates.assert_awaited_once()
        self.service._model.assert_awaited_once()
        self.assertEqual(await self.store.list(), [])
        self.assertEqual((await self.store.state())["last_reason"], "想先看书")

    async def test_refreshed_content_can_wake_long_wait_without_forcing_watch(self):
        self.service.reader.candidates.return_value = [self.metadata()]
        self.service._model.return_value = {"action": "wait", "review_minutes": 120}
        await self.service.tick()
        self.now += dt.timedelta(minutes=16)
        self.service._discovery_cache = {key: (at - 901, values)
                                        for key, (at, values) in self.service._discovery_cache.items()}
        self.service.reader.candidates.return_value = [{**self.metadata(), "bvid": browsing_fixture.BV2}]
        await self.service.tick()
        self.assertEqual(self.service._model.await_count, 2)
        self.assertEqual(await self.store.list(), [])

    async def test_stable_refresh_does_not_override_wait(self):
        self.service.reader.candidates.return_value = [self.metadata()]
        self.service._model.return_value = {"action": "wait", "review_minutes": 120}
        await self.service.tick()
        self.now += dt.timedelta(minutes=16)
        self.service._discovery_cache = {key: (at - 901, values)
                                        for key, (at, values) in self.service._discovery_cache.items()}
        await self.service.tick()
        self.assertEqual(self.service.reader.candidates.await_count, 2)
        self.service._model.assert_awaited_once()

    async def test_failed_discovery_obeys_backoff_instead_of_retrying_each_tick(self):
        self.service.reader.candidates.side_effect = BiliReadError("上游暂时不可用")
        await self.service.tick()
        self.now += dt.timedelta(minutes=6)
        await self.service.tick()
        self.service.reader.candidates.assert_awaited_once()
        self.service._model.assert_not_awaited()
        self.now += dt.timedelta(minutes=25)
        self.service.reader.candidates.side_effect = None
        self.service.reader.candidates.return_value = []
        self.service._model.return_value = {"action": "wait"}
        await self.service.tick()
        self.assertEqual(self.service.reader.candidates.await_count, 2)
        self.service._model.assert_awaited_once()

    async def test_discovery_interrupted_by_chat_cannot_reopen_browsing(self):
        async def discover(*args, **kwargs):
            await self.service.incoming(types.SimpleNamespace(message_str="一起聊会", scope="private:a"))
            return [self.metadata()]
        self.service.reader.candidates.side_effect = discover
        await self.service.tick()
        self.service._model.assert_not_awaited()
        self.assertEqual(await self.store.list(), [])
        self.assertFalse(self.service._discovery_cache)

    async def test_sleep_during_metadata_prevents_starting_a_video(self):
        self.service.reader.candidates.return_value = [self.metadata()]
        self.service._model.return_value = {"action": "watch", "id": record_id(self.metadata()["bvid"])}
        async def metadata(*args):
            self.world["sleeping"] = True
            return self.metadata()
        self.service.reader.metadata = AsyncMock(side_effect=metadata)
        await self.service.tick()
        self.assertEqual(await self.store.list(), [])

    async def test_busy_or_sleeping_does_not_even_fetch_candidates(self):
        for world in ({"sleeping": True}, {"run": {"status": "running", "action": {"action_type": "work"}}}):
            self.world.update(world)
            await self.service.tick()
            self.service.reader.candidates.assert_not_awaited()
            self.service._model.assert_not_awaited()
            self.world.pop("sleeping", None)

    async def test_selection_reason_replaces_earlier_search_intention(self):
        self.service.reader.candidates.return_value = [self.metadata()]
        self.service._model.side_effect = [
            {"action": "choose", "reason": "想找点新东西"},
            {"action": "wait", "reason": "这些内容暂时不感兴趣", "review_minutes": 17},
        ]
        await self.service.tick()
        state = await self.store.state()
        self.assertEqual(state["last_reason"], "这些内容暂时不感兴趣")
        self.assertEqual(state["last_action"], "wait")
        self.assertEqual(state["next_at"], stamp(self.now + dt.timedelta(minutes=17)))
        self.service.reader.candidates.assert_awaited_once()

    async def test_seen_videos_are_not_new_candidates(self):
        await self.record(status="completed", position=300)
        self.service.reader.candidates.return_value = [self.metadata()]
        self.assertEqual(await self.service._discover(await self.store.list()), [])

    async def test_evening_context_keeps_current_schedule(self):
        self.day.timeline = [types.SimpleNamespace(as_dict=lambda i=i: {"index": i}) for i in range(40)]
        self.world["run"] = {"action": {"timeline_index": 33}}
        context = await self.service._context(self.day, self.world, {})
        self.assertIn({"index": 33}, context["schedule"])
        self.assertIn({"index": 39}, context["schedule"])

    async def test_login_change_invalidates_discovery_cache(self):
        self.service.reader.candidates.return_value = [self.metadata()]
        await self.service._discover([])
        self.service.social.close = AsyncMock()
        await self.service.login_changed()
        self.assertFalse(self.service._discovery_cache)


class IndependentActivitiesTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = social_fixture.SocialTest.asyncSetUp
    asyncTearDown = social_fixture.SocialTest.asyncTearDown
    video = social_fixture.SocialTest.video
    incoming = social_fixture.SocialTest.incoming

    async def test_waiting_for_contact_does_not_delay_personal_favorite(self):
        record = await self.video()
        await self.store.update({"bindings": {"private:a": {"uid": "5678"}}})
        self.browse._model.side_effect = [
            {"action": "wait", "reason": "刚联系过，先等对方", "review_minutes": 45},
            {"action": "favorite", "id": record["id"], "reason": "自己以后想再看", "review_minutes": 60},
        ]
        await self.social.tick(self.day, self.world)
        self.platform.favorite.assert_not_awaited()
        await self.social.tick(self.day, self.world)
        self.platform.favorite.assert_awaited_once()
        context = self.browse._model.await_args.args[1]
        self.assertEqual(context["decision_channel"], "activity")
        for name in ["contact_rhythm", "bound_contact", "contact", "bili_dialogue", "incoming"]:
            self.assertNotIn(name, context)
        await self.social.tick(self.day, self.world)
        self.assertEqual(self.browse._model.await_count, 2)
        self.platform.dm.assert_not_awaited()
        self.platform.comment.assert_not_awaited()

    async def test_legacy_wait_migrates_without_blocking_personal_activity(self):
        record = await self.video()
        deadline = time.time() + 2700
        await self.store.update({"review_after": deadline})
        self.browse._model.return_value = {"action": "favorite", "id": record["id"]}
        await self.social.tick(self.day, self.world)
        self.platform.favorite.assert_awaited_once()
        state = await self.store.state()
        self.assertIn("activity", state["reviews"])
        self.assertEqual(state["review_after"], deadline)

    async def test_bound_incoming_bypasses_wait_and_remains_passive(self):
        await self.store.update({"review_after": time.time() + 7200})
        row = await self.incoming()
        self.browse._model.return_value = {"action": "dm", "id": row["id"], "text": "这段实验确实有意思"}
        await self.social.tick(self.day, self.world)
        self.platform.dm.assert_awaited_once()
        context = self.browse._model.await_args.args[1]
        self.assertEqual(context["allowed_actions"], ["wait", "dm", "dismiss"])
        self.assertEqual((await self.store.state())["reviews"]["inbox"]["action"], "dm")

    async def test_empty_public_material_does_not_request_a_comment(self):
        await self.social.tick(self.day, self.world)
        context = self.browse._model.await_args.args[1]
        self.assertEqual(context["decision_channel"], "activity")
        self.assertNotIn("comment", context["allowed_actions"])
        self.assertNotIn("dynamic", context["allowed_actions"])

    async def test_sleep_during_expression_prevents_autonomous_comment(self):
        record = await self.video()
        async def model(rule, context):
            if context.get("allowed_actions") == ["comment", "wait"]:
                self.world["sleeping"] = True
                return {"action": "comment", "text": "具体的观看感受"}
            return {"action": "comment", "id": record["id"]}
        self.browse._model.side_effect = model
        with self.assertRaises(BiliInteractionDeferred):
            await self.social.tick(self.day, self.world)
        self.platform.comment.assert_not_awaited()
        self.assertFalse([row for row in await self.store.records() if row.get("outbound")])

    async def test_new_work_during_favorite_lookup_prevents_account_write(self):
        record = await self.video()
        async def folders():
            self.world["run"] = {"status": "running", "action": {"action_type": "work"}}
            return [{"id": "7"}]
        self.platform.folders.side_effect = folders
        with self.assertRaises(BiliInteractionDeferred):
            await self.social.act("favorite", record=record, autonomous=True)
        self.platform.favorite.assert_not_awaited()

    async def test_new_video_reopens_interaction_but_progress_alone_does_not(self):
        await self.video()
        public = await self.browse.archive.list(scope="global")
        now = time.time()
        key = observation(self.world, public)
        state = review_patch({}, "interaction", {"action": "wait"}, now=now - 301,
                             after=now + 3600, context_key=key)
        state.update(review_patch(state, "activity", {"action": "wait"}, now=now,
                                  after=now + 3600, context_key=key))
        await self.store.update(state)
        await self.video(position=45)
        await self.social.tick(self.day, self.world)
        self.browse._model.assert_not_awaited()
        other = self.browse.new_record({"bvid": browsing_fixture.BV2, "page": 1, "aid": "43", "duration": 80})
        await self.browse._save(other, evidence=[{"quote": "新片段"}], impression="另一件有意思的事")
        await self.social.tick(self.day, self.world)
        self.browse._model.assert_awaited_once()
        self.assertEqual(self.browse._model.await_args.args[1]["decision_channel"], "interaction")

    async def test_changed_life_reopens_activity_without_reviving_old_contact_wait(self):
        now = time.time()
        key = observation(self.world, [])
        state = review_patch({}, "activity", {"action": "wait"}, now=now - 301,
                             after=now + 3600, context_key=key)
        await self.store.update({**state, "review_after": now + 3600})
        self.world["run"] = {"status": "running", "action": {"action_type": "rest", "action_id": "new-rest"}}
        await self.social.tick(self.day, self.world)
        self.browse._model.assert_awaited_once()
        self.assertEqual(self.browse._model.await_args.args[1]["decision_channel"], "activity")


class ReviewClockTest(unittest.TestCase):
    def test_new_evidence_wakes_only_after_debounce(self):
        state = review_patch({}, "interaction", {"action": "wait"}, now=1000, after=8200, context_key="old")
        self.assertFalse(due(state, "interaction", "new", 1299))
        self.assertTrue(due(state, "interaction", "new", 1300))
        self.assertFalse(due(state, "interaction", "old", 1300))

    def test_wait_deadlines_do_not_cross_channels(self):
        state = review_patch({}, "interaction", {"action": "wait"}, now=1000, after=8200, context_key="same")
        self.assertTrue(due(state, "activity", "same", 1000))
        state.update(review_patch(state, "activity", {"action": "wait"}, now=1000, after=2000, context_key="same"))
        self.assertTrue(due(state, "activity", "same", 2000))
        self.assertFalse(due(state, "interaction", "same", 2000))

    def test_progress_and_wording_do_not_create_per_minute_share_triggers(self):
        rows = [{"id": "v1", "status": "watching", "position": 10, "impression": "第一段"}]
        before = observation({}, rows)
        rows[0].update(position=20, impression="接着看")
        self.assertEqual(before, observation({}, rows))
        rows[0]["status"] = "completed"
        self.assertNotEqual(before, observation({}, rows))

    def test_new_life_action_changes_observation(self):
        self.assertNotEqual(observation({}, []), observation(
            {"run": {"status": "running", "action": {"action_id": "rest-1"}}}, []))
