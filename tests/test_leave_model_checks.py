"""Leave requirements/material matches stay private, pending and independent."""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import threading
import unittest
from assistant_app.store import Store, AppError, uid
from assistant_app.model import ModelClient
from assistant_app.model_checks import normalize_checks
from assistant_app.server import Runner
from assistant_app.checklist import ChecklistService
from assistant_app.journey import JourneyService, SEMANTICS
from assistant_app.material_nodes import MaterialNodesService
from assistant_app.material_records import MaterialRecordsService
from assistant_app.waiting import WaitingService


class LeaveModelChecksSmoke(unittest.TestCase):
    def test_match_edit_reopen_and_independent_material_states(self):
        for protected in (False, True):
            with self.subTest(protected=protected),tempfile.TemporaryDirectory(prefix='qa-leave-checks-',dir=Path(__file__).resolve().parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                service=ChecklistService(store);journey=JourneyService(store);nodes=MaterialNodesService(store)
                with store.as_actor(actors[0]):
                    m=store.mutate('create',None,{'domain':'leave','goal_text':'工程假设要求核对，非真实人员或手续；不判断可通行','auto_prepare':False},uid());mid=m['id']
                    lines=['工程假设本次要求：核对护照材料日期。','工程假设本次要求：提供国内证明原文，正式名称与适用未知。','工程假设材料：护照日期2026-11-30，非真实证件。']
                    m=store.mutate('source',mid,{'expected_version':m['version'],'kind':'user_text','text':'\n'.join(lines)},uid());source=m['sources'][-1]['id']
                    def ref(n):return {'source_index':2,'start_line':n,'end_line':n,'quote':lines[n-1]}
                    proposed=[{'requirement_text':'核对护照材料日期。','requirement_refs':ref(1),'provided_refs':ref(3),'note':'材料匹配候选，日期效力及适用仍未知。'},{'requirement_text':'提供国内证明原文，正式名称与适用未知。','requirement_refs':ref(2),'provided_refs':[],'note':'材料缺项待补，不生成证明。'}]
                    self.assertTrue(all(item['check']=='pending' for item in normalize_checks(proposed,m)))
                    wrong=json.loads(json.dumps(proposed));wrong[0]['note']='2027-05-30早于2026-11-10，模型计算错误样例'
                    neutral=normalize_checks(wrong,m)[0];self.assertNotIn('早于',neutral['note']);self.assertEqual(neutral['model_note'],wrong[0]['note'])
                    for patch in ({'requirement_text':'模型编造的办理窗口'},{'check':'approved'},{'provided_refs':ref(1)}):
                        bad=json.loads(json.dumps(proposed));bad[0].update(patch)
                        with self.assertRaises(ValueError):normalize_checks(bad,m)
                    material={'id':uid(),'kind':'passport','availability':'source_provided','provider_class':'current_actor_statement','valid_until':'2026-11-30','check':'local_source_checked','source_id':source,'source_position':'第3–3行'}
                    m=journey.save_materials(mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'upserts':[material],'removed_ids':[]},uid())
                    candidate=nodes.get_candidates(mid)['items'][0];node=nodes.bind(mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'material_id':material['id'],'mode':'create','basis_token':candidate['basis_token'],'who':'本人','due_at':None,'note':'原本人提醒'},uid())
                    fresh=store.detail(mid);check=service.get_checks(mid);old={'id':'manual-old','requirement_text':'本人原核对目标','requirement_refs':[{'source_id':source,'start_line':1,'end_line':1}],'provided_refs':[],'check':'pending','note':'旧人工备注不能覆盖'}
                    service.save_checks(mid,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'base_structure_version':check['structure_version'],'upserts':[old],'removed_ids':[]},uid())
                    fresh=store.detail(mid);original=store.mutate('manual_artifact',mid,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'edited_content':'本人原稿，手续与签发均未知，不得覆盖'},uid());original_id=original['saved_artifact_id'];tracks=original['tracks'];materials=journey.get_journey(mid)['materials']['items']
                    client=ModelClient(enabled=True,api_key='EngineeringStubOnly');calls=[]
                    output={'content':'工程要求匹配候选，正式手续未知。','candidates':{},'questions':['国内证明材料未提供。'],'check_candidates':proposed};raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(output,ensure_ascii=False)}}],'usage':{'total_tokens':123}}).encode()
                    def opened(request,timeout):
                        payload=json.loads(request.data);calls.append(payload);self.assertIn('NIS',payload['messages'][0]['content']);self.assertIn('不生成证明',payload['messages'][0]['content']);self.assertEqual(json.loads(payload['messages'][1]['content'])['domain'],'leave');return BytesIO(raw)
                    client._opener=SimpleNamespace(open=opened);store.mutate('prepare',mid,{'expected_version':original['version'],'kind':'prepare','mode':'model'},uid())
                runner=Runner.__new__(Runner);runner.store=store;runner.model=client;runner.stopped=threading.Event();self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    before=store.detail(mid);batch=service.get_model_candidates(mid);self.assertEqual(store.detail(mid),before);self.assertEqual(len(batch['items']),2);self.assertEqual(len(calls),1)
                    self.assertTrue(all(item['check']=='pending' for item in batch['items']));paused=store.mutate('control',mid,{'expected_version':before['version'],'command':'pause'},uid());current=service.get_checks(mid)
                    selected=[{key:item[key] for key in ('id','requirement_text','requirement_refs','provided_refs','check','note')} for item in batch['items']]
                    for item in selected:
                        for key in ('requirement_refs','provided_refs'):item[key]=[{k:v for k,v in r.items() if k!='excerpt'} for r in item[key]]
                    selected[0].update(check='local_source_checked',note='本人仅核工程原文，不核效力或可通行')
                    def save_checks(items):
                        fresh=store.detail(mid);current=service.get_checks(mid);return service.save_checks(mid,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'base_structure_version':current['structure_version'],'upserts':items,'removed_ids':[]},uid())
                    old_data={'expected_version':paused['version'],'input_revision':paused['revision_no'],'base_structure_version':current['structure_version'],'upserts':selected,'removed_ids':[]};key=uid();saved=service.save_checks(mid,old_data,key);self.assertEqual(service.save_checks(mid,old_data,key),saved)
                    self.assertEqual(saved['items'][0]['note'],old['note']);self.assertEqual(len(saved['items']),3);self.assertTrue(all(item['compliance']=='unknown' for item in saved['items']));self.assertEqual(journey.get_journey(mid)['materials']['items'],materials)
                    record=MaterialRecordsService(store).get_records(mid);self.assertTrue(all(item['submission']=='unknown' and item['receipt']=='unknown' for item in record['items']));self.assertTrue(WaitingService(store).get_waiting(mid)['items'][0]['basis_stale'])
                    fresh=store.detail(mid);candidate=nodes.get_candidates(mid)['items'][0];nodes.bind(mid,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'material_id':material['id'],'mode':'rebind','basis_token':candidate['basis_token']},uid())
                    selected[0]['note']='本人再次修改备注，原依据不变';save_checks([selected[0]]);self.assertFalse(WaitingService(store).get_waiting(mid)['items'][0]['basis_stale'])
                    with self.assertRaises(AppError):service.render_checks(mid,{'expected_version':store.detail(mid)['version'],'input_revision':store.detail(mid)['revision_no'],'structure_version':service.get_checks(mid)['structure_version']},uid())
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(ChecklistService(reopened).get_checks(mid)['items'],service.get_checks(mid)['items'])
                    with self.assertRaises(AppError) as conflict:service.save_checks(mid,old_data,uid())
                    self.assertEqual(conflict.exception.status,409)
                    fresh=store.detail(mid);active=store.mutate('control',mid,{'expected_version':fresh['version'],'command':'resume'},uid());checks=service.get_checks(mid);rendered=service.render_checks(mid,{'expected_version':active['version'],'input_revision':active['revision_no'],'structure_version':checks['structure_version']},uid());self.assertIn('材料未提供，待补',rendered['content']);self.assertIn('可通行',rendered['scope']);self.assertEqual(store.download(original_id,1),'本人原稿，手续与签发均未知，不得覆盖');self.assertEqual(store.detail(mid)['tracks'],tracks)
                    fresh=store.detail(mid);journey.save_plan(mid,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'plan_semantics':dict(SEMANTICS),'field_changes':{'flight_info':{'value':'工程假设航班变化','status':'confirmed'}}},uid());self.assertTrue(service.get_checks(mid)['stale']);self.assertTrue(service.get_model_candidates(mid)['stale']);self.assertEqual(len(calls),1)
                    with self.assertRaises(AppError) as stale:service.render_checks(mid,{'expected_version':store.detail(mid)['version'],'input_revision':store.detail(mid)['revision_no'],'structure_version':checks['structure_version']},uid())
                    self.assertEqual(stale.exception.status,409)
                if protected:
                    with store.as_actor(actors[1]),self.assertRaises(AppError) as private:service.get_model_candidates(mid)
                    self.assertEqual(private.exception.status,404)
                    with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors[0]['id'],))
                    with store.as_actor(actors[0]),self.assertRaises(AppError) as revoked:service.get_model_candidates(mid)
                    self.assertEqual(revoked.exception.status,403)
