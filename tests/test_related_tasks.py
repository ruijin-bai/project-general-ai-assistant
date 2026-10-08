import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import tempfile,threading,unittest
from assistant_app.store import Store,AppError,uid
from assistant_app.server import Runner
from assistant_app.model import ModelClient
from assistant_app.related_tasks import RelatedTasksService,normalize_related

class RelatedTasksSmoke(unittest.TestCase):
    def test_discovery_independent_save_and_basis_guards(self):
        for protected in (False,True):
            with self.subTest(protected=protected),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                with store.as_actor(actors[0]):
                    m=store.mutate('create',None,{'domain':'general','goal_text':'工程原话：外出核设备，中午可能赶不回营地。没有说明要取消用餐。','auto_prepare':False},uid());mid=m['id'];original=store.detail(mid)
                    proposal={'domain':'dining','goal_text':'核对外出当天午餐需求，暂不扣餐','reason':'原话称中午可能赶不回，需求、日期与饭点须另核。','refs':[{'source_index':1,'start_line':1,'end_line':1,'quote':m['goal_text']}]}
                    self.assertEqual(normalize_related([proposal],m)[0]['confirmation'],'candidate')
                    for bad in ({**proposal,'domain':'general'},{**proposal,'refs':[{**proposal['refs'][0],'quote':'伪造依据'}]},{**proposal,'refs':[{**proposal['refs'][0],'source_index':2}]}):
                        with self.assertRaises(ValueError):normalize_related([bad],m)
                    payload={'content':'工程准备草稿','candidates':{},'questions':[],'related_tasks':[proposal]};calls=[]
                    raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(payload,ensure_ascii=False)}}],'usage':{'total_tokens':123}}).encode()
                    client=ModelClient(enabled=True,api_key='EngineeringStubOnly')
                    def opened(request,timeout):
                        body=json.loads(request.data);calls.append(body);self.assertIn('related_tasks',body['messages'][0]['content']);self.assertEqual(json.loads(body['messages'][1]['content'])['sources'][0]['source_index'],1);return BytesIO(raw)
                    client._opener=SimpleNamespace(open=opened);store.mutate('prepare',mid,{'expected_version':m['version'],'kind':'prepare','mode':'model'},uid())
                runner=Runner.__new__(Runner);runner.store=store;runner.model=client;runner.stopped=threading.Event();self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    service=RelatedTasksService(store);data=service.read(mid);m=store.detail(mid);self.assertEqual(m['facts'],original['facts']);self.assertEqual(m['tracks'],original['tracks']);self.assertFalse(data['stale']);self.assertEqual(len(data['items']),1);self.assertEqual(len(calls),1)
                    store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid());data=service.read(mid)
                    body={'expected_version':data['expected_version'],'input_revision':data['input_revision'],'action_id':data['action_id'],'candidate_id':data['items'][0]['id'],'goal_text':'本人修正目标：先问是否保留午餐，不自动减餐'};key=uid()
                    saved=service.create(mid,body,key);self.assertEqual(service.create(mid,body,key),saved);child=saved['matter_id'];cm=store.detail(child);self.assertEqual(cm['domain'],'dining');self.assertEqual(store.detail(mid)['assistant_status'],'paused');self.assertTrue(all(f['status']=='unknown' for f in cm['facts'].values()));self.assertFalse(cm['model_disclosure_blocked']);self.assertEqual(len(calls),1)
                    fresh=service.read(mid);second={**body,'expected_version':fresh['expected_version'],'goal_text':'重复点击不会覆盖已存人工目标'};self.assertEqual(service.create(mid,second,uid()),saved)
                    with self.assertRaises(AppError) as conflict:service.create(mid,body,uid())
                    self.assertEqual(conflict.exception.status,409)
                    artifact=cm['artifacts'][0];store.mutate('control',child,{'expected_version':cm['version'],'command':'pause'},uid());store.mutate('artifact',artifact['id'],{'base_version':1,'edited_content':'本人独立用餐人工稿，未安排或扣餐'},uid())
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(reopened.download(artifact['id'],2),'本人独立用餐人工稿，未安排或扣餐');self.assertEqual(reopened.detail(child)['assistant_status'],'paused')
                    cm=store.detail(child);store.mutate('control',child,{'expected_version':cm['version'],'command':'resume'},uid());cm=store.detail(child);store.mutate('prepare',child,{'expected_version':cm['version'],'kind':'prepare','mode':'model'},uid())
                    def changed_during_call(request,timeout):
                        calls.append(json.loads(request.data));m=store.detail(mid);store.mutate('source',mid,{'expected_version':m['version'],'kind':'user_text','text':'工程更正：能否赶回仍待核。'},uid())
                        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'content':'迟到响应不得发布','candidates':{},'questions':[],'related_tasks':[]},ensure_ascii=False)}}],'usage':{'total_tokens':100}}).encode())
                    client._opener=SimpleNamespace(open=changed_during_call);self.assertTrue(runner.step());self.assertEqual(len(calls),2)
                    self.assertTrue(any(a['status']=='cancelled' for a in store.detail(child)['actions']));self.assertFalse(any('迟到响应' in a['content'] for a in store.detail(child)['artifacts']));self.assertTrue(service.read(mid)['stale']);self.assertTrue(store.detail(child)['related_origin']['stale']);self.assertEqual(store.detail(child)['artifacts'][0]['content'],'本人独立用餐人工稿，未安排或扣餐')
                    cm=store.detail(child);store.mutate('control',child,{'expected_version':cm['version'],'command':'resume'},uid());cm=store.detail(child)
                    with self.assertRaises(AppError) as blocked:store.mutate('prepare',child,{'expected_version':cm['version'],'kind':'prepare','mode':'model'},uid())
                    self.assertEqual(blocked.exception.status,409);self.assertEqual(service.create(mid,body,key),saved)
                    store.mutate('control',child,{'expected_version':cm['version'],'command':'pause'},uid());cm=store.detail(child);service.unlink(child,{'expected_version':cm['version'],'input_revision':cm['revision_no'],'acknowledged_copy_review':True},uid());self.assertFalse(store.detail(child)['model_disclosure_blocked']);self.assertEqual(store.detail(child)['assistant_status'],'paused');self.assertEqual(store.download(artifact['id'],2),'本人独立用餐人工稿，未安排或扣餐')
                    if protected:
                        with store.as_actor(actors[1]),self.assertRaises(AppError) as denied:service.read(mid)
                        self.assertEqual(denied.exception.status,404)
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors[0]['id'],))
                        with self.assertRaises(AppError) as revoked:service.read(child)
                        self.assertEqual(revoked.exception.status,403)

if __name__=='__main__':unittest.main()
