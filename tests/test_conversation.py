import json,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from io import BytesIO
from assistant_app.store import Store,AppError,uid
from assistant_app.server import Runner
from assistant_app.model import ModelClient

class ConversationSmoke(unittest.TestCase):
    def test_messages_preserve_human_state_and_stop_late_output(self):
        for protected in (False,True):
            with self.subTest(protected=protected),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'conversation'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                with store.as_actor(actors[0]):
                    m=store.mutate('create',None,{'domain':'travel','goal_text':'工程出行原目标','auto_prepare':False},uid());mid=m['id']
                    m=store.mutate('facts',mid,{'expected_version':m['version'],'field_changes':{'departure_at':'2026-11-12T09:00:00+01:00'}},uid())
                    m=store.mutate('manual_artifact',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'本人原稿保留'},uid());aid=m['saved_artifact_id'];before=store.detail(mid)
                    m=store.mutate('workflow',mid,{'expected_version':m['version'],'enabled':True},uid())
                    m=store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid())
                    payload={'expected_version':m['version'],'text':'出发改到下午，仍需核对；别替我批准。','mode':'save'};key=uid();m=store.mutate('message',mid,payload,key)
                    self.assertEqual(store.mutate('message',mid,payload,key),m)
                    self.assertEqual(m['assistant_status'],'paused');self.assertEqual(m['facts'],before['facts']);self.assertEqual(m['tracks'],before['tracks']);self.assertEqual(m['goal_text'],before['goal_text']);self.assertEqual(store.download(aid,1),'本人原稿保留')
                    self.assertFalse(any(a['status'] in {'queued','running'} for a in m['actions']))
                    for body in ({**payload,'expected_version':m['version'],'mode':'model'},{**payload,'expected_version':m['version'],'mode':'save','approve':True},payload):
                        with self.assertRaises(AppError):store.mutate('message',mid,body,uid())
                    self.assertEqual(len(store.detail(mid)['sources']),2)
                    m=store.mutate('control',mid,{'expected_version':m['version'],'command':'resume'},uid())
                    m=store.mutate('message',mid,{'expected_version':m['version'],'text':'请先列缺项，具体时间未核。','mode':'model'},uid())
                client=ModelClient(enabled=True,api_key='EngineeringStubOnly');calls=[]
                def opened(request,timeout):
                    calls.append(json.loads(request.data));m=store.detail(mid)
                    store.mutate('message',mid,{'expected_version':m['version'],'text':'又补充：先只保存，停止旧整理。','mode':'save'},uid())
                    self.assertNotIn('本人原稿保留',calls[-1]['messages'][1]['content'])
                    result={'content':'旧输入的候选不发布','candidates':{},'questions':['具体出发时间需要你核对。']}
                    return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result,ensure_ascii=False)}}],'usage':{'total_tokens':123}}).encode())
                client._opener=SimpleNamespace(open=opened);runner=Runner.__new__(Runner);runner.store=store;runner.model=client;runner.stopped=threading.Event();self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    m=store.detail(mid);self.assertEqual(len(calls),1);self.assertEqual(m['assistant_status'],'waiting');self.assertEqual(len(m['artifacts']),1);self.assertEqual(m['conversation_questions'],[])
                    with store.connect() as c:usage=json.loads(c.execute("SELECT message FROM events WHERE matter_id=? AND event_type='model_usage' ORDER BY recorded_at DESC LIMIT 1",(mid,)).fetchone()[0])
                    self.assertTrue(usage['discarded']);self.assertEqual(usage['usage']['total_tokens'],123)
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(reopened.detail(mid)['sources'][-1]['text'],'又补充：先只保存，停止旧整理。');self.assertEqual(reopened.download(aid,1),'本人原稿保留')
                    if protected:
                        with store.as_actor(actors[1]),self.assertRaises(AppError) as denied:store.mutate('message',mid,{'expected_version':m['version'],'text':'越权','mode':'save'},uid())
                        self.assertEqual(denied.exception.status,404)
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors[0]['id'],))
                        with self.assertRaises(AppError) as revoked:store.mutate('message',mid,{'expected_version':m['version'],'text':'撤权','mode':'save'},uid())
                        self.assertEqual(revoked.exception.status,403)

if __name__=='__main__':unittest.main()
