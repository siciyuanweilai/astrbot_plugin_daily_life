import datetime
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from support import LifeArchive  # isort: skip

from componentfixtures import (
    ProactiveFollowupComponent,
    ProactiveSendComponent,
    SpineBootComponent,
)
from core.models import CommitmentRecord, DayRecord, TimelineItem
from core.sources.dispatch import PermanentScopeDeliveryError


async def value(result):
    return result


class SharedActivityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = LifeArchive(Path(self.directory.name) / "life.db")
        self.runtime = ProactiveFollowupComponent.__new__(ProactiveFollowupComponent)
        self.runtime.archive = self.archive
        self.now = datetime.datetime(2026, 10, 3, 18, 0)
        self.clock = patch("core.runtime.proactive.rendezvous.life_now", lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        for target in ("core.runtime.spine.boot.life_now", "core.archive.knowledge.life_now"):
            clock = patch(target, lambda: self.now)
            clock.start()
            self.addCleanup(clock.stop)
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

        self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = evaluate
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

    def worker(self):
        worker = SpineBootComponent.__new__(SpineBootComponent)
        worker.archive = self.archive
        worker._durable_task_owner = "test-worker"
        worker._durable_runtime_handlers = {
            "proactive_commitment": self.runtime.run_proactive_commitment_task
        }
        return worker

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
        worker = self.worker()
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

    async def test_partial_or_unknown_contact_is_not_reported_as_sent(self):
        for status in ("partial", "unknown"):
            async def send(*args, **kwargs):
                kwargs["send_payload"]["_delivery"] = {"status":status,"sent_text":"一段"}
                return status == "partial"
            self.runtime._send_proactive_message = send
            self.assertEqual((await self.send_now())["outcome"],status)

    async def test_cancel_after_uncertain_delivery_closes_task_without_completing_activity(self):
        async def send(*args, **kwargs):
            kwargs["send_payload"]["_delivery"] = {"status":"unknown"}
            raise asyncio.CancelledError()
        self.runtime._send_proactive_message = send
        with self.assertRaises(asyncio.CancelledError):
            await self.send_now()
        task=await self.archive.get_durable_task(self.task.task_key)
        self.assertEqual(task.status,"completed")
        self.assertEqual(task.result["outcome"],"unknown")
        self.assertEqual((await self.archive.get_commitment(self.commitment.id)).status,"scheduled")

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

    async def test_outfit_change_during_contact_generation_requires_new_decision(self):
        async def evaluate(**kwargs):
            day = await self.archive.get_day(self.day.date)
            day.outfit = "刚换好的出门装"
            await self.archive.save_day(day)
            return self.decision
        self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = evaluate
        result = await self.send_now()
        self.assertIn("retry_at", result)
        self.assertEqual(self.sent, [])

    async def test_unsupported_completion_claim_is_reviewed_again_without_sending(self):
        from core.runtime.proactive.rendezvous import SharedActivityContactComponent
        self.runtime.get_text_provider = AsyncMock(return_value=object())
        self.runtime._current_proactive_persona = AsyncMock(return_value="角色")
        self.runtime._proactive_voice_capability = lambda scope: "文字"
        self.runtime._format_recent_context_messages = lambda recent, **kw: "等我两分钟"
        self.runtime.close_text_session = AsyncMock()
        self.runtime.config = SimpleNamespace(proactive=SimpleNamespace(contact_provider="contact-test"))
        self.runtime._audit_proactive_continuity = AsyncMock(return_value=(False, "没有换装完成证据"))
        self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = SharedActivityContactComponent(
            self.runtime
        )._evaluate_shared_activity_contact
        with patch("core.runtime.proactive.rendezvous.call_pure_json", AsyncMock(return_value={
            "decision": "send", "reply_text": "换好啦", "activity_index": 2,
            "contact_at": self.now.isoformat(),
        })):
            result = await self.run_contact()
        self.assertIn("retry_at", result)
        self.assertEqual(self.sent, [])
        facts = self.runtime._audit_proactive_continuity.call_args.kwargs["life_context"]
        self.assertIn('"committed_actions": []', facts)
        self.assertIn('"outfit"', facts)
        self.assertEqual(result["progress"]["review_issue"], "没有换装完成证据")
        self.runtime.get_text_provider.assert_awaited_once_with("contact-test")
        self.assertEqual(
            self.runtime._audit_proactive_continuity.call_args.kwargs["provider_id"],
            "contact-test",
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

        self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = evaluate
        result = await self.run_contact()
        self.assertIn("retry_at", result)
        self.assertEqual(self.sent, [])

    async def test_cancel_during_model_work_does_not_send_or_restore_commitment(self):
        async def evaluate(**kwargs):
            await self.archive.set_commitment_status(self.commitment.id, "cancelled")
            return self.decision

        self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = evaluate
        result = await self.run_contact()
        self.assertEqual(result["outcome"], "cancelled")
        self.assertEqual(self.sent, [])

    async def test_expired_contact_does_not_attach_to_later_unrelated_activity(self):
        self.now = datetime.datetime(2026, 10, 3, 19, 2)
        result = await self.send_now()
        self.assertEqual(result["outcome"], "expired")
        self.assertEqual(self.sent, [])

    async def test_delayed_executable_activity_is_still_available_for_coordination(self):
        day = await self.archive.get_day(self.day.date)
        day.timeline[2].execution_state = "planned"
        day.meta["planned_life_actions"] = json.dumps([{
            "action_id": "shared-walk", "action_type": "social", "target": "一起看电影",
            "timeline_index": 2, "duration_minutes": 90,
            "payload": {"schedule_commitment_id": self.commitment.id,
                        "depends_on_action_ids": ["unfinished-preparation"]},
        }])
        await self.archive.save_day(day)
        self.now = datetime.datetime(2026, 10, 3, 19, 2)
        self.decision = {"decision": "send", "activity_index": 2,
                         "reply_text": "我还差一点收尾，晚一会儿出发可以吗？", "reason": "准备尚未完成"}
        result = await self.run_contact()
        self.assertEqual(result["outcome"], "sent")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((await self.archive.get_day(self.day.date)).timeline[2].execution_state, "planned")
        entities = await self.archive.get_living_entities(self.commitment.source_session, kind="shared_activity")
        self.assertEqual(entities[0]["state"], "planned")

    async def test_delayed_task_is_not_expired_during_recovery(self):
        day = await self.archive.get_day(self.day.date)
        day.timeline[2].execution_state = "planned"
        day.meta["planned_life_actions"] = json.dumps([{
            "action_id": "shared-walk", "action_type": "social", "timeline_index": 2,
            "payload": {"schedule_commitment_id": self.commitment.id},
        }])
        await self.archive.save_day(day)
        result = await self.run_contact()
        await self.archive.update_durable_task_progress(self.task.id, owner=self.task.lease_owner, progress=result["progress"])
        self.now = datetime.datetime(2026, 10, 3, 19, 2)
        await self.runtime.reconcile_scheduled_invite_contacts(now=self.now)
        saved = await self.archive.get_durable_task(self.task.task_key)
        self.assertEqual(saved.status, "pending")

    async def test_send_uses_actual_clock_without_requiring_model_timestamp(self):
        for model_time in (None, "", "2026-10-03 17:59:00", "2026-10-03 19:00:00"):
            with self.subTest(model_time=model_time):
                self.now = datetime.datetime(2026, 10, 3, 18, 0, 25)

                async def evaluate(**kwargs):
                    self.now += datetime.timedelta(seconds=5)
                    return {
                        "decision": "send", "activity_index": 2,
                        "contact_at": model_time, "reply_text": "要不要顺路买点喝的？",
                    }

                self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = evaluate
                result = await self.run_contact()
                self.assertEqual(result["outcome"], "sent")
                self.assertEqual(result["progress"]["contact_at"], "2026-10-03T18:00:30")
                self.assertEqual(len(self.sent), 1)
                self.sent.clear()

    async def test_wait_still_normalizes_timezone_and_keeps_model_selected_time(self):
        self.decision["contact_at"] = "2026-10-03T10:20:00+00:00"
        result = await self.run_contact()
        self.assertEqual(result["retry_at"], "2026-10-03 18:20:00")
        self.assertEqual(self.sent, [])

    async def test_invalid_wait_saves_boundaries_and_rejected_decision_for_retry(self):
        for contact_at in (
            "", "不是时间", "2026-10-02 18:20:00", "2026-10-03 18:00:00",
            "2026-10-03 19:00:00", "2026-10-03 19:10:00",
        ):
            with self.subTest(contact_at=contact_at):
                self.task = (await self.archive.lease_durable_tasks(
                    "owner", now="2026-10-03 18:00:00",
                ))[0]
                self.decision["contact_at"] = contact_at
                with self.assertRaises(ValueError):
                    await self.run_contact()
                saved = await self.archive.get_durable_task(self.task.task_key)
                progress = saved.result["progress"]
                self.assertTrue(progress["review_issue"])
                self.assertEqual(progress["rejected_decision"]["contact_at"], contact_at)
                self.assertEqual(progress["judged_at"], "2026-10-03T18:00:00")
                self.assertEqual(progress["activity"]["starts_at"], "2026-10-03T19:00:00")
                self.assertEqual(self.sent, [])
                await self.archive.defer_durable_task(
                    saved.id, "2026-10-03 18:00:00", owner="owner", progress=progress,
                )

    async def test_retry_uses_persisted_issue_and_latest_chat_then_can_skip_naturally(self):
        from core.runtime.proactive.rendezvous import SharedActivityContactComponent

        worker = self.worker()
        self.decision["contact_at"] = "2026-10-03 17:59:00"
        self.assertEqual(await worker._run_durable_tasks_once(), 0)
        saved = await self.archive.get_durable_task(self.task.task_key)
        self.assertEqual(saved.status, "pending")
        self.assertEqual(saved.attempts, 1)
        self.assertTrue(saved.result["progress"]["review_issue"])
        await self.archive.aclose()
        self.archive = LifeArchive(Path(self.directory.name) / "life.db")
        self.runtime.archive = self.archive
        self.recent = [{"role": "user", "content": "饮料已经买好了，直接碰头吧"}]
        self.runtime.get_text_provider = AsyncMock(return_value=object())
        self.runtime._current_proactive_persona = AsyncMock(return_value="角色")
        self.runtime._proactive_voice_capability = lambda scope: "文字"
        self.runtime._format_recent_context_messages = lambda recent, **kwargs: str(recent)
        self.runtime.close_text_session = AsyncMock()
        self.runtime.config = SimpleNamespace(proactive=SimpleNamespace(contact_provider="contact-test"))
        self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = SharedActivityContactComponent(
            self.runtime,
        )._evaluate_shared_activity_contact
        self.now += datetime.timedelta(minutes=2)
        with patch("core.runtime.proactive.rendezvous.call_pure_json", AsyncMock(return_value={
            "decision": "skip", "reason": "刚刚已商量好，不重复联系",
        })) as model:
            self.assertEqual(await self.worker()._run_durable_tasks_once(), 1)
        prompt = model.call_args.args[2]
        self.runtime.get_text_provider.assert_awaited_once_with("contact-test")
        self.assertEqual(model.call_args.kwargs["primary_provider_id"], "contact-test")
        self.assertIn("稍后联系时间必须晚于判断时刻且早于活动开始", prompt)
        self.assertIn("2026-10-03 17:59:00", prompt)
        self.assertIn("当前时间：2026-10-03 18:02:00", prompt)
        self.assertIn("饮料已经买好了", prompt)
        saved = await self.archive.get_durable_task(self.task.task_key)
        self.assertEqual(saved.result["outcome"], "skipped")
        self.assertEqual(self.sent, [])
        self.assertEqual((await self.archive.get_commitment(self.commitment.id)).status, "scheduled")

    async def test_repeated_invalid_waits_keep_existing_attempt_limit(self):
        self.decision["contact_at"] = "2026-10-03 19:10:00"
        worker = self.worker()
        for attempt in range(1, self.task.max_attempts + 1):
            self.assertEqual(await worker._run_durable_tasks_once(), 0)
            saved = await self.archive.get_durable_task(self.task.task_key)
            self.assertEqual(saved.attempts, attempt)
            self.assertTrue(saved.result["progress"]["review_issue"])
            if attempt < self.task.max_attempts:
                self.assertEqual(saved.status, "pending")
                self.assertEqual(await worker._run_durable_tasks_once(), 0)
                self.assertEqual(len(self.decisions), attempt)
                self.now = datetime.datetime.fromisoformat(saved.available_at)
        self.assertEqual(saved.status, "dead")
        self.assertEqual(await worker._run_durable_tasks_once(), 0)
        self.assertEqual(len(self.decisions), self.task.max_attempts)
        self.assertEqual(self.sent, [])

    async def test_invalid_wait_does_not_retry_after_bound_activity_has_started(self):
        self.decision["contact_at"] = "2026-10-03 19:10:00"
        worker = self.worker()
        await worker._run_durable_tasks_once()
        self.now = datetime.datetime(2026, 10, 3, 19, 2)
        self.assertEqual(await worker._run_durable_tasks_once(), 1)
        saved = await self.archive.get_durable_task(self.task.task_key)
        self.assertEqual(saved.result["outcome"], "expired")
        self.assertEqual(len(self.decisions), 1)
        self.assertEqual(self.sent, [])

    async def test_reconciliation_expires_bound_contact_without_clearing_its_activity(self):
        self.decision["contact_at"] = "2026-10-03 19:10:00"
        worker = self.worker()
        await worker._run_durable_tasks_once()
        previous = await self.archive.get_durable_task(self.task.task_key)
        self.now = datetime.datetime(2026, 10, 3, 19, 2)
        await self.runtime.reconcile_scheduled_invite_contacts(now=self.now)
        saved = await self.archive.get_durable_task(self.task.task_key)
        self.assertEqual(saved.status, "completed")
        self.assertEqual(saved.result["outcome"], "expired")
        self.assertEqual(saved.result["progress"], previous.result["progress"])
        self.assertEqual(await worker._run_durable_tasks_once(), 0)
        self.assertEqual(len(self.decisions), 1)
        self.assertEqual(self.sent, [])
        self.assertEqual((await self.archive.get_commitment(self.commitment.id)).status, "scheduled")

    async def test_activity_start_during_model_work_precedes_invalid_time_check(self):
        self.now = datetime.datetime(2026, 10, 3, 18, 59, 50)

        async def evaluate(**kwargs):
            self.now += datetime.timedelta(seconds=15)
            return {"decision": "wait", "activity_index": 2, "contact_at": "无效时间"}

        self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = evaluate
        result = await self.run_contact()
        self.assertEqual(result["outcome"], "expired")
        self.assertEqual(self.sent, [])

    async def test_later_plan_change_does_not_reopen_contact_for_started_activity(self):
        worker = self.worker()
        await worker._run_durable_tasks_once()
        changed = await self.archive.get_day(self.day.date)
        changed.timeline[3].time = "22:30"
        await self.archive.save_day(changed)
        self.now = datetime.datetime(2026, 10, 3, 19, 2)
        await self.runtime.reconcile_scheduled_invite_contacts(now=self.now)
        saved = await self.archive.get_durable_task(self.task.task_key)
        self.assertEqual(saved.status, "completed")
        self.assertEqual(saved.result["outcome"], "expired")
        self.assertEqual(await worker._run_durable_tasks_once(), 0)
        self.assertEqual(len(self.decisions), 1)
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

        self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = evaluate
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
        runtime = ProactiveSendComponent.__new__(ProactiveSendComponent)
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

        self.runtime._part_shared_activity_contact_component._evaluate_shared_activity_contact = evaluate
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
