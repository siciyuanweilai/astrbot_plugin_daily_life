"""真实 SQLite 进度与发送边界回归；不调用外部模型或真实联系人。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import support
from core.runtime.proactive.grounding import ProactiveGrounding, conversation_snapshot
from core.runtime.proactive.send import ProactiveSendComponent
from core.sources.history import SavedHistoryReader
from runtimehelpers import RuntimeAsyncHelpers


def plan(mode="social", request="", **values):
    return {"valid": True, "response_plan": {
        "mode": mode, "request_ref": request, "needs_execution": False,
        "delivers_complete_result": mode == "deliver", "execution_task_keys": [],
        "claims_completed_task_keys": [], "requires_original_media": False, **values}}


class ProactiveGroundingTest(RuntimeAsyncHelpers, unittest.IsolatedAsyncioTestCase):
    scope = "test:FriendMessage:person"

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = support.LifeArchive(Path(self.tmp.name)/"life.db")
        self.runtime, self.provider = self._make_proactive_runtime()
        self.runtime.archive = self.archive
        self.runtime.composer.archive = self.archive
        self.grounding = ProactiveGrounding(self.runtime)
        self.runtime.proactive_grounding = self.grounding
        self.messages = [{"role": "user", "content": "请按原文的段落整理成文字。", "message_id": "request-1"}]
        self.runtime._read_recent_context_messages = AsyncMock(side_effect=lambda *a, **k: list(self.messages))
        self.runtime.close_text_session = AsyncMock()

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.tmp.cleanup()

    def context(self, state="open", deliveries=None):
        snapshot = conversation_snapshot(self.scope, self.messages)
        request = next(m["ref"] for m in snapshot["messages"] if m["role"] == "user")
        return {**snapshot, "work_items": [{"request_ref": request, "summary": "整理原文",
            "state": state, "delivery_refs": deliveries or [], "resolution_ref": ""}], "execution_tasks": []}

    async def test_same_evidence_reuses_persisted_interpretation_after_restart(self):
        work = self.context()["work_items"]
        with patch.object(self.grounding, "_interpret", new=AsyncMock(return_value=work)) as model:
            first, second = await asyncio.gather(*[self.grounding.context(self.scope, self.messages, None, "") for _ in range(2)])
            self.assertEqual(first, second)
            self.assertEqual(model.await_count, 1)
        restarted = ProactiveGrounding(self.runtime)
        with patch.object(restarted, "_interpret", new=AsyncMock(side_effect=AssertionError("must reuse"))):
            self.assertEqual((await restarted.context(self.scope, self.messages, None, ""))["work_items"], work)

    async def test_new_message_and_other_scope_have_independent_interpretations(self):
        with patch.object(self.grounding, "_interpret", new=AsyncMock(return_value=[])) as model:
            await self.grounding.context(self.scope, self.messages, None, "")
            await self.grounding.context("test:FriendMessage:other", self.messages, None, "")
            self.messages.append({"role": "user", "content": "请把标题单列。"})
            await self.grounding.context(self.scope, self.messages, None, "")
            self.assertEqual(model.await_count, 3)

    async def test_slow_conversation_does_not_block_other_scopes(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def interpret(snapshot, *args):
            if snapshot["scope"] == self.scope:
                started.set()
                await release.wait()
            return []
        with patch.object(self.grounding, "_interpret", side_effect=interpret):
            slow = asyncio.create_task(self.grounding.context(self.scope, self.messages, None, ""))
            await started.wait()
            try:
                other = await asyncio.wait_for(self.grounding.context("other", self.messages, None, ""), 1)
                self.assertEqual(other["scope"], "other")
            finally:
                release.set()
                await slow

    def test_delivery_cannot_regress_to_open_without_new_user_request(self):
        self.messages.append({"role": "assistant", "content": "标题\n\n第一段。\n\n第二段。"})
        context = self.context()
        item = context["work_items"][0]
        item["delivery_refs"] = [context["messages"][-1]["ref"]]
        items = self.grounding.validate_progress({"work_items": [item]}, context)
        self.assertEqual(items[0]["state"], "awaiting_feedback")
        context["work_items"] = items
        for proposal in (plan("deliver", item["request_ref"]), plan(needs_execution=True)):
            self.assertIsNone(self.grounding.approve(proposal, context, "我马上重新做好发给你")[0])
        self.assertIsNotNone(self.grounding.approve(plan(), context, "刚泡好茶，终于能坐一会儿了")[0])

    def test_new_user_revision_can_be_delivered(self):
        self.messages += [{"role": "assistant", "content": "上次完整结果"}, {"role": "user", "content": "第二段请换个位置", "message_id": "request-2"}]
        context = self.context()
        item = {**context["work_items"][0], "request_ref": context["messages"][-1]["ref"]}
        context["work_items"] = self.grounding.validate_progress({"work_items": [item]}, context)
        self.assertIsNotNone(self.grounding.approve(plan("deliver", item["request_ref"]), context, "修改后的完整内容")[0])

    def test_invalid_references_and_unproved_resolution_are_rejected(self):
        context = self.context()
        for change in ({"request_ref": "invented"}, {"delivery_refs": ["invented"]}, {"state": "resolved"}, {"state": "awaiting_feedback"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.grounding.validate_progress({"work_items": [{**context["work_items"][0], **change}]}, context)

    def test_missing_media_or_truncated_source_blocks_completion(self):
        context = self.context("needs_input")
        ref = context["work_items"][0]["request_ref"]
        self.assertIsNone(self.grounding.approve(plan("deliver", ref), context, "完整结果")[0])
        self.assertIsNotNone(self.grounding.approve(plan("clarify", ref), context, "把原文贴给我好吗")[0])
        self.assertIsNone(self.grounding.approve(plan(requires_original_media=True), context, "已逐项核对原图")[0])
        context["work_items"][0]["state"] = "open"
        context["messages"][0]["truncated"] = True
        self.assertIsNone(self.grounding.approve(plan("deliver", ref), context, "完整结果")[0])

    def test_missing_invalid_plan_and_unbacked_execution_fail_closed(self):
        context = self.context()
        for proposal in ({"valid": True}, {"valid": False}, plan(needs_execution=True), plan("task_status"), plan(execution_task_keys=[{}])):
            self.assertIsNone(self.grounding.approve(proposal, context, "回复")[0])

    async def test_task_must_match_scope_request_and_current_version(self):
        await self.archive.enqueue_durable_task("task", "proactive_commitment", {"scope": self.scope, "source_message_id": "request-1", "message_goal": "整理原文"})
        await self.archive.enqueue_durable_task("other", "proactive_commitment", {"scope": "other"})
        with patch.object(self.grounding, "_interpret", new=AsyncMock(return_value=self.context()["work_items"])):
            context = await self.grounding.context(self.scope, self.messages, None, "")
        self.assertEqual([t["key"] for t in context["execution_tasks"]], ["task"])
        ref = context["work_items"][0]["request_ref"]
        proposal = plan("task_status", ref, needs_execution=True, execution_task_keys=["task"])
        approval, _ = self.grounding.approve(proposal, context, "任务已排好")
        self.assertIsNotNone(approval)
        self.assertTrue(await self.grounding.current(approval, self.scope, "任务已排好"))
        self.assertIsNone(self.grounding.approve(proposal, context, "任务已排好", executing_task_key="task")[0])
        context["execution_tasks"][0]["source_message_id"] = "unrelated"
        self.assertIsNone(self.grounding.approve(proposal, context, "任务已排好")[0])
        await self.archive.complete_durable_task_by_key("task", {"outcome": "cancelled"})
        self.assertFalse(await self.grounding.current(approval, self.scope, "任务已排好"))

    def test_completed_task_claim_requires_actual_success(self):
        context = self.context()
        context["execution_tasks"] = [{"key": "t", "status": "completed", "outcome": "partial"}]
        proposal = plan("task_status", claims_completed_task_keys=["t"])
        self.assertIsNone(self.grounding.approve(proposal, context, "已经发好了")[0])
        context["execution_tasks"][0]["outcome"] = "sent"
        self.assertIsNotNone(self.grounding.approve(proposal, context, "已经发好了")[0])

    async def test_approval_is_bound_to_exact_text_scope_and_latest_messages(self):
        approval, _ = self.grounding.approve(plan(), self.context(), "自然分享")
        self.assertTrue(await self.grounding.current(approval, self.scope, "自然分享"))
        self.assertFalse(await self.grounding.current(approval, self.scope, "改过的正文"))
        self.assertFalse(await self.grounding.current(approval, "other", "自然分享"))
        self.messages.append({"role": "user", "content": "先别说了"})
        self.assertFalse(await self.grounding.current(approval, self.scope, "自然分享"))

    async def test_delivery_reservation_persists_and_blocks_concurrent_repeats(self):
        context = self.context()
        approval, _ = self.grounding.approve(plan("deliver", context["work_items"][0]["request_ref"]), context, "完整结果")
        self.assertEqual(sorted(await asyncio.gather(self.grounding.begin_delivery(approval), self.grounding.begin_delivery(approval))), [False, True])
        for status in ("confirmed", "partial", "unknown"):
            await self.grounding.record_delivery(approval, status)
            self.assertFalse(await ProactiveGrounding(self.runtime).begin_delivery(approval))

    async def test_model_failure_is_not_persisted_and_cancellation_propagates(self):
        for error in (ValueError("invalid json"), asyncio.CancelledError()):
            with patch.object(self.grounding, "_interpret", new=AsyncMock(side_effect=error)):
                with self.assertRaises(type(error)):
                    await self.grounding.context(self.scope, self.messages, None, "")
        self.assertEqual(await self.archive.get_decision_traces(scope=self.scope), [])

    async def test_real_continuity_contract_creates_and_rechecks_approval(self):
        payload = {"reply_text": "自然分享"}
        with patch("core.runtime.proactive.grounding.call_pure_json", new=AsyncMock(return_value={"work_items": []})), patch("core.runtime.proactive.procontext.call_pure_json", new=AsyncMock(return_value=plan())):
            ok, _ = await self.runtime._audit_proactive_continuity(payload=payload, recent_context="最近对话", provider=self.provider, provider_id="test", target_scope=self.scope, recent_messages=self.messages)
        self.assertTrue(ok)
        approval, _ = await self.grounding.prepare_send(self.scope, "自然分享", payload)
        self.assertIsNotNone(approval)
        self.assertNotIn("_delivery_approval", payload)

    async def test_cleanup_failure_does_not_discard_successful_semantic_check(self):
        self.runtime.close_text_session.side_effect = OSError("cleanup offline")
        payload = {"reply_text":"自然分享"}
        with patch("core.runtime.proactive.grounding.call_pure_json",new=AsyncMock(return_value={"work_items":[]})), patch("core.runtime.proactive.procontext.call_pure_json",new=AsyncMock(return_value=plan())):
            ok,_=await self.runtime._audit_proactive_continuity(payload=payload,recent_context="最近对话",provider=self.provider,provider_id="test",target_scope=self.scope,recent_messages=self.messages)
        self.assertTrue(ok)

    async def test_unapproved_entrypoint_uses_current_life_facts(self):
        self.runtime._part_proactive_revisit_component._private_revisit_life_context = AsyncMock(return_value=("现在在客厅", True))
        payload = {"reply_text": "自然分享"}
        with patch("core.runtime.proactive.grounding.call_pure_json", new=AsyncMock(return_value={"work_items": []})), patch("core.runtime.proactive.procontext.call_pure_json", new=AsyncMock(return_value=plan())) as audit:
            approval, _ = await self.grounding.prepare_send(self.scope, "自然分享", payload)
        self.assertIsNotNone(approval)
        self.assertIn("现在在客厅", str(audit.await_args))

    def sending(self, text="标题\n\n    保留缩进\n第二段"):
        context = self.context()
        approval, _ = self.grounding.approve(plan("deliver", context["work_items"][0]["request_ref"]), context, text)
        host = SimpleNamespace(
            proactive_grounding=self.grounding,
            _snapshot_proactive_send_event=lambda *a, **k: SimpleNamespace(),
            _proactive_send_is_current=lambda *a: True, can_send_for_source=lambda *a, **k: True,
            _apply_proactive_send_timing=AsyncMock(), send_background_text=AsyncMock(return_value=True),
            note_structured_bot_message=lambda *a: None, _append_proactive_send_history=AsyncMock(),
            archive=self.archive,
        )
        payload = {"_delivery_approval": approval, "source": "proactive_commitment"}
        component = ProactiveSendComponent(host)
        component._send_proactive_voice_if_enabled = AsyncMock(side_effect=AssertionError("complete work must stay text"))
        return component, host, payload, text

    async def test_complete_work_preserves_format_and_uses_actual_delivery_receipt(self):
        component, host, payload, text = self.sending()
        self.assertTrue(await component._send_proactive_message(self.scope, text, "test", send_payload=payload))
        self.assertEqual(host.send_background_text.await_args.args[1], text)
        self.assertEqual(payload["_delivery"]["status"], "confirmed")
        self.assertEqual(self.runtime._proactive_reply_text(text), text)
        host._append_proactive_send_history.assert_awaited_once_with(self.scope, text)

    async def test_unknown_transport_result_is_not_retried(self):
        component, host, payload, text = self.sending()
        host.send_background_text.side_effect = TimeoutError()
        approval = payload["_delivery_approval"]
        self.assertFalse(await component._send_proactive_message(self.scope, text, "test", send_payload=payload))
        self.assertEqual(payload["_delivery"]["status"], "unknown")
        self.assertFalse(await self.grounding.begin_delivery(approval))
        host._append_proactive_send_history.assert_not_awaited()

    async def test_cancel_during_transport_preserves_uncertainty(self):
        component, host, payload, text = self.sending()
        host.send_background_text.side_effect = asyncio.CancelledError()
        approval = payload["_delivery_approval"]
        with self.assertRaises(asyncio.CancelledError):
            await component._send_proactive_message(self.scope, text, "test", send_payload=payload)
        self.assertEqual(payload["_delivery"]["status"], "unknown")
        self.assertFalse(await self.grounding.begin_delivery(approval))

    async def test_cancel_after_positive_receipt_does_not_erase_completion(self):
        component, host, payload, text = self.sending()
        async def sent(*args, on_sent, **kwargs):
            on_sent(text)
            raise asyncio.CancelledError()
        host.send_background_text.side_effect = sent
        with self.assertRaises(asyncio.CancelledError):
            await component._send_proactive_message(self.scope, text, "test", send_payload=payload)
        self.assertEqual(payload["_delivery"]["status"], "confirmed")

    async def test_person_revision_is_audited_before_approval_is_created(self):
        self.messages[:] = [{"role":"user", "content":"今天有些累"}, {"role":"assistant", "content":"先坐会儿吧"}]
        revised = "泡好茶了，一起歇会儿"
        self.runtime.composer._build_person_fact_context = AsyncMock(return_value={})
        async def person(payload, **kwargs):
            json.dumps(payload)  # 不允许把 Python 凭据混进人物事实审计。
            return SimpleNamespace(payload={**payload, "reply_text":revised}, unresolved=False)
        self.runtime.composer._audit_person_payload = person
        proposal={"should_reply":True,"decision":"reply","confidence":0.95,"reply_text":"初始候选",
                  "expression_review":{"passed":True},"benefit":90,"timeliness":90,"continuity":90,"disruption":0,"uncertainty":0}
        with patch("core.runtime.proactive.grounding.call_pure_json",new=AsyncMock(return_value={"work_items":[]})), patch("core.runtime.proactive.revisit.call_pure_json",new=AsyncMock(return_value=proposal)), patch("core.runtime.proactive.procontext.call_pure_json",new=AsyncMock(return_value=plan())) as audit:
            result=await self.runtime._part_proactive_revisit_component._evaluate_private_revisit_payload(
                self.scope,relationship=None,now=support.datetime.datetime(2026,11,2,12))
        self.assertTrue(result["should_reply"], result)
        self.assertIn(revised, str(audit.await_args))
        self.assertTrue(await self.grounding.current(result["_delivery_approval"],self.scope,revised))

    async def test_audit_failure_never_claims_transport_attempt(self):
        component, host, payload, text = self.sending()
        with patch.object(self.grounding, "prepare_send", new=AsyncMock(side_effect=TimeoutError())):
            self.assertFalse(await component._send_proactive_message(self.scope, text, "test", send_payload=payload))
        self.assertEqual(payload["_delivery"]["status"], "deferred")
        host.send_background_text.assert_not_awaited()

    async def test_new_message_during_pacing_cancels_approved_send(self):
        component, host, payload, text = self.sending()
        async def changed(*args):
            self.messages.append({"role": "user", "content": "不用了"})
        host._apply_proactive_send_timing.side_effect = changed
        self.assertFalse(await component._send_proactive_message(self.scope, text, "test", send_payload=payload))
        self.assertEqual(payload["_delivery"]["status"], "interrupted")
        host.send_background_text.assert_not_awaited()

    def test_checkpoint_and_other_nonconversation_roles_do_not_pollute_context(self):
        reader = SavedHistoryReader(self.runtime.context, "test")
        for role in ("_checkpoint", "system", "developer", "tool", "function", "unknown"):
            self.assertIsNone(reader._normalize_conversation_item({"role": role, "content": "内部状态"}))
        self.assertIsNotNone(reader._normalize_conversation_item({"role": "assistant", "content": "真实对话"}))

    async def test_identical_request_in_later_turn_has_distinct_delivery_identity(self):
        manager = self.runtime.context.conversation_manager
        manager.current_ids[self.scope] = "same-conversation"
        history = [{"role":"user","content":"请整理一份今天的摘要"}]
        manager.conversations[self.scope] = SimpleNamespace(history=history)
        reader = SavedHistoryReader(self.runtime.context,"test")
        first = await reader.fetch(self.scope,max_count=1,hours=12,prefer_conversation=True)
        history += [{"role":"assistant","content":"第一天的结果"}, {"role":"user","content":"请整理一份今天的摘要"}]
        last = await reader.fetch(self.scope,max_count=1,hours=12,prefer_conversation=True)
        ref1=conversation_snapshot(self.scope,first)["messages"][0]["ref"]
        ref2=conversation_snapshot(self.scope,last)["messages"][0]["ref"]
        self.assertNotEqual(ref1,ref2)
        wider = await reader.fetch(self.scope,max_count=12,hours=12,prefer_conversation=True)
        self.assertEqual(ref2,conversation_snapshot(self.scope,wider)["messages"][-1]["ref"])

    async def test_conversation_reset_does_not_reuse_old_request_identity(self):
        manager=self.runtime.context.conversation_manager
        manager.conversations[self.scope]=SimpleNamespace(history=[{"role":"user","content":"重新整理"}])
        reader=SavedHistoryReader(self.runtime.context,"test")
        manager.current_ids[self.scope]="before-reset"
        before=await reader.fetch(self.scope,max_count=1,hours=12,prefer_conversation=True)
        manager.current_ids[self.scope]="after-reset"
        after=await reader.fetch(self.scope,max_count=1,hours=12,prefer_conversation=True)
        self.assertNotEqual(conversation_snapshot(self.scope,before)["revision"],conversation_snapshot(self.scope,after)["revision"])
