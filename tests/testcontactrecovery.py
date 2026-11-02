import datetime
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from support import LifeArchive
from componentfixtures import ProactiveFollowupComponent, SpineInviteComponent
from core.archive.schema import init_schema
from core.archive.migrations import CURRENT_SCHEMA_FINGERPRINT, schema_fingerprint
from core.models import CommitmentRecord, DayRecord, TimelineItem


class ContactRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "life.db"
        self.archive = LifeArchive(self.path)
        self.runtime = ProactiveFollowupComponent.__new__(ProactiveFollowupComponent)
        self.runtime.archive = self.archive
        self.now = datetime.datetime(2026, 10, 24, 13, 35)
        self.clock = patch("core.runtime.proactive.followup.life_now", return_value=self.now)
        self.clock.start()

    async def asyncTearDown(self):
        self.clock.stop()
        await self.archive.aclose()
        self.directory.cleanup()

    async def promise(self, **changes):
        return await self.archive.save_commitment(CommitmentRecord.from_value({
            "content": "一点五十去敲门，一起出门散步",
            "owner": "当前角色", "trigger_date": "2026-10-24",
            "trigger_time": "13:50", "source": "chat_batch",
            "source_session": "test:FriendMessage:1",
            "source_message": "我这边都齐了\n一点五十敲你门",
            **changes,
        }))

    async def schedule(self, record, **changes):
        return await self.runtime.schedule_proactive_commitment(
            record, owner=record.owner, observed_at=self.now,
            follow_up={"action": "contact_person", "execute_at": "2026-10-24T13:50:00+08:00", **changes},
        )

    async def prepare_review(self, record, decision):
        self.runtime.config = SimpleNamespace(proactive=SimpleNamespace(provider=""))
        self.runtime._get_proactive_provider = AsyncMock(return_value=object())
        self.runtime.close_text_session = AsyncMock()
        with patch("core.runtime.proactive.followup.call_pure_json", new=AsyncMock(return_value=decision)):
            return await self.runtime.run_commitment_contact_review_task(
                SimpleNamespace(payload={"commitment_id": record.id})
            )

    async def prepare_delivery(self, record, evaluator=None):
        self.runtime.resolve_interaction_context = AsyncMock(return_value=SimpleNamespace(mode="co_present"))
        self.runtime._proactive_commitment_relationship = AsyncMock(return_value=None)
        self.runtime._send_proactive_message = AsyncMock(return_value=True)
        self.runtime._part_proactive_followup_component._evaluate_proactive_commitment = evaluator or AsyncMock(return_value={
            "should_send": True, "settlement": "send", "reply_text": "准备好了吗？我们走吧。",
        })
        return await self.archive.get_durable_task(f"proactive_commitment:{record.id}")

    async def test_queue_failure_recovers_after_database_reopen_once(self):
        record = await self.promise()
        with patch.object(self.archive, "enqueue_durable_task", new=AsyncMock(side_effect=RuntimeError("队列暂不可用"))):
            with self.assertRaises(RuntimeError):
                await self.schedule(record)
        await self.archive.aclose()
        self.archive = LifeArchive(self.path)
        self.runtime.archive = self.archive
        self.assertEqual(await self.runtime.reconcile_commitment_contact_tasks(now=self.now), 1)
        self.assertEqual(await self.runtime.reconcile_commitment_contact_tasks(now=self.now), 0)
        tasks = await self.archive.get_durable_tasks(kind="proactive_commitment")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].available_at, "2026-10-24 13:50:00")
        saved = await self.archive.get_commitment(record.id)
        self.assertEqual(saved.follow_up["execute_at"], "2026-10-24T13:50:00")

    async def test_conditional_recovery_preserves_observation_and_expiry(self):
        record = await self.promise(follow_up={
            "action": "contact_person", "condition": "饭做好后叫对方",
            "check_after_minutes": 15, "observed_at": "2026-10-24T12:00:00+08:00",
        })
        await self.runtime.reconcile_commitment_contact_tasks(now=self.now)
        saved = await self.archive.get_commitment(record.id)
        self.assertEqual(saved.follow_up["execute_at"], "2026-10-24T12:15:00")
        self.assertEqual(saved.follow_up["expires_at"], "2026-10-25T12:00:00")

    async def test_completed_task_is_not_resurrected(self):
        record = await self.promise()
        await self.schedule(record)
        await self.archive.complete_durable_task_by_key(f"proactive_commitment:{record.id}", {"outcome": "sent"})
        self.assertEqual(await self.runtime.reconcile_commitment_contact_tasks(now=self.now), 0)
        task = await self.archive.get_durable_task(f"proactive_commitment:{record.id}")
        self.assertEqual(task.status, "completed")

    async def test_partial_or_unknown_delivery_does_not_complete_commitment(self):
        for status in ("partial", "unknown"):
            record = await self.promise(content=f"测试{status}回执", trigger_time="13:35")
            await self.schedule(record, execute_at="2026-10-24T13:35:00+08:00")
            task = await self.prepare_delivery(record)
            async def send(*args, **kwargs):
                kwargs["send_payload"]["_delivery"] = {"status": status, "sent_text": "第一段" if status == "partial" else ""}
                return status == "partial"
            self.runtime._send_proactive_message = send
            result = await self.runtime.run_proactive_commitment_task(task)
            self.assertEqual(result["outcome"], status)
            self.assertEqual((await self.archive.get_commitment(record.id)).status, "delivery_failed")

    async def test_cancelled_delivery_keeps_receipt_and_never_retries_unknown_send(self):
        record = await self.promise(trigger_time="13:35")
        await self.schedule(record, execute_at="2026-10-24T13:35:00+08:00")
        task = await self.prepare_delivery(record)
        async def send(*args, **kwargs):
            kwargs["send_payload"]["_delivery"] = {"status":"unknown"}
            raise asyncio.CancelledError()
        self.runtime._send_proactive_message = send
        with self.assertRaises(asyncio.CancelledError):
            await self.runtime.run_proactive_commitment_task(task)
        self.assertEqual((await self.archive.get_commitment(record.id)).status, "delivery_failed")
        self.assertEqual((await self.runtime.run_proactive_commitment_task(task))["outcome"], "delivery_failed")

    async def test_no_backfill_for_overdue_or_unowned_group_media_promises(self):
        for changes in (
            {"trigger_time": "13:00"}, {"owner": "说话人"},
            {"owner": "共同"}, {"source_session": "test:GroupMessage:1"},
            {"media_kind": "photo"}, {"status": "cancelled"},
        ):
            await self.promise(**changes)
        self.assertEqual(await self.runtime.reconcile_commitment_contact_tasks(now=self.now), 0)
        self.assertEqual(await self.archive.get_durable_tasks(), [])

    async def test_semantic_review_recovers_knock_without_making_all_plans_contact(self):
        contact = await self.promise()
        ordinary = await self.promise(content="下午自己去散步", source_message="一点五十出门散步")
        self.assertEqual(await self.runtime.reconcile_commitment_contact_tasks(now=self.now), 2)
        self.assertEqual((await self.prepare_review(contact, {"action": "contact_person", "message_goal": "按约定叫对方出门"}))["outcome"], "scheduled")
        self.assertEqual((await self.prepare_review(ordinary, {"action": "none"}))["outcome"], "no_contact")
        tasks = await self.archive.get_durable_tasks(kind="proactive_commitment")
        self.assertEqual([task.payload["commitment_id"] for task in tasks], [contact.id])
        self.assertEqual((await self.archive.get_commitment(ordinary.id)).follow_up["reviewed"], True)

    async def test_rescheduled_pending_task_moves_in_both_directions(self):
        record = await self.promise()
        await self.schedule(record)
        with patch("core.archive.promises.life_now", return_value=self.now):
            for new_time in ("15:30", "13:40"):
                await self.archive.reschedule_commitment(record.id, "2026-10-24", trigger_time=new_time)
                task = await self.archive.get_durable_task(f"proactive_commitment:{record.id}")
                self.assertEqual(task.available_at, f"2026-10-24 {new_time}:00")
                await self.prepare_delivery(record)
                result = await self.runtime.run_proactive_commitment_task(task)
                self.assertEqual(result["retry_at"], task.available_at)
                self.runtime._send_proactive_message.assert_not_awaited()

    async def test_reschedule_to_vague_window_does_not_use_old_clock(self):
        record = await self.promise()
        await self.schedule(record)
        with patch("core.archive.promises.life_now", return_value=self.now):
            await self.archive.reschedule_commitment(record.id, "2026-10-25", time_window="傍晚")
        saved = await self.archive.get_commitment(record.id)
        self.assertIn("傍晚", saved.follow_up["condition"])
        task = await self.prepare_delivery(record)
        result = await self.runtime.run_proactive_commitment_task(task)
        self.assertEqual(result["retry_at"], "2026-10-25 00:00:00")
        self.runtime._send_proactive_message.assert_not_awaited()

    async def test_cancellation_during_model_evaluation_prevents_delivery(self):
        record = await self.promise()
        await self.schedule(record, execute_at="2026-10-24 13:30")
        async def evaluate(**kwargs):
            await self.archive.set_commitment_status(record.id, "cancelled")
            return {"should_send": True, "settlement": "send", "reply_text": "走吧"}
        task = await self.prepare_delivery(record, evaluate)
        self.assertEqual((await self.runtime.run_proactive_commitment_task(task))["outcome"], "cancelled")
        self.runtime._send_proactive_message.assert_not_awaited()

    async def test_reschedule_during_model_evaluation_prevents_old_delivery(self):
        record = await self.promise()
        await self.schedule(record, execute_at="2026-10-24 13:30")
        async def evaluate(**kwargs):
            await self.archive.reschedule_commitment(record.id, "2026-10-24", trigger_time="16:00")
            return {"should_send": True, "settlement": "send", "reply_text": "走吧"}
        task = await self.prepare_delivery(record, evaluate)
        with patch("core.archive.promises.life_now", return_value=self.now):
            self.assertIn("retry_at", await self.runtime.run_proactive_commitment_task(task))
        self.runtime._send_proactive_message.assert_not_awaited()

    async def test_condition_wait_does_not_mark_promise_done(self):
        record = await self.promise()
        await self.schedule(record, execute_at="2026-10-24 13:30", condition="饭做好了")
        task = await self.prepare_delivery(record, AsyncMock(return_value={
            "should_send": False, "settlement": "wait", "retry_after_minutes": 20,
        }))
        result = await self.runtime.run_proactive_commitment_task(task)
        self.assertEqual(result["retry_at"], "2026-10-24 13:55:00")
        self.assertEqual((await self.archive.get_commitment(record.id)).status, "active")
        self.runtime._send_proactive_message.assert_not_awaited()

    async def test_contact_and_activity_share_day_without_simulated_completion(self):
        record = await self.promise(kind="plan", follow_up={"action": "contact_person"})
        await self.archive.save_day(DayRecord(date="2026-10-24", timeline=[
            TimelineItem(time="13:00", activity="在家休息"),
        ]))
        runtime = SpineInviteComponent.__new__(SpineInviteComponent)
        runtime.archive = self.archive
        runtime.composer = SimpleNamespace(reconcile_commitment_with_timeline=AsyncMock(return_value=(
            [TimelineItem(time="13:50", activity="一起出门散步")],
            {"should_apply": True, "shared_activity": True},
        )))
        runtime.mark_page_status_changed = AsyncMock()
        runtime.schedule_invite_outfit_sync = lambda *args: None
        runtime.schedule_invite_contact = self.runtime.schedule_invite_contact
        self.assertTrue(await runtime.apply_commitment_to_current_day(record, now=self.now))
        self.assertEqual((await self.archive.get_commitment(record.id)).status, "scheduled")
        self.assertIsNone(await self.archive.get_durable_task(f"invite_contact:{record.id}"))
        self.assertFalse(await self.archive.complete_simulated_commitment(
            record.id, scope=record.source_session, when="2026-10-24 14:00:00",
            evidence="已经出门了", source_id="action-1",
        ))
        self.assertEqual((await self.archive.get_commitment(record.id)).status, "scheduled")

    async def test_simulated_completion_preserves_unreviewed_external_task(self):
        record = await self.promise(kind="plan")
        await self.archive.enqueue_durable_task(
            f"commitment_contact_review:{record.id}", "commitment_contact_review", {"commitment_id": record.id},
        )
        self.assertFalse(await self.archive.complete_simulated_commitment(
            record.id, scope=record.source_session, when="2026-10-24 14:00:00",
            evidence="已经出门了", source_id="action-1",
        ))
        ordinary = await self.promise(kind="plan", content="整理书桌", source_message="我整理一下书桌")
        self.assertTrue(await self.archive.complete_simulated_commitment(
            ordinary.id, scope=ordinary.source_session, when="2026-10-24 14:00:00",
            evidence="书桌整理好了", source_id="action-2",
        ))

    async def evaluate_settlement(self, *, refs, settlement="already_done"):
        record = await self.promise()
        await self.schedule(record, execute_at="2026-10-24 13:30")
        original = self.runtime._part_proactive_followup_component._evaluate_proactive_commitment
        task = await self.prepare_delivery(record, original)
        self.runtime.config = SimpleNamespace(proactive=SimpleNamespace(provider=""))
        self.runtime._get_proactive_provider = AsyncMock(return_value=object())
        self.runtime._current_proactive_persona = AsyncMock(return_value="自然亲切")
        self.runtime._read_recent_context_messages = AsyncMock(return_value=[{
            "role": "user", "content": "你刚叫过我了，我马上出来", "message_id": "actual-message",
            "timestamp": "2026-10-24 13:34:00",
        }])
        self.runtime._proactive_current_day = AsyncMock(return_value=("2026-10-24", False, DayRecord(
            date="2026-10-24", timeline=[TimelineItem(time="13:30", activity="准备出门")],
        )))
        self.runtime.build_hidden_activity_hint = lambda *args: ("", "准备出门")
        self.runtime._proactive_voice_capability = lambda *args: "text"
        self.runtime.close_text_session = AsyncMock()
        model = AsyncMock(return_value={
            "should_send": False, "settlement": settlement, "evidence_refs": refs,
            "settlement_evidence": [{"content": "模型自行补写的凭据"}],
        })
        with patch("core.runtime.proactive.followup.call_pure_json", new=model):
            result = await self.runtime.run_proactive_commitment_task(task)
        self.runtime._send_proactive_message.assert_not_awaited()
        self.runtime.close_text_session.assert_awaited_once()
        return record, result

    async def test_terminal_settlement_requires_actual_observation(self):
        record, result = await self.evaluate_settlement(refs=["m1"])
        self.assertEqual(result["outcome"], "already_done")
        self.assertEqual(result["evidence"][0]["message_id"], "actual-message")
        self.assertEqual((await self.archive.get_commitment(record.id)).status, "done")

    async def test_missing_evidence_cannot_finish_contact(self):
        record, result = await self.evaluate_settlement(refs=[])
        self.assertIn("retry_at", result)
        self.assertEqual((await self.archive.get_commitment(record.id)).status, "active")

    async def test_plan_reference_cannot_cancel_contact(self):
        record, result = await self.evaluate_settlement(refs=["planned_life"], settlement="cancelled")
        self.assertIn("retry_at", result)
        self.assertEqual((await self.archive.get_commitment(record.id)).status, "active")

    async def test_v28_migration_preserves_old_promise_and_enables_recovery(self):
        record = await self.promise()
        self.archive._conn.execute("ALTER TABLE commitments DROP COLUMN follow_up_json")
        self.archive._conn.execute("UPDATE meta SET value = '28' WHERE key = 'schema_version'")
        self.archive._conn.commit()
        init_schema(self.archive._conn)
        init_schema(self.archive._conn)
        self.assertEqual(schema_fingerprint(self.archive._conn), CURRENT_SCHEMA_FINGERPRINT)
        saved = await self.archive.get_commitment(record.id)
        self.assertEqual(saved.content, record.content)
        self.assertEqual(saved.follow_up, {})
        self.assertEqual(await self.runtime.reconcile_commitment_contact_tasks(now=self.now), 1)
