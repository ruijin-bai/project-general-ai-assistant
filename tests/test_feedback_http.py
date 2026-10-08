"""Independent HTTP acceptance for voluntary feedback correction and revocation."""
import json
import unittest

import test_identity_http as fixture
from assistant_app.store import uid


class FeedbackHTTP(unittest.TestCase):
    setUp = fixture.IdentityHTTP.setUp
    stop = fixture.IdentityHTTP.stop
    check = fixture.IdentityHTTP.check
    login = fixture.IdentityHTTP.login
    account = fixture.IdentityHTTP.account
    create = fixture.IdentityHTTP.create
    publication = fixture.IdentityHTTP.publication

    def submit(self, employee, publication, key=None):
        data = {'version': publication['version'], 'feedback_text': 'ORIGINAL_PRIVATE_HISTORY 工程原意见', 'dietary_constraint': '工程明确约束'}
        feedback = self.check(employee.request('POST', '/api/menu-publications/' + publication['id'] + '/feedback', data, key), 201)
        return feedback, data

    def test_01_correction_withdraw_old_replays_keep_current_private_state(self):
        cook, _, _, publication = self.publication()
        employee, _ = self.account('feedback_employee', ['employee'])
        create_key = uid()
        feedback, original = self.submit(employee, publication, create_key)
        path = '/api/menu-feedback/' + feedback['id']
        own = self.check(employee.request('GET', path), 200)
        revise_key = uid()
        revised_data = {'expected_version': own['version'], 'feedback_text': 'CORRECTED_CURRENT 工程本人更正', 'dish': '蔬菜'}
        changed = self.check(employee.request('POST', path + '/revise', revised_data, revise_key), 200)
        self.assertTrue(changed['shared'])
        projection_path = '/api/menu-publications/' + publication['id'] + '/feedback'
        projection = self.check(cook.request('GET', projection_path), 200)
        self.assertEqual(len(projection['items']), 1)
        item = projection['items'][0]
        self.assertEqual(item['feedback_text'], revised_data['feedback_text'])
        self.assertTrue(item['corrected_by_self'])
        self.assertEqual(item['correction_count'], 1)
        raw = json.dumps(projection)
        for private in ('ORIGINAL_PRIVATE_HISTORY', 'sources', 'activities', 'history', 'artifacts'):
            self.assertNotIn(private, raw)
        withdrawn = self.check(employee.request('POST', path + '/withdraw', {'expected_version': changed['version']}), 200)
        self.assertFalse(withdrawn['shared'])
        self.assertEqual(self.check(cook.request('GET', projection_path), 200)['items'], [])
        for replay, expected_status in (
            (employee.request('POST', '/api/menu-publications/' + publication['id'] + '/feedback', original, create_key), 201),
            (employee.request('POST', path + '/revise', revised_data, revise_key), 200),
        ):
            current = self.check(replay, expected_status)
            self.assertFalse(current['shared'])
            self.assertFalse(current['shared_with_publisher'])
            self.assertEqual(current['feedback_text'], revised_data['feedback_text'])
        private = self.check(employee.request('POST', path + '/revise', {'expected_version': withdrawn['version'], 'feedback_text': 'PRIVATE_AFTER_WITHDRAW 仅本人保存'}), 200)
        self.assertFalse(private['shared'])
        self.assertEqual(self.check(cook.request('GET', projection_path), 200)['items'], [])
        detail = self.check(employee.request('GET', '/api/matters/' + feedback['id']), 200)
        self.assertTrue(any('ORIGINAL_PRIVATE_HISTORY' in source['text'] for source in detail['sources']))
        self.assertTrue(any('PRIVATE_AFTER_WITHDRAW' in source['text'] for source in detail['sources']))

    def test_02_other_actors_cannot_read_or_edit_and_menu_withdraw_does_not_block_self_revocation(self):
        cook, _, _, publication = self.publication()
        employee, _ = self.account('feedback_employee', ['employee'])
        other, _ = self.account('feedback_other_employee', ['employee'])
        feedback, _ = self.submit(employee, publication)
        path = '/api/menu-feedback/' + feedback['id']
        own = self.check(employee.request('GET', path), 200)
        for client, denied in ((other, 404), (cook, 403)):
            self.check(client.request('GET', path), denied)
            self.check(client.request('POST', path + '/revise', {'expected_version': own['version'], 'feedback_text': '冒改本人意见'}), denied)
            self.check(client.request('POST', path + '/withdraw', {'expected_version': own['version']}), denied)
        self.check(cook.request('POST', '/api/menu-publications/' + publication['id'] + '/withdraw', {'expected_version': publication['version']}), 200)
        withdrawn = self.check(employee.request('POST', path + '/withdraw', {'expected_version': own['version']}), 200)
        self.assertFalse(withdrawn['shared'])
        self.assertEqual(self.check(cook.request('GET', '/api/menu-publications/' + publication['id'] + '/feedback'), 200)['items'], [])
        self.check(employee.request('GET', '/api/matters/' + feedback['id']), 200)


if __name__ == '__main__':
    unittest.main()
