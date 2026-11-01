import asyncio
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import DayRecord, LifeArchive
from testwardrobelife import Runtime
from core.life.aesthetics import (
    AESTHETIC_VERSION, aesthetic_context, aesthetic_context_key, aesthetic_sources,
    reconcile_aesthetics, wardrobe_design,
)
from core.life.dressing import digest, wardrobe_fact_text
from core.runtime.clothier import WardrobeLifeService


def claim(**changes):
    return {"preference": "喜欢舒适的穿搭", "origin": "persona", "scope": "日常穿着",
            "disposition": "prefer", "status": "supported", "reason": "原文明确表达舒适偏好",
            "quote": "喜欢舒适", "confidence": 0.8, "evidence_ids": ["p"], **changes}


def reconcile(points, sources):
    return reconcile_aesthetics({"aesthetics": points}, sources, "2026-11-01 12:00")


class AestheticEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.sources = {"p": {"kind": "persona", "text": "喜欢舒适，也在意性价比"},
                        "u": {"kind": "feedback", "text": "我想看亮色搭配"}}

    def test_legacy_claims_are_not_treated_as_confirmed_taste(self):
        profile = {"aesthetics": [{"preference": "未经核验的宽泛标签", "origin": "persona"}]}
        self.assertEqual(aesthetic_context(profile), [])
        self.assertNotIn("宽泛标签", wardrobe_fact_text({"profile": profile}))

    def test_verified_origin_scope_and_uncertainty_reach_conversation(self):
        points = reconcile([claim(), claim(preference="想尝试不同的设计", status="tentative",
                                           disposition="curious")], self.sources)
        profile = {"aesthetics": points}
        text = wardrobe_fact_text({"profile": profile})
        self.assertIn("角色自身审美", text)
        self.assertIn("正在了解的倾向", text)
        self.assertIn("日常穿着", text)
        self.assertLessEqual(points[1]["confidence"], .35)
        self.assertEqual(aesthetic_context({**profile, "aesthetic_review_pending": True}), [])

    def test_feedback_cannot_claim_to_be_persona_or_own_experience(self):
        for origin in ("persona", "own_experience"):
            self.assertIsNone(reconcile([claim(origin=origin, evidence_ids=["u"], quote="想看亮色")], self.sources))
        points = reconcile([claim(origin="user_feedback", evidence_ids=["u"], quote="想看亮色")], self.sources)
        self.assertEqual(points[0]["status"], "suggestion")

    def test_missing_or_invented_quote_and_citation_do_not_pass(self):
        for point in (claim(quote="喜欢亮红色"), claim(evidence_ids=["invented"]), claim(quote="")):
            self.assertIsNone(reconcile([point], self.sources))

    def test_duplicate_receipts_cannot_manufacture_stable_experience(self):
        sources = {f"w{i}": {"kind": "wear", "receipt_id": "same", "date": f"2026-10-{29+i}",
                              "text": "我喜欢这件的剪裁"} for i in range(3)}
        points = reconcile([claim(origin="own_experience", evidence_ids=list(sources), quote="")], sources)
        self.assertEqual(points[0]["status"], "tentative")

    def test_repeated_wear_without_choice_reasons_remains_observation(self):
        sources = {f"w{i}": {"kind": "wear", "receipt_id": str(i), "date": f"2026-10-{29+i}"}
                   for i in range(3)}
        points = reconcile([claim(origin="own_experience", evidence_ids=list(sources), quote="")], sources)
        self.assertEqual(points[0]["status"], "tentative")
        for source in sources.values():
            source["text"] = "喜欢这种利落的线条，主动选了它"
        points = reconcile([claim(origin="own_experience", evidence_ids=list(sources), quote="")], sources)
        self.assertEqual(points[0]["status"], "supported")
        self.assertLessEqual(points[0]["confidence"], .65)

    def test_snapshotted_evidence_survives_window_but_current_persona_replaces_old(self):
        point = reconcile([claim(origin="user_feedback", evidence_ids=["u"], quote="想看亮色")], self.sources)[0]
        sources = aesthetic_sources({"profile": {"aesthetics": [point]}}, "现在更喜欢朴素")
        self.assertIn("u", sources)
        self.assertEqual(sum(s["kind"] == "persona" for s in sources.values()), 1)
        self.assertNotIn("p", sources)

    def test_invalid_review_is_distinct_from_confirmed_empty_preferences(self):
        self.assertIsNone(reconcile_aesthetics({}, self.sources, "now"))
        self.assertIsNone(reconcile([claim(confidence=float("nan"))], self.sources))
        self.assertEqual(reconcile([], self.sources), [])

    def test_design_must_be_complete_and_cannot_invent_aesthetic_evidence(self):
        design = {"visual_brief": "具体的视觉搭配", "reason": "适合活动", "difference": "新轮廓", "aesthetic_ids": []}
        self.assertIsNotNone(wardrobe_design(design, []))
        self.assertIsNone(wardrobe_design({**design, "aesthetic_ids": ["invented"]}, []))
        self.assertIsNone(wardrobe_design({**design, "visual_brief": ""}, []))


class AestheticRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.tmp.name) / "life.db")
        self.runtime = Runtime(self.archive)
        self.service = self.runtime.wardrobe_life
        del self.service.__dict__["_review_aesthetics"]
        self.runtime.get_persona_text.return_value = "喜欢舒适，也在意性价比"
        self.ref = "persona:" + digest(await self.runtime.get_persona_text())
        self.runtime.call_text_model.return_value = json.dumps({"aesthetics": [claim(evidence_ids=[self.ref])]})
        await self.archive.save_day(DayRecord(date="2026-10-08", outfit="已有衣物"))

    async def asyncTearDown(self):
        await self.runtime.background.cancel_all()
        await self.archive.aclose()
        self.tmp.cleanup()

    async def test_semantic_review_replaces_overgeneralized_legacy_claim_without_actions(self):
        await self.archive.save_wardrobe_review({"aesthetics": [{"preference": "舒适且只喜欢浅色甜美", "origin": "persona"}]},
                                               expected_revision=0, at=str(self.runtime.now))
        self.assertTrue(await self.service.refresh_aesthetics())
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual([p["preference"] for p in state["profile"]["aesthetics"]], ["喜欢舒适的穿搭"])
        self.assertEqual(state["jobs"], [])
        self.assertEqual((await self.archive.get_day("2026-10-08")).outfit, "已有衣物")
        self.assertEqual(await self.archive.get_life_action_receipts(), [])
        self.runtime._generate_life_image_with_policy_retry.assert_not_awaited()

    async def test_unchanged_sources_are_cached_and_concurrent_review_shares_model_call(self):
        state = await self.archive.get_wardrobe_snapshot()
        persona = await self.runtime.get_persona_text()
        await asyncio.gather(*(self.service._review_aesthetics(state, persona) for _ in range(3)))
        await self.service.refresh_aesthetics()
        await self.service.refresh_aesthetics()
        self.assertEqual(self.runtime.call_text_model.await_count, 1)

    async def test_new_feedback_reopens_review_without_relabeling_it_as_own_taste(self):
        await self.service.refresh_aesthetics()
        await self.archive.record_wardrobe_feedback(event_id="new", item_ids=[], text="我想看亮色搭配",
                                                    at=str(self.runtime.now), scope="test")
        self.runtime.call_text_model.return_value = json.dumps({"aesthetics": [
            claim(evidence_ids=[self.ref]), claim(preference="对方想看亮色", origin="user_feedback",
                evidence_ids=["event:new"], quote="想看亮色")
        ]})
        self.assertTrue(await self.service.refresh_aesthetics())
        points = (await self.archive.get_wardrobe_snapshot())["profile"]["aesthetics"]
        self.assertEqual(points[0]["origin"], "persona")
        self.assertEqual(points[1]["status"], "suggestion")
        self.assertEqual(self.runtime.call_text_model.await_count, 2)

    async def test_failed_review_keeps_record_for_retry_but_hides_unconfirmed_claim(self):
        await self.archive.save_wardrobe_review({"aesthetics": [{"preference": "旧的扩展推断", "origin": "persona"}]},
                                               expected_revision=0, at=str(self.runtime.now))
        self.runtime.call_text_model.return_value = "unavailable"
        self.assertFalse(await self.service.refresh_aesthetics())
        state = await self.archive.get_wardrobe_snapshot()
        self.assertTrue(state["profile"]["aesthetic_review_pending"])
        self.assertEqual(state["profile"]["aesthetics"][0]["preference"], "旧的扩展推断")
        self.assertEqual(aesthetic_context(state["profile"]), [])

    async def test_persona_change_during_review_cannot_save_stale_conclusion(self):
        async def changed(*args, **kwargs):
            self.runtime.get_persona_text.return_value = "新的角色偏好"
            return json.dumps({"aesthetics": [claim(evidence_ids=[self.ref])]})
        self.runtime.call_text_model.side_effect = changed
        self.assertFalse(await self.service.refresh_aesthetics())
        self.assertEqual((await self.archive.get_wardrobe_snapshot())["profile_revision"], 0)

    async def test_manual_generation_receives_reviewed_preferences_with_scope(self):
        self.service._run_wardrobe_job = AsyncMock()
        await self.service.queue_wardrobe_generation(None, requirement="设计一套可轮换的新衣",
                                                    generation_mode="text_to_image", count=1)
        await asyncio.gather(*list(self.runtime.background.tasks))
        state = await self.archive.get_wardrobe_snapshot()
        tastes = state["jobs"][0]["payload"]["aesthetics"]
        self.assertEqual(tastes[0]["scope"], "日常穿着")
        self.assertEqual(tastes[0]["review_version"], AESTHETIC_VERSION)

    async def test_automatic_design_uses_current_audit_and_existing_clothing_in_same_review(self):
        item = await self.archive.upsert_style_catalog_item({"kind": "outfit", "source_image_hash": "a" * 64,
                "description": "已有套装", "confidence": .95})
        self.service._run_wardrobe_job = AsyncMock()
        async def decide(provider, prompt, *args, **kwargs):
            data = json.loads(prompt.split("【眼前内容】\n", 1)[1])
            if "previous_claims" in data:
                return json.dumps({"aesthetics": [claim(evidence_ids=[self.ref])]})
            self.assertEqual(data["existing_aesthetics"][0]["preference"], "喜欢舒适的穿搭")
            evidence = [key for key, value in data["sources"].items() if value.get("kind") == "own_aesthetic"]
            return json.dumps({"acquisition": {"decision": "generate", "motive": "interest", "key": "new-shape",
                "requirement": "一套舒适但版型不同的完整造型", "difference": "尝试新的结构组合",
                "reason": "保留舒适偏好，探索新轮廓", "evidence_ids": evidence}})
        self.runtime.call_text_model.side_effect = decide
        day = await self.archive.get_day("2026-10-08")
        state = await self.archive.get_wardrobe_snapshot()
        conditions = self.service._wardrobe_conditions(day, self.runtime.now)
        inventory = digest({k: {f: v.get(f) for f in ("ownership", "condition")} for k, v in state["items"].items()})
        await self.service._review_wardrobe(day, conditions, state, inventory, digest([]))
        await asyncio.gather(*list(self.runtime.background.tasks))
        state = await self.archive.get_wardrobe_snapshot()
        payload = state["jobs"][0]["payload"]
        self.assertEqual(payload["aesthetics"][0]["status"], "supported")
        self.assertEqual(payload["existing_clothes"][0]["id"], item.id)
        self.assertEqual((await self.archive.get_day(day.date)).outfit, "已有衣物")

    async def test_background_review_respects_quiet_hours_but_explicit_request_can_review(self):
        self.runtime.now = self.runtime.now.replace(hour=1)
        self.assertFalse(await self.service.refresh_aesthetics(background=True))
        self.runtime.call_text_model.assert_not_awaited()
        self.assertTrue(await self.service.refresh_aesthetics())
        self.runtime.call_text_model.assert_awaited_once()

    async def test_new_feedback_arriving_during_review_prevents_stale_save(self):
        async def changed(*args, **kwargs):
            await self.archive.record_wardrobe_feedback(event_id="arrived", item_ids=[], text="现在更想尝试别的",
                                                        at=str(self.runtime.now), scope="test")
            return json.dumps({"aesthetics": [claim(evidence_ids=[self.ref])]})
        self.runtime.call_text_model.side_effect = changed
        self.assertFalse(await self.service.refresh_aesthetics())
        self.assertEqual((await self.archive.get_wardrobe_snapshot())["profile_revision"], 0)

    async def test_wear_receipt_remembers_actual_reason_and_feedback_has_independent_window(self):
        item = await self.archive.upsert_style_catalog_item({"kind": "outfit", "source_image_hash": "a" * 64,
                "description": "已有套装", "confidence": .95})
        day = await self.archive.get_day("2026-10-08")
        day.meta.update(style_catalog_reference_ids=str(item.id), outfit_fact_confirmed_at=str(self.runtime.now),
                        outfit_reason="今天只有这套已经晾干", outfit_scene_category="home")
        await self.archive.save_day(day)
        await self.archive.record_wardrobe_wear(day, event_id="wear")
        await self.archive.record_wardrobe_feedback(event_id="feedback", item_ids=[item.id], text="可以试试不同的配色",
                                                    at="2026-10-01 12:00", scope="test")
        for index in range(85):
            await self.archive.adopt_wardrobe_items([item.id], event_id=f"adopt:{index}",
                                                   at="2026-10-08 13:00", reason="已有衣物")
        state = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(state["wear_history"][0]["payload"]["choice_reason"], "今天只有这套已经晾干")
        self.assertTrue(state["feedback_history"])
        self.assertFalse(any(e["kind"] == "feedback" for e in state["events"]))

    async def test_existing_panel_feedback_enters_review_as_user_evidence(self):
        item = await self.archive.upsert_style_catalog_item({"kind": "outfit", "source_image_hash": "a" * 64,
                "description": "用户喜欢的既有套装", "confidence": .95})
        await self.archive.add_style_catalog_feedback(item.id, scope="dashboard", feedback="面板标记喜欢",
                                                      sentiment="prefer", score_delta=.35)
        state = await self.archive.get_wardrobe_snapshot()
        sources = aesthetic_sources(state, "")
        feedback = next(value for value in sources.values() if value["kind"] == "feedback")
        self.assertEqual(feedback["outfit"], "用户喜欢的既有套装")
        self.assertEqual(feedback["sentiment"], "prefer")
        self.assertEqual(feedback["item_ids"], [item.id])

    async def test_renderer_gets_only_decided_design_and_original_image_survives_validation_retry(self):
        candidate = await self.archive.upsert_style_catalog_item({"kind": "outfit", "source_image_hash": "a" * 64,
            "description": "新的完整造型", "confidence": .95, "attributes": {"wardrobe_job": "design-test"}})
        picture = Path(self.tmp.name) / "new.png"
        picture.write_bytes(b"image")
        self.runtime._generate_life_image_with_policy_retry.return_value = SimpleNamespace(path=str(picture))
        self.runtime._learn_style_catalog_image.return_value = [candidate]
        self.service._validate_wardrobe_acquisition.side_effect = [None, {"fits": True, "reason": "设计符合目标"}]
        self.service._creative_prompt = lambda requirement, **kwargs: requirement
        self.service._design_wardrobe.return_value = {"visual_brief": "确定的新设计目标", "reason": "探索不同结构",
                                                     "difference": "主体轮廓不同", "aesthetic_ids": []}
        await self.archive.enqueue_wardrobe_job("design-test", {"automatic": False, "count": 1,
            "generation_mode": "text_to_image", "requirement": "设计一套新衣",
            "existing_clothes": [{"description": "旧衣只作比较不供描画"}],
            "conditions": {"current_outfit": "当前穿搭不是绘图目标"}}, at=str(self.runtime.now))
        await self.service._run_wardrobe_job("design-test")
        prompt = self.runtime._generate_life_image_with_policy_retry.call_args.args[1]
        self.assertEqual(prompt, "确定的新设计目标")
        self.runtime.now += datetime.timedelta(minutes=3)
        await self.service._run_wardrobe_job("design-test")
        self.service._design_wardrobe.assert_awaited_once()
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()
        row = await self.archive._run_db(lambda: self.archive._conn.execute(
            "SELECT progress_json FROM wardrobe_jobs WHERE job_id='design-test'").fetchone()[0])
        self.assertEqual(json.loads(row)["completed_designs"][0]["design"]["visual_brief"], "确定的新设计目标")

    async def test_unavailable_design_does_not_submit_or_mark_image_result_unknown(self):
        self.service._design_wardrobe.return_value = None
        await self.archive.enqueue_wardrobe_job("design-pending", {"automatic": False, "count": 1,
            "generation_mode": "text_to_image", "requirement": "设计一套新衣"}, at=str(self.runtime.now))
        await self.service._run_wardrobe_job("design-pending")
        job = (await self.archive.get_wardrobe_snapshot())["jobs"][0]
        self.assertEqual(job["status"], "pending")
        self.assertFalse(job["progress"]["current"].get("submitting"))
        self.runtime._generate_life_image_with_policy_retry.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
