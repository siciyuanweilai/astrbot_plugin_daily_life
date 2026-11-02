import json
import tempfile
import unittest
from pathlib import Path

from support import LifeArchive
from core.addresses import distinct_address, settle_batch_addresses


class RelationshipAddressTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'life.db'
        self.db=LifeArchive(self.path)
        self.scope='qq:FriendMessage:42'
        self.rows=[]
        await self.db.touch_relationship('42',name='Sylus',alias='Sylus',platform='qq',user_id='42',note='共同经历')

    async def asyncTearDown(self):
        self.db.close();self.temp.cleanup()

    async def message(self, text, *, role='user', profile='42', quoted=False, platform='qq', at=None):
        n=len(self.rows)+1
        row=dict(event_key=f'e{n}',session_id=self.scope,message_id=f'm{n}',role=role,
                 sender_profile_id=profile,sender_name='Sylus',user_id=profile,platform=platform,
                 is_quoted=quoted,message_text=text,occurred_at=at or f'2026-11-02T01:00:{n:02d}')
        row['id'],_=await self.db.enqueue_chat_memory_message(row)
        self.rows.append(row)
        return row

    def update(self,row,name='',operation='set',**kwargs):
        return dict(profile_id='42',operation=operation,name=name,basis='user_request',
                    source_message_id=row['message_id'],evidence=row['message_text'],
                    address_source_message_id=row['message_id'])|kwargs

    async def apply(self,update):
        return await self.db.apply_relationship_address(update,session_id=self.scope,rows=self.rows)

    async def test_set_change_clear_restart_and_replay(self):
        a=await self.message('以后叫我阿夜吧。')
        first=self.update(a,'阿夜')
        self.assertEqual(await self.apply(first),'applied')
        self.assertEqual((await self.db.get_relationship('42')).subjective_name,'阿夜')
        b=await self.message('现在我更喜欢你叫我小夜。')
        self.assertEqual(await self.apply(self.update(b,'小夜')),'applied')
        c=await self.message('不用特别的称呼了，按昵称叫我就好。')
        self.assertEqual(await self.apply(self.update(c,operation='clear')),'applied')
        self.db.close();self.db=LifeArchive(self.path)
        self.assertEqual(await self.apply(first),'stale')
        person=await self.db.get_relationship('42')
        self.assertEqual(person.subjective_name,'')
        self.assertEqual((person.name,person.interactions),('Sylus',1))
        self.assertEqual(person.notes[0].content,'共同经历')

    async def test_same_nickname_has_no_redundant_value(self):
        row=await self.message('就叫我Sylus。')
        self.assertEqual(await self.apply(self.update(row,'Sylus')),'applied')
        self.assertEqual((await self.db.get_relationship('42')).subjective_name,'')
        self.assertEqual(distinct_address(' ＳＹＬＵＳ ','Sylus'),'')

    async def test_generic_looking_address_is_allowed_when_user_actually_wants_it(self):
        row=await self.message('我就想让你叫我这位群友，这是我的选择。')
        self.assertEqual(await self.apply(self.update(row,'这位群友')),'applied')
        self.assertEqual((await self.db.get_relationship('42')).subjective_name,'这位群友')

    async def test_model_or_calibration_cannot_write_unverified_address(self):
        await self.db.touch_relationship('42',name='Sylus',subjective_name='虚构外号')
        await self.db.revise_relationship_profile('42',subjective_name='另一个外号',relationship_story='真实关系印象')
        person=await self.db.get_relationship('42')
        self.assertEqual(person.subjective_name,'')
        self.assertEqual(person.relationship_story,'真实关系印象')

    async def test_verified_address_survives_unrelated_profile_updates(self):
        row=await self.message('叫我阿夜吧。')
        await self.apply(self.update(row,'阿夜'))
        await self.db.touch_relationship('42',name='Sylus',subjective_name='模型的称呼')
        await self.db.revise_relationship_profile('42',subjective_name='另一个称呼',note='新的共同经历')
        self.assertEqual((await self.db.get_relationship('42')).subjective_name,'阿夜')

    async def test_database_failure_rolls_back_both_identity_and_preference(self):
        import sqlite3
        row=await self.message('叫我阿夜吧。',profile='new')
        await self.db._run_db(lambda:self.db._conn.execute("CREATE TRIGGER fail_address BEFORE UPDATE OF address_basis ON relationships BEGIN SELECT RAISE(ABORT, 'test'); END"))
        with self.assertRaises(sqlite3.IntegrityError):
            await self.apply(self.update(row,'阿夜',profile_id='new'))
        self.assertIsNone(await self.db.get_relationship('new'))

    async def test_accepted_use_requires_user_evidence_and_prior_actual_address(self):
        named=await self.message('那我叫你阿夜，可以吗？',role='assistant',profile='bot')
        user=await self.message('可以，我喜欢这个叫法。')
        item=self.update(user,'阿夜',basis='accepted_use',address_source_message_id=named['message_id'])
        self.assertEqual(await self.apply(item),'applied')
        self.assertEqual((await self.db.get_relationship('42')).subjective_name,'阿夜')

    async def test_other_users_quotes_assistant_and_platform_do_not_establish_preference(self):
        for kwargs in ({'profile':'99'},{'quoted':True},{'role':'assistant'},{'platform':'other'}):
            with self.subTest(kwargs=kwargs):
                row=await self.message('叫我阿夜吧。',**kwargs)
                self.assertNotEqual(await self.apply(self.update(row,'阿夜')),'applied')
        self.assertEqual((await self.db.get_relationship('42')).subjective_name,'')

    async def test_forged_or_absent_source_and_name_are_rejected(self):
        row=await self.message('叫我阿夜吧。')
        for changes in ({'evidence':'伪造原话'},{'source_message_id':'missing'},
                        {'address_source_message_id':'missing'},{'name':'未说过的称呼'},
                        {'basis':'model_guess'},{'operation':[]}, {'name':''}):
            with self.subTest(changes=changes):
                self.assertNotEqual(await self.apply(self.update(row,'阿夜')|changes),'applied')

    async def test_older_imported_message_cannot_replace_current_preference(self):
        current=await self.message('叫我阿夜吧。')
        await self.apply(self.update(current,'阿夜'))
        old=await self.message('叫我小夜。',at='2026-10-01T00:00:00')
        self.assertEqual(await self.apply(self.update(old,'小夜')),'stale')

    async def test_current_batch_and_session_bound_evidence(self):
        row=await self.message('叫我阿夜吧。')
        for scope,rows in ((self.scope,[]),('qq:FriendMessage:99',self.rows)):
            self.assertNotEqual(await self.db.apply_relationship_address(self.update(row,'阿夜'),session_id=scope,rows=rows),'applied')

    async def test_batch_address_updates_work_without_memory_targets(self):
        row=await self.message('叫我阿夜吧。')
        await settle_batch_addresses(self.db,{'worth_saving':False,'address_updates':[self.update(row,'阿夜')]},
                                     {'session_id':self.scope,'messages':self.rows})
        person=await self.db.get_relationship('42')
        self.assertEqual(person.subjective_name,'阿夜')
        self.assertNotIn('address_basis',person.as_dict())
        self.assertEqual(person.relationship_state['observed_dimensions'],[])

    async def test_first_message_can_establish_preference_without_inventing_identity(self):
        row=await self.message('叫我阿夜吧。',profile='new')
        self.assertEqual(await self.apply(self.update(row,'阿夜',profile_id='new')),'applied')
        person=await self.db.get_relationship('new')
        self.assertEqual((person.name,person.user_id,person.subjective_name),('Sylus','new','阿夜'))

    async def test_legacy_migration_archives_unverified_values_and_preserves_history(self):
        before=(await self.db.get_relationship('42')).as_dict()
        self.db.close()
        import sqlite3
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE relationships SET subjective_name='旧称呼'")
            conn.execute('ALTER TABLE relationships DROP COLUMN address_basis')
            conn.execute("UPDATE meta SET value='33' WHERE key='schema_version'")
        conn.close()
        self.db=LifeArchive(self.path)
        after=(await self.db.get_relationship('42')).as_dict()
        self.assertEqual(before,after)
        with sqlite3.connect(self.path) as conn:
            proof=json.loads(conn.execute("SELECT address_basis FROM relationships WHERE id='42'").fetchone()[0])
            self.assertEqual(proof,{'legacy_unverified':'旧称呼'})
        conn.close()
        self.db.close();self.db=LifeArchive(self.path)
        self.assertEqual(after,(await self.db.get_relationship('42')).as_dict())
