import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from support import LifeArchive  # isort: skip

from core.models import CommitmentRecord, DayRecord, TimelineItem
from core.runtime.proactive.followup import ProactiveFollowupMixin
from core.runtime.proactive.send import ProactiveSendMixin
from core.runtime.spine.boot import SpineBootMixin
from core.sources.dispatch import PermanentScopeDeliveryError


async def value(result):
    return result


class SharedActivityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.directory.name) / "life.db")
        self.runtime = ProactiveFollowupMixin.__new__(ProactiveFollowupMixin)
        self.runtime.archive = self.archive
        self.now = datetime.datetime(2026, 10, 3, 18, 0)
        self.clock = patch("core.runtime.proactive.rendezvous.life_now", lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.day = await self.archive.save_day(
            DayRecord(
                date="2026-10-03",
                timeline=[
                    TimelineItem(time="18:15", activity="换上外出衣服"),
                    TimelineItem(time="18:30", activity="出发去电影院"),
                    TimelineItem(time="19:00", activity="和小林看已约好的电影"),
                    TimelineItem(time="22:00", activity="回家休息"),
                ],
            )
        )
        self.commitment = await self.archive.save_commitment(
            CommitmentRecord(
                content="和小林19点一起看电影",
                owner="共同",
                source="chat",
                trigger_date=self.day.date,
                source_session="test:FriendMessage:1",
                status="scheduled",
            )
        )
        self.recent = []
        self.sent = []
        self.decisions = []
        self.runtime._read_recent_context_messages = lambda *args, **kwargs: value(
            list(self.recent)
        )
        self.runtime._proactive_commitment_relationship = lambda scope: value(None)
        self.runtime.resolve_interaction_context = lambda **kwargs: value(
            SimpleNamespace(mode="remote", evidence="远程聊天")
        )
        self.runtime._snapshot_proactive_send_event = lambda scope: SimpleNamespace(
            unified_msg_origin=scope
        )

        async def evaluate(**kwargs):
            self.decisions.append(kwargs)
            return self.decision

        async def send(scope, text, *args, **kwargs):
            self.sent.append((scope, text, kwargs))
            return True

        self.runtime._evaluate_shared_activity_contact = evaluate
        self.runtime._send_proactive_message = send
        self.decision = {
            "decision": "wait",
            "activity_index": 2,
            "contact_at": "2026-10-03 18:20:00",
            "message_goal": "商量电影前吃喝",
            "reason": "给出发前准备留时间",
        }
        await self.runtime.schedule_invite_contact(
            self.commitment, observed_at=self.now
        )
        self.task = (await self.archive.get_durable_tasks())[0]

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.directory.cleanup()

    async def run_contact(self):
        return await self.runtime.run_proactive_commitment_task(self.task)

    async def send_now(self):
        self.decision = {
            "decision": "send",
            "activity_index": 2,
            "contact_at": self.now.strftime("%Y-%m-%d %H:%M:%S"),
            "reply_text": "咱们要不要顺路买点喝的？",
            "reason": "准备出发",
        }
        return await self.run_contact()

    async def test_wait_binds_actual_activity_and_persists_plan_across_worker_restart(
        self,
    ):
        worker = SpineBootMixin.__new__(SpineBootMixin)
        worker.archive = self.archive
        worker._durable_task_owner = "test-worker"
        worker._durable_runtime_handlers = {
            "proactive_commitment": self.runtime.run_proactive_commitment_task
        }
        self.assertEqual(await worker._run_durable_tasks_once(), 0)
        saved = (await self.archive.get_durable_tasks())[0]
        self.assertEqual(saved.available_at, "2026-10-03 18:20:00")
        self.assertEqual(saved.result["progress"]["activity"]["index"], 2)
        self.assertEqual(saved.attempts, 0)
        await self.archive.aclose()
        self.archive = LifeArchive(Path(self.directory.name) / "life.db")
        restored = (await self.archive.get_durable_tasks())[0]
        self.assertEqual(restored.result, saved.result)

    async def test_close_start_still_sends_natural_contact_without_settling_activity(
        self,
    ):
        self.now = datetime.datetime(2026, 10, 3, 18, 57)
        result = await self.send_now()
        self.assertEqual(result["outcome"], "sent")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1], "咱们要不要顺路买点喝的？")
        self.assertEqual(
            self.sent[0][2]["source_event"]._daily_life_proactive_expires_at,
            datetime.datetime(2026, 10, 3, 19, 0),
        )
        self.assertEqual(
            (await self.archive.get_commitment(self.commitment.id)).status, "scheduled"
        )

    async def test_already_discussed_preparation_is_silent_without_settling_activity(
        self,
    ):
        self.recent = [{"role": "user", "content": "喝的买好了，直接碰头吧"}]
        self.decision = {"decision": "skip", "reason": "吃喝和会合已经聊好了"}
        result = await self.run_contact()
        self.assertEqual(result["outcome"], "skipped")
        self.assertEqual(self.sent, [])
        self.assertEqual(
            (await self.archive.get_commitment(self.commitment.id)).status, "scheduled"
        )

    async def test_messages_arriving_during_model_work_are_rechecked(self):
        async def evaluate(**kwargs):
            self.recent.append({"role": "user", "content": "今天不去了"})
            return {
                "decision": "send",
                "activity_index": 2,
                "contact_at": "2026-10-03 18:00:00",
                "reply_text": "该准备啦",
            }

        self.runtime._evaluate_shared_activity_contact = evaluate
        result = await self.run_contact()
        self.assertIn("retry_at", result)
        self.assertEqual(self.sent, [])

    async def test_cancel_during_model_work_does_not_send_or_restore_commitment(self):
        async def evaluate(**kwargs):
            await self.archive.set_commitment_status(self.commitment.id, "cancelled")
            return self.decision

        self.runtime._evaluate_shared_activity_contact = evaluate
        result = await self.run_contact()
        self.assertEqual(result["outcome"], "cancelled")
        self.assertEqual(self.sent, [])

    async def test_expired_contact_does_not_attach_to_later_unrelated_activity(self):
        self.now = datetime.datetime(2026, 10, 3, 19, 2)
        with self.assertRaises(ValueError):
            await self.send_now()
        self.assertEqual(self.sent, [])

    async def test_activity_begins_while_model_is_working(self):
        self.now = datetime.datetime(2026, 10, 3, 18, 59, 50)

        async def evaluate(**kwargs):
            self.now += datetime.timedelta(seconds=15)
            return {
                "decision": "send",
                "activity_index": 2,
                "contact_at": "2026-10-03 18:59:50",
                "reply_text": "准备好了吗？",
            }

        self.runtime._evaluate_shared_activity_contact = evaluate
        result = await self.run_contact()
        self.assertEqual(result["outcome"], "expired")
        self.assertEqual(self.sent, [])

    async def test_new_timeline_reschedules_pending_contact_for_semantic_replanning(
        self,
    ):
        result = await self.run_contact()
        task = (
            await self.archive.lease_durable_tasks("owner", now="2026-10-03 18:00:00")
        )[0]
        await self.archive.defer_durable_task(
            task.id, result["retry_at"], owner="owner", progress=result["progress"]
        )
        changed = await self.archive.get_day(self.day.date)
        changed.timeline[2].time = "18:10"
        await self.archive.save_day(changed)
        await self.runtime.reconcile_scheduled_invite_contacts(now=self.now)
        saved = (await self.archive.get_durable_tasks())[0]
        self.assertEqual(saved.available_at, "2026-10-03 18:00:00")

    async def test_normal_chat_joint_plan_recovers_once_but_solo_plan_does_not(self):
        solo = await self.archive.save_commitment(
            CommitmentRecord(
                content="自己去买书",
                owner="当前角色",
                trigger_date=self.day.date,
                source_session="test:FriendMessage:2",
                status="scheduled",
            )
        )
        other = await self.archive.save_commitment(
            CommitmentRecord(
                content="和另一个朋友一起看展",
                owner="共同",
                trigger_date=self.day.date,
                source_session="test:FriendMessage:3",
                status="scheduled",
                source="chat",
            )
        )
        self.assertEqual(
            await self.runtime.reconcile_scheduled_invite_contacts(now=self.now), 1
        )
        self.assertEqual(
            await self.runtime.reconcile_scheduled_invite_contacts(now=self.now), 0
        )
        tasks = await self.archive.get_durable_tasks()
        self.assertIn(other.id, [task.payload["commitment_id"] for task in tasks])
        self.assertNotIn(solo.id, [task.payload["commitment_id"] for task in tasks])

    async def test_explicit_followup_prevents_duplicate_preparation_contact(self):
        await self.archive.enqueue_durable_task(
            f"proactive_commitment:{self.commitment.id}", "proactive_commitment", {}
        )
        result = await self.run_contact()
        self.assertEqual(result["outcome"], "already_covered")
        self.assertEqual(self.decisions, [])

    async def test_delivery_failure_does_not_fail_shared_activity(self):
        async def send(*args, **kwargs):
            raise PermanentScopeDeliveryError("当前无法私聊", code="cannot_send")

        self.runtime._send_proactive_message = send
        result = await self.send_now()
        self.assertEqual(result["outcome"], "undeliverable")
        self.assertEqual(
            (await self.archive.get_commitment(self.commitment.id)).status, "scheduled"
        )

    async def test_send_snapshot_expires_before_slow_voice_can_deliver(self):
        runtime = ProactiveSendMixin.__new__(ProactiveSendMixin)
        runtime.media_request_is_current_turn = lambda event: True
        event = SimpleNamespace(_daily_life_proactive_expires_at=self.now)
        with patch("core.runtime.proactive.send.life_now", return_value=self.now):
            self.assertFalse(runtime._proactive_send_is_current(event))

    async def test_wait_time_arrives_during_model_work_and_is_rechecked_without_failure(
        self,
    ):
        async def evaluate(**kwargs):
            self.now += datetime.timedelta(minutes=25)
            return self.decision

        self.runtime._evaluate_shared_activity_contact = evaluate
        result = await self.run_contact()
        self.assertEqual(result["retry_at"], "2026-10-03 18:25:00")
        self.assertEqual(self.sent, [])

    async def test_previous_activity_start_stops_contact_without_another_model_call(
        self,
    ):
        planning = await self.run_contact()
        self.task.result = {"progress": planning["progress"]}
        self.now = datetime.datetime(2026, 10, 3, 19, 2)
        self.decisions.clear()
        result = await self.run_contact()
        self.assertEqual(result["outcome"], "expired")
        self.assertEqual(self.decisions, [])

    async def test_other_lease_owner_cannot_replace_contact_progress(self):
        leased = (
            await self.archive.lease_durable_tasks("owner", now="2026-10-03 18:00:00")
        )[0]
        updated = await self.archive.defer_durable_task(
            leased.id,
            "2026-10-03 18:20:00",
            owner="another-owner",
            progress={"activity": "wrong"},
        )
        saved = await self.archive.get_durable_task(self.task.task_key)
        self.assertFalse(updated)
        self.assertEqual(saved.status, "leased")
        self.assertNotIn("progress", saved.result)

    async def test_today_confirmation_without_explicit_date_uses_approved_activity_date(
        self,
    ):
        self.commitment.trigger_date = ""
        self.commitment = await self.archive.save_commitment(self.commitment)
        await self.runtime.schedule_invite_contact(
            self.commitment, observed_at=self.now
        )
        # 首个任务已属于本次确认的生活日。
        result = await self.send_now()
        self.assertEqual(result["outcome"], "sent")
