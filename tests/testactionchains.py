import datetime
import json
import unittest
from unittest.mock import AsyncMock

from support import TimelineItem, make_composer
import testcontinuouslife as continuous_fixture
from core.life.actionchain import actions, ready
from core.life.dressing import suitability_key, wardrobe_conditions
from core.models import LifeActionIntent


class ActionChainsTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = continuous_fixture.ContinuousLifeTest.asyncSetUp
    asyncTearDown = continuous_fixture.ContinuousLifeTest.asyncTearDown
    tick = continuous_fixture.ContinuousLifeTest.tick

    async def decide(self, **fields):
        day = await self.archive.get_day(self.runtime.date)
        world = await self.archive.get_continuous_life()
        await self.runtime.continuous_life._apply_continuous_decision(
            day,
            world,
            {"decision": "start", "reason": "依据当前生活安排逐步执行", **fields},
            self.runtime.now,
        )
        return (await self.archive.get_continuous_life()).get("run")

    async def choose_pending(self):
        runtime = self.runtime
        runtime.composer._get_persona = AsyncMock(return_value="角色")
        runtime.composer._collect_recent_chat_context = AsyncMock(return_value="无")
        runtime.get_text_provider = AsyncMock(return_value=object())
        runtime.close_text_session = AsyncMock()
        runtime.call_text_model = AsyncMock(
            return_value='{"decision":"wait","reason":"根据新事实确认下一步"}'
        )
        day = await self.archive.get_day(runtime.date)
        world = await self.archive.get_continuous_life()
        decision = await runtime.continuous_decision.choose(day, world, runtime.now)
        return decision["_candidates"]

    async def select(self, candidate):
        return await self.decide(
            action_id=candidate["action_id"], _candidates=[candidate]
        )

    async def wardrobe(self):
        composer, _, _, _ = make_composer([])
        composer.archive = self.archive
        composer.domains = self.runtime.domains
        self.runtime.composer = composer
        item = await self.archive.upsert_style_catalog_item(
            {
                "kind": "outfit",
                "description": "米白丝缎吊带睡裙",
                "source_image_hash": "a" * 64,
                "confidence": 0.95,
            }
        )
        await self.archive.adopt_wardrobe_items(
            [item.id], event_id="own", at=str(self.now), reason="已有睡裙"
        )
        return item

    def step(self, kind, target, *, index=None, payload=None):
        return {
            "owner": "self",
            "action_type": kind,
            "target": target,
            "duration_minutes": 1,
            "timeline_index": index,
            "payload": payload or {},
        }

    async def bath_plan(self, *, index=None):
        item = await self.wardrobe()
        steps = [
            self.step("groom", "洗温水澡", index=index),
            self.step(
                "change_outfit",
                "模型写的衣服不作为事实",
                index=index,
                payload={"catalog_reference_ids": [item.id], "scene_category": "sleep"},
            ),
        ]
        run = await self.decide(new_actions=steps)
        self.assertIsNotNone(run)
        return item, run

    async def test_bath_then_catalog_outfit_have_separate_effects_and_receipts(self):
        item, first = await self.bath_plan()
        await self.tick()
        day = await self.archive.get_day(self.runtime.date)
        self.assertEqual(day.outfit, "居家服")
        candidates = await self.choose_pending()
        self.assertEqual(
            [item["action_type"] for item in candidates], ["change_outfit"]
        )
        second = await self.select(candidates[0])
        self.assertEqual((await self.archive.get_day(day.date)).outfit, "居家服")
        await self.tick()
        day = await self.archive.get_day(day.date)
        self.assertIn("米白丝缎吊带睡裙", day.outfit)
        self.assertNotIn("模型写的", day.outfit)
        self.assertIn(str(item.id), day.meta["style_catalog_reference_ids"])
        for run in (first, second):
            receipts = await self.archive.get_life_action_receipts(
                action_id=run["action"]["action_id"]
            )
            self.assertEqual(len(receipts), 1)
        await self.tick()
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 2)

    async def test_autonomous_outfit_and_grooming_commit_only_after_execution(self):
        item = await self.wardrobe()
        day = await self.archive.get_day(self.runtime.date)
        day.meta.update({
            "hair_style": "湿发披散", "hair": "湿发贴着肩背",
            "outfit_components": json.dumps({
                "main_clothing": {"state": "worn", "description": "居家服"},
            }),
        })
        await self.archive.save_day(day)
        first = await self.decide(new_actions=[
            self.step("groom", "吹干头发，扎低马尾", payload={"hair_style": "低马尾", "hair": "头发吹干后低低扎起"}),
            self.step("change_outfit", "换上已有睡裙", payload={
                "catalog_reference_ids": [item.id], "scene_category": "sleep",
                "outfit_components": {"main_clothing": {"state": "unknown", "description": "米白丝缎吊带睡裙"}},
            }),
        ])
        self.assertIsNotNone(first)
        started = await self.archive.get_day(day.date)
        self.assertEqual(started.meta["hair_style"], "湿发披散")
        self.assertEqual(started.outfit, "居家服")
        await self.tick()
        groomed = await self.archive.get_day(day.date)
        self.assertEqual(groomed.meta["hair_style"], "低马尾")
        self.assertEqual(groomed.meta["hair"], "头发吹干后低低扎起")
        self.assertEqual(groomed.outfit, "居家服")
        second = await self.select((await self.choose_pending())[0])
        self.assertIsNotNone(second)
        self.assertEqual((await self.archive.get_day(day.date)).outfit, "居家服")
        await self.tick()
        dressed = await self.archive.get_day(day.date)
        self.assertEqual(dressed.outfit, "米白丝缎吊带睡裙")
        self.assertEqual(dressed.meta["hair_style"], "低马尾")
        self.assertEqual(json.loads(dressed.meta["outfit_components"])["main_clothing"]["state"], "worn")
        states = (await self.archive.get_wardrobe_snapshot())["items"]
        self.assertTrue(states[str(item.id)]["currently_worn"])
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 2)

    async def test_compound_timeline_only_finishes_after_last_step_even_when_delayed(
        self,
    ):
        day = await self.archive.get_day(self.runtime.date)
        day.timeline = [
            TimelineItem(time="01:00", activity="洗澡后换睡裙", duration_minutes=2)
        ]
        await self.archive.save_day(day)
        await self.bath_plan(index=0)
        await self.tick()
        saved = await self.archive.get_day(day.date)
        self.assertEqual(saved.timeline[0].execution_state, "active")
        self.runtime.now += datetime.timedelta(minutes=12)
        candidates = await self.choose_pending()
        self.assertEqual(len(candidates), 1)
        self.assertIsNotNone(await self.select(candidates[0]))
        await self.tick()
        saved = await self.archive.get_day(day.date)
        self.assertEqual(saved.timeline[0].execution_state, "completed")
        self.assertIn("米白丝缎吊带睡裙", saved.outfit)

    async def test_cooking_does_not_count_as_eating_and_later_step_cannot_jump_ahead(
        self,
    ):
        await self.archive.adjust_pantry_item("鸡蛋", 2, unit="个")
        await self.archive.mutate_continuous_life(
            self.runtime.date, lambda day, world: world["body"].update(hunger=80)
        )
        await self.decide(
            new_actions=[
                self.step(
                    "cook",
                    "煮鸡蛋",
                    payload={
                        "ingredients": [{"name": "鸡蛋", "quantity": 1, "unit": "个"}]
                    },
                ),
                self.step("meal", "吃鸡蛋"),
            ]
        )
        day = await self.archive.get_day(self.runtime.date)
        self.assertFalse(ready(day, actions(day)[-1]))
        await self.tick()
        world = await self.archive.get_continuous_life()
        self.assertGreater(world["body"]["hunger"], 70)
        candidates = await self.choose_pending()
        self.assertEqual([item["action_type"] for item in candidates], ["meal"])
        self.assertIsNotNone(await self.select(candidates[0]))
        await self.tick()
        self.assertLess(
            (await self.archive.get_continuous_life())["body"]["hunger"], 30
        )
        self.assertEqual((await self.archive.get_pantry_items())[0]["quantity"], 1)

    async def test_autonomous_purchase_and_cooking_follow_actual_place_and_stock(self):
        self.runtime.domains.estimate_route = AsyncMock(
            return_value={"duration_seconds": 60}
        )
        await self.archive.mutate_day(
            self.runtime.date, lambda day: day.meta.update(current_place="家")
        )
        await self.archive.mutate_continuous_life(
            self.runtime.date, lambda day, world: world["body"].update(hunger=80)
        )
        first = await self.decide(new_actions=[
            self.step("move", "去菜市场", payload={"origin": "家", "destination": "菜市场"}),
            self.step("purchase", "买鸡蛋", payload={
                "place": "菜市场", "pantry_items": [{"name": "鸡蛋", "quantity": 2, "unit": "个"}],
            }),
            self.step("move", "买完回家", payload={"origin": "菜市场", "destination": "家"}),
            self.step("cook", "煮一个鸡蛋", payload={
                "place": "家", "ingredients": [{"name": "鸡蛋", "quantity": 1, "unit": "个"}],
            }),
            self.step("meal", "吃煮鸡蛋", payload={"place": "家"}),
        ])
        self.assertIsNotNone(first)
        self.assertEqual((await self.archive.get_day(self.runtime.date)).meta["current_place"], "家")
        self.assertEqual(await self.archive.get_pantry_items(), [])
        await self.tick()
        self.assertEqual((await self.archive.get_day(self.runtime.date)).meta["current_place"], "菜市场")
        for kind in ("purchase", "move", "cook", "meal"):
            candidates = await self.choose_pending()
            self.assertEqual([item["action_type"] for item in candidates], [kind])
            self.assertIsNotNone(await self.select(candidates[0]))
            if kind == "purchase":
                self.assertEqual(await self.archive.get_pantry_items(), [])
            await self.tick()
            if kind == "purchase":
                self.assertEqual((await self.archive.get_pantry_items())[0]["quantity"], 2)
            elif kind == "move":
                self.assertEqual((await self.archive.get_day(self.runtime.date)).meta["current_place"], "家")
            elif kind == "cook":
                self.assertEqual((await self.archive.get_pantry_items())[0]["quantity"], 1)
                self.assertGreater((await self.archive.get_continuous_life())["body"]["hunger"], 70)
        self.assertLess((await self.archive.get_continuous_life())["body"]["hunger"], 30)
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 5)
        await self.tick()
        self.assertEqual((await self.archive.get_pantry_items())[0]["quantity"], 1)
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 5)

    async def test_autonomous_move_requires_explicit_different_places(self):
        for payload in ({}, {"destination": "公园"}, {"origin": "家", "destination": "家"}):
            with self.subTest(payload=payload):
                self.assertIsNone(await self.decide(
                    new_actions=[self.step("move", "出去走走", payload=payload)]
                ))
        self.assertEqual(await self.archive.get_life_action_receipts(), [])

    async def test_user_outfit_change_supersedes_unstarted_followup(self):
        await self.bath_plan()
        await self.tick()

        def update(day):
            day.outfit = "用户刚确定的另一套睡衣"
            day.meta.update(
                outfit_fact_source="user_instruction",
                outfit_fact_confirmed_at=str(self.runtime.now),
            )

        await self.archive.mutate_day(self.runtime.date, update)
        candidate = (await self.choose_pending())[0]
        self.assertIsNone(await self.select(candidate))
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).outfit,
            "用户刚确定的另一套睡衣",
        )

    async def test_missing_or_unknown_catalog_cannot_create_clothing(self):
        await self.wardrobe()
        for ids in ([], [999999]):
            self.assertIsNone(
                await self.decide(
                    new_actions=[
                        self.step(
                            "change_outfit",
                            "凭空的裙子",
                            payload={"catalog_reference_ids": ids},
                        )
                    ]
                )
            )
        day = await self.archive.get_day(self.runtime.date)
        self.assertEqual(day.outfit, "居家服")
        self.assertEqual(actions(day), [])
        self.assertEqual(await self.archive.get_life_action_receipts(), [])

    async def test_new_unbound_steps_survive_restart_without_claiming_completion(self):
        await self.decide(
            new_actions=[self.step("groom", "洗漱"), self.step("drink", "喝水")]
        )
        await self.tick()
        from testcontinuouslife import Runtime

        await self.runtime.continuous_life.close()
        self.runtime = Runtime(self.archive, self.runtime.now)
        candidates = await self.choose_pending()
        self.assertEqual([item["action_type"] for item in candidates], ["drink"])
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 1)
        await self.select(candidates[0])
        await self.tick()
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 2)

    async def test_inserting_another_activity_preserves_unfinished_followup(self):
        await self.decide(
            new_actions=[self.step("groom", "洗漱"), self.step("drink", "喝水")]
        )
        await self.tick()
        await self.decide(new_actions=[self.step("rest", "坐一会儿")])
        await self.tick()
        candidates = await self.choose_pending()
        self.assertEqual([item["action_type"] for item in candidates], ["drink"])
        self.assertIsNotNone(await self.select(candidates[0]))
        await self.tick()
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 3)

    async def test_settlement_rejects_out_of_order_steps(self):
        day = await self.archive.get_day(self.runtime.date)
        day.timeline = [TimelineItem(time="01:00", activity="准备后吃饭")]
        steps = [
            LifeActionIntent.from_value(
                {
                    "action_id": name,
                    "action_type": kind,
                    "target": name,
                    "timeline_index": 0,
                    "duration_minutes": 1,
                }
            )
            for name, kind in (("prepare", "groom"), ("eat", "meal"))
        ]
        day.meta["planned_life_actions"] = json.dumps(
            [step.as_dict() for step in steps]
        )
        result = self.runtime.composer.settle_life_action(
            day, steps[1], now=self.runtime.now
        )
        self.assertEqual(result.status, "rejected")
        self.assertNotEqual(day.timeline[0].execution_state, "completed")
        first = self.runtime.composer.settle_life_action(
            day, steps[0], now=self.runtime.now
        )
        self.assertEqual(first.status, "committed")
        second = self.runtime.composer.settle_life_action(
            day, steps[1], now=self.runtime.now
        )
        self.assertEqual(second.status, "committed")
        self.assertEqual(day.timeline[0].execution_state, "completed")

    async def test_evidence_confirmed_compound_node_settles_all_steps_once(self):
        await self.wardrobe()
        day = await self.archive.get_day(self.runtime.date)
        day.timeline = [
            TimelineItem(
                time="01:00",
                activity="洗漱后喝水",
                execution_state="completed",
                execution_evidence="角色已确认洗漱完成并喝过水",
            )
        ]
        steps = [
            LifeActionIntent(
                action_id=name,
                action_type=kind,
                target=target,
                timeline_index=0,
                duration_minutes=1,
                source="daily_plan",
            )
            for name, kind, target in (
                ("wash", "groom", "洗漱"),
                ("water", "drink", "喝水"),
            )
        ]
        day.meta["planned_life_actions"] = json.dumps(
            [step.as_dict() for step in steps]
        )
        await self.archive.save_day(day)
        outcomes = await self.runtime.composer.settle_completed_planned_actions(
            day, now=self.runtime.now
        )
        self.assertEqual([outcome.status for outcome in outcomes], ["committed"] * 2)
        self.assertEqual(day.timeline[0].execution_state, "completed")
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 2)
        await self.runtime.composer.settle_completed_planned_actions(
            day, now=self.runtime.now
        )
        self.assertEqual(len(await self.archive.get_life_action_receipts()), 2)

    async def test_removed_timeline_cannot_turn_followup_into_spontaneous_action(self):
        day = await self.archive.get_day(self.runtime.date)
        day.timeline = [
            TimelineItem(time="01:00", activity="洗澡后换睡裙", duration_minutes=2)
        ]
        await self.archive.save_day(day)
        await self.bath_plan(index=0)
        await self.tick()
        from core.life.itinerary import apply_edited_timeline

        def remove(day):
            apply_edited_timeline(day, [], {"_planned_life_actions": []})

        await self.archive.mutate_day(self.runtime.date, remove)
        self.assertEqual(await self.choose_pending(), [])
        self.assertEqual(
            (await self.archive.get_day(self.runtime.date)).outfit, "居家服"
        )

    async def test_planned_move_changes_place_before_followup_can_execute(self):
        self.runtime.domains.estimate_route = AsyncMock(
            return_value={"duration_seconds": 60}
        )
        day = await self.archive.get_day(self.runtime.date)
        day.meta["current_place"] = "家"
        day.timeline = [
            TimelineItem(time="01:00", activity="到公园后喝水", place="公园")
        ]
        plan = [
            LifeActionIntent(
                action_id="walk",
                action_type="move",
                target="步行去公园",
                timeline_index=0,
                duration_minutes=1,
                payload={"origin": "家", "destination": "公园"},
            ),
            LifeActionIntent(
                action_id="water",
                action_type="drink",
                target="喝水",
                timeline_index=0,
                duration_minutes=1,
            ),
        ]
        day.meta["planned_life_actions"] = json.dumps([item.as_dict() for item in plan])
        await self.archive.save_day(day)
        candidates = await self.choose_pending()
        self.assertEqual([item["action_type"] for item in candidates], ["move"])
        self.assertIsNotNone(await self.select(candidates[0]))
        self.assertEqual(
            (await self.archive.get_day(day.date)).meta["current_place"], "家"
        )
        await self.tick()
        saved = await self.archive.get_day(day.date)
        self.assertEqual(saved.meta["current_place"], "公园")
        self.assertEqual(saved.timeline[0].execution_state, "active")
        self.assertIsNotNone(await self.select((await self.choose_pending())[0]))
        await self.tick()
        self.assertEqual(
            (await self.archive.get_day(day.date)).timeline[0].execution_state,
            "completed",
        )

    async def test_wardrobe_observes_execution_and_keeps_cache_stable_between_ticks(
        self,
    ):
        await self.decide(new_actions=[self.step("groom", "洗澡")])
        day = await self.archive.get_day(self.runtime.date)
        conditions = wardrobe_conditions(day, self.runtime.now)
        self.assertEqual(
            conditions["actual_execution"]["current"]["action_type"], "groom"
        )
        key = suitability_key(conditions)
        await self.tick()
        day = await self.archive.get_day(self.runtime.date)
        completed = wardrobe_conditions(day, self.runtime.now)
        self.assertEqual(
            completed["actual_execution"]["recent_completed"][-1]["action_type"],
            "groom",
        )
        self.assertEqual(suitability_key(completed), key)
        later = wardrobe_conditions(
            day, self.runtime.now + datetime.timedelta(minutes=2)
        )
        self.assertEqual(suitability_key(completed), suitability_key(later))
