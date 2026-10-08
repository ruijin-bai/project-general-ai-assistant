"""One real Store smoke of private local quiet preferences and preservation."""

import json
import tempfile
import unittest
from pathlib import Path

from assistant_app.reminders import ReminderPreferencesService
from assistant_app.store import AppError, Store, dump, now, uid


class ReminderPreferencesSmoke(unittest.TestCase):
    def test_two_modes_quiet_replay_versions_and_unchanged_business(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-reminders-", dir=Path(__file__).resolve().parent) as directory:
                store, actors = Store(directory), {"owner": None, "other": None, "admin": None, "executor": None}
                if protected:
                    store.enable_identity()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            caps = [] if name == "admin" else ["executor"] if name == "executor" else ["employee"]
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,is_admin,created_at,created_by) VALUES(?,?,?,'active',?,?,0,'fixture')", (actor_id, name, "隔离 " + name, dump(caps), int(name == "admin")))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}
                service = ReminderPreferencesService(store)

                def call(method, *args, actor="owner"):
                    with store.as_actor(actors[actor]):
                        return getattr(service, method)(*args)

                def error(status, data, actor="owner", key=None):
                    with self.assertRaises(AppError) as caught:
                        call("save_preferences", data, key or uid(), actor=actor)
                    self.assertEqual(caught.exception.status, status)

                with store.as_actor(actors["owner"]):
                    matter = store.mutate("create", None, {"goal_text": "隔离提醒保全", "domain": "general"}, uid())
                    matter = store.mutate("source", matter["id"], {"expected_version": matter["version"], "kind": "user_text", "text": "正式事项原文保持"}, uid())
                    matter = store.mutate("control", matter["id"], {"expected_version": matter["version"], "command": "pause"}, uid())
                    manual_id = uid()
                    with store.transaction() as connection:
                        connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (manual_id, matter["id"], "general", "人工稿", "人工正文保留", "human_saved", matter["revision_no"], now()))
                    before = store.detail(matter["id"])
                initial = call("get_preferences")
                self.assertEqual((initial["version"], initial["mode"], initial["quiet_until"], initial["quiet_active"]), (0, "normal", None, False))
                old_data, old_key = {"expected_version": 0, "mode": "quiet", "quiet_until": None}, uid()
                indefinite = call("save_preferences", old_data, old_key)
                self.assertTrue(indefinite["quiet_active"])
                error(409, old_data)
                future_string = "2999-01-01T00:00:00+01:00"
                future = call("save_preferences", {"expected_version": 1, "mode": "quiet", "quiet_until": future_string}, uid())
                self.assertEqual(future["quiet_until"], future_string)
                self.assertTrue(future["quiet_active"])
                expired = call("save_preferences", {"expected_version": 2, "mode": "quiet", "quiet_until": "2000-01-01T00:00:00+00:00"}, uid())
                self.assertFalse(expired["quiet_active"])
                normal = call("save_preferences", {"expected_version": 3, "mode": "normal", "quiet_until": None}, uid())
                self.assertFalse(normal["quiet_active"])
                self.assertEqual(call("save_preferences", old_data, old_key), normal)
                for bad in ({"expected_version": True, "mode": "quiet", "quiet_until": None},
                            {"expected_version": "4", "mode": "quiet", "quiet_until": None},
                            {"expected_version": 4, "mode": {}, "quiet_until": None},
                            {"expected_version": 4, "mode": "normal", "quiet_until": future_string},
                            {"expected_version": 4, "mode": "quiet", "quiet_until": True},
                            {"expected_version": 4, "mode": "quiet", "quiet_until": "2026-10-08T08:00:00"},
                            {"expected_version": 4, "mode": "quiet", "quiet_until": None, "actor_id": "forged"}):
                    error(422, bad)
                self.assertEqual(call("get_preferences"), normal)
                with store.connect() as connection:
                    rows = connection.execute("SELECT matter_id,response FROM actions WHERE kind='reminder_preferences'").fetchall()
                    self.assertEqual(len(rows), 1)
                    self.assertIsNone(rows[0]["matter_id"])
                    self.assertEqual(set(json.loads(rows[0]["response"])), {"version", "mode", "quiet_until"})
                with store.as_actor(actors["owner"]):
                    self.assertEqual(store.detail(matter["id"]), before)
                    self.assertEqual(store.download(manual_id, 1), "人工正文保留")
                restarted = ReminderPreferencesService(Store(directory))
                with restarted.store.as_actor(actors["owner"]):
                    self.assertEqual(restarted.get_preferences(), normal)
                if protected:
                    self.assertEqual(call("get_preferences", actor="other")["version"], 0)
                    for denied in ("admin", "executor"):
                        error(403, old_data, actor=denied, key=old_key)
                    error(409, old_data, actor="other", key=old_key)
                    with store.transaction() as connection:
                        connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (actors["owner"]["id"],))
                    error(401, old_data, key=old_key)


if __name__ == "__main__":
    unittest.main()
