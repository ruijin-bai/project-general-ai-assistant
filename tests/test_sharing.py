"""One real Store migration/grant smoke using isolated account fixtures."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from assistant_app.sharing import SHARING_SCHEMA, ShareService
from assistant_app.store import AppError, Store, now, uid


class SharingSmoke(unittest.TestCase):
    def test_migration_snapshot_projection_and_revocation(self):
        with tempfile.TemporaryDirectory(prefix="qa-sharing-", dir=Path(__file__).resolve().parent) as directory:
            store = Store(directory)
            store.enable_identity()
            store.enable_services()
            actors = {name: {"id": uid(), "auth_epoch": 1} for name in ("owner", "recipient", "other")}
            with store.transaction() as connection:
                for name, actor in actors.items():
                    connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active',?,0,'fixture')", (actor["id"], name, "隔离 " + name, json.dumps(["clerk" if name == "recipient" else "employee"])))
            with store.as_actor(actors["owner"]):
                matter = store.mutate("create", None, {"domain": "general", "goal_text": "私有目标绝不自动共享"}, uid())
                matter = store.mutate("source", matter["id"], {"expected_version": matter["version"], "kind": "oral_note", "text": "私有原文和联系方式不要发给接收人"}, uid())
                artifact_id = uid()
                with store.transaction() as connection:
                    connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (artifact_id, matter["id"], "general", "人工原稿", "私有人工原稿正文", "human_saved", matter["revision_no"], now()))
            store.enable_sharing(SHARING_SCHEMA)
            self.assertTrue(list((Path(directory) / "backups").glob("schema-v4-*.sqlite3")))
            with store.connect() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
                self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())
            with closing(sqlite3.connect(next((Path(directory) / "backups").glob("schema-v4-*.sqlite3")))) as backup:
                self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual(backup.execute("SELECT content FROM artifacts WHERE id=?", (artifact_id,)).fetchone()[0], "私有人工原稿正文")
            service = ShareService(store)

            def call(method, *args, actor="owner"):
                with store.as_actor(actors[actor]):
                    return getattr(service, method)(*args)

            def denied(method, *args, actor="recipient"):
                with self.assertRaises(AppError) as caught:
                    call(method, *args, actor=actor)
                self.assertEqual(caught.exception.status, 404)

            def share(version=1):
                with store.as_actor(actors["owner"]):
                    current = store.detail(matter["id"])
                data = {"expected_version": current["version"], "artifact_id": artifact_id, "artifact_version": version, "recipient_id": actors["recipient"]["id"], "title": "明确脱敏的独立准备", "content": "仅分享这段必要准备文字", "dependency_refs": [matter["sources"][-1]["id"]]}
                key = uid()
                return call("create_share", matter["id"], data, key), data, key

            self.assertEqual(call("eligible_recipients"), [{"id": actors["recipient"]["id"], "display_name": "隔离 recipient"}])
            grant, data, key = share()
            received = call("get_share", grant["id"], actor="recipient")
            self.assertEqual(set(received), {"id", "version", "title", "content", "owner_display_name", "created_at", "input_revision", "stale", "scope"})
            self.assertEqual(received["content"], "仅分享这段必要准备文字")
            self.assertEqual(call("download_share", grant["id"], 1, actor="recipient"), received["content"])
            self.assertEqual(len(call("list_received", actor="recipient")["items"]), 1)
            denied("get_share", grant["id"], actor="other")
            denied("create_share", matter["id"], data, key, actor="recipient")
            with store.as_actor(actors["recipient"]), self.assertRaises(AppError) as caught:
                store.detail(matter["id"])
            self.assertEqual(caught.exception.status, 404)
            with store.as_actor(actors["owner"]):
                current = store.detail(matter["id"])
                changed = store.mutate("source", matter["id"], {"expected_version": current["version"], "kind": "oral_note", "text": "私有补充，不跟随公开"}, uid())
                with store.transaction() as connection:
                    connection.execute("INSERT INTO artifacts VALUES(?,2,?,?,?,?,?,?,?)", (artifact_id, matter["id"], "general", "人工修改稿", "后续人工修改仍私有", "human_saved", changed["revision_no"], now()))
            self.assertTrue(call("get_share", grant["id"], actor="recipient")["stale"])
            self.assertEqual(call("get_share", grant["id"], actor="recipient")["content"], received["content"])
            withdrawn = call("withdraw_share", grant["id"], {"expected_version": 1}, uid())
            self.assertEqual(withdrawn["status"], "withdrawn")
            self.assertEqual(call("create_share", matter["id"], data, key)["status"], "withdrawn")
            denied("get_share", grant["id"])
            denied("download_share", grant["id"], 1)
            self.assertEqual(call("list_received", actor="recipient")["items"], [])
            second, _, _ = share(2)
            with store.transaction() as connection:
                connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (actors["recipient"]["id"],))
            actors["recipient"]["auth_epoch"] = 2
            denied("get_share", second["id"])
            denied("download_share", second["id"], 1)
            call("withdraw_share", second["id"], {"expected_version": 1}, uid())
            third, _, _ = share(2)
            with store.transaction() as connection:
                connection.execute("UPDATE accounts SET state='disabled',auth_epoch=auth_epoch+1 WHERE id=?", (actors["owner"]["id"],))
            denied("get_share", third["id"])
            self.assertEqual(call("list_received", actor="recipient")["items"], [])
            with store.transaction() as connection:
                connection.execute("UPDATE accounts SET state='active' WHERE id=?", (actors["owner"]["id"],))
            actors["owner"]["auth_epoch"] = 2
            denied("get_share", third["id"])
            with store.as_actor(actors["owner"]):
                self.assertEqual(store.download(artifact_id, 1), "私有人工原稿正文")
                self.assertEqual(store.download(artifact_id, 2), "后续人工修改仍私有")
            self.assertEqual(len(call("list_owned", matter["id"])["items"]), 3)
            with store.connect() as connection:
                self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())


if __name__ == "__main__":
    unittest.main()
