import json,tempfile,threading,unittest
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from assistant_app.store import Store,AppError,uid
from assistant_app.server import Runner
from assistant_app.model import ModelClient
from assistant_app.domain_routing import normalize

class RoutingSmoke(unittest.TestCase):
    def test_natural_goal_category_preservation_and_stop_fences(self):
        for protected in (False,True):
            with self.subTest(protected=protected),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'routing'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                with store.as_actor(actors[0]):
                    goal='工程地点B房空调滴水，只整理报修需求，联系人未知。'
                    m=store.mutate('create',None,{'domain':'auto','goal_text':goal,'initial_mode':'model','auto_prepare':False},uid());mid=m['id']
                    m=store.mutate('manual_artifact',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'工程原人工稿，保持原文'},uid());aid=m['saved_artifact_id'];original=store.detail(mid)
                    proposal={'domain':'repair','reason':'原话明确空调滴水，按报修准备','refs':[{'source_index':1,'start_line':1,'end_line':1,'quote':goal}]}
                    for bad in ({**proposal,'domain':[]},{**proposal,'domain':'unknown'},{**proposal,'refs':[{**proposal['refs'][0],'quote':'伪造'}]}):
                        with self.assertRaises((ValueError,TypeError)):normalize(bad,original)
                    self.assertEqual(normalize({'domain':'general','reason':'主要目标未明','refs':[]},original)['domain'],'general')
                client=ModelClient(enabled=True,api_key='EngineeringStubOnly');calls=[]
                def opened(request,timeout):
                    sent=json.loads(json.loads(request.data)['messages'][1]['content']);calls.append(sent)
                    self.assertIn('repair',sent['preparation_categories']);self.assertNotIn('工程原人工稿',json.dumps(sent,ensure_ascii=False))
                    result={'content':'工程报修准备，未分派或执行','candidates':{'subject':'空调滴水核对'},'questions':['必要联系人待补'],'related_tasks':[],'related_matches':[],'preparation_domain':proposal}
                    return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result,ensure_ascii=False)}}],'usage':{'total_tokens':120}}).encode())
                client._opener=SimpleNamespace(open=opened);runner=Runner.__new__(Runner);runner.store=store;runner.model=client;runner.stopped=threading.Event();self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    m=store.detail(mid);self.assertEqual(m['domain'],'repair');self.assertEqual(m['domain_routing']['state'],'classified');self.assertEqual(m['sources'],original['sources']);self.assertEqual(store.download(aid,1),'工程原人工稿，保持原文');self.assertEqual(m['legacy_facts']['subject']['value'],'空调滴水核对');self.assertTrue(all(t['status']=='unknown' for t in m['tracks'].values()))
                    m=store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid());body={'expected_version':m['version'],'input_revision':m['revision_no'],'domain':'general'};key=uid();m=store.mutate('category',mid,body,key);self.assertEqual(m['assistant_status'],'paused');self.assertEqual(len(calls),1)
                    m=store.mutate('category',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'domain':'repair'},uid());self.assertEqual(store.mutate('category',mid,body,key)['domain'],'repair')
                    stale={'expected_version':m['version']-1,'input_revision':m['revision_no'],'domain':'general'}
                    with self.assertRaises(AppError) as error:store.mutate('category',mid,stale,uid())
                    self.assertEqual(error.exception.status,409)
                    m=store.mutate('facts',mid,{'expected_version':m['version'],'field_changes':{'location':'已核工程B房'}},uid())
                    with self.assertRaises(AppError):store.mutate('category',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'domain':'general'},uid())
                    self.assertEqual(store.detail(mid)['facts']['location']['value'],'已核工程B房')
                    old=store.mutate('create',None,{'domain':'general','goal_text':'原有事项不迁移'},uid())
                    with self.assertRaises(AppError):store.mutate('category',old['id'],{'expected_version':old['version'],'input_revision':old['revision_no'],'domain':'repair'},uid())
                    late=store.mutate('create',None,{'domain':'auto','goal_text':goal,'initial_mode':'model'},uid());late_id=late['id']
                    def changed(request,timeout):
                        raw=opened(request,timeout);current=store.detail(late_id);store.mutate('goal',late_id,{'expected_version':current['version'],'goal_text':'工程改为其他目标，原分类不采用','recheck_fields':[]},uid());return raw
                    client._opener=SimpleNamespace(open=changed)
                self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    self.assertEqual(store.detail(late_id)['domain'],'general')
                    with store.connect() as c:usage=json.loads(c.execute("SELECT message FROM events WHERE matter_id=? AND event_type='model_usage'",(late_id,)).fetchone()[0])
                    self.assertTrue(usage['discarded'])
                    stopped=store.mutate('create',None,{'domain':'auto','goal_text':goal,'initial_mode':'model'},uid());store.mutate('control',stopped['id'],{'expected_version':stopped['version'],'command':'pause'},uid());self.assertFalse(runner.step());self.assertEqual(len(calls),2)
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(reopened.detail(mid)['domain'],'repair');self.assertEqual(reopened.download(aid,1),'工程原人工稿，保持原文')
                    if protected:
                        with store.as_actor(actors[1]),self.assertRaises(AppError) as denied:store.mutate('category',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'domain':'general'},uid())
                        self.assertEqual(denied.exception.status,404)
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors[0]['id'],))
                        with self.assertRaises(AppError) as denied:store.mutate('category',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'domain':'general'},uid())
                        self.assertEqual(denied.exception.status,403)

if __name__=='__main__':unittest.main()
