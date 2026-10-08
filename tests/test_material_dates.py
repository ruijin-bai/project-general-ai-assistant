"""Calendar arithmetic, precision/zone gates, private fixed drafts and link races."""
from pathlib import Path
import tempfile
import unittest
from assistant_app.store import Store, AppError, uid
from assistant_app.journey import JourneyService, SEMANTICS
from assistant_app.material_nodes import MaterialNodesService
from assistant_app.material_records import MaterialRecordsService
from assistant_app.source_lifecycle import SourceLifecycleService


class MaterialDatesSmoke(unittest.TestCase):
    def test_comparison_precision_link_and_saved_versions(self):
        for protected in (False, True):
            with self.subTest(protected=protected),tempfile.TemporaryDirectory(prefix='qa-material-dates-',dir=Path(__file__).resolve().parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                journey=JourneyService(store);nodes=MaterialNodesService(store)
                with store.as_actor(actors[0]):
                    m=store.mutate('create',None,{'domain':'leave','goal_text':'工程材料日期比较，非真实证件，不判断通行','auto_prepare':False},uid());mid=m['id']
                    m=store.mutate('source',mid,{'expected_version':m['version'],'kind':'user_text','text':'工程假设护照日期2027-05-30\n工程假设签证日期2026-11-09\n工程假设国内证明日期2026-11-10\n工程假设入境材料日期2028-02-29'},uid());source=m['sources'][-1]['id']
                    m=store.mutate('manual_artifact',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'本人原稿，效力均未知，不得覆盖'},uid());old=m['saved_artifact_id'];tracks=m['tracks']
                    def plan(changes,semantics=None,target=None):
                        ident=target or mid;fresh=store.detail(ident);return journey.save_plan(ident,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'plan_semantics':semantics or dict(SEMANTICS),'field_changes':{k:{'value':v,'status':'confirmed'} for k,v in changes.items()}},uid())
                    plan({'leave_start':'2026-11-09','leave_end':'2026-11-21','departure_at':'2026-11-10','return_at':'2026-11-20'})
                    fresh=store.detail(mid);materials=[{'id':uid(),'kind':kind,'availability':'source_provided','provider_class':'current_actor_statement','valid_until':day,'check':'local_source_checked','source_id':source,'source_position':f'第{i}–{i}行'} for i,(kind,day) in enumerate((('passport','2027-05-30'),('visa','2026-11-09'),('domestic_proof','2026-11-10'),('nis_entry','2028-02-29')),1)]
                    journey.save_materials(mid,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'upserts':materials,'removed_ids':[]},uid())
                    before=store.detail(mid);data=nodes.get_candidates(mid);report=data['date_report'];self.assertEqual(store.detail(mid),before)
                    def row(kind,field,report=report):return next(i for i in report['items'] if i['kind']==kind and i['field']==field)
                    self.assertEqual((row('passport','departure_at')['calendar_days'],row('passport','return_at')['calendar_days']),(201,191))
                    self.assertEqual(row('visa','departure_at')['calendar_days'],-1);self.assertEqual(row('domestic_proof','departure_at')['relation'],'same_day');self.assertEqual(row('nis_exit','departure_at')['status'],'pending');self.assertTrue(all(i['formal_validity']=='unknown' for i in report['items']))
                    def payload(data=None):
                        data=data or nodes.get_candidates(mid);return {'expected_version':data['version'],'input_revision':data['revision_no'],'report_token':data['date_report']['report_token']}
                    request=payload(data);key=uid();saved=nodes.render_dates(mid,request,key);self.assertEqual(nodes.render_dates(mid,request,key),saved);self.assertIn('之后201个日历日',saved['content'])
                    edit=store.mutate('artifact',saved['artifact_id'],{'base_version':1,'edited_content':saved['content']+'\n本人续办备注'},uid());self.assertEqual(store.download(saved['artifact_id'],1),saved['content'])
                    fresh=store.detail(mid);store.mutate('control',mid,{'expected_version':fresh['version'],'command':'pause'},uid())
                    with self.assertRaises(AppError) as paused:nodes.render_dates(mid,payload(),uid())
                    self.assertEqual(paused.exception.status,409)
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(MaterialNodesService(reopened).get_candidates(mid)['date_report'],nodes.get_candidates(mid)['date_report']);self.assertTrue(reopened.download(saved['artifact_id'],2).endswith('本人续办备注'))
                    # No default timezone or truncating date-times. Explicit comparison offset crosses midnight.
                    plan({'departure_at':'2026-11-10T00:30:00+02:00','return_at':'2026-11-20T00:30:00+02:00'})
                    mixed=nodes.get_candidates(mid)['date_report'];self.assertEqual(row('domestic_proof','departure_at',mixed)['status'],'pending')
                    plan({}, {**SEMANTICS,'comparison_offset':'+00:00'});explicit=nodes.get_candidates(mid)['date_report'];self.assertEqual(row('domestic_proof','departure_at',explicit)['calendar_days'],1);self.assertEqual(row('domestic_proof','departure_at',explicit)['comparison_date'],'2026-11-09')
                    plan({'leave_start':'2028-02-28','leave_end':'2028-03-01'});leap=nodes.get_candidates(mid)['date_report'];self.assertEqual(row('nis_entry','leave_start',leap)['calendar_days'],1)
                    fresh=store.detail(mid);store.mutate('control',mid,{'expected_version':fresh['version'],'command':'resume'},uid())
                    # Associated travel can change without changing the leave matter version.
                    target=store.mutate('create',None,{'domain':'travel','goal_text':'工程关联行程，未实际出行','auto_prepare':False},uid());plan({'departure_at':'2026-11-10T00:30:00+02:00','return_at':'2026-11-20T00:30:00+02:00'},target=target['id']);target=store.detail(target['id']);fresh=store.detail(mid)
                    journey.set_travel_link(mid,{'expected_version':fresh['version'],'travel_matter_id':target['id'],'travel_revision':target['revision_no']},uid());old_request=payload();plan({'return_at':'2026-11-21'},target=target['id'])
                    with self.assertRaises(AppError) as changed:nodes.render_dates(mid,old_request,uid())
                    self.assertEqual(changed.exception.payload['code'],'material_dates_changed');current=nodes.get_candidates(mid)['date_report'];self.assertEqual(row('passport','return_at',current)['status'],'pending');self.assertEqual(row('passport','leave_start',current)['status'],'computed')
                    fresh=store.detail(mid);SourceLifecycleService(store).set_source_state(mid,source,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'state':'withheld','replacement_source_id':None,'note':'本人暂不用此原文'},uid());self.assertEqual(nodes.get_candidates(mid)['date_report']['computed_count'],0)
                    self.assertEqual(store.download(old,1),'本人原稿，效力均未知，不得覆盖');self.assertEqual(store.detail(mid)['tracks'],tracks);self.assertTrue(all(i['receipt']=='unknown' and i['submission']=='unknown' for i in MaterialRecordsService(store).get_records(mid)['items']))
                if protected:
                    with store.as_actor(actors[1]),self.assertRaises(AppError) as private:nodes.get_candidates(mid)
                    self.assertEqual(private.exception.status,404)
                    with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors[0]['id'],))
                    with store.as_actor(actors[0]),self.assertRaises(AppError) as revoked:nodes.get_candidates(mid)
                    self.assertEqual(revoked.exception.status,403)
