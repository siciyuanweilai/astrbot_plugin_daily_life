import datetime
import json
import tempfile
import unittest
from unittest.mock import AsyncMock

from core.life.domain import LifeDomainService
from support import DayRecord, LifeArchive, TimelineItem, make_composer


class OutfitExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(f"{self.temp.name}/life.db")
        self.composer, self.provider, _, _ = make_composer([])
        self.composer.archive = self.archive
        self.composer.domains = LifeDomainService(self.composer.config.domains, self.archive)
        self.now = datetime.datetime(2026, 8, 16, 22, 10)
        await self.archive.save_day(DayRecord(
            date="2026-08-16", outfit="日间外出装",
            timeline=[TimelineItem(time="22:00", activity="准备休息")],
            meta={"outfit_fact_source": "user_instruction",
                  "outfit_fact_confirmed_at": "2026-08-16 12:00:00"},
        ))
        self.item = await self.archive.upsert_style_catalog_item({
            "kind": "outfit", "title": "衣橱测试睡裙",
            "description": "米白色缎面蕾丝吊带睡裙",
            "source_image_hash": "a" * 64, "confidence": 0.9,
        })
        await self.archive.adopt_wardrobe_items(
            [self.item.id], event_id="owned-sleepwear", at="2026-08-16 12:00:00",
            reason="已拥有的睡裙",
        )

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.temp.cleanup()

    def decision(self):
        return {
            "outfit_decision": "sleepwear", "current_outfit_basis": "action_intent",
            "scene_category": "sleep", "style_pool": "sleep_styles",
            "component_review": {"main_clothing": "adjust"},
            "outfit": self.item.description, "style": "居家睡眠风",
            "catalog_reference_ids": [self.item.id], "reason": "准备休息，换上睡裙",
            "change_evidence": {"kind": "action_intent", "source": "autonomous"},
        }

    async def update(self, **options):
        return await self.composer.update_outfit(
            "2026-08-16", "late_night", current_time=self.now, **options,
        )

    async def test_current_decision_executes_and_updates_outfit_wardrobe_and_receipt(self):
        self.provider.responses.append(json.dumps(self.decision(), ensure_ascii=False))
        result = await self.update()
        self.assertIsNotNone(result)
        stored = await self.archive.get_day("2026-08-16")
        self.assertIn("米白色缎面蕾丝吊带睡裙", stored.outfit)
        self.assertEqual(stored.timeline[0].execution_state, "planned")
        self.assertEqual(stored.meta["outfit_fact_source"], "life_action")
        self.assertEqual(stored.time_period, "late_night")
        self.assertEqual(stored.outfit_history["late_night"], stored.outfit)
        self.assertEqual(stored.meta["outfit_decision"], "sleepwear")
        self.assertEqual(stored.meta["outfit_style_pool"], "sleep_styles")
        self.assertEqual(stored.meta["style_catalog_reference_ids"], str(self.item.id))
        wardrobe = await self.archive.get_wardrobe_snapshot()
        self.assertTrue(wardrobe["items"][str(self.item.id)]["currently_worn"])
        outcomes = await self.archive.get_life_action_outcomes(date="2026-08-16")
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "committed")
        receipts = await self.archive.get_life_action_receipts(action_id=outcomes[0].action_id)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].status, "simulated")

    async def test_owned_current_choice_executes_without_trusting_completion_claim(self):
        unsupported = self.decision()
        unsupported["current_outfit_basis"] = "live_state"
        unsupported["change_evidence"] = {
            "kind": "explicit_outfit_change", "source": "live_state", "quote": "已经换好",
        }
        self.provider.responses.append(json.dumps(unsupported, ensure_ascii=False))
        result = await self.update()
        self.assertIn("米白色缎面蕾丝吊带睡裙", result.outfit)
        self.assertEqual(len(self.provider.prompts), 1)
        self.assertEqual(result.meta["outfit_fact_source"], "life_action")
        self.assertEqual(result.meta["outfit_fact_confirmed_at"], str(self.now))
        outcomes = await self.archive.get_life_action_outcomes(date="2026-08-16")
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].status, "committed")

    async def test_autonomous_tool_detail_executes_owned_clothes_without_user_confirmation(self):
        self.provider.responses.append(json.dumps(self.decision(), ensure_ascii=False))
        result = await self.update(
            instruction="想换上睡裙休息", instruction_source="autonomous",
        )
        self.assertIn(self.item.description, result.outfit)
        self.assertEqual(result.meta["outfit_fact_source"], "life_action")
        outcomes = await self.archive.get_life_action_outcomes(date="2026-08-16")
        self.assertEqual([outcome.status for outcome in outcomes], ["committed"])

    async def test_autonomous_tool_detail_cannot_bypass_laundry_and_requests_replenishment(self):
        await self.archive.start_wardrobe_care(
            [self.item.id], decision="dirty", event_id="needs-washing", at=str(self.now),
            observer="test", duration_minutes=0, reason="穿后待洗",
        )
        self.composer.wardrobe_shortage_callback = AsyncMock(return_value=True)
        self.provider.responses.append(json.dumps(self.decision(), ensure_ascii=False))
        result = await self.update(
            instruction="想换上睡裙休息", instruction_source="autonomous",
        )
        self.assertEqual(result.outfit, "日间外出装")
        self.composer.wardrobe_shortage_callback.assert_awaited_once()
        self.assertEqual(await self.archive.get_life_action_outcomes(date="2026-08-16"), [])
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(self.item.id)]
        self.assertEqual(state["condition"], "dirty")
        self.assertFalse(state["currently_worn"])

    async def test_completion_claim_cannot_override_executor_failure(self):
        unsupported = self.decision()
        unsupported["current_outfit_basis"] = "live_state"
        unsupported["change_evidence"] = {
            "kind": "explicit_outfit_change", "source": "live_state", "quote": "已经换好",
        }
        self.provider.responses.extend([json.dumps(unsupported)] * 2)
        self.composer.domains.validate_action = AsyncMock(return_value=(False, "当前不可换装"))
        result = await self.update()
        self.assertIsNone(result)
        self.assertEqual((await self.archive.get_day("2026-08-16")).outfit, "日间外出装")
        outcomes = await self.archive.get_life_action_outcomes(date="2026-08-16")
        receipts = await self.archive.get_life_action_receipts()
        self.assertTrue(outcomes)
        self.assertTrue(receipts)
        self.assertTrue(all(outcome.status == "failed" for outcome in outcomes))
        self.assertTrue(all(receipt.status == "failed" for receipt in receipts))

    async def test_owned_current_choice_with_schedule_claim_executes_at_current_time(self):
        unsupported = self.decision()
        unsupported["current_outfit_basis"] = "occurred_schedule"
        unsupported["change_evidence"] = {
            "kind": "explicit_outfit_change", "source": "occurred_schedule",
            "timeline_time": "12:00", "quote": "已经换好",
        }
        self.provider.responses.append(json.dumps(unsupported))
        result = await self.update()
        self.assertIn(self.item.description, result.outfit)
        self.assertEqual(result.meta["outfit_fact_source"], "life_action")
        self.assertEqual(result.meta["outfit_fact_confirmed_at"], str(self.now))
        self.assertEqual(result.timeline[0].execution_state, "planned")
        outcomes = await self.archive.get_life_action_outcomes(date="2026-08-16")
        self.assertEqual([outcome.status for outcome in outcomes], ["committed"])
        receipts = await self.archive.get_life_action_receipts(action_id=outcomes[0].action_id)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].occurred_at, str(self.now))

    async def test_current_choice_cannot_use_future_schedule_as_completed_evidence(self):
        day = await self.archive.get_day("2026-08-16")
        day.timeline.append(TimelineItem(time="23:00", activity="换睡裙休息"))
        await self.archive.save_day(day)
        unsupported = self.decision()
        unsupported["current_outfit_basis"] = "occurred_schedule"
        unsupported["change_evidence"] = {
            "kind": "explicit_outfit_change", "source": "occurred_schedule",
            "timeline_time": "23:00", "quote": "已经换好",
        }
        self.provider.responses.extend([json.dumps(unsupported)] * 2)
        result = await self.update()
        self.assertIsNone(result)
        self.assertEqual((await self.archive.get_day("2026-08-16")).outfit, "日间外出装")
        self.assertEqual(await self.archive.get_life_action_outcomes(date="2026-08-16"), [])
        self.assertEqual(await self.archive.get_life_action_receipts(), [])
