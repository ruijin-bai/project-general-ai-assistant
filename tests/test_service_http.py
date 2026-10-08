"""Independent local service HTTP and schema4 upgrade acceptance."""
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from assistant_app.auth import AuthService
from assistant_app.menu import MENU_SCHEMA, MenuService
from assistant_app.server import create_server
from assistant_app.services import SERVICE_SCHEMA
from assistant_app.store import Store, uid
import test_identity_http as identity_fixture
from test_identity_http import Client, PASSWORD, ROOT


class ServiceHTTP(unittest.TestCase):
    setUp = identity_fixture.IdentityHTTP.setUp
    stop = identity_fixture.IdentityHTTP.stop
    check = identity_fixture.IdentityHTTP.check
    login = identity_fixture.IdentityHTTP.login
    account = identity_fixture.IdentityHTTP.account
    create = identity_fixture.IdentityHTTP.create
    ready = identity_fixture.IdentityHTTP.ready

    def actors(self):
        self.owner, self.owner_account = self.account('svc_owner', ['employee'])
        self.clerk, self.clerk_account = self.account('svc_clerk', ['clerk'])
        self.executor, self.executor_account = self.account('svc_executor', ['executor'])
        self.other_executor, self.other_executor_account = self.account('svc_other_executor', ['executor'])

    def matter(self, domain='repair'):
        matter = self.create(self.owner, domain)
        changes = {'location': '工程夹具房间', 'problem' if domain == 'repair' else 'scope': '工程夹具待处理事项'}
        if domain == 'repair':
            changes['contact'] = 'PRIVATE_PHONE_MARKER 不默认公开'
        return self.check(self.owner.request('POST', '/api/matters/' + matter['id'] + '/facts/confirm', {'expected_version': matter['version'], 'field_changes': changes}), 200)

    def submit(self, matter, key=None):
        data = {'expected_version': matter['version'], 'input_revision': matter['revision_no'], 'clerk_id': self.clerk_account['id']}
        return self.check(self.owner.request('POST', '/api/matters/' + matter['id'] + '/service/submit', data, key), 201)

    def assign(self, case, account=None, note=''):
        return self.check(self.clerk.request('POST', '/api/services/' + case['id'] + '/assign', {'expected_version': case['version'], 'executor_id': (account or self.executor_account)['id'], 'note': note}), 200)

    def respond(self, case, command, note='', client=None, key=None):
        data = {'expected_version': case['version'], 'assignment_epoch': case['assignment_epoch'], 'command': command, 'note': note}
        return self.check((client or self.executor).request('POST', '/api/services/' + case['id'] + '/respond', data, key), 200)

    def test_07_readable_participants_waiting_keep_unshared_requester_name_private(self):
        self.actors()
        case = self.assign(self.submit(self.matter()))
        actual_name = self.owner_account['display_name']
        for client in (self.clerk, self.executor):
            view = self.check(client.request('GET', '/api/services/' + case['id']), 200)
            requester = view['participants']['requester']
            self.assertEqual(requester['id'], self.owner_account['id'])
            self.assertEqual(requester['display_name'], '服务发起人')
            self.assertNotIn(actual_name, json.dumps(view, ensure_ascii=False))
            by_actor = [event for event in view['events'] if event['actor_id'] == self.owner_account['id']]
            if client is self.clerk:
                self.assertTrue(by_actor, 'clerk must have actual requester submit event to check redaction')
            self.assertTrue(all(event['actor_display_name'] == '服务发起人' for event in by_actor))
            requester_wait = next(item for item in view['waiting'] if item['who'] == self.owner_account['id'])
            self.assertEqual(requester_wait['who_name'], '服务发起人')
            executor_wait = next(item for item in view['waiting'] if item['who'] == self.executor_account['id'])
            self.assertEqual(executor_wait['who_name'], self.executor_account['display_name'])
        owner_view = self.check(self.owner.request('GET', '/api/services/' + case['id']), 200)
        self.assertEqual(owner_view['participants']['requester']['display_name'], actual_name)
        own_events = [event for event in owner_view['events'] if event['actor_id'] == self.owner_account['id']]
        self.assertTrue(own_events)
        self.assertTrue(all(event['actor_display_name'] == actual_name for event in own_events))
        with self.server.store.connect() as connection:
            original = json.loads(connection.execute("SELECT message FROM events WHERE event_type='service_event' ORDER BY rowid LIMIT 1").fetchone()[0])
            self.assertEqual((original['actor_id'], original['actor_display_name']), (self.owner_account['id'], actual_name))

    def test_01_repair_cleaning_projection_actual_actor_flow_and_no_formal_close(self):
        self.actors()
        for domain in ('repair', 'cleaning'):
            with self.subTest(domain=domain):
                matter = self.matter(domain)
                case = self.submit(matter)
                clerk_view = self.check(self.clerk.request('GET', '/api/services/' + case['id']), 200)
                self.assertEqual(clerk_view['projection']['contact'], '')
                self.assertEqual(clerk_view['projection']['entry_permission'], {'status': 'unknown'})
                raw = json.dumps(clerk_view, ensure_ascii=False)
                for private in ('PRIVATE_RAW_MARKER', 'PRIVATE_PHONE_MARKER', 'sources', 'artifacts', 'matter_id'):
                    self.assertNotIn(private, raw)
                case = self.assign(case)
                case = self.respond(case, 'accept')
                case = self.respond(case, 'blocked', '工程进入条件待核')
                self.assertEqual(case['tracks']['requester_result'], 'unknown')
                self.assertTrue(any(item['who'] == self.clerk_account['id'] for item in case['waiting']))
                case = self.respond(case, 'claimed_done', '工程执行者本人声明，尚未核实现场结果')
                self.assertEqual((case['tracks']['execution'], case['tracks']['requester_result'], case['tracks']['inspection']), ('claimed_done', 'unknown', 'unknown'))
                case = self.check(self.owner.request('POST', '/api/services/' + case['id'] + '/result', {'expected_version': case['version'], 'assignment_epoch': case['assignment_epoch'], 'result': 'resolved', 'note': '工程发起人本人结果'}), 200)
                self.assertEqual((case['tracks']['execution'], case['tracks']['requester_result'], case['tracks']['inspection'], case['tracks']['closure']), ('claimed_done', 'resolved', 'unknown', 'waiting_rule'))
                self.assertEqual(self.check(self.owner.request('POST', '/api/services/' + case['id'] + '/close', {}), 409)['code'], 'contract_unconfirmed')
                self.assertEqual(case['events'][-2]['actor_id'], self.executor_account['id'])
                self.assertEqual(case['events'][-1]['actor_id'], self.owner_account['id'])

    def test_02_foreign_service_and_private_download_deny_wrong_actor(self):
        self.actors()
        cook, _ = self.account('svc_cook', ['cook'])
        matter = self.ready(self.owner, self.matter())
        artifact = matter['artifacts'][0]
        case = self.assign(self.submit(matter))
        for client in (cook, self.admin, self.other_executor):
            self.check(client.request('GET', '/api/services/' + case['id']), 404)
            self.check(client.request('POST', '/api/services/' + case['id'] + '/close', {}), 404)
            self.assertEqual(self.check(client.request('GET', '/api/services'), 200)['items'], [])
        for client in (self.clerk, self.executor, self.other_executor, cook, self.admin):
            self.check(client.request('GET', '/api/matters/' + matter['id']), 404)
            self.check(client.request('GET', '/api/artifacts/' + artifact['id'] + '/download'), 404)
        response_data = {'expected_version': case['version'], 'assignment_epoch': case['assignment_epoch'], 'command': 'accept'}
        self.check(self.clerk.request('POST', '/api/services/' + case['id'] + '/respond', response_data), 403)
        self.check(self.executor.request('POST', '/api/services/' + case['id'] + '/result', {'expected_version': case['version'], 'assignment_epoch': case['assignment_epoch'], 'result': 'resolved'}), 403)
        candidates = self.check(self.owner.request('GET', '/api/service-accounts?capability=clerk'), 200)
        self.assertTrue(all(set(item) == {'id', 'display_name'} for item in candidates['items']))

    def test_03_reassign_old_epoch_and_withdraw_deny_replays_preserve_history(self):
        self.actors()
        matter = self.matter('cleaning')
        case = self.assign(self.submit(matter))
        old_data = {'expected_version': case['version'], 'assignment_epoch': case['assignment_epoch'], 'command': 'accept', 'note': ''}
        old_key, old_epoch = uid(), case['assignment_epoch']
        case = self.respond(case, 'accept', key=old_key)
        replay = self.check(self.executor.request('POST', '/api/services/' + case['id'] + '/respond', old_data, old_key), 200)
        self.assertEqual(sum(event['command'] == 'accept' for event in replay['events']), 1)
        case = self.respond(case, 'blocked', 'OLD_EXECUTOR_PRIVATE_NOTE')
        case = self.assign(case, self.other_executor_account, '工程改派原因')
        self.assertGreater(case['assignment_epoch'], old_epoch)
        self.assertEqual(case['tracks']['acceptance'], 'pending')
        self.check(self.executor.request('GET', '/api/services/' + case['id']), 404)
        self.check(self.executor.request('POST', '/api/services/' + case['id'] + '/respond', old_data, old_key), 404)
        self.assertEqual(self.check(self.executor.request('GET', '/api/services'), 200)['items'], [])
        new_view = self.check(self.other_executor.request('GET', '/api/services/' + case['id']), 200)
        self.assertNotIn('OLD_EXECUTOR_PRIVATE_NOTE', json.dumps(new_view))
        self.check(self.other_executor.request('POST', '/api/services/' + case['id'] + '/respond', {'expected_version': case['version'], 'assignment_epoch': old_epoch, 'command': 'accept'}), 409)
        new_key = uid()
        new_data = {'expected_version': case['version'], 'assignment_epoch': case['assignment_epoch'], 'command': 'accept', 'note': ''}
        case = self.respond(case, 'accept', client=self.other_executor, key=new_key)
        statement_key = uid(); statement_data = {'expected_version': case['version'], 'reported_by': '工程原陈述人', 'note': '工程转述待核'}
        case = self.check(self.clerk.request('POST', '/api/services/' + case['id'] + '/statement', statement_data, statement_key), 200)
        withdrawn = self.check(self.owner.request('POST', '/api/services/' + case['id'] + '/withdraw', {'expected_version': case['version'], 'note': '本人明确撤共享'}), 200)
        self.assertEqual(withdrawn['sharing_status'], 'withdrawn')
        for client in (self.clerk, self.other_executor):
            self.check(client.request('GET', '/api/services/' + case['id']), 404)
            self.assertEqual(self.check(client.request('GET', '/api/services'), 200)['items'], [])
        self.check(self.clerk.request('POST', '/api/services/' + case['id'] + '/statement', statement_data, statement_key), 404)
        self.check(self.other_executor.request('POST', '/api/services/' + case['id'] + '/respond', new_data, new_key), 404)
        owner_view = self.check(self.owner.request('GET', '/api/services/' + case['id']), 200)
        self.assertTrue(any(event['note'] == 'OLD_EXECUTOR_PRIVATE_NOTE' for event in owner_view['events']))
        self.check(self.owner.request('GET', '/api/matters/' + matter['id']), 200)

    def test_04_concurrent_assignment_version_conflict_and_idempotent_one_event(self):
        self.actors()
        matter, submit_key = self.matter(), uid()
        case = self.submit(matter, submit_key)
        replay = self.submit(matter, submit_key)
        self.assertEqual(case['id'], replay['id'])
        self.assertEqual(sum(event['command'] == 'submit' for event in replay['events']), 1)
        barrier = threading.Barrier(2)
        def assign(account):
            barrier.wait(timeout=2)
            return self.clerk.request('POST', '/api/services/' + case['id'] + '/assign', {'expected_version': case['version'], 'executor_id': account['id']})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(assign, (self.executor_account, self.other_executor_account)))
        self.assertCountEqual([result[0] for result in results], [200, 409])
        current = self.check(self.clerk.request('GET', '/api/services/' + case['id']), 200)
        self.assertEqual(sum(event['command'] == 'assign' for event in current['events']), 1)
        active_client = self.executor if current['executor_id'] == self.executor_account['id'] else self.other_executor
        incomplete = self.create(self.owner, 'repair')
        self.check(self.owner.request('POST', '/api/matters/' + incomplete['id'] + '/service/submit', {'expected_version': 1, 'input_revision': 1, 'clerk_id': self.clerk_account['id']}), 422)
        self.check(active_client.request('POST', '/api/services/' + case['id'] + '/respond', {'expected_version': current['version'], 'assignment_epoch': current['assignment_epoch'], 'command': 'claimed_done', 'note': '未接受不可处理'}), 409)
        paused = self.check(self.owner.request('POST', '/api/matters/' + matter['id'] + '/control', {'expected_version': matter['version'], 'command': 'pause'}), 200)
        current = self.respond(current, 'accept', client=active_client)
        current = self.respond(current, 'blocked', '工程暂停不抹本人实际反馈', client=active_client)
        self.assertEqual(current['tracks']['execution'], 'blocked')
        self.assertEqual(paused['assistant_status'], 'paused')


