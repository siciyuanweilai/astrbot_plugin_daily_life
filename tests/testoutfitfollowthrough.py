"""穿搭状态、未完成需求、洗护和补衣闭环的回归。"""

import asyncio
import copy
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import support
from support import DayRecord, LifeArchive, LifeSettings, DailyLifeCommandCenter, Event, async_return
from componentfixtures import RuntimeStyleCatalogComponent
from core.life.dressing import digest, suitability_key, wardrobe_conditions
from core.life.outfitrequest import active_outfit_request, make_outfit_request
from core.life.outfitstate import outfit_change, outfit_update_feedback, review_outfit_decision, normalize_outfit_appearance
from core.life.wardrobefit import validate_scene_choices, wardrobe_targets, understanding_key
from core.models import TimelineItem, WeatherInfo
from testwardrobelife import Runtime


class OutfitFactsTest(unittest.TestCase):
    def test_style_rewording_cannot_promote_keep_to_partial_action(self):
        stored = normalize_outfit_appearance({"outfit": "当前家居服", "style": "日常/外出"})
        generated = {**stored, "style": "日常/外出风格"}
        result = review_outfit_decision("keep", current_basis="stored", scene_category="home",
            style_pool="outfit_styles", component_review={"main_clothing": "keep"},
            stored=stored, generated=generated, has_instruction=True)
        self.assertEqual(result.decision, "keep")
        self.assertFalse(result.partial_change)

    def test_style_label_change_is_not_dressing(self):
        before = DayRecord(date="2026-10-30", outfit="旧家居服", meta={"style": "日常/外出"})
        after = copy.deepcopy(before)
        after.meta.update(style="日常/外出风格", outfit_reason="选中的睡裙待洗")
        result = outfit_update_feedback(before, after)
        self.assertEqual(result.status, "unchanged")
        self.assertFalse(result.facts["clothing_changed"])
        self.assertIn("睡裙待洗", result)
        self.assertNotIn("穿搭已更新", result)

    def test_shoes_and_hair_are_partial_not_main_clothes(self):
        before = DayRecord(date="2026-10-30", outfit="家居服，袜子", meta={
            "outfit_components": json.dumps({"main_clothing": {"state": "worn", "description": "家居服"},
                                             "footwear": {"state": "worn", "description": "袜子"}}),
            "hair": "披发",
        })
        after = copy.deepcopy(before)
        after.outfit = "家居服"
        after.meta.update(hair="扎低马尾", outfit_components=json.dumps({
            "main_clothing": {"state": "worn", "description": "家居服"},
            "footwear": {"state": "removed", "description": "袜子"},
        }))
        self.assertEqual(outfit_change(before, after).status, "partial")
        self.assertFalse(outfit_change(before, after).clothing_changed)

    def test_main_clothes_change_has_completion(self):
        before = DayRecord(date="2026-10-30", outfit="针织衫")
        after = DayRecord(date="2026-10-30", outfit="新睡衣")
        self.assertEqual(outfit_change(before, after).status, "completed")
        self.assertEqual(outfit_update_feedback(before, after), "")

    def test_request_survives_reworded_style_and_expires_or_resolves(self):
        now = datetime.datetime(2026, 10, 30, 21)
        day = DayRecord(date="2026-10-30", outfit="针织衫")
        value = make_outfit_request(day, "换轻薄睡衣", now)
        day.meta["outfit_request"] = json.dumps(value)
        day.meta["style"] = "另一种风格名称"
        self.assertEqual(make_outfit_request(day, "换轻薄睡衣", now + datetime.timedelta(minutes=1)), value)
        self.assertTrue(active_outfit_request(day, now))
        self.assertFalse(active_outfit_request(day, now + datetime.timedelta(hours=6)))
        day.outfit = "新睡衣"
        self.assertFalse(active_outfit_request(day, now))

    def test_pending_requirement_is_part_of_cache_and_target_identity(self):
        now = datetime.datetime(2026, 10, 30, 21)
        day = DayRecord(date="2026-10-30", outfit="针织衫", meta={"outfit_scene_category": "home"})
        old = wardrobe_conditions(day, now)
        day.meta["outfit_request"] = json.dumps(make_outfit_request(day, "换轻薄睡衣", now))
        new = wardrobe_conditions(day, now)
        self.assertNotEqual(suitability_key(old), suitability_key(new))
        target = wardrobe_targets(new, [])[0]
        self.assertEqual(target["request"], "换轻薄睡衣")
        result = validate_scene_choices([{
            "target_id": target["id"], "verdict": "fits", "outfit_basis": "current",
            "item_ids": [], "reason": "现有衣服也能穿", "confidence": 0.95,
        }], [target], [], {"items": {}}, current_outfit="针织衫")
        self.assertEqual(result, [])

    def test_new_intent_supersedes_old_without_reusing_identity(self):
        now = datetime.datetime(2026, 10, 30, 21)
        day = DayRecord(date="2026-10-30", outfit="当前家居服")
        old = make_outfit_request(day, "轻薄睡衣", now)
        day.meta["outfit_request"] = json.dumps(old)
        new = make_outfit_request(day, "出门用的整套搭配", now)
        self.assertNotEqual(new["id"], old["id"])
        day.meta["outfit_request"] = json.dumps(new)
        self.assertEqual(active_outfit_request(day, now)["instruction"], "出门用的整套搭配")

    def test_footwear_change_keeps_clothing_request_pending(self):
        now = datetime.datetime(2026, 10, 30, 21)
        main = {"state": "worn", "description": "当前家居服"}
        day = DayRecord(date="2026-10-30", outfit="当前家居服，拖鞋", meta={
            "outfit_components": json.dumps({"main_clothing": main,
                "footwear": {"state": "worn", "description": "拖鞋"}}),
        })
        day.meta["outfit_request"] = json.dumps(make_outfit_request(day, "轻薄睡衣", now))
        day.outfit = "当前家居服"
        day.meta["outfit_components"] = json.dumps({"main_clothing": main,
            "footwear": {"state": "removed", "description": "拖鞋"}})
        self.assertTrue(active_outfit_request(day, now))

    def test_catalog_exposes_availability_without_parsing_title(self):
        item = SimpleNamespace(id=18, kind="outfit", title="一套睡衣", description="", source_image_hash="a", image_path="")
        result = RuntimeStyleCatalogComponent._style_catalog_result_text([item], wardrobe_states={
            "18": {"ownership": "owned", "condition": "dirty", "available": False},
        })
        self.assertIn("待洗", result)
        self.assertIn("当前不可用于换装", result)


