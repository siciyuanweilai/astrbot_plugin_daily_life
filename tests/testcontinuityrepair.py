# isort: skip_file
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import DayRecord, LifeArchive, LifeState, TimelineItem
from core.models import CommitmentRecord
from core.life.appearance import (
    format_current_appearance_context,
    format_image_appearance_context,
)
from core.life.lookback import (
    appearance_query,
    historical_appearance_context,
    record_appearance_snapshot,
)
from core.life.settlement import LifeActionMixin
from core.life.invite import InviteMixin
from core.life.record import DailyRecordMixin
from core.life.wardrobe import (
    format_outfit_components,
    merge_outfit_components,
    normalize_outfit_components,
)
from core.runtime.capture.execution import ChatExecutionMixin
from core.runtime.capture.batch import ChatMemoryBatchMixin
from core.runtime.live import DailyLifeRuntime


class AppearanceRepairTest(unittest.TestCase):
    def test_legacy_main_does_not_resurrect_removed_components(self):
        full = "浅粉色细吊带短款背心，搭配同色轻薄长袖开衫外套与高腰蕾丝边抽绳短裤，脚穿浅粉色毛绒交叉宽带厚底露趾居家拖鞋，佩戴细锁骨链"
        ledger = {
            "main_clothing": {"state": "worn", "description": full},
            "outer_layer": {
                "state": "removed",
                "description": "浅粉色轻薄长袖开衫外套，已脱下",
            },
            "footwear": {
                "state": "removed",
                "description": "浅粉色毛绒交叉宽带厚底露趾居家拖鞋，已脱下",
            },
            "carried_accessories": {
                "state": "removed",
                "description": "细锁骨链，已取下",
            },
        }
        day = DayRecord(
            date="2026-09-24",
            outfit=full,
            meta={"outfit_components": json.dumps(ledger)},
        )
        normalized = normalize_outfit_components(ledger)
        self.assertEqual(normalize_outfit_components(normalized), normalized)
        for scene in ("home", "outdoor", "public", "sleep"):
            context = format_current_appearance_context(day, scene_category=scene)
            outfit = context.splitlines()[0]
            self.assertIn("背心", outfit)
            self.assertIn("短裤", outfit)
            for removed in ("外套", "拖鞋", "锁骨链"):
                self.assertNotIn(removed, outfit)
            self.assertIn(
                "外套不在身上",
                format_image_appearance_context(day, scene_category=scene),
            )
        self.assertEqual(day.outfit, full)

    def test_partial_update_removes_overlap_without_discarding_other_clothes(self):
        base = {
            "main_clothing": {
                "state": "worn",
                "description": "白衬衫，蓝牛仔裤，黑皮鞋",
            }
        }
        result = merge_outfit_components(
            base, {"footwear": {"state": "removed", "description": "黑皮鞋"}}
        )
        self.assertEqual(format_outfit_components(result), "白衬衫，蓝牛仔裤")

    def test_component_details_are_preserved_while_duplicate_main_text_is_removed(self):
        ledger = {
            "main_clothing": {"state": "worn", "description": "白衬衫，蓝牛仔裤，黑皮鞋"},
            "footwear": {"state": "removed", "description": "黑皮鞋，鞋面有细纹"},
        }
        normalized = normalize_outfit_components(ledger)
        self.assertEqual(normalized["footwear"]["description"], "黑皮鞋，鞋面有细纹")
        self.assertEqual(normalized["main_clothing"]["description"], "白衬衫，蓝牛仔裤")
        self.assertEqual(normalize_outfit_components(normalized), normalized)

    def test_snapshots_preserve_two_changes_in_same_period(self):
        day = DayRecord(date="2026-09-23", outfit="蓝裙", meta={"hair": "低马尾"})
        record_appearance_snapshot(day, dt.datetime(2026, 9, 23, 15, 10))
        record_appearance_snapshot(day, dt.datetime(2026, 9, 23, 15, 11))
        day.outfit, day.meta["hair"] = "白衬衫", "散发"
        record_appearance_snapshot(day, dt.datetime(2026, 9, 23, 16, 20))
        self.assertEqual(
            sum(key.startswith("appearance_snapshot:") for key in day.meta), 2
        )

    def test_calendar_query_validates_structured_dates_and_periods(self):
        now = dt.datetime(2026, 9, 24, 0, 30)
        self.assertEqual(
            appearance_query("2026-09-23", now, period="evening_to_night")[:2],
            ("2026-09-23", (18, 24)),
        )
        self.assertEqual(appearance_query("2026-09-22", now)[0], "2026-09-22")
        self.assertEqual(
            appearance_query("2026-09-20", now, period="afternoon", time="15:00"),
            ("2026-09-20", (14, 18), 900),
        )
        self.assertEqual(appearance_query("2026-02-30", now)[0], "ambiguous")
        self.assertEqual(appearance_query("那天那套", now)[0], "ambiguous")
        self.assertEqual(appearance_query("2026-09-25", now)[0], "ambiguous")
        self.assertEqual(appearance_query("2026-09-23", now, period="unknown")[0], "ambiguous")
        self.assertEqual(appearance_query("2026-09-23", now, time="25:00")[0], "ambiguous")