class ServiceMigration(unittest.TestCase):
    def setUp(self):
        identity_fixture.IdentityMigration.setUp(self)
        self.store.enable_identity(MENU_SCHEMA)
        auth = AuthService(self.store)
        admin = auth.bootstrap('migration_admin', PASSWORD)
        actors = {}
        for name, caps in [('migration_cook', ['cook']), ('migration_employee', ['employee'])]:
            created = auth.create_account(admin, name, '隔离升级工程身份', caps)
            actors[name] = auth.activate(created['activation_token'], PASSWORD)
        menu = MenuService(self.store)
        with self.store.as_actor(actors['migration_cook']):
            matter = self.store.mutate('create', None, {'domain': 'menu', 'goal_text': '工程升级菜单'}, uid())
            publication = menu.publish(matter['id'], {'expected_version': 1, 'menu_date': '2026-10-09', 'meal_slot': '午餐', 'dishes': '工程快照', 'notes': ''}, uid())
        with self.store.as_actor(actors['migration_employee']):
            menu.feedback(publication['id'], {'version': 1, 'feedback_text': '工程升级保留意见'}, uid())
        with self.store.connect() as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            self.before = {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table)] for table in tables}

    def test_05_schema4_backup_preserves_identity_menu_and_manual_records_flag_omission_protected(self):
        self.store.enable_services()
        with closing(sqlite3.connect(next((self.folder / 'backups').glob('schema-v3-*.sqlite3')))) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 3)
            for table, rows in self.before.items():
                self.assertEqual(backup.execute('SELECT * FROM ' + table).fetchall(), rows)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertIsNone(connection.execute('PRAGMA foreign_key_check').fetchone())
            for table, rows in self.before.items():
                self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM ' + table)], rows)
        server = create_server(port=0, data_dir=self.folder, web_dir=ROOT / 'web')
        self.addCleanup(server.server_close)
        self.assertIsNotNone(server.auth)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(2)))
        client = Client(server)
        self.assertFalse(client.request('GET', '/api/session')[1]['authenticated'])
        self.assertEqual(client.request('GET', '/api/services')[0], 401)
        self.assertEqual(client.request('GET', '/api/matters/' + self.matter['id'])[0], 401)

    def test_06_schema4_fault_rollback_preserves_schema3_and_readable_backup(self):
        with patch('assistant_app.services.SERVICE_SCHEMA', SERVICE_SCHEMA + ';CREATE TABLE fault_transient(id INTEGER);SELECT * FROM missing_injected_table;'):
            with self.assertRaises(sqlite3.Error):
                self.store.enable_services()
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name IN ('service_cases','fault_transient')").fetchone())
            for table, rows in self.before.items():
                self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM ' + table)], rows)
        with closing(sqlite3.connect(next((self.folder / 'backups').glob('schema-v3-*.sqlite3')))) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(backup.execute('SELECT content FROM artifacts').fetchone()[0], 'MANUAL_PRESERVE_MARKER')


if __name__ == '__main__':
    unittest.main()
