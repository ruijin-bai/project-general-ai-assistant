"""Independent loopback identity/menu acceptance with isolated synthetic accounts."""
import http.client
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from contextlib import closing

from assistant_app.auth import AuthService
from assistant_app.menu import MENU_SCHEMA
from assistant_app.server import create_server
from assistant_app.store import Store, dump, now, uid
from run import instance_lock

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'independent-engineering-fixture-01'


class Client:
    def __init__(self, server):
        self.server, self.cookie, self.csrf = server, '', ''

    def request(self, method, path, data=None, key=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        request_headers = {'Cookie': self.cookie}
        if data is not None:
            request_headers.update({'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf,
                                    'Idempotency-Key': key or uid()})
        request_headers.update(headers or {})
        try:
            connection.request(method, path, json.dumps(data, ensure_ascii=False).encode() if data is not None else None, request_headers)
            response = connection.getresponse()
            raw, cookie = response.read(), response.getheader('Set-Cookie')
            if cookie:
                self.cookie = cookie.split(';')[0]
            body = json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json') else raw.decode()
            if isinstance(body, dict) and 'csrf_token' in body:
                self.csrf = body['csrf_token']
            return response.status, body, dict(response.getheaders())
        finally:
            connection.close()


class BlockingModel:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()

    def status(self):
        return {'configured': True, 'reason': 'isolated_fixture'}

    def prepare(self, detail):
        self.entered.set()
        if not self.release.wait(4):
            raise RuntimeError('fixture timeout')
        return {'content': 'REVOKED_MODEL_RESULT', 'candidates': {}, 'questions': [], 'model': 'fixture', 'usage': {}}


class IdentityHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qa-identity-', dir=ROOT / 'tests')
        self.addCleanup(self.temp.cleanup)
        self.server = create_server(port=0, data_dir=Path(self.temp.name), web_dir=ROOT / 'web', multi_user=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        AuthService(self.server.store).bootstrap('qa_admin', PASSWORD)
        self.admin = self.login('qa_admin')

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
            self.server = None

    def check(self, response, status):
        self.assertEqual(response[0], status, response[1])
        return response[1]

    def login(self, username):
        client = Client(self.server)
        self.check(client.request('GET', '/api/session'), 200)
        self.check(client.request('POST', '/api/auth/login', {'username': username, 'password': PASSWORD}), 200)
        return client

    def account(self, username, caps):
        created = self.check(self.admin.request('POST', '/api/admin/accounts', {'username': username, 'display_name': '隔离工程身份 ' + username, 'capabilities': caps}), 201)
        anon = Client(self.server)
        self.check(anon.request('GET', '/api/session'), 200)
        self.check(anon.request('POST', '/api/auth/activate', {'token': created['activation_token'], 'password': PASSWORD}), 200)
        client = self.login(username)
        principal = self.check(client.request('GET', '/api/session'), 200)['principal']
        return client, principal

    def create(self, client, domain='general', key=None):
        return self.check(client.request('POST', '/api/matters', {'domain': domain, 'goal_text': 'PRIVATE_RAW_MARKER 工程私有目标', 'auto_prepare': False}, key), 201)

    def ready(self, client, matter):
        self.check(client.request('POST', '/api/matters/' + matter['id'] + '/actions', {'expected_version': matter['version'], 'kind': 'prepare'}), 202)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            detail = self.check(client.request('GET', '/api/matters/' + matter['id']), 200)
            if detail['artifacts']:
                return detail
            time.sleep(.02)
        self.fail('template did not complete')

    def publication(self):
        cook, account = self.account('qa_cook', ['cook'])
        matter = self.create(cook, 'menu')
        data = {'expected_version': matter['version'], 'menu_date': '2026-10-09', 'meal_slot': '午餐', 'dishes': '工程菜单 米饭蔬菜', 'notes': ''}
        publication = self.check(cook.request('POST', '/api/matters/' + matter['id'] + '/menu/publish', data), 201)
        return cook, account, matter, publication

    def test_01_anonymous_cookie_csrf_rotation_and_no_private_status(self):
        anon = Client(self.server)
        session_response = anon.request('GET', '/api/session')
        session = self.check(session_response, 200)
        self.assertFalse(session['authenticated'])
        self.assertNotIn('model_status', session)
        self.assertNotIn('principal', session)
        self.assertNotIn('sqlite_version', session)
        cookie = session_response[2]['Set-Cookie']
        for attribute in ('HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(attribute, cookie)
        self.assertNotIn('Domain=', cookie)
        self.check(anon.request('GET', '/api/worklist'), 401)
        employee, account = self.account('qa_employee', ['employee'])
        self.assertNotEqual(employee.csrf, self.admin.csrf)
        self.check(employee.request('POST', '/api/matters', {'goal_text': '工程夹具'}, headers={'X-CSRF-Token': self.admin.csrf}), 403)
        self.check(employee.request('POST', '/api/matters', {'goal_text': '工程夹具'}, headers={'Origin': 'http://example.invalid'}), 403)
        previous = employee.cookie
        self.check(employee.request('POST', '/api/auth/logout', {}), 200)
        self.assertNotEqual(previous, employee.cookie)
        old = Client(self.server); old.cookie = previous
        self.check(old.request('GET', '/api/worklist'), 401)
        self.check(employee.request('GET', '/api/worklist'), 401)

    def test_02_private_ids_download_replay_and_admin_default_deny(self):
        owner, _ = self.account('qa_owner', ['employee'])
        other, _ = self.account('qa_other', ['employee'])
        key = uid()
        matter = self.create(owner, key=key)
        detail = self.ready(owner, matter)
        artifact = detail['artifacts'][0]
        for client in (other, self.admin):
            self.assertEqual(self.check(client.request('GET', '/api/worklist'), 200)['items'], [])
            self.check(client.request('GET', '/api/matters/' + matter['id']), 404)
            self.check(client.request('GET', '/api/artifacts/' + artifact['id'] + '/download'), 404)
            status = 403 if client is self.admin else 404
            self.check(client.request('POST', '/api/matters', {'domain': 'general', 'goal_text': 'PRIVATE_RAW_MARKER 工程私有目标', 'auto_prepare': False}, key), status)
        self.check(self.admin.request('POST', '/api/matters', {'goal_text': '不能管理身份代替业务', 'domain': 'menu'}), 403)
        self.assertIn('PRIVATE_RAW_MARKER', self.check(owner.request('GET', '/api/artifacts/' + artifact['id'] + '/download'), 200))
        self.check(other.request('POST', '/api/admin/accounts', {'username': 'self_admin', 'display_name': '伪造', 'capabilities': ['cook']}), 403)
        with self.server.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT recorded_by FROM sources WHERE matter_id=?', (matter['id'],)).fetchone()[0], self.check(owner.request('GET', '/api/session'), 200)['principal']['id'])

    def test_03_menu_safe_snapshot_and_voluntary_feedback_projection(self):
        cook, account, matter, publication = self.publication()
        employee, _ = self.account('qa_employee', ['employee'])
        other_cook, _ = self.account('qa_other_cook', ['cook'])
        listed = self.check(employee.request('GET', '/api/menu-publications'), 200)['items']
        self.assertEqual(len(listed), 1)
        serialized = json.dumps(listed, ensure_ascii=False)
        for private in ('PRIVATE_RAW_MARKER', 'source_matter_id', 'sources', 'facts', 'artifacts'):
            self.assertNotIn(private, serialized)
        self.check(employee.request('GET', '/api/matters/' + matter['id']), 404)
        self.check(other_cook.request('POST', '/api/matters/' + matter['id'] + '/menu/publish', {'expected_version': 2, 'menu_date': '2026-10-09', 'meal_slot': '午餐', 'dishes': '伪造', 'notes': ''}), 404)
        self.check(self.admin.request('GET', '/api/menu-publications'), 403)
        key, data = uid(), {'version': 1, 'feedback_text': '工程少数意见 保留明确过敏', 'dish': '蔬菜', 'dietary_constraint': '工程约束 花生'}
        path = '/api/menu-publications/' + publication['id'] + '/feedback'
        feedback = self.check(employee.request('POST', path, data, key), 201)
        self.assertEqual(feedback, self.check(employee.request('POST', path, data, key), 201))
        own = self.check(employee.request('GET', '/api/matters/' + feedback['id']), 200)
        self.assertEqual(own['actions'], [])
        projection = self.check(cook.request('GET', path), 200)
        self.assertEqual((projection['feedback_count'], projection['contributor_count']), (1, 1))
        self.assertEqual(projection['items'][0]['dietary_constraint'], data['dietary_constraint'])
        self.check(cook.request('GET', '/api/matters/' + feedback['id']), 404)
        self.check(other_cook.request('GET', path), 404)
        self.check(self.admin.request('GET', path), 403)
        self.check(employee.request('POST', path, {**data, 'reported_by': '假厨师'}), 422)

    def test_04_withdraw_and_disabled_publisher_deny_old_replay(self):
        cook, account, matter, publication = self.publication()
        employee, _ = self.account('qa_employee', ['employee'])
        key, data = uid(), {'version': 1, 'feedback_text': '工程意见'}
        path = '/api/menu-publications/' + publication['id'] + '/feedback'
        feedback = self.check(employee.request('POST', path, data, key), 201)
        self.check(cook.request('POST', '/api/menu-publications/' + publication['id'] + '/withdraw', {'expected_version': 1}), 200)
        self.assertEqual(self.check(employee.request('GET', '/api/menu-publications'), 200)['items'], [])
        self.check(employee.request('GET', '/api/menu-publications/' + publication['id']), 404)
        self.check(employee.request('POST', path, data, key), 404)
        self.check(employee.request('GET', '/api/matters/' + feedback['id']), 200)
        current = self.check(cook.request('GET', '/api/matters/' + matter['id']), 200)
        new = self.check(cook.request('POST', '/api/matters/' + matter['id'] + '/menu/publish', {'expected_version': current['version'], 'menu_date': '2026-10-10', 'meal_slot': '晚餐', 'dishes': '工程新版', 'notes': ''}), 201)
        self.check(self.admin.request('POST', '/api/admin/accounts/' + account['id'] + '/control', {'expected_epoch': account['auth_epoch'], 'command': 'disabled'}), 200)
        self.check(cook.request('GET', '/api/worklist'), 401)
        self.assertEqual(self.check(employee.request('GET', '/api/menu-publications'), 200)['items'], [])
        self.check(employee.request('POST', '/api/menu-publications/' + new['id'] + '/feedback', data), 404)
        with self.server.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT status FROM menu_publications WHERE id=?', (new['id'],)).fetchone()[0], 'published')

    def test_05_auth_epoch_revoked_during_model_call_fences_write(self):
        employee, account = self.account('qa_employee', ['employee'])
        model = BlockingModel(); self.addCleanup(model.release.set)
        self.server.model = self.server.runner.model = model
        matter = self.create(employee)
        self.check(employee.request('POST', '/api/matters/' + matter['id'] + '/actions', {'expected_version': matter['version'], 'kind': 'prepare', 'mode': 'model'}), 202)
        self.assertTrue(model.entered.wait(2))
        with self.server.store.transaction() as connection:
            connection.execute('INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)', (uid(), matter['id'], 'preparation', '撤权前人工稿', 'MANUAL_BEFORE_REVOCATION', 'human_saved', 1, now()))
        self.check(self.admin.request('POST', '/api/admin/accounts/' + account['id'] + '/control', {'expected_epoch': account['auth_epoch'], 'command': 'capabilities', 'capabilities': ['employee', 'cook']}), 200)
        model.release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.server.store.connect() as connection:
                action = connection.execute("SELECT status,error FROM actions WHERE kind='prepare_model'").fetchone()
                if action['status'] == 'cancelled':
                    break
            time.sleep(.02)
        self.assertEqual(action['status'], 'cancelled')
        with self.server.store.connect() as connection:
            self.assertEqual([row[0] for row in connection.execute('SELECT content FROM artifacts WHERE matter_id=?', (matter['id'],))], ['MANUAL_BEFORE_REVOCATION'])
            self.assertEqual(connection.execute('SELECT revision_no FROM matters WHERE id=?', (matter['id'],)).fetchone()[0], 1)
        self.check(employee.request('GET', '/api/matters/' + matter['id']), 401)
        relogged = self.login('qa_employee')
        self.check(relogged.request('GET', '/api/matters/' + matter['id']), 200)


class IdentityMigration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qa-identity-migration-', dir=ROOT / 'tests')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = Store(self.folder)
        self.matter = self.store.mutate('create', None, {'goal_text': '原六表工程记录', 'domain': 'general'}, uid())
        self.artifact_id = uid()
        with self.store.transaction() as connection:
            connection.execute('INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)', (self.artifact_id, self.matter['id'], 'preparation', '人工稿', 'MANUAL_PRESERVE_MARKER', 'human_saved', 1, now()))
            connection.execute("INSERT INTO actions(id,matter_id,kind,status,input_revision,control_epoch,created_at) VALUES(?,?,'prepare','queued',1,0,?)", (uid(), self.matter['id'], now()))
        with self.store.connect() as connection:
            self.before = {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table)] for table in ('matters', 'revisions', 'sources', 'artifacts', 'actions', 'events')}

    def test_06_migration_backup_preserves_six_tables_and_explicit_legacy_claim(self):
        self.store.enable_identity(MENU_SCHEMA)
        backup_path = next((self.folder / 'backups').glob('schema-v2-*.sqlite3'))
        with closing(sqlite3.connect(backup_path)) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 2)
            for table, before in self.before.items():
                self.assertEqual(backup.execute('SELECT * FROM ' + table).fetchall(), before)
        with self.store.connect() as connection:
            self.assertIsNone(connection.execute('PRAGMA foreign_key_check').fetchone())
            for table in ('matters', 'revisions', 'sources', 'artifacts', 'events'):
                self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM ' + table)], self.before[table])
            self.assertEqual(connection.execute("SELECT status FROM actions WHERE kind='prepare'").fetchone()[0], 'cancelled')
            request = connection.execute("SELECT * FROM actions WHERE kind='request:create'").fetchone()
            original_request = next(row for row in self.before['actions'] if row[2] == 'request:create')
            self.assertEqual(tuple(request)[:len(original_request)], original_request)
        server = create_server(port=0, data_dir=self.folder, web_dir=ROOT / 'web')
        self.addCleanup(server.server_close)
        self.assertIsNotNone(server.auth, 'schema3 must remain protected if flag omitted')
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(2)))
        auth = AuthService(self.store); admin = auth.bootstrap('qa_admin', PASSWORD)
        created = auth.create_account(admin, 'qa_clerk', '隔离旧记录认领', ['clerk'])
        clerk = auth.activate(created['activation_token'], PASSWORD)
        client = Client(server); client.request('GET', '/api/session')
        self.assertEqual(client.request('GET', '/api/matters/' + self.matter['id'])[0], 401)
        client.request('POST', '/api/auth/login', {'username': 'qa_admin', 'password': PASSWORD})
        self.assertEqual(client.request('GET', '/api/matters/' + self.matter['id'])[0], 404)
        self.assertEqual(client.request('GET', '/api/admin/legacy')[1]['unassigned_count'], 1)
        self.assertEqual(client.request('POST', '/api/admin/legacy/claim', {'expected_count': 1, 'target_account_id': clerk['id']})[0], 200)
        client.request('POST', '/api/auth/logout', {}); client.request('POST', '/api/auth/login', {'username': 'qa_clerk', 'password': PASSWORD})
        detail = client.request('GET', '/api/matters/' + self.matter['id'])
        self.assertEqual(detail[0], 200)
        self.assertEqual(detail[1]['artifacts'][0]['content'], 'MANUAL_PRESERVE_MARKER')
        self.assertEqual(detail[1]['sources'][0]['recorded_by'], 'local-preview')
        self.assertIn('legacy_claimed', [event['event_type'] for event in detail[1]['activities']])

    def test_07_failed_identity_migration_rolls_back_and_backup_readable(self):
        with self.assertRaises(sqlite3.Error):
            self.store.enable_identity(MENU_SCHEMA + '; CREATE TABLE qa_transient(id INTEGER); SELECT * FROM definitely_missing_table;')
        self.assertFalse(self.store.requires_auth)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name IN ('accounts','qa_transient')").fetchone())
            for table, before in self.before.items():
                self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM ' + table)], before)
        with closing(sqlite3.connect(next((self.folder / 'backups').glob('schema-v2-*.sqlite3')))) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(backup.execute('SELECT content FROM artifacts').fetchone()[0], 'MANUAL_PRESERVE_MARKER')

    def test_08_cli_requires_interactive_and_os_instance_lock(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'run.py'), '--data-dir', str(self.folder), '--setup-admin', 'qa_admin'], input='', text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn('交互终端', result.stderr)
        with instance_lock(self.folder):
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', 'from pathlib import Path; from run import instance_lock; import sys;\nwith instance_lock(Path(sys.argv[1])): print("UNEXPECTED_LOCK")', str(self.folder)], cwd=ROOT, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('UNEXPECTED_LOCK', result.stdout)


if __name__ == '__main__':
    unittest.main()
