"""Literal travel proposals -> existing human plan -> paused/reopened saved draft."""
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from assistant_app.model import ModelClient
from assistant_app.model_journey import normalize_journey
from assistant_app.server import Runner
from assistant_app.store import Store, AppError, uid
from assistant_app.journey import JourneyService, SEMANTICS


class ModelJourneySmoke(unittest.TestCase):
    def test_literal_plan_confirm_pause_reopen_and_privacy(self):
        for protected in (False,True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix='qa-model-journey-',dir=Path(__file__).resolve().parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                with store.as_actor(actors[0]):
                    m=store.mutate('create',None,{'domain':'travel','goal_text':'工程计划核对，非真实出行','auto_prepare':False},uid());mid=m['id']
                    lines=['目的地原述：工程机场','计划离营：2026-11-10T09:00:00+01:00','计划返营：2026-11-10T16:00:00+01:00','用途：本人旧用途','明天，具体日期待核']
                    m=store.mutate('source',mid,{'expected_version':m['version'],'kind':'user_text','text':'\n'.join(lines)},uid())
                    journey=JourneyService(store);journey.save_plan(mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'field_changes':{'purpose':{'value':'本人旧用途','status':'confirmed'}},'plan_semantics':dict(SEMANTICS)},uid())
                    m=store.detail(mid);m=store.mutate('manual_artifact',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'旧人工准备稿保持'},uid());old=m['saved_artifact_id'];before=store.detail(mid)
                    def proposal(field,value,line):return {'field':field,'value':value,'refs':{'source_index':2,'start_line':line,'end_line':line,'quote':lines[line-1]}}
                    proposals=[proposal('destination','工程机场',1),proposal('departure_at','2026-11-10T09:00:00+01:00',2),proposal('return_at','2026-11-10T16:00:00+01:00',3),proposal('purpose','本人旧用途',4)]
                    self.assertEqual(len(normalize_journey(proposals,before)),3)
                    for bad in ([proposal('departure_at','2026-11-10',2)],[proposal('departure_at','明天',5)],[{**proposals[0],'value':'模型编造地点'}],[proposals[0],proposals[0]]):
                        with self.assertRaises(ValueError):normalize_journey(bad,before)
                    response={'content':'工程计划候选，未批准未执行','candidates':{},'questions':['同行与用车待核'],'journey_candidates':proposals};calls=[];client=ModelClient(enabled=True,api_key='EngineeringStubOnly')
                    raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(response,ensure_ascii=False)}}],'usage':{'total_tokens':123}}).encode()
                    def opened(request,timeout):
                        data=json.loads(request.data);calls.append(data);self.assertIn('journey_candidates',data['messages'][0]['content']);self.assertEqual(json.loads(data['messages'][1]['content'])['sources'][1]['source_index'],2);return BytesIO(raw)
                    client._opener=SimpleNamespace(open=opened);store.mutate('prepare',mid,{'expected_version':m['version'],'kind':'prepare','mode':'model'},uid())
                runner=Runner.__new__(Runner);runner.store=store;runner.model=client;runner.stopped=threading.Event();self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    data=journey.get_model_candidates(mid);m=store.detail(mid);self.assertFalse(data['stale']);self.assertEqual(len(data['items']),3);self.assertEqual(m['facts'],before['facts']);self.assertEqual(m['tracks'],before['tracks']);self.assertEqual(journey.get_model_candidates(mid),data);self.assertEqual(len(calls),1)
                    store.mutate('control',mid,{'expected_version':m['version'],'command':'pause'},uid());m=store.detail(mid)
                    request={'expected_version':m['version'],'input_revision':m['revision_no'],'field_changes':{i['field']:{'value':i['value'],'status':'confirmed'} for i in data['items']},'plan_semantics':dict(SEMANTICS)};key=uid();saved=journey.save_plan(mid,request,key);self.assertEqual(journey.save_plan(mid,request,key),saved)
                    self.assertEqual(store.detail(mid)['assistant_status'],'paused');self.assertEqual(saved['journey']['plan_fields']['purpose']['value'],'本人旧用途');self.assertTrue(journey.get_model_candidates(mid)['stale'])
                    with self.assertRaises(AppError) as conflict:journey.save_plan(mid,request,uid())
                    self.assertEqual(conflict.exception.status,409)
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(JourneyService(reopened).get_journey(mid)['journey'],saved['journey']);self.assertEqual(reopened.download(old,1),'旧人工准备稿保持')
                    m=store.detail(mid);store.mutate('control',mid,{'expected_version':m['version'],'command':'resume'},uid());m=store.detail(mid)
                    result=journey.render(mid,{'expected_version':m['version'],'input_revision':m['revision_no']},uid())
                    self.assertTrue(any('2026-11-10T09:00:00+01:00' in a['content'] for a in store.detail(mid)['artifacts']));self.assertEqual(store.detail(mid)['tracks'],before['tracks']);self.assertFalse(journey.get_journey(mid)['movements']);self.assertFalse(journey.get_journey(mid)['journey']['meal_requests']);self.assertEqual(len(calls),1)
                    if protected:
                        with store.as_actor(actors[1]),self.assertRaises(AppError) as denied:journey.get_model_candidates(mid)
                        self.assertEqual(denied.exception.status,404)
                        with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors[0]['id'],))
                        with self.assertRaises(AppError) as revoked:journey.get_model_candidates(mid)
                        self.assertEqual(revoked.exception.status,403)


if __name__=='__main__':unittest.main()
