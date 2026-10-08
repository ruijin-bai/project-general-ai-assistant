"""One combined Store smoke for local waiting edits and private replay guards."""

import tempfile
import unittest
from pathlib import Path

from assistant_app.store import AppError, Store, now, uid
from assistant_app.waiting import WaitingService


class WaitingSmoke(unittest.TestCase):
    def test_incremental_reminders_replay_and_preservation_in_both_modes(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-waiting-", dir=Path(__file__).resolve().parent) as directory:
                store = Store(directory)
                service = WaitingService(store)
                actors = {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'fixture')", (actor_id, name, "隔离 " + name))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}

                def call(method, *args, actor="owner"):
                    with store.as_actor(actors[actor]):
                        return getattr(service, method)(*args)

                def error(status, method, *args, actor="owner"):
                    with self.assertRaises(AppError) as caught:
                        call(method, *args, actor=actor)
                    self.assertEqual(caught.exception.status, status)

                with store.as_actor(actors["owner"]):
                    detail = store.mutate("create", None, {"goal_text": "隔离等待准备", "domain": "general"}, uid())
                    detail = store.mutate("source", detail["id"], {"expected_version": detail["version"], "kind": "oral_note", "text": "既有原始资料保留"}, uid())
                    artifact_id = uid()
                    with store.transaction() as connection:
                        connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (artifact_id, detail["id"], "general", "人工原稿", "原文不能覆盖", "human_saved", detail["revision_no"], now()))
                first = {"id": uid(), "who": "本人注明的原渠道对象", "reason": "具体口径缺失", "next_step": "沿原渠道核对", "due_at": "2000-01-01T12:00:00+01:00", "state": "waiting", "note": "未称真实受理"}
                second = {**first, "id": uid(), "who": "另一注明对象", "due_at": "9999-01-01T12:00:00+01:00"}
                original_data = {"expected_version": detail["version"], "upserts": [first], "removed_ids": []}
                key = uid()
                for invalid in ("2026-10-10T12:00:00", "明天", "2026-10-10"):
                    error(422, "save_waiting", detail["id"], {**original_data, "upserts": [{**first, "due_at": invalid}]}, uid())
                saved = call("save_waiting", detail["id"], original_data, key)
                self.assertTrue(saved["items"][0]["due_now"])
                error(409, "save_waiting", detail["id"], original_data, uid())
                saved = call("save_waiting", detail["id"], {"expected_version": saved["version"], "upserts": [second], "removed_ids": []}, uid())
                self.assertEqual(len(saved["items"]), 2)
                self.assertFalse(saved["items"][1]["due_now"])
                saved = call("save_waiting", detail["id"], {"expected_version": saved["version"], "upserts": [{**first, "due_at": None, "note": "取消本地提醒，原等待继续"}], "removed_ids": []}, uid())
                self.assertIsNone(saved["items"][0]["due_now"])
                self.assertEqual(saved["items"][1]["who"], second["who"])
                saved = call("save_waiting", detail["id"], {"expected_version": saved["version"], "upserts": [], "removed_ids": [first["id"]]}, uid())
                replay = call("save_waiting", detail["id"], original_data, key)
                self.assertEqual(replay["version"], saved["version"])
                self.assertFalse(replay["items"][0]["active"])
                self.assertIn("取消本地提醒", replay["items"][0]["note"])
                if protected:
                    error(404, "get_waiting", detail["id"], actor="other")
                    error(404, "save_waiting", detail["id"], original_data, key, actor="other")
                with store.as_actor(actors["owner"]):
                    paused = store.mutate("control", detail["id"], {"expected_version": saved["version"], "command": "pause"}, uid())
                saved = call("save_waiting", detail["id"], {"expected_version": paused["version"], "upserts": [{**second, "state": "resolved"}], "removed_ids": []}, uid())
                self.assertFalse(saved["items"][1]["due_now"])
                with store.as_actor(actors["owner"]):
                    current = store.detail(detail["id"])
                    self.assertEqual(current["assistant_status"], "paused")
                    self.assertEqual(current["revision_no"], detail["revision_no"])
                    self.assertEqual(current["facts"], detail["facts"])
                    self.assertEqual(current["sources"], detail["sources"])
                    self.assertEqual(store.download(artifact_id, 1), "原文不能覆盖")
                reopened = WaitingService(Store(directory))
                with reopened.store.as_actor(actors["owner"]):
                    restored = reopened.get_waiting(detail["id"])
                    self.assertEqual(restored["items"], saved["items"])
                with store.connect() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM actions WHERE matter_id=? AND kind IN ('prepare','prepare_model')", (detail["id"],)).fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE matter_id=? AND event_type='waiting_changed'", (detail["id"],)).fetchone()[0], 5)
                    if not protected:
                        self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='accounts'").fetchone())


if __name__ == "__main__":
    unittest.main()
