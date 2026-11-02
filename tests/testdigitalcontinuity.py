"""跨月生活、事实边界和后台响应的集成验证；不访问真实模型或发送消息。"""

import asyncio
import copy
import datetime
import json
import unittest
from types import SimpleNamespace

import testcontinuouslife as fixtures
import testlivingcontinuity as living_fixtures
from support import DayRecord, LifeArchive, LifeState
from core.clock import now as life_now
from core.epistemic import knowledge_label
from core.life.body import ContinuousBody
from core.life.entities import living_context
from core.life.achievement import apply_reviews
from core.life.presence import apply_action_reflections, kernel_context
from core.models.vitals import BodyCondition
from componentfixtures import ChatMemoryBatchComponent
from core.telemetry import LoopDelayProbe, ModelCacheMetrics


class DigitalContinuityTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ContinuousLifeTest.asyncSetUp
    asyncTearDown = fixtures.ContinuousLifeTest.asyncTearDown
    tick = fixtures.ContinuousLifeTest.tick

    async def start(self, *args, payload=None, **kwargs):
        day = await self.archive.get_day(self.runtime.date)
        return await fixtures.ContinuousLifeTest.start(
            self, *args,
            payload={"place": day.meta.get("current_place", ""), **(payload or {})},
            **kwargs,
        )

    async def object(self, *, amount=2, owner="self", location="书桌", available_at=""):
        await self.archive.mutate_continuous_life(
            self.runtime.date, lambda day, world: day.meta.update(current_place="书桌")
        )
        return await self.archive.update_life_object(
            "global",
            entity_id="",
            title="练习纸",
            state="available",
            location=location,
            content="",
            progress="",
            evidence_id="owned-paper",
            evidence="已确认属于角色的练习纸",
            revision=0,
            owner=owner,
            quantity=amount,
            unit="张",
            available_at=available_at,
        )

    def use(self, item, **extra):
        return {
            "resource_uses": [
                {
                    "object_id": item["id"],
                    "revision": item["revision"],
                    "quantity": 1,
                    "unit": "张",
                    "mode": "consume",
                    **extra,
                }
            ]
        }

    async def reflect(self, run, number=1):
        def apply(day, world):
            apply_action_reflections(
                world,
                [
                    {
                        "action_id": run["action"]["action_id"],
                        "summary": f"第 {number} 次练习，补充比较的依据",
                        "adjustment": "下次先比较局部，再整理完整构图",
                        "next_step": "继续比较构图",
                        "artifact": {
                            "kind": "practice_note",
                            "content": f"第 {number} 版：居中构图稳定，三分构图留下视线空间。",
                        },
                    }
                ],
                allowed_action_ids={run["action"]["action_id"]},
                now=self.runtime.now,
            )

        await self.archive.mutate_continuous_life(self.runtime.date, apply)
        return await self.archive.get_living_entity(
            self.archive.life_entity_id(
                "object", "global", f"artifact:{run['action']['action_id']}"
            ),
            "global",
        )

    async def test_thirty_days_resources_artifacts_learning_and_restart(self):
        paper = await self.object(amount=30)
        parent = None
        initial_date = self.runtime.now
        last_body = None
        for index in range(30):
            self.runtime.now = initial_date + datetime.timedelta(days=index)
            self.runtime.date = self.runtime.now.date().isoformat()
            if index:
                state = LifeState(energy=65, sleepiness=30)
                if index < 4:
                    state.physiological_rhythm.body_condition = BodyCondition(
                        label="轻微不适",
                        intensity=30,
                        burden_present=True,
                        kind="discomfort",
                        phase="recovering",
                        started_at=initial_date.isoformat(),
                    )
                await self.archive.save_day(
                    DayRecord(
                        date=self.runtime.date,
                        state=state,
                        meta={"current_place": "书桌"},
                    )
                )
                await self.runtime.continuous_life._check_continuous_life_once()
            paper = await self.archive.get_living_entity(paper["id"], "global")
            payload = self.use(paper)
            if parent:
                payload["artifact_parent_id"] = parent["id"]
            run = await self.start(
                payload=payload,
                expectation={
                    "reason": "根据此前练习",
                    "body_change": {"energy": -0.07},
                },
            )
            self.assertIsNotNone(run, f"第 {index + 1} 天应可开始")
            await self.tick()
            await self.tick()
            world = await self.archive.get_continuous_life()
            self.assertNotIn("run", world)
            self.assertEqual(world["history"][-1]["status"], "completed")
            parent = await self.reflect(run, index + 1)
            self.assertEqual(parent["artifact_version"], index + 1)
            self.assertEqual(parent["progress"], f"已保存第 {index + 1} 版")
            self.assertEqual(parent["next_step"], "继续比较构图")
            self.assertIn("后续意向（尚未执行）：继续比较构图", living_context([parent]))
            # 每天回放有观测的练习、进食、饮水和休息；其余离线时间保持未知。
            for action_type in ("meal", "drink"):
                self.assertIsNotNone(await self.start(action_type))
                await self.tick(2)
            self.assertIsNotNone(
                await self.start("rest", duration=24, rest_kind="sleep")
            )
            for _ in range(8):
                await self.tick(3)
            last_body = (await self.archive.get_continuous_life())["body"]
            if index == 14:
                # 中途重启读取真正的 SQLite 文件，检查世界状态不会重置。
                await self.archive.aclose()
                self.archive = LifeArchive(self.path)
                self.runtime = fixtures.Runtime(self.archive, self.runtime.now)
                self.assertEqual(
                    (await self.archive.get_continuous_life())["body"], last_body
                )
        paper = await self.archive.get_living_entity(paper["id"], "global")
        self.assertEqual(paper["quantity"], 0)
        self.assertEqual(paper["state"], "unavailable")
        versions = await self.archive.get_artifact_versions(parent["id"], "global")
        self.assertEqual(len(versions), 30)
        self.assertTrue(all(item["next_step"] not in item["progress"] for item in versions))
        self.assertEqual(
            versions[-1]["content"], "第 1 版：居中构图稳定，三分构图留下视线空间。"
        )
        world = await self.archive.get_continuous_life()
        samples = world["action_learning"]["study:break"]["samples"]
        self.assertEqual(len(samples), 12)
        self.assertTrue(
            all("energy" in sample["prediction_error"] for sample in samples)
        )
        self.assertIn("下次先比较局部", kernel_context(world))
        self.assertLessEqual(
            len(world["action_learning"]["study:break"]["adjustments"]), 4
        )
        self.assertIsNone(await self.start(payload=self.use(paper)))

    async def test_resource_cancel_releases_without_consumption_and_stale_rejected(
        self,
    ):
        paper = await self.object()
        run = await self.start(payload=self.use(paper))
        occupied = await self.archive.get_living_entity(paper["id"], "global")
        self.assertEqual(occupied["reserved_by"], run["action"]["action_id"])
        await self.runtime.continuous_life._close_continuous_run(
            run, "cancelled", "临时决定休息", self.runtime.now
        )
        released = await self.archive.get_living_entity(paper["id"], "global")
        self.assertEqual(released["quantity"], 2)
        self.assertNotIn("reserved_by", released)
        self.assertIsNone(await self.start(payload=self.use(paper)))
        second = await self.start(payload=self.use(released))
        await self.tick(2)
        await self.runtime.continuous_life._finish_continuous_action(second, self.runtime.now)
        finished = await self.archive.get_living_entity(paper["id"], "global")
        self.assertEqual(finished["quantity"], 1)

    async def test_resource_location_owner_and_readiness_are_constraints(self):
        paper = await self.object(location="储物间")
        self.assertIsNone(await self.start(payload=self.use(paper)))
        for changes in (
            {"location": "书桌", "owner": "user"},
            {"location": "书桌", "owner": "self", "available_at": "2099-01-01"},
        ):
            paper = await self.archive.update_life_object(
                "global",
                entity_id=paper["id"],
                title="",
                state="available",
                progress="",
                content="",
                evidence="明确更新",
                evidence_id=f"change-{paper['revision']}",
                revision=paper["revision"],
                **changes,
            )
            self.assertIsNone(await self.start(payload=self.use(paper)))

    async def test_resource_change_during_work_fails_and_releases(self):
        paper = await self.object()
        await self.start(payload=self.use(paper))
        occupied = await self.archive.get_living_entity(paper["id"], "global")
        await self.archive.update_life_object(
            "global",
            entity_id=paper["id"],
            title="",
            state="unavailable",
            location="",
            progress="",
            content="",
            evidence_id="paper-damaged",
            evidence="练习纸已损坏",
            revision=occupied["revision"],
        )
        await self.tick(2)
        await self.runtime.continuous_life._check_continuous_life_once()
        paper = await self.archive.get_living_entity(paper["id"], "global")
        self.assertEqual(paper["quantity"], 2)
        self.assertNotIn("reserved_by", paper)
        self.assertEqual(paper["state"], "unavailable")
        self.assertEqual(
            (await self.archive.get_continuous_life())["history"][-1]["status"],
            "failed",
        )

    async def test_moving_reserved_object_prevents_completion(self):
        paper = await self.object()
        await self.start(payload=self.use(paper, mode="use"))
        occupied = await self.archive.get_living_entity(paper["id"], "global")
        await self.archive.update_life_object(
            "global",
            entity_id=paper["id"],
            title="",
            state="",
            location="储物间",
            progress="",
            content="",
            evidence_id="paper-moved",
            evidence="纸搬到储物间",
            revision=occupied["revision"],
        )
        await self.tick(2)
        await self.runtime.continuous_life._check_continuous_life_once()
        paper = await self.archive.get_living_entity(paper["id"], "global")
        self.assertEqual(paper["location"], "储物间")
        self.assertNotIn("reserved_by", paper)
        self.assertEqual(
            (await self.archive.get_continuous_life())["history"][-1]["status"],
            "failed",
        )

    async def test_skill_assessment_keeps_criterion_and_evidence_after_restart(self):
        fixture = living_fixtures.AchievementTest()
        fixture.setUp()
        fixture.goal["skill"] = "构图"
        candidate = fixture.artifact()
        apply_reviews(
            fixture.world,
            [fixture.review(candidate, verdict="needs_practice", next_step="补充留白")],
            offered=[candidate],
            now=fixture.now,
        )
        await self.archive.mutate_continuous_life(
            self.runtime.date,
            lambda day, world: world.update(skills=fixture.world["skills"]),
        )
        await self.archive.aclose()
        self.archive = LifeArchive(self.path)
        assessment = (await self.archive.get_continuous_life())["skills"]["构图"][
            "assessments"
        ][0]
        self.assertEqual(assessment["criterion"], "草稿中比较两种构图")
        self.assertEqual(assessment["verdict"], "needs_practice")
        self.assertEqual(assessment["next_step"], "补充留白")
        self.assertTrue(assessment["evidence"])
        self.assertFalse(assessment["verified_mastery"])

    async def test_discomfort_is_observed_not_automatically_cured(self):
        def discomfort(day, world):
            day.state.physiological_rhythm.body_condition = BodyCondition(
                label="身体不适",
                intensity=50,
                burden_present=True,
                kind="illness_like",
                phase="ongoing",
                started_at=self.runtime.now.isoformat(),
            )

        await self.archive.mutate_continuous_life(self.runtime.date, discomfort)
        run = await self.start("rest", rest_kind="sleep")
        await self.tick(2)
        world = await self.archive.get_continuous_life()
        self.assertEqual(world["body"]["discomfort"], 50)
        self.assertEqual(world["body_observations"][-1]["minutes"], 2)
        self.assertEqual(
            world["body_observations"][-1]["action_id"], run["action"]["action_id"]
        )
        day = await self.archive.get_day(self.runtime.date)
        self.assertEqual(day.state.physiological_rhythm.body_condition.phase, "ongoing")

    async def test_background_model_wait_does_not_hold_chat_or_database(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args):
            entered.set()
            await release.wait()
            return {
                "decision": "start",
                "reason": "过期判断",
                "new_actions": [{
                    "owner": "self",
                    "action_type": "study",
                    "target": "练习",
                    "duration_minutes": 2,
                }],
            }

        self.runtime.continuous_life._decide = delayed
        await self.archive.mutate_continuous_life(
            self.runtime.date, lambda day, world: world.pop("next_decision_at", None)
        )
        task = asyncio.create_task(self.runtime.continuous_life._check_continuous_life_once())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            event = SimpleNamespace(
                message_id="chat-during-work", unified_msg_origin="private:user"
            )
            await asyncio.wait_for(self.runtime.note_continuous_chat_exchange(event), 1)
            self.assertFalse(task.done())
            release.set()
            await asyncio.wait_for(task, 1)
            self.assertNotIn("run", await self.archive.get_continuous_life())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_hypotheses_never_replace_or_invalidate_known_fact(self):
        now = life_now().replace(microsecond=0) - datetime.timedelta(minutes=5)
        base = {
            "scope": "private:u",
            "subject": "user",
            "predicate": "location",
            "observed_at": now.isoformat(sep=" "),
            "source_id": "message-1",
            "source_type": "chat_message",
            "provenance": {"epistemic_status": "reported"},
        }
        known = await self.archive.write_temporal_fact(
            "ADD", {**base, "object_value": "家"}
        )
        hypothesis = {
            **base,
            "object_value": "办公室",
            "source_id": "message-2",
            "confidence": 1,
            "observed_at": (now + datetime.timedelta(minutes=1)).isoformat(sep=" "),
            "provenance": {"epistemic_status": "inferred"},
        }
        guess = await self.archive.write_temporal_fact("UPDATE", hypothesis)
        self.assertEqual(guess.status, "uncertain")
        self.assertLessEqual(guess.confidence, 0.5)
        await self.archive.write_temporal_fact("INVALIDATE", hypothesis)
        self.assertEqual(
            (
                await self.archive.get_current_temporal_fact(
                    "private:u", "user", "location"
                )
            ).id,
            known.id,
        )
        repeat = await self.archive.write_temporal_fact("UPDATE", hypothesis)
        self.assertEqual(repeat.id, guess.id)
        self.assertIn("推测", knowledge_label(guess))
        self.assertEqual(
            len(
                await self.archive.get_temporal_facts(
                    scope="private:u", include_uncertain=True
                )
            ),
            2,
        )
        confirmed = {
            **hypothesis,
            "source_id": "message-3",
            "provenance": {"epistemic_status": "reported"},
            "observed_at": (now + datetime.timedelta(minutes=2)).isoformat(sep=" "),
        }
        await self.archive.write_temporal_fact("UPDATE", confirmed)
        self.assertEqual(
            len(
                await self.archive.get_temporal_facts(
                    scope="private:u", include_uncertain=True
                )
            ),
            1,
        )
        self.assertIsNone(await self.archive.write_temporal_fact("UPDATE", hypothesis))
        historical = await self.archive.get_temporal_facts(
            scope="private:u", as_of=hypothesis["observed_at"]
        )
        self.assertEqual([item.id for item in historical], [known.id])

    async def test_reaffirming_existing_fact_closes_pending_guess(self):
        now = life_now().replace(microsecond=0) - datetime.timedelta(minutes=5)
        base = {
            "scope": "private:u",
            "subject": "user",
            "predicate": "location",
            "source_id": "known",
            "object_value": "家",
            "observed_at": now.isoformat(sep=" "),
            "provenance": {"epistemic_status": "reported"},
        }
        known = await self.archive.write_temporal_fact("ADD", base)
        await self.archive.write_temporal_fact(
            "ADD",
            {
                **base,
                "source_id": "guess",
                "object_value": "办公室",
                "observed_at": (now + datetime.timedelta(minutes=1)).isoformat(sep=" "),
                "provenance": {"epistemic_status": "uncertain"},
            },
        )
        await self.archive.write_temporal_fact(
            "ADD",
            {
                **base,
                "source_id": "reaffirm",
                "observed_at": (now + datetime.timedelta(minutes=2)).isoformat(sep=" "),
            },
        )
        self.assertEqual(
            [
                item.id
                for item in await self.archive.get_temporal_facts(
                    scope="private:u", include_uncertain=True
                )
            ],
            [known.id],
        )

    async def test_assistant_text_and_forged_quote_cannot_create_reported_fact(self):
        runtime = ChatMemoryBatchComponent()
        runtime.archive = self.archive
        runtime._chinese_text_payload = lambda value: str(value or "")
        for index, (role, quote) in enumerate(
            (("assistant", "已经到家"), ("user", "伪造的原话"), ("user", "已经到家"))
        ):
            fact = {
                "operation": "ADD",
                "subject": f"user-{index}",
                "predicate": "location",
                "object_value": "家",
                "confidence": 0.9,
                "source_message_id": str(index),
                "epistemic_status": "reported",
                "evidence_quote": quote,
            }
            batch = {
                "session_id": "private:u",
                "messages": [
                    {
                        "role": role,
                        "message_id": str(index),
                        "message_text": "已经到家",
                        "occurred_at": "2026-10-12 12:00:00",
                    }
                ],
            }
            saved = await runtime._part_chat_memory_batch_component._save_batch_temporal_facts(
                {"temporal_facts": [fact]}, batch
            )
            self.assertEqual(saved[0].status, "active" if index == 2 else "uncertain")


