import asyncio
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import DayRecord, LifeArchive, TimelineItem
from testwardrobelife import Runtime
from componentfixtures import StyleCatalogComponent
from core.life.curation import validate_wardrobe_choice
from core.life.dressing import digest, reviewed_candidates
from core.life.rotation import care_backlog, unreviewed_care, wardrobe_rotation
from core.life.wardrobefit import wardrobe_targets


def item(number, kind, scenes=("home", "sleep")):
    return SimpleNamespace(id=number, kind=kind, title="衣物", description="原有视觉事实",
                           attributes={"scene_categories": list(scenes)}, preference_score=0,
                           source_image_hash=str(number), confidence=0.95)


def state(units, condition="clean"):
    return {"ownership": "owned", "available": condition in {"clean", "worn", "stored"},
            "condition": condition, "unit_ids": units, "currently_worn": False}


def wear(at, ids, **payload):
    return {"kind": "wear", "occurred_at": at, "payload": {"item_ids": ids, **payload}}


class RotationFactsTest(unittest.TestCase):
    def setUp(self):
        self.items = [item(1, "outfit"), item(2, "top"), item(3, "bottom"), item(4, "footwear")]
        self.snapshot = {"items": {"1": state(["top", "bottom"]), "2": state(["top"]),
                                   "3": state(["bottom"]), "4": state(["shoe"])},
                         "wear_history": [wear("2026-10-07 21:00", [1], day="2026-10-08"),
                                          wear("2026-10-07 21:00", [2, 3, 4]),
                                          wear("2026-10-08 19:00", [1, 4])]}

    def test_real_units_aliases_and_duplicate_receipts_share_identity(self):
        facts = wardrobe_rotation(self.items, self.snapshot, "2026-10-08")
        self.assertEqual(len(facts["recent_combinations"]), 1)
        self.assertEqual(facts["items"]["1"]["wear_dates"], ["2026-10-07", "2026-10-08"])
        self.assertEqual(facts["items"]["2"]["wear_days"], 2)
        self.assertNotIn("4", facts["items"])
        sleep = next(s for s in facts["scenes"] if s["scene_category"] == "sleep")
        self.assertTrue(sleep["single_structural_option"])
        self.assertTrue(sleep["rotation_pressure"])

    def test_same_night_shoe_changes_and_future_or_old_records_do_not_create_repeat(self):
        self.snapshot["wear_history"] = [wear("2026-10-08 21:00", [1]), wear("2026-10-08 22:00", [2, 3, 4]),
                                         wear("2026-10-09 19:00", [1]), wear("2026-09-30 19:00", [1])]
        facts = wardrobe_rotation(self.items, self.snapshot, "2026-10-08")
        self.assertEqual(facts["items"]["1"]["wear_days"], 1)
        self.assertFalse(any(s["rotation_pressure"] for s in facts["scenes"]))

    def test_wash_blocked_alternative_is_rotation_pressure_but_clean_splits_are_options(self):
        self.snapshot["wear_history"] = []
        self.items.append(item(5, "outfit"))
        self.snapshot["items"]["5"] = state(["another"], "dirty")
        facts = wardrobe_rotation(self.items, self.snapshot, "2026-10-08")
        self.assertTrue(next(s for s in facts["scenes"] if s["scene_category"] == "sleep")["rotation_pressure"])
        self.items.append(item(6, "top"))
        self.snapshot["items"]["6"] = state(["another_top"])
        facts = wardrobe_rotation(self.items, self.snapshot, "2026-10-08")
        self.assertFalse(next(s for s in facts["scenes"] if s["scene_category"] == "sleep")["single_structural_option"])

    def test_rotation_motive_requires_evidence_and_rephrasing_does_not_create_new_job(self):
        scene = wardrobe_rotation(self.items, self.snapshot, "2026-10-08")["scenes"][0]
        sources = {"rotation:home": {"kind": "rotation", **scene}}
        raw = {"decision": "generate", "motive": "rotation", "key": "idea-a", "requirement": "新完整套装",
               "difference": "不同版型与色彩", "reason": "已有衣物可穿，另备一套交替", "evidence_ids": list(sources)}
        choice = validate_wardrobe_choice(raw, sources, self.snapshot["items"])
        self.assertEqual(choice["status"], "decided")
        self.assertEqual(choice["key"], validate_wardrobe_choice({**raw, "key": "idea-b"}, sources, self.snapshot["items"])["key"])
        sources["rotation:home"]["rotation_pressure"] = False
        self.assertEqual(validate_wardrobe_choice(raw, sources, self.snapshot["items"])["status"], "unverified")

    def test_home_or_unknown_use_does_not_claim_sleepwear_coverage(self):
        self.items.extend([item(5, "outfit", ("home",)), item(6, "outfit", ())])
        self.snapshot["items"].update({"5": state(["home_only"]), "6": state(["unknown"])})
        facts = wardrobe_rotation(self.items, self.snapshot, "2026-10-08")
        sleep = next(scene for scene in facts["scenes"] if scene["scene_category"] == "sleep")
        self.assertEqual(sleep["distinct_available_outfits"], 1)
        self.assertNotIn(5, sleep["owned_item_ids"])
        self.assertNotIn(6, sleep["owned_item_ids"])

    def test_history_ranking_changes_only_similarly_suitable_clothing(self):
        old, fresh = item(1, "outfit"), item(2, "outfit")
        old.preference_score = 200
        for i, count in [(old, 3), (fresh, 0)]:
            i.attributes.update(wardrobe=state([str(i.id)]), wear_history={"wear_days": count})
        profile = {"suitability": {"1": {"verdict": "suitable"}, "2": {"verdict": "suitable"}}}
        self.assertEqual(reviewed_candidates([old, fresh], profile)[0].id, 2)
        self.assertEqual(reviewed_candidates([old, fresh], {})[0].id, 2)
        profile["suitability"]["2"]["verdict"] = "unsuitable"
        self.assertEqual(reviewed_candidates([old, fresh], profile)[0].id, 1)


class RotationRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.tmp.name) / "life.db")
        self.runtime = Runtime(self.archive)
        self.runtime.wardrobe_life._run_wardrobe_job = AsyncMock()
        self.day = DayRecord(date="2026-10-08", outfit="已有的套装", timeline=[
            TimelineItem(time="12:00", activity="在家休息", activity_kind="rest", place_kind="home")])
        self.day.meta.update(outfit_scene_category="home")
        await self.archive.save_day(self.day)
        self.item = await self.archive.upsert_style_catalog_item({"kind": "outfit", "source_image_hash": "a" * 64,
            "description": "旧套装", "attributes": {"scene_categories": ["home", "sleep"]}, "confidence": 0.95})
        await self.archive.adopt_wardrobe_items([self.item.id], event_id="owned", at=str(self.runtime.now), reason="已有")

    async def asyncTearDown(self):
        await self.runtime.background.cancel_all()
        await self.archive.aclose()
        self.tmp.cleanup()

    async def review(self, responder):
        service = self.runtime.wardrobe_life
        day = await self.archive.get_day(self.day.date)
        snapshot = await self.archive.get_wardrobe_snapshot()
        conditions = service._wardrobe_conditions(day, self.runtime.now)
        key = digest({k: {f: v.get(f) for f in ("ownership", "condition")} for k, v in snapshot["items"].items()})
        self.runtime.call_text_model.side_effect = responder
        await service._review_wardrobe(day, conditions, snapshot, key, digest([]))
        await asyncio.gather(*list(self.runtime.background.tasks))
        return await self.archive.get_wardrobe_snapshot()

    async def test_generation_for_rotation_survives_existing_fit_and_does_not_fake_wear(self):
        for at in ["2026-10-07 20:00", "2026-10-08 08:00"]:
            day = self.day if at[:10] == self.day.date else DayRecord(date=at[:10], outfit=self.day.outfit)
            day.meta.update(style_catalog_reference_ids=str(self.item.id), outfit_fact_confirmed_at=at)
            await self.archive.save_day(day)
            await self.archive.record_wardrobe_wear(day, event_id=at)
        async def choose(provider, prompt, *args, **kwargs):
            data = json.loads(prompt.split("【眼前内容】\n", 1)[1])
            target = data["targets"][0]
            return json.dumps({"scene_choices": [{"target_id": target["id"], "verdict": "fits", "outfit_basis": "selection",
                "item_ids": [self.item.id], "confidence": .9, "reason": "旧衣仍可穿"}],
                "acquisition": {"decision": "generate", "motive": "rotation", "key": "another", "requirement": "不同的新完整套装",
                    "difference": "新的版型与配色", "reason": "两日复穿，准备交替穿", "evidence_ids": ["rotation:home"]}})
        snapshot = await self.review(choose)
        self.assertEqual(snapshot["profile"]["acquisition"]["status"], "queued")
        self.assertEqual(snapshot["jobs"][0]["payload"]["motive"], "rotation")
        self.assertEqual(snapshot["jobs"][0]["payload"]["existing_clothes"][0]["id"], self.item.id)
        self.assertEqual((await self.archive.get_day(self.day.date)).outfit, "已有的套装")
        self.assertEqual(await self.archive.get_life_action_receipts(), [])

    async def test_planning_and_outfit_context_share_physical_wear_history(self):
        self.day.meta.update(style_catalog_reference_ids=str(self.item.id), outfit_fact_confirmed_at="2026-10-08 08:00")
        await self.archive.save_day(self.day)
        await self.archive.record_wardrobe_wear(self.day, event_id="actual")
        composer = StyleCatalogComponent()
        composer.archive = self.archive
        for scene in ("", "home"):
            context = await composer._style_catalog_context(conditions={"date": "2026-10-08"}, scene_category=scene)
            self.assertIn('"wear_dates": ["2026-10-08"]', context)
            self.assertIn('"wear_days": 1', context)

    async def test_care_aliases_cover_one_garment_but_invented_evidence_does_not(self):
        await self.archive.start_wardrobe_care([self.item.id], decision="dirty", event_id="dirty",
            at="2026-10-01 08:00", observer="test", duration_minutes=10, reason="待洗")
        snapshot = await self.archive.get_wardrobe_snapshot()
        backlog = care_backlog(snapshot)
        sources = {"inventory": {"kind": "inventory_state", "item_id": self.item.id}}
        value = {"decision": "washing", "item_ids": [self.item.id], "reason": "洗护", "evidence_ids": ["invented"]}
        self.assertEqual(unreviewed_care([value], backlog, sources), backlog)
        value["evidence_ids"] = ["inventory"]
        self.assertEqual(unreviewed_care([value], backlog, sources), [])

    async def test_old_laundry_is_reviewed_and_washed_using_persistent_inventory(self):
        await self.archive.start_wardrobe_care([self.item.id], decision="dirty", event_id="old-dirty",
            at="2026-10-01 08:00", observer="test", duration_minutes=10, reason="已穿后待洗")
        calls = []
        async def choose(provider, prompt, *args, **kwargs):
            data = json.loads(prompt.split("【眼前内容】\n", 1)[1]); calls.append(data)
            refs = [k for k, v in data["sources"].items() if v["kind"] == "inventory_state"]
            return json.dumps({"acquisition": {"decision": "keep", "reason": "先整理衣柜"}, "care": [] if len(calls) == 1 else [
                {"item_ids": [self.item.id], "decision": "washing", "reason": "已经待洗一周，现在在家可洗",
                 "duration_minutes": 30, "drying_minutes": 120, "evidence_ids": refs}]})
        snapshot = await self.review(choose)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["validation_result"]["issues"][0]["code"], "care_backlog_not_evaluated")
        self.assertTrue(snapshot["profile"]["care_review_complete"])
        self.assertEqual(snapshot["items"][str(self.item.id)]["condition"], "washing")

    async def test_deferred_laundry_stays_dirty_and_is_due_for_review(self):
        await self.archive.start_wardrobe_care([self.item.id], decision="dirty", event_id="dirty",
            at="2026-10-01 08:00", observer="test", duration_minutes=10, reason="待洗")
        async def choose(provider, prompt, *args, **kwargs):
            data = json.loads(prompt.split("【眼前内容】\n", 1)[1])
            return json.dumps({"acquisition": {"decision": "keep", "reason": "有事先出门"}, "care": [
                {"item_ids": [self.item.id], "decision": "defer", "reason": "先出门回来洗",
                 "evidence_ids": [k for k, v in data["sources"].items() if v["kind"] == "inventory_state"]}]})
        snapshot = await self.review(choose)
        self.assertEqual(snapshot["items"][str(self.item.id)]["condition"], "dirty")
        self.assertTrue(snapshot["profile"]["care_review_complete"])
        self.assertFalse(self.runtime.wardrobe_life._wardrobe_review_due(snapshot["profile"], self.runtime.now))
        self.assertTrue(self.runtime.wardrobe_life._wardrobe_review_due(snapshot["profile"], self.runtime.now + datetime.timedelta(hours=6)))

    async def test_wear_history_survives_more_than_eighty_other_events(self):
        self.day.meta.update(style_catalog_reference_ids=str(self.item.id), outfit_fact_confirmed_at="2026-10-07 21:00")
        await self.archive.save_day(self.day)
        await self.archive.record_wardrobe_wear(self.day, event_id="real-wear")
        for index in range(85):
            await self.archive.record_wardrobe_feedback(item_ids=[self.item.id], event_id=str(index),
                                                       at="2026-10-08 12:00", text="参考", scope="test")
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertFalse(any(e["kind"] == "wear" for e in snapshot["events"]))
        self.assertTrue(any(e["occurred_at"] == "2026-10-07 21:00" for e in snapshot["wear_history"]))

    async def manual_material(self, job_id):
        self.runtime = Runtime(self.archive)
        candidate = await self.archive.upsert_style_catalog_item({"kind": "outfit", "source_image_hash": "b" * 64,
            "description": "生成的服装", "confidence": .95, "attributes": {"wardrobe_job": job_id}})
        image = Path(self.tmp.name) / "generated.png"
        image.write_bytes(b"image")
        self.runtime._generate_life_image_with_policy_retry.return_value = SimpleNamespace(path=str(image))
        self.runtime._learn_style_catalog_image.return_value = [candidate]
        await self.archive.enqueue_wardrobe_job(job_id, {"automatic": False, "count": 1,
            "generation_mode": "text_to_image", "requirement": "与旧款明显不同的新睡衣"}, at=str(self.runtime.now))
        return candidate, image

    async def test_manual_generation_also_rejects_mismatched_material_and_corrects_once(self):
        candidate, _ = await self.manual_material("manual-mismatch")
        self.runtime.wardrobe_life._validate_wardrobe_acquisition.return_value = {"fits": False, "reason": "仍然是旧款主体"}
        for expected in ("pending", "failed"):
            await self.runtime.wardrobe_life._run_wardrobe_job("manual-mismatch")
            snapshot = await self.archive.get_wardrobe_snapshot()
            self.assertEqual(snapshot["jobs"][0]["status"], expected)
            self.assertEqual(snapshot["items"][str(candidate.id)]["ownership"], "candidate")
            self.runtime.now += datetime.timedelta(minutes=3)
        await self.runtime.wardrobe_life._run_wardrobe_job("manual-mismatch")
        self.assertEqual(self.runtime._generate_life_image_with_policy_retry.await_count, 2)

    async def test_manual_validation_unavailable_keeps_original_image_for_retry(self):
        candidate, image = await self.manual_material("manual-unknown")
        self.runtime.wardrobe_life._validate_wardrobe_acquisition.return_value = None
        await self.runtime.wardrobe_life._run_wardrobe_job("manual-unknown")
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(snapshot["jobs"][0]["progress"]["current"]["path"], str(image))
        self.assertEqual(snapshot["items"][str(candidate.id)]["ownership"], "candidate")
        self.runtime.now += datetime.timedelta(minutes=3)
        self.runtime.wardrobe_life._validate_wardrobe_acquisition.return_value = {"fits": True, "reason": "新结构满足目标"}
        await self.runtime.wardrobe_life._run_wardrobe_job("manual-unknown")
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()
        self.assertEqual((await self.archive.get_wardrobe_snapshot())["items"][str(candidate.id)]["ownership"], "owned")

    async def test_manual_design_receives_existing_clothes_for_comparison(self):
        await self.runtime.queue_wardrobe_generation(None, requirement="一套可轮换的新衣", generation_mode="text_to_image", count=1)
        await asyncio.gather(*list(self.runtime.background.tasks))
        payload = (await self.archive.get_wardrobe_snapshot())["jobs"][0]["payload"]
        self.assertEqual(payload["existing_clothes"][0]["id"], self.item.id)
        self.assertEqual(payload["motive"], "request")
