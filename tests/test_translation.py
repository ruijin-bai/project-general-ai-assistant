"""Selected saved-language draft preservation, rejected values, cancellation, recovery."""
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from assistant_app.model import ModelClient, ModelError
from assistant_app.server import Runner
from assistant_app.store import AppError, Store, uid
from assistant_app.translation import validate_output


class TranslationSmoke(unittest.TestCase):
    def test_fixed_saved_basis_only_and_preservation(self):
        original = '工程假设：2026-10-11拟核对2份资料，打印费0.3 USD，未批准、未执行；职责待核。'
        translated = 'Engineering hypothesis: review 2 documents on 2026-10-11; printing fee 0.3 USD. Not approved or executed; responsibilities remain unverified.'
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix='qa-translation-', dir=Path(__file__).resolve().parent) as folder:
                store = Store(folder); actors = [None, None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid = uid(); c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')", (aid, 'fixture'+str(i), 'fixture'))
                            actors[i] = {'id': aid, 'auth_epoch': 1}
                with store.as_actor(actors[0]):
                    matter = store.mutate('create', None, {'domain': 'general', 'goal_text': '语言工程验证，不是真实安排', 'auto_prepare': False}, uid())
                    matter = store.mutate('manual_artifact', matter['id'], {'expected_version': matter['version'], 'input_revision': matter['revision_no'], 'edited_content': original}, uid())
                    artifact = matter['artifacts'][0]; original_facts = matter['facts']; tracks = matter['tracks']
                    def data(m): return {'expected_version': m['version'], 'input_revision': m['revision_no'], 'artifact_id': artifact['id'], 'artifact_version': artifact['version'], 'target_language': 'en'}
                    output = {'content': translated, 'candidates': {}, 'questions': []}
                    for bad in (translated.replace('0.3', '0.4'), translated+' 7', translated.replace('USD', 'EUR'), translated.replace('2026-10-11', '11/10/2026')):
                        with self.assertRaises(ValueError): validate_output({**output, 'content': bad}, {'content': original})
                    with self.assertRaises(ValueError): validate_output({**output, 'candidates': {'decision': 'approved'}}, {'content': original})
                    client = ModelClient(enabled=True, api_key='EngineeringStubOnly'); calls = []; hook = [None]; returned = [output]
                    def opened(request, timeout):
                        payload = json.loads(request.data); sent = json.loads(payload['messages'][1]['content']); calls.append(sent)
                        self.assertEqual(sent, {'content': original, 'target_language': 'English'})
                        if hook[0]: hook[0]()
                        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(returned[0], ensure_ascii=False)}}], 'usage': {'total_tokens': 45}}).encode())
                    client._opener = SimpleNamespace(open=opened)
                    with self.assertRaises(ModelError) as withheld: client.prepare({**matter, 'model_disclosure_blocked': True, '_translation_basis': {'content': original, 'target_language': 'en'}})
                    self.assertEqual(withheld.exception.code, 'source_withheld'); self.assertEqual(len(calls), 0)
                    key = uid(); queued = store.mutate('translate', matter['id'], data(matter), key)
                    self.assertEqual(store.mutate('translate', matter['id'], data(matter), key), queued)
                runner = Runner.__new__(Runner); runner.store = store; runner.model = client; runner.stopped = threading.Event()
                self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    saved = store.detail(matter['id']); self.assertEqual(saved['facts'], original_facts); self.assertEqual(saved['tracks'], tracks)
                    language = next(a for a in saved['artifacts'] if a['type'] == 'language_draft')
                    self.assertEqual(language['content'], translated); self.assertEqual(language['language_basis']['artifact_version'], 1)
                    self.assertFalse(language['language_basis']['original_stale']); self.assertEqual(store.download(artifact['id'], 1), original)
                    # Same saved basis does not implicitly call again, even with a fresh request key.
                    store.mutate('translate', matter['id'], data(saved), uid()); self.assertFalse(runner.step()); self.assertEqual(len(calls), 1)
                    current = store.detail(matter['id']); paused = store.mutate('control', matter['id'], {'expected_version': current['version'], 'command': 'pause'}, uid())
                    with self.assertRaises(AppError) as stopped: store.mutate('translate', matter['id'], data(paused), uid())
                    self.assertEqual(stopped.exception.status, 409)
                    edited = store.mutate('artifact', language['id'], {'base_version': 1, 'edited_content': translated+' Human review pending.'}, uid())
                    self.assertEqual(edited['assistant_status'], 'paused'); self.assertEqual(edited['facts'], original_facts)
                    reopened = Store(folder)
                    with reopened.as_actor(actors[0]): self.assertEqual(reopened.download(language['id'], 2), translated+' Human review pending.')
                    current = store.mutate('control', matter['id'], {'expected_version': edited['version'], 'command': 'resume'}, uid())
                    # A different selected source version is a fresh explicit request. Cancel during transport.
                    store.mutate('artifact', artifact['id'], {'base_version': 1, 'edited_content': original}, uid()); artifact = store.detail(matter['id'])['artifacts'][0]
                    current = store.detail(matter['id']); store.mutate('translate', matter['id'], data(current), uid())
                    def pause_now():
                        m = store.detail(matter['id']); store.mutate('control', matter['id'], {'expected_version': m['version'], 'command': 'pause'}, uid())
                    hook[0] = pause_now
                self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    after = store.detail(matter['id']); self.assertEqual(after['assistant_status'], 'paused'); self.assertEqual(len([a for a in after['artifacts'] if a['type']=='language_draft']), 1)
                    self.assertTrue(after['artifacts'][1]['language_basis']['original_stale']); self.assertEqual(len(calls), 2)
                    with store.connect() as c:
                        usage = [json.loads(row[0]) for row in c.execute("SELECT message FROM events WHERE matter_id=? AND event_type='model_usage'", (matter['id'],))]
                    self.assertTrue(usage[-1]['discarded'])
                    # Reject a changed numeric response and retain validated usage, no result, no automatic retry.
                    current = store.mutate('control', matter['id'], {'expected_version': after['version'], 'command': 'resume'}, uid())
                    store.mutate('translate', matter['id'], data(current), uid()); hook[0] = None; returned[0] = {**output, 'content': translated+' 9'}
                self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    failed = store.detail(matter['id']); self.assertEqual(failed['actions'][-1]['status'], 'failed'); self.assertIn('数字', failed['actions'][-1]['error'])
                    self.assertFalse(runner.step()); self.assertEqual(len(calls), 3); self.assertEqual(failed['facts'], original_facts)
                    with self.assertRaises(AppError) as conflict: store.mutate('translate', matter['id'], data(current), uid())
                    self.assertEqual(conflict.exception.status, 409)
                with store.as_actor(actors[0]):
                    # Source changes during a call reject publication, without replacing the human source.
                    returned[0] = output
                    m = store.detail(matter['id']); store.mutate('translate', matter['id'], data(m), uid())
                    hook[0] = lambda: store.mutate('artifact', artifact['id'], {'base_version': artifact['version'], 'edited_content': original+' 本人新说明，未执行。'}, uid())
                self.assertTrue(runner.step())
                with store.as_actor(actors[0]):
                    changed = store.detail(matter['id']); self.assertEqual(len([a for a in changed['artifacts'] if a['type']=='language_draft']), 1)
                    self.assertEqual(changed['actions'][-1]['status'], 'cancelled'); self.assertEqual(changed['assistant_status'], 'waiting')
                    artifact = next(a for a in changed['artifacts'] if a['id']==artifact['id'])
                    store.mutate('translate', matter['id'], data(changed), uid())
                    with store.transaction() as c: c.execute("UPDATE actions SET status='running' WHERE matter_id=? AND kind='translate_model' AND status='queued'", (matter['id'],))
                recovered_runner = Runner(store, client); recovered_runner.close()
                with store.as_actor(actors[0]):
                    recovered = store.detail(matter['id']); self.assertEqual(recovered['actions'][-1]['status'], 'failed'); self.assertEqual(len(calls), 4)
                    self.assertEqual(recovered['facts'], original_facts)
                if protected:
                    with store.as_actor(actors[1]), self.assertRaises(AppError) as denied: store.mutate('translate', matter['id'], data(failed), uid())
                    self.assertEqual(denied.exception.status, 404)
