"""One combined own-reference, human-value, exact-money and recovery check."""
import json
import tempfile
import unittest
from pathlib import Path
from assistant_app.store import Store, AppError, uid
from assistant_app.journey import JourneyService, SEMANTICS
from assistant_app.expense_journey import ExpenseJourneyService
from assistant_app.row_reports import RowReportsService
from assistant_app.exports import rows_csv_bytes
from assistant_app.source_lifecycle import SourceLifecycleService
from assistant_app.sharing import ShareService, SHARING_SCHEMA


class ExpenseJourneySmoke(unittest.TestCase):
    def test_owned_reuse_values_money_and_recovery(self):
        for protected in (False,True):
            with self.subTest(protected=protected),tempfile.TemporaryDirectory(prefix='qa-expense-journey-',dir=Path(__file__).resolve().parent) as folder:
                store=Store(folder);actors={name:None for name in ('owner','other','cook')}
                if protected:
                    store.enable_identity();store.enable_services();store.enable_sharing(SHARING_SCHEMA)
                    with store.transaction() as c:
                        for name,cap in (('owner','employee'),('other','clerk'),('cook','cook')):
                            actors[name]={'id':uid(),'auth_epoch':1};c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active',?,0,'fixture')",(actors[name]['id'],name,'工程'+name,json.dumps([cap])))
                service=ExpenseJourneyService(store);journey=JourneyService(store);reports=RowReportsService(store)
                def fail(status,method,*args):
                    with self.assertRaises(AppError) as caught:method(*args)
                    self.assertEqual(caught.exception.status,status)
                def create(domain):return store.mutate('create',None,{'domain':domain,'goal_text':'工程隐私本次目标与私人联系人','auto_prepare':False},uid())
                with store.as_actor(actors['owner']):
                    travel=create('travel');tid=travel['id'];travel=journey.save_plan(tid,{'expected_version':travel['version'],'input_revision':travel['revision_no'],'plan_semantics':dict(SEMANTICS),'field_changes':{'purpose':{'value':'工程设备核对','status':'confirmed'},'destination':{'value':'工程候选地点','status':'candidate'},'departure_at':{'value':'2026-10-09T18:30:00+02:00','status':'confirmed'},'return_at':{'value':'2026-10-10T09:00:00+01:00','status':'confirmed'},'participants':{'value':'私人同行引用不进入费用副本','status':'confirmed'}}},uid())
                    original_travel=store.detail(tid)
                    for human_purpose in (None,'本人原费用事由必须保留'):
                        m=create('expense');mid=m['id'];m=store.mutate('facts',mid,{'expected_version':m['version'],'field_changes':{'purpose':human_purpose,'period':'2026-11','expense_lines':'工程纸张USD0.1\n工程纸张USD0.2\n工程材料EUR0.5'}},uid())
                        m=store.mutate('manual_artifact',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'费用原人工v1必须保留'},uid());old=m['saved_artifact_id'];source=m['sources'][-1]['id']
                        rows=[{'id':'r'+str(i),'label':'工程纸张','value':value,'currency':currency,'period':'2026-11','source_id':source,'source_position':'第1–1行'} for i,(value,currency) in enumerate((('0.1','USD'),('0.2','USD'),('0.5','EUR')),1)]
                        m=store.mutate('rows',mid,{'expected_version':m['version'],'rows':rows},uid());tracks=m['tracks']
                        inputs=service.inputs(mid);self.assertIsNone(inputs['context']);self.assertEqual(len(inputs['items']),1)
                        before=store.detail(mid);p=service.preview(mid,{'travel_id':tid});self.assertEqual(store.detail(mid),before);self.assertEqual(p['items'][2]['value'],'2026-10-09T18:30:00+02:00')
                        def request(fields=None):
                            fresh=store.detail(mid);return {'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'travel_id':tid,'travel_revision':journey.get_journey(tid)['revision_no'],'fields':fields or ['purpose','departure_at','return_at'],'fill_empty_purpose':True}
                        fail(422,service.preview,mid,{'travel_id':[]});fail(409,service.save,mid,request(['purpose','destination']),uid());self.assertEqual(store.detail(mid),before)
                        fail(422,service.save,mid,{**request(),'fields':['purpose','purpose']},uid());fail(409,service.save,mid,{**request(),'travel_revision':0},uid());fail(409,service.save,mid,{**request(),'expected_version':0},uid())
                        m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid());data=request();key=uid();saved=service.save(mid,data,key);self.assertEqual(service.save(mid,data,key),saved)
                        m=store.detail(mid);self.assertEqual(m['assistant_status'],'paused');self.assertEqual(m['facts']['purpose']['value'],human_purpose or '工程设备核对');self.assertEqual(saved['purpose_filled'],human_purpose is None)
                        self.assertEqual(m['facts']['period']['value'],'2026-11');self.assertEqual(m['rows'],rows);self.assertEqual(m['tracks'],tracks);self.assertEqual(store.detail(tid),original_travel)
                        self.assertNotIn('私人同行引用',json.dumps(saved,ensure_ascii=False));self.assertNotIn('工程隐私',json.dumps(saved,ensure_ascii=False))
                        store.mutate('rows',mid,{'expected_version':m['version'],'rows':rows},uid())
                        def reportbody():
                            d=reports.get_inputs(mid);return {'expected_version':d['version'],'input_revision':d['revision_no'],'rows_revision':d['rows_revision'],'row_ids':['r1','r2','r3'],'note':'工程费用期间明确为11月，行程为10月；不转换期间','duplicate_acknowledged':False}
                        fail(409,reports.render_report,mid,reportbody(),uid());m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'resume'},uid())
                        report=reports.render_report(mid,reportbody(),uid());aid=report['artifact_id'];self.assertEqual([group['total'] for group in report['calculation']['groups']],['0.3','0.5']);self.assertEqual(report['snapshot']['journey_context']['travel_id'],tid);self.assertIn('2026-10-09T18:30:00+02:00',report['content']);self.assertIn('2026-11',report['content']);self.assertNotIn('私人同行引用',report['content'])
                        fixed=rows_csv_bytes(reports.csv_snapshot(mid,aid));self.assertNotIn(b'2026-10-09',fixed)
                        if protected:
                            shares=ShareService(store)
                            def sharebody():return {'expected_version':store.detail(mid)['version'],'artifact_id':aid,'artifact_version':1,'recipient_id':actors['other']['id'],'title':'本人选定费用准备','content':report['content'],'dependency_refs':[]}
                            grant=shares.create_share(mid,sharebody(),uid())
                        m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid());store.mutate('artifact',aid,{'base_version':1,'edited_content':report['content']+'\n费用本人暂停人工v2'},uid())
                        reopened=Store(folder)
                        with reopened.as_actor(actors['owner']):
                            self.assertEqual(reopened.detail(mid)['assistant_status'],'paused');self.assertFalse(ExpenseJourneyService(reopened).inputs(mid)['context']['stale']);self.assertEqual(reopened.download(old,1),'费用原人工v1必须保留');self.assertTrue(reopened.download(aid,2).endswith('费用本人暂停人工v2'));self.assertEqual(rows_csv_bytes(RowReportsService(reopened).csv_snapshot(mid,aid)),fixed)
                        if protected:
                            with store.as_actor(actors['other']):
                                foreign=create('travel');fail(404,service.inputs,mid);fail(404,reports.csv_snapshot,mid,aid)
                            fail(404,service.preview,mid,{'travel_id':foreign['id']})
                            with store.as_actor(actors['cook']):fail(403,service.inputs,mid)
                        travel=journey.get_journey(tid);journey.save_plan(tid,{'expected_version':travel['version'],'input_revision':travel['revision_no'],'plan_semantics':dict(SEMANTICS),'field_changes':{'return_at':{'value':'2026-10-11T09:00:00+01:00','status':'confirmed'}}},uid())
                        self.assertTrue(service.inputs(mid)['context']['stale']);self.assertEqual(service.save(mid,data,key),saved)
                        self.assertTrue(next(a for a in store.detail(mid)['artifacts'] if a['id']==aid)['stale'])
                        if protected:
                            fail(409,shares.create_share,mid,sharebody(),uid())
                            with store.as_actor(actors['other']):self.assertTrue(shares.get_share(grant['id'])['stale']);self.assertEqual(shares.download_share(grant['id'],1),report['content'])
                        m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'resume'},uid());fail(409,reports.render_report,mid,reportbody(),uid());self.assertEqual(rows_csv_bytes(reports.csv_snapshot(mid,aid)),fixed);self.assertEqual(store.download(aid,1),report['content'])
                        # Explicit re-read/reselect replaces the current reference, preserving manual purpose.
                        new=request();new['fill_empty_purpose']=False;service.save(mid,new,uid());self.assertFalse(service.inputs(mid)['context']['stale']);self.assertEqual(store.detail(mid)['facts']['purpose']['value'],human_purpose or '工程设备核对')
                        travel=store.detail(tid);SourceLifecycleService(store).set_source_state(tid,travel['sources'][0]['id'],{'expected_version':travel['version'],'input_revision':travel['revision_no'],'state':'withheld','replacement_source_id':None,'note':'工程原文暂不使用'},uid())
                        fail(409,service.preview,mid,{'travel_id':tid});self.assertTrue(service.inputs(mid)['context']['stale'])
                        m=store.detail(mid);service.unlink(mid,{'expected_version':m['version'],'input_revision':m['revision_no']},uid());self.assertIsNone(service.inputs(mid)['context']);m=store.detail(mid);self.assertEqual(m['facts']['purpose']['value'],human_purpose or '工程设备核对');store.mutate('rows',mid,{'expected_version':m['version'],'rows':rows},uid());plain=reports.render_report(mid,reportbody(),uid());self.assertIsNone(plain['snapshot']['journey_context']);self.assertEqual(store.download(old,1),'费用原人工v1必须保留');self.assertEqual(store.detail(mid)['tracks'],tracks)
                        # Restore source for the next independent expense fixture, retaining the source history.
                        travel=store.detail(tid);SourceLifecycleService(store).set_source_state(tid,travel['sources'][0]['id'],{'expected_version':travel['version'],'input_revision':travel['revision_no'],'state':'usable','replacement_source_id':None,'note':'工程重新核对使用'},uid());original_travel=store.detail(tid)
                    if protected:
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors['owner']['id'],))
                        fail(403,service.inputs,mid)


if __name__=='__main__':unittest.main()
