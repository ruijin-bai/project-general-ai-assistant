"""One bounded critical-risk check for selected feedback/model/withdrawal closure."""
import json
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from assistant_app.menu import MENU_SCHEMA, MenuService
from assistant_app.menu_model import MenuModelService, validate_output
from assistant_app.model import ModelClient
from assistant_app.server import Runner
from assistant_app.store import AppError, Store, dump, uid


class MenuModelSmoke(unittest.TestCase):
    def test_guarded_candidate_to_human_response(self):
        with tempfile.TemporaryDirectory(prefix='menu-model-', dir=Path(__file__).parent) as directory:
            store = Store(directory)
            store.enable_identity(MENU_SCHEMA)
            actors = {name: {'id': uid(), 'auth_epoch': 1} for name in ('cook', 'other_cook', 'employee', 'clerk')}
            with store.transaction() as connection:
                for name, actor in actors.items():
                    connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active',?,0,'fixture')", (actor['id'], name, 'PrivateName-'+name, dump(['cook' if 'cook' in name else name])))
            menu = MenuService(store)
            client = ModelClient(enabled=True, api_key='EngineeringStubOnly')
            service = MenuModelService(store, client)
            def invoke(actor, owner, method, *args):
                with store.as_actor(actors[actor]):
                    return getattr(owner, method)(*args)
            def fail(status, actor, owner, method, *args):
                with self.assertRaises(AppError) as caught:
                    invoke(actor, owner, method, *args)
                self.assertEqual(caught.exception.status, status)
            matter = invoke('cook', store, 'mutate', 'create', None, {'domain': 'menu', 'goal_text': 'PrivateGoal', 'auto_prepare': False}, uid())
            invoke('cook', store, 'mutate', 'manual_artifact', matter['id'], {'expected_version': matter['version'], 'input_revision': matter['revision_no'], 'edited_content': 'PrivateManual'}, uid())
            matter = invoke('cook', store, 'detail', matter['id'])
            pub = invoke('cook', menu, 'publish', matter['id'], {'expected_version': matter['version'], 'menu_date': '2026-10-20', 'meal_slot': '工程午餐', 'dishes': '工程豆腐', 'notes': ''}, uid())
            feedback = [invoke('employee', menu, 'feedback', pub['id'], {'version': 1, 'feedback_text': text, 'dish': '工程豆腐', 'dietary_constraint': 'PrivateConstraint'}, uid()) for text in ('工程清淡意见', '工程不同意见')]
            before = invoke('cook', store, 'detail', matter['id'])
            sent, on_send = [], [None]
            raw_result = {'content': '仅所选意见，待核。', 'candidates': {}, 'questions': [], 'feedback_review': {'themes': [{'title': '不同观点', 'text': '清淡与不同意见分别核对。', 'feedback_indices': [1, 2]}], 'replies': [{'feedback_index': index, 'text': '收到这条意见，具体做法另核。'} for index in (1, 2)]}}
            def open_response(request, timeout):
                sent.append(json.loads(request.data))
                if on_send[0]:
                    on_send[0]()
                result = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': dump(raw_result)}}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
                return BytesIO(dump(result).encode())
            client._opener = SimpleNamespace(open=open_response)
            runner = Runner.__new__(Runner)
            runner.store, runner.model, runner.stopped = store, client, threading.Event()
            def read():
                return invoke('cook', service, 'read', pub['id'])
            def data():
                view = read()
                return {key: view[key] for key in ('expected_version', 'basis_token')} | {'publication_version': 1, 'selected_ids': [row['id'] for row in feedback], 'include_constraints': False}
            def start():
                return invoke('cook', service, 'start', pub['id'], data(), uid())
            fail(404, 'other_cook', service, 'read', pub['id'])
            fail(403, 'clerk', service, 'read', pub['id'])
            fail(403, 'employee', service, 'start', pub['id'], data(), uid())
            fail(409, 'cook', service, 'start', pub['id'], data() | {'basis_token': 'old'}, uid())
            key, request_data = uid(), data()
            queued = invoke('cook', service, 'start', pub['id'], request_data, key)
            self.assertEqual(invoke('cook', service, 'start', pub['id'], request_data, key), queued)
            self.assertEqual(read()['batch']['status'], 'queued')
            self.assertTrue(runner.step())
            result = read()
            self.assertEqual(result['batch']['status'], 'completed')
            self.assertEqual(result['batch']['selected_count'], 2)
            self.assertEqual(result['batch']['contributor_count'], 1)
            self.assertEqual(len(result['review']['replies']), 2)
            payload = sent[0]['messages'][1]['content']
            for forbidden in ('PrivateName', 'PrivateGoal', 'PrivateManual', 'PrivateConstraint', actors['employee']['id'], actors['cook']['id'], feedback[0]['id']):
                self.assertNotIn(forbidden, payload)
            parsed_input = json.loads(payload)
            self.assertEqual(len(parsed_input['feedback']), 2)
            after = invoke('cook', store, 'detail', matter['id'])
            for field in ('artifacts', 'facts', 'sources', 'revision_no'):
                self.assertEqual(before[field], after[field])
            self.assertNotIn('review', dump(after['actions']))
            self.assertEqual(invoke('cook', menu, 'list_feedback', pub['id'])['items'][0]['responses'], [])
            self.assertTrue(start()['reused'])
            self.assertFalse(runner.step())
            self.assertEqual(len(sent), 1)
            bad = json.loads(dump(raw_result))
            bad['feedback_review']['themes'][0]['feedback_indices'] = [1]
            with self.assertRaises(ValueError):
                validate_output(bad, parsed_input)
            # Human declaration follows the existing explicit response operation.
            reply = result['review']['replies'][0]
            invoke('cook', menu, 'respond_feedback', pub['id'], reply['ref']['id'], {'expected_publication_version': 1, 'feedback_correction_count': 0, 'response_text': reply['text']}, uid())
            self.assertEqual(len(invoke('employee', menu, 'get_own_feedback', reply['ref']['id'])['responses']), 1)
            def correct(index, text):
                own = invoke('employee', menu, 'get_own_feedback', feedback[index]['id'])
                return invoke('employee', menu, 'update_feedback', own['id'], {'expected_version': own['expected_version'], 'feedback_text': text}, uid())
            correct(0, '工程本人更正')
            stale = read()
            self.assertTrue(stale['batch']['stale'])
            self.assertIsNone(stale['review'])
            self.assertNotIn('工程清淡意见', dump(stale))
            # Correction between queue and dispatch causes no model call.
            start()
            correct(1, '工程第二条更正')
            self.assertTrue(runner.step())
            self.assertEqual(len(sent), 1)
            self.assertEqual(read()['batch']['status'], 'cancelled')
            # Pause cancels queued work, keeps manual response and original artifacts.
            start()
            current = invoke('cook', store, 'detail', matter['id'])
            paused = invoke('cook', store, 'mutate', 'control', matter['id'], {'expected_version': current['version'], 'command': 'pause'}, uid())
            fail(409, 'cook', service, 'start', pub['id'], data(), uid())
            self.assertFalse(runner.step())
            resumed = invoke('cook', store, 'mutate', 'control', matter['id'], {'expected_version': paused['version'], 'command': 'resume'}, uid())
            # Withdrawal during the actual request discards result and records usage.
            on_send[0] = lambda: invoke('employee', menu, 'withdraw_feedback', feedback[1]['id'], {'expected_version': invoke('employee', menu, 'get_own_feedback', feedback[1]['id'])['expected_version']}, uid())
            queued = start()
            self.assertTrue(runner.step())
            self.assertEqual(len(sent), 2)
            self.assertIsNone(read()['review'])
            with store.connect() as connection:
                usage = [json.loads(row[0]) for row in connection.execute("SELECT message FROM events WHERE matter_id=? AND event_type='model_usage'", (matter['id'],))]
            self.assertTrue(usage[-1]['discarded'])
            self.assertFalse(invoke('employee', menu, 'get_own_feedback', feedback[1]['id'])['shared'])
            # Reopening keeps the actual chef declaration and private versions.
            reopened = Store(directory)
            with reopened.as_actor(actors['cook']):
                self.assertIsNone(MenuModelService(reopened, client).read(pub['id'])['review'])
                self.assertEqual(reopened.detail(matter['id'])['artifacts'], before['artifacts'])
            current_data = data() | {'selected_ids': [feedback[0]['id']], 'include_constraints': True}
            fail(422, 'cook', MenuModelService(store, ModelClient()), 'start', pub['id'], current_data, uid())
            # Explicit constraint opt-in is bounded to selected input; revoked role during
            # provider response discards candidates and still records actual usage.
            def revoke():
                with store.transaction() as connection:
                    connection.execute("UPDATE accounts SET capabilities='[]' WHERE id=?", (actors['cook']['id'],))
            on_send[0] = revoke
            raw_result['feedback_review']['themes'][0]['feedback_indices'] = [1]
            raw_result['feedback_review']['replies'] = raw_result['feedback_review']['replies'][:1]
            queued = invoke('cook', service, 'start', pub['id'], current_data, uid())
            self.assertTrue(runner.step())
            self.assertEqual(len(sent), 3)
            self.assertEqual(json.loads(sent[-1]['messages'][1]['content'])['feedback'][0]['dietary_constraint'], 'PrivateConstraint')
            fail(403, 'cook', service, 'read', pub['id'])
            with store.transaction() as connection:
                usage = json.loads(connection.execute("SELECT message FROM events WHERE matter_id=? AND event_type='model_usage' ORDER BY rowid DESC LIMIT 1", (matter['id'],)).fetchone()[0])
                self.assertTrue(usage['discarded'])
                connection.execute('UPDATE accounts SET capabilities=? WHERE id=?', (dump(['cook']), actors['cook']['id']))
            # Interrupted external request is failed, never auto replayed at startup.
            with store.transaction() as connection:
                connection.execute("UPDATE actions SET status='running' WHERE id=?", (queued['action_id'],))
            with patch('threading.Thread.start'):
                recovered = Runner(store, client)
            recovered.stopped.set()
            with store.connect() as connection:
                self.assertEqual(connection.execute('SELECT status FROM actions WHERE id=?', (queued['action_id'],)).fetchone()[0], 'failed')
            self.assertEqual(len(sent), 3)
            with store.transaction() as connection:
                connection.execute("UPDATE accounts SET capabilities='[]' WHERE id=?", (actors['cook']['id'],))
            fail(403, 'cook', service, 'read', pub['id'])


if __name__ == '__main__':
    unittest.main()
