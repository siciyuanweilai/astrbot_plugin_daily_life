"""邀约答复、真实排程、条件执行与重放的离线回归。"""
import asyncio
import copy
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from support import LifeArchive, LifeSettings, DailyLifeCommandCenter, make_composer
from core.archive import DayRevisionConflict
from core.life.actionchain import actions, ready
from core.life.itinerary import compile_edited_actions, apply_edited_timeline
from core.models import DayRecord, TimelineItem, CommitmentRecord
from core.runtime.actionreply import ActionReplyService, RECEIPTS_ATTR, action_receipt_fallback
from core.runtime.spine.rsvp import SpineInviteComponent
from core.runtime.proactive.rendezvous import SharedActivityContactComponent
from core.runtime.decisioncontext import decision_observation, decision_observation_changed
from core.runtime.refresh import RefreshComponent


class InviteContinuityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'life.db'
        self.archive = LifeArchive(self.path)
        self.now = datetime.datetime(2026, 11, 1, 16, 45)
        for target in ('core.runtime.spine.rsvp.life_now', 'core.archive.schedulelinks.life_now'):
            clock = patch(target, lambda: self.now)
            clock.start()
            self.addCleanup(clock.stop)
        self.day = await self.archive.save_day(DayRecord(date='2026-11-01', timeline=[
            TimelineItem(time='16:00', activity='整理笔记', execution_state='active'),
        ], meta={'planned_life_actions': json.dumps([{
            'action_id': 'notes:original', 'timeline_index': 0, 'action_type': 'study',
            'target': '整理笔记', 'duration_minutes': 30,
        }])}))
        self.event = SimpleNamespace(message_str='忙完一起去走走吗', unified_msg_origin='test:FriendMessage:1',
                                     message_id='invitation-1', _daily_life_expression_context={'conversation': '一起散步'})
        self.config = LifeSettings.from_dict({})
        self.config.state.enabled = False
        self.host = SimpleNamespace(archive=self.archive, config=self.config,
            contact_resolver=SimpleNamespace(resolve_event_sender=AsyncMock(return_value='朋友')),
            _event_context_meta=AsyncMock(side_effect=lambda e, *args: {'session_id':e.unified_msg_origin, 'message_id':e.message_id}),
            remember_interaction=AsyncMock(), schedule_invite_outfit_sync=Mock(), schedule_memos_selected_items=Mock(),
            event_was_recalled=lambda e: False, continuous_turn_event_is_current=lambda e: True,
            composer=SimpleNamespace(handle_invite=AsyncMock(), learn_preferences_from_payload=AsyncMock(),
                                     persist_life_events_from_payload=AsyncMock()),
            _runtime_now=lambda: self.now, resolve_injection_target=AsyncMock(return_value=(self.day.date, False)),
            get_text_provider=AsyncMock(return_value=object()), call_text_model=AsyncMock(), close_text_session=AsyncMock())
        self.service = SpineInviteComponent(self.host)
        self.host.accept_user_invite = self.service.accept_user_invite
        self.host.schedule_invite_contact = SharedActivityContactComponent(self.host).schedule_invite_contact
        self.timeline = [copy.deepcopy(self.day.timeline[0]),
                         TimelineItem(time='16:50', activity='收拾随身东西'),
                         TimelineItem(time='17:00', activity='和朋友散步')]
        self.decision = {'decision':'accept', 'wait_for_action_ids':['notes:original'],
            'timeline_edits':[{'operation':'insert','item':item.as_dict()} for item in self.timeline[1:]],
            'planned_actions':[
                {'edit_index':0,'action_type':'chore','target':'收拾随身东西','duration_minutes':5},
                {'edit_index':1,'action_type':'social','target':'和朋友散步','duration_minutes':20},
            ]}
        issue = await compile_edited_actions(self.host, self.decision, self.timeline, date_str=self.day.date, source='invite')
        self.assertEqual(issue, '')
        self.host.composer.handle_invite.return_value = ('想一起放松', self.timeline, self.decision)

    async def asyncTearDown(self):
        await self.archive.aclose()
        self.directory.cleanup()

    async def accept(self, event=None):
        return await self.service.accept_user_invite(event or self.event, '整理完笔记后和朋友散步')

    async def test_reply_persists_invitation_before_confirming_schedule(self):
        self.host.call_text_model.side_effect = [json.dumps({'decision':'act','action':'respond_invite',
            'detail':'整理完笔记后和朋友散步'}), json.dumps({'decision':'revise','text':'好，等笔记收尾。收拾一下，预计五点出发。'})]
        command = DailyLifeCommandCenter(self.host)
        command.permission_denial = lambda *args, **kwargs: ''
        response = SimpleNamespace(completion_text='好，我这就出门，日程记着呢。')
        await ActionReplyService(self.host).reconcile(self.event, response, command.adjust_life)
        records = await self.archive.get_commitments(status='scheduled')
        self.assertEqual(len(records), 1)
        receipt = getattr(self.event, RECEIPTS_ATTR)[0]
        self.assertTrue(receipt['facts']['schedule_saved'])
        self.assertTrue(receipt['facts']['waiting'])
        self.assertEqual(receipt['facts']['schedule'][-1]['time'], '17:00')
        self.assertIn('五点', response.completion_text)
        self.host.schedule_invite_outfit_sync.assert_not_called()
        self.assertEqual((await self.archive.get_living_entities(self.event.unified_msg_origin, kind='shared_activity'))[0]['state'], 'planned')
        self.assertIsNotNone(await self.archive.get_durable_task(f'invite_contact:{records[0].id}'))

    async def test_same_message_concurrent_calls_and_restart_reuse_saved_arrangement(self):
        replies = await asyncio.gather(self.accept(), self.accept(copy.copy(self.event)))
        self.assertEqual([json.loads(item)['commitment_id'] for item in replies], [1,1])
        self.host.composer.handle_invite.assert_awaited_once()
        before = (await self.archive.get_day(self.day.date)).meta['planned_life_actions']
        await self.archive.aclose()
        self.archive = LifeArchive(self.path)
        self.host.archive = self.archive
        replay = await self.accept(copy.copy(self.event))
        self.assertTrue(json.loads(replay)['schedule_saved'])
        self.host.composer.handle_invite.assert_awaited_once()
        self.assertEqual(before, (await self.archive.get_day(self.day.date)).meta['planned_life_actions'])

    async def test_failed_schedule_save_is_recorded_but_never_marked_scheduled(self):
        with patch.object(self.service, '_save_invite_day', AsyncMock(side_effect=DayRevisionConflict('timeline changed'))):
            with self.assertRaises(DayRevisionConflict):
                await self.accept()
        record = (await self.archive.get_commitments())[0]
        self.assertEqual(record.status, 'active')
        self.assertEqual(len((await self.archive.get_day(self.day.date)).timeline), 1)
        replay = json.loads(await self.accept())
        self.assertFalse(replay['schedule_saved'])
        self.assertIsNone(await self.archive.get_durable_task(f'invite_contact:{record.id}'))

    async def test_cancelled_invite_cannot_be_reactivated_by_message_replay(self):
        await self.accept()
        record = await self.archive.get_commitment(1)
        record.status = 'cancelled'
        await self.archive.save_commitment(record)
        replay = json.loads(await self.accept())
        self.assertEqual(replay['decision'], 'cancelled')
        self.assertFalse(replay['schedule_saved'])
        self.host.composer.handle_invite.assert_awaited_once()

    async def test_new_message_during_invite_review_does_not_commit_old_plan(self):
        async def choose(*args, **kwargs):
            self.host.continuous_turn_event_is_current = lambda e: False
            return '想去', self.timeline, self.decision
        self.host.composer.handle_invite.side_effect = choose
        result = json.loads(await self.accept())
        self.assertEqual(result['decision'], 'undetermined')
        self.assertEqual(await self.archive.get_commitments(status=''), [])
        self.assertEqual(len((await self.archive.get_day(self.day.date)).timeline), 1)

    async def test_scope_and_message_jointly_identify_invitation(self):
        await self.accept()
        other = copy.copy(self.event)
        other.unified_msg_origin = 'test:FriendMessage:2'
        # 不实际重复排程，只验证其他会话不能读取同编号消息的回执。
        self.host.composer.handle_invite.return_value = ('今天不去了', None, {'decision':'reject'})
        result = json.loads(await self.accept(other))
        self.assertEqual(result['decision'], 'reject')
        self.assertEqual(self.host.composer.handle_invite.await_count, 2)

    async def test_permission_denial_blocks_reply_fallback_invite(self):
        command = DailyLifeCommandCenter(self.host)
        command.permission_denial = Mock(return_value='此会话无权限')
        result = await command.adjust_life(self.event, 'respond_invite', detail='一起散步')
        self.assertEqual(result, '此会话无权限')
        self.assertEqual(command.permission_denial.call_args.args[1], 'invite:respond')
        self.host.composer.handle_invite.assert_not_awaited()

    async def test_dependency_and_step_order_survive_restart_and_cannot_settle_early(self):
        await self.accept()
        day = await self.archive.get_day(self.day.date)
        first, last = actions(day)[1:]
        composer, _, _, _ = make_composer([])
        self.assertFalse(ready(day, first))
        self.assertEqual(composer.settle_life_action(day, first, now=self.now).status, 'rejected')
        for status in ('cancelled', 'failed', 'pending'):
            day.meta['life_action_settlements'] = json.dumps({'notes:original':{'status':status}})
            self.assertFalse(ready(day, first))
        day.meta['life_action_settlements'] = json.dumps({'notes:original':{'status':'committed'}})
        self.assertTrue(ready(day, first))
        self.assertFalse(ready(day, last))
        day.meta['life_action_settlements'] = json.dumps({key:{'status':'committed'} for key in ('notes:original',first.action_id)})
        await self.archive.save_day(day)
        await self.archive.aclose()
        self.archive = LifeArchive(self.path)
        self.host.archive = self.archive
        self.assertTrue(ready(await self.archive.get_day(self.day.date), last))

    async def test_invalid_dependency_does_not_create_action_plan(self):
        for ids in (['invented'], 'notes:original', [None]):
            result = copy.deepcopy(self.decision)
            result.pop('_planned_life_actions', None)
            result['wait_for_action_ids'] = ids
            self.assertTrue(await compile_edited_actions(self.host, result, self.timeline, date_str=self.day.date, source='invite'))
            self.assertNotIn('_planned_life_actions', result)

    async def test_action_receipt_wakes_decision_even_without_timeline_change(self):
        before = decision_observation(self.day, {})
        self.day.meta['life_action_settlements'] = json.dumps({'notes:original':{'status':'committed'}})
        after = decision_observation(self.day, {})
        self.assertTrue(decision_observation_changed(before, after))
        self.day.meta['life_action_settlements'] = json.dumps({'notes:original':{'status':'committed','replayed':True}})
        self.assertFalse(decision_observation_changed(after, decision_observation(self.day, {})))

    async def test_state_refresh_cannot_replace_unsaved_invite_timeline(self):
        self.config.state.enabled = True
        async def refresh(date, *args, **kwargs):
            return await self.archive.get_day(date)
        self.host.refresh_state_for_day = AsyncMock(side_effect=refresh)
        result = json.loads(await self.accept())
        self.assertTrue(result['schedule_saved'])
        self.assertEqual(len((await self.archive.get_day(self.day.date)).timeline), 3)
        self.host.refresh_state_for_day.assert_awaited_once()
        self.assertIs(self.host.refresh_state_for_day.call_args.kwargs['source_event'], self.event)

    async def test_legacy_pending_outfit_does_not_bypass_scheduled_dependency(self):
        await self.accept()
        day = await self.archive.get_day(self.day.date)
        day.meta['pending_commitment_outfit'] = json.dumps({
            'commitment_id':1,'instruction':'为出门换衣','date':day.date,'effective_time':'16:40',
        })
        self.assertEqual(RefreshComponent._pending_commitment_outfit(day, self.now), ('', None))

    async def test_reply_facts_are_scoped_and_fallback_does_not_expose_json(self):
        receipt = await self.accept()
        await self.archive.save_commitment(CommitmentRecord(content='另一个人的私人安排', source_session='test:FriendMessage:2'))
        facts = await ActionReplyService(self.host)._facts(self.event)
        self.assertEqual(len(facts['commitments']), 1)
        self.assertEqual(facts['timeline'][-1]['time'], '17:00')
        self.assertNotIn('另一个人', json.dumps(facts, ensure_ascii=False))
        setattr(self.event, RECEIPTS_ATTR, [{'result':str(receipt), 'facts':receipt.facts}])
        self.assertNotIn('{', action_receipt_fallback(self.event))
        self.assertIn('进度', action_receipt_fallback(self.event))
