"""One scoped adoption/version/privacy/durable-save check, no model calls."""
import tempfile
import unittest
from pathlib import Path
from assistant_app.menu import MenuService, MENU_SCHEMA
from assistant_app.store import Store, AppError, dump, uid


class MenuAdoptionSmoke(unittest.TestCase):
    def test_explicit_new_menu_statement(self):
        with tempfile.TemporaryDirectory(prefix='menu-adoption-', dir=Path(__file__).parent) as directory:
            store = Store(directory)
            store.enable_identity(MENU_SCHEMA)
            menu = MenuService(store)
            actors = {name: {'id': uid(), 'auth_epoch': 1} for name in ('cook', 'other_cook', 'employee', 'other_employee', 'clerk')}
            with store.transaction() as connection:
                for name, actor in actors.items():
                    cap = 'cook' if 'cook' in name else 'employee' if 'employee' in name else 'clerk'
                    connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active',?,0,'fixture')", (actor['id'], name, name, dump([cap])))
            def call(actor, owner, method, *args):
                with store.as_actor(actors[actor]):
                    return getattr(owner, method)(*args)
            def fail(status, actor, method, *args):
                with self.assertRaises(AppError) as caught:
                    call(actor, menu, method, *args)
                self.assertEqual(caught.exception.status, status)
            matter = call('cook', store, 'mutate', 'create', None, {'domain': 'menu', 'goal_text': '工程原菜单准备', 'auto_prepare': False}, uid())
            matter = call('cook', store, 'mutate', 'manual_artifact', matter['id'], {'expected_version': matter['version'], 'input_revision': matter['revision_no'], 'edited_content': '原人工稿'}, uid())
            pub = call('cook', menu, 'publish', matter['id'], {'expected_version': matter['version'], 'menu_date': '2026-10-21', 'meal_slot': '工程午餐', 'dishes': '工程旧菜品', 'notes': ''}, uid())
            first = call('employee', menu, 'feedback', pub['id'], {'version': 1, 'feedback_text': 'PrivateFeedback希望另备辣味'}, uid())
            second = call('other_employee', menu, 'feedback', pub['id'], {'version': 1, 'feedback_text': 'PrivateMinority保留清淡', 'dietary_constraint': 'PrivateConstraint'}, uid())
            data = {'expected_version': 1, 'menu_date': '2026-10-21', 'meal_slot': '工程午餐', 'dishes': '工程清淡豆腐，可选辣味', 'notes': '工程公开备注', 'adoptions': [{'feedback_id': first['id'], 'feedback_correction_count': 0, 'note': 'PrivateNote本人新版另备可选辣味，实际做法另核'}]}
            fail(404, 'other_cook', 'revise', pub['id'], data, uid())
            fail(403, 'clerk', 'revise', pub['id'], data, uid())
            fail(403, 'employee', 'revise', pub['id'], data, uid())
            fail(422, 'cook', 'revise', pub['id'], data | {'adoptions': data['adoptions'] * 2}, uid())
            fail(422, 'cook', 'revise', pub['id'], data | {'adoptions': [data['adoptions'][0] | {'note': ''}]}, uid())
            first = call('employee', menu, 'get_own_feedback', first['id'])
            first = call('employee', menu, 'update_feedback', first['id'], {'expected_version': first['expected_version'], 'feedback_text': 'PrivateFeedback明确另备可选辣味'}, uid())
            fail(409, 'cook', 'revise', pub['id'], data, uid())
            self.assertEqual(len(call('cook', menu, 'list_publications')['items']), 1)
            self.assertEqual(call('cook', menu, 'list_publications')['items'][0]['status'], 'published')
            data['adoptions'][0]['feedback_correction_count'] = 1
            before = call('cook', store, 'detail', matter['id'])
            paused = call('cook', store, 'mutate', 'control', matter['id'], {'expected_version': before['version'], 'command': 'pause'}, uid())
            key = uid()
            revised = call('cook', menu, 'revise', pub['id'], data, key)
            self.assertEqual(call('cook', menu, 'revise', pub['id'], data, key), revised)
            self.assertEqual(revised['version'], 2)
            for private in ('PrivateFeedback', 'PrivateMinority', 'PrivateConstraint', 'PrivateNote', first['id'], second['id'], actors['employee']['id']):
                self.assertNotIn(private, dump(call('other_employee', menu, 'list_publications')))
            own = call('employee', menu, 'get_own_feedback', first['id'])
            self.assertEqual(len(own['adoptions']), 1)
            self.assertFalse(own['adoptions'][0]['old_feedback_scope'])
            self.assertEqual(own['adoptions'][0]['target_menu_snapshot']['id'], revised['id'])
            self.assertEqual(own['adoptions'][0]['target_menu_snapshot']['dishes'], data['dishes'])
            self.assertEqual(call('other_employee', menu, 'get_own_feedback', second['id'])['adoptions'], [])
            self.assertEqual(call('cook', menu, 'list_feedback', revised['id'])['items'], [])
            after = call('cook', store, 'detail', matter['id'])
            for field in ('facts', 'sources', 'artifacts', 'tracks', 'revision_no'):
                self.assertEqual(after[field], before[field])
            self.assertEqual(after['assistant_status'], 'paused')
            # Later menu and opinion changes never move or silently reaffirm the declaration.
            next_menu = call('cook', menu, 'revise', revised['id'], {k: v for k, v in data.items() if k != 'adoptions'} | {'expected_version': 2, 'dishes': '工程第三版'}, uid())
            old_snapshot = call('employee', menu, 'get_own_feedback', first['id'])['adoptions'][0]['target_menu_snapshot']
            self.assertEqual(old_snapshot['version'], 2)
            self.assertEqual(old_snapshot['dishes'], data['dishes'])
            self.assertEqual(old_snapshot['status'], 'withdrawn')
            own = call('employee', menu, 'update_feedback', first['id'], {'expected_version': own['expected_version'], 'feedback_text': 'PrivateFeedback再次更正，不要求默认加辣'}, uid())
            self.assertTrue(own['adoptions'][0]['old_feedback_scope'])
            own = call('employee', menu, 'withdraw_feedback', first['id'], {'expected_version': own['expected_version']}, uid())
            self.assertEqual(len(own['adoptions']), 1)
            self.assertNotIn(first['id'], [item['id'] for item in call('cook', menu, 'list_feedback', pub['id'])['items']])
            # Cannot adopt a withdrawn or foreign publication opinion into another version.
            fail(404, 'cook', 'revise', next_menu['id'], data | {'expected_version': 3}, uid())
            reopened = Store(directory)
            with reopened.as_actor(actors['employee']):
                saved = MenuService(reopened).get_own_feedback(first['id'])
                self.assertFalse(saved['shared'])
                self.assertEqual(saved['adoptions'], own['adoptions'])
            with store.transaction() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE event_type='feedback_cook_adoption'").fetchone()[0], 1)
                connection.execute("UPDATE accounts SET capabilities='[]' WHERE id=?", (actors['cook']['id'],))
            fail(403, 'cook', 'list_feedback', pub['id'])


if __name__ == '__main__':
    unittest.main()
