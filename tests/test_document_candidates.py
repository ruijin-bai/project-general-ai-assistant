"""One document proposal -> editable sections -> saved draft and preservation smoke."""
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from assistant_app.meeting_candidates import normalize_document_sections
from assistant_app.model import ModelClient, ModelError
from assistant_app.server import Runner
from assistant_app.store import AppError, Store, uid
from assistant_app.structured import StructuredService


class DocumentCandidateSmoke(unittest.TestCase):
    def test_document_references_sections_preservation_and_scoped_read(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix='qa-doc-candidates-',dir=Path(__file__).resolve().parent) as folder:
                store = Store(folder);actors = [None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for index in range(2):
                            aid = uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(index),'fixture'));actors[index]={'id':aid,'auth_epoch':1}
                with store.as_actor(actors[0]):
                    matter = store.mutate('create',None,{'domain':'document','goal_text':'工程起草，非真实来文或正式回复','auto_prepare':False},uid())
                    matter = store.mutate('source',matter['id'],{'expected_version':matter['version'],'kind':'user_text','text':'工程来文：请核对设施位置和可进入时段。\n已知底稿：具体位置及可进入时段均待核，未完成检查。'},uid())
                    source = matter['sources'][-1]
                    section = {'heading':'待核事项','body':'设施位置和可进入时段均待核，未完成检查。','refs':[{'source_index':2,'start_line':2,'end_line':2,'quote':source['text'].splitlines()[1]}]}
                    normalized = normalize_document_sections([section],matter)
                    self.assertEqual(normalized[0]['confirmation'],'candidate')
                    bad = {**section,'issued':True}
                    with self.assertRaises(ValueError):normalize_document_sections([bad],matter)
                    bad = json.loads(json.dumps(section));bad['refs'][0]['quote']='伪造签发原文'
                    with self.assertRaises(ValueError):normalize_document_sections([bad],matter)
                    matter = store.mutate('manual_artifact',matter['id'],{'expected_version':matter['version'],'input_revision':matter['revision_no'],'edited_content':'原人工正文，保留实际未签发状态'},uid())
                    client = ModelClient(enabled=True,api_key='EngineeringStubOnly');calls=[]
                    response={'content':'通用工程候选','candidates':{},'questions':[],'document_sections':[section]}
                    raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(response,ensure_ascii=False)}}],'usage':{'total_tokens':123}}).encode()
                    def opened(request,timeout):
                        payload=json.loads(request.data);calls.append(payload)
                        self.assertIn('document_sections',payload['messages'][0]['content'])
                        self.assertEqual(json.loads(payload['messages'][1]['content'])['sources'][1]['source_index'],2)
                        return BytesIO(raw)
                    client._opener=SimpleNamespace(open=opened)
                    matter=store.mutate('prepare',matter['id'],{'expected_version':matter['version'],'kind':'prepare','mode':'model'},uid())
                runner=Runner.__new__(Runner);runner.store=store;runner.model=client;runner.stopped=threading.Event();self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    service=StructuredService(store);before=store.detail(matter['id']);candidates=service.get_document_candidates(matter['id'])
                    self.assertEqual(store.detail(matter['id']),before);self.assertFalse(candidates['stale']);self.assertEqual(len(calls),1)
                    self.assertTrue(any(a['content']=='原人工正文，保留实际未签发状态' for a in before['artifacts']))
                    paused=store.mutate('control',matter['id'],{'expected_version':before['version'],'command':'pause'},uid())
                    structure=service.get_document_structure(matter['id']);section={**candidates['items'][0]};section['refs']=[{k:v for k,v in ref.items() if k!='excerpt'} for ref in section['refs']];section['body']+=' 人工补充：未外发。'
                    data={'expected_version':paused['version'],'input_revision':paused['revision_no'],'base_structure_version':structure['structure_version'],'upserts':[section],'removed_ids':[]}
                    key=uid();saved=service.save_document_structure(matter['id'],data,key)
                    self.assertEqual(service.save_document_structure(matter['id'],data,key),saved)
                    self.assertEqual(store.detail(matter['id'])['assistant_status'],'paused')
                    self.assertTrue(service.get_document_candidates(matter['id'])['stale'])
                    self.assertEqual(before['tracks'],store.detail(matter['id'])['tracks'])
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):self.assertEqual(StructuredService(reopened).get_document_structure(matter['id'])['sections'][0]['body'],section['body'])
                    self.assertEqual(len(calls),1)
                    # Rejected provider output still records only validated usage;
                    # no quoted provider text leaks into the diagnostic or saved draft.
                    rejected=json.loads(json.dumps(response));rejected['document_sections'][0]['refs'][0]['quote']='伪造签发原文'
                    raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(rejected,ensure_ascii=False)}}],'usage':{'total_tokens':123}}).encode()
                    with self.assertRaises(ModelError) as invalid:client.prepare(store.detail(matter['id']))
                    self.assertIn('引用摘录',str(invalid.exception));self.assertNotIn('伪造签发',str(invalid.exception));self.assertEqual(invalid.exception.usage,{'total_tokens':123})
                    current=store.mutate('control',matter['id'],{'expected_version':store.detail(matter['id'])['version'],'command':'resume'},uid())
                    store.mutate('prepare',matter['id'],{'expected_version':current['version'],'kind':'prepare','mode':'model'},uid())
                self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    failed=store.detail(matter['id'])
                    self.assertTrue(any('合同校验拒绝' in event.get('message','') for event in failed['activities']))
                    self.assertEqual([{k:v for k,v in a.items() if k!='stale'} for a in failed['artifacts']], [{k:v for k,v in a.items() if k!='stale'} for a in before['artifacts']])
                if protected:
                    with store.as_actor(actors[1]),self.assertRaises(AppError) as denied:StructuredService(store).get_document_candidates(matter['id'])
                    self.assertEqual(denied.exception.status,404)
