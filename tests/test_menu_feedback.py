"""Actual Store checks for owner corrections and revocable feedback sharing."""

import json
import tempfile
import unittest
from pathlib import Path

from assistant_app.menu import MENU_SCHEMA, MenuService
from assistant_app.store import AppError, Store, dump, uid


class MenuFeedbackContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-feedback-revise-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.enable_identity(MENU_SCHEMA)
        self.menu = MenuService(self.store)
        self.actors = {}
        with self.store.transaction() as connection:
            for name, caps in (("cook", ["cook"]), ("employee", ["employee"]), ("other", ["employee"]), ("admin", [])):
                account_id = uid()
                connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,is_admin,created_at,created_by) VALUES(?,?,?,'active',?,?,0,'isolated-fixture')",
                                   (account_id, name, "隔离工程账号 " + name, dump(caps), int(name == "admin")))
                self.actors[name] = {"id": account_id, "auth_epoch": 1}
        with self.store.as_actor(self.actors["cook"]):
            self.menu_matter = self.store.mutate("create", None, {"goal_text": "隔离菜单准备", "domain": "menu"}, uid())
            self.publication = self.menu.publish(self.menu_matter["id"], {"expected_version": self.menu_matter["version"], "menu_date": "2026-10-09", "meal_slot": "午餐", "dishes": "工程夹具菜品", "notes": ""}, uid())
        self.original_data = {"version": 1, "feedback_text": "原始工程意见", "dish": "蔬菜", "dietary_constraint": "工程夹具原约束"}
        self.create_key = uid()
        self.feedback = self.call("employee", "feedback", self.publication["id"], self.original_data, self.create_key)

    def call(self, actor, method, *args):
        with self.store.as_actor(self.actors[actor]):
            return getattr(self.menu, method)(*args)

    def error(self, status, actor, method, *args):
        with self.assertRaises(AppError) as caught:
            self.call(actor, method, *args)
        self.assertEqual(caught.exception.status, status, caught.exception.payload)

    def own(self):
        return self.call("employee", "get_own_feedback", self.feedback["id"])

    def test_owner_correction_preserves_original_history_and_menu_association(self):
        original = self.own()
        key = uid()
        data = {"expected_version": original["expected_version"], "feedback_text": "本人更正后的工程意见", "dish": "更正对应菜品"}
        corrected = self.call("employee", "update_feedback", original["id"], data, key)
        self.assertTrue(corrected["shared"])
        self.assertEqual(corrected["publication_id"], self.publication["id"])
        self.assertEqual(corrected["publication_version"], 1)
        self.assertEqual(corrected["dietary_constraint"], self.original_data["dietary_constraint"])
        self.assertEqual(corrected, self.call("employee", "update_feedback", original["id"], data, key))
        projection = self.call("cook", "list_feedback", self.publication["id"])["items"][0]
        self.assertEqual(projection["feedback_text"], data["feedback_text"])
        self.assertTrue(projection["corrected_by_self"])
        self.assertEqual(projection["correction_count"], 1)
        self.assertIsNotNone(projection["corrected_at"])
        self.assertNotIn(self.original_data["feedback_text"], json.dumps(projection, ensure_ascii=False))
        self.assertNotIn("sources", projection)
        with self.store.connect() as connection:
            revisions = [json.loads(row[0]) for row in connection.execute("SELECT fields FROM revisions WHERE matter_id=? ORDER BY revision_no", (original["id"],))]
            self.assertEqual([facts["feedback_text"]["value"] for facts in revisions], [self.original_data["feedback_text"], data["feedback_text"]])
            self.assertTrue(all(facts["menu_ref"]["value"] == self.publication["id"] + "/v1" for facts in revisions))
            sources = connection.execute("SELECT text,recorded_by FROM sources WHERE matter_id=? ORDER BY recorded_at,rowid", (original["id"],)).fetchall()
            self.assertEqual([row[0] for row in sources], [self.original_data["feedback_text"], data["feedback_text"]])
            self.assertTrue(all(row[1] == self.actors["employee"]["id"] for row in sources))
        with self.store.as_actor(self.actors["cook"]):
            latest = self.store.detail(self.menu_matter["id"])
            new_publication = self.menu.publish(latest["id"], {"expected_version": latest["version"], "menu_date": "2026-10-10", "meal_slot": "晚餐", "dishes": "新版工程菜单", "notes": ""}, uid())
        self.assertEqual(self.call("cook", "list_feedback", new_publication["id"])["items"], [])
        self.assertEqual(self.own()["publication_id"], self.publication["id"])

    def test_other_accounts_epoch_stale_version_and_forged_actor_are_rejected(self):
        own = self.own()
        data = {"expected_version": own["expected_version"], "feedback_text": "尝试更正"}
        for method, args in (("get_own_feedback", (own["id"],)), ("update_feedback", (own["id"], data, uid())),
                             ("withdraw_feedback", (own["id"], {"expected_version": own["expected_version"]}, uid()))):
            self.error(404, "other", method, *args)
            self.error(403, "cook", method, *args)
            self.error(403, "admin", method, *args)
        self.error(422, "employee", "update_feedback", own["id"], {**data, "actor_id": self.actors["other"]["id"]}, uid())
        self.error(422, "employee", "update_feedback", own["id"], {**data, "publication_id": "other-menu"}, uid())
        self.error(409, "employee", "update_feedback", own["id"], {**data, "expected_version": own["expected_version"] + 1}, uid())
        with self.store.transaction() as connection:
            connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (self.actors["employee"]["id"],))
        self.error(401, "employee", "get_own_feedback", own["id"])
        self.error(401, "employee", "update_feedback", own["id"], data, uid())

    def test_withdraw_removes_cook_projection_and_replay_or_private_edit_never_reshare(self):
        own = self.own()
        update_key = uid()
        data = {"expected_version": own["expected_version"], "feedback_text": "撤共享前本人更正"}
        corrected = self.call("employee", "update_feedback", own["id"], data, update_key)
        withdraw_key = uid()
        withdraw_data = {"expected_version": corrected["expected_version"]}
        withdrawn = self.call("employee", "withdraw_feedback", own["id"], withdraw_data, withdraw_key)
        self.assertFalse(withdrawn["shared"])
        self.assertEqual(self.call("cook", "list_feedback", self.publication["id"])["items"], [])
        replay = self.call("employee", "update_feedback", own["id"], data, update_key)
        self.assertFalse(replay["shared_with_publisher"])
        self.assertEqual(replay["expected_version"], withdrawn["expected_version"])
        old_submission = self.call("employee", "feedback", self.publication["id"], self.original_data, self.create_key)
        self.assertFalse(old_submission["shared_with_publisher"])
        self.assertEqual(self.call("employee", "withdraw_feedback", own["id"], withdraw_data, withdraw_key), withdrawn)
        private = self.call("employee", "update_feedback", own["id"], {"expected_version": withdrawn["expected_version"], "feedback_text": "撤共享后的仅本人私有更正"}, uid())
        self.assertFalse(private["shared"])
        self.assertIn("私有", private["scope"])
        self.assertEqual(self.call("cook", "list_feedback", self.publication["id"])["feedback_count"], 0)
        with self.store.as_actor(self.actors["employee"]):
            detail = self.store.detail(own["id"])
            self.assertEqual(detail["facts"]["feedback_text"]["value"], private["feedback_text"])
            self.assertEqual(len(detail["sources"]), 3)
            self.assertEqual(sum(event["event_type"] == "feedback_shared_withdrawn" for event in detail["activities"]), 1)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT shared FROM menu_feedback WHERE matter_id=?", (own["id"],)).fetchone()[0], 0)

    def test_menu_withdrawal_and_publisher_disable_do_not_block_owner_withdrawal(self):
        self.call("cook", "withdraw", self.publication["id"], {"expected_version": 1}, uid())
        with self.store.transaction() as connection:
            connection.execute("UPDATE accounts SET state='disabled',auth_epoch=auth_epoch+1 WHERE id=?", (self.actors["cook"]["id"],))
        own = self.own()
        withdrawn = self.call("employee", "withdraw_feedback", own["id"], {"expected_version": own["expected_version"]}, uid())
        self.assertFalse(withdrawn["shared"])
        with self.store.as_actor(self.actors["employee"]):
            detail = self.store.detail(own["id"])
            self.assertEqual(detail["facts"]["menu_ref"]["value"], self.publication["id"] + "/v1")
            self.assertEqual(detail["sources"][0]["text"], self.original_data["feedback_text"])
        with self.store.connect() as connection:
            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())


if __name__ == "__main__":
    unittest.main()
