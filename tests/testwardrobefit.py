"""衣物理解缓存、整套场景适配及补衣到换装的流程回归。"""

import asyncio
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import DayRecord, LifeArchive, make_composer

# isort: split

from core.life.dressing import digest, suitability_key, wardrobe_conditions
from core.life.wardrobefit import (
    generation_need,
    item_traits,
    understanding_current,
    understanding_key,
    validate_scene_choices,
    validate_understanding,
    wardrobe_targets,
)
from core.models import TimelineItem, WeatherInfo
from testwardrobelife import Runtime


class WardrobeFitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.tmp.name) / "life.db")
        self.runtime = Runtime(self.archive)
        self.day = DayRecord(
            date="2026-10-08", outfit="原来的衣服", weather_info=WeatherInfo(temp=24),
            timeline=[TimelineItem(time="11:00", activity="去书店看书", activity_kind="outdoor", place_kind="poi")],
        )
        await self.archive.save_day(self.day)

    async def asyncTearDown(self):
        await self.runtime.background.cancel_all()
        self.archive.close()
        self.tmp.cleanup()

    async def item(self, seed=1, kind="outfit", owned=False, attrs=None):
        item = await self.archive.upsert_style_catalog_item({
            "source_image_hash": f"{seed:064x}", "kind": kind,
            "title": "日常衣物", "description": "浅色针织上衣搭配柔软长裤",
            "attributes": attrs or {}, "confidence": 0.95,
        })
        if owned:
            await self.archive.adopt_wardrobe_items(
                [item.id], event_id=f"own:{seed}:{kind}", at=str(self.runtime.now), reason="已有衣物",
            )
        return item

    async def drain(self):
        while self.runtime.background.tasks:
            await asyncio.gather(*list(self.runtime.background.tasks))

    async def test_requested_usage_is_resolved_independently_of_dressing_location(self):
        conditions = {"current_scene_category": "home",
            "current_activity": {"place_kind": "home", "activity_kind": "personal_care"},
            "outfit_request": {"id": "accepted", "instruction": "换好外出套装去散步"}}
        targets = wardrobe_targets(conditions, [])
        suit = await self.item(owned=True, attrs={"scene_categories": ["public"]})
        snapshot = await self.archive.get_wardrobe_snapshot()
        choice = {"target_id": targets[0]["id"], "usage_scene": "public", "verdict": "fits",
            "item_ids": [suit.id], "confidence": 0.9, "reason": "适合外出散步"}
        result = validate_scene_choices([choice], targets, [suit], snapshot)
        self.assertEqual(result[0]["scene_category"], "public")
        self.assertEqual(result[0]["item_ids"], [suit.id])
        issues = []
        choice.pop("usage_scene")
        self.assertEqual(validate_scene_choices([choice], targets, [suit], snapshot, issues=issues), [])
        self.assertEqual(issues[0]["code"], "usage_scene_required")

    async def test_home_unsuitability_does_not_discard_outdoor_inventory(self):
        from core.life.inspiration import StyleCatalogComponent
        conditions = {"current_scene_category": "home"}
        suit = await self.item(owned=True, attrs={"scene_categories": ["public"]})
        snapshot = await self.archive.get_wardrobe_snapshot()
        snapshot["profile"] = {"context_key": suitability_key(conditions),
            "suitability": {str(suit.id): {"verdict": "unsuitable"}}}
        self.assertTrue(StyleCatalogComponent._style_catalog_item_eligible(
            suit, snapshot, scene_category="public", conditions=conditions))
        self.assertTrue(StyleCatalogComponent._style_catalog_item_eligible(
            suit, snapshot, conditions=conditions))
        snapshot["items"][str(suit.id)]["available"] = False
        self.assertFalse(StyleCatalogComponent._style_catalog_item_eligible(
            suit, snapshot, scene_category="public", conditions=conditions))

    async def test_understanding_batches_all_items_and_reuses_unchanged_results(self):
        items = [await self.item(seed=i + 1, owned=i == 0, attrs={"custom": "保留"}) for i in range(35)]
        await self.archive.start_wardrobe_care(
            [items[0].id], decision="dirty", event_id="dirty", at=str(self.runtime.now),
            observer="test", duration_minutes=0, reason="实际待洗",
        )

        async def model(provider, prompt, session, **kwargs):
            batch = json.loads(prompt.split("【眼前内容】\n")[1])
            return json.dumps({"items": [
                {"item_id": i["item_id"], "status": "understood", "confidence": 0.9,
                 "attributes": {"scene_categories": ["home"], "weather_fit": ["温暖"]}}
                for i in batch
            ]})

        self.runtime.call_text_model.side_effect = model
        self.runtime.wardrobe_life._review_wardrobe = AsyncMock()
        await self.runtime.check_wardrobe_life()
        await self.drain()
        self.assertEqual(self.runtime.call_text_model.await_count, 3)
        catalog = await self.archive.get_style_catalog_items(status="active", limit=500)
        self.assertTrue(all(understanding_current(item) for item in catalog))
        self.assertTrue(all(item_traits(item)["scene_categories"] == ["home"] for item in catalog))
        self.assertTrue(all(item.attributes["custom"] == "保留" for item in catalog))
        state = (await self.archive.get_wardrobe_snapshot())["items"][str(items[0].id)]
        self.assertEqual((state["ownership"], state["condition"]), ("owned", "dirty"))

        await self.runtime.check_wardrobe_life()
        await self.drain()
        self.assertEqual(self.runtime.call_text_model.await_count, 3)
        await self.archive.revise_style_catalog_item(
            items[1].id, title="修改后", description="新提供的衣物说明",
            attributes=items[1].attributes, confidence=0.95, status="active",
        )
        await self.runtime.check_wardrobe_life()
        await self.drain()
        self.assertEqual(self.runtime.call_text_model.await_count, 4)

    async def test_stale_understanding_cannot_overwrite_edited_item_or_original_tags(self):
        item = await self.item(attrs={"scene_categories": ["public"], "custom": 42})
        record = validate_understanding({
            "status": "understood", "confidence": 0.9,
            "attributes": {"scene_categories": ["home"]},
        }, item)
        self.assertEqual(await self.archive.save_wardrobe_understanding({item.id: record}), 1)
        current = (await self.archive.get_style_catalog_items(ids=[item.id]))[0]
        self.assertEqual(item_traits(current)["scene_categories"], ["public"])
        await self.archive.revise_style_catalog_item(
            item.id, title="新资料", description="修改后的衣物描述",
            attributes={"custom": 42}, confidence=0.95, status="active",
        )
        self.assertEqual(await self.archive.save_wardrobe_understanding({item.id: record}), 0)
        current = (await self.archive.get_style_catalog_items(ids=[item.id]))[0]
        self.assertNotIn("scene_categories", item_traits(current))

    async def test_unreviewed_items_and_partial_coverage_cannot_be_counted_as_complete(self):
        top = await self.item(kind="top", owned=True)
        bottom = await self.item(seed=2, kind="bottom", owned=True)
        candidate = await self.item(seed=3)
        items = [top, bottom, candidate]
        snapshot = await self.archive.get_wardrobe_snapshot()
        targets = wardrobe_targets({"current_activity": {"place_kind": "poi"}}, [])
        value = {"target_id": targets[0]["id"], "verdict": "fits", "confidence": 0.9,
                 "reason": "完整搭配适合当前活动", "item_ids": [top.id]}
        self.assertEqual(validate_scene_choices([], targets, items, snapshot), [])
        self.assertEqual(validate_scene_choices([value], targets, items, snapshot), [])
        value.update(item_ids=[top.id, bottom.id])
        self.assertEqual(validate_scene_choices([value], targets, items, snapshot)[0]["verdict"], "fits")
        value.update(verdict="missing", item_ids=[], considered_ids=[top.id, bottom.id])
        self.assertEqual(validate_scene_choices([value], targets, items, snapshot), [])
        value["considered_ids"].append(candidate.id)
        self.assertEqual(validate_scene_choices([value], targets, items, snapshot)[0]["verdict"], "missing")

    async def test_uncertain_fit_is_cached_as_unknown_without_claiming_coverage_or_need(self):
        item = await self.item(owned=True)
        snapshot = await self.archive.get_wardrobe_snapshot()
        targets = wardrobe_targets({"current_activity": {"place_kind": "home"}}, [])
        for verdict in ("fits", "missing", "unknown"):
            choices = validate_scene_choices([{
                "target_id": targets[0]["id"], "verdict": verdict, "item_ids": [item.id],
                "confidence": 0.6, "reason": "用途资料仍不充分，不能确定是否适合",
            }], targets, [item], snapshot)
            self.assertEqual(choices[0]["verdict"], "unknown")
            self.assertEqual(choices[0]["item_ids"], [])
            self.assertEqual(generation_need({"motive": "need"}, choices, {}), {})

    async def test_unique_pair_is_only_completed_from_a_fresh_verified_combination(self):
        top = await self.item(kind="top", owned=True)
        bottom = await self.item(seed=2, kind="bottom", owned=True)
        composer, _, _, _ = make_composer([])
        composer.archive = self.archive
        conditions = self.runtime.wardrobe_life._wardrobe_conditions(self.day, self.runtime.now)
        self.assertEqual(await composer._style_catalog_resolve_new_outfit_reference_ids(
            [], scene_category="public", conditions=conditions,
        ), [])
        snapshot = await self.archive.get_wardrobe_snapshot()
        targets = wardrobe_targets(conditions, [])
        choices = validate_scene_choices([{
            "target_id": targets[0]["id"], "verdict": "fits", "item_ids": [top.id, bottom.id],
            "reason": "两件搭配适合当前外出", "confidence": 0.9,
        }], targets, [top, bottom], snapshot)
        await self.archive.save_wardrobe_review({
            "context_key": suitability_key(conditions), "scene_choices": choices,
        }, expected_revision=0, at=str(self.runtime.now))
        self.assertEqual(await composer._style_catalog_resolve_new_outfit_reference_ids(
            [], scene_category="public", conditions=conditions,
        ), [top.id, bottom.id])
        await self.archive.revise_style_catalog_item(
            top.id, title="重新核对后的上装", description="更新后的真实款式",
            attributes=top.attributes, confidence=0.95, status="active",
        )
        self.assertEqual(await composer._style_catalog_resolve_new_outfit_reference_ids(
            [], scene_category="public", conditions=conditions,
        ), [])
        await self.archive.revise_style_catalog_item(
            top.id, title=top.title, description=top.description,
            attributes=top.attributes, confidence=0.95, status="active",
        )
        await self.archive.start_wardrobe_care(
            [bottom.id], decision="dirty", event_id="dirty", at=str(self.runtime.now),
            observer="test", duration_minutes=0, reason="后来待洗",
        )
        self.assertEqual(await composer._style_catalog_resolve_new_outfit_reference_ids(
            [], scene_category="public", conditions=conditions,
        ), [])

    async def test_dirty_current_clothes_can_continue_but_cannot_be_worn_again(self):
        item = await self.item(owned=True)
        self.day.meta.update(
            style_catalog_reference_ids=str(item.id), outfit_fact_confirmed_at=str(self.runtime.now),
        )
        await self.archive.save_day(self.day)
        await self.archive.record_wardrobe_wear(self.day, event_id="wear-current")
        await self.archive.start_wardrobe_care(
            [item.id], decision="dirty", event_id="dirty-current", at=str(self.runtime.now),
            observer="test", duration_minutes=0, reason="已穿着待洗",
        )
        targets = wardrobe_targets({"current_activity": {"place_kind": "poi"}}, [])
        choices = [{"target_id": targets[0]["id"], "verdict": "fits", "confidence": 0.9,
                    "reason": "继续穿着，不重新穿回", "item_ids": [item.id]}]
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertTrue(snapshot["items"][str(item.id)]["currently_worn"])
        self.assertEqual(validate_scene_choices(choices, targets, [item], snapshot)[0]["verdict"], "fits")
        other = await self.item(seed=2, owned=True)
        self.day.meta.update(
            style_catalog_reference_ids=str(other.id),
            outfit_fact_confirmed_at=str(self.runtime.now + datetime.timedelta(minutes=1)),
        )
        await self.archive.save_day(self.day)
        await self.archive.record_wardrobe_wear(self.day, event_id="wear-other")
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertFalse(snapshot["items"][str(item.id)]["currently_worn"])
        self.assertEqual(validate_scene_choices(choices, targets, [item], snapshot), [])

    async def test_explicit_current_basis_resolves_continuation_and_local_changes(self):
        top = await self.item(kind="top", owned=True)
        bottom = await self.item(seed=2, kind="bottom", owned=True)
        shoes = await self.item(seed=3, kind="footwear", owned=True)
        self.day.meta.update(
            style_catalog_reference_ids=f"{top.id},{bottom.id}",
            outfit_fact_confirmed_at=str(self.runtime.now),
        )
        await self.archive.save_day(self.day)
        await self.archive.record_wardrobe_wear(self.day, event_id="current-pair")
        snapshot = await self.archive.get_wardrobe_snapshot()
        targets = wardrobe_targets({"current_activity": {"place_kind": "poi"}}, [])
        value = {"target_id": targets[0]["id"], "verdict": "fits", "outfit_basis": "current",
                 "item_ids": [shoes.id], "reason": "保持主体衣物，只调整外出鞋履", "confidence": 0.9}
        items = [top, bottom, shoes]
        current = [top.id, bottom.id]
        self.assertEqual(validate_scene_choices([value], targets, items, snapshot), [])
        choices = validate_scene_choices([value], targets, items, snapshot, current_ids=current)
        self.assertEqual(choices[0]["item_ids"], [top.id, bottom.id, shoes.id])
        value["item_ids"] = []
        choices = validate_scene_choices([value], targets, items, snapshot, current_ids=current)
        self.assertEqual(choices[0]["item_ids"], current)
        value.pop("outfit_basis")
        self.assertEqual(validate_scene_choices([value], targets, items, snapshot, current_ids=current), [])
        value["outfit_basis"] = "current"
        snapshot["items"][str(bottom.id)]["currently_worn"] = False
        self.assertEqual(validate_scene_choices([value], targets, items, snapshot, current_ids=current), [])

    async def test_review_keeps_confirmed_current_pair_when_model_explicitly_continues(self):
        top = await self.item(kind="top", owned=True)
        bottom = await self.item(seed=2, kind="bottom", owned=True)
        self.day.meta.update(
            style_catalog_reference_ids=f"{top.id},{bottom.id}",
            outfit_fact_confirmed_at=str(self.runtime.now),
        )
        await self.archive.save_day(self.day)
        await self.archive.record_wardrobe_wear(self.day, event_id="keep-current-pair")
        conditions = self.runtime.wardrobe_life._wardrobe_conditions(self.day, self.runtime.now)
        snapshot = await self.archive.get_wardrobe_snapshot()
        target = wardrobe_targets(conditions, [])[0]
        self.runtime.call_text_model.return_value = json.dumps({
            "scene_choices": [{"target_id": target["id"], "verdict": "fits",
                "outfit_basis": "current", "item_ids": [], "confidence": 0.9,
                "reason": "当前穿搭已适合活动和天气，无需更换"}],
            "acquisition": {"decision": "keep", "reason": "继续穿当前合适的衣物"},
        })
        inventory_key = digest({
            k: {field: v.get(field) for field in ("ownership", "condition")}
            for k, v in snapshot["items"].items()
        })
        await self.runtime.wardrobe_life._review_wardrobe(
            self.day, conditions, snapshot, inventory_key, digest([]),
        )
        profile = (await self.archive.get_wardrobe_snapshot())["profile"]
        self.assertTrue(profile["scene_review_complete"])
        self.assertEqual(profile["scene_choices"][0]["item_ids"], [top.id, bottom.id])

    async def test_uncatalogued_current_outfit_is_not_replaced_by_yesterdays_wear(self):
        old = await self.item(owned=True)
        self.day.meta.update(style_catalog_reference_ids=str(old.id), outfit_fact_confirmed_at=str(self.runtime.now))
        await self.archive.save_day(self.day)
        await self.archive.record_wardrobe_wear(self.day, event_id="old-wear")
        self.day.outfit = "已确认的白色短袖与浅蓝短裤"
        self.day.meta.pop("style_catalog_reference_ids")
        self.day.meta["outfit_fact_confirmed_at"] = str(self.runtime.now + datetime.timedelta(minutes=1))
        await self.archive.save_day(self.day)
        self.assertTrue(await self.archive.record_wardrobe_wear(self.day, event_id="uncatalogued"))
        self.assertFalse(await self.archive.record_wardrobe_wear(self.day, event_id="repeat"))
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertFalse(snapshot["items"][str(old.id)]["currently_worn"])
        targets = wardrobe_targets({"current_activity": {"place_kind": "home"}}, [])
        value = {"target_id": targets[0]["id"], "verdict": "fits", "outfit_basis": "current",
                 "item_ids": [], "confidence": 0.9, "reason": "已确认的当前穿着适合居家活动"}
        choices = validate_scene_choices([value], targets, [old], snapshot, current_outfit=self.day.outfit)
        self.assertEqual(choices[0]["item_ids"], [])
        self.assertEqual(choices[0]["current_outfit"], self.day.outfit)
        value["item_ids"] = [old.id]
        self.assertEqual(validate_scene_choices([value], targets, [old], snapshot, current_outfit=self.day.outfit), [])
        # 延续未编入衣橱的当前穿着，不能变成供自主换装使用的完整候选。
        composer, _, _, _ = make_composer([])
        composer.archive = self.archive
        conditions = self.runtime.wardrobe_life._wardrobe_conditions(self.day, self.runtime.now)
        choices[0].update(current=True, scene_category="home")
        await self.archive.save_wardrobe_review({
            "context_key": suitability_key(conditions), "scene_choices": choices,
        }, expected_revision=0, at=str(self.runtime.now))
        self.assertEqual(await composer._style_catalog_resolve_new_outfit_reference_ids(
            [], scene_category="home", conditions=conditions,
        ), [])

    async def test_late_previous_day_cannot_restore_wear_after_uncatalogued_change(self):
        item = await self.item(owned=True)
        self.day.meta.update(style_catalog_reference_ids=str(item.id), outfit_fact_confirmed_at=str(self.runtime.now))
        await self.archive.save_day(self.day)
        tomorrow = DayRecord(date="2026-10-09", outfit="另有已确认的衣物", meta={
            "outfit_fact_confirmed_at": "2026-10-09 08:00:00",
        })
        await self.archive.save_day(tomorrow)
        await self.archive.record_wardrobe_wear(tomorrow, event_id="tomorrow")
        await self.archive.record_wardrobe_wear(self.day, event_id="late-yesterday")
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertFalse(snapshot["items"][str(item.id)]["currently_worn"])

    async def test_real_need_has_its_own_cadence_and_same_gap_still_deduplicates(self):
        now = self.runtime.now
        await self.archive.enqueue_wardrobe_job(
            "exploration", {"automatic": True, "motive": "interest", "requested_at": str(now)}, at=str(now),
        )
        await self.archive.claim_wardrobe_job("exploration", "test", now=now)
        await self.archive.update_wardrobe_job(
            "exploration", "test", status="completed", progress={"submitted_at": str(now)}, at=str(now), release=True,
        )
        conditions = self.runtime.wardrobe_life._wardrobe_conditions(self.day, now)
        target = wardrobe_targets(conditions, [])[0]
        need = generation_need({"motive": "need"}, [{
            **target, "verdict": "missing", "reason": "缺少适合外出的完整衣物", "confidence": 0.9,
        }], conditions)
        payload = {"automatic": True, "motive": "need", "verified_need": need, "requested_at": str(now)}
        self.assertFalse(await self.archive.wardrobe_auto_requested_since(str(now - datetime.timedelta(days=1)), payload=payload))
        self.assertTrue(await self.archive.wardrobe_auto_requested_since(str(now - datetime.timedelta(days=1)), payload={"automatic": True, "motive": "need"}))
        await self.archive.save_wardrobe_review(
            {"acquisition": {"decision": "generate"}}, expected_revision=0, at=str(now),
            generation={"job_id": "practical", "payload": payload},
        )
        snap = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(snap["profile"]["acquisition"]["status"], "queued")
        self.assertTrue(await self.archive.wardrobe_auto_requested_since(str(now - datetime.timedelta(days=1)), payload=payload))

    async def test_cancelled_plan_does_not_submit_a_delayed_practical_generation(self):
        payload = {"automatic": True, "motive": "need", "verified_need": {
            "target_id": "removed", "context_key": "removed", "verdict": "missing", "reason": "原计划缺衣",
        }}
        await self.archive.enqueue_wardrobe_job("removed", payload, at=str(self.runtime.now))
        await self.runtime.wardrobe_life._run_wardrobe_job("removed")
        self.runtime._generate_life_image_with_policy_retry.assert_not_awaited()
        self.runtime._edit_life_image_with_policy_retry.assert_not_awaited()
        snap = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(snap["recent_jobs"][0]["status"], "cancelled")

    async def queue_verified_need(self, job_id="need"):
        conditions = self.runtime.wardrobe_life._wardrobe_conditions(self.day, self.runtime.now)
        target = wardrobe_targets(conditions, [])[0]
        choice = {**target, "verdict": "missing", "item_ids": [],
                  "reason": "没有适合该场景的完整衣物", "confidence": 0.9}
        snapshot = await self.archive.get_wardrobe_snapshot()
        items = await self.archive.get_style_catalog_items(status="active", limit=500)
        profile = {
            "context_key": suitability_key(conditions), "scene_choices": [choice],
            "inventory_key": digest({
                k: {field: v.get(field) for field in ("ownership", "condition")}
                for k, v in snapshot["items"].items()
            }),
            "catalog_key": digest({item.id: understanding_key(item) for item in items}),
        }
        await self.archive.save_wardrobe_review(
            profile, expected_revision=snapshot["profile_revision"], at=str(self.runtime.now),
        )
        await self.archive.enqueue_wardrobe_job(job_id, {
            "automatic": True, "motive": "need", "conditions": conditions,
            "verified_need": generation_need({"motive": "need"}, [choice], conditions),
        }, at=str(self.runtime.now))
        return profile

    async def test_changed_weather_or_inventory_waits_for_review_before_generation(self):
        service = self.runtime.wardrobe_life
        service.check_wardrobe_life = AsyncMock()
        for change in ("weather", "inventory", "catalog"):
            with self.subTest(change=change):
                item = await self.item(seed=20, owned=True)
                await self.queue_verified_need(change)
                if change == "weather":
                    self.day.weather_info.temp = 6
                    await self.archive.save_day(self.day)
                elif change == "inventory":
                    await self.archive.start_wardrobe_care(
                        [item.id], decision="dirty", event_id="changed-condition", at=str(self.runtime.now),
                        observer="test", duration_minutes=0, reason="穿着后待洗",
                    )
                else:
                    await self.archive.revise_style_catalog_item(
                        item.id, title="更新后的衣物资料", description="重新确认后的款式",
                        attributes=item.attributes, confidence=0.95, status="active",
                    )
                await service._run_wardrobe_job(change)
                snap = await self.archive.get_wardrobe_snapshot()
                job = next(j for j in snap["jobs"] if j["job_id"] == change)
                self.assertEqual(job["status"], "pending")
                self.assertFalse(job["lease_owner"])
        self.assertEqual(service.check_wardrobe_life.await_count, 3)
        self.runtime._generate_life_image_with_policy_retry.assert_not_awaited()
        self.runtime._edit_life_image_with_policy_retry.assert_not_awaited()

    async def test_newly_satisfied_need_cancels_an_older_generation(self):
        item = await self.item(owned=True)
        profile = await self.queue_verified_need()
        snapshot = await self.archive.get_wardrobe_snapshot()
        target = profile["scene_choices"][0]
        profile["scene_choices"] = validate_scene_choices([{
            "target_id": target["id"], "verdict": "fits", "item_ids": [item.id],
            "reason": "已有衣物现在可以满足需要", "confidence": 0.9,
        }], [target], [item], snapshot)
        await self.archive.save_wardrobe_review(
            profile, expected_revision=snapshot["profile_revision"], at=str(self.runtime.now),
        )
        await self.runtime.wardrobe_life._run_wardrobe_job("need")
        self.runtime._generate_life_image_with_policy_retry.assert_not_awaited()
        self.runtime._edit_life_image_with_policy_retry.assert_not_awaited()
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(snapshot["recent_jobs"][0]["status"], "cancelled")

    async def test_current_scene_fallback_covers_unlocated_activity(self):
        self.day.timeline[0].place_kind = "none"
        self.day.meta["outfit_scene_category"] = "home"
        conditions = wardrobe_conditions(self.day, self.runtime.now)
        targets = wardrobe_targets(conditions, [])
        self.assertEqual([t["scene_category"] for t in targets], ["home"])
        self.assertTrue(targets[0]["current"])
        self.assertNotEqual(suitability_key(conditions), suitability_key({
            **conditions, "current_scene_category": "public",
        }))
        self.day.timeline[0].place_kind = "poi"
        conditions = wardrobe_conditions(self.day, self.runtime.now)
        self.assertEqual(wardrobe_targets(conditions, [])[0]["scene_category"], "public")
        self.assertEqual(wardrobe_targets({"current_activity": {}}, []), [])

    async def test_forced_shortage_review_ignores_routine_interval_but_respects_retry(self):
        self.runtime.call_text_model.return_value = json.dumps({
            "acquisition": {"decision": "keep", "reason": "先评价已有衣物"},
        })
        await self.runtime.check_wardrobe_life()
        await self.drain()
        service = self.runtime.wardrobe_life
        service._review_wardrobe = AsyncMock()
        # 近期已评估且条件未变化时，普通周期不会再次调用模型。
        service._review_retry_after = None
        await self.runtime.check_wardrobe_life()
        await self.drain()
        service._review_wardrobe.assert_not_awaited()
        await service.check_wardrobe_life(force_review=True)
        await self.drain()
        service._review_wardrobe.assert_awaited_once()
        service._review_retry_after = self.runtime.now + datetime.timedelta(minutes=10)
        await service.check_wardrobe_life(force_review=True)
        await self.drain()
        service._review_wardrobe.assert_awaited_once()

    async def test_invalid_selection_uses_existing_clothes_without_requesting_generation(self):
        owned = await self.item(owned=True, attrs={"scene_categories": ["public"]})
        candidate = await self.item(seed=2, attrs={
            "scene_categories": ["public"], "wardrobe_job": "pending",
        })
        result = {
            "outfit_decision": "change", "current_outfit_basis": "stored", "scene_category": "public",
            "style_pool": "outfit_styles", "style": "轻便外出", "outfit": candidate.description,
            "catalog_reference_ids": [candidate.id], "reason": "准备去书店",
        }
        composer, provider, _, _ = make_composer([
            json.dumps(result), json.dumps({**result, "catalog_reference_ids": [owned.id]}),
        ])
        composer.archive = self.archive
        composer.wardrobe_shortage_callback = AsyncMock(return_value=True)
        changed = await composer.update_outfit(self.day.date, "daytime", current_time=self.runtime.now)
        self.assertEqual(changed.meta["style_catalog_reference_ids"], str(owned.id))
        self.assertEqual(len(provider.prompts), 2)
        composer.wardrobe_shortage_callback.assert_not_awaited()

    async def test_gap_generation_adoption_review_and_actual_change_form_one_loop(self):
        generated = None
        path = Path(self.tmp.name) / "new.png"
        path.write_bytes(b"test image")
        self.runtime.media.image.first_configured_character_reference_image = lambda: ""
        self.runtime.media.image.can_edit_image = lambda: False
        self.runtime._generate_life_image_with_policy_retry.return_value = SimpleNamespace(path=str(path))

        async def learn(*args, **kwargs):
            nonlocal generated
            generated = await self.item(seed=2, attrs={"scene_categories": ["public"], "weather_fit": ["温暖"]})
            return [generated]

        self.runtime._learn_style_catalog_image.side_effect = learn

        async def model(provider, prompt, session, **kwargs):
            data = json.loads(prompt.split("【眼前内容】\n")[1])
            if "traits_" in session:
                return json.dumps({"items": [{
                    "item_id": i["item_id"], "status": "understood", "confidence": 0.9,
                    "attributes": {"scene_categories": ["public"]},
                } for i in data]})
            target = data["targets"][0]
            return json.dumps({
                "scene_choices": [{"target_id": target["id"], "verdict": "fits" if generated else "missing",
                    "item_ids": [generated.id] if generated else [], "considered_ids": [candidate.id],
                    "confidence": 0.9, "reason": "新衣已满足外出需要" if generated else "没有可用的完整外出衣物"}],
                "acquisition": {"decision": "keep", "reason": "先从现有衣橱选择"},
            })

        self.runtime.call_text_model.side_effect = model
        candidate = await self.item(attrs={
            "scene_categories": ["public"], "wardrobe_job": "pending",
        })
        composer, provider, _, _ = make_composer([json.dumps({
            "outfit_decision": "change", "current_outfit_basis": "stored", "scene_category": "public",
            "style_pool": "outfit_styles", "style": "轻便外出", "outfit": candidate.description,
            "catalog_reference_ids": [candidate.id], "reason": "准备去书店但衣橱没有可穿搭配",
        })])
        composer.archive = self.archive
        composer.wardrobe_shortage_callback = lambda: self.runtime.wardrobe_life.check_wardrobe_life(
            force_review=True,
        )
        reviewed = await composer.update_outfit(self.day.date, "daytime", current_time=self.runtime.now)
        self.assertIsNotNone(reviewed)
        self.assertEqual(reviewed.outfit, "原来的衣服")
        self.assertEqual(len(provider.prompts), 1)
        await self.drain()
        self.runtime._generate_life_image_with_policy_retry.assert_awaited_once()
        prompt = self.runtime._generate_life_image_with_policy_retry.await_args.args[1]
        context = json.loads(prompt.split("生活条件与审美（只作背景，不将参考图旧衣当成新衣）：", 1)[1])
        self.assertEqual(context["target"]["scene_category"], "public")
        self.assertEqual(context["target"]["plan"]["activity"], "去书店看书")
        snapshot = await self.archive.get_wardrobe_snapshot()
        self.assertEqual(snapshot["items"][str(generated.id)]["ownership"], "owned")
        self.assertEqual(snapshot["recent_jobs"][0]["status"], "completed")
        day = await self.archive.get_day(self.day.date)
        self.assertTrue(day.meta.get("wardrobe_selection_key"))
        self.assertEqual(day.outfit, "原来的衣服")
        composer, _, _, _ = make_composer([json.dumps({
            "outfit_decision": "change", "current_outfit_basis": "stored", "scene_category": "public",
            "style_pool": "outfit_styles", "style": "轻便外出", "outfit": generated.description,
            "catalog_reference_ids": [generated.id], "reason": "出门前换上已入橱的合适衣物",
        })])
        composer.archive = self.archive
        changed = await composer.update_outfit(day.date, "daytime", current_time=self.runtime.now)
        self.assertEqual(changed.meta["style_catalog_reference_ids"], str(generated.id))
        self.assertTrue((await self.archive.get_wardrobe_snapshot())["items"][str(generated.id)]["currently_worn"])
