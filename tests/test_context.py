"""One private preference smoke with logical deletion and preserved business history."""

import json
import tempfile
import unittest
from pathlib import Path

from assistant_app.context import ContextService
from assistant_app.store import AppError, Store, dump, now, uid


class ContextSmoke(unittest.TestCase):
    def test_both_modes_delete_replay_privacy_and_business_preservation(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-context-", dir=Path(__file__).resolve().parent) as directory:
                store = Store(directory)
                service = ContextService(store)
                actors = {"owner": None, "other": None, "admin": None, "executor": None}
                if protected:
                    store.enable_identity()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            caps = [] if name == "admin" else ["executor"] if name == "executor" else ["employee"]
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,is_admin,created_at,created_by) VALUES(?,?,?,'active',?,?,0,'fixture')", (actor_id, name, "隔离 " + name, dump(caps), int(name == "admin")))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}

                def call(method, *args, actor="owner"):
                    with store.as_actor(actors[actor]):
                        return getattr(service, method)(*args)

                def error(status, method, *args, actor="owner"):
                    with self.assertRaises(AppError) as caught:
                        call(method, *args, actor=actor)
                    self.assertEqual(caught.exception.status, status)

                old_text = "隔离偏好旧正文：标题使用简短名词"
                note = {"id": uid(), "text": old_text}
                original_data, original_key = {"expected_version": 0, "upserts": [note], "removed_ids": []}, uid()
                self.assertEqual(call("get_context")["notes"], [])
                current = call("save_context", original_data, original_key)
                self.assertEqual(current["notes"][0]["origin"], "本人明确输入")
                self.assertEqual(call("save_context", original_data, original_key)["version"], 1)
                error(409, "save_context", original_data, uid())
                second = {"id": uid(), "text": "另一条：日期用ISO格式"}
                current = call("save_context", {"expected_version": current["version"], "upserts": [second], "removed_ids": []}, uid())
                self.assertEqual(len(current["notes"]), 2)
                first_stamp = current["notes"][0]["recorded_at"]
                current = call("save_context", {"expected_version": current["version"], "upserts": [{**note, "text": "本人更正后的偏好"}], "removed_ids": []}, uid())
                self.assertEqual(current["notes"][0]["recorded_at"], first_stamp)
                # The user explicitly copied the original text into a business source.
                with store.as_actor(actors["owner"]):
                    matter = store.mutate("create", None, {"goal_text": "隔离业务历史", "domain": "general"}, uid())
                    matter = store.mutate("source", matter["id"], {"expected_version": matter["version"], "kind": "user_text", "text": old_text}, uid())
                    manual_id = uid()
                    with store.transaction() as connection:
                        connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (manual_id, matter["id"], "general", "人工稿", "人工正文保留", "human_saved", matter["revision_no"], now()))
                current = call("save_context", {"expected_version": current["version"], "upserts": [], "removed_ids": [note["id"]]}, uid())
                replay = call("save_context", original_data, original_key)
                self.assertEqual(replay, current)
                self.assertEqual([item["id"] for item in replay["notes"]], [second["id"]])
                with store.connect() as connection:
                    payloads = connection.execute("SELECT kind,response FROM actions WHERE kind='personal_context' OR kind='request:structured_personal_context_save'").fetchall()
                    self.assertNotIn(old_text, "".join(row["response"] for row in payloads))
                    self.assertNotIn("本人更正后的偏好", "".join(row["response"] for row in payloads))
                    for row in payloads:
                        if row["kind"] != "personal_context":
                            self.assertEqual(set(json.loads(row["response"])), {"context_version"})
                with store.as_actor(actors["owner"]):
                    retained = store.detail(matter["id"])
                    self.assertEqual(retained["revision_no"], matter["revision_no"])
                    self.assertEqual(retained["sources"], matter["sources"])
                    self.assertIn(old_text, [source["text"] for source in retained["sources"]])
                    self.assertEqual(store.download(manual_id, 1), "人工正文保留")
                restarted = ContextService(Store(directory))
                with restarted.store.as_actor(actors["owner"]):
                    self.assertEqual(restarted.get_context(), current)
                if protected:
                    self.assertEqual(call("get_context", actor="other")["notes"], [])
                    error(409, "save_context", original_data, original_key, actor="other")
                    error(403, "get_context", actor="admin")
                    error(403, "get_context", actor="executor")
                    with store.transaction() as connection:
                        connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (actors["owner"]["id"],))
                    error(401, "save_context", original_data, original_key)
                    store.requires_auth = False
                    error(401, "get_context")
                else:
                    with store.connect() as connection:
                        self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='accounts'").fetchone())


if __name__ == "__main__":
    unittest.main()
