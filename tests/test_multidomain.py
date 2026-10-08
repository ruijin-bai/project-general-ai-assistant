"""Independent multi-domain/migration/model integration checks; isolated fixtures only."""
import http.client
from contextlib import closing, contextmanager
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from assistant_app import model
from assistant_app.preparation import DOMAINS, initial_facts, prepare as real_prepare
from assistant_app.server import create_server
from assistant_app.store import SCHEMA, Store, dump

ROOT = Path(__file__).resolve().parents[1]
TABLES = ('matters', 'revisions', 'sources', 'artifacts', 'actions', 'events')


@contextmanager
def database(path):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            yield connection


class ModelFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(payload)
        self.server.entered.set()
        self.server.release.wait(4)
        body = self.server.body
        self.send_response(self.server.code)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class MultiDomainHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qa-multidomain-', dir=ROOT / 'tests')
        self.addCleanup(self.temp.cleanup)
        self.patch = patch.object(model, '_PROCESS_BUDGET', model._CallBudget())
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.fixture = ThreadingHTTPServer(('127.0.0.1', 0), ModelFixture)
        self.fixture.requests = []
        self.fixture.entered = threading.Event()
        self.fixture.release = threading.Event()
        self.fixture.release.set()
        self.fixture.code = 200
        self.reply()
        self.fixture_thread = threading.Thread(target=self.fixture.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.fixture_thread.start()
        self.addCleanup(self.close_fixture)
        self.server = None
        self.addCleanup(self.stop)
        self.start()

    def reply(self, candidates=None):
        result = {'content': '工程模拟草稿，真实处理等待原责任人。', 'candidates': candidates or {}, 'questions': ['请核必要来源']}
        envelope = {'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': dump(result)}}],
                    'usage': {'prompt_tokens': 12, 'completion_tokens': 20, 'total_tokens': 32}}
        self.fixture.body = dump(envelope).encode()

    def close_fixture(self):
        self.fixture.release.set()
        self.fixture.shutdown()
        self.fixture.server_close()
        self.fixture_thread.join(2)

    def start(self):
        client = model.ModelClient(enabled=True, base_url=f'http://127.0.0.1:{self.fixture.server_port}/api/compatible/v1',
                                   api_key='fixture-key-only', timeout=4, allow_test_loopback=True)
        self.server = create_server(port=0, data_dir=Path(self.temp.name), web_dir=ROOT / 'web', model_client=client)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        code, session = self.request('GET', '/api/session')
        self.assertEqual(code, 200)
        self.token = session['csrf_token']
        self.assertTrue(session['model_configured'])

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
            self.server = None

    def request(self, method, path, data=None, key=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json', 'X-CSRF-Token': getattr(self, 'token', ''), 'Idempotency-Key': key or str(uuid.uuid4())}
        try:
            connection.request(method, path, dump(data).encode() if data is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def create(self, domain):
        code, detail = self.request('POST', '/api/matters', {'goal_text': '隔离工程夹具：' + domain, 'domain': domain})
        self.assertEqual(code, 201, detail)
        return detail

    def get(self, matter):
        code, detail = self.request('GET', '/api/matters/' + matter['id'])
        self.assertEqual(code, 200, detail)
        return detail

    def post(self, matter, suffix, **data):
        return self.request('POST', '/api/matters/' + matter['id'] + '/' + suffix, {'expected_version': matter['version'], **data})

    def terminal(self, matter):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            detail = self.get(matter)
            if detail['actions'] and detail['actions'][-1]['status'] in {'completed', 'failed', 'cancelled'}:
                return detail
            time.sleep(.015)
        self.fail('后台动作4秒内未结束')

    def saved_template(self, domain='repair'):
        detail = self.create(domain)
        self.assertEqual(self.post(detail, 'actions', kind='prepare')[0], 202)
        detail = self.terminal(detail)
        artifact = detail['artifacts'][0]
        code, detail = self.request('PATCH', '/api/artifacts/' + artifact['id'],
                                    {'base_version': artifact['version'], 'edited_content': '人工原稿必须保留'})
        self.assertEqual(code, 200, detail)
        return detail, artifact['id']

    def assert_unknown_tracks(self, detail):
        self.assertEqual(set(detail['tracks']), set(DOMAINS[detail['domain']]['tracks']))
        self.assertTrue(all(item['status'] == 'unknown' for item in detail['tracks'].values()))

    def test_domain_isolation_prepare_edit_restart_control(self):
        # Expected domain names are independent of the runtime registry.
        expected = {'general', 'travel', 'repair', 'cleaning', 'menu', 'feedback', 'leave', 'document', 'meeting', 'expense', 'inventory', 'hr'}
        self.assertEqual(set(self.request('GET', '/api/catalog')[1]['domains']), expected)
        saved = []
        for domain in sorted(expected):
            with self.subTest(domain=domain):
                detail = self.create(domain)
                self.assertEqual(set(detail['facts']), set(DOMAINS[domain]['fields']))
                foreign = next(field for field in ('destination', 'problem', 'visa_info', 'hr_goal') if field not in detail['facts'])
                self.assertEqual(self.post(detail, 'facts/confirm', field_changes={foreign: '跨域应拒绝'})[0], 422)
                values = {field: ('2026-10-10T08:00:00+01:00' if field == 'departure_at' else
                                  '2026-10-10T18:00:00+01:00' if field == 'return_at' else
                                  '2026-10-10' if field in {'menu_date', 'meeting_date', 'leave_start', 'leave_end'} else '隔离字段原述')
                          for field in DOMAINS[domain]['required']}
                if values:
                    code, detail = self.post(detail, 'facts/confirm', field_changes=values)
                    self.assertEqual(code, 200, detail)
                self.assertEqual(self.post(detail, 'actions', kind='prepare')[0], 202)
                detail = self.terminal(detail)
                self.assertEqual(detail['preparation_status'], 'ready')
                self.assert_unknown_tracks(detail)
                artifact = detail['artifacts'][0]
                text = '人工保留 ' + domain
                code, detail = self.request('PATCH', '/api/artifacts/' + artifact['id'], {'base_version': 1, 'edited_content': text})
                self.assertEqual(code, 200, detail)
                code, detail = self.post(detail, 'control', command='pause')
                self.assertEqual(code, 200, detail)
                self.assertEqual(self.post(detail, 'actions', kind='prepare')[0], 409)
                code, detail = self.post(detail, 'control', command='resume')
                self.assertEqual(code, 200, detail)
                self.assert_unknown_tracks(detail)
                saved.append((detail, artifact['id'], text))
        self.stop()
        self.start()
        for matter, artifact_id, text in saved:
            detail = self.get(matter)
            self.assertEqual(next(a for a in detail['artifacts'] if a['id'] == artifact_id)['content'], text)
            self.assert_unknown_tracks(detail)

    def test_dates_reject_relative_invalid_and_reversal_atomically(self):
        for domain, changes in [('menu', {'menu_date': '明天'}), ('meeting', {'meeting_date': '2026-02-30'}),
                                ('leave', {'leave_start': '2026-10-12', 'leave_end': '2026-10-11'}),
                                ('leave', {'departure_at': '2026-10-12T18:00:00+01:00', 'return_at': '2026-10-11T18:00:00+01:00'})]:
            with self.subTest(domain=domain, changes=changes):
                detail = self.create(domain)
                self.assertEqual(self.post(detail, 'facts/confirm', field_changes=changes)[0], 422)
                self.assertEqual(self.get(detail)['revision_no'], 1)
                self.assert_unknown_tracks(self.get(detail))

    def test_model_candidates_usage_and_human_history_survive_restart(self):
        detail, artifact_id = self.saved_template()
        code, detail = self.post(detail, 'facts/confirm', field_changes={'location': '人工确认位置'})
        self.assertEqual(code, 200, detail)
        self.reply({'location': '模型试图覆盖', 'problem': '来源故障候选'})
        self.assertEqual(self.post(detail, 'actions', kind='prepare', mode='model')[0], 202)
        detail = self.terminal(detail)
        self.assertEqual(detail['actions'][-1]['status'], 'completed')
        self.assertEqual(detail['facts']['location'], {'value': '人工确认位置', 'status': 'confirmed'})
        self.assertEqual(detail['facts']['problem']['status'], 'candidate')
        self.assertEqual(detail['facts']['problem']['value'], '来源故障候选')
        self.assertEqual(detail['artifacts'][0]['status'], 'model_draft')
        with self.server.store.connect() as connection:
            usage = [json.loads(row[0]) for row in connection.execute("SELECT message FROM events WHERE matter_id=? AND event_type='model_usage'", (detail['id'],))]
        self.assertEqual(usage[0]['usage'], {'prompt_tokens': 12, 'completion_tokens': 20, 'total_tokens': 32})
        self.assertFalse(usage[0]['replayed'])
        self.assert_unknown_tracks(detail)
        self.stop()
        self.start()
        detail = self.get(detail)
        self.assertEqual(next(a for a in detail['artifacts'] if a['id'] == artifact_id)['content'], '人工原稿必须保留')
        with self.server.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM artifacts WHERE id=?', (artifact_id,)).fetchone()[0], 2)
        self.assertEqual(len(self.fixture.requests), 1)

    def test_model_failure_is_redacted_and_preserves_manual_artifact(self):
        detail, artifact_id = self.saved_template()
        self.fixture.code = 500
        self.fixture.body = b'provider-private-details fixture-key-only'
        self.assertEqual(self.post(detail, 'actions', kind='prepare', mode='model')[0], 202)
        detail = self.terminal(detail)
        self.assertEqual(detail['actions'][-1]['status'], 'failed')
        self.assertNotIn('provider-private-details', dump(detail))
        self.assertNotIn('fixture-key-only', dump(detail))
        self.assertEqual(next(a for a in detail['artifacts'] if a['id'] == artifact_id)['content'], '人工原稿必须保留')
        self.assertEqual(len(self.fixture.requests), 1)

    def test_inflight_model_pause_and_fact_revision_fence(self):
        for operation in ('pause', 'facts'):
            with self.subTest(operation=operation):
                detail, artifact_id = self.saved_template()
                original_artifacts = detail['artifacts']
                self.fixture.entered.clear()
                self.fixture.release.clear()
                self.reply({'problem': '过期模型候选'})
                self.assertEqual(self.post(detail, 'actions', kind='prepare', mode='model')[0], 202)
                self.assertTrue(self.fixture.entered.wait(3))
                try:
                    detail = self.get(detail)
                    if operation == 'pause':
                        code, detail = self.post(detail, 'control', command='pause')
                    else:
                        code, detail = self.post(detail, 'facts/confirm', field_changes={'problem': '新的人工事实'})
                    self.assertEqual(code, 200, detail)
                finally:
                    self.fixture.release.set()
                # A second template completion proves the single runner processed the old response.
                other = self.create('general')
                self.assertEqual(self.post(other, 'actions', kind='prepare')[0], 202)
                self.assertEqual(self.terminal(other)['actions'][-1]['status'], 'completed')
                detail = self.get(detail)
                self.assertEqual(detail['actions'][-1]['status'], 'cancelled')
                self.assertEqual([(a['id'], a['version'], a['content']) for a in detail['artifacts']],
                                 [(a['id'], a['version'], a['content']) for a in original_artifacts])
                self.assertNotEqual(detail['facts']['problem'].get('value'), '过期模型候选')
                self.assertFalse(any(e['event_type'] == 'model_usage' for e in detail['activities']))
                self.assertEqual(detail['assistant_status'], 'paused' if operation == 'pause' else 'waiting')
                self.assert_unknown_tracks(detail)

    def test_restart_does_not_replay_running_model(self):
        detail, artifact_id = self.saved_template()
        self.stop()
        with database(Path(self.temp.name) / 'assistant.sqlite3') as connection:
            connection.execute("UPDATE matters SET assistant_status='processing' WHERE id=?", (detail['id'],))
            connection.execute("INSERT INTO actions(id,matter_id,kind,status,input_revision,control_epoch,attempts,created_at) VALUES(?,?,'prepare_model','running',?,?,1,'2026-10-08T00:00:00Z')",
                               ('interrupted-model', detail['id'], detail['revision_no'], detail['control_epoch']))
        self.start()
        detail = self.get(detail)
        self.assertEqual(next(a for a in detail['actions'] if a['id'] == 'interrupted-model')['status'], 'failed')
        self.assertEqual(detail['assistant_status'], 'waiting')
        self.assertEqual(self.fixture.requests, [])
        self.assertEqual(next(a for a in detail['artifacts'] if a['id'] == artifact_id)['content'], '人工原稿必须保留')
        self.assertTrue(any(e['event_type'] == 'model_interrupted' for e in detail['activities']))

    def test_legacy_general_facts_survive_new_fact_source_and_model_revisions(self):
        detail = self.create('general')
        legacy = initial_facts('travel')
        legacy['destination'] = {'value': '历史人工位置', 'status': 'confirmed'}
        with self.server.store.transaction() as connection:
            connection.execute('UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=1', (dump(legacy), detail['id']))
        detail = self.get(detail)
        self.assertEqual(detail['legacy_facts'], legacy)
        code, detail = self.post(detail, 'facts/confirm', field_changes={'subject': '新业务主题'})
        self.assertEqual(code, 200, detail)
        self.assertEqual(detail['legacy_facts'], legacy)
        code, detail = self.post(detail, 'sources', kind='user_text', text='新来源原话')
        self.assertEqual(code, 200, detail)
        self.assertEqual(detail['legacy_facts'], legacy)
        self.reply({'facts_text': '本业务事实候选'})
        self.assertEqual(self.post(detail, 'actions', kind='prepare', mode='model')[0], 202)
        detail = self.terminal(detail)
        self.assertEqual(detail['actions'][-1]['status'], 'completed')
        self.assertEqual(detail['legacy_facts'], legacy)
        self.assertEqual(detail['facts']['facts_text']['status'], 'candidate')
        # Legacy values must stay local and cannot leak into a different domain's model input.
        self.assertNotIn('历史人工位置', dump(self.fixture.requests[0]))
        with self.server.store.connect() as connection:
            raw = json.loads(connection.execute('SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?',
                                                (detail['id'], detail['revision_no'])).fetchone()[0])
        self.assertEqual({key: raw[key] for key in legacy}, legacy)

    def test_auto_workflow_create_idempotency_and_running_pause_fence(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(detail):
            entered.set()
            if not release.wait(4):
                raise TimeoutError('隔离自动整理栅栏未释放')
            return real_prepare(detail)
        data = {'goal_text': '自动委托隔离夹具', 'domain': 'repair', 'auto_prepare': True}
        key = str(uuid.uuid4())
        with patch('assistant_app.server.prepare', blocked):
            try:
                code, original = self.request('POST', '/api/matters', data, key)
                self.assertEqual(code, 201, original)
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.request('POST', '/api/matters', data, key), (201, original))
                current = self.get(original)
                self.assertEqual(len(current['actions']), 1)
                code, current = self.post(current, 'control', command='pause')
                self.assertEqual(code, 200, current)
                code, current = self.post(current, 'sources', kind='user_text', text='暂停期间原话')
                self.assertEqual(code, 200, current)
                self.assertEqual(len(current['actions']), 1)
                self.assertEqual(current['assistant_status'], 'paused')
            finally:
                release.set()
            # Same runner can complete another action only after consuming the paused result.
            other = self.create('general')
            self.assertEqual(self.post(other, 'actions', kind='prepare')[0], 202)
            self.assertEqual(self.terminal(other)['actions'][-1]['status'], 'completed')
        current = self.get(original)
        self.assertEqual(current['artifacts'], [])
        self.assertEqual(current['actions'][0]['status'], 'cancelled')
        self.assertEqual(current['assistant_status'], 'paused')
        self.assertEqual(self.fixture.requests, [])
        self.assert_unknown_tracks(current)

    def test_stale_calculation_in_new_template_remains_explicitly_historical(self):
        detail = self.create('expense')
        rows = [{'id': 'one', 'label': '隔离费用', 'value': '0.1', 'currency': 'NGN', 'period': '2026-10',
                 'source_id': detail['sources'][0]['id'], 'source_position': '原表第1行'},
                {'id': 'two', 'label': '隔离费用', 'value': '0.2', 'currency': 'NGN', 'period': '2026-10',
                 'source_id': detail['sources'][0]['id'], 'source_position': '原表第2行'}]
        code, detail = self.post(detail, 'rows/confirm', rows=rows)
        self.assertEqual(code, 200, detail)
        code, detail = self.post(detail, 'facts/confirm', field_changes={'purpose': '事实已变化', 'expense_lines': '原表明细'})
        self.assertEqual(code, 200, detail)
        self.assertTrue(detail['calculation']['stale'])
        old_revision = detail['calculation']['input_revision']
        self.assertEqual(self.post(detail, 'actions', kind='prepare')[0], 202)
        detail = self.terminal(detail)
        self.assertTrue(detail['calculation']['stale'])
        self.assertEqual(detail['calculation']['input_revision'], old_revision)
        content = detail['artifacts'][0]['content']
        self.assertIn('以下为原核对范围的计算，须重核后用于下一步', content)
        self.assertIn('0.3', content)
        self.assert_unknown_tracks(detail)


class SchemaMigrationTests(unittest.TestCase):
    def fixture(self, folder, orphan=False):
        path = Path(folder) / 'assistant.sqlite3'
        # Reproduce v1 six-table DDL, including its travel/general domain CHECK.
        schema = SCHEMA.replace('domain TEXT NOT NULL,', "domain TEXT NOT NULL CHECK(domain IN ('travel','general')),").replace('user_version=2', 'user_version=1')
        with database(path) as connection:
            connection.executescript(schema)
            for domain in ('travel', 'general'):
                ident = 'legacy-' + domain
                connection.execute('INSERT INTO matters VALUES(?,?,?,?,?,?,?,?,?,?)', (ident, 'local-preview', '旧人工目标', domain, 9, 2, 4, 'paused', 'needs_review', 'legacy-time'))
                for revision in (1, 2):
                    facts = initial_facts('travel')
                    facts['purpose'] = {'value': '历史确认' + str(revision), 'status': 'confirmed'}
                    connection.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (ident, revision, dump(facts), 'legacy-time', 'legacy-human'))
                connection.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?)', ('source-' + domain, ident, 'oral_note', '历史原话', '实际陈述人', 'legacy-human', 'legacy-time'))
                for version in (1, 2):
                    connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)', ('artifact-' + domain, version, ident, 'preparation', '旧稿', '人工历史' + str(version), 'human_saved', 1, 'legacy-time'))
                connection.execute('INSERT INTO actions(id,matter_id,kind,status,idempotency_key,fingerprint,response,input_revision,control_epoch,attempts,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                   ('action-' + domain, ident, 'prepare', 'completed', 'key-' + domain, 'legacy-fingerprint', '{"preserved":true}', 1, 4, 1, 'legacy-time'))
                connection.execute('INSERT INTO events VALUES(?,?,?,?,?,?)', ('event-' + domain, ident, 'artifact_saved', '旧操作原文', 'legacy-human', 'legacy-time'))
            if orphan:
                connection.execute('INSERT INTO events VALUES(?,?,?,?,?,?)', ('orphan', 'missing-matter', 'legacy', '旧异常引用', 'legacy-human', 'legacy-time'))
        return path

    def snapshot(self, path):
        with database(path) as connection:
            return {table: connection.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall() for table in TABLES}

    def test_v1_migration_preserves_all_six_tables_and_backup(self):
        with tempfile.TemporaryDirectory(prefix='qa-v1-', dir=ROOT / 'tests') as folder:
            path = self.fixture(folder)
            before = self.snapshot(path)
            store = Store(folder)
            self.assertEqual(self.snapshot(path), before)
            backups = list((Path(folder) / 'backups').glob('schema-v1-*.sqlite3'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(self.snapshot(backups[0]), before)
            with store.connect() as connection:
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
                self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
                self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
                self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute('INSERT INTO events VALUES(?,?,?,?,?,?)', ('new-orphan', 'missing', 'x', 'x', 'x', 'x'))
            self.assertEqual(store.detail('legacy-travel')['artifacts'][0]['content'], '人工历史2')
            self.assertEqual(store.detail('legacy-general')['goal_text'], '旧人工目标')
            Store(folder)
            self.assertEqual(len(list((Path(folder) / 'backups').glob('*.sqlite3'))), 1)

    def test_invalid_v1_references_roll_back_and_preserve_backup(self):
        with tempfile.TemporaryDirectory(prefix='qa-v1-fail-', dir=ROOT / 'tests') as folder:
            path = self.fixture(folder, orphan=True)
            before = self.snapshot(path)
            with self.assertRaisesRegex(RuntimeError, '迁移引用检查失败'):
                Store(folder)
            self.assertEqual(self.snapshot(path), before)
            with database(path) as connection:
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)
                self.assertIn("CHECK(domain IN ('travel','general'))", connection.execute("SELECT sql FROM sqlite_master WHERE name='matters'").fetchone()[0])
            backups = list((Path(folder) / 'backups').glob('*.sqlite3'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(self.snapshot(backups[0]), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
