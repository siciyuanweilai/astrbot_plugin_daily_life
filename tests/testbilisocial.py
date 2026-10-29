"""B站账号动作使用真实 SQLite，所有外部读写均由可检查的模拟接口承接。"""
import asyncio
import datetime as dt
import hashlib
import io
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from support import LifeArchive
from core.browsing.feed import API, BiliReadError, BiliReader, BiliLoginRequired, video
from core.browsing.platform import BiliPlatform, BiliWriteUncertain
from core.browsing.socializing import BiliSocialService, BiliDMScopeError, DM_SCOPE_RULE
from core.browsing.correspondence import BiliSocialStore
from core.browsing.surfing import BiliBrowseService
from core.browsing.visiting import BiliLiveVisit, capture_live, capture_hls, hls_parts
from core.browsing.material import BrowseMaterial
from core.browsing.storyboards import snapshot_entries, storyboard, tile, public_bytes
from core.config.options.viewing import BiliBrowseSettings
from core.clock import local_timestamp
from core.runtime.contact import contact_rhythm
from core.runtime.sharegate import ShareContactGate
from core.sight.bridge import SightFrameDescriptionResult

BV = 'BV1xx411c7mD'


class SocialTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = LifeArchive(Path(self.temp.name) / 'life.db')
        self.now = dt.datetime.now()
        self.day = types.SimpleNamespace(state=None, timeline=[])
        self.world = {'body': {'energy': 70}, 'run': {}, 'kernel': {}, 'goals': []}
        self.runtime = types.SimpleNamespace(
            archive=self.db, config=types.SimpleNamespace(browsing=BiliBrowseSettings()),
            _runtime_now=lambda: self.now, mark_page_status_changed=AsyncMock(),
            composer=types.SimpleNamespace(_get_persona=AsyncMock(return_value='角色本人')),
            get_persona_text=AsyncMock(return_value='当前关系'),
            _read_recent_context_messages=AsyncMock(return_value=[{'role': 'user', 'content': '私密约定'}]),
            _sight_cache_dir=lambda: Path(self.temp.name),
            _describe_sight_frames=AsyncMock(return_value=SightFrameDescriptionResult(notes=['正在实际弹琴'])),
            _prepare_sight_clip_material=AsyncMock(return_value={}),
        )
        self.browse = BiliBrowseService(self.runtime)
        self.browse.reader.require_login = AsyncMock(return_value='1234')
        self.browse.reader._mid = '1234'
        self.social = self.browse.social
        # 本组既有测试专注账号回执与身份，话题测试单独启用真实判断入口。
        async def related(scope, store, *, incoming=(), text=""):
            return {"incoming": [{"id": r["id"], "related": True} for r in incoming], "outgoing_related": True}
        self.social._review_dm = AsyncMock(side_effect=related)
        self.platform = types.SimpleNamespace(**{name: AsyncMock(return_value=[]) for name in (
            'folders', 'favorites', 'comments', 'notifications', 'dm_history', 'dynamics', 'live_rooms', 'live_history')})
        for name in ('favorite', 'comment', 'dm', 'dynamic', 'danmaku'):
            setattr(self.platform, name, AsyncMock(return_value={'accepted': True, 'remote_id': '999', 'folder_id': '7'}))
        self.platform.dynamic_page = AsyncMock(return_value={'rows': [], 'offset': ''})
        self.platform.dm_page = AsyncMock(return_value={'rows': [], 'more': False, 'oldest': 0, 'before': ''})
        self.platform.find_comment = AsyncMock(return_value=None)
        self.platform.notification_comment = AsyncMock(return_value=None)
        self.platform.favorite_membership = AsyncMock(return_value=set())
        self.platform.folders.return_value = [{'id': '7'}]
        self.platform.notification_page = AsyncMock(return_value={'items': [], 'cursor': None})
        self.platform.live_info = AsyncMock(return_value={'live_status': 1})
        self.platform.live_stream = AsyncMock(return_value='https://live.bilivideo.com/live.flv')
        self.factory = patch('core.browsing.socializing.BiliPlatform', return_value=self.platform)
        self.factory.start()
        self.social.platform = self.platform
        self.social._account = '1234'
        self.social._live = types.SimpleNamespace(tick=AsyncMock(return_value=False), leave=AsyncMock())
        self.store = BiliSocialStore(self.db, '1234')
        self.browse._model = AsyncMock(return_value={'action': 'wait', 'review_minutes': 5})
        await self.store.update({'poll_after': time.time()+3600, 'reconcile_after': time.time()+3600})

    async def asyncTearDown(self):
        self.factory.stop()
        await self.browse.close()
        self.db.close()
        self.temp.cleanup()

    async def video(self, **changes):
        record = self.browse.new_record({'bvid': BV, 'page': 1, 'aid': '42', 'cid': 8, 'duration': 180,
                                       'title': '实际实验', 'url': 'https://www.bilibili.com/video/'+BV})
        record = await self.browse.archive.get(record['id']) or record
        return await self.browse._save(record, evidence=[{'quote': '对照组', 'start': 0, 'end': 8}], impression='有意思', **changes)

    async def incoming(self, scope='private:a', key='1'):
        await self.store.update({'bindings': {scope: {'uid': '5678', 'since': 0, 'seen': []}}})
        return await self.store.put('dm-in:'+key, {'kind': 'dm_in', 'scope': scope, 'uid': '5678', 'text': '我们刚才聊的呢',
                                                   'at': self.now.isoformat(), 'status': 'new'})

    async def test_claim_is_atomic_and_survives_restart(self):
        value = {'kind': 'dm', 'scope': 'private:a', 'at': self.now.isoformat(), 'text': '好', 'outbound': True}
        results = await asyncio.gather(*(self.store.claim('same', value) for _ in range(8)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertIsNone(await BiliSocialStore(self.db, '1234').claim('same', value))
        self.assertIsNotNone(await BiliSocialStore(self.db, 'other').claim('same', value))

    async def test_daily_limit_counts_pending_requests(self):
        value = {'kind': 'dm', 'at': self.now.isoformat(), 'text': '好', 'outbound': True}
        first = await self.store.claim('a', value, daily_limit=1)
        await self.store.finish(first, status='uncertain')
        self.assertIsNone(await self.store.claim('b', value, daily_limit=1))

    async def test_scope_filter_applies_before_limit(self):
        for i in range(10):
            await self.store.put(str(i), {'kind': 'dm_in', 'scope': 'private:b', 'at': self.now.isoformat(), 'text': '秘密'})
        await self.store.put('a', {'kind': 'dm_in', 'scope': 'private:a', 'at': self.now.isoformat(), 'text': '自己'})
        self.assertEqual(len(await self.store.records(scope='private:a', limit=1)), 1)
        self.assertEqual(await BiliSocialStore(self.db, 'other').records(), [])
        self.assertEqual(await self.store.records(scope='global'), [])

    async def test_binding_requires_actual_sender_and_secret(self):
        result = await self.social.binding('private:a', '5678')
        token = result.splitlines()[1]
        state = await self.store.state()
        self.assertNotIn(token, json.dumps(state))
        self.assertEqual(state['challenges']['private:a']['hash'], hashlib.sha256(token.encode()).hexdigest())
        now = int(time.time())
        self.platform.dm_page.return_value = {'rows': [{'id': '1', 'uid': '9999', 'text': token, 'at': now, 'seq': '1'}], 'more': False, 'oldest': now, 'before': '1'}
        await self.store.update({'poll_after': 0})
        await self.social.poll(self.store, await self.store.state())
        self.assertFalse((await self.store.state()).get('bindings'))
        self.platform.dm_page.return_value['rows'][0]['uid'] = '5678'
        await self.store.update({'poll_after': 0})
        await self.social.poll(self.store, await self.store.state())
        self.assertEqual((await self.store.state())['bindings']['private:a']['uid'], '5678')
        await self.social.binding('private:a', remove=True)
        self.assertFalse((await self.store.state())['bindings'])

    async def test_binding_rejects_group_self_and_duplicate_owner(self):
        for scope, uid in [('global', '5678'), ('private:a', '1234'), ('private:a', 'NaN')]:
            with self.assertRaises(ValueError):
                await self.social.binding(scope, uid)
        await self.incoming()
        with self.assertRaises(ValueError):
            await self.social.binding('private:b', '5678')

    async def test_expired_binding_is_not_accepted(self):
        await self.store.update({'poll_after': 0, 'challenges': {'private:a': {'uid': '5678', 'expires': 1}}})
        await self.social.poll(self.store, await self.store.state())
        self.assertFalse((await self.store.state())['challenges'])
        self.platform.dm_page.assert_not_awaited()

    async def test_dm_same_second_distinct_ids_and_no_strangers(self):
        await self.incoming(key='old')
        now = int(time.time())
        rows = [{'id': str(i), 'uid': uid, 'text': str(i), 'at': now, 'seq': str(i)} for i, uid in [(3,'5678'), (4,'5678'), (5,'9999')]]
        self.platform.dm_page.return_value = {'rows': rows, 'more': False, 'oldest': now, 'before': '3'}
        for _ in range(2):
            await self.store.update({'poll_after': 0})
            await self.social.poll(self.store, await self.store.state())
        records = [r for r in await self.store.records(scope='private:a') if r['text'] in {'3','4','5'}]
        self.assertEqual({r['text'] for r in records}, {'3','4'})

    async def test_pagination_uses_raw_cursor_and_continues_backlog(self):
        await self.incoming()
        async def page(uid, before=''):
            n = int(before or 1000)
            return {'rows': [], 'more': n > 700, 'oldest': 100, 'before': str(n-30)}
        self.platform.dm_page.side_effect = page
        await self.store.update({'poll_after': 0})
        await self.social.poll(self.store, await self.store.state())
        binding = (await self.store.state())['bindings']['private:a']
        self.assertEqual(binding['backfill_before'], '820')
        await self.store.update({'poll_after': 0})
        await self.social.poll(self.store, await self.store.state())
        self.assertNotIn('backfill_before', (await self.store.state())['bindings']['private:a'])

    async def test_private_dm_isolated_from_ordinary_chat_and_life(self):
        target = await self.incoming()
        self.browse._model.return_value = {'action': 'dm', 'id': target['id'], 'text': '还记得我们刚才说的'}
        await self.social.tick(self.day, self.world)
        context = self.browse._model.await_args.args[1]
        self.assertNotIn('私密约定', json.dumps(context, ensure_ascii=False))
        self.assertNotIn('same_person_chat', context)
        self.assertNotIn('schedule', context)
        self.assertNotIn('current_run', context)
        self.runtime._read_recent_context_messages.assert_not_awaited()
        self.platform.dm.assert_awaited_once()
        self.assertEqual((await self.store.get(target['id']))['status'], 'handled')

    def enable_topic_review(self):
        self.social._review_dm = BiliSocialService._review_dm.__get__(self.social)

    async def test_unrelated_incoming_is_silent_and_does_not_trigger_contact(self):
        self.enable_topic_review()
        row = await self.incoming()
        await self.store.finish(row, text='从B站来找你，今晚陪我聊天吧')
        await self.video()
        self.browse._model.return_value = {'incoming': [{'id': row['id'], 'related': False}]}
        await self.social.tick(self.day, self.world)
        self.assertEqual((await self.store.get(row['id']))['status'], 'dismissed')
        self.assertNotIn('contact', self.browse._model.await_args.args[1]['allowed_actions'])
        self.platform.dm.assert_not_awaited()
        self.assertEqual(await self.social.context('private:a'), [])
        self.assertEqual((await self.social.message('private:a', 'history'))['dialogue'], [])

    async def test_mixed_burst_only_answers_bili_part(self):
        self.enable_topic_review()
        unrelated = await self.incoming(key='unrelated')
        await self.store.finish(unrelated, text='今晚陪我聊天吧', remote_at=10)
        related = await self.incoming(key='related')
        await self.store.finish(related, text='这个视频的结尾你怎么看', remote_at=11)
        async def model(rule, context):
            if rule == DM_SCOPE_RULE:
                return {'incoming': [{'id': r['id'], 'related': r['id'] == related['id']}
                                     for r in context['incoming']], 'outgoing_related': True}
            self.assertNotIn('今晚陪我聊天吧', json.dumps(context, ensure_ascii=False))
            return {'action': 'dm', 'id': related['id'], 'text': '结尾那个转折挺有意思'}
        self.browse._model.side_effect = model
        await self.social.tick(self.day, self.world)
        self.assertEqual((await self.store.get(unrelated['id']))['status'], 'dismissed')
        self.assertEqual((await self.store.get(related['id']))['status'], 'handled')
        sent = (await self.store.pending(['accepted']))[0]
        self.assertEqual(sent['covered_ids'], [related['id']])
        self.assertEqual(sent['topic_scope'], 'bilibili')
        self.platform.dm.assert_awaited_once()

    async def test_contextual_reply_without_platform_name_uses_confirmed_topic(self):
        self.enable_topic_review()
        old = await self.incoming(key='old')
        await self.store.finish(old, text='这个视频里的对照实验挺有意思', status='handled', topic_scope='bilibili')
        row = await self.incoming(key='new')
        await self.store.finish(row, text='你觉得最后那一步呢')
        async def model(rule, context):
            if rule == DM_SCOPE_RULE:
                self.assertIn('对照实验', json.dumps(context['bili_dialogue'], ensure_ascii=False))
                return {'incoming': [{'id': r['id'], 'related': True} for r in context['incoming']],
                        'outgoing_related': True}
            return {'action': 'dm', 'id': row['id'], 'text': '我觉得还缺一个对照'}
        self.browse._model.side_effect = model
        await self.social.tick(self.day, self.world)
        self.platform.dm.assert_awaited_once()
        self.runtime._read_recent_context_messages.assert_not_awaited()

    async def test_message_tool_cannot_send_unrelated_content_even_with_platform_word(self):
        self.enable_topic_review()
        await self.incoming()
        self.browse._model.return_value = {'outgoing_related': False}
        with self.assertRaises(BiliDMScopeError):
            await self.social.message('private:a', 'send', 'B站上给你说晚安，别熬夜', 'new-request')
        self.platform.dm.assert_not_awaited()
        self.assertFalse([r for r in await self.store.records() if r.get('outbound')])

    async def test_scope_review_failure_or_invalid_boolean_never_sends(self):
        self.enable_topic_review()
        await self.incoming()
        for result in ({}, {'outgoing_related': 'true'}, {'outgoing_related': 1}):
            self.browse._model.return_value = result
            with self.assertRaises(BiliDMScopeError):
                await self.social.message('private:a', 'send', '看看这个视频', 'request')
        self.browse._model.side_effect = RuntimeError('模型暂不可用')
        with self.assertRaises(BiliDMScopeError):
            await self.social.message('private:a', 'send', '看看这个视频', 'request')
        self.platform.dm.assert_not_awaited()
        self.assertFalse([r for r in await self.store.records() if r.get('outbound')])

    async def test_unknown_incoming_is_deferred_without_false_dismissal(self):
        self.enable_topic_review()
        row = await self.incoming()
        self.browse._model.return_value = {}
        await self.social.tick(self.day, self.world)
        saved = await self.store.get(row['id'])
        self.assertEqual(saved['status'], 'new')
        self.assertGreater(saved['review_after'], time.time())
        self.assertNotIn('incoming', self.browse._model.await_args.args[1])
        self.assertEqual(await self.social.context('private:a'), [])
        self.platform.dm.assert_not_awaited()

    async def test_conflicting_incoming_verdicts_defer_instead_of_dismissing(self):
        self.enable_topic_review()
        row = await self.incoming()
        self.browse._model.return_value = {'incoming': [
            {'id': row['id'], 'related': True}, {'id': row['id'], 'related': False}]}
        await self.social.tick(self.day, self.world)
        saved = await self.store.get(row['id'])
        self.assertEqual(saved['status'], 'new')
        self.assertGreater(saved['review_after'], time.time())
        self.platform.dm.assert_not_awaited()

    async def test_reply_cannot_expand_bili_topic_into_life_chat(self):
        self.enable_topic_review()
        row = await self.incoming()
        async def model(rule, context):
            if rule == DM_SCOPE_RULE:
                return {'incoming': [{'id': r['id'], 'related': True} for r in context['incoming']],
                        'outgoing_related': False}
            return {'action': 'dm', 'id': row['id'], 'text': '这个视频不错，你今晚吃饭了吗'}
        self.browse._model.side_effect = model
        await self.social.tick(self.day, self.world)
        self.platform.dm.assert_not_awaited()
        self.assertEqual((await self.store.get(row['id']))['status'], 'new')
        self.assertFalse([r for r in await self.store.records() if r.get('outbound')])

    async def test_autonomous_contact_rechecks_rhythm_after_scope_review(self):
        self.enable_topic_review()
        await self.store.update({'bindings': {'private:a': {'uid': '5678'}}})
        target, _ = await self.social._contact(self.store, await self.store.state(), [], [await self.video()])
        async def model(rule, context):
            await self.db.save_conversation_receipt('private:a', local_timestamp(self.now))
            return {'outgoing_related': True}
        self.browse._model.side_effect = model
        with self.assertRaises(BiliDMScopeError):
            await self.social.act('dm', text='这个实验挺有意思', target=target, scope='private:a')
        self.platform.dm.assert_not_awaited()

    async def test_scope_review_excludes_other_people_and_unclassified_history(self):
        self.enable_topic_review()
        row = await self.incoming(scope='private:b')
        await self.store.finish(row, text='另一个人的话', topic_scope='bilibili')
        row = await self.incoming()
        await self.store.finish(row, text='旧的生活闲聊', status='handled')
        await self.social._review_dm('private:a', self.store, text='这个视频挺不错')
        context = self.browse._model.await_args.args[1]
        self.assertEqual(set(context), {'bili_material', 'bili_dialogue', 'incoming', 'outgoing'})
        self.assertEqual(context['bili_dialogue'], [])
        self.assertNotIn('私密约定', json.dumps(context, ensure_ascii=False))
        self.runtime._read_recent_context_messages.assert_not_awaited()

    async def test_unbinding_during_scope_review_prevents_send(self):
        self.enable_topic_review()
        await self.incoming()
        async def model(rule, context):
            await self.store.update({'bindings': {}})
            return {'outgoing_related': True}
        self.browse._model.side_effect = model
        with self.assertRaises(BiliDMScopeError):
            await self.social.message('private:a', 'send', '这个视频挺不错', 'request')
        self.platform.dm.assert_not_awaited()

    async def test_runtime_change_during_scope_review_prevents_send(self):
        self.enable_topic_review()
        await self.incoming()
        async def model(rule, context):
            self.browse._epoch += 1
            return {'outgoing_related': True}
        self.browse._model.side_effect = model
        with self.assertRaises(BiliDMScopeError):
            await self.social.message('private:a', 'send', '这个视频挺不错', 'request')
        self.platform.dm.assert_not_awaited()

    async def test_ordinary_chat_alone_never_creates_bili_contact(self):
        await self.store.update({'bindings': {'private:a': {'uid': '5678'}}})
        await self.social.tick(self.day, self.world)
        self.assertNotIn('contact', self.browse._model.await_args.args[1]['allowed_actions'])
        self.runtime._read_recent_context_messages.assert_not_awaited()
        self.platform.dm.assert_not_awaited()

    async def test_dm_never_crosses_scope_or_sends_unverified(self):
        target = await self.incoming()
        with self.assertRaises(ValueError):
            await self.social.act('dm', text='私密', target=target, scope='private:b')
        self.platform.dm.assert_not_awaited()
        self.assertEqual(await self.social.context('private:b'), [])

    async def test_uncertain_send_is_not_retried(self):
        target = await self.incoming()
        self.platform.dm.side_effect = BiliWriteUncertain('超时')
        for _ in range(2):
            with self.assertRaises((ValueError, BiliWriteUncertain)):
                await self.social.act('dm', text='一次就好', target=target, scope='private:a')
        self.platform.dm.assert_awaited_once()
        self.assertEqual(len(await self.store.pending(['uncertain'])), 1)
        self.assertEqual((await self.store.get(target['id']))['status'], 'responding')

    async def test_cancelled_send_preserves_journal(self):
        target = await self.incoming()
        self.platform.dm.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.social.act('dm', text='一次', target=target, scope='private:a')
        self.assertEqual(len(await self.store.pending(['uncertain'])), 1)

    async def test_old_identical_message_is_not_delivery_receipt(self):
        target = await self.incoming()
        self.platform.dm.side_effect = BiliWriteUncertain()
        with self.assertRaises(BiliWriteUncertain):
            await self.social.act('dm', text='收到', target=target, scope='private:a')
        self.platform.dm_page.return_value = {'rows': [{'id':'900','uid':'1234','text':'收到','at':1,'seq':'900'}], 'more':False, 'oldest':1, 'before':'900'}
        await self.social.reconcile(self.store)
        self.assertEqual(len(await self.store.pending(['uncertain'])), 1)
        self.platform.dm_page.return_value['rows'][0]['at'] = int(time.time())
        await self.social.reconcile(self.store)
        self.assertEqual(len(await self.store.pending(['confirmed'])), 1)
        self.assertEqual((await self.store.get(target['id']))['status'], 'handled')

    async def test_public_actions_refuse_private_video(self):
        record = await self.video()
        record['scope'] = 'private:a'
        for action in ('favorite','comment','dynamic'):
            with self.assertRaises(ValueError):
                await self.social.act(action, record=record, text='公开')
        self.platform.dynamic.assert_not_awaited()

    async def test_uncertain_receipts_rotate_without_danmaku_starvation(self):
        for i in range(6):
            await self.store.put('old-danmaku:' + str(i), {
                'kind': 'danmaku', 'at': self.now.isoformat(), 'status': 'uncertain',
                'target': {'room': '66'}, 'text': '旧弹幕', 'outbound': True,
            })
        for i in range(5):
            await self.store.put('old-dm:' + str(i), {
                'kind': 'dm', 'at': self.now.isoformat(), 'status': 'uncertain',
                'target': {'uid': str(6000 + i)}, 'text': '旧私信', 'outbound': True,
            })
        await self.social.reconcile(self.store)
        first = {call.args[0] for call in self.platform.dm_page.await_args_list}
        self.assertEqual(len(first), 4)
        self.platform.dm_page.reset_mock()
        await self.social.reconcile(self.store)
        second = {call.args[0] for call in self.platform.dm_page.await_args_list}
        self.assertEqual(first | second, {str(6000 + i) for i in range(5)})
        self.platform.danmaku.assert_not_awaited()
        self.platform.dm.assert_not_awaited()

    async def test_public_expression_excludes_private_life_and_dialogue(self):
        record = await self.video()
        self.world['run'] = {'private': '不能外传'}
        self.browse._model.side_effect = [{'action':'dynamic','id':record['id'],'text':'这个草稿包含不能外传'}, {'action':'dynamic','id':record['id'],'text':'实验对照很有意思'}]
        await self.social.tick(self.day, self.world)
        public_context = self.browse._model.await_args_list[1].args[1]
        self.assertNotIn('不能外传', json.dumps(public_context, ensure_ascii=False))
        self.assertNotIn('same_person_chat', public_context)
        self.platform.dynamic.assert_awaited_once_with('实验对照很有意思')

    async def test_same_video_dynamic_cannot_repeat_variant(self):
        record = await self.video()
        await self.social.act('dynamic', record=record, text='我的想法')
        with self.assertRaises(ValueError):
            await self.social.act('dynamic', record=record, text='换个说法')
        self.platform.dynamic.assert_awaited_once()

    async def test_danmaku_expression_uses_only_actual_public_live_material(self):
        live = {'id': 'visit:1', 'room': {'id': '66', 'title': '音乐直播'},
                'evidence': [{'source': '实际直播画面', 'quote': '正在实际弹琴'}]}
        self.social._live.tick.return_value = live
        self.world['run'] = {'private': '不能外传'}
        self.browse._model.side_effect = [
            {'action': 'danmaku', 'text': '这个草稿包含不能外传'},
            {'action': 'danmaku', 'text': '这段弹得真好听'},
        ]
        await self.social.tick(self.day, self.world)
        public_context = self.browse._model.await_args_list[1].args[1]
        self.assertNotIn('不能外传', json.dumps(public_context, ensure_ascii=False))
        self.assertEqual(public_context['public_material']['evidence'], live['evidence'])
        self.platform.danmaku.assert_awaited_once_with('66', '这段弹得真好听')

    async def test_live_public_generation_interruption_prevents_send(self):
        self.social._live.tick.return_value = {
            'id': 'visit:1', 'room': {'id': '66', 'title': '音乐直播'},
            'evidence': [{'quote': '正在实际弹琴'}],
        }

        async def model(rule, context):
            if context.get('allowed_actions') == ['danmaku', 'wait']:
                self.browse._epoch += 1
            return {'action': 'danmaku', 'text': '好听'}

        self.browse._model.side_effect = model
        await self.social.tick(self.day, self.world)
        self.platform.danmaku.assert_not_awaited()

    async def test_favorite_unfavorite_refavorite_are_distinct_transitions(self):
        record = await self.video()
        for action in ('favorite','unfavorite','favorite'):
            self.platform.favorite_membership.return_value = {'7'} if action == 'unfavorite' else set()
            record = await self.browse.archive.get(record['id'])
            await self.social.act(action, record=record)
        self.assertEqual(self.platform.favorite.await_count, 3)
        self.assertTrue((await self.browse.archive.get(record['id']))['favorited'])

    async def test_uncertain_favorite_blocks_reverse_action(self):
        record = await self.video()
        self.platform.favorite.side_effect = BiliWriteUncertain()
        with self.assertRaises(BiliWriteUncertain):
            await self.social.act('favorite', record=record)
        with self.assertRaises(ValueError):
            await self.social.act('unfavorite', record=record)
        self.platform.favorite.assert_awaited_once()

    async def test_favorite_journal_remembers_folder_before_uncertain_send(self):
        record = await self.video()
        self.platform.favorite.side_effect = BiliWriteUncertain()
        with self.assertRaises(BiliWriteUncertain):
            await self.social.act('favorite', record=record)
        row = (await self.store.pending(['uncertain']))[0]
        self.assertEqual(row['folder_id'], '7')
        self.platform.folders.return_value = [{'id':'8'}, {'id':'7'}]
        self.platform.favorite_membership.return_value = {'7'}
        await self.social.reconcile(self.store)
        self.assertEqual((await self.browse.archive.get(record['id']))['favorite_folder'], '7')

    async def test_manual_favorite_is_synced_without_repeating_write(self):
        record = await self.video()
        self.platform.favorite_membership.return_value = {'7'}
        with self.assertRaises(ValueError):
            await self.social.act('favorite', record=record)
        self.platform.favorite.assert_not_awaited()
        self.assertTrue((await self.browse.archive.get(record['id']))['favorited'])

    async def test_resolved_journal_returns_to_normal_retention(self):
        row = await self.store.claim('retention', {'kind':'dm', 'at':self.now.isoformat(), 'text':'好', 'outbound':True})
        self.assertEqual(self.db._conn.execute('SELECT date FROM continuous_life_entries WHERE id=?',(row['id'],)).fetchone()[0], '')
        await self.store.finish(row, status='accepted')
        self.assertEqual(self.db._conn.execute('SELECT date FROM continuous_life_entries WHERE id=?',(row['id'],)).fetchone()[0], self.now.date().isoformat())

    async def test_login_change_leaves_old_account_live_immediately(self):
        visit = self.social._live
        await self.browse.login_changed()
        visit.leave.assert_awaited_once()
        self.assertIsNone(self.social._live)
        self.assertEqual(self.social._account, '')

    async def test_comments_reverify_exact_floor(self):
        target = {'id':'notify:1','aid':'42','root':'8','parent':'9','uid':'5678','text':'原内容'}
        self.platform.find_comment.return_value = {'text':'改了'}
        with self.assertRaises(ValueError):
            await self.social.act('reply', target=target, text='回复')
        self.platform.comment.assert_not_awaited()

    async def test_notifications_store_resolved_reply_id_not_original_parent(self):
        self.platform.notification_page.side_effect = [{'items':[{'id':'7','reply_time':20,'user':{'mid':'5678'},'item':{'business_id':1,'subject_id':'42','root_id':'8','source_id':'9','title':'不可信'}}], 'cursor':None}, {'items':[], 'cursor':None}]
        self.platform.notification_comment.return_value = {'id':'10','root':'8','parent':'9','uid':'5678','text':'实际回复','at':20}
        await self.store.update({'poll_after':0,'notification_ready':True})
        await self.social.poll(self.store, await self.store.state())
        row = next(r for r in await self.store.records() if r['kind']=='notification')
        self.assertEqual((row['parent'],row['text']), ('10','实际回复'))

    async def test_busy_life_never_calls_interaction_model(self):
        await self.video()
        await self.social.tick(self.day, self.world, '正在睡觉')
        self.browse._model.assert_not_awaited()
        self.platform.dynamic.assert_not_awaited()

    async def test_notification_backlog_keeps_cursor_until_all_pages_read(self):
        async def page(kind, cursor=None):
            if kind == 'at':
                return {'items':[], 'cursor':None}
            n = int((cursor or {}).get('id', 100))
            return {'items':[{'id':str(n), 'reply_time':n, 'item':{'business_id':17}}],
                    'cursor':{'id':str(n-1), 'time':n-1} if n > 92 else None}
        self.platform.notification_page.side_effect = page
        await self.store.update({'poll_after':0,'notification_ready':True})
        await self.social.poll(self.store, await self.store.state())
        self.assertEqual((await self.store.state())['notification_progress']['reply']['cursor']['id'], '94')
        await self.store.update({'poll_after':0})
        await self.social.poll(self.store, await self.store.state())
        result = (await self.store.state())['notification_progress']['reply']
        self.assertEqual(result, {'after':100, 'high_water':100, 'cursor':None})

    async def test_interrupted_model_never_publishes(self):
        record = await self.video()
        async def model(*args):
            self.browse._epoch += 1
            return {'action':'dynamic','id':record['id'],'text':'迟到的内容'}
        self.browse._model.side_effect = model
        await self.social.tick(self.day, self.world)
        self.platform.dynamic.assert_not_awaited()

    async def test_live_and_favorites_available_without_video_history(self):
        self.platform.live_rooms.return_value = [{'id':'100','title':'直播'}]
        await self.social.tick(self.day, self.world)
        allowed = self.browse._model.await_args.args[1]['allowed_actions']
        self.assertIn('join', allowed)
        self.assertIn('revisit', allowed)

    async def test_favorite_pages_rotate_across_account_folders(self):
        self.platform.folders.return_value = [{'id':'1'},{'id':'2'}]
        self.platform.favorites.return_value = []
        await self.social._favorite_candidates(self.store)
        await self.social._favorite_candidates(self.store)
        self.assertEqual([c.args[0] for c in self.platform.favorites.await_args_list], ['1','2'])

    async def test_dm_tool_uses_bound_identity_and_per_message_dedup(self):
        await self.incoming()
        await self.social.message('private:a','send','B站见','request-1')
        with self.assertRaises(ValueError):
            await self.social.message('private:a','send','B站见','request-1')
        self.platform.dm.assert_awaited_once()
        self.assertEqual(self.platform.dm.await_args.args[0], '5678')

    async def test_ability_switch_prevents_network_write(self):
        self.runtime.config.browsing.comments_enabled=False
        with self.assertRaises(ValueError):
            await self.social.act('comment', record=await self.video(), text='你好')
        self.platform.comment.assert_not_awaited()

    async def test_rejected_reply_restores_message_and_can_be_reconsidered(self):
        target = await self.incoming()
        self.platform.dm.side_effect = BiliReadError('平台拒绝')
        with self.assertRaises(BiliReadError):
            await self.social.act('dm', text='好', target=target, scope='private:a')
        self.assertEqual((await self.store.get(target['id']))['status'], 'new')
        journal = (await self.store.pending(['rejected']))[0]
        self.assertEqual(journal['attempts'], 1)
        with self.assertRaises(ValueError):
            await self.social.act('dm', text='好', target=target, scope='private:a')
        await self.store.finish(journal, retry_after=0)
        self.platform.dm.side_effect = None
        await self.social.act('dm', text='换个自然说法', target=target, scope='private:a')
        self.assertEqual((await self.store.get(target['id']))['status'], 'handled')
        self.assertEqual((await self.store.pending(['accepted']))[0]['attempts'], 2)
        self.assertEqual(self.platform.dm.await_count, 2)

    async def test_repeated_explicit_rejection_finishes_as_failed(self):
        target = await self.incoming()
        self.platform.dm.side_effect = BiliReadError('平台拒绝')
        for _ in range(3):
            for row in await self.store.pending(['rejected']):
                await self.store.finish(row, retry_after=0)
            with self.assertRaises(BiliReadError):
                await self.social.act('dm', text='好', target=target, scope='private:a')
        self.assertEqual((await self.store.get(target['id']))['status'], 'failed')
        with self.assertRaises(ValueError):
            await self.social.act('dm', text='好', target=target, scope='private:a')
        self.assertEqual(self.platform.dm.await_count, 3)

    async def test_waiting_old_message_does_not_starve_next_message(self):
        first = await self.incoming(key='oldest')
        first = await self.store.finish(first, at=(self.now-dt.timedelta(minutes=1)).isoformat())
        second = await self.incoming(scope='private:b', key='latest')
        await self.social.tick(self.day, self.world)
        self.assertEqual(self.browse._model.await_args.args[1]['incoming']['id'], first['id'])
        await self.social.tick(self.day, self.world)
        self.assertEqual(self.browse._model.await_args.args[1]['incoming']['id'], second['id'])
        self.assertGreater((await self.store.get(first['id']))['review_after'], time.time())

    async def test_incoming_does_not_block_public_life(self):
        await self.incoming()
        record = await self.video()
        self.browse._model.side_effect = [
            {'action':'dynamic', 'id':record['id']},
            {'action':'dynamic', 'id':record['id'], 'text':'实际实验挺有意思'},
        ]
        await self.social.tick(self.day, self.world)
        self.platform.dynamic.assert_awaited_once_with('实际实验挺有意思')
        public = self.browse._model.await_args.args[1]
        self.assertEqual(public['persona'], '角色本人')
        self.assertNotIn('私密约定', json.dumps(public, ensure_ascii=False))

    async def test_message_can_be_left_without_an_endless_pending_state(self):
        target = await self.incoming()
        self.browse._model.return_value = {'action':'dismiss', 'id':target['id'], 'reason':'这条不需要回复'}
        await self.social.tick(self.day, self.world)
        self.assertEqual((await self.store.get(target['id']))['status'], 'dismissed')
        self.platform.dm.assert_not_awaited()

    async def test_autonomous_contact_needs_real_changes_and_does_not_chase(self):
        await self.video()
        await self.store.update({'bindings': {'private:a': {'uid':'5678'}}})
        async def model(rule, context):
            contact = context.get('contact')
            return {'action':'contact', 'id':contact['id'], 'text':'刚才那个实验我又想到一点'} if contact else {'action':'wait'}
        self.browse._model.side_effect = model
        await self.social.tick(self.day, self.world)
        self.platform.dm.assert_awaited_once()
        self.assertTrue((self.platform.dm.await_args.args[1]).startswith('刚才'))
        await self.video()
        await self.store.update({'review_after':0})
        await self.social.tick(self.day, self.world)
        self.assertNotIn('contact', self.browse._model.await_args.args[1]['allowed_actions'])
        self.platform.dm.assert_awaited_once()

    async def test_unverified_person_never_becomes_autonomous_contact(self):
        await self.social.tick(self.day, self.world)
        self.assertNotIn('contact', self.browse._model.await_args.args[1]['allowed_actions'])

    async def test_dm_burst_is_understood_and_answered_once(self):
        first = await self.incoming(key='first')
        first = await self.store.finish(first, remote_at=10)
        latest = await self.incoming(key='latest')
        latest = await self.store.finish(latest, remote_at=11)
        self.browse._model.return_value = {'action':'dm', 'id':latest['id'], 'text':'两条都看到啦'}
        await self.social.tick(self.day, self.world)
        self.platform.dm.assert_awaited_once()
        self.assertEqual((await self.store.get(first['id']))['status'], 'handled')
        self.assertEqual((await self.store.get(latest['id']))['status'], 'handled')

    async def test_sent_echo_does_not_allow_another_unanswered_contact(self):
        await self.store.update({'bindings': {'private:a': {'uid':'5678'}}})
        target, _ = await self.social._contact(self.store, await self.store.state(), [], [await self.video()])
        await self.social.act('dm', text='刚才想到你', target=target, scope='private:a')
        await self.store.put('echo', {'kind':'dm_self', 'scope':'private:a', 'uid':'5678',
            'text':'刚才想到你', 'at':(self.now+dt.timedelta(seconds=3)).isoformat(), 'status':'received'})
        await self.video()
        await self.social.tick(self.day, self.world)
        self.assertNotIn('contact', self.browse._model.await_args.args[1]['allowed_actions'])
        self.platform.dm.assert_awaited_once()

    async def test_rejected_contact_can_be_reconsidered_after_cooldown(self):
        await self.store.update({'bindings': {'private:a': {'uid':'5678'}}})
        target, _ = await self.social._contact(self.store, await self.store.state(), [], [await self.video()])
        self.platform.dm.side_effect = BiliReadError('平台拒绝')
        with self.assertRaises(BiliReadError):
            await self.social.act('dm', text='刚才想到你', target=target, scope='private:a')
        contact, _ = await self.social._contact(self.store, await self.store.state(), await self.store.records(), [await self.video()])
        self.assertIsNone(contact)
        rejected = (await self.store.pending(['rejected']))[0]
        await self.store.finish(rejected, retry_after=0)
        contact, _ = await self.social._contact(self.store, await self.store.state(), await self.store.records(), [await self.video()])
        self.assertEqual(contact['id'], target['id'])

    async def test_ordinary_chat_response_cannot_create_a_bili_topic(self):
        await self.store.update({'bindings': {'private:a': {'uid':'5678'}}})
        target, _ = await self.social._contact(self.store, await self.store.state(), [], [await self.video()])
        await self.social.act('dm', text='刚才想到你', target=target, scope='private:a')
        self.now += dt.timedelta(minutes=10)
        await self.db.save_conversation_arrival('private:a', local_timestamp(self.now))
        self.runtime._context_message_timestamp = lambda row: float(row.get('timestamp') or 0)
        self.runtime._read_recent_context_messages.return_value = [
            {'role':'user', 'content':'刚刚在B站看到啦', 'timestamp':local_timestamp(self.now)}]
        await self.social.tick(self.day, self.world)
        self.assertNotIn('contact', self.browse._model.await_args.args[1]['allowed_actions'])

    async def test_new_experience_can_prompt_later_contact_without_a_reply(self):
        await self.video()
        await self.store.update({'bindings': {'private:a': {'uid': '5678'}}})

        async def model(rule, context):
            contact = context.get('contact')
            return {'action': 'contact', 'id': contact['id'], 'text': '刚看到个新东西'} if contact else {'action': 'wait'}

        self.browse._model.side_effect = model
        await self.social.tick(self.day, self.world)
        self.assertEqual(self.platform.dm.await_count, 1)
        self.now += dt.timedelta(minutes=10)
        await self.store.update({'review_after': 0})
        await self.social.tick(self.day, self.world)
        self.assertEqual(self.platform.dm.await_count, 1)
        await self.video(position=30)
        await self.store.update({'review_after': 0})
        await self.social.tick(self.day, self.world)
        self.assertEqual(self.platform.dm.await_count, 2)
        rhythm = await contact_rhythm(self.runtime, 'private:a', self.now)
        self.assertEqual(rhythm['unanswered_contacts'], 2)
        self.assertEqual(rhythm['retry_after'], 16 * 60)

    async def test_qq_or_share_receipt_delays_bili_contact(self):
        await self.video()
        await self.store.update({'bindings': {'private:a': {'uid': '5678'}}})
        await self.db.save_conversation_receipt('private:a', local_timestamp(self.now))
        await self.social.tick(self.day, self.world)
        context = self.browse._model.await_args.args[1]
        self.assertNotIn('contact', context['allowed_actions'])
        self.platform.dm.assert_not_awaited()

    async def test_receipt_during_decision_prevents_cross_platform_burst(self):
        await self.video()
        await self.store.update({'bindings': {'private:a': {'uid': '5678'}}})

        async def model(rule, context):
            await self.db.save_conversation_receipt('private:a', local_timestamp(self.now))
            return {'action': 'contact', 'id': context['contact']['id'], 'text': '刚看到个新东西'}

        self.browse._model.side_effect = model
        await self.social.tick(self.day, self.world)
        self.platform.dm.assert_not_awaited()
        self.assertEqual(await self.store.records(), [])

    async def test_other_plugin_reservation_prevents_bili_contact(self):
        await self.video()
        await self.store.update({'bindings': {'private:a': {'uid': '5678'}}})
        gate = self.runtime.share_contact_gate = ShareContactGate(self.runtime)
        self.assertTrue(gate.claim('private:a', 'daily_share:test'))

        async def model(rule, context):
            return {'action': 'contact', 'id': context['contact']['id'], 'text': '刚看到个新东西'}

        self.browse._model.side_effect = model
        await self.social.tick(self.day, self.world)
        self.platform.dm.assert_not_awaited()
        self.assertEqual(gate.leases['private:a'][0], 'daily_share:test')

    async def test_poll_records_only_bound_identity_using_remote_time(self):
        stamp = int(local_timestamp(self.now))
        await self.store.update({'poll_after': 0, 'bindings': {'private:a': {'uid': '5678', 'since': 0}}})
        self.platform.dm_page.return_value = {'rows': [
            {'id': '1', 'uid': '1234', 'at': stamp - 60, 'text': '角色发的私信'},
            {'id': '2', 'uid': '5678', 'at': stamp - 30, 'text': '我收到啦'},
            {'id': '3', 'uid': '9999', 'at': stamp, 'text': '其他人'},
        ], 'more': False, 'oldest': stamp - 60, 'before': ''}
        await self.social.poll(self.store, await self.store.state())
        rhythm = await contact_rhythm(self.runtime, 'private:a', self.now)
        self.assertEqual(rhythm['last_sent_at'], stamp - 60)
        self.assertEqual(rhythm['last_received_at'], stamp - 30)
        self.assertEqual(rhythm['unanswered_contacts'], 0)
        self.assertEqual(len(await self.store.records(scope='private:a')), 2)

    async def test_contact_releases_shared_reservation_after_send_or_rejection(self):
        await self.video()
        await self.store.update({'bindings': {'private:a': {'uid': '5678'}}})
        gate = self.runtime.share_contact_gate = ShareContactGate(self.runtime)

        async def model(rule, context):
            return {'action': 'contact', 'id': context['contact']['id'], 'text': '刚看到个新东西'}

        self.browse._model.side_effect = model
        with patch('core.runtime.sharegate.life_now', lambda: self.now):
            await self.social.tick(self.day, self.world)
            self.assertEqual(self.platform.dm.await_count, 1)
            self.assertEqual(gate.leases, {})
            self.now += dt.timedelta(minutes=20)
            await self.video(position=30)
            await self.store.update({'review_after': 0})
            self.platform.dm.side_effect = BiliReadError('平台明确拒绝')
            with self.assertRaises(BiliReadError):
                await self.social.tick(self.day, self.world)
        self.assertEqual(gate.leases, {})
        rhythm = await contact_rhythm(self.runtime, 'private:a', self.now)
        self.assertEqual(rhythm['unanswered_contacts'], 1)

    async def test_public_initiative_contains_no_private_receipts(self):
        self.runtime.config.proactive = types.SimpleNamespace(private_talk_frequency=0.85)
        await self.db.save_conversation_receipt('private:a', local_timestamp(self.now))
        context = await self.browse._context(self.day, self.world, {})
        self.assertEqual(context['contact_rhythm'], {'willingness': 0.85})

    async def test_reconciliation_resumes_comment_page_after_budget(self):
        row = await self.store.put('comment:pending', {'kind':'comment', 'status':'uncertain', 'at':self.now.isoformat(),
            'text':'实际评论', 'aid':'42', 'outbound':True, 'target':{}})
        now = int(self.now.timestamp())
        async def comments(aid, root='', page=1):
            if page == 4:
                return [{'id':'9','uid':'1234','text':'实际评论','at':now,'parent':''}]
            return [{'id':str(i),'uid':'9999','text':'其他','at':now,'parent':''} for i in range(20)]
        self.platform.comments.side_effect = comments
        await self.social.reconcile(self.store)
        self.assertEqual((await self.store.get(row['id']))['reconcile_page'], 4)
        await self.social.reconcile(self.store)
        self.assertEqual((await self.store.get(row['id']))['status'], 'confirmed')
        self.assertEqual([c.kwargs['page'] for c in self.platform.comments.await_args_list], [1,2,3,4])

    async def test_reconciliation_finds_dynamic_beyond_first_page(self):
        row = await self.store.put('dynamic:pending', {'kind':'dynamic', 'status':'uncertain', 'at':self.now.isoformat(),
            'text':'实际动态', 'outbound':True, 'target':{}})
        self.platform.dynamic_page.side_effect = [
            {'rows':[], 'offset':'older'},
            {'rows':[{'id':'10','text':'实际动态','at':int(self.now.timestamp())}], 'offset':''},
        ]
        await self.social.reconcile(self.store)
        self.assertEqual((await self.store.get(row['id']))['status'], 'confirmed')
        self.assertEqual(self.platform.dynamic_page.await_args.kwargs['offset'], 'older')

    async def test_live_context_keeps_room_evidence_and_actual_duration(self):
        await self.store.put('live:context', {'kind':'live','status':'left','at':self.now.isoformat(), 'scope':'global',
            'room':{'id':'100','title':'音乐'}, 'text':'弹了钢琴','evidence':[{'quote':'真实钢琴画面'}], 'seconds':3})
        context = (await self.social.context('private:a'))[0]
        self.assertEqual((context['room']['id'],context['seconds']),('100',3))
        self.assertEqual(context['evidence'],[{'quote':'真实钢琴画面'}])

    async def test_following_feed_backfill_survives_restart_and_new_head(self):
        reader = BiliReader(lambda: {}, self.db)
        reader.require_login = AsyncMock(return_value='1234')
        async def request(url, params):
            n = int(params.get('offset') or 100)
            return {'items':[{'id_str':str(n), 'modules':{}}], 'offset':str(n-1), 'has_more':n > 90}
        reader._request = AsyncMock(side_effect=request)
        await reader.following_feed()
        progress = (await self.store.state())['following_progress']
        self.assertEqual((progress['offset'], progress['high_water']), ('94',100))
        restarted = BiliReader(lambda: {}, self.db)
        restarted.require_login = AsyncMock(return_value='1234')
        async def updated(url, params):
            n = int(params.get('offset') or 110)
            return {'items':[{'id_str':str(n), 'modules':{}}], 'offset':str(n-1), 'has_more':n > 90}
        restarted._request = AsyncMock(side_effect=updated)
        await restarted.following_feed()
        progress = (await self.store.state())['following_progress']
        self.assertEqual((progress['offset'], progress['after']), ('',100))
        await restarted.following_feed()
        progress = (await self.store.state())['following_progress']
        self.assertEqual(progress['high_water'],110)
        self.assertTrue(progress['offset'])

    async def test_deleted_notification_does_not_reset_other_notification_pages(self):
        self.platform.notification_page.side_effect = [
            {'items':[{'id':'8','reply_time':20,'user':{'mid':'5678'},'item':{'business_id':1,'subject_id':'42','root_id':'8','source_id':'9'}}], 'cursor':None},
            {'items':[], 'cursor':None},
        ]
        await self.store.update({'poll_after':0, 'notification_ready':True})
        await self.social.poll(self.store, await self.store.state())
        self.assertEqual((await self.store.state())['notification_progress']['reply']['after'],20)
        row = (await self.store.pending(['unresolved']))[0]
        self.assertEqual(row['text'],'')
        for _ in range(2):
            await self.store.finish(await self.store.get(row['id']), lookup_after=0)
            self.platform.notification_page.side_effect = None
            self.platform.notification_page.return_value = {'items':[], 'cursor':None}
            await self.store.update({'poll_after':0})
            await self.social.poll(self.store, await self.store.state())
        self.assertEqual((await self.store.get(row['id']))['status'],'unavailable')

    async def test_live_uses_hls_when_first_line_fails(self):
        visit = await self.live()
        self.platform.live_stream.side_effect = ['https://a.bilivideo.com/a.flv','https://a.bilivideo.com/a.m3u8']
        with patch('core.browsing.visiting.capture_live', AsyncMock(side_effect=[BiliReadError('线路中断'),8])) as capture, patch('core.browsing.visiting.ffprobe_executable', return_value='ffprobe'), patch('core.browsing.visiting._video_duration', return_value=3), patch('core.browsing.visiting.ffmpeg_executable', return_value='ffmpeg'), patch('core.browsing.visiting._extract_frame', return_value=True):
            result = await visit.tick(self.store)
        self.assertEqual(result['seconds'],3)
        self.assertEqual(capture.await_count,2)
        self.assertEqual(self.platform.live_stream.await_args.kwargs['exclude'],['https://a.bilivideo.com/a.flv'])

    async def test_creator_familiarity_keeps_updated_observations_without_duplicate_videos(self):
        record = await self.video(author_id='5678', author='真实UP主')
        await self.browse._learn_interest(record,{})
        record['impression'] = '看到后半段后改了想法'
        record['position'] = 120
        await self.browse._learn_interest(record,{})
        creator = (await self.browse.archive.state())['creators']['5678']
        self.assertEqual(len(creator['examples']),1)
        self.assertEqual(len(creator['observations']),1)
        self.assertEqual(creator['last_impression'],'看到后半段后改了想法')
        self.assertEqual(creator['observations'][0]['position'],120)

    async def test_subtitle_region_note_and_each_frame_timestamp_survive(self):
        from PIL import Image
        path = Path(self.temp.name)/'frame.jpg'
        Image.new('RGB',(160,90)).save(path)
        async def description(clip, frames):
            return SightFrameDescriptionResult(notes=['画面','可见文字'], assets=[
                {'path':str(frames[0].path),'note':'实际画面'},
                {'path':str(frames[-1].path),'note':'屏幕上写着对照实验'},
            ])
        self.runtime.config.sight = types.SimpleNamespace(frame_provider='vision')
        self.runtime._describe_sight_frames.side_effect = description
        material = BrowseMaterial(self.runtime,self.browse.reader)
        frames = [types.SimpleNamespace(path=path,second=12),types.SimpleNamespace(path=path,second=18)]
        with patch('core.browsing.material.storyboard', AsyncMock(return_value=frames)):
            rows = await material.frames({'page':1,'scope':'global','url':'https://www.bilibili.com/video/'+BV,'title':'实际实验','duration':60}, {'source_path':None,'segments':[]}, 10,20)
        self.assertEqual(rows[-1]['source'],'采样画面文字')
        self.assertIn('对照实验',rows[-1]['quote'])
        self.assertEqual((rows[-1]['start'],rows[-1]['end']),(12,18))

    async def test_actual_live_experience_can_become_text_dynamic(self):
        live = await self.store.put('live:public', {'kind':'live','status':'left','scope':'global','at':self.now.isoformat(),
            'room':{'id':'66','title':'音乐'},'text':'弹了钢琴','evidence':[{'quote':'实际弹奏钢琴'}],'seconds':3})
        self.browse._model.side_effect = [
            {'action':'dynamic','id':live['id']},
            {'action':'dynamic','id':live['id'],'text':'刚听到一小段钢琴，真好听'},
        ]
        await self.social.tick(self.day,self.world)
        self.platform.dynamic.assert_awaited_once()
        self.assertEqual(self.browse._model.await_args.args[1]['public_material'][0]['room']['id'],'66')

    async def live(self):
        visit = BiliLiveVisit(self.social)
        visit.started=True
        active = await self.store.put('live:1', {'kind':'live','scope':'global','at':self.now.isoformat(),'status':'visiting',
            'room':{'id':'100','title':'真实直播'},'seconds':0,'last_tick':time.time(),'evidence':[],'text':''})
        await self.store.update({'active_live':active})
        return visit

    async def test_live_restart_does_not_count_offline_time(self):
        visit=await self.live()
        visit.started=False
        self.assertFalse(await visit.tick(self.store))
        self.assertIsNone((await self.store.state())['active_live'])
        self.platform.live_stream.assert_not_awaited()

    async def test_live_offline_and_busy_leave_without_capture(self):
        for blocked, status in [('正在忙',1), ('',0)]:
            visit=await self.live()
            self.platform.live_info.return_value={'live_status':status}
            self.assertFalse(await visit.tick(self.store,blocked))
        self.platform.live_stream.assert_not_awaited()

    async def test_live_without_real_evidence_does_not_advance(self):
        visit=await self.live()
        with patch('core.browsing.visiting.capture_live', AsyncMock(return_value=8)), patch('core.browsing.visiting.ffprobe_executable', return_value='ffprobe'), patch('core.browsing.visiting._video_duration', return_value=6), patch('core.browsing.visiting.ffmpeg_executable', return_value=None):
            self.assertFalse(await visit.tick(self.store))
        self.assertFalse((await self.store.state()).get('live_seconds'))

    async def test_live_counts_only_decoded_actual_clip(self):
        visit=await self.live()
        with patch('core.browsing.visiting.capture_live', AsyncMock(return_value=8)), patch('core.browsing.visiting.ffprobe_executable', return_value='ffprobe'), patch('core.browsing.visiting._video_duration', return_value=3), patch('core.browsing.visiting.ffmpeg_executable', return_value='ffmpeg'), patch('core.browsing.visiting._extract_frame', return_value=True):
            result=await visit.tick(self.store)
        self.assertEqual(result['seconds'], 3)
        self.assertEqual((await self.store.state())['live_seconds'],3)
        self.assertTrue(result['evidence'])


class PlatformTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader=BiliReader(lambda: {'SESSDATA':'fake','bili_jct':'a'*32})
        self.reader.require_login=AsyncMock(return_value='1234')
        self.reader._account=await self.reader._account_key()
        self.reader._key='a'*32
        self.reader._key_at=time.time()
        self.reader._request=AsyncMock(return_value={})
        self.platform=BiliPlatform(self.reader,'1234')

    async def test_account_change_blocks_reads_and_writes(self):
        self.reader.require_login.return_value='5678'
        with self.assertRaises(BiliReadError):
            await self.platform.get('/x/test')
        with self.assertRaises(BiliReadError):
            await self.platform.dm('9999','你好','device')
        self.reader._request.assert_not_awaited()

    async def test_csrf_and_account_snapshot_used_for_post(self):
        await self.platform.dm('5678','你好','device')
        call=self.reader._request.await_args
        self.assertEqual(call.kwargs['method'],'POST')
        self.assertEqual(call.kwargs['data']['csrf'],'a'*32)
        self.assertEqual(call.kwargs['expected_account'],self.reader._account)
        self.assertEqual(json.loads(call.kwargs['data']['msg[content]'])['content'],'你好')

    async def test_invalid_csrf_never_sends(self):
        self.reader.cookies=lambda:{'SESSDATA':'fake'}
        with self.assertRaises(BiliReadError):
            await self.platform.dynamic('公开内容')
        self.reader._request.assert_not_awaited()

    async def test_dynamic_uses_json_and_signed_csrf(self):
        self.reader.sign_values=lambda p:{**p,'w_rid':'signed'}
        await self.platform.dynamic('公开内容')
        call=self.reader._request.await_args
        self.assertEqual(call.kwargs['json_body']['dyn_req']['content']['contents'][0]['raw_text'],'公开内容')
        self.assertEqual(call.args[1]['w_rid'],'signed')

    async def test_nontext_dm_still_supplies_pagination_cursor(self):
        self.reader._request.return_value={'messages':[{'msg_type':2,'msg_seqno':77,'timestamp':99}], 'has_more':1}
        page=await self.platform.dm_page('5678')
        self.assertEqual(page, {'rows':[],'more':True,'oldest':99,'before':'77'})
        self.assertIn('w_rid', self.reader._request.await_args.args[1])

    async def test_notification_cursor_uses_kind_timestamp(self):
        self.reader._request.return_value={'items':[{'id':'8'}], 'cursor':{'id':8,'time':99,'is_end':False}}
        page = await self.platform.notification_page('at',cursor={'id':'9','time':100})
        self.assertEqual(page['cursor'], {'id':'8','time':99})
        params = self.reader._request.await_args.args[1]
        self.assertEqual((params['id'], params['at_time']), ('9',100))

    async def test_reply_lookup_matches_sender_parent_and_notification_time(self):
        self.platform.comments=AsyncMock(return_value=[{'id':'11','uid':'5678','parent':'9','text':'真正新回复','at':100}, {'id':'12','uid':'5678','parent':'8','text':'另一句','at':100}])
        self.assertEqual((await self.platform.notification_comment('42','1','9','5678',100))['id'],'11')
        self.assertIsNone(await self.platform.notification_comment('42','1','9','5678',500))

    async def test_ambiguous_reply_is_never_guessed(self):
        self.platform.comments=AsyncMock(return_value=[{'id':str(i),'uid':'5678','parent':'9','text':'同一秒','at':100} for i in (11,12)])
        self.assertIsNone(await self.platform.notification_comment('42','1','9','5678',100))

    async def test_following_feed_normalizes_creator_and_duration(self):
        self.reader._request.return_value={'items':[{'modules':{'module_author':{'mid':5678,'name':'UP主'},'module_dynamic':{'major':{'archive':{'bvid':BV,'aid':'42','title':'新视频','duration_text':'03:20'}}}}}]}
        rows=[video(item, '关注更新') for item in await self.reader.following_feed()]
        self.assertEqual((rows[0]['author_id'],rows[0]['duration']),('5678',200))

    async def test_live_untrusted_url_and_picture_never_requested(self):
        for url in ('http://127.0.0.1/a','https://localhost/a','https://evil.test/a','https://api.bilibili.com:444/a'):
            with self.assertRaises(BiliReadError):
                await public_bytes(url)
            with self.assertRaises(BiliReadError):
                await capture_live(url,Path('/tmp/not-created.flv'))


class VisionTest(unittest.IsolatedAsyncioTestCase):
    def test_snapshot_units_and_invalid_numbers(self):
        self.assertEqual(snapshot_entries({'index':[0,10000,30000]},60),[(0,0),(10,1),(30,2)])
        self.assertEqual(snapshot_entries({'index':[0,10,30,float('nan'),-1]},60),[(0,0),(10,1),(30,2)])

    async def test_storyboard_only_uses_current_window_and_correct_tile(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            img=Image.new('RGB',(64,32),'red'); img.paste('blue',(32,0,64,32))
            raw=io.BytesIO();img.save(raw,'PNG')
            reader=types.SimpleNamespace(_request=AsyncMock(return_value={'index':[0,30], 'image':['https://i.hdslb.com/a'], 'img_x_size':32,'img_y_size':32}))
            record={'bvid':BV,'cid':8,'page':1,'duration':60}
            with patch('core.browsing.storyboards.public_bytes',AsyncMock(return_value=raw.getvalue())):
                frames=await storyboard(reader,record,Path(folder),20,40)
            self.assertEqual([f.second for f in frames],[30])
            self.assertGreater(Image.open(frames[0].path).getpixel((10,10))[2],200)
            self.assertEqual(await storyboard(reader,{**record,'page':2},Path(folder),0,60),[])

    def test_storyboard_out_of_bounds_is_rejected(self):
        from PIL import Image
        raw=io.BytesIO();Image.new('RGB',(32,32)).save(raw,'PNG')
        with self.assertRaises(ValueError):
            tile(raw.getvalue(),3,32,32,Path('/tmp/not-created.jpg'))

    def test_new_settings_have_bounded_resource_limits(self):
        settings=BiliBrowseSettings.from_dict({'max_daily_interactions':999,'max_live_minutes':-1})
        self.assertEqual((settings.max_daily_interactions,settings.max_live_minutes),(60,1))
        self.assertTrue(settings.enhanced_vision and settings.dm_enabled and settings.dynamics_enabled)


class StreamingTest(unittest.IsolatedAsyncioTestCase):
    def test_hls_master_and_media_parse_real_urls_and_init(self):
        base = 'https://a.bilivideo.com/live/index.m3u8'
        master = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nlow/index.m3u8\n'
        variants, _, _ = hls_parts(master, base)
        self.assertEqual(variants,[(1000,'https://a.bilivideo.com/live/low/index.m3u8')])
        media = b'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:3,\na.m4s\n#EXTINF:3,\nb.m4s\n'
        _, segments, init = hls_parts(media,base)
        self.assertEqual(init,'https://a.bilivideo.com/live/init.mp4')
        self.assertEqual(segments[-1],('https://a.bilivideo.com/live/b.m4s',3))

    async def test_hls_capture_reads_only_latest_bounded_segments(self):
        base = 'https://a.bilivideo.com/live/index.m3u8'
        media = b'#EXTM3U\n#EXTINF:4,\nold.ts\n#EXTINF:4,\na.ts\n#EXTINF:4,\nb.ts\n'
        async def read(url, limit):
            return media if url.endswith('.m3u8') else b'x'*2048
        with tempfile.TemporaryDirectory() as folder, patch('core.browsing.visiting.live_bytes', AsyncMock(side_effect=read)) as fetch:
            path = Path(folder)/'clip.ts'
            self.assertEqual(await capture_hls(base,path,8),8)
            self.assertEqual(path.stat().st_size,4096)
            self.assertEqual([c.args[0] for c in fetch.await_args_list], [base,base.replace('index.m3u8','a.ts'),base.replace('index.m3u8','b.ts')])

    def test_hls_does_not_silently_ignore_encryption_or_byte_ranges(self):
        for tag in ('#EXT-X-KEY:METHOD=AES-128,URI="key"', '#EXT-X-BYTERANGE:100@0'):
            with self.assertRaises(BiliReadError):
                hls_parts(('#EXTM3U\n'+tag+'\n').encode(),'https://a.bilivideo.com/live.m3u8')
