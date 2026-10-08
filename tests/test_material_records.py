"""Independent private material records, stale receipt guards and preserved drafts."""
from pathlib import Path
import tempfile
import unittest
from assistant_app.material_records import MaterialRecordsService
from assistant_app.journey import JourneyService, SEMANTICS
from assistant_app.store import AppError, Store, uid


class MaterialRecordsSmoke(unittest.TestCase):
    def test_independent_receipts_stale_and_private_preservation(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix='qa-material-records-', dir=Path(__file__).resolve().parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')", (aid,'fixture'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                service=MaterialRecordsService(store)
                with store.as_actor(actors[0]):
                    m=store.mutate('create',None,{'domain':'leave','goal_text':'工程手续记录验证，非真实人员或办理，未实际提交','auto_prepare':False},uid())
                    text='工程提交字段样例，未真实提交。\n工程回执字段样例，非真实回执；未批准或签发。\n工程假设航班变化，材料待核。'
                    m=store.mutate('source',m['id'],{'expected_version':m['version'],'kind':'user_text','text':text},uid());source=m['sources'][-1]
                    m=store.mutate('manual_artifact',m['id'],{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'本人旧稿不得覆盖；未实际办理'},uid());old_artifact=m['saved_artifact_id'];facts=m['facts'];tracks=m['tracks']
                    def payload(view,record_type='submission_statement',kind='visa',line=1):return {'expected_version':view['version'],'input_revision':view['revision_no'],'material_kind':kind,'record_type':record_type,'note':'工程路径验证，非真实办理或回执，未批准未签发','occurred_at':None,'ref':{'source_id':source['id'],'start_line':line,'end_line':line}}
                    before=service.get_records(m['id']);self.assertEqual(len(before['items']),5);self.assertTrue(all(item['submission']=='unknown' and item['receipt']=='unknown' for item in before['items']))
                    key=uid();data=payload(before);submitted=service.record(m['id'],data,key);self.assertEqual(service.record(m['id'],data,key),submitted)
                    visa=next(item for item in submitted['items'] if item['kind']=='visa');self.assertEqual(visa['submission'],'statement_recorded');self.assertEqual(visa['receipt'],'unknown')
                    self.assertTrue(all(item['receipt']=='unknown' for item in submitted['items']));self.assertEqual(store.detail(m['id'])['facts'],facts)
                    receipt=service.record(m['id'],payload(submitted,'receipt_reference',line=2),uid());receipt_id=receipt['saved_record_id'];self.assertEqual(next(item for item in receipt['items'] if item['kind']=='visa')['receipt'],'source_provided')
                    for patch in ({'material_kind':'passport'},{'ref':{'source_id':source['id'],'start_line':1,'end_line':1}},{'receipt_id':'unknown'}):
                        bad={**payload(receipt,'receipt_source_checked',line=2),'receipt_id':receipt_id,**patch}
                        with self.assertRaises(AppError) as denied:service.record(m['id'],bad,uid())
                        self.assertEqual(denied.exception.status,409)
                    check={**payload(receipt,'receipt_source_checked',line=2),'receipt_id':receipt_id};checked=service.record(m['id'],check,uid());visa=next(item for item in checked['items'] if item['kind']=='visa')
                    self.assertEqual(visa['receipt'],'local_source_checked');self.assertEqual(visa['formal_validity'],'unknown');self.assertEqual(len(checked['records']),3)
                    paused=store.mutate('control',m['id'],{'expected_version':checked['version'],'command':'pause'},uid());reviewed=service.record(m['id'],payload(paused,'needs_review',line=3),uid())
                    self.assertEqual(store.detail(m['id'])['assistant_status'],'paused');self.assertEqual(next(item for item in reviewed['items'] if item['kind']=='visa')['receipt'],'needs_review')
                    with self.assertRaises(AppError):service.record(m['id'],{**payload(reviewed,'receipt_source_checked',line=2),'receipt_id':receipt_id},uid())
                    with self.assertRaises(AppError) as stopped:service.render(m['id'],{'expected_version':reviewed['version'],'input_revision':reviewed['revision_no']},uid())
                    self.assertEqual(stopped.exception.status,409)
                    active=store.mutate('control',m['id'],{'expected_version':reviewed['version'],'command':'resume'},uid());relinked=service.record(m['id'],payload(active,'receipt_reference',line=2),uid());new_id=relinked['saved_record_id']
                    self.assertNotEqual(new_id,receipt_id);self.assertEqual(next(item for item in relinked['items'] if item['kind']=='visa')['receipt'],'source_provided')
                    rendered=service.render(m['id'],{'expected_version':relinked['version'],'input_revision':relinked['revision_no']},uid());self.assertIn('正式适用及效力未知',rendered['content']);self.assertIn('工程回执字段样例',rendered['content']);self.assertEqual(store.download(old_artifact,1),'本人旧稿不得覆盖；未实际办理')
                    # Existing flight-plan change revises the common basis, retaining all records and artifact versions.
                    latest=store.detail(m['id']);changed=JourneyService(store).save_plan(m['id'],{'expected_version':latest['version'],'input_revision':latest['revision_no'],'plan_semantics':dict(SEMANTICS),'field_changes':{'flight_info':{'value':'工程假设航班变更，非真实','status':'confirmed'}}},uid())
                    stale=service.get_records(m['id']);self.assertTrue(all(record['stale'] for record in stale['records']));self.assertEqual(next(item for item in stale['items'] if item['kind']=='visa')['receipt'],'needs_review')
                    with self.assertRaises(AppError):service.record(m['id'],{**payload(stale,'receipt_source_checked',line=2),'receipt_id':new_id},uid())
                    self.assertEqual(store.detail(m['id'])['tracks'],tracks)
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(MaterialRecordsService(reopened).get_records(m['id'])['records'],stale['records'])
                    with self.assertRaises(AppError) as conflict:service.record(m['id'],data,uid())
                    self.assertEqual(conflict.exception.status,409)
                    # Withholding unrelated content does not stop an actual manual record;
                    # the explicitly selected source itself must remain usable.
                    from assistant_app.source_lifecycle import SourceLifecycleService
                    current=store.detail(m['id']);current=store.mutate('source',m['id'],{'expected_version':current['version'],'kind':'user_text','text':'不供新准备使用的工程备注'},uid());unrelated=current['sources'][-1]['id']
                    lifecycle=SourceLifecycleService(store)
                    lifecycle.set_source_state(m['id'],unrelated,{'expected_version':current['version'],'input_revision':current['revision_no'],'state':'withheld','replacement_source_id':None,'note':'本人暂不用'},uid())
                    current=store.detail(m['id']);manual=service.record(m['id'],payload(current,'receipt_reference',line=2),uid());self.assertEqual(next(item for item in manual['items'] if item['kind']=='visa')['receipt'],'source_provided')
                    current=store.detail(m['id']);lifecycle.set_source_state(m['id'],source['id'],{'expected_version':current['version'],'input_revision':current['revision_no'],'state':'withheld','replacement_source_id':None,'note':'本人暂不用此原处'},uid())
                    current=store.detail(m['id'])
                    with self.assertRaises(AppError) as withheld:service.record(m['id'],payload(current),uid())
                    self.assertEqual(withheld.exception.payload['code'],'source_not_current')
                    another=store.mutate('create',None,{'domain':'leave','goal_text':'另一工程事项'},uid())
                    with self.assertRaises(AppError):service.record(another['id'],payload(another),uid())
                    with self.assertRaises(AppError):service.record(m['id'],{**payload(stale),'record_type':'approved'},uid())
                if protected:
                    with store.as_actor(actors[1]),self.assertRaises(AppError) as private:service.get_records(m['id'])
                    self.assertEqual(private.exception.status,404)
                    with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?", (actors[0]['id'],))
                    with store.as_actor(actors[0]),self.assertRaises(AppError) as revoked:service.get_records(m['id'])
                    self.assertEqual(revoked.exception.status,403)
