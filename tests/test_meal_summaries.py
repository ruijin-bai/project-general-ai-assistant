"""One combined deterministic counts, fixed projection, rights and recovery check."""
import json
import tempfile
import unittest
from pathlib import Path
from assistant_app.store import Store, AppError, uid
from assistant_app.journey import JourneyService, SEMANTICS
from assistant_app.meal_summaries import MealSummaryService
from assistant_app.sharing import ShareService, SHARING_SCHEMA
from assistant_app.source_lifecycle import SourceLifecycleService


class MealSummarySmoke(unittest.TestCase):
    def test_counts_projection_and_recovery(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix='qa-meal-summary-',dir=Path(__file__).resolve().parent) as folder:
                store=Store(folder);actors={name:None for name in ('owner','other','cook','employee')}
                if protected:
                    store.enable_identity();store.enable_services();store.enable_sharing(SHARING_SCHEMA)
                    with store.transaction() as c:
                        for name,cap in (('owner','clerk'),('other','clerk'),('cook','cook'),('employee','employee')):
                            actors[name]={'id':uid(),'auth_epoch':1}
                            c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active',?,0,'engineering')",(actors[name]['id'],name,'工程'+name,json.dumps([cap])))
                journey=JourneyService(store);reports=MealSummaryService(store);shares=ShareService(store)
                def fail(status,fn,*args):
                    with self.assertRaises(AppError) as caught:fn(*args)
                    self.assertEqual(caught.exception.status,status)
                def create(domain='travel'):
                    return store.mutate('create',None,{'domain':domain,'goal_text':'私人行程与联系电话禁止进入厨师副本','auto_prepare':False},uid())
                def meals(mid,rows):
                    m=store.detail(mid);return journey.save_meals(mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'upserts':rows,'removed_ids':[]},uid())
                def row(person,slot='午餐',request='keep',date='2026-10-09'):
                    return {'id':uid(),'person_ref':person,'meal_slot':slot,'meal_date':date,'request':request,'note':'私人手机号与备注保留在经办'}
                with store.as_actor(actors['owner']):
                    a,b,link=create(),create('leave'),create();mid=a['id']
                    old=store.mutate('manual_artifact',mid,{'expected_version':a['version'],'input_revision':a['revision_no'],'edited_content':'原人工v1必须保留'},uid())['saved_artifact_id']
                    arows=[row('甲'),row('乙',request='keep'),row('丙',request='pending'),row('甲','晚餐',date='2026-10-10')]
                    brows=[row('甲'),row('乙',request='request_reduce'),row('丁',request='request_reduce')]
                    meals(mid,arows);b=journey.set_travel_link(b['id'],{'expected_version':b['version'],'travel_matter_id':link['id'],'travel_revision':link['revision_no']},uid());meals(b['id'],brows)
                    data={'selected_ids':[mid,b['id']],'meal_date':'2026-10-09'};before=store.detail(mid);p=reports.preview(mid,data);self.assertEqual(store.detail(mid),before)
                    self.assertEqual(p['groups'],[{'meal_slot':'午餐','keep':1,'request_reduce':1,'pending':1,'conflict':1,'unique_refs':4,'duplicates':2}]);self.assertEqual(len(p['details']),4)
                    self.assertTrue(any(d['state']=='conflict' and d['person_ref']=='乙' for d in p['details']))
                    for private in ('私人手机号','联系电话',mid,b['id'],'甲','乙','丙','丁','2026-10-10'):self.assertNotIn(private,p['cook_content'])
                    fail(422,reports.preview,mid,{**data,'selected_ids':[mid,mid]});fail(422,reports.preview,mid,{**data,'meal_date':'2026-02-30'})
                    def body(p):return {key:p[key] for key in ('expected_version','selected_ids','meal_date','basis_token')}
                    empty=reports.preview(mid,{**data,'meal_date':'2026-10-11'});self.assertFalse(empty['has_records']);self.assertIn('未知',empty['cook_content']);fail(422,reports.render_report,mid,body(empty),uid())
                    savedbody=body(p);key=uid();saved=reports.render_report(mid,savedbody,key);self.assertEqual(reports.render_report(mid,savedbody,key),saved)
                    aid=saved['artifact_id'];m=store.detail(mid);tracks=m['tracks'];store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid())
                    fail(409,reports.render_report,mid,body(reports.preview(mid,data)),uid())
                    store.mutate('artifact',aid,{'base_version':1,'edited_content':saved['cook_content']+'\n私有人工v2及姓名不可给厨师'},uid())
                    reopened=Store(folder)
                    with reopened.as_actor(actors['owner']):
                        self.assertEqual(reopened.detail(mid)['assistant_status'],'paused');self.assertEqual(reopened.download(aid,1),saved['cook_content']);self.assertTrue(reopened.download(aid,2).endswith('私有人工v2及姓名不可给厨师'));self.assertEqual(reopened.download(old,1),'原人工v1必须保留');self.assertEqual(MealSummaryService(reopened).inputs(mid)['reports'][0]['groups'],p['groups'])
                    m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'resume'},uid())
                    if protected:
                        def sharebody():return {'expected_version':store.detail(mid)['version'],'artifact_id':aid,'artifact_version':1,'recipient_id':actors['cook']['id'],'title':saved['title'],'content':saved['cook_content'],'dependency_refs':[]}
                        sb=sharebody();grant=shares.create_share(mid,sb,uid())
                        for changes in ({'content':saved['cook_content']+'\n任意私人稿'},{'title':'私有姓名'},{'artifact_version':2},{'dependency_refs':['private-source']},{'artifact_id':old},{'recipient_id':actors['employee']['id']}):fail(422,shares.create_share,mid,{**sharebody(),**changes},uid())
                        with store.as_actor(actors['cook']):
                            received=shares.get_share(grant['id']);self.assertEqual(received['content'],saved['cook_content']);self.assertEqual(received['projection_kind'],'meal_summary');self.assertNotIn('basis',received);self.assertNotIn('matter_id',received);self.assertEqual(shares.download_share(grant['id'],1),saved['cook_content']);fail(404,store.detail,mid);fail(404,reports.inputs,mid)
                        with store.as_actor(actors['employee']):
                            fail(404,shares.get_share,grant['id']);personal=create();fail(403,reports.inputs,personal['id'])
                        with store.as_actor(actors['other']):
                            foreign=create();fail(404,reports.preview,mid,data)
                        fail(404,reports.preview,mid,{**data,'selected_ids':[foreign['id']]})
                        ordinary=shares.create_share(mid,{**sharebody(),'artifact_id':old,'recipient_id':actors['other']['id'],'title':'明确普通稿','content':'独立普通准备文字'},uid())
                        with store.as_actor(actors['other']):self.assertEqual(shares.get_share(ordinary['id'])['content'],'独立普通准备文字')
                    # A linked journey changed without editing the report's own matter.
                    link=journey.save_plan(link['id'],{'expected_version':link['version'],'input_revision':link['revision_no'],'plan_semantics':dict(SEMANTICS),'field_changes':{'destination':{'value':'工程改地点','status':'confirmed'}}},uid())
                    self.assertTrue(reports.inputs(mid)['reports'][0]['stale']);fail(409,reports.preview,mid,data)
                    self.assertEqual(reports.render_report(mid,savedbody,key),saved)
                    if protected:
                        fail(409,shares.create_share,mid,sharebody(),uid())
                        with store.as_actor(actors['cook']):self.assertTrue(shares.get_share(grant['id'])['stale']);self.assertEqual(shares.download_share(grant['id'],1),saved['cook_content'])
                        # Role loss and password epoch revoke recipient reading, without deleting history.
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"employee\"]' WHERE id=?",(actors['owner']['id'],))
                        with store.as_actor(actors['cook']):fail(404,shares.get_share,grant['id'])
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"clerk\"]' WHERE id=?",(actors['owner']['id'],));c.execute("UPDATE accounts SET capabilities='[\"employee\"]' WHERE id=?",(actors['cook']['id'],))
                        with store.as_actor(actors['cook']):fail(404,shares.download_share,grant['id'],1)
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"cook\"]',auth_epoch=2 WHERE id=?",(actors['cook']['id'],))
                        actors['cook']['auth_epoch']=2
                        with store.as_actor(actors['cook']):fail(404,shares.get_share,grant['id'])
                        with store.transaction() as c:c.execute('UPDATE accounts SET auth_epoch=1 WHERE id=?',(actors['cook']['id'],))
                        actors['cook']['auth_epoch']=1;shares.withdraw_share(grant['id'],{'expected_version':1},uid())
                        with store.as_actor(actors['cook']):fail(404,shares.get_share,grant['id']);fail(404,shares.download_share,grant['id'],2);self.assertEqual(shares.list_received()['items'],[])
                    self.assertEqual(store.detail(mid)['tracks'],tracks);self.assertEqual(store.download(old,1),'原人工v1必须保留')
                    # Root-source control also blocks a report whose scope excludes the root.
                    m=store.detail(mid);m=store.mutate('source',mid,{'expected_version':m['version'],'kind':'user_text','text':'工程撤回来源'},uid())
                    SourceLifecycleService(store).set_source_state(mid,m['sources'][-1]['id'],{'expected_version':m['version'],'input_revision':m['revision_no'],'state':'withheld','replacement_source_id':None,'note':'工程不用'},uid())
                    fail(409,reports.preview,mid,{'selected_ids':[link['id']],'meal_date':'2026-10-09'})


if __name__=='__main__':unittest.main()
