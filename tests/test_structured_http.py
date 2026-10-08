"""Independent source/statement/export/isolation acceptance over actual localhost HTTP."""
import http.client
import json
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

import test_identity_http as fixture
from assistant_app.server import create_server
from assistant_app.store import dump, uid


SOURCE = '讨论：原述通风问题\n提议：考虑增加设备\n决定：记录明确决定先核资料\n分歧：反对立即采购\n行动：本人核对，期限2026-10-09\n行动：张工尽快处理\n行动：本人检查，期限2026-10-10T00:30:00+01:00'


class StructuredHTTP(unittest.TestCase):
    setUp = fixture.IdentityHTTP.setUp
    stop = fixture.IdentityHTTP.stop
    check = fixture.IdentityHTTP.check
    login = fixture.IdentityHTTP.login
    account = fixture.IdentityHTTP.account
    create = fixture.IdentityHTTP.create

    def owner(self):
        self.client, self.actor = self.account('structured_owner', ['employee'])

    def matter(self, domain='meeting', text=SOURCE):
        matter = self.create(self.client, domain)
        matter = self.check(self.client.request('POST', '/api/matters/' + matter['id'] + '/sources', {'expected_version': matter['version'], 'kind': 'oral_note', 'text': text, 'reported_by': '工程原记录者'}), 200)
        return matter, matter['sources'][-1]['id']

    def item(self, source, line, kind='action', own=False, deadline=None):
        return {'id': uid(), 'kind': kind, 'text': '工程原记录 ' + kind, 'refs': [{'source_id': source, 'start_line': line, 'end_line': line}],
                'confirmation': 'local_checked', 'responsible_text': '本人' if own else '张工原述', 'responsible_account_id': self.actor['id'] if own else None,
                'deadline_text': '尽快，未给明确年月日' if deadline is None else deadline['value'], 'deadline': deadline or {'kind': 'unknown', 'value': None}}

    def save_data(self, view, items, structure_version=None):
        return {'expected_version': view['version'], 'input_revision': view['revision_no'], 'base_structure_version': view.get('structure_version', 0) if structure_version is None else structure_version, 'upserts': items, 'removed_ids': []}

    def save(self, view, items, domain='meeting', key=None):
        return self.check(self.client.request('POST', '/api/matters/' + view['id'] + '/' + domain + '/structure', self.save_data(view, items), key), 200)

    def get(self, matter_id, domain='meeting', **query):
        path = '/api/matters/' + matter_id + '/' + domain + '/structure'
        return self.check(self.client.request('GET', path + ('?' + urlencode(query) if query else '')), 200)

    def progress(self, view, item, command, **extra):
        data = {'expected_version': view['version'], 'item_revision': item['item_revision'], 'command': command, 'note': '工程实际说明', **extra}
        return self.check(self.client.request('POST', '/api/matters/' + view['id'] + '/meeting/actions/' + item['id'] + '/progress', data), 200)

    def render(self, view):
        return self.check(self.client.request('POST', '/api/matters/' + view['id'] + '/structure/render', {'expected_version': view['version'], 'input_revision': view['revision_no'], 'structure_version': view['structure_version']}), 201)

    def download_bytes(self, client, artifact_id, version, fmt):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', '/api/artifacts/' + artifact_id + '/download?' + urlencode({'version': version, 'format': fmt}), headers={'Cookie': client.cookie})
            response = connection.getresponse()
            raw = response.read()
            self.assertEqual(response.status, 200)
            self.assertIn('charset=utf-8', response.getheader('Content-Type'))
            self.assertIn('preparation-v' + str(version) + '.' + fmt, response.getheader('Content-Disposition'))
            return raw
        finally:
            connection.close()

    def test_01_source_classification_stable_items_unknown_deadline_and_stale(self):
        self.owner()
        matter, source = self.matter()
        items = [self.item(source, line, kind) for line, kind in enumerate(('discussion', 'proposal', 'decision', 'dissent', 'action'), 1)]
        saved = self.save(matter, items)
        self.assertFalse(saved['stale'])
        self.assertEqual(saved['input_revision'], saved['revision_no'])
        self.assertEqual([item['kind'] for item in saved['items']], ['discussion', 'proposal', 'decision', 'dissent', 'action'])
        self.assertEqual(saved['items'][2]['refs'][0]['excerpt'], SOURCE.splitlines()[2])
        self.assertEqual(saved['items'][4]['deadline'], {'kind': 'unknown', 'value': None})
        self.assertIsNone(saved['items'][4]['deadline_hint']['overdue'])
        changed = {**items[0], 'text': '人工核对后修正讨论原述'}
        saved = self.save(saved, [changed])
        self.assertEqual([item['id'] for item in saved['items']], [item['id'] for item in items])
        foreign, foreign_source = self.matter()
        bad = {**changed, 'refs': [{'source_id': foreign_source, 'start_line': 1, 'end_line': 1}]}
        self.check(self.client.request('POST', '/api/matters/' + saved['id'] + '/meeting/structure', self.save_data(saved, [bad])), 422)
        forged = {**changed, 'refs': [{'source_id': source, 'start_line': 1, 'end_line': 1, 'excerpt': '伪造摘录'}]}
        self.check(self.client.request('POST', '/api/matters/' + saved['id'] + '/meeting/structure', self.save_data(saved, [forged])), 422)
        rendered = self.render(saved)
        self.assertIn('记录中的明确决定', rendered['content'])
        self.assertIn('## 分歧', rendered['content'])
        detail = self.check(self.client.request('GET', '/api/matters/' + saved['id']), 200)
        self.check(self.client.request('POST', '/api/matters/' + saved['id'] + '/sources', {'expected_version': detail['version'], 'kind': 'user_text', 'text': '工程新来源需重核'}), 200)
        stale = self.get(saved['id'])
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['items'][0]['text'], changed['text'])
        self.assertEqual(stale['items'][0]['confirmation'], 'local_checked')
        self.check(self.client.request('POST', '/api/matters/' + saved['id'] + '/structure/render', {'expected_version': stale['version'], 'input_revision': stale['revision_no'], 'structure_version': stale['structure_version']}), 409)

    def test_02_actions_date_precision_self_transcribed_and_old_scope(self):
        self.owner()
        matter, source = self.matter()
        own = self.item(source, 5, own=True, deadline={'kind': 'date', 'value': '2026-10-09', 'confirmed': True})
        other = self.item(source, 6)
        timed = self.item(source, 7, own=True, deadline={'kind': 'datetime', 'value': '2026-10-10T00:30:00+01:00', 'confirmed': True})
        saved = self.save(matter, [own, other, timed])
        day = self.get(saved['id'], as_of_date='2026-10-09', display_timezone='+01:00')
        self.assertFalse(day['items'][0]['deadline_hint']['overdue'])
        later = self.get(saved['id'], as_of_date='2026-10-10', display_timezone='+01:00', as_of_datetime='2026-10-09T23:45:00+00:00')
        self.assertTrue(later['items'][0]['deadline_hint']['overdue'])
        self.assertTrue(later['items'][2]['deadline_hint']['overdue'])
        self.assertIsNone(later['items'][1]['deadline_hint']['overdue'])
        self.assertIsNone(self.get(saved['id'], as_of_date='2026-10-10')['items'][0]['deadline_hint']['overdue'])
        self.check(self.client.request('GET', '/api/matters/' + saved['id'] + '/meeting/structure?' + urlencode({'as_of_datetime': '2026-10-10T10:00:00'})), 422)
        missing_zone = {**timed, 'deadline': {'kind': 'datetime', 'value': '2026-10-10T00:30:00', 'confirmed': True}}
        self.check(self.client.request('POST', '/api/matters/' + saved['id'] + '/meeting/structure', self.save_data(saved, [missing_zone])), 422)
        self.check(self.client.request('POST', '/api/matters/' + saved['id'] + '/meeting/actions/' + other['id'] + '/progress', {'expected_version': saved['version'], 'item_revision': 1, 'command': 'self_accept', 'note': '不得代称本人'}), 403)
        original_revision = saved['revision_no']
        saved = self.progress(saved, saved['items'][0], 'self_accept')
        self.assertEqual(saved['revision_no'], original_revision)
        saved = self.progress(saved, saved['items'][1], 'reported_progress', reported_by='张工原述')
        event = saved['items'][1]['progress']['reported_progress'][0]
        self.assertEqual((event['evidence_class'], event['actor_id'], event['reported_by']), ('transcribed_statement', self.actor['id'], '张工原述'))
        changed = {**own, 'responsible_account_id': None, 'responsible_text': '改为张工原述'}
        saved = self.save(saved, [changed])
        self.assertEqual(saved['items'][0]['item_revision'], 2)
        self.assertEqual(saved['items'][1]['item_revision'], 1)
        self.assertTrue(saved['items'][0]['progress']['events'][0]['old_item_scope'])
        self.assertEqual(saved['items'][0]['progress']['acceptance'], 'unknown')
        self.check(self.client.request('POST', '/api/matters/' + saved['id'] + '/meeting/actions/' + own['id'] + '/progress', {'expected_version': saved['version'], 'item_revision': 1, 'command': 'result_note', 'note': '旧范围'}), 409)
        paused = self.check(self.client.request('POST', '/api/matters/' + saved['id'] + '/control', {'expected_version': saved['version'], 'command': 'pause'}), 200)
        current = self.get(saved['id'])
        current = self.progress(current, current['items'][1], 'reported_progress', reported_by='张工实际补充')
        self.check(self.client.request('POST', '/api/matters/' + saved['id'] + '/structure/render', {'expected_version': current['version'], 'input_revision': current['revision_no'], 'structure_version': current['structure_version']}), 409)

    def test_03_document_real_utf8_fixed_version_txt_md_manual_artifact_retained(self):
        self.owner()
        matter, source = self.matter('document', '第一段事实底稿。\n第二段仅供核对。')
        sections = [{'id': uid(), 'heading': heading, 'body': body, 'refs': [{'source_id': source, 'start_line': line, 'end_line': line}], 'confirmation': 'local_checked'} for line, heading, body in [(1, '事实说明', '中文第一段\n第二行保留。'), (2, '待核材料', '准备正文，正式签发待责任人。')]]
        saved = self.save(matter, sections, 'document')
        artifact = self.render(saved)
        self.assertIn('通用准备稿', artifact['content'])
        manual = '人工正文独立修改\n不能被结构重整覆盖。'
        self.check(self.client.request('PATCH', '/api/artifacts/' + artifact['artifact_id'], {'base_version': 1, 'edited_content': manual}), 200)
        latest = self.get(saved['id'], 'document')
        new_artifact = self.render(latest)
        self.assertNotEqual(artifact['artifact_id'], new_artifact['artifact_id'])
        for version, content in ((1, artifact['content']), (2, manual)):
            for fmt in ('txt', 'md'):
                raw = self.download_bytes(self.client, artifact['artifact_id'], version, fmt)
                target = Path(self.temp.name) / ('actual-download-v' + str(version) + '.' + fmt)
                target.write_bytes(raw)
                self.assertEqual(target.read_bytes(), content.encode('utf-8'))
                self.assertEqual(target.read_text(encoding='utf-8'), content)
        self.check(self.client.request('GET', '/api/artifacts/' + artifact['artifact_id'] + '/download?version=999&format=txt'), 404)
        for query in ('version=0', 'version=abc', 'version=1&format=docx', 'version=1&version=2'):
            self.check(self.client.request('GET', '/api/artifacts/' + artifact['artifact_id'] + '/download?' + query), 422)

    def test_04_isolation_preserved_metadata_restart_and_default_six_table_path(self):
        self.owner()
        other, _ = self.account('structured_other', ['employee'])
        expense = self.create(self.client, 'expense')
        rows = [{'id': uid(), 'label': '工程测试费用', 'value': value, 'currency': 'USD', 'period': '2026-10', 'source_id': expense['sources'][0]['id'], 'source_position': '第1行工程目标'} for value in ('0.1', '0.2')]
        expense = self.check(self.client.request('POST', '/api/matters/' + expense['id'] + '/rows/confirm', {'expected_version': expense['version'], 'rows': rows}), 200)
        original_calculation = expense['calculation']
        self.assertEqual(original_calculation['groups'][0]['total'], '0.3')
        matter, source = self.matter()
        item = self.item(source, 6)
        metadata = {'__rows': [{'legacy_fixture_only': '保存不解释此元数据'}], '__custom': {'preserve': True}}
        with self.server.store.transaction() as connection:
            facts = self.server.store._stored_facts(connection, matter)
            facts.update(metadata)
            connection.execute('UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?', (dump(facts), matter['id'], matter['revision_no']))
        data, key = self.save_data(matter, [item]), uid()
        saved = self.check(self.client.request('POST', '/api/matters/' + matter['id'] + '/meeting/structure', data, key), 200)
        saved = self.progress(saved, saved['items'][0], 'reported_progress', reported_by='工程重启原述人')
        artifact = self.render(saved)
        for method, path, body, replay_key in [
            ('GET', '/api/matters/' + matter['id'] + '/meeting/structure', None, None),
            ('POST', '/api/matters/' + matter['id'] + '/meeting/structure', data, key),
            ('POST', '/api/matters/' + matter['id'] + '/meeting/actions/' + item['id'] + '/progress', {'expected_version': saved['version'], 'item_revision': 1, 'command': 'reported_progress', 'reported_by': '原述', 'note': '他账号无权'}, None),
            ('GET', '/api/artifacts/' + artifact['artifact_id'] + '/download?version=1&format=md', None, None),
        ]:
            self.check(other.request(method, path, body, replay_key), 404)
        self.check(self.client.request('POST', '/api/matters/' + matter['id'] + '/meeting/structure', data), 409)
        with self.server.store.connect() as connection:
            latest = json.loads(connection.execute('SELECT fields FROM revisions WHERE matter_id=? ORDER BY revision_no DESC LIMIT 1', (matter['id'],)).fetchone()[0])
            self.assertEqual(latest['__rows'], metadata['__rows'])
            self.assertEqual(latest['__custom'], metadata['__custom'])
            self.assertEqual(latest['__workflow'], facts['__workflow'])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sources WHERE matter_id=?', (matter['id'],)).fetchone()[0], 2)
        folder = Path(self.temp.name)
        self.stop()
        self.server = create_server(port=0, data_dir=folder, web_dir=fixture.ROOT / 'web')
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True); self.thread.start()
        self.client = self.login('structured_owner')
        restored = self.get(saved['id'])
        self.assertEqual(restored['items'][0]['id'], item['id'])
        self.assertEqual(restored['items'][0]['refs'][0]['excerpt'], SOURCE.splitlines()[5])
        restored_event = restored['items'][0]['progress']['reported_progress'][0]
        self.assertEqual((restored_event['reported_by'], restored_event['actor_id'], restored_event['evidence_class']), ('工程重启原述人', self.actor['id'], 'transcribed_statement'))
        self.assertEqual(self.download_bytes(self.client, artifact['artifact_id'], 1, 'md'), artifact['content'].encode())
        expense_after = self.check(self.client.request('GET', '/api/matters/' + expense['id']), 200)
        self.assertEqual(expense_after['rows'], rows)
        self.assertEqual(expense_after['calculation'], original_calculation)
        default = create_server(port=0, data_dir=folder / 'default-six', web_dir=fixture.ROOT / 'web')
        thread = threading.Thread(target=default.serve_forever, kwargs={'poll_interval': .02}, daemon=True); thread.start()
        try:
            client = fixture.Client(default); self.check(client.request('GET', '/api/session'), 200)
            fresh = self.check(client.request('POST', '/api/matters', {'domain': 'document', 'goal_text': '默认隔离公文底稿 中文'}), 201)
            section = {'id': uid(), 'heading': '默认正文', 'body': '默认六表真实正文', 'refs': [{'source_id': fresh['sources'][0]['id'], 'start_line': 1, 'end_line': 1}], 'confirmation': 'local_checked'}
            fresh = self.check(client.request('POST', '/api/matters/' + fresh['id'] + '/document/structure', self.save_data(fresh, [section])), 200)
            rendered = self.check(client.request('POST', '/api/matters/' + fresh['id'] + '/structure/render', {'expected_version': fresh['version'], 'input_revision': fresh['revision_no'], 'structure_version': fresh['structure_version']}), 201)
            self.assertIn('默认六表真实正文', rendered['content'])
            with default.store.connect() as connection:
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='accounts'").fetchone())
        finally:
            default.shutdown(); default.server_close(); thread.join(2)


if __name__ == '__main__':
    unittest.main()
