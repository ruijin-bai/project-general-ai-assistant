"""Affected model contract and owner-only proposal -> editable structure smoke."""
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from assistant_app.meeting_candidates import normalize_meeting_items
from assistant_app.model import ModelClient, ModelError
from assistant_app.server import Runner
from assistant_app.source_lifecycle import SourceLifecycleService
from assistant_app.store import AppError, Store, uid
from assistant_app.structured import StructuredService


class MeetingCandidateSmoke(unittest.TestCase):
    def test_model_quote_validation_private_history_and_structured_continuation(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix='qa-meeting-candidates-', dir=Path(__file__).resolve().parent) as folder:
                store = Store(folder)
                actors = [None, None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as connection:
                        for index in range(2):
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')", (actor_id, 'fixture'+str(index), 'fixture'))
                            actors[index] = {'id': actor_id, 'auth_epoch': 1}
                with store.as_actor(actors[0]):
                    matter = store.mutate('create', None, {'domain':'meeting','goal_text':'工程会议候选验证，非真实会议','auto_prepare':False}, uid())
                    matter = store.mutate('source', matter['id'], {'expected_version':matter['version'],'kind':'user_text','text':'讨论：工程位置待核。\n行动：工程经办拟在2026-10-11核对缺项，未实际接受或执行。'}, uid())
                    source = matter['sources'][-1]
                    matter = store.mutate('manual_artifact', matter['id'], {'expected_version':matter['version'],'input_revision':matter['revision_no'],'edited_content':'原人工稿不得覆盖'}, uid())
                    item = {'kind':'action','text':'拟核对缺项，尚未接受或执行','responsible_text':'工程经办','deadline_text':'2026-10-11','refs':[{'source_index':2,'start_line':2,'end_line':2,'quote':source['text'].splitlines()[1]}]}
                    normalized = normalize_meeting_items([item], matter)
                    self.assertIsNone(normalized[0]['responsible_account_id'])
                    self.assertEqual(normalized[0]['deadline'], {'kind':'unknown','value':None})
                    for field, value in [('quote','伪造摘录'),('end_line',3),('source_index',99)]:
                        invalid = json.loads(json.dumps(item));invalid['refs'][0][field] = value
                        with self.assertRaises(ValueError):normalize_meeting_items([invalid], matter)
                    invalid = {**item,'responsible_text':'未见的责任人'}
                    with self.assertRaises(ValueError):normalize_meeting_items([invalid], matter)
                    result = {'content':'工程候选稿，未开会或执行','candidates':{},'questions':[],'meeting_items':[item]}
                    envelope = json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result,ensure_ascii=False)}}],'usage':{'total_tokens':123}}).encode()
                    client = ModelClient(enabled=True, api_key='EngineeringStubOnly')
                    calls = []
                    def opened(request, timeout):
                        sent = json.loads(request.data);calls.append(sent)
                        self.assertIn('meeting_items', sent['messages'][0]['content'])
                        self.assertEqual(json.loads(sent['messages'][1]['content'])['sources'][1]['source_index'],2)
                        return BytesIO(envelope)
                    client._opener = SimpleNamespace(open=opened)
                    matter = store.mutate('prepare',matter['id'],{'expected_version':matter['version'],'kind':'prepare','mode':'model'},uid())
                runner = Runner.__new__(Runner);runner.store = store;runner.model = client;runner.stopped = threading.Event()
                self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    service = StructuredService(store)
                    before = store.detail(matter['id'])
                    candidates = service.get_meeting_candidates(matter['id'])
                    self.assertFalse(candidates['stale']);self.assertEqual(len(candidates['items']),1)
                    self.assertEqual(len(calls),1)
                    self.assertEqual(store.detail(matter['id']),before)
                    self.assertTrue(any(a['content']=='原人工稿不得覆盖' for a in before['artifacts']))
                    paused = store.mutate('control',matter['id'],{'expected_version':before['version'],'command':'pause'},uid())
                    view = service.get_meeting_structure(matter['id'])
                    incoming = {key:value for key,value in candidates['items'][0].items() if key!='refs'}
                    incoming['refs']=[{key:value for key,value in ref.items() if key!='excerpt'} for ref in candidates['items'][0]['refs']]
                    incoming['text'] += '；本人编辑说明'
                    saved = service.save_meeting_structure(matter['id'],{'expected_version':paused['version'],'input_revision':paused['revision_no'],'base_structure_version':view['structure_version'],'upserts':[incoming],'removed_ids':[]},uid())
                    self.assertEqual(saved['items'][0]['deadline']['kind'],'unknown')
                    self.assertEqual(saved['items'][0]['progress']['acceptance'],'unknown')
                    self.assertEqual(store.detail(matter['id'])['assistant_status'],'paused')
                    old = service.get_meeting_candidates(matter['id']);self.assertTrue(old['stale'])
                    self.assertNotIn('本人编辑说明',old['items'][0]['text'])
                    self.assertEqual(len(calls),1)
                    reopened = Store(folder)
                    with reopened.as_actor(actors[0]):
                        self.assertEqual(StructuredService(reopened).get_meeting_structure(matter['id'])['items'][0]['text'],incoming['text'])
                    current = store.detail(matter['id'])
                    SourceLifecycleService(store).set_source_state(matter['id'],source['id'],{'expected_version':current['version'],'input_revision':current['revision_no'],'state':'withheld','replacement_source_id':None,'note':'工程暂不用来源'},uid())
                    with self.assertRaises(AppError) as withheld:service.get_meeting_candidates(matter['id'])
                    self.assertEqual(withheld.exception.status,409)
                if protected:
                    with store.as_actor(actors[1]), self.assertRaises(AppError) as denied:StructuredService(store).get_meeting_candidates(matter['id'])
                    self.assertEqual(denied.exception.status,404)