class ContinuityArchiveTest(unittest.IsolatedAsyncioTestCase):
    async def test_location_audit_preserves_activity_kind_on_edited_node(self):
        async def audit(payload, **_):
            item = dict(payload["timeline"][0])
            item.pop("activity_kind")
            item["place"] = "新地点"
            return {"timeline": [item]}, ""

        mixin = InviteMixin()
        mixin.domains = SimpleNamespace(audit_daily_locations=audit)
        changed, _, reason = await mixin._audit_future_timeline(
            past_timeline=[],
            mutable_timeline=[TimelineItem(time="21:00", activity="躺下休息", activity_kind="rest")],
            protected_timeline=[],
            current_places=[],
        )
        self.assertEqual(reason, "")
        self.assertEqual(changed[0].activity_kind, "rest")
        self.assertEqual(changed[0].place, "新地点")

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.temp.name) / "life.db")

    async def asyncTearDown(self):
        self.archive.close()
        self.temp.cleanup()

    async def test_historical_period_and_date_never_mix_latest_hair(self):
        await self.archive.save_day(
            DayRecord(
                date="2026-09-23",
                outfit="睡衣",
                outfit_history={
                    "afternoon": "白裙",
                    "evening": "蓝裙",
                    "late_night": "睡衣",
                },
                meta={"hair": "睡前散发"},
            )
        )
        now = dt.datetime(2026, 9, 24, 0, 30)
        result = await historical_appearance_context(
            self.archive, "2026-09-23", now, period="evening_to_night"
        )
        self.assertIn("2026-09-23", result)
        self.assertNotIn("白裙", result)
        self.assertNotIn("睡前散发", result)
        result = await historical_appearance_context(
            self.archive, "2026-09-23", now, period="evening"
        )
        self.assertIn("蓝裙", result)
        self.assertNotIn("睡衣", result)
        self.assertEqual(
            await historical_appearance_context(self.archive, "2026-09-22", now), ""
        )

    async def test_festival_photo_uses_recorded_afternoon_not_evening_appearance(self):
        day = DayRecord(date="2026-09-25", outfit="睡裙", meta={"hair": "散发"})
        day.outfit, day.meta["hair"] = "浅紫色针织裙和米白开衫", "高马尾"
        record_appearance_snapshot(day, dt.datetime(2026, 9, 25, 16))
        day.outfit, day.meta["hair"] = "睡裙", "散发"
        record_appearance_snapshot(day, dt.datetime(2026, 9, 25, 21))
        await self.archive.save_day(day)

        result = await historical_appearance_context(
            self.archive, "2026-09-25", dt.datetime(2026, 9, 27, 21),
            period="afternoon",
        )

        self.assertIn("浅紫色针织裙和米白开衫", result)
        self.assertIn("高马尾", result)
        self.assertNotIn("睡裙", result)
        self.assertNotIn("散发", result)

    async def test_timestamp_query_preserves_appearance_at_requested_time(self):
        day = DayRecord(date="2026-09-23", outfit="蓝裙", meta={"hair": "低马尾"})
        record_appearance_snapshot(day, dt.datetime(2026, 9, 23, 15, 10))
        day.outfit, day.meta["hair"] = "白衬衫", "散发"
        record_appearance_snapshot(day, dt.datetime(2026, 9, 23, 16, 20))
        await self.archive.save_day(day)
        result = await historical_appearance_context(
            self.archive, "2026-09-23", dt.datetime(2026, 9, 24, 12),
            period="afternoon", time="15:30",
        )
        self.assertIn("蓝裙", result)
        self.assertIn("低马尾", result)
        self.assertNotIn("散发", result)

    async def test_last_outfit_finds_prior_change_today_without_mutation(self):
        day = DayRecord(date="2026-09-24", outfit="蓝裙")
        record_appearance_snapshot(day, dt.datetime(2026, 9, 24, 10))
        day.outfit = "白衬衫"
        record_appearance_snapshot(day, dt.datetime(2026, 9, 24, 11))
        await self.archive.save_day(day)
        result = await historical_appearance_context(
            self.archive, "last", dt.datetime(2026, 9, 24, 12), day
        )
        self.assertIn("蓝裙", result)
        self.assertNotIn("白衬衫", result)
        self.assertEqual((await self.archive.get_day(day.date)).outfit, "白衬衫")

    async def _execution(
        self,
        *,
        kind="plan",
        owner="当前角色",
        media_kind="none",
        message="我已经把小桌收拾好了。",
        role="assistant",
        scope="private:1",
        date="2026-09-24",
    ):
        commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="收拾小桌",
                kind=kind,
                owner=owner,
                media_kind=media_kind,
                source_session="private:1",
                trigger_date=date,
                status="scheduled",
            )
        )
        runtime = ChatExecutionMixin()
        runtime.archive = self.archive
        batch = {
            "session_id": scope,
            "messages": [
                {
                    "id": 1,
                    "message_id": "m1",
                    "role": role,
                    "message_text": message,
                    "occurred_at": "2026-09-24T12:00:00",
                }
            ],
        }
        batch.update(await runtime._chat_execution_context(batch))
        payload = {
            "worth_saving": False,
            "execution_updates": [
                {
                    "completed": True,
                    "commitment_ids": [commitment.id],
                    "source_message_id": "m1",
                    "evidence": message,
                    "action_id": "",
                }
            ],
        }
        return commitment, runtime, batch, payload

    async def test_completion_is_scoped_evidenced_and_idempotent(self):
        item, runtime, batch, payload = await self._execution()
        self.assertEqual(await runtime._save_batch_execution_updates(payload, batch), 1)
        self.assertEqual(await runtime._save_batch_execution_updates(payload, batch), 0)
        self.assertEqual((await self.archive.get_commitment(item.id)).status, "done")
        traces = await self.archive.get_decision_traces(scope="private:1")
        self.assertEqual(len(traces), 1)

    async def test_sent_reply_without_platform_id_uses_persistent_row_id(self):
        item, runtime, batch, payload = await self._execution()
        batch["messages"][0]["message_id"] = ""
        payload["execution_updates"][0]["source_message_id"] = "1"
        prompt = ChatMemoryBatchMixin()._build_chat_memory_batch_prompt(batch)
        source = json.loads(prompt.split("输入批次：", 1)[1])
        self.assertEqual(source["messages"][0]["message_id"], "1")
        self.assertEqual(await runtime._save_batch_execution_updates(payload, batch), 1)
        self.assertEqual((await self.archive.get_commitment(item.id)).status, "done")

    async def test_day_regeneration_preserves_recorded_appearance_snapshots(self):
        old = DayRecord(date="2026-09-24", outfit="蓝裙")
        record_appearance_snapshot(old, dt.datetime(2026, 9, 24, 10))
        await self.archive.save_day(old)
        recorder = DailyRecordMixin()
        recorder.archive = self.archive
        new = DayRecord(
            date=old.date,
            outfit="白衬衫",
            meta={"outfit_fact_confirmed_at": "2026-09-24 12:00:00"},
        )
        await recorder._persist_generated_day(new.date, new, [])
        saved = await self.archive.get_day(new.date)
        self.assertEqual(
            sum(key.startswith("appearance_snapshot:") for key in saved.meta), 2
        )
        history = await historical_appearance_context(
            self.archive, "2026-09-24", dt.datetime(2026, 9, 24, 13),
            time="10:00",
        )
        self.assertIn("蓝裙", history)
        self.assertNotIn("白衬衫", history)

    async def test_completion_rejects_unconfirmed_wrong_owner_media_scope_and_future(self):
        for kwargs in (
            {"role": "user"},
            {"owner": "说话人"},
            {"owner": "共同"},
            {"media_kind": "photo"},
            {"media_kind": "video"},
            {"scope": "private:2"},
            {"date": "2026-09-25"},
            {"kind": "reminder"},
        ):
            with self.subTest(kwargs=kwargs):
                item, runtime, batch, payload = await self._execution(**kwargs)
                self.assertEqual(
                    await runtime._save_batch_execution_updates(payload, batch), 0
                )
                self.assertEqual(
                    (await self.archive.get_commitment(item.id)).status, "scheduled"
                )

        item, runtime, batch, payload = await self._execution(
            message="我还没有收拾小桌。"
        )
        payload["execution_updates"][0]["completed"] = False
        self.assertEqual(await runtime._save_batch_execution_updates(payload, batch), 0)
        self.assertEqual((await self.archive.get_commitment(item.id)).status, "scheduled")

    async def test_completion_rejects_invented_evidence_and_unlisted_ids(self):
        item, runtime, batch, payload = await self._execution()
        payload["execution_updates"][0]["evidence"] = "任务已经全部完成"
        self.assertEqual(await runtime._save_batch_execution_updates(payload, batch), 0)
        payload["execution_updates"][0]["evidence"] = "我已经把小桌收拾好了。"
        batch["open_commitments"] = []
        self.assertEqual(await runtime._save_batch_execution_updates(payload, batch), 0)

    async def test_failed_action_does_not_mark_timeline_or_commitment_done(self):
        item, runtime, batch, payload = await self._execution()
        engine = LifeActionMixin()
        engine.archive = self.archive
        engine.sync_day_world_facts = AsyncMock()
        runtime.composer = engine
        action = {
            "action_id": "chore1",
            "action_type": "chore",
            "target": "收拾小桌",
            "timeline_index": 0,
            "preconditions": [
                {"field": "state.energy", "operator": "gte", "expected": 30}
            ],
        }
        day = DayRecord(
            date="2026-09-24",
            state=LifeState(energy=1),
            timeline=[TimelineItem(time="11:00", activity="收拾小桌")],
            meta={"planned_life_actions": json.dumps([action])},
        )
        await self.archive.save_day(day)
        batch.update(await runtime._chat_execution_context(batch))
        payload["execution_updates"][0]["action_id"] = "chore1"
        self.assertEqual(await runtime._save_batch_execution_updates(payload, batch), 0)
        self.assertNotEqual(
            (await self.archive.get_day(day.date)).timeline[0].execution_state,
            "completed",
        )
        self.assertEqual(
            (await self.archive.get_commitment(item.id)).status, "scheduled"
        )

    async def test_successful_action_completes_timeline_and_commitment_once(self):
        item, runtime, batch, payload = await self._execution()
        engine = LifeActionMixin()
        engine.archive = self.archive
        engine.sync_day_world_facts = AsyncMock()
        runtime.composer = engine
        action = {
            "action_id": "chore1",
            "action_type": "chore",
            "target": "收拾小桌",
            "timeline_index": 0,
        }
        day = DayRecord(
            date="2026-09-24",
            state=LifeState(energy=70),
            timeline=[TimelineItem(time="11:00", activity="收拾小桌")],
            meta={"planned_life_actions": json.dumps([action])},
        )
        await self.archive.save_day(day)
        batch.update(await runtime._chat_execution_context(batch))
        payload["execution_updates"][0]["action_id"] = "chore1"
        self.assertEqual(await runtime._save_batch_execution_updates(payload, batch), 1)
        saved = await self.archive.get_day(day.date)
        self.assertEqual(saved.timeline[0].execution_state, "completed")
        energy = saved.state.energy
        await runtime._save_batch_execution_updates(payload, batch)
        self.assertEqual((await self.archive.get_day(day.date)).state.energy, energy)

    async def test_missing_history_stops_generation_before_model_or_delivery(self):
        runtime = DailyLifeRuntime.__new__(DailyLifeRuntime)
        runtime._direct_image_tool_already_sent = lambda event: False
        runtime._event_current_image_request_text = lambda event: "前天的穿搭再现一下"
        runtime._historical_life_appearance_snapshot = AsyncMock(return_value="")
        runtime._prepare_image_generation_plan = AsyncMock(
            side_effect=AssertionError("must not generate")
        )
        result = await runtime._life_image_generate_inline(
            SimpleNamespace(), "昨天衣服", subject_route="current_character",
            historical_target="2026-09-23",
        )
        self.assertIn("未生成", result)
        runtime._historical_life_appearance_snapshot.assert_awaited_once_with(
            "2026-09-23", period="", time=""
        )
