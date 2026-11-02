"""生活动作从开始到结算、重放、回程及历史纠正的回归。"""
import copy
import datetime
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import AsyncMock

from support import TimelineItem
import testcontinuouslife as fixture
from core.models import LifeActionIntent
from core.life.locationfacts import action_chain_place_issue
from core.life.executiontime import timeline_execution_times
from core.archive.actionrepair import repair_committed_actions
from core.life.chatview import chat_life_projection
from core.life.actionchain import execution_evidence


class ActionReplayTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ContinuousLifeTest.asyncSetUp
    asyncTearDown = fixture.ContinuousLifeTest.asyncTearDown
    start = fixture.ContinuousLifeTest.start
    tick = fixture.ContinuousLifeTest.tick

    async def completed_move(self):
        day = await self.archive.get_day(self.runtime.date)
        day.meta['current_place'] = '住处'
        day.timeline = [TimelineItem(time='01:00', activity='出门走走', place='街区')]
        action = LifeActionIntent.from_value({
            'action_id': 'outbound', 'action_type': 'move', 'target': '出门走走',
            'duration_minutes': 5, 'timeline_index': 0,
            'payload': {'origin': '住处', 'destination': '街区'},
        })
        day.meta['planned_life_actions'] = json.dumps([action.as_dict()])
        await self.archive.save_day(day)
        stale = copy.deepcopy(day)
        first = await self.runtime.composer.settle_and_persist_life_action(
            day, action, now=self.now + datetime.timedelta(minutes=5), receipt_status='simulated')
        self.assertEqual(first.status, 'committed')
        self.assertEqual(day.meta['current_place'], '街区')
        return day, action, stale, first

    async def test_repeated_review_preserves_completion_after_arrival(self):
        day, action, _, first = await self.completed_move()
        before = (await self.archive.get_day(day.date)).state.as_dict()
        for minute in (6, 9):
            results = await self.runtime.composer.settle_completed_planned_actions(
                day, now=self.now + datetime.timedelta(minutes=minute))
            self.assertEqual(results[0].status, 'committed')
            self.assertTrue(results[0].replayed)
            self.assertEqual(results[0].committed_at, first.committed_at)
        saved = await self.archive.get_day(day.date)
        self.assertEqual(saved.timeline[0].execution_state, 'completed')
        self.assertEqual(saved.state.as_dict(), before)
        self.assertEqual(saved.meta['current_place'], '街区')
        self.assertNotIn('schedule_replan_pending', saved.meta)
        self.assertEqual((await self.archive.get_life_action_outcomes())[0].status, 'committed')

    async def test_stale_receipts_cannot_reverse_success(self):
        day, action, stale, first = await self.completed_move()
        for status in ('failed', 'cancelled', 'simulated', 'confirmed'):
            with self.subTest(status=status):
                result = await self.runtime.composer.record_life_action_receipt(
                    copy.deepcopy(stale), action.action_id,
                    {'status': status, 'source': 'late', 'evidence': ['晚到的旧回执']},
                    now=self.now + datetime.timedelta(minutes=8))
                self.assertEqual(result.status, 'committed')
                self.assertEqual(result.committed_at, first.committed_at)
        self.assertEqual((await self.archive.get_day(day.date)).timeline[0].execution_state, 'completed')
        self.assertFalse(any(r.status in {'failed', 'cancelled'} for r in
                             await self.archive.get_life_action_receipts(action_id=action.action_id)))

    async def test_new_action_still_checks_location(self):
        day, action, _, first = await self.completed_move()
        other = copy.deepcopy(action)
        other.action_id, other.timeline_index = 'another-departure', None
        result = await self.runtime.composer.record_life_action_receipt(
            day, other.action_id, {'status': 'simulated'}, planned_action=other)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(day.meta['current_place'], '街区')

    async def test_commit_during_validation_wins_over_late_failure(self):
        day, action, stale, first = await self.completed_move()
        original, calls = self.archive.get_day, 0
        async def snapshot(date):
            nonlocal calls
            calls += 1
            return copy.deepcopy(stale) if calls == 1 else await original(date)
        self.archive.get_day = snapshot
        self.runtime.domains.validate_action = AsyncMock(return_value=(False, '起点已变化'))
        result = await self.runtime.composer.record_life_action_receipt(
            stale, action.action_id, {'status': 'simulated'}, now=self.now)
        self.assertEqual(result.status, 'committed')
        self.assertEqual(result.committed_at, first.committed_at)

    async def test_replay_does_not_require_current_wardrobe_or_retimestamp_facts(self):
        day, action, _, first = await self.completed_move()
        self.archive._prepare_outfit_action_unlocked = lambda *args: (_ for _ in ()).throw(
            AssertionError('已结算动作不能重新准备当前衣橱'))
        self.runtime.composer.sync_day_world_facts = AsyncMock()
        replay = await self.runtime.composer.settle_and_persist_life_action(day, action, now=self.now)
        self.assertTrue(replay.replayed)
        call = self.runtime.composer.sync_day_world_facts.call_args
        self.assertEqual(call.kwargs['observed_at'], first.committed_at)
        self.assertEqual(call.args[0].outfit, '')
        self.assertEqual(call.args[0].meta['current_place'], '街区')

    async def test_replay_does_not_attribute_later_location_to_earlier_action(self):
        day, action, _, _ = await self.completed_move()
        await self.archive.mutate_day(day.date, lambda current: current.meta.update(
            current_place='另一处', place_fact_action_id='later-action', place_fact_at='2026-10-07 02:00:00'))
        self.runtime.composer.sync_day_world_facts = AsyncMock()
        await self.runtime.composer.record_life_action_receipt(day, action.action_id, {'status':'simulated'})
        self.runtime.composer.sync_day_world_facts.assert_not_called()

    async def test_outcome_storage_cannot_downgrade_committed_action(self):
        day, action, _, first = await self.completed_move()
        result = await self.archive.save_life_action_outcome({
            'action_id': action.action_id, 'date': day.date, 'action_type': 'move',
            'status': 'failed', 'reason': '延迟任务的失败结果', 'committed_at': str(self.now),
        })
        self.assertEqual(result.status, 'committed')
        self.assertEqual(result.committed_at, first.committed_at)

    async def test_late_projection_preserves_success_and_removes_false_replan(self):
        day, action, _, first = await self.completed_move()
        day.timeline[0].execution_state = 'skipped'
        day.meta['schedule_replan_pending'] = json.dumps({'action_id': action.action_id})
        await self.archive.save_day(day)
        saved = await self.archive.get_day(day.date)
        self.assertEqual(saved.timeline[0].execution_state, 'completed')
        self.assertEqual(saved.timeline[0].execution_updated_at, first.committed_at)
        self.assertNotIn('schedule_replan_pending', saved.meta)

    async def test_shared_node_waits_for_all_steps(self):
        day, action, _, _ = await self.completed_move()
        other = LifeActionIntent.from_value({'action_id': 'rest-after-arrival', 'action_type': 'rest',
                                            'timeline_index': 0, 'payload': {'place': '街区'}})
        day.meta['planned_life_actions'] = json.dumps([action.as_dict(), other.as_dict()])
        day.timeline[0].execution_state = 'active'
        await self.archive.save_day(day)
        self.assertEqual((await self.archive.get_day(day.date)).timeline[0].execution_state, 'active')

    async def test_roundtrip_requires_return_before_rest_at_home(self):
        day = await self.archive.get_day(self.runtime.date)
        day.meta['current_place'] = '住处'
        steps = [LifeActionIntent.from_value(s) for s in [
            {'action_type': 'move', 'payload': {'origin': '住处', 'destination': '街区'}},
            {'action_type': 'rest', 'payload': {'place': '街区'}},
            {'action_type': 'move', 'payload': {'origin': '街区', 'destination': '住处'}},
            {'action_type': 'rest', 'payload': {'place': '住处'}},
        ]]
        self.assertEqual(action_chain_place_issue(day, steps), '')
        self.assertTrue(action_chain_place_issue(day, steps[:2] + steps[3:]))
        self.assertEqual(day.meta['current_place'], '住处')

    async def test_autonomous_physical_action_requires_explicit_place(self):
        day = await self.archive.get_day(self.runtime.date)
        day.meta['current_place'] = '街区'
        for kind in ('rest', 'drink', 'groom', 'study', 'change_outfit'):
            action = LifeActionIntent.from_value({'action_type': kind, 'target': '任意自然描述'})
            self.assertTrue(action_chain_place_issue(day, [action]), kind)
            action.payload['place'] = '街区'
            self.assertEqual(action_chain_place_issue(day, [action]), '', kind)

    async def test_departure_return_and_review_use_actual_places_and_times(self):
        self.runtime.domains.estimate_route = AsyncMock(return_value={'duration_seconds': 60})
        await self.archive.mutate_day(self.runtime.date, lambda day: day.meta.update(current_place='住处'))
        for origin, destination in [('住处', '街区'), ('街区', '住处')]:
            run = await self.start('move', duration=1, payload={
                'origin': origin, 'destination': destination, 'travel_mode': 'walking'})
            self.assertIsNotNone(run)
            day = await self.archive.get_day(self.runtime.date)
            index = run['action']['timeline_index']
            self.assertEqual(day.timeline[index].place, destination)
            self.assertEqual(day.timeline[index].travel_origin, origin)
            self.assertEqual(day.meta['current_place'], origin)
            await self.tick()
            day = await self.archive.get_day(self.runtime.date)
            await self.runtime.composer.settle_completed_planned_actions(day, now=self.runtime.now)
            self.assertEqual(day.meta['current_place'], destination)
            self.assertTrue(all(item.execution_state == 'completed' for item in day.timeline))
            times = timeline_execution_times(day)[index]
            self.assertTrue(times['actual_started_at'])
            self.assertEqual(times['actual_duration_minutes'], 1)
            self.assertEqual(times['actual_completed_at'], str(self.runtime.now))

    async def test_historical_time_is_not_inferred_from_planned_duration(self):
        day, _, _, first = await self.completed_move()
        times = timeline_execution_times(day)[0]
        self.assertEqual(times['actual_started_at'], '')
        self.assertEqual(times['actual_completed_at'], first.committed_at)
        self.assertIsNone(times['actual_duration_minutes'])

    async def test_arrival_evidence_does_not_claim_unexecuted_return(self):
        day, action, _, _ = await self.completed_move()
        action.target = '下楼散步然后回家躺沙发'
        action.payload['next_step'] = '回家休息'
        day.meta['planned_life_actions'] = json.dumps([action.as_dict()])
        view = chat_life_projection({'history': [{'action': action.as_dict(), 'status': 'completed'}]})
        self.assertIn('从住处到达街区', view)
        self.assertNotIn('回家', view)
        evidence = execution_evidence(day)['recent_completed'][0]
        self.assertEqual(evidence['target'], '从住处到达街区')
        self.assertEqual(evidence['next_intention'], '回家休息')

    async def test_evidence_based_repair_is_idempotent_and_does_not_repeat_effects(self):
        day, action, _, first = await self.completed_move()
        before = (await self.archive.get_day(day.date)).state.as_dict()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE life_action_outcomes SET status='failed',reason='地点复查失败' WHERE action_id=?", (action.action_id,))
            conn.execute("UPDATE timelines SET execution_state='skipped',execution_reason='地点复查失败' WHERE date=?", (day.date,))
            conn.execute("INSERT INTO life_action_receipts(receipt_id,action_id,date,action_type,status,source,evidence_json) VALUES('late',?,?,'move','failed','life_domain_validation',?)",
                         (action.action_id, day.date, json.dumps(['地点复查失败'])))
            report = repair_committed_actions(conn)
            self.assertEqual(len(report['actions']), 1)
            self.assertEqual(repair_committed_actions(conn)['actions'], [])
            self.assertEqual(conn.execute("SELECT status FROM life_action_receipts WHERE receipt_id='late'").fetchone()[0], 'failed')
        saved = await self.archive.get_day(day.date)
        self.assertEqual(saved.state.as_dict(), before)
        self.assertEqual(saved.timeline[0].execution_state, 'completed')
        self.assertEqual(saved.meta['current_place'], '街区')
        self.assertIn(action.action_id, json.loads(saved.meta['action_reconciliation']))
        self.assertEqual((await self.archive.get_life_action_outcomes())[0].committed_at, first.committed_at)

    async def test_repair_does_not_promote_real_failure_without_success(self):
        day, action, _, _ = await self.completed_move()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE day_meta SET value='{}' WHERE date=? AND key='life_action_settlements'", (day.date,))
            conn.execute("UPDATE life_action_outcomes SET status='failed',reason='真实失败' WHERE action_id=?", (action.action_id,))
            self.assertEqual(repair_committed_actions(conn)['actions'], [])
            self.assertEqual(conn.execute('SELECT status FROM life_action_outcomes').fetchone()[0], 'failed')


if __name__ == '__main__':
    unittest.main()
