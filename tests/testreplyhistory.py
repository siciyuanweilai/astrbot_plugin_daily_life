import asyncio
import copy
import json
import types
import unittest
from unittest.mock import AsyncMock

from support import Event, ProviderRequest
from core.runtime.preparation import ReplyPreparationService
from core.runtime.replyhistory import capture_reply_history, suppress_reply_draft
import testcontinuousturn as turn_fixtures


class ReplyContinuityTest(unittest.IsolatedAsyncioTestCase):
    def test_sdk_message_object_id_survives_background_snapshot(self):
        from core.runtime.turns import ContinuousTurnComponent
        event = types.SimpleNamespace(message_obj=types.SimpleNamespace(message_id="platform-id"))
        self.assertEqual(ContinuousTurnComponent._continuous_turn_message_id(event), "platform-id")
        self.assertEqual(ContinuousTurnComponent._continuous_turn_message_id(copy.copy(event)), "platform-id")

    async def start(self, text="第一句", mid="first"):
        runtime = turn_fixtures.ContinuousTurnTest._runtime(continuous_turn_wait_seconds=0)
        event = turn_fixtures.ContinuousTurnTest._event(text, mid)
        runtime.note_continuous_turn_incoming(event)
        await runtime.settle_continuous_turn(event)
        request = ProviderRequest(prompt=text)
        runtime.prepare_continuous_turn_llm_request(event, request)
        return runtime, event, request

    async def test_three_messages_during_generation_keep_all_unanswered_input(self):
        runtime, first, request = await self.start("啥时候嫌弃你了")
        second = turn_fixtures.ContinuousTurnTest._event("早嫌弃你也不是这个时候", "second")
        third = turn_fixtures.ContinuousTurnTest._event("好好看", "third")
        for event in (second, third):
            runtime.note_continuous_turn_incoming(event)
            await runtime.settle_continuous_turn(event)
        self.assertFalse(runtime.continuous_turn_reply_is_current(first))
        runtime.note_continuous_turn_response(first)
        self.assertFalse(runtime.prepare_continuous_turn_llm_request(second, ProviderRequest()))
        # 原轮只留下真实用户输入，无未发送 assistant；后两句合并为一次请求。
        contexts = [{"role": "user", "content": request.prompt},
                    {"role": "_checkpoint", "content": {"id": first.get_extra("llm_checkpoint_id")}}]
        latest = ProviderRequest(contexts=contexts)
        self.assertTrue(runtime.prepare_continuous_turn_llm_request(third, latest))
        self.assertEqual(latest.prompt, "早嫌弃你也不是这个时候\n好好看")
        self.assertEqual(latest.contexts, contexts)
        self.assertTrue(runtime.continuous_turn_reply_is_current(third))

    async def test_missing_previous_history_keeps_original_input_in_merged_prompt(self):
        runtime, first, _ = await self.start()
        last = turn_fixtures.ContinuousTurnTest._event("补充", "last")
        runtime.note_continuous_turn_incoming(last)
        await runtime.settle_continuous_turn(last)
        runtime.note_continuous_turn_response(first)
        request = ProviderRequest()
        runtime.prepare_continuous_turn_llm_request(last, request)
        self.assertEqual(request.prompt, "第一句\n补充")

    async def test_single_message_preserves_framework_prepared_prompt(self):
        runtime, event, _ = await self.start()
        request = ProviderRequest(prompt="带有已解析媒体资料的原始请求")
        self.assertTrue(runtime.prepare_continuous_turn_llm_request(event, request))
        self.assertEqual(request.prompt, "带有已解析媒体资料的原始请求")

    async def test_identical_old_text_is_not_a_delivery_or_input_receipt(self):
        runtime, first, _ = await self.start("好")
        last = turn_fixtures.ContinuousTurnTest._event("补充", "last")
        runtime.note_continuous_turn_incoming(last)
        await runtime.settle_continuous_turn(last)
        runtime.note_continuous_turn_response(first)
        request = ProviderRequest(contexts=[{"role": "user", "content": "好"},
                                            {"role": "assistant", "content": "以前的对话"}])
        runtime.prepare_continuous_turn_llm_request(last, request)
        self.assertEqual(request.prompt, "好\n补充")

    async def test_quote_outside_batch_still_supersedes_unsent_reply(self):
        runtime, first, _ = await self.start()
        last = turn_fixtures.ContinuousTurnTest._event("引用另一件事", "last")
        last.message_items.append({"type": "reply"})
        self.assertFalse(runtime.note_continuous_turn_incoming(last))
        self.assertFalse(runtime.continuous_turn_reply_is_current(first))

    async def test_consumed_native_follow_up_can_send_without_replaying_tool(self):
        runtime, first, _ = await self.start()
        last = turn_fixtures.ContinuousTurnTest._event("补充", "last")
        runtime.note_continuous_turn_incoming(last)
        await runtime.settle_continuous_turn(last)
        self.assertFalse(runtime.continuous_turn_reply_is_current(first))
        last.set_extra("_follow_up_captured", {"target_run_id": "first"})
        self.assertTrue(runtime.continuous_turn_reply_is_current(first))
        runtime.note_continuous_turn_response(first)
        self.assertTrue(runtime.continuous_turn_reply_is_current(first))

    async def test_late_follow_up_after_consumed_one_keeps_tool_history(self):
        runtime, first, initial = await self.start()
        second = turn_fixtures.ContinuousTurnTest._event("工具期间的补充", "second")
        runtime.note_continuous_turn_incoming(second)
        second.set_extra("_follow_up_captured", {"target_run_id": "first"})
        third = turn_fixtures.ContinuousTurnTest._event("最后补充", "third")
        runtime.note_continuous_turn_incoming(third)
        await runtime.settle_continuous_turn(third)
        self.assertFalse(runtime.continuous_turn_reply_is_current(first))
        runtime.note_continuous_turn_response(first)
        contexts = [{"role": "user", "content": [{"type": "text", "text": initial.prompt}]},
                    {"role": "assistant", "tool_calls": [{"id": "done"}]},
                    {"role": "tool", "tool_call_id": "done", "content": "真实执行回执以及续话"},
                    {"role": "_checkpoint", "content": {"id": first.get_extra("llm_checkpoint_id")}}]
        latest = ProviderRequest(contexts=contexts)
        runtime.prepare_continuous_turn_llm_request(third, latest)
        self.assertEqual(latest.prompt, "最后补充")
        self.assertEqual(latest.contexts, contexts)

    async def test_incoming_during_preparation_keeps_unanswered_batch(self):
        runtime, first, _ = await self.start()
        runtime.note_continuous_turn_response(first)
        later = turn_fixtures.ContinuousTurnTest._event("继续说", "later")
        runtime.note_continuous_turn_incoming(later)
        self.assertFalse(runtime.continuous_turn_reply_is_current(first))
        self.assertFalse(runtime.complete_continuous_turn(first))
        await runtime.settle_continuous_turn(later)
        latest = ProviderRequest()
        runtime.prepare_continuous_turn_llm_request(later, latest)
        self.assertEqual(latest.prompt, "第一句\n继续说")


