import copy
import datetime
import json
import unittest

import support
from componentfixtures import LifeActionComponent
from core.life.outfitflow import observation_contract, review_signature, wearability_key
from core.life.outfitstate import outfit_update_feedback
from core.models import DayRecord, LifeActionIntent, TimelineItem, WeatherInfo
import testoutfitexecution as execution


def running(day, kind="groom", action_id="care-1", status="running"):
    day.meta["continuous_execution"] = json.dumps({
        "status": status, "action": {"action_id": action_id, "action_type": kind},
    })


def completed(day, action_id="care-1", kind="groom", at="2026-08-16 22:20:00"):
    rows = json.loads(day.meta.get("life_action_settlements") or "{}")
    rows[action_id] = {"action_id": action_id, "action_type": kind,
                       "status": "committed", "committed_at": at}
    day.meta["life_action_settlements"] = json.dumps(rows)
    running(day, kind, action_id, status="completed")


class OutfitFlowIntegrationTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = execution.OutfitExecutionTest.asyncSetUp
    asyncTearDown = execution.OutfitExecutionTest.asyncTearDown
    decision = execution.OutfitExecutionTest.decision
    update = execution.OutfitExecutionTest.update

    async def test_one_bath_waits_then_dresses_once_across_rechecks(self):
        day = await self.archive.get_day("2026-08-16")
        running(day)
        await self.archive.save_day(day)
        self.provider.responses.append(json.dumps(self.decision()))
        for minute in (10, 15, 19):
            self.now = self.now.replace(minute=minute)
            result = await self.update()
            self.assertEqual(result.outfit, "日间外出装")
        self.assertEqual(self.provider.prompts, [])
        self.assertEqual(await self.archive.get_life_action_receipts(), [])

        day = await self.archive.get_day(day.date)
        completed(day)
        await self.archive.save_day(day)
        self.now = self.now.replace(minute=21)
        first = await self.update()
        self.assertIn(self.item.description, first.outfit)
        for minute in (22, 32, 42):
            self.now = self.now.replace(minute=minute)
            await self.update()
        self.assertEqual(len(self.provider.prompts), 1)
        outcomes = await self.archive.get_life_action_outcomes(date=day.date)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "committed")

    async def test_explicit_request_during_action_waits_and_survives_until_completion(self):
        day = await self.archive.get_day("2026-08-16")
        running(day, "exercise")
        await self.archive.save_day(day)
        await self.update(instruction="换这件米白睡裙", instruction_source="user")
        day = await self.archive.get_day(day.date)
        self.assertEqual(day.outfit, "日间外出装")
        self.assertEqual(json.loads(day.meta["outfit_request"])["instruction_source"], "user")
        self.assertEqual(outfit_update_feedback(day, day).status, "pending")
        self.assertEqual(self.provider.prompts, [])
        completed(day, kind="exercise")
        await self.archive.save_day(day)
        self.now = self.now.replace(minute=21)
        self.provider.responses.append(json.dumps(self.decision()))
        self.assertIn(self.item.description, (await self.update()).outfit)

    async def test_completed_review_does_not_block_new_explicit_choice(self):
        self.provider.responses.extend([json.dumps(self.decision())] * 2)
        await self.update()
        self.now += datetime.timedelta(minutes=1)
        await self.update(instruction="我想再调整一下穿搭", instruction_source="autonomous")
        self.assertEqual(len(self.provider.prompts), 2)

    async def test_reload_adopts_existing_receipt_without_redressing(self):
        self.provider.responses.append(json.dumps(self.decision()))
        await self.update()
        day = await self.archive.get_day("2026-08-16")
        day.meta.pop("outfit_evaluation")
        await self.archive.save_day(day)
        result = await self.update()
        self.assertIn(self.item.description, result.outfit)
        self.assertEqual(len(self.provider.prompts), 1)
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 1)

    async def test_actual_weather_change_reopens_review_but_catalog_rotation_does_not(self):
        self.provider.responses.extend([json.dumps(self.decision())] * 2)
        await self.update()
        day = await self.archive.get_day("2026-08-16")
        day.meta["wardrobe_selection_key"] = "new-candidates"
        await self.archive.save_day(day)
        await self.update()
        self.assertEqual(len(self.provider.prompts), 1)
        day = await self.archive.get_day(day.date)
        day.weather_info = WeatherInfo(temp=10, is_cold=True)
        await self.archive.save_day(day)
        await self.update()
        self.assertEqual(len(self.provider.prompts), 2)

    async def test_action_started_while_model_thinks_discards_the_old_result(self):
        original = self.composer._call_llm_text
        async def start_action(*args, **kwargs):
            answer = await original(*args, **kwargs)
            day = await self.archive.get_day("2026-08-16")
            running(day)
            await self.archive.save_day(day)
            return answer
        self.composer._call_llm_text = start_action
        self.provider.responses.append(json.dumps(self.decision()))
        self.assertIsNone(await self.update())
        self.assertEqual((await self.archive.get_day("2026-08-16")).outfit, "日间外出装")
        self.assertEqual(await self.archive.get_life_action_receipts(), [])

    async def test_planned_dressing_waits_for_its_existing_executor(self):
        day = await self.archive.get_day("2026-08-16")
        day.meta["planned_life_actions"] = json.dumps([
            {"action_id": "planned-change", "action_type": "change_outfit", "timeline_index": 0},
        ])
        await self.archive.save_day(day)
        self.assertEqual((await self.update()).outfit, "日间外出装")
        self.assertEqual(self.provider.prompts, [])


class OutfitFlowContractTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 8, 16, 22, 10)
        self.day = DayRecord(date="2026-08-16", outfit="当前衣服",
                             timeline=[TimelineItem(time="22:00", place_kind="home")])
        self.engine = LifeActionComponent()

    def test_other_physical_actions_block_appearance_until_completion(self):
        for kind in ("groom", "exercise", "move", "travel", "change_outfit"):
            for payload in ({"hair": "新发型"}, {"makeup": "新妆容"}, {"nails": "新美甲"}):
                with self.subTest(kind=kind, payload=payload):
                    day = copy.deepcopy(self.day)
                    running(day, kind)
                    action = LifeActionIntent(action_id="new-groom", action_type="groom", payload=payload)
                    refused = self.engine.settle_life_action(day, action, now=self.now)
                    self.assertEqual(refused.status, "rejected")
                    self.assertNotIn("new-groom", json.loads(day.meta.get("life_action_settlements") or "{}"))
                    completed(day, kind=kind)
                    accepted = self.engine.settle_life_action(day, action, now=self.now)
                    self.assertEqual(accepted.status, "committed")

    def test_executor_can_commit_its_own_grooming_without_deadlock(self):
        running(self.day)
        action = LifeActionIntent(action_id="care-1", action_type="groom", payload={"hair": "吹干的头发"})
        self.assertEqual(self.engine.settle_life_action(self.day, action, now=self.now).status, "committed")

    def test_unrelated_conversation_can_continue_during_care(self):
        running(self.day)
        action = LifeActionIntent(action_id="chat-1", action_type="chat", target="回复对话")
        self.assertEqual(self.engine.settle_life_action(self.day, action, now=self.now).status, "committed")

    def test_contract_is_rechecked_on_actual_commit_and_not_permanently_rejected(self):
        action = LifeActionIntent(action_id="change-1", action_type="change_outfit", target="干净衣服",
                                  payload={"appearance_observation": observation_contract(self.day, self.now)})
        running(self.day)
        self.assertEqual(self.engine.settle_life_action(self.day, action, now=self.now).status, "rejected")
        completed(self.day)
        self.assertEqual(self.engine.settle_life_action(self.day, action, now=self.now).status, "rejected")
        action.payload["appearance_observation"] = observation_contract(self.day, self.now)
        self.assertEqual(self.engine.settle_life_action(self.day, action, now=self.now).status, "committed")

    def test_calendar_start_is_not_completion_and_gap_does_not_replay_event(self):
        self.day.meta["planned_life_actions"] = json.dumps([
            {"action_id": "care-1", "action_type": "groom", "timeline_index": 0}])
        before = review_signature(self.day, self.now)
        self.day.timeline[0].activity = "洗澡完成换上睡衣"  # 文案不能创建回执。
        self.assertEqual(before, review_signature(self.day, self.now))
        running(self.day)
        during = review_signature(self.day, self.now)
        self.assertNotEqual(before, during)
        completed(self.day)
        after = review_signature(self.day, self.now)
        self.assertNotEqual(during, after)
        self.day.timeline[0].execution_state = "completed"
        self.day.timeline.append(TimelineItem(time="22:25", activity="听歌", place_kind="home"))
        self.assertEqual(after, review_signature(self.day, self.now.replace(minute=26)))
        self.day.meta["wardrobe_selection_key"] = "different-available-clothes"
        self.assertEqual(after, review_signature(self.day, self.now.replace(minute=36)))

    def test_new_exercise_event_can_trigger_another_review_same_night(self):
        completed(self.day)
        first = review_signature(self.day, self.now)
        completed(self.day, "exercise-2", "exercise", "2026-08-16 23:00:00")
        self.assertNotEqual(first, review_signature(self.day, self.now.replace(hour=23)))

    def test_pending_selection_retries_when_inventory_becomes_ready(self):
        self.day.meta["outfit_selection_pending"] = "need"
        before = review_signature(self.day, self.now)
        self.day.meta["wardrobe_selection_key"] = "now-ready"
        self.assertNotEqual(before, review_signature(self.day, self.now))

    def test_dirty_current_clothes_reopen_review_without_counting_clean_to_worn(self):
        clean = {"items": {"1": {"currently_worn": True, "condition": "clean"}}}
        worn = {"items": {"1": {"currently_worn": True, "condition": "worn"}}}
        self.assertEqual(wearability_key(clean), wearability_key(worn))
        before = review_signature(self.day, self.now)
        self.day.meta["outfit_wearability"] = wearability_key(
            {"items": {"1": {"currently_worn": True, "condition": "dirty"}}})
        self.assertNotEqual(before, review_signature(self.day, self.now))

    def test_existing_same_action_receipt_replays_without_second_change(self):
        action = LifeActionIntent(action_id="change-1", action_type="change_outfit", target="新衣服",
                                  payload={"appearance_observation": observation_contract(self.day, self.now)})
        self.assertEqual(self.engine.settle_life_action(self.day, action, now=self.now).status, "committed")
        running(self.day)
        again = self.engine.settle_life_action(self.day, action, now=self.now)
        self.assertTrue(again.replayed)
        self.assertEqual(self.day.outfit, "新衣服")
