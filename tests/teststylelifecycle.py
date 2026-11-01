import asyncio
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import DayRecord, LifeArchive, TimelineItem, make_composer
from core.models import LifeActionIntent, PreferenceRecord
from core.life.aesthetics import aesthetic_sources, reconcile_aesthetics, wardrobe_design
from core.life.appearance import format_life_preference_context
from core.life.curation import validate_wardrobe_choice
from core.life.dressing import digest, wardrobe_fact_text
from core.life.outfitstate import normalize_outfit_appearance, review_outfit_decision, select_outfit_appearance
from core.life.stylememory import record_style_transition, style_comparisons, style_usage_state, usage_context
from testaesthetics import claim
from testwardrobelife import Runtime


class StyleDecisionTest(unittest.TestCase):
    def test_clothes_change_preserves_unreviewed_personal_styles(self):
        old = normalize_outfit_appearance({"outfit": "旧衣", "hair": "低扎发", "makeup": "日常妆", "nails": "已做美甲"})
        proposed = normalize_outfit_appearance({"outfit": "新衣", "hair": "另一个发型", "makeup": "另一个妆", "nails": "新美甲"})
        for review in ({}, {"hair": "keep", "makeup": "unknown", "nails": "keep"}):
            result = select_outfit_appearance("change", old, proposed, component_review=review)
            self.assertEqual(result["outfit"], "新衣")
            for kind in ("hair", "makeup", "nails"):
                self.assertEqual(result[kind], old[kind])

    def test_removal_is_an_explicit_partial_change(self):
        old = normalize_outfit_appearance({"outfit": "衣服", "makeup": "已经上妆", "nails": "已有美甲"})
        new = normalize_outfit_appearance({"outfit": "衣服"})
        review = {"makeup": "not_present", "nails": "keep"}
        decision = review_outfit_decision("keep", current_basis="stored", scene_category="home", style_pool="",
                                         component_review=review, stored=old, generated=new, has_instruction=False)
        self.assertEqual(decision.decision, "partial_change")
        result = select_outfit_appearance(decision.decision, old, new, component_review=review)
        self.assertEqual(result["makeup"], "")
        self.assertEqual(result["nails"], "已有美甲")

    def test_clothing_evidence_cannot_confirm_makeup_taste(self):
        sources = {"w": {"kind": "wear", "text": "喜欢这个造型", "receipt_id": "w", "date": "2026-11-01"}}
        point = claim(categories=["makeup"], origin="own_experience", evidence_ids=["w"], quote="")
        self.assertIsNone(reconcile_aesthetics({"aesthetics": [point]}, sources, "now"))

    def test_distinct_grooming_choices_can_support_category_taste(self):
        sources = {f"s{i}": {"kind": "style_use", "receipt_id": str(i), "date": f"2026-11-0{i+1}",
                              "categories": ["hair"], "text": "喜欢这样不遮视线",
                              "appearance": {"transition": "selected"}, "choice_origin": "life_action"} for i in range(3)}
        point = claim(categories=["hair"], origin="own_experience", evidence_ids=list(sources), quote="")
        points = reconcile_aesthetics({"aesthetics": [point]}, sources, "now")
        self.assertEqual(points[0]["status"], "supported")
        self.assertEqual(points[0]["categories"], ["hair"])
        for s in sources.values():
            s["choice_origin"] = "user_instruction"
        self.assertEqual(reconcile_aesthetics({"aesthetics": [point]}, sources, "now")[0]["status"], "tentative")

    def test_invalid_design_category_and_frame_are_rejected(self):
        design = {"visual_brief": "手部设计", "reason": "想法", "difference": "变化", "aesthetic_ids": [],
                  "categories": ["nails"], "framing": "hands"}
        self.assertEqual(wardrobe_design(design, [])["categories"], ["nails"])
        self.assertIsNone(wardrobe_design({**design, "categories": ["invented"]}, []))
        self.assertIsNone(wardrobe_design({**design, "framing": "invented"}, []))

    def test_mixed_category_receipts_do_not_prove_three_choices_per_category(self):
        sources = {f"s{i}": {"kind": "style_use", "receipt_id": str(i), "date": f"2026-11-0{i+1}",
                              "categories": ["hair" if i < 2 else "nails"], "text": "有自己的选择理由",
                              "appearance": {"transition": "selected"}} for i in range(3)}
        point = claim(categories=["hair", "nails"], origin="own_experience", evidence_ids=list(sources), quote="")
        points = reconcile_aesthetics({"aesthetics": [point]}, sources, "now")
        self.assertEqual(points[0]["status"], "tentative")

    def test_structured_keep_does_not_invent_missing_makeup(self):
        old = normalize_outfit_appearance({"outfit": "旧衣"})
        generated = normalize_outfit_appearance({"outfit": "旧衣", "makeup": "模型补出的妆"})
        self.assertEqual(select_outfit_appearance("keep", old, generated,
                         component_review={"makeup": "keep"})["makeup"], "")

    def test_rotation_of_clothes_cannot_justify_changing_nails(self):
        choice = {"decision": "generate", "motive": "rotation", "categories": ["nails"], "key": "new",
                  "reason": "想改变", "requirement": "新美甲", "difference": "不同", "evidence_ids": ["r"]}
        sources = {"r": {"kind": "rotation", "rotation_pressure": True}}
        self.assertEqual(validate_wardrobe_choice(choice, sources, {})["status"], "unverified")

    def test_legacy_feedback_remains_attributed_to_other_person(self):
        pref = PreferenceRecord(category="makeup", content="更鲜明的唇色", weight=.5, source="style_catalog_feedback")
        text = format_life_preference_context([pref], limit=5, appearance_only=True)
        self.assertIn("对方造型建议", text)
        self.assertNotIn("已学习长期偏好：", text)


class StyleReceiptTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.tmp.name) / "life.db")
        self.day = DayRecord(date="2026-11-01", outfit="一直穿着的衣物", meta={
            "hair": "原来扎发", "makeup": "原来妆容", "nails": "原来美甲",
            "outfit_fact_confirmed_at": "2026-11-01 08:00:00"})
        await self.archive.save_day(self.day)
        await self.archive.record_wardrobe_wear(self.day, event_id="baseline")

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.tmp.cleanup()

    async def change(self, **values):
        before = dict(self.day.meta)
        self.day.meta.update(values)
        record_style_transition(self.day, before, at="2026-11-01 09:00:00", reason="整理得更舒服", origin="life_action")
        await self.archive.save_day(self.day)
        return await self.archive.get_wardrobe_snapshot()

    async def test_hair_only_change_gets_receipt_without_new_wear(self):
        state = await self.change(hair="新的扎发")
        self.assertEqual(len(state["wear_history"]), 1)
        self.assertEqual(len(state["style_history"]), 4)
        self.assertEqual(style_usage_state(state)["hair"]["description"], "新的扎发")
        self.assertEqual(style_usage_state(state)["nails"]["changed_at"], "")
        self.assertIn("新的扎发", wardrobe_fact_text(state))

    async def test_duplicate_and_cross_day_continuation_do_not_inflate_choice_count(self):
        await self.change(nails="已采用的新美甲")
        original = usage_context(self.day.meta)["nails"]["changed_at"]
        for _ in range(3):
            await self.archive.record_wardrobe_wear(self.day, event_id="duplicate")
        next_day = DayRecord(date="2026-11-02", outfit="第二天换衣", meta=dict(self.day.meta))
        next_day.meta["outfit_fact_confirmed_at"] = "2026-11-02 08:00:00"
        await self.archive.save_day(next_day)
        await self.archive.record_wardrobe_wear(next_day, event_id="next-day")
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(len([e for e in state["style_history"] if e["payload"]["category"] == "nails"]), 2)
        self.assertEqual(style_usage_state(state)["nails"]["changed_at"], original)

    async def test_makeup_removal_does_not_remove_nails(self):
        state = await self.change(makeup="", makeup_style="")
        use = style_usage_state(state)
        self.assertEqual(use["makeup"]["transition"], "removed")
        self.assertEqual(use["nails"]["description"], "原来美甲")
        sources = aesthetic_sources(state, "")
        removal = next(s for s in sources.values() if s.get("appearance", {}).get("transition") == "removed")
        self.assertEqual(removal["categories"], ["makeup"])

    async def test_stale_callback_cannot_create_a_false_usage(self):
        stale = copy.deepcopy(self.day)
        await self.change(hair="新的扎发")
        self.assertFalse(await self.archive.record_wardrobe_wear(stale, event_id="late"))
        self.assertEqual(style_usage_state(await self.archive.get_wardrobe_snapshot())["hair"]["description"], "新的扎发")

    async def test_groom_action_records_actual_usage_without_changing_clothes(self):
        composer, _, _, _ = make_composer([])
        composer.archive = self.archive
        action = LifeActionIntent(action_id="groom-nails", action_type="groom", target="护理指甲",
                                  payload={"nails": "修整后的自然短甲", "nails_style": "自然短甲"},
                                  requested_at="2026-11-01 10:00:00", evidence="今天想让指尖更轻便")
        outcome = await composer.settle_and_persist_life_action(self.day, action, now=dt.datetime(2026, 11, 1, 10))
        self.assertEqual(outcome.status, "committed")
        day = await self.archive.get_day(self.day.date)
        self.assertEqual(day.outfit, "一直穿着的衣物")
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(style_usage_state(state)["nails"]["description"], "修整后的自然短甲")
        self.assertEqual(len(state["wear_history"]), 1)

    async def test_cross_day_schedule_does_not_perform_planned_makeup(self):
        composer, _, _, _ = make_composer([])
        composer.archive = self.archive
        tomorrow = DayRecord(date="2026-11-02", outfit="次日服装", meta={"makeup": "计划的新妆容"},
                             timeline=[TimelineItem(time="08:00", activity="准备工作")])
        result = await composer._part_daily_engine_component._ground_generated_current_appearance(tomorrow, context={
            "check_time": dt.datetime(2026, 11, 2, 9), "period": "morning"})
        self.assertEqual(result.meta["makeup"], "原来妆容")
        self.assertEqual(result.meta["plan_makeup"], "计划的新妆容")

    async def test_personal_reference_changes_keep_clothing_and_drop_removed_makeup(self):
        items = [await self.archive.upsert_style_catalog_item({"kind": kind, "source_image_hash": kind * 16,
                 "description": kind, "confidence": .95}) for kind in ("outfit", "makeup", "nails")]
        composer, _, _, _ = make_composer([])
        composer.archive = self.archive
        result = await composer._part_outfit_component._merge_actual_style_references(
            [i.id for i in items], [], self.day.meta, {**self.day.meta, "makeup": ""}, {}, partial=True)
        self.assertEqual(set(result), {items[0].id, items[2].id})


class StyleGenerationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.tmp.name) / "life.db")
        self.runtime = Runtime(self.archive)
        self.service = self.runtime.wardrobe_life
        self.image = Path(self.tmp.name) / "image.png"
        self.image.write_bytes(b"image")
        self.runtime.media.image.first_configured_character_reference_image = lambda: str(self.image)
        self.runtime._edit_life_image_with_policy_retry.return_value = SimpleNamespace(path=str(self.image))
        self.runtime._generate_life_image_with_policy_retry.return_value = SimpleNamespace(path=str(self.image))
        self.service._design_wardrobe.return_value = {"visual_brief": "新设计的指甲细节", "reason": "便于日常活动",
            "difference": "不同的图案", "aesthetic_ids": [], "categories": ["nails"], "framing": "hands"}
        self.service.check_wardrobe_life = AsyncMock()
        await self.archive.save_day(DayRecord(date="2026-10-08", outfit="已有服装", meta={"nails": "实际旧美甲"}))

    async def asyncTearDown(self):
        await self.runtime.background.cancel_all()
        await self.archive.aclose()
        self.tmp.cleanup()

    async def candidate(self, kind="nails", job="style-test"):
        item = await self.archive.upsert_style_catalog_item({"kind": kind, "source_image_hash": "a" * 64,
            "description": "新造型细节", "confidence": .95,
            "attributes": {"wardrobe_job": job, "design_validation": "pending"}})
        self.runtime._learn_style_catalog_image.return_value = [item]
        return item

    async def enqueue(self, job="style-test", automatic=True):
        await self.archive.enqueue_wardrobe_job(job, {"requirement": "设计一款新美甲", "automatic": automatic,
            "generation_mode": "text_to_image", "target_categories": ["nails"], "count": 1}, at=str(self.runtime.now))

    async def job(self):
        return json.loads(await self.archive._run_db(lambda: self.archive._conn.execute(
            "SELECT progress_json FROM wardrobe_jobs WHERE job_id='style-test'").fetchone()[0]))

    async def test_automatic_nail_generation_completes_without_clothing_adoption(self):
        item = await self.candidate()
        self.assertEqual(await self.archive.get_style_catalog_items(kind="nails"), [])
        await self.enqueue()
        await self.service._run_wardrobe_job("style-test")
        self.assertEqual((await self.job())["completed_ids"], [item.id])
        self.assertEqual((await self.archive.get_style_catalog_items(kind="nails"))[0].id, item.id)
        day = await self.archive.get_day("2026-10-08")
        self.assertEqual(day.meta["nails"], "实际旧美甲")
        self.assertEqual(day.outfit, "已有服装")
        self.assertNotIn(str(item.id), (await self.archive.get_wardrobe_snapshot())["items"])
        self.assertEqual(self.runtime._learn_style_catalog_image.call_args.kwargs["kind"], "nails")
        prompt = self.runtime._edit_life_image_with_policy_retry.call_args.args[1]
        self.assertIn("手部近景", prompt)
        self.assertNotIn("全身从头", prompt)

    async def test_wrong_category_is_rejected_and_not_available_for_use(self):
        item = await self.candidate(kind="makeup")
        await self.enqueue()
        await self.service._run_wardrobe_job("style-test")
        self.assertEqual((await self.job()).get("completed_count", 0), 0)
        self.assertEqual(await self.archive.get_style_catalog_items(kind="makeup"), [])
        saved = await self.archive.get_style_catalog_item(item.id)
        self.assertEqual(saved.attributes["design_validation"], "rejected")
        self.service._validate_wardrobe_acquisition.assert_not_awaited()

    async def test_unknown_validation_keeps_original_image_unavailable_then_recovers(self):
        item = await self.candidate()
        await self.enqueue(automatic=False)
        self.service._validate_wardrobe_acquisition.side_effect = [None, {"fits": True, "reason": "设计符合目标"}]
        await self.service._run_wardrobe_job("style-test")
        self.assertEqual(await self.archive.get_style_catalog_items(kind="nails"), [])
        self.runtime.now += dt.timedelta(minutes=3)
        await self.service._run_wardrobe_job("style-test")
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()
        self.assertEqual((await self.job())["completed_ids"], [item.id])

    async def test_all_categories_remain_visible_in_design_comparison(self):
        personal = [await self.archive.upsert_style_catalog_item({"kind": kind, "source_image_hash": kind * 16,
                    "description": kind, "confidence": .95}) for kind in ("hair", "makeup", "nails")]
        clothes = [SimpleNamespace(id=i+100, kind="outfit", status="active", description="衣服", last_used_at="") for i in range(30)]
        state = {"items": {str(i.id): {"ownership": "owned"} for i in clothes}}
        compared = style_comparisons([*clothes, *personal], state)
        self.assertTrue({"hair", "makeup", "nails"} <= {i["kind"] for i in compared})

    async def test_repeated_image_does_not_overwrite_a_previously_accepted_style(self):
        item = await self.candidate(job="old-job")
        await self.archive.set_style_catalog_design_result([item.id], job_id="old-job", verdict="accepted", reason="已核验")
        repeated = await self.archive.upsert_style_catalog_item({"kind": "nails", "source_image_hash": "a" * 64,
            "description": "另一个任务误写的新描述", "confidence": .95,
            "attributes": {"wardrobe_job": "new-job", "design_validation": "pending"}})
        await self.archive.set_style_catalog_design_result([repeated.id], job_id="new-job", verdict="rejected", reason="重复")
        saved = await self.archive.get_style_catalog_item(item.id)
        self.assertEqual(saved.description, "新造型细节")
        self.assertEqual(saved.attributes["design_validation"], "accepted")
        self.assertEqual(saved.attributes["wardrobe_job"], "old-job")

    async def test_autonomous_interest_can_prepare_a_personal_style(self):
        await self.candidate(job="other")
        self.service._run_wardrobe_job = AsyncMock()
        day = await self.archive.get_day("2026-10-08")
        state = await self.archive.get_wardrobe_snapshot()
        conditions = self.service._wardrobe_conditions(day, self.runtime.now)
        self.runtime.call_text_model.return_value = json.dumps({"acquisition": {
            "decision": "generate", "categories": ["nails"], "motive": "interest", "key": "nails-idea",
            "requirement": "新指甲造型", "reason": "自己的新兴趣", "difference": "不同设计",
            "evidence_ids": ["persona:" + digest(await self.runtime.get_persona_text())]}})
        await self.service._review_wardrobe(day, conditions, state, digest({}), digest([]))
        await asyncio.gather(*list(self.runtime.background.tasks))
        state = await self.archive.get_wardrobe_snapshot()
        payload = state["jobs"][0]["payload"]
        self.assertEqual(payload["target_categories"], ["nails"])
        self.assertEqual(payload["verified_need"], {})


if __name__ == "__main__":
    unittest.main()