class ReplyHistoryTest(unittest.IsolatedAsyncioTestCase):
    def event(self):
        event = Event()
        event._daily_life_turn_request = ProviderRequest(conversation=types.SimpleNamespace(cid="original"))
        message = types.SimpleNamespace(role="assistant", content="未发送的草稿", _no_save=False)
        capture_reply_history(event, types.SimpleNamespace(messages=[message]))
        event.set_result(event.chain_result([types.SimpleNamespace(text="未发送的草稿")]))
        return event, message

    def manager(self):
        return types.SimpleNamespace(
            get_conversation=AsyncMock(return_value=types.SimpleNamespace(history='[{"role":"user","content":"原话"}]')),
            update_conversation=AsyncMock())

    async def run_reply(self, event, prepare, *, current=lambda e: True, lock=None):
        self.manager_instance = self.manager()
        self.delivered = AsyncMock()
        self.completed = []
        jobs = []
        def schedule(job, **kwargs):
            jobs.append(asyncio.create_task(job))
            return True
        async def commit(snapshot):
            await snapshot._daily_life_reply_history.commit(self.manager_instance, "scope")
        options = {"transaction": lambda: lock} if lock is not None else {}
        service = ReplyPreparationService(schedule=schedule, prepare=prepare, delivered=self.delivered,
            complete=self.completed.append, is_current=current, commit=commit, **options)
        self.assertTrue(service.defer(event))
        return jobs

    async def test_cancelled_draft_never_enters_history_or_completes(self):
        event, message = self.event()
        jobs = await self.run_reply(event, AsyncMock(), current=lambda e: False)
        await asyncio.gather(*jobs)
        self.assertTrue(message._no_save)
        self.assertFalse(event.sent_messages)
        self.manager_instance.update_conversation.assert_not_awaited()
        self.assertFalse(self.completed)

    async def test_partial_delivery_commits_only_sent_text_even_on_exception(self):
        event, message = self.event()
        async def prepare(snapshot):
            await snapshot.send(snapshot.chain_result([types.SimpleNamespace(text="已发出的第一段")]))
            raise RuntimeError("第二段发送失败")
        jobs = await self.run_reply(event, prepare)
        await asyncio.gather(*jobs)
        history = self.manager_instance.update_conversation.call_args.kwargs["history"]
        self.assertEqual(history[-1], {"role": "assistant", "content": "已发出的第一段"})
        self.assertTrue(message._no_save)
        self.delivered.assert_awaited_once()

    async def test_segment_delivery_clearing_result_still_gets_receipt(self):
        event, _ = self.event()
        async def prepare(snapshot):
            for text in ("第一段", "第二段"):
                await snapshot.send(snapshot.chain_result([types.SimpleNamespace(text=text)]))
            snapshot.clear_result()
        jobs = await self.run_reply(event, prepare)
        await asyncio.gather(*jobs)
        history = self.manager_instance.update_conversation.call_args.kwargs["history"]
        self.assertEqual(history[-1]["content"], "第一段\n第二段")
        self.delivered.assert_awaited_once()
        self.assertEqual(len(self.completed), 1)

    async def test_voice_delivery_records_spoken_text_and_original_branch_once(self):
        event, _ = self.event()
        async def prepare(snapshot):
            snapshot._daily_life_pending_voice_receipt = {"text": "实际说出的语音"}
            snapshot.set_result(snapshot.chain_result([types.SimpleNamespace(type="record")]))
        jobs = await self.run_reply(event, prepare)
        await asyncio.gather(*jobs)
        receipt = event._daily_life_reply_history
        await receipt.commit(self.manager_instance, "scope")
        self.manager_instance.update_conversation.assert_awaited_once()
        call = self.manager_instance.update_conversation.call_args
        self.assertEqual(call.args, ("scope", "original"))
        self.assertEqual(call.kwargs["history"][-1]["content"], "实际说出的语音")

    async def test_background_waits_for_core_history_and_rechecks_new_messages(self):
        lock = asyncio.Lock()
        await lock.acquire()
        event, message = self.event()
        state = {"current": True}
        prepare = AsyncMock()
        jobs = await self.run_reply(event, prepare, lock=lock, current=lambda e: state["current"])
        await asyncio.sleep(0)
        self.assertTrue(message._no_save)
        prepare.assert_not_awaited()
        state["current"] = False
        lock.release()
        await asyncio.gather(*jobs)
        self.assertFalse(event.sent_messages)
        self.manager_instance.update_conversation.assert_not_awaited()

    async def test_queue_rejection_keeps_framework_history_and_original_result(self):
        event, message = self.event()
        def reject(job, **kwargs):
            job.close()
            return False
        service = ReplyPreparationService(schedule=reject, prepare=AsyncMock(), delivered=AsyncMock(),
            complete=lambda _: None, is_current=lambda _: True)
        self.assertFalse(service.defer(event))
        self.assertFalse(message._no_save)
        self.assertIsNotNone(event.get_result())

    async def test_delivery_is_inserted_before_original_checkpoint(self):
        event, _ = self.event()
        receipt = event._daily_life_reply_history
        receipt.checkpoint_id = "turn"
        receipt.texts = ["实际送达"]
        manager = self.manager()
        original = [{"role": "user", "content": "原话"},
                    {"role": "_checkpoint", "content": {"id": "turn"}},
                    {"role": "user", "content": "后续消息"}]
        manager.get_conversation.return_value.history = json.dumps(original)
        await receipt.commit(manager, "scope")
        history = manager.update_conversation.call_args.kwargs["history"]
        self.assertEqual(history[1], {"role": "assistant", "content": "实际送达"})
        self.assertEqual(history[2:], original[1:])

    async def test_suppressing_draft_preserves_previous_messages_and_tool_calls(self):
        event, message = self.event()
        suppress_reply_draft(event)
        self.assertTrue(message._no_save)
        prior = types.SimpleNamespace(role="assistant", tool_calls=[{"id": "tool"}], _no_save=False)
        other = Event()
        capture_reply_history(other, types.SimpleNamespace(messages=[prior]))
        suppress_reply_draft(other)
        self.assertFalse(prior._no_save)
