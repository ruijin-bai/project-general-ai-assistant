"""One Store smoke for scoped cook responses, correction and withdrawal."""

import json
import tempfile
import unittest
from pathlib import Path

from assistant_app.menu import MENU_SCHEMA, MenuService
from assistant_app.store import AppError, Store, dump, uid


class MenuReplySmoke(unittest.TestCase):
    def test_real_cook_statement_correction_and_revoked_replay(self):
        with tempfile.TemporaryDirectory(prefix="qa-menu-reply-", dir=Path(__file__).resolve().parent) as directory:
            store = Store(directory)
            store.enable_identity(MENU_SCHEMA)
            menu = MenuService(store)
            actors = {name: {"id": uid(), "auth_epoch": 1} for name in ("cook", "other_cook", "employee", "clerk")}
            with store.transaction() as connection:
                for name, actor in actors.items():
                    caps = ["cook"] if "cook" in name else [name]
                    connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active',?,0,'fixture')", (actor["id"], name, "隔离 " + name, dump(caps)))

            def call(actor, method, *args):
                with store.as_actor(actors[actor]):
                    return getattr(menu, method)(*args)

            def error(status, actor, method, *args):
                with self.assertRaises(AppError) as caught:
                    call(actor, method, *args)
                self.assertEqual(caught.exception.status, status)

            with store.as_actor(actors["cook"]):
                matter = store.mutate("create", None, {"goal_text": "隔离菜单", "domain": "menu"}, uid())
            publication = call("cook", "publish", matter["id"], {"expected_version": matter["version"], "menu_date": "2026-10-10", "meal_slot": "午餐", "dishes": "工程夹具菜品", "notes": ""}, uid())
            feedback = call("employee", "feedback", publication["id"], {"version": 1, "feedback_text": "本人自愿原意见"}, uid())
            data = {"expected_publication_version": 1, "feedback_correction_count": 0, "response_text": "收到意见，后续决定另核。"}
            key = uid()
            error(404, "other_cook", "respond_feedback", publication["id"], feedback["id"], data, key)
            error(403, "clerk", "respond_feedback", publication["id"], feedback["id"], data, key)
            error(422, "cook", "respond_feedback", publication["id"], feedback["id"], {**data, "actor_id": actors["employee"]["id"]}, uid())
            error(409, "cook", "respond_feedback", publication["id"], feedback["id"], {**data, "feedback_correction_count": 1}, uid())
            with store.as_actor(actors["employee"]):
                original = store.detail(feedback["id"])
            response = call("cook", "respond_feedback", publication["id"], feedback["id"], data, key)
            self.assertEqual(call("cook", "respond_feedback", publication["id"], feedback["id"], data, key), response)
            self.assertFalse(response["old_feedback_scope"])
            self.assertIn("不代表采纳", response["scope"])
            own = call("employee", "get_own_feedback", feedback["id"])
            self.assertEqual(own["responses"], [response])
            cook_view = call("cook", "list_feedback", publication["id"])["items"][0]
            self.assertEqual(cook_view["responses"], [response])
            self.assertNotIn("sources", response)
            with store.as_actor(actors["employee"]):
                current = store.detail(feedback["id"])
            self.assertEqual(current["revision_no"], original["revision_no"])
            self.assertEqual(current["sources"], original["sources"])
            self.assertEqual(current["facts"], original["facts"])
            self.assertEqual(current["version"], original["version"] + 1)
            with store.connect() as connection:
                record = connection.execute("SELECT recorded_by,message FROM events WHERE id=?", (response["id"],)).fetchone()
                self.assertEqual(record[0], actors["cook"]["id"])
                self.assertEqual(json.loads(record[1])["actor_id"], actors["cook"]["id"])
            corrected = call("employee", "update_feedback", feedback["id"], {"expected_version": own["expected_version"], "feedback_text": "本人更正后的原意见"}, uid())
            self.assertTrue(corrected["responses"][0]["old_feedback_scope"])
            self.assertTrue(call("cook", "list_feedback", publication["id"])["items"][0]["responses"][0]["old_feedback_scope"])
            error(409, "cook", "respond_feedback", publication["id"], feedback["id"], data, key)
            revised_response_data = {**data, "feedback_correction_count": 1, "response_text": "回应此次更正意见。"}
            second_key = uid()
            call("cook", "respond_feedback", publication["id"], feedback["id"], revised_response_data, second_key)
            own = call("employee", "get_own_feedback", feedback["id"])
            call("employee", "withdraw_feedback", feedback["id"], {"expected_version": own["expected_version"]}, uid())
            error(404, "cook", "respond_feedback", publication["id"], feedback["id"], revised_response_data, second_key)
            error(404, "cook", "respond_feedback", publication["id"], feedback["id"], revised_response_data, uid())
            self.assertEqual(call("cook", "list_feedback", publication["id"])["items"], [])
            self.assertEqual(len(call("employee", "get_own_feedback", feedback["id"])["responses"]), 2)
            call("cook", "withdraw", publication["id"], {"expected_version": 1}, uid())
            error(404, "cook", "respond_feedback", publication["id"], feedback["id"], revised_response_data, second_key)
            with store.transaction() as connection:
                connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (actors["cook"]["id"],))
            error(401, "cook", "respond_feedback", publication["id"], feedback["id"], revised_response_data, second_key)


if __name__ == "__main__":
    unittest.main()