class OutfitFollowthroughTest(unittest.IsolatedAsyncioTestCase):
    async def test_commitment_is_cleared_only_after_main_clothing_changes(self):
        for clothing_changed in (False, True):
            with self.subTest(clothing_changed=clothing_changed):
                archive = support.DataManager()
                now = datetime.datetime(2026, 10, 30, 21)
                day = DayRecord(date="2026-10-30", outfit="旧家居服", time_period="night",
                    timeline=[TimelineItem(time="20:00", activity="在家阅读", place_kind="home")],
                    meta={"pending_commitment_outfit": json.dumps({"date":"2026-10-30", "instruction":"轻薄睡衣"})})
                await archive.save_day(day)
                after = copy.deepcopy(day)
                after.meta["style"] = "标签改写"
                if clothing_changed:
                    after.outfit = "新睡衣"
                runtime = support.DailyLifeRuntime.__new__(support.DailyLifeRuntime)
                runtime.config = LifeSettings.from_dict({"state_config": {"enabled": True}})
                runtime.archive = archive
                runtime.composer = SimpleNamespace(update_outfit=AsyncMock(return_value=after))
                runtime.generation_lock = asyncio.Lock()
                runtime._page_status_version = 0
                runtime._page_status_changed = asyncio.Condition()
                runtime._runtime_now = lambda: now
                runtime.try_update_weather = lambda *args: async_return(None)
                runtime.resolve_injection_target = lambda now: async_return((day.date, False))
                runtime._get_curr_period = lambda now=None: "night"
                runtime.refresh_state_for_day = lambda date, data, now, **kwargs: async_return(data)
                await runtime.check_autonomous_life_update()
                stored = await archive.get_day(day.date)
                self.assertEqual("pending_commitment_outfit" in stored.meta, not clothing_changed)

    async def test_true_dressing_photo_requires_a_clothing_transition(self):
        runtime = support.DailyLifeRuntime.__new__(support.DailyLifeRuntime)
        runtime.context = support.Context(support.Provider([]))
        runtime._runtime_now = lambda: datetime.datetime(2026, 10, 8, 12)
        runtime.resolve_injection_target = lambda now: async_return((self.day.date, False))
        runtime._get_curr_period = lambda now: "daytime"
        unchanged = copy.deepcopy(self.day)
        unchanged.meta["style"] = "不同标签"
        runtime.archive = self.archive
        runtime.composer = SimpleNamespace(update_outfit=AsyncMock(return_value=unchanged))
        runtime.media = SimpleNamespace(image=SimpleNamespace(generate_image=AsyncMock()))
        result = await runtime._part_runtime_image_media_component._life_image_generate_inline(
            Event(), "在家阅读", subject_route="current_character", current_outfit_change=True,
            current_outfit_instruction="换轻薄睡衣")
        self.assertIn("本次真实换装照未生成", result)
        runtime.media.image.generate_image.assert_not_awaited()

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.tmp.name) / "life.db")
        self.runtime = Runtime(self.archive)
        self.day = DayRecord(date="2026-10-08", outfit="当前针织衫", weather_info=WeatherInfo(temp=28),
            timeline=[TimelineItem(time="11:00", activity="在家阅读", activity_kind="study", place_kind="home")],
            meta={"outfit_scene_category": "home"})
        await self.archive.save_day(self.day)

    async def asyncTearDown(self):
        await self.runtime.background.cancel_all()
        self.archive.close()
        self.tmp.cleanup()

    async def item(self, seed=1):
        item = await self.archive.upsert_style_catalog_item({
            "kind": "outfit", "source_image_hash": str(seed) * 64, "title": "睡衣",
            "description": "轻薄睡衣", "confidence": 0.95,
            "attributes": {"scene_categories": ["home", "sleep"]},
        })
        await self.archive.save_wardrobe_understanding({item.id: {
            "version": 1, "basis": understanding_key(item), "status": "understood", "confidence": 0.9, "attributes": {},
        }})
        return item

    async def drain(self):
        while self.runtime.background.tasks:
            await asyncio.gather(*list(self.runtime.background.tasks))

    async def test_persisted_dirty_inventory_can_start_care_without_old_chat_logs(self):
        item = await self.item()
        await self.archive.start_wardrobe_care([item.id], decision="dirty", event_id="old-state",
            at=str(self.runtime.now), observer="previous", duration_minutes=0, reason="已有待洗事实")
        async def model(provider, prompt, session, **kwargs):
            data = json.loads(prompt.split("【眼前内容】\n")[1])
            inventory = next(key for key, value in data["sources"].items() if value.get("kind") == "inventory_state")
            return json.dumps({"acquisition": {"decision": "keep", "reason": "先维护现有衣物"},
                "care": [{"item_ids": [item.id], "decision": "washing", "duration_minutes": 15,
                    "drying_minutes": 90, "reason": "在家清洗已脱下的脏衣", "evidence_ids": [inventory]}]})
        self.runtime.call_text_model.side_effect = model
        await self.runtime.check_wardrobe_life()
        await self.drain()
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(item.id)]
        self.assertEqual(state["condition"], "washing")
        self.assertFalse(state["available"])

    async def test_request_gap_generates_once_then_becomes_selectable(self):
        generated = None
        image = Path(self.tmp.name) / "generated.png"
        image.write_bytes(b"test image")
        self.runtime.media.image.first_configured_character_reference_image = lambda: ""
        self.runtime.media.image.can_edit_image = lambda: False
        self.runtime._generate_life_image_with_policy_retry.return_value = SimpleNamespace(path=str(image))
        async def learn(*args, **kwargs):
            nonlocal generated
            generated = await self.item(seed=2)
            return [generated]
        self.runtime._learn_style_catalog_image.side_effect = learn
        async def model(provider, prompt, session, **kwargs):
            data = json.loads(prompt.split("【眼前内容】\n")[1])
            request = next(target for target in data["targets"] if target.get("request"))
            self.assertEqual(request["request"], "换成适合现在天气的轻薄睡衣")
            choices = []
            for target in data["targets"]:
                requested = bool(target.get("request"))
                choices.append({"target_id": target["id"], "verdict": "missing" if requested and not generated else "fits",
                    "usage_scene": "sleep",
                    "outfit_basis": "selection" if requested else "current",
                    "item_ids": [generated.id] if requested and generated else [], "considered_ids": [],
                    "reason": "新睡衣已可用" if generated else "当前没有满足轻薄需求的可穿睡衣", "confidence": 0.9})
            return json.dumps({"scene_choices": choices, "acquisition": {"decision": "keep", "reason": "按需求检查库存"}})
        self.runtime.call_text_model.side_effect = model
        await self.runtime.wardrobe_life.remember_outfit_request(self.day.date, "换成适合现在天气的轻薄睡衣")
        await self.runtime.wardrobe_life.check_wardrobe_life(force_review=True)
        await self.drain()
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(snapshot["recent_jobs"][0]["status"], "completed")
        self.assertIn("轻薄睡衣", snapshot["recent_jobs"][0]["payload"]["requirement"])
        self.assertTrue(snapshot["items"][str(generated.id)]["available"])
        self.assertEqual((await self.archive.get_day(self.day.date)).outfit, "当前针织衫")
        self.runtime.check_autonomous_life_update.assert_awaited_once()
        await self.runtime.wardrobe_life.check_wardrobe_life(force_review=True)
        await self.drain()
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()
        self.runtime.check_autonomous_life_update.assert_awaited_once()

    async def test_inventory_review_uses_configured_timeout_and_clothing_only(self):
        item = await self.item()
        await self.archive.upsert_style_catalog_item({
            "kind": "hair", "source_image_hash": "h" * 64, "title": "发型资料",
            "description": "低马尾", "confidence": 0.95,
        })
        self.runtime.config.llm_timeout_seconds = 180
        async def model(provider, prompt, session, **kwargs):
            return json.dumps({"acquisition": {"decision": "keep", "reason": "记录可用衣物"}})
        self.runtime.call_text_model.side_effect = model
        await self.runtime.check_wardrobe_life()
        await self.drain()
        self.runtime.call_text_model.assert_awaited_once()
        call = self.runtime.call_text_model.call_args
        self.assertEqual(call.kwargs["timeout_seconds"], 180)
        data = json.loads(call.args[1].split("【眼前内容】\n")[1])
        self.assertEqual([i["id"] for i in data["wardrobe"]], [item.id])
        self.assertTrue((await self.archive.get_wardrobe_snapshot())["profile"].get("reviewed_at"))

    async def test_incomplete_candidate_review_is_corrected_structurally(self):
        item = await self.item()
        await self.runtime.wardrobe_life.remember_outfit_request(self.day.date, "轻薄睡衣")
        inputs = []
        async def model(provider, prompt, session, **kwargs):
            data = json.loads(prompt.split("【眼前内容】\n")[1])
            inputs.append(data)
            choices = []
            for target in data["targets"]:
                requested = bool(target.get("request"))
                choices.append({"target_id": target["id"], "confidence": 0.9,
                    "usage_scene": "sleep",
                    "verdict": ("missing" if len(inputs) == 1 else "fits") if requested else "unknown",
                    "outfit_basis": "selection", "item_ids": [item.id] if requested and len(inputs) > 1 else [],
                    "considered_ids": [], "reason": "按完整候选资料重新评价"})
            return json.dumps({"scene_choices": choices, "adopt_ids": [item.id], "adopt_reason":"选用现有睡衣",
                "acquisition":{"decision":"keep","reason":"先核对已有衣物"}})
        self.runtime.call_text_model.side_effect = model
        await self.runtime.check_wardrobe_life()
        await self.drain()
        self.assertEqual(len(inputs), 2)
        issues = inputs[1]["validation_result"]["issues"]
        self.assertIn(item.id, next(i["unassessed_ids"] for i in issues if i["code"] == "missing_requires_candidate_review"))
        self.runtime.check_autonomous_life_update.assert_awaited_once()
        self.runtime._generate_life_image_with_policy_retry.assert_not_awaited()

    async def test_appearance_scope_does_not_enqueue_clothing_request(self):
        before = copy.deepcopy(self.day)
        after = copy.deepcopy(before)
        after.meta["hair"] = "低马尾"
        wardrobe = SimpleNamespace(remember_outfit_request=AsyncMock(), check_wardrobe_life=AsyncMock())
        runtime = SimpleNamespace(config=LifeSettings.from_dict({}), generation_lock=asyncio.Lock(),
            archive=SimpleNamespace(get_day=lambda date: async_return(before)), wardrobe_life=wardrobe,
            composer=SimpleNamespace(update_outfit=AsyncMock(return_value=after)),
            _get_curr_period=lambda: "night", _target_datetime_for_command=lambda date, now: now,
            _resolve_command_target_date=lambda now: async_return((before.date, False)))
        result = await DailyLifeCommandCenter(runtime).adjust_life(
            Event(), action="update_outfit", detail="扎一个低马尾", change_scope="appearance")
        wardrobe.remember_outfit_request.assert_not_awaited()
        self.assertEqual(result.status, "partial")

    async def test_missing_day_cannot_acknowledge_request_saved(self):
        await self.archive.delete_day(self.day.date)
        saved = await self.runtime.wardrobe_life.remember_outfit_request(self.day.date, "轻薄睡衣")
        self.assertFalse(saved)

    async def test_failed_change_preserves_request_with_pending_outcome(self):
        wardrobe = SimpleNamespace(remember_outfit_request=AsyncMock(return_value=True),
            check_wardrobe_life=AsyncMock())
        runtime = SimpleNamespace(config=LifeSettings.from_dict({}), generation_lock=asyncio.Lock(),
            archive=SimpleNamespace(get_day=lambda date: async_return(self.day)), wardrobe_life=wardrobe,
            composer=SimpleNamespace(update_outfit=AsyncMock(return_value=None)),
            _get_curr_period=lambda: "night", _target_datetime_for_command=lambda date, now: now,
            _resolve_command_target_date=lambda now: async_return((self.day.date, False)))
        result = await DailyLifeCommandCenter(runtime).adjust_life(
            Event(), action="update_outfit", detail="轻薄睡衣")
        wardrobe.remember_outfit_request.assert_awaited_once_with(self.day.date, "轻薄睡衣")
        wardrobe.check_wardrobe_life.assert_awaited_once_with(force_review=True)
        self.assertEqual(result.status, "pending")
        self.assertTrue(result.facts["request_pending"])
        self.assertFalse(result.facts["clothing_changed"])
