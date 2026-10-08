"""One combined local Store smoke for unparsed originals and transactional safety."""

import base64
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from assistant_app.attachments import AttachmentService, FILE_LIMIT, TABLE
from assistant_app.sharing import SHARING_SCHEMA
from assistant_app.store import AppError, Store, dump, now, uid


class AttachmentSmoke(unittest.TestCase):
    def test_two_versions_preservation_backup_rollback_limits_privacy_and_replay(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-attachments-", dir=Path(__file__).resolve().parent) as directory:
                store, actors = Store(directory), {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    store.enable_services()
                    store.enable_sharing(SHARING_SCHEMA)
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'fixture')", (actor_id, name, "隔离 " + name))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}
                service = AttachmentService(store)

                def call(method, *args, actor="owner"):
                    with store.as_actor(actors[actor]):
                        return getattr(service, method)(*args)

                def error(status, method, *args, actor="owner"):
                    with self.assertRaises(AppError) as caught:
                        call(method, *args, actor=actor)
                    self.assertEqual(caught.exception.status, status)

                with store.as_actor(actors["owner"]):
                    matter = store.mutate("create", None, {"goal_text": "本地原件隔离夹具", "domain": "general"}, uid())
                    matter = store.mutate("control", matter["id"], {"expected_version": matter["version"], "command": "pause"}, uid())
                    manual_id = uid()
                    with store.transaction() as connection:
                        raw = store._stored_facts(connection, matter)
                        raw.update(__workflow={"enabled": True, "mode": "template"}, __manual_note="本人元数据不能消失")
                        connection.execute("UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?", (dump(raw), matter["id"], matter["revision_no"]))
                        original_fields = connection.execute("SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?", (matter["id"], matter["revision_no"])).fetchone()[0]
                        connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (manual_id, matter["id"], "general", "人工稿", "人工正文保持", "human_saved", matter["revision_no"], now()))
                        if protected:
                            connection.execute("UPDATE matters SET assistant_status='processing',preparation_status='ready' WHERE id=?", (matter["id"],))
                            connection.execute("INSERT INTO actions(id,matter_id,kind,status,input_revision,control_epoch,created_at) VALUES(?,?,'prepare','running',?,?,?)", (uid(), matter["id"], matter["revision_no"], matter["control_epoch"], now()))
                    before = store.detail(matter["id"])
                version = 5 if protected else 2
                self.assertEqual(call("get_attachments", matter["id"])["attachments"], [])
                with store.connect() as connection:
                    self.assertFalse(connection.execute("SELECT name FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())
                payload = b"Unparsed fixture bytes\x00\xff: not a readable source"
                encoded = base64.b64encode(payload).decode("ascii")
                data = {"expected_version": before["version"], "filename": "隔离原件.PDF", "content_base64": encoded}
                old_key = uid()
                for invalid in ({**data, "filename": "../bad.pdf"}, {**data, "filename": "x\x00.pdf"},
                                {**data, "filename": "unsupported.exe"}, {**data, "content_base64": "abcd\n"},
                                {**data, "content_base64": "Zh=="}, {**data, "content_base64": True},
                                {**data, "expected_version": True}, {**data, "actor": "forged"}):
                    error(422, "upload", matter["id"], invalid, uid())
                error(413, "upload", matter["id"], {**data, "content_base64": base64.b64encode(b"x" * (FILE_LIMIT + 1)).decode("ascii")}, uid())
                self.assertEqual(list((Path(directory) / "backups").glob("before-attachments-*")), [])
                # An injected first-upload event failure rolls back DDL, BLOB, revision and request.
                with patch.object(store, "_event", side_effect=RuntimeError("isolated rollback fixture")):
                    with self.assertRaises(RuntimeError):
                        call("upload", matter["id"], data, old_key)
                with store.connect() as connection:
                    self.assertFalse(connection.execute("SELECT name FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())
                    self.assertFalse(connection.execute("SELECT id FROM actions WHERE idempotency_key=?", (old_key,)).fetchone())
                with store.as_actor(actors["owner"]):
                    self.assertEqual(store.detail(matter["id"]), before)
                saved = call("upload", matter["id"], data, old_key)
                attachment = saved["attachments"][0]
                self.assertEqual((saved["version"], saved["input_revision"]), (before["version"] + 1, before["revision_no"] + 1))
                self.assertEqual(attachment["declared_format"], "pdf")
                self.assertEqual(attachment["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual(call("upload", matter["id"], data, old_key), saved)
                downloaded = call("download", matter["id"], attachment["id"])
                self.assertEqual(downloaded["content"], payload)
                self.assertEqual(downloaded["filename"], data["filename"])
                error(409, "upload", matter["id"], data, uid())
                with store.as_actor(actors["owner"]):
                    after = store.detail(matter["id"])
                    self.assertEqual(after["assistant_status"], "waiting" if protected else "paused")
                    self.assertEqual(after["preparation_status"], "needs_review")
                    self.assertEqual(after["sources"], before["sources"])
                    self.assertEqual(store.download(manual_id, 1), "人工正文保持")
                with store.connect() as connection:
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], version)
                    self.assertFalse(connection.execute("PRAGMA foreign_key_check").fetchall())
                    if protected:
                        preparation = connection.execute("SELECT status FROM actions WHERE matter_id=? AND kind='prepare'", (matter["id"],)).fetchall()
                        self.assertEqual([row[0] for row in preparation], ["cancelled"])
                    self.assertEqual(connection.execute("SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?", (matter["id"], saved["input_revision"])).fetchone()[0], original_fields)
                    self.assertNotIn(encoded, "".join(row[0] or "" for row in connection.execute("SELECT response FROM actions")))
                    self.assertNotIn(encoded, "".join(row[0] for row in connection.execute("SELECT message FROM events")))
                backups = list((Path(directory) / "backups").glob("before-attachments-*"))
                self.assertTrue(backups)
                for path in backups:
                    with closing(sqlite3.connect(path)) as backup:
                        self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], version)
                        self.assertIsNone(backup.execute("SELECT name FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())
                        self.assertEqual(backup.execute("SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?", (matter["id"], before["revision_no"])).fetchone()[0], original_fields)
                other_matter = None
                with store.as_actor(actors["owner"]):
                    other_matter = store.mutate("create", None, {"goal_text": "另一事项原件隔离", "domain": "general"}, uid())
                error(404, "download", other_matter["id"], attachment["id"])
                # Limits count actual stored bytes; only fixture records are enlarged to the boundary.
                with store.transaction() as connection:
                    connection.execute("UPDATE matter_attachments_v1 SET content=?,byte_count=?,sha256=? WHERE id=?", (sqlite3.Binary(b"x" * FILE_LIMIT), FILE_LIMIT, hashlib.sha256(b"x" * FILE_LIMIT).hexdigest(), attachment["id"]))
                    for number in range(9):
                        connection.execute("INSERT INTO matter_attachments_v1 VALUES(?,?,?,?,?,?,?,?,?)", (uid(), matter["id"], "limit" + str(number) + ".txt", "txt", FILE_LIMIT, hashlib.sha256(b"x" * FILE_LIMIT).hexdigest(), attachment["uploaded_by"], now(), sqlite3.Binary(b"x" * FILE_LIMIT)))
                error(413, "upload", matter["id"], {**data, "expected_version": saved["version"]}, uid())
                with store.transaction() as connection:
                    connection.execute("DELETE FROM matter_attachments_v1 WHERE id!=?", (attachment["id"],))
                    connection.execute("UPDATE matter_attachments_v1 SET content=?,byte_count=?,sha256=? WHERE id=?", (sqlite3.Binary(payload), len(payload), hashlib.sha256(payload).hexdigest(), attachment["id"]))
                restarted = AttachmentService(Store(directory))
                with restarted.store.as_actor(actors["owner"]):
                    self.assertEqual(restarted.download(matter["id"], attachment["id"])["content"], payload)
                with store.transaction() as connection:
                    connection.execute("ALTER TABLE matter_attachments_v1 ADD COLUMN incompatible TEXT")
                error(409, "upload", matter["id"], {**data, "expected_version": saved["version"]}, uid())
                with store.transaction() as connection:
                    connection.execute("ALTER TABLE matter_attachments_v1 DROP COLUMN incompatible")
                if protected:
                    error(404, "get_attachments", matter["id"], actor="other")
                    error(404, "download", matter["id"], attachment["id"], actor="other")
                    error(404, "upload", matter["id"], data, old_key, actor="other")
                    with store.transaction() as connection:
                        connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (actors["owner"]["id"],))
                    error(401, "download", matter["id"], attachment["id"])
                    error(401, "upload", matter["id"], data, old_key)
                else:
                    # Explicit fixture legacy claim never transfers an original uploader's file grant.
                    store.enable_identity()
                    actor_id = uid()
                    with store.transaction() as connection:
                        connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'fixture')", (actor_id, "claimed", "隔离领取者"))
                        connection.execute("UPDATE matters SET owner=? WHERE id=?", (actor_id, matter["id"]))
                    actors["owner"] = {"id": actor_id, "auth_epoch": 1}
                    self.assertEqual(call("get_attachments", matter["id"])["attachments"], [])
                    error(404, "download", matter["id"], attachment["id"])
                    error(409, "upload", matter["id"], data, old_key)


if __name__ == "__main__":
    unittest.main()
