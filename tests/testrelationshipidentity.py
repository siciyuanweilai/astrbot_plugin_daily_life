"""身份取消息来源，习惯称呼不改写身份，也不能用来匹配另一个人。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import LifeArchive
from core.archive.restoration import restore_observed_relationship_names
from core.identity import relationship_for_contact
from core.memos.conduit import MemosComponent
from core.interface.view import PageViewComponent
from core.runtime.capture.portrait import ImprintPortraitComponent
from core.runtime.capture.batch import ChatMemoryBatchComponent
from core.runtime.voicecall.manager import VoiceCallManager
from core.runtime.voicecall.videocall import RtcVoiceCallManager


class RelationshipIdentityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'life.db'
        self.db = LifeArchive(self.path)
        self.runtime = SimpleNamespace(archive=self.db,
            _list_payload=lambda value: value if isinstance(value,list) else [],
            _str_payload=lambda value,default='': str(value or default).strip(),
            _relationship_contact_payload=lambda meta: {'contact_type':'group_member','group_id':meta['group_id']},
            _calibrate_relationship_profile=AsyncMock())
        self.meta = dict(sender_profile_id='42',sender_name='Sylus',platform='qq',user_id='42',
                         group_id='group',date='2026-11-02',session_id='qq:GroupMessage:group')

    async def asyncTearDown(self):
        self.db.close()
        self.temp.cleanup()

    async def test_model_name_cannot_override_observed_name(self):
        target={'profile_id':'42','name':'这位群友','alias':'模型虚构昵称','subjective_name':'偶尔聊工具的朋友','note':'共同聊过工具'}
        saved=await ImprintPortraitComponent(self.runtime)._save_memory_targets({'memory_targets':[target]},self.meta)
        record=await self.db.get_relationship('42')
        self.assertEqual((record.name,record.alias),('Sylus','Sylus'))
        self.assertEqual(record.subjective_name,'')
        self.assertEqual((saved[0]['name'],saved[0]['alias']),('Sylus','Sylus'))

    async def test_observed_rename_replaces_legacy_name_without_keyword_list(self):
        await self.db.touch_relationship('42',name='任意旧称呼',alias='任意旧称呼')
        target={'profile_id':'42','name':'完全不像泛称的假名','note':'今天又聊了几句'}
        await ImprintPortraitComponent(self.runtime)._save_memory_targets({'memory_targets':[target]},self.meta)
        self.meta['sender_name']='新昵称'
        await ImprintPortraitComponent(self.runtime)._save_memory_targets({'memory_targets':[target]},self.meta)
        record=await self.db.get_relationship('42')
        self.assertEqual((record.name,record.alias),('新昵称','新昵称'))

    async def test_missing_sender_name_uses_id_instead_of_model_name(self):
        self.meta['sender_name']=''
        await ImprintPortraitComponent(self.runtime)._save_memory_targets({'memory_targets':[
            {'profile_id':'42','name':'编造的姓名','note':'真实参与，但暂无昵称'}]},self.meta)
        self.assertEqual((await self.db.get_relationship('42')).name,'42')

    async def test_unknown_person_is_not_created_from_model_identity(self):
        await ImprintPortraitComponent(self.runtime)._save_memory_targets({'memory_targets':[
            {'profile_id':'other','name':'别人的昵称','note':'猜测'}, {'name':'猜测的人','note':'猜测'}]},self.meta)
        self.assertEqual(await self.db.get_recent_relationships(10),[])

    async def test_missing_nickname_preserves_previously_observed_identity(self):
        await self.db.touch_relationship('qq42', name='Sylus', alias='Sylus', user_id='42')
        self.meta.update(sender_profile_id='qq42', sender_name='42')
        saved = await ImprintPortraitComponent(self.runtime)._save_memory_targets({'memory_targets':[
            {'profile_id':'qq42', 'name':'猜测的名字', 'note':'昵称暂不可用'}]}, self.meta)
        record = await self.db.get_relationship('qq42')
        self.assertEqual((record.name, record.alias), ('Sylus','Sylus'))
        self.assertEqual(saved[0]['name'], 'Sylus')
        await self.db.touch_relationship('qq42', name='42', alias='42', user_id='42', observed_name='42')
        record = await self.db.get_relationship('qq42')
        self.assertEqual((record.name, record.alias), ('Sylus','Sylus'))

    async def test_quoted_message_does_not_establish_sender_identity(self):
        writer = AsyncMock(return_value=[])
        component = ChatMemoryBatchComponent(SimpleNamespace(_save_memory_targets=writer))
        await component._save_batch_memory_targets({'memory_targets':[{'profile_id':'42'}]},
            {'messages':[{'id':'q1','sender_profile_id':'42','role':'user', 'is_quoted':True,
                          'sender_name':'引文中的名字'}]}, self.meta)
        writer.assert_not_awaited()

    async def test_legacy_repair_uses_actual_sender_and_preserves_history(self):
        await self.db.touch_relationship('42',name='这位群友',alias='这位群友',subjective_name='这位群友',
            platform='qq',user_id='42',note='历史关系',date_str='2026-11-01',contact_type='group_member',group_id='group')
        before=(await self.db.get_relationship('42')).as_dict()
        rows=[('1','Sylus','user','qq','42',0),('2','机器人','assistant','qq','42',0),
              ('3','其他平台的人','user','other','42',0),('4','他人','user','qq','99',0),
              ('5','引用昵称','user','qq','42',1)]
        for mid,name,role,platform,uid,quoted in rows:
            await self.db.enqueue_chat_memory_message(dict(event_key=mid,session_id='qq:GroupMessage:group',
                message_id=mid,role=role,sender_profile_id='42',sender_name=name,platform=platform,user_id=uid,
                is_quoted=quoted,message_text='内容',occurred_at='2026-11-02T00:00:0'+mid))
        await self.db._run_db(lambda:self.db._conn.execute("DELETE FROM meta WHERE key='relationship_observed_names_v1'"))
        self.db.close();self.db=LifeArchive(self.path)
        after=(await self.db.get_relationship('42')).as_dict()
        self.assertEqual((after.pop('name'),after.pop('alias')),('Sylus','Sylus'))
        before.pop('name');before.pop('alias')
        self.assertEqual(before,after)
        self.assertEqual(await self.db._run_db(restore_observed_relationship_names,self.db._conn),0)

    async def test_configured_alias_is_used_before_saved_name(self):
        view=PageViewComponent(SimpleNamespace(_is_generic_page_name=PageViewComponent._is_generic_page_name))
        resolver=SimpleNamespace(get_relationship_alias=lambda uid:'手动称呼' if uid=='42' else '',
                                 get_onebot_nickname=AsyncMock(return_value='平台昵称'))
        self.assertEqual(await view._resolve_relationship_display_name(
            {'id':'42','name':'Sylus','alias':'旧模型称呼','subjective_name':'群友'},resolver),'手动称呼')
        resolver.get_onebot_nickname.assert_not_awaited()

    async def test_page_does_not_promote_subjective_or_legacy_alias(self):
        view=PageViewComponent(SimpleNamespace(_is_generic_page_name=PageViewComponent._is_generic_page_name))
        for name in ('Sylus','42'):
            self.assertEqual(await view._resolve_relationship_display_name(
                {'id':'42','name':name,'alias':'模型称呼','subjective_name':'群友'},None),name)


class ContactIdentitySelectionTest(unittest.TestCase):
    def test_memory_projection_separates_person_name_and_subjective_name(self):
        memos = MemosComponent(SimpleNamespace())
        person = {'name':'Sylus', 'subjective_name':'这位群友', 'note':'一起讨论工具'}
        for key in ('speaker_relationship', 'memory_targets'):
            items = memos._memos_items_from_payload({}, saved_records={key:[person]})
            self.assertIn('关系画像：Sylus；习惯称呼：这位群友', items[0])

    def test_voice_and_video_use_ids_even_when_names_are_identical(self):
        people=[{'id':'wrong','name':'Sylus','subjective_name':'这位群友'},
                {'id':'42','name':'Sylus','subjective_name':'这位群友'}]
        for cls in (VoiceCallManager,RtcVoiceCallManager):
            selected=cls._match_current_relationship(people,user_id='42',user_name='Sylus',scope='qq:FriendMessage:42')
            self.assertIs(selected,people[1])
            self.assertEqual(cls._match_current_relationship(people,user_id='missing',user_name='Sylus',scope=''),{})

    def test_names_alone_or_ambiguous_ids_do_not_establish_identity(self):
        self.assertEqual(relationship_for_contact([{'name':'Sylus','subjective_name':'群友'}],user_id='',scope=''),{})
        self.assertEqual(relationship_for_contact([{'id':'42'},{'id':'42'}],user_id='42',scope=''),{})

    def test_verified_contact_can_resolve_different_platform_without_crossing_accounts(self):
        person={'id':'qq42','platform':'qq','user_id':'42','contacts':[
            {'platform':'wx','user_id':'wx42','target_scope':'wx:FriendMessage:wx42','contact_type':'friend'}]}
        self.assertIs(relationship_for_contact([person],user_id='wx42',scope='wx:FriendMessage:wx42'),person)
        self.assertEqual(relationship_for_contact([person],user_id='42',scope='wx:FriendMessage:42'),{})
        self.assertIs(relationship_for_contact([person],user_id='',scope='wx:FriendMessage:wx42'),person)


if __name__=='__main__':
    unittest.main()
