"""Selected scopes, fixed snapshots, privacy, pause and saved recovery in one check."""
from pathlib import Path
import tempfile
import unittest
from assistant_app.store import Store, AppError, uid
from assistant_app.row_reports import RowReportsService
from assistant_app.exports import rows_csv_bytes
from assistant_app.source_lifecycle import SourceLifecycleService


class RowReportsSmoke(unittest.TestCase):
    def test_selected_report_and_fixed_recovery(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix='qa-row-reports-', dir=Path(__file__).resolve().parent) as folder:
                store=Store(folder); actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                reports=RowReportsService(store)
                with store.as_actor(actors[0]):
                    for domain in ('expense','inventory'):
                        m=store.mutate('create',None,{'domain':domain,'goal_text':'工程选期汇总；未发生付款收发','auto_prepare':False},uid());mid=m['id']
                        m=store.mutate('source',mid,{'expected_version':m['version'],'kind':'user_text','text':'工程0.1\n工程0.2\n工程0.5\n工程1.0'},uid());source=m['sources'][-1]['id']
                        m=store.mutate('manual_artifact',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'原人工稿保持'},uid());old=m['saved_artifact_id'];tracks=m['tracks']
                        dimension='currency' if domain=='expense' else 'unit'
                        rows=[{'id':'r'+str(i),'label':'工程纸张','value':v,dimension:d,'period':p,'source_id':source,'source_position':f'第{i}–{i}行'} for i,(v,d,p) in enumerate((('0.1','USD' if domain=='expense' else '包','2026-10'),('0.2','USD' if domain=='expense' else '包','2026-10'),('0.5','EUR' if domain=='expense' else '箱','2026-10'),('1.0','USD' if domain=='expense' else '包','2026-11')),1)]
                        rows.append({**rows[0],'id':'repeat'})
                        m=store.mutate('rows',mid,{'expected_version':m['version'],'rows':rows},uid())
                        def body(ids=None):
                            data=reports.get_inputs(mid);return {'expected_version':data['version'],'input_revision':data['revision_no'],'rows_revision':data['rows_revision'],'row_ids':ids or ['r1','r2','r3'],'note':'工程选期口径；项目组织未核','duplicate_acknowledged':False}
                        before=store.detail(mid);inputs=reports.get_inputs(mid);self.assertEqual(store.detail(mid),before);self.assertEqual(inputs['duplicate_groups'],[['r1','repeat']])
                        with self.assertRaises(AppError) as dup:reports.render_report(mid,body(['r1','repeat']),uid())
                        self.assertEqual(dup.exception.payload['code'],'duplicate_scope_pending')
                        acknowledge=body(['r1','repeat']);acknowledge['duplicate_acknowledged']=True;acknowledge['note']='工程明确两笔同值同处各自累计，并非自动去重'
                        duplicate=reports.render_report(mid,acknowledge,uid());self.assertEqual(duplicate['calculation']['groups'][0]['total'],'0.2')
                        stale_request=body();request=body();key=uid();saved=reports.render_report(mid,request,key);self.assertEqual(reports.render_report(mid,request,key),saved)
                        self.assertEqual([g['total'] for g in saved['calculation']['groups']],['0.3','0.5']);self.assertEqual([r['id'] for r in saved['snapshot']['rows']],['r1','r2','r3']);self.assertNotIn('2026-11',saved['content']);self.assertIn('工程0.1',saved['content'])
                        fixed=rows_csv_bytes(reports.csv_snapshot(mid,saved['artifact_id']));self.assertNotIn(b'2026-11',fixed)
                        with self.assertRaises(AppError) as race:reports.render_report(mid,stale_request,uid())
                        self.assertEqual(race.exception.status,409)
                        m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid())
                        with self.assertRaises(AppError) as paused:reports.render_report(mid,body(),uid())
                        self.assertEqual(paused.exception.status,409)
                        store.mutate('artifact',saved['artifact_id'],{'base_version':1,'edited_content':saved['content']+'\n本人暂停续办备注'},uid())
                        reopened=Store(folder)
                        with reopened.as_actor(actors[0]):
                            self.assertEqual(reopened.detail(mid)['assistant_status'],'paused');self.assertEqual(rows_csv_bytes(RowReportsService(reopened).csv_snapshot(mid,saved['artifact_id'])),fixed);self.assertTrue(reopened.download(saved['artifact_id'],2).endswith('本人暂停续办备注'))
                        m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'resume'},uid())
                        m=store.detail(mid);store.mutate('source',mid,{'expected_version':m['version'],'kind':'user_text','text':'工程后续来源，要求重核原明细'},uid())
                        with self.assertRaises(AppError) as stale:reports.render_report(mid,body(),uid())
                        self.assertEqual(stale.exception.payload['code'],'rows_report_stale');self.assertTrue(reports.get_inputs(mid)['stale'])
                        m=store.detail(mid);changed=[{**r,'value':'9'} if r['id']=='r1' else r for r in rows];store.mutate('rows',mid,{'expected_version':m['version'],'rows':changed},uid())
                        self.assertEqual(rows_csv_bytes(reports.csv_snapshot(mid,saved['artifact_id'])),fixed);self.assertEqual(store.download(saved['artifact_id'],1),saved['content']);self.assertEqual(store.download(old,1),'原人工稿保持');self.assertEqual(store.detail(mid)['tracks'],tracks)
                        m=store.detail(mid);SourceLifecycleService(store).set_source_state(mid,source,{'expected_version':m['version'],'input_revision':m['revision_no'],'state':'withheld','replacement_source_id':None,'note':'工程暂不用'},uid())
                        with self.assertRaises(AppError) as withheld:reports.render_report(mid,body(),uid())
                        self.assertEqual(withheld.exception.payload['code'],'rows_report_stale');self.assertEqual(rows_csv_bytes(reports.csv_snapshot(mid,saved['artifact_id'])),fixed)
                        if protected:
                            with store.as_actor(actors[1]),self.assertRaises(AppError) as private:reports.csv_snapshot(mid,saved['artifact_id'])
                            self.assertEqual(private.exception.status,404)
                    if protected:
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors[0]['id'],))
                        with self.assertRaises(AppError) as revoked:reports.get_inputs(mid)
                        self.assertEqual(revoked.exception.status,403)


if __name__=='__main__':unittest.main()
