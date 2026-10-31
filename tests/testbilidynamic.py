"""图文动态使用临时图片、SQLite 和模拟平台；不访问真实账号。"""
import asyncio
import io
import types
import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager

import support
import testbilisocial as fixtures
from PIL import Image
from core.browsing.dynamic import DynamicPublisher, dynamic_matches, prepare_picture
from core.browsing.platform import BiliPlatform, BiliWriteUncertain
from core.clock import local_timestamp
from core.runtime.channel.image import RuntimeImageMediaComponent


class DynamicTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.SocialTest.asyncSetUp
    asyncTearDown = fixtures.SocialTest.asyncTearDown
    video = fixtures.SocialTest.video

    def picture(self, name="image.png", color="red"):
        path = Path(self.temp.name) / name
        Image.new("RGB", (24, 32), color).save(path)
        return str(path)

    async def publish(self, images=None, guard=None):
        self.platform.upload_dynamic_picture = getattr(self.platform, 'upload_dynamic_picture', AsyncMock(
            return_value={"img_src":"https://i0.hdslb.com/bfs/new_dyn/test.jpg", "img_width":24,"img_height":32}))
        with patch('core.browsing.dynamic.BiliPlatform', return_value=self.platform):
            return await DynamicPublisher(self.social).publish(self.store, {
                'intent':'test-intent', 'source_id':'source', 'text':'这张是今天的创作', 'images':images or []}, guard or AsyncMock())

    async def test_upload_then_publish_and_restart_dedup(self):
        images = [self.picture()]
        sent = await self.publish(images)
        self.assertEqual(sent['status'], 'accepted')
        self.assertEqual(len(sent['pictures']), 1)
        self.platform.dynamic.assert_awaited_once_with('这张是今天的创作', pictures=sent['pictures'])
        await self.publish(images)
        self.platform.dynamic.assert_awaited_once()
        self.platform.upload_dynamic_picture.assert_awaited_once()

    async def test_invalid_second_picture_prevents_all_uploads(self):
        with self.assertRaises(ValueError):
            await self.publish([self.picture(), str(Path(self.temp.name)/'missing.png')])
        self.platform.dynamic.assert_not_awaited()
        self.platform.upload_dynamic_picture.assert_not_awaited()
        self.assertEqual(await self.store.records(), [])

    async def test_image_upload_failure_does_not_fall_back_to_text(self):
        self.platform.upload_dynamic_picture = AsyncMock(side_effect=BiliWriteUncertain('upload timeout'))
        result = await self.publish([self.picture()])
        self.assertEqual((result['status'], result['phase']), ('rejected','preparing'))
        self.platform.dynamic.assert_not_awaited()

    async def test_partial_uploads_resume_without_reuploading_first_picture(self):
        first = {'img_src':'https://i0.hdslb.com/bfs/new_dyn/one.jpg','img_width':24,'img_height':32}
        second = {**first,'img_src':'https://i0.hdslb.com/bfs/new_dyn/two.jpg'}
        images = [self.picture(), self.picture('second.png','blue')]
        self.platform.upload_dynamic_picture = AsyncMock(side_effect=[first, BiliWriteUncertain('upload timeout')])
        rejected = await self.publish(images)
        self.assertEqual(rejected['pictures'], [first])
        await self.store.finish(rejected, retry_after=0)
        self.platform.upload_dynamic_picture.side_effect = [second]
        result = await self.publish(images)
        self.assertEqual(result['pictures'], [first, second])
        self.assertEqual(self.platform.upload_dynamic_picture.await_count, 3)
        self.platform.dynamic.assert_awaited_once()

    async def test_cancel_during_upload_can_resume_but_cancel_during_publish_cannot_resend(self):
        images = [self.picture()]
        self.platform.upload_dynamic_picture = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.publish(images)
        row = (await self.store.pending(['sending']))[0]
        self.assertEqual(row['phase'], 'preparing')
        self.platform.upload_dynamic_picture.side_effect = None
        self.platform.upload_dynamic_picture.return_value = {'img_src':'https://i0.hdslb.com/bfs/new_dyn/a.jpg','img_width':24,'img_height':32}
        self.platform.dynamic.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.publish(images)
        self.assertEqual((await self.store.get(row['id']))['status'], 'uncertain')
        await self.publish(images)
        self.platform.dynamic.assert_awaited_once()

    async def test_uncertain_dynamic_requires_same_images_to_confirm(self):
        images = [self.picture()]
        self.platform.dynamic.side_effect = BiliWriteUncertain('post timeout')
        with self.assertRaises(BiliWriteUncertain):
            await self.publish(images)
        row = (await self.store.pending(['uncertain']))[0]
        remote = {'id':'99','at':int(local_timestamp(self.now)), 'text':row['text'], 'pictures':[]}
        self.platform.dynamic_page.return_value = {'rows':[remote],'offset':''}
        await self.social.reconcile(self.store)
        self.assertEqual((await self.store.get(row['id']))['status'], 'uncertain')
        remote['pictures'] = [row['pictures'][0]['img_src']]
        await self.social.reconcile(self.store)
        self.assertEqual((await self.store.get(row['id']))['status'], 'confirmed')

    async def test_prepare_phase_is_not_mistaken_for_submitted_dynamic(self):
        self.platform.upload_dynamic_picture = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.publish([self.picture()])
        await self.social.reconcile(self.store)
        self.platform.dynamic_page.assert_not_awaited()

    async def test_guard_changed_after_upload_prevents_publishing(self):
        calls = 0
        async def guard():
            nonlocal calls
            calls += 1
            if calls > 2:
                raise ValueError('已关闭动态')
        result = await self.publish([self.picture()], guard)
        self.assertEqual(result['phase'], 'preparing')
        self.platform.dynamic.assert_not_awaited()

    async def test_uploaded_file_change_does_not_replace_pending_image(self):
        paths = [self.picture()]
        self.platform.upload_dynamic_picture = AsyncMock(side_effect=BiliWriteUncertain('timeout'))
        row = await self.publish(paths)
        await self.store.finish(row, retry_after=0)
        self.picture(color='blue')
        with self.assertRaises(ValueError):
            await self.publish(paths)
        self.platform.dynamic.assert_not_awaited()

    async def test_successful_network_write_is_not_repeated_if_local_receipt_write_fails(self):
        finish = self.store.finish
        async def fail(record, **changes):
            if changes.get('status') == 'accepted':
                raise ValueError('disk unavailable')
            return await finish(record, **changes)
        with patch.object(self.store, 'finish', side_effect=fail):
            with self.assertRaises(ValueError):
                await self.publish()
        result = await self.publish()
        self.assertEqual(result['status'], 'sending')
        self.platform.dynamic.assert_awaited_once()

    def install_delegate(self, draft):
        plugin = types.SimpleNamespace(config={'enable_auto_share':True}, bili_dynamic_enabled=lambda:True,
            prepare_bili_dynamic=AsyncMock(return_value=draft), record_bili_dynamic_receipt=AsyncMock())
        self.runtime.context = types.SimpleNamespace(get_all_stars=lambda:[types.SimpleNamespace(
            root_dir_name='astrbot_plugin_daily_share', activated=True, star_cls=plugin)])
        return plugin

    async def test_daily_share_is_only_dynamic_decision_owner(self):
        await self.video()
        delegate = self.install_delegate({'status':'wait'})
        await self.social.tick(self.day, self.world)
        delegate.prepare_bili_dynamic.assert_awaited_once()
        self.assertNotIn('dynamic', self.browse._model.await_args.args[1]['allowed_actions'])
        opportunity = delegate.prepare_bili_dynamic.await_args.args[0]
        self.assertEqual(set(opportunity), {'account','persona','materials','assets','recent_public_actions'})
        self.assertNotIn('私密约定', str(opportunity))

    async def test_delegate_image_draft_reaches_upload_and_actual_publisher(self):
        record = await self.video()
        delegate = self.install_delegate({'status':'ready','intent':'draft', 'source_id':record['id'],
                                         'text':'这个实验让我想到了这幅创作', 'images':[self.picture()]})
        self.platform.upload_dynamic_picture = AsyncMock(return_value={'img_src':'https://i0.hdslb.com/bfs/new_dyn/a.jpg','img_width':24,'img_height':32})
        with patch('core.browsing.dynamic.BiliPlatform', return_value=self.platform):
            await self.social.tick(self.day, self.world)
        self.platform.dynamic.assert_awaited_once()
        self.browse._model.assert_not_awaited()
        self.assertEqual(delegate.record_bili_dynamic_receipt.await_args.args[2]['status'],'accepted')

    async def test_private_and_unmarked_images_are_not_shared(self):
        self.db.get_living_entities = AsyncMock(return_value=[
            {'id':'private','scope':'private:a','visibility':'public','state':'available','resource':{'media_kind':'image','artifacts':[self.picture()]}},
            {'id':'global','scope':'global','state':'available','resource':{'media_kind':'image','artifacts':[self.picture()]}},
        ])
        opportunity, _ = await self.social.dynamic_sharing.opportunity(self.store, self.day, [], '角色', [])
        self.assertEqual(opportunity['assets'], {})
        self.assertEqual(opportunity['materials'], [])

    async def test_forged_source_is_not_published(self):
        delegate = self.install_delegate({'status':'ready','intent':'fake','source_id':'not-viewed','text':'假的经历','images':[]})
        with patch('core.browsing.dynamic.BiliPlatform', return_value=self.platform):
            await self.social.tick(self.day, self.world)
        self.platform.dynamic.assert_not_awaited()

    async def test_outbound_choice_moves_to_share_but_favorite_and_passive_dm_stay_local(self):
        owner = self.install_delegate({'status':'wait'})
        owner.bili_interaction_enabled = lambda:True
        owner.decide_bili_interaction = AsyncMock(return_value={'action':'contact','id':'contact'})
        context = {'allowed_actions':['wait','favorite','dm','contact','comment']}
        self.browse._model.return_value = {'action':'interact'}
        result, chosen_owner = await self.social._social_decision(context, {'kind':'dm_in'})
        local = self.browse._model.await_args.args[1]['allowed_actions']
        self.assertEqual(local, ['wait','favorite','dm','interact'])
        self.assertEqual(owner.decide_bili_interaction.await_args.args[0]['allowed_actions'], ['wait','contact','comment'])
        self.assertEqual(result['action'],'contact')
        self.assertIs(chosen_owner,owner)
        owner.decide_bili_interaction.reset_mock()
        self.browse._model.return_value = {'action':'dm','id':'incoming','text':'回复站内来信'}
        result, chosen_owner = await self.social._social_decision(context, {'kind':'dm_in'})
        self.assertEqual(result['action'],'dm')
        owner.decide_bili_interaction.assert_not_awaited()
        self.assertIsNone(chosen_owner)

    async def test_life_model_cannot_bypass_shared_decision_owner(self):
        owner = self.install_delegate({'status':'wait'})
        owner.bili_interaction_enabled = lambda:True
        owner.decide_bili_interaction = AsyncMock()
        self.browse._model.return_value = {'action':'comment','id':'video','text':'越权草稿'}
        result, _ = await self.social._social_decision({'allowed_actions':['wait','comment']},None)
        self.assertEqual(result,{'action':'wait'})
        owner.decide_bili_interaction.assert_not_awaited()

    async def test_shared_mention_uses_validated_binding_and_public_expression(self):
        record = await self.video()
        await self.store.update({'bindings':{'private:a':{'uid':'5678'}}})
        owner = self.install_delegate({'status':'wait'})
        owner.bili_interaction_enabled = lambda:True
        owner.decide_bili_interaction = AsyncMock(side_effect=[
            {'action':'mention','id':record['id']}, {'action':'mention','text':'这个实验想和你一起看看'}])
        self.browse._model.return_value = {'action':'interact'}
        await self.social.tick(self.day,self.world)
        self.platform.comment.assert_awaited_once()
        self.assertEqual(self.platform.comment.await_args.kwargs['mention']['uid'],'5678')
        expression = owner.decide_bili_interaction.await_args
        self.assertTrue(expression.kwargs['expression'])
        self.assertNotIn('bili_dialogue',expression.args[0])

    async def test_disabling_share_before_transport_cancels_outbound_action(self):
        record = await self.video()
        owner = self.install_delegate({'status':'wait'})
        owner.bili_interaction_enabled = lambda:True
        owner.config['enable_auto_share'] = False
        with self.assertRaises(ValueError):
            await self.social.act('comment', record=record,text='已过时的草稿',decision_owner=owner)
        self.platform.comment.assert_not_awaited()


class DynamicProtocolTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.PlatformTest.asyncSetUp

    async def test_upload_is_authenticated_multipart(self):
        result = await asyncio.to_thread(subprocess.run, [sys.executable, str(Path(__file__).with_name('bilidynamichttpcheck.py'))],
                                         capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    async def test_image_dynamic_uses_scene_two_and_picture_receipts(self):
        self.reader._request.return_value = {'dyn_id_str':'99'}
        pictures = [{'img_src':'https://i0.hdslb.com/bfs/new_dyn/a.jpg','img_width':24,'img_height':32}]
        result = await self.platform.dynamic('图文', pictures=pictures)
        request = self.reader._request.await_args.kwargs['json_body']['dyn_req']
        self.assertEqual((request['scene'],request['pics']), (2,pictures))
        self.assertEqual(result['remote_id'],'99')

    async def test_missing_remote_id_is_uncertain(self):
        self.reader._request.return_value = {}
        with self.assertRaises(BiliWriteUncertain):
            await self.platform.dynamic('文字')

    async def test_unsafe_upload_receipt_never_enters_dynamic(self):
        self.platform.post = AsyncMock(return_value={'image_url':'https://evil.test/a.jpg','image_width':1,'image_height':1})
        with self.assertRaises(ValueError):
            await self.platform.upload_dynamic_picture({'data':b'jpeg'})

    async def test_dynamic_page_exposes_actual_pictures(self):
        self.reader._request.return_value = {'items':[{'id_str':'1','modules':{
            'module_author':{'pub_ts':42}, 'module_dynamic':{'desc':{'text':'图文'},
            'major':{'draw':{'items':[{'src':'https://i0.hdslb.com/bfs/a.jpg'}]}}}}}]}
        result = await self.platform.dynamic_page()
        self.assertEqual(result['rows'][0]['pictures'], ['https://i0.hdslb.com/bfs/a.jpg'])

    async def test_dynamic_page_reads_opus_text_and_pictures_from_live_shape(self):
        # Real B站 readback: legacy desc/draw can be null after a successful image post.
        url = 'http://i0.hdslb.com/bfs/new_dyn/verified.jpg'
        self.reader._request.return_value = {'items':[{'id_str':'99','modules':{
            'module_author':{'pub_ts':42}, 'module_dynamic':{'desc':None,
            'major':{'type':'MAJOR_TYPE_OPUS','draw':None,'opus':{
                'summary':{'text':'小熊拖鞋踩在地上 走两步就想笑\n','has_more':False},
                'pics':[{'url':url,'width':941,'height':1672}]}}}}}],
            'has_more':True,'offset':'next'}
        result = await self.platform.dynamic_page(offset='previous')
        row = result['rows'][0]
        self.assertEqual(row, {'id':'99','at':42,'text':'小熊拖鞋踩在地上 走两步就想笑','pictures':[url]})
        self.assertEqual(result['offset'], 'next')
        params = self.reader._request.await_args.args[1]
        self.assertEqual(params['features'], 'itemOpusStyle')
        self.assertEqual(params['offset'], 'previous')
        journal = {'text':row['text'],'pictures':[{'img_src':url}]}
        self.assertTrue(dynamic_matches(journal,row))
        self.assertFalse(dynamic_matches(journal,{**row,'pictures':[]}))
        self.assertFalse(dynamic_matches(journal,{**row,'text':''}))

    async def test_dynamic_page_reads_text_only_opus_and_legacy_posts(self):
        self.reader._request.return_value = {'items':[
            {'id_str':'1','modules':{'module_dynamic':{'desc':None,'major':{'opus':{
                'summary':{'text':'今晚的生活片段\n'},'pics':[]}}}}},
            {'id_str':'2','modules':{'module_dynamic':{'desc':{'text':'旧版纯文字'},'major':None}}}]}
        result = await self.platform.dynamic_page()
        self.assertEqual([(r['text'],r['pictures']) for r in result['rows']],
                         [('今晚的生活片段',[]),('旧版纯文字',[])])

    async def test_public_generation_uses_no_private_director_context(self):
        @asynccontextmanager
        async def lease():
            yield
        host = types.SimpleNamespace(runtime_service_lease=lease,
            _character_appearance_profile=AsyncMock(return_value='角色稳定体貌'),
            _life_character_reference_image=lambda:'reference.png',
            _directed_life_image_result=AsyncMock(side_effect=AssertionError('不能读取私聊导演上下文')),
            media=types.SimpleNamespace(image=types.SimpleNamespace(
                generate_image=AsyncMock(return_value='scene'), edit_image=AsyncMock(return_value='self'))))
        component = RuntimeImageMediaComponent(host)
        self.assertEqual(await component.generate_life_image_asset(None,'公开场景', public_only=True), 'scene')
        self.assertEqual(await component.generate_life_image_asset(None,'当前实际穿搭', public_only=True, contains_character=True), 'self')
        host._directed_life_image_result.assert_not_awaited()
        self.assertFalse(host.media.image.generate_image.await_args.kwargs['include_character_reference'])
        self.assertEqual(host.media.image.edit_image.await_args.args[1], 'reference.png')
