import json,tempfile,threading,unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from assistant_app.store import Store,AppError,uid
from assistant_app.server import Runner
from assistant_app.model import ModelClient
from assistant_app.related_tasks import RelatedTasksService
from assistant_app.private_context import PrivateContextService,normalize_matches,title_snapshot
from assistant_app.source_lifecycle import SourceLifecycleService

class PrivateContextSmoke(unittest.TestCase):
    def test_titles_owned_consent_match_and_changed_basis(self):
        for protected in (False,True):
            with self.subTest(protected=protected),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                    with store.as_actor(actors[1]):store.mutate('create',None,{'domain':'dining','goal_text':'OTHER_OWNER_PRIVATE_TITLE','auto_prepare':False},uid())
                with store.as_actor(actors[0]):
                    def create(domain,goal):return store.mutate('create',None,{'domain':domain,'goal_text':goal,'auto_prepare':False},uid())
                    target=create('dining','工程独立午餐需求，同次设备核对，是否留饭待核');tid=target['id'];target=store.mutate('source',tid,{'expected_version':target['version'],'kind':'user_text','text':'PRIVATE_TARGET_SOURCE_NOT_SENT'},uid());target=store.mutate('facts',tid,{'expected_version':target['version'],'field_changes':{'person_ref':'PRIVATE_TARGET_FACT_NOT_SENT'}},uid())
                    blocked=create('hr','WITHHELD_TITLE_NOT_SENT');SourceLifecycleService(store).set_source_state(blocked['id'],blocked['sources'][0]['id'],{'expected_version':blocked['version'],'input_revision':blocked['revision_no'],'state':'withheld','replacement_source_id':None,'note':'工程暂不用'},uid())
                    create('general','LONG_TITLE_NOT_SENT'+('x'*1100));m=create('general','工程原话：外出设备核对，可能赶不上午餐，留饭没定');mid=m['id'];m=store.mutate('manual_artifact',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'PRIVATE_MANUAL_DRAFT_NOT_SENT'},uid());aid=m['saved_artifact_id'];service=PrivateContextService(store);client=ModelClient(enabled=True,api_key='EngineeringStubOnly');calls=[]
                    self.assertNotIn('other_tasks',json.loads(client._input(store.detail(mid))[0]));preview=service.preview(mid);self.assertFalse(preview['enabled']);self.assertEqual(len(preview['items']),1);self.assertEqual(preview['items'][0]['matter_id'],tid)
                    store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid());m=store.detail(mid);service.save_scope(mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'enabled':True},uid());self.assertEqual(store.detail(mid)['assistant_status'],'paused');self.assertEqual(store.download(aid,1),'PRIVATE_MANUAL_DRAFT_NOT_SENT');target_before=store.detail(tid)
                    preview=service.preview(mid);context={'items':preview['items']};detail={**store.detail(mid),'_related_context':context};proposal={'target_index':1,'target_quote':target_before['goal_text'],'reason':'可核对是否同次需求，不据标题认定已经留饭','refs':[{'source_index':1,'start_line':1,'end_line':1,'quote':detail['goal_text']}]}
                    self.assertEqual(normalize_matches([proposal],detail)[0]['target']['matter_id'],tid)
                    for bad in ({**proposal,'target_index':999},{**proposal,'target_quote':'伪造标题'},{**proposal,'refs':[{**proposal['refs'][0],'quote':'伪造原话'}]}):
                        with self.assertRaises(ValueError):normalize_matches([bad],detail)
                    with self.assertRaises(ValueError):normalize_matches([proposal],store.detail(mid))
                    def opened(request,timeout):
                        body=json.loads(request.data);calls.append(body);text=body['messages'][1]['content'];sent=json.loads(text)
                        for secret in ('PRIVATE_TARGET_SOURCE_NOT_SENT','PRIVATE_TARGET_FACT_NOT_SENT','PRIVATE_MANUAL_DRAFT_NOT_SENT','OTHER_OWNER_PRIVATE_TITLE','WITHHELD_TITLE_NOT_SENT','LONG_TITLE_NOT_SENT'):self.assertNotIn(secret,text)
                        self.assertEqual(set(sent['other_tasks'][0]),{'title_index','domain','goal_text'});item={**proposal,'target_quote':sent['other_tasks'][0]['goal_text']}
                        response={'content':'工程当前准备稿','candidates':{},'questions':[],'related_tasks':[],'related_matches':[item]}
                        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(response,ensure_ascii=False)}}],'usage':{'total_tokens':100}}).encode())
                    client._opener=SimpleNamespace(open=opened);m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'resume'},uid());m=store.detail(mid);store.mutate('prepare',mid,{'expected_version':m['version'],'kind':'prepare','mode':'model'},uid())
                runner=Runner.__new__(Runner);runner.store=store;runner.model=client;runner.stopped=threading.Event();self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid());m=store.detail(mid);view=RelatedTasksService(store).read(mid)['matches'];self.assertFalse(view['stale']);self.assertEqual(len(calls),1)
                    data={'expected_version':m['version'],'input_revision':m['revision_no'],'action_id':view['action_id'],'match_id':view['items'][0]['id'],'note':'本人核对同次需求，先沿已有事项续办'};key=uid();saved=service.save_match(mid,data,key);self.assertEqual(service.save_match(mid,data,key),saved);self.assertEqual(store.detail(tid),target_before);self.assertEqual(store.detail(mid)['assistant_status'],'paused')
                    fresh=store.detail(mid);self.assertEqual(service.save_match(mid,{**data,'expected_version':fresh['version'],'note':'重复不覆盖'},uid()),saved);self.assertEqual(len(RelatedTasksService(store).read(mid)['matches']['linked']),1)
                    service.withdraw(mid,{'expected_version':fresh['version'],'link_id':saved['link_id']},uid());self.assertTrue(RelatedTasksService(store).read(mid)['matches']['linked'][0]['withdrawn'])
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(RelatedTasksService(reopened).read(mid)['matches']['linked'][0]['note'],data['note']);self.assertEqual(reopened.download(aid,1),'PRIVATE_MANUAL_DRAFT_NOT_SENT')
                    # A title changes before the queued call: no request is sent.
                    m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'resume'},uid());m=store.detail(mid);store.mutate('prepare',mid,{'expected_version':m['version'],'kind':'prepare','mode':'model'},uid());t=store.detail(tid);store.mutate('goal',tid,{'expected_version':t['version'],'goal_text':'工程更正午餐需求，范围仍待核','recheck_fields':[]},uid());self.assertTrue(runner.step());self.assertEqual(len(calls),1);self.assertTrue(RelatedTasksService(store).read(mid)['matches']['stale'])
                    # A title changes during a real client call: usage kept, output discarded.
                    m=store.detail(mid);store.mutate('prepare',mid,{'expected_version':m['version'],'kind':'prepare','mode':'model'},uid())
                    def changed(request,timeout):
                        raw=opened(request,timeout);t=store.detail(tid);store.mutate('goal',tid,{'expected_version':t['version'],'goal_text':'工程再次更正午餐范围','recheck_fields':[]},uid());return raw
                    client._opener=SimpleNamespace(open=changed);self.assertTrue(runner.step());self.assertEqual(len(calls),2)
                    with store.connect() as c:usage=[json.loads(r[0]) for r in c.execute("SELECT message FROM events WHERE matter_id=? AND event_type='model_usage'",(mid,))]
                    self.assertTrue(usage[-1]['discarded']);self.assertEqual(usage[-1]['usage']['total_tokens'],100)
                    m=store.detail(mid);service.save_scope(mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'enabled':False},uid());self.assertNotIn('other_tasks',json.loads(client._input(store.detail(mid))[0]));self.assertTrue(RelatedTasksService(store).read(mid)['matches']['stale']);self.assertEqual(store.download(aid,1),'PRIVATE_MANUAL_DRAFT_NOT_SENT')
                    if protected:
                        with store.as_actor(actors[1]),self.assertRaises(AppError) as denied:service.preview(mid)
                        self.assertEqual(denied.exception.status,404)
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors[0]['id'],))
                        with self.assertRaises(AppError) as revoked:service.preview(mid)
                        self.assertEqual(revoked.exception.status,403)

if __name__=='__main__':unittest.main()
