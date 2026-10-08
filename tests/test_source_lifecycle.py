"""One source/history/revocation smoke, with real six-table and schema5 Stores."""

import tempfile
import unittest
from pathlib import Path

from assistant_app.sharing import SHARING_SCHEMA, ShareService
from assistant_app.source_lifecycle import SourceLifecycleService
from assistant_app.store import AppError, Store, dump, now, uid


class SourceLifecycleSmoke(unittest.TestCase):
    def test_history_cycles_replay_and_atomic_share_revocation(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-source-state-", dir=Path(__file__).resolve().parent) as directory:
                store, actors = Store(directory), {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    store.enable_services()
                    store.enable_sharing(SHARING_SCHEMA)
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active',?,0,'fixture')", (actor_id, name, "隔离 " + name, dump(["employee" if name == "owner" else "clerk"])))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}
                service = SourceLifecycleService(store)

                def call(method, *args, actor="owner"):
                    with store.as_actor(actors[actor]):
                        return getattr(service, method)(*args)

                def error(status, method, *args, actor="owner"):
                    with self.assertRaises(AppError) as caught:
                        call(method, *args, actor=actor)
                    self.assertEqual(caught.exception.status, status)

                def request(view, state, replacement=None):
                    return {"expected_version": view["version"], "input_revision": view["revision_no"], "state": state, "replacement_source_id": replacement, "note": "本人明确说明准备引用范围，正式效力待核"}

                with store.as_actor(actors["owner"]):
                    detail = store.mutate("create", None, {"goal_text": "隔离来源控制", "domain": "general"}, uid())
                    for text in ("原文A不可删除", "原文B替代说明"):
                        detail = store.mutate("source", detail["id"], {"expected_version": detail["version"], "kind": "oral_note", "text": text}, uid())
                    sources = {source["text"]: source["id"] for source in detail["sources"]}
                    first, second = sources["原文A不可删除"], sources["原文B替代说明"]
                    manual_id = uid()
                    with store.transaction() as connection:
                        facts = store._stored_facts(connection, detail)
                        facts.update(__workflow={"enabled": False, "mode": "template"}, __rows=[{"历史": "原人工明细元数据"}])
                        connection.execute("UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?", (dump(facts), detail["id"], detail["revision_no"]))
                        connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (manual_id, detail["id"], "general", "人工稿", "人工原稿不可覆盖", "human_saved", detail["revision_no"], now()))
                initial_data, initial_key = request(detail, "usable"), uid()
                current = call("set_source_state", detail["id"], first, initial_data, initial_key)
                current = call("set_source_state", detail["id"], first, request(current, "superseded", second), uid())
                error(422, "set_source_state", detail["id"], second, request(current, "superseded", first), uid())
                error(422, "set_source_state", detail["id"], first, request(current, "superseded", first), uid())
                if protected:
                    error(404, "get_sources", detail["id"], actor="other")
                    error(404, "set_source_state", detail["id"], first, initial_data, initial_key, actor="other")
                    with store.as_actor(actors["owner"]):
                        grant = ShareService(store).create_share(detail["id"], {"expected_version": current["version"], "artifact_id": manual_id, "artifact_version": 1, "recipient_id": actors["other"]["id"], "title": "必要准备", "content": "明确选定的独立准备文字", "dependency_refs": [second]}, uid())
                    current = call("get_sources", detail["id"])
                with store.as_actor(actors["owner"]):
                    queued = store.mutate("prepare", detail["id"], {"expected_version": current["version"], "kind": "prepare", "mode": "template"}, uid())
                    old_epoch = queued["control_epoch"]
                current = call("set_source_state", detail["id"], first, request(queued, "withheld"), uid())
                replay = call("set_source_state", detail["id"], first, initial_data, initial_key)
                self.assertEqual(replay, current)
                first_view = next(item for item in current["items"] if item["id"] == first)
                self.assertEqual(first_view["source_state"], "withheld")
                self.assertEqual(first_view["text"], "原文A不可删除")
                with store.as_actor(actors["owner"]):
                    retained = store.detail(detail["id"])
                    self.assertEqual(retained["control_epoch"], old_epoch + 1)
                    self.assertEqual(store.download(manual_id, 1), "人工原稿不可覆盖")
                    with store.connect() as connection:
                        raw = store._stored_facts(connection, retained)
                        self.assertEqual(raw["__rows"], facts["__rows"])
                        self.assertEqual(raw["__workflow"], facts["__workflow"])
                        self.assertEqual(connection.execute("SELECT status FROM actions WHERE matter_id=? AND kind='prepare'", (detail["id"],)).fetchone()[0], "cancelled")
                        self.assertEqual(connection.execute("SELECT COUNT(*) FROM sources WHERE matter_id=?", (detail["id"],)).fetchone()[0], len(detail["sources"]))
                    paused = store.mutate("control", detail["id"], {"expected_version": current["version"], "command": "pause"}, uid())
                if protected:
                    with store.as_actor(actors["other"]), self.assertRaises(AppError) as caught:
                        ShareService(store).download_share(grant["id"], 1)
                    self.assertEqual(caught.exception.status, 404)
                    with store.as_actor(actors["owner"]):
                        withdrawn = ShareService(store).get_share(grant["id"])
                    self.assertEqual(withdrawn["status"], "withdrawn")
                    self.assertEqual(withdrawn["version"], 2)
                current = call("set_source_state", detail["id"], first, request(paused, "usable"), uid())
                with store.as_actor(actors["owner"]):
                    self.assertEqual(store.detail(detail["id"])["assistant_status"], "paused")
                self.assertEqual(next(item for item in current["items"] if item["id"] == first)["source_state"], "usable")
                reopened = SourceLifecycleService(Store(directory))
                with reopened.store.as_actor(actors["owner"]):
                    self.assertEqual(reopened.get_sources(detail["id"]), current)
                with store.connect() as connection:
                    self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())


if __name__ == "__main__":
    unittest.main()
