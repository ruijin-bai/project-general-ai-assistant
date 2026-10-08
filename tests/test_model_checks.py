"""HR requirement proposals remain pending, editable, scoped and recoverable."""
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from assistant_app.checklist import ChecklistService
from assistant_app.model import ModelClient
from assistant_app.model_checks import normalize_checks
from assistant_app.server import Runner
from assistant_app.store import AppError,Store,uid

class ModelChecksSmoke(unittest.TestCase):
    def test_requirements_material_candidates_preservation_and_no_decisions(self):
        for protected in (False,True):
            with self.subTest(protected=protected),tempfile.TemporaryDirectory(prefix='qa-model-checks-',dir=Path(__file__).resolve().parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(i),'fixture'));actors[i]={'id':aid,'auth_epoch':1}
                with store.as_actor(actors[0]):
                    m=store.mutate('create',None,{'domain':'hr','goal_text':'工程准备核对，非真实招聘，不评定资格或录用','auto_prepare':False},uid())
                    lines=['工程假设要求：提供履历文字。','工程假设要求：提供经历材料。','工程假设材料：本人履历片段，未提供经历材料。']
                    m=store.mutate('source',m['id'],{'expected_version':m['version'],'kind':'user_text','text':'\n'.join(lines)},uid());source=m['sources'][-1]
                    def ref(n):return {'source_index':2,'start_line':n,'end_line':n,'quote':lines[n-1]}
                    proposed=[{'requirement_text':'提供履历文字。','requirement_refs':ref(1),'provided_refs':ref(3),'note':'履历匹配候选，实际适用待核。'},{'requirement_text':'提供经历材料。','requirement_refs':[ref(2)],'provided_refs':[],'note':'材料未提供，保持待补。'}]
                    normalized=normalize_checks(proposed,m);self.assertTrue(all(item['check']=='pending' for item in normalized))
                    for patch in ({'check':'local_source_checked'},{'requirement_text':'必须满足模型编造标准'}):
                        bad=json.loads(json.dumps(proposed));bad[0].update(patch)
                        with self.assertRaises(ValueError):normalize_checks(bad,m)
                    bad=json.loads(json.dumps(proposed));bad[0]['requirement_refs']['quote']='伪造要求'
                    with self.assertRaises(ValueError):normalize_checks(bad,m)
                    service=ChecklistService(store);empty=service.get_checks(m['id']);old={'id':'manual-old','requirement_text':'本人原核对目标','requirement_refs':[{'source_id':source['id'],'start_line':1,'end_line':1}],'provided_refs':[],'check':'pending','note':'旧人工备注不得覆盖'}
                    service.save_checks(m['id'],{'expected_version':m['version'],'input_revision':m['revision_no'],'base_structure_version':empty['structure_version'],'upserts':[old],'removed_ids':[]},uid());m=store.detail(m['id'])
                    m=store.mutate('manual_artifact',m['id'],{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'旧人工稿，未作任何人事决定'},uid());original=store.detail(m['id'])
                    client=ModelClient(enabled=True,api_key='EngineeringStubOnly');calls=[]
                    output={'content':'工程核对候选','candidates':{},'questions':['经历材料尚未提供'],'check_candidates':proposed};raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(output,ensure_ascii=False)}}],'usage':{'total_tokens':123}}).encode()
                    def opened(request,timeout):
                        payload=json.loads(request.data);calls.append(payload);self.assertIn('check_candidates',payload['messages'][0]['content']);return BytesIO(raw)
                    client._opener=SimpleNamespace(open=opened);store.mutate('prepare',m['id'],{'expected_version':m['version'],'kind':'prepare','mode':'model'},uid())
                runner=Runner.__new__(Runner);runner.store=store;runner.model=client;runner.stopped=threading.Event();self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    before=store.detail(m['id']);batch=service.get_model_candidates(m['id']);self.assertEqual(store.detail(m['id']),before);self.assertEqual(len(calls),1)
                    self.assertEqual(service.get_checks(m['id'])['items'][0]['note'],old['note']);self.assertFalse(batch['stale']);self.assertEqual(batch['items'][0]['check'],'pending')
                    paused=store.mutate('control',m['id'],{'expected_version':before['version'],'command':'pause'},uid());current=service.get_checks(m['id'])
                    selected=[{key:item[key] for key in ('id','requirement_text','requirement_refs','provided_refs','check','note')} for item in batch['items']]
                    for item in selected:
                        for key in ('requirement_refs','provided_refs'):item[key]=[{k:v for k,v in ref.items() if k!='excerpt'} for ref in item[key]]
                    selected[0]['note']+=' 本人核对补充，未录用。';data={'expected_version':paused['version'],'input_revision':paused['revision_no'],'base_structure_version':current['structure_version'],'upserts':selected,'removed_ids':[]};key=uid();saved=service.save_checks(m['id'],data,key)
                    self.assertEqual(service.save_checks(m['id'],data,key),saved);self.assertEqual(len(saved['items']),3);self.assertEqual(saved['items'][0]['note'],old['note']);self.assertTrue(all(item['compliance']=='unknown' for item in saved['items']))
                    self.assertTrue(any(gap['reason']=='未提供对应材料' for gap in saved['gaps']));self.assertTrue(service.get_model_candidates(m['id'])['stale'])
                    detail=store.detail(m['id']);self.assertEqual(detail['assistant_status'],'paused');self.assertEqual(detail['tracks'],original['tracks']);self.assertTrue(any(a['content']=='旧人工稿，未作任何人事决定' for a in detail['artifacts']))
                    with self.assertRaises(AppError) as conflict:service.save_checks(m['id'],data,uid())
                    self.assertEqual(conflict.exception.status,409)
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(ChecklistService(reopened).get_checks(m['id'])['items'],saved['items'])
                    self.assertEqual(len(calls),1)
                if protected:
                    with store.as_actor(actors[1]),self.assertRaises(AppError) as denied:ChecklistService(store).get_model_candidates(m['id'])
                    self.assertEqual(denied.exception.status,404)