class BodyAndMetricsTest(unittest.IsolatedAsyncioTestCase):
    def test_discomfort_changes_effort_recovery_and_attention(self):
        healthy = ContinuousBody(energy=50, fatigue=50)
        sick = copy.deepcopy(healthy)
        sick.discomfort = 60
        self.assertLess(sick.attention_capacity, healthy.attention_capacity)
        healthy.advance(20, activity="exercise", intensity=2)
        sick.advance(20, activity="exercise", intensity=2)
        self.assertLess(sick.energy, healthy.energy)
        self.assertGreater(sick.fatigue, healthy.fatigue)
        first, second = healthy.energy, sick.energy
        healthy.advance(20, activity="rest", sleeping=True)
        sick.advance(20, activity="rest", sleeping=True)
        self.assertLess(sick.energy - second, healthy.energy - first)

    async def test_metrics_report_failures_unknown_cache_and_probe_cleanup(self):
        meter = ModelCacheMetrics(limit=2)
        meter.record({}, kind="internal", model="test", elapsed_ms=100)
        meter.record(None, kind="internal", model="test", elapsed_ms=200, failed=True)
        row = meter.snapshot()["models"][0]
        self.assertEqual(row["requests"], 2)
        self.assertEqual(row["failed_requests"], 1)
        self.assertIsNone(row["cached_token_ratio"])
        self.assertEqual(row["latency"]["p95_ms"], 200)
        probe = LoopDelayProbe(meter.record_loop_delay)
        probe.start()
        handle = probe.handle
        # 调用实际回调验证记录与重新调度，不需等待一秒。
        handle._callback(handle._args[0] - 0.05)
        probe.stop()
        handle.cancel()
        self.assertIsNone(probe.handle)
        self.assertEqual(meter.snapshot()["event_loop_delay"]["samples"], 1)
        json.dumps(meter.snapshot(), allow_nan=False)
