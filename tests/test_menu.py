"""Isolated service contracts with synthetic independent principals."""

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from assistant_app.auth import AUTH_SCHEMA
from assistant_app.menu import MENU_SCHEMA, MenuService
from assistant_app.preparation import initial_facts
from assistant_app.store import AppError, Store, dump, now, uid


BASE_SCHEMA = """
CREATE TABLE matters(id TEXT PRIMARY KEY,owner TEXT,goal_text TEXT,domain TEXT,version INTEGER,revision_no INTEGER,control_epoch INTEGER,assistant_status TEXT,preparation_status TEXT,updated_at TEXT);
CREATE TABLE revisions(matter_id TEXT,revision_no INTEGER,fields TEXT,recorded_at TEXT,confirmed_by TEXT,PRIMARY KEY(matter_id,revision_no));
CREATE TABLE sources(id TEXT PRIMARY KEY,matter_id TEXT,kind TEXT,text TEXT,reported_by TEXT,recorded_by TEXT,recorded_at TEXT);
CREATE TABLE events(id TEXT PRIMARY KEY,matter_id TEXT,event_type TEXT,message TEXT,recorded_by TEXT,recorded_at TEXT);
CREATE TABLE actions(id TEXT PRIMARY KEY,matter_id TEXT,kind TEXT,status TEXT,idempotency_key TEXT UNIQUE,fingerprint TEXT,response TEXT,created_at TEXT);
"""


class FixtureStore:
    _matter = Store._matter
    _check_version = Store._check_version
    _event = Store._event
    _source = Store._source
    _bump = Store._bump

    def __init__(self, folder):
        self.path = Path(folder) / "isolated-menu.sqlite3"
        self.lock = threading.RLock()
        self.actor = ContextVar("menu_fixture_actor", default=None)
        with self.connect() as connection:
            connection.executescript(BASE_SCHEMA + AUTH_SCHEMA + MENU_SCHEMA)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        with self.lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def as_actor(self, actor):
        token = self.actor.set(actor)
        try:
            yield
        finally:
            self.actor.reset(token)

    def principal(self):
        return self.actor.get()

    def actor_id(self):
        principal = self.principal()
        if not principal:
            raise AppError("authentication_required", "请先登录。", 401)
        return principal["id"]


class MenuContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-menu-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.store = FixtureStore(self.temp.name)
        self.menu = MenuService(self.store)
        self.actors = {}
        with self.store.transaction() as connection:
            for name, caps in (("cook", ["cook"]), ("other_cook", ["cook"]), ("employee", ["employee"]),
                               ("other_employee", ["employee"]), ("clerk", ["clerk"]), ("admin", [])):
                actor_id = uid()
                connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,is_admin,created_at,created_by) VALUES(?,?,?,'active',?,?,0,'fixture')",
                                   (actor_id, name, "隔离工程账号 " + name, dump(caps), int(name == "admin")))
                self.actors[name] = {"id": actor_id, "auth_epoch": 1}
        self.matter = self.matter_for("cook")

    def matter_for(self, actor_name, domain="menu"):
        actor, matter_id = self.actors[actor_name], uid()
        with self.store.transaction() as connection:
            connection.execute("INSERT INTO matters VALUES(?,?,?,?,1,1,0,'idle','draft',?)", (matter_id, actor["id"], "private-source-marker 原始私有目标", domain, now()))
            connection.execute("INSERT INTO revisions VALUES(?,1,?,?,?)", (matter_id, dump(initial_facts(domain)), now(), actor["id"]))
        return matter_id

    def call(self, actor_name, method, *args):
        with self.store.as_actor(self.actors[actor_name] if actor_name else None):
            return getattr(self.menu, method)(*args)

    def error(self, expected_status, actor_name, method, *args):
        with self.assertRaises(AppError) as caught:
            self.call(actor_name, method, *args)
        self.assertEqual(caught.exception.status, expected_status)

    def publish_data(self, version=1):
        return {"expected_version": version, "menu_date": "2026-10-09", "meal_slot": "午餐", "dishes": "夹具菜单：米饭、蔬菜", "notes": ""}

    def publish(self):
        return self.call("cook", "publish", self.matter, self.publish_data(), uid())

    def feedback(self, publication, text="夹具少数意见：蔬菜偏少", key=None, actor="employee"):
        return self.call(actor, "feedback", publication["id"], {"version": publication["version"], "feedback_text": text, "dish": "蔬菜", "dietary_constraint": "夹具明确约束"}, key or uid())

    def test_only_owner_cook_can_publish_strict_safe_snapshot(self):
        for actor in ("employee", "clerk", "admin"):
            self.error(403, actor, "publish", self.matter, self.publish_data(), uid())
        self.error(404, "other_cook", "publish", self.matter, self.publish_data(), uid())
        self.error(401, None, "publish", self.matter, self.publish_data(), uid())
        self.error(422, "cook", "publish", self.matter, {**self.publish_data(), "actor_id": self.actors["cook"]["id"]}, uid())
        self.error(422, "cook", "publish", self.matter, {**self.publish_data(), "menu_date": "明天"}, uid())
        published = self.publish()
        employee = self.call("employee", "list_publications")
        self.assertEqual(employee["items"], [{key: value for key, value in published.items() if key != "scope"}])
        raw = json.dumps(employee, ensure_ascii=False)
        self.assertNotIn("private-source-marker", raw)
        self.assertNotIn("source_matter_id", raw)
        self.assertEqual(published["publisher_id"], self.actors["cook"]["id"])
        self.assertEqual(published["publisher_name"], "隔离工程账号 cook")
        self.assertNotIn("password_hash", raw)
        for actor in ("clerk", "admin"):
            self.error(403, actor, "list_publications")

    def test_revision_preserves_snapshots_feedback_and_explicit_identity(self):
        old = self.publish()
        opinion = self.feedback(old)
        response = self.call("cook", "respond_feedback", old["id"], opinion["id"], {"expected_publication_version": 1, "feedback_correction_count": 0, "response_text": "工程厨师原版回应，未声称采纳"}, uid())
        with self.store.connect() as connection:
            original_facts = connection.execute("SELECT fields FROM revisions WHERE matter_id=?", (self.matter,)).fetchone()[0]
        data = {**self.publish_data(), "dishes": "工程修订：米饭、青菜、鸡肉", "notes": "本人修改的公开备注"}
        for actor in ("employee", "clerk", "admin"):
            self.error(403, actor, "revise", old["id"], data, uid())
        self.error(404, "other_cook", "revise", old["id"], data, uid())
        self.error(409, "cook", "revise", old["id"], {**data, "expected_version": 0}, uid())
        self.error(422, "cook", "revise", old["id"], {**data, "menu_date": "明天"}, uid())
        key = uid()
        new = self.call("cook", "revise", old["id"], data, key)
        self.assertEqual(new, self.call("cook", "revise", old["id"], data, key))
        self.assertEqual(new["version"], 2)
        self.assertEqual(new["previous_publication_id"], old["id"])
        self.assertEqual([item["id"] for item in self.call("employee", "list_publications")["items"]], [new["id"]])
        self.error(404, "employee", "feedback", old["id"], {"version": 1, "feedback_text": "旧页面迟到提交"}, uid())
        self.error(409, "cook", "revise", old["id"], data, uid())
        current_opinion = self.feedback(new, "针对新版的工程自愿意见")
        self.assertEqual(self.call("cook", "list_feedback", old["id"])["items"][0]["id"], opinion["id"])
        self.assertEqual(self.call("cook", "list_feedback", new["id"])["items"][0]["id"], current_opinion["id"])
        own = self.call("employee", "get_own_feedback", opinion["id"])
        self.assertEqual(own["publication_version"], 1)
        self.assertEqual(own["menu_snapshot"]["dishes"], old["dishes"])
        self.assertEqual(own["menu_snapshot"]["replacement_publication_id"], new["id"])
        self.assertEqual(own["responses"][0], response)
        self.assertNotIn("private-source-marker", dump(own))
        self.assertNotIn("source_matter_id", dump(own))
        # Fresh connection checks persisted snapshots and private-input preservation.
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM menu_publications").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT version,dishes,status FROM menu_publications WHERE id=?", (old["id"],)).fetchone()[:], (1, old["dishes"], "withdrawn"))
            self.assertEqual(connection.execute("SELECT fields FROM revisions WHERE matter_id=?", (self.matter,)).fetchone()[0], original_facts)
        self.call("cook", "withdraw", new["id"], {"expected_version": 2}, uid())
        self.assertEqual(self.call("cook", "revise", old["id"], data, key)["status"], "withdrawn")
        self.assertEqual(self.call("employee", "list_publications")["items"], [])

    def test_publish_replay_is_actor_scoped_and_never_duplicates(self):
        key, data = uid(), self.publish_data()
        first = self.call("cook", "publish", self.matter, data, key)
        self.assertEqual(first, self.call("cook", "publish", self.matter, data, key))
        other_matter = self.matter_for("other_cook")
        self.error(409, "other_cook", "publish", other_matter, data, key)
        self.error(409, "cook", "publish", self.matter, {**data, "dishes": "不同菜单"}, key)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM menu_publications").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE event_type='menu_published_local'").fetchone()[0], 1)

    def test_feedback_private_owner_sources_projection_and_real_contributor_counts(self):
        publication = self.publish()
        first = self.feedback(publication)
        second = self.feedback(publication, "另一条原话仍是同一人")
        third = self.feedback(publication, "另一人持不同意见", actor="other_employee")
        projection = self.call("cook", "list_feedback", publication["id"])
        self.assertEqual(projection["feedback_count"], 3)
        self.assertEqual(projection["contributor_count"], 2)
        self.assertEqual({item["feedback_text"] for item in projection["items"]}, {first["feedback_text"], second["feedback_text"], third["feedback_text"]})
        self.assertTrue(any(item["dietary_constraint"] == "夹具明确约束" for item in projection["items"]))
        self.assertNotIn("sources", json.dumps(projection))
        for actor in ("employee", "other_employee", "clerk", "admin"):
            self.error(403, actor, "list_feedback", publication["id"])
        self.error(404, "other_cook", "list_feedback", publication["id"])
        with self.store.connect() as connection:
            matter = connection.execute("SELECT * FROM matters WHERE id=?", (first["id"],)).fetchone()
            self.assertEqual(matter["owner"], self.actors["employee"]["id"])
            source = connection.execute("SELECT * FROM sources WHERE matter_id=?", (first["id"],)).fetchone()
            self.assertEqual(source["recorded_by"], self.actors["employee"]["id"])
            self.assertEqual(source["reported_by"], "隔离工程账号 employee")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM actions WHERE kind IN ('prepare','prepare_model')").fetchone()[0], 0)
            fields = json.loads(connection.execute("SELECT fields FROM revisions WHERE matter_id=?", (first["id"],)).fetchone()[0])
            self.assertEqual(fields["menu_ref"]["value"], publication["id"] + "/v1")
            self.assertFalse(fields["__workflow"]["enabled"])

    def test_feedback_replay_version_and_actor_forgery_rejected(self):
        publication, key = self.publish(), uid()
        data = {"version": 1, "feedback_text": "工程夹具原意见"}
        first = self.call("employee", "feedback", publication["id"], data, key)
        self.assertEqual(first, self.call("employee", "feedback", publication["id"], data, key))
        self.error(409, "other_employee", "feedback", publication["id"], data, key)
        self.error(409, "employee", "feedback", publication["id"], {**data, "version": 2}, uid())
        self.error(422, "employee", "feedback", publication["id"], {**data, "reported_by": "冒充厨师"}, uid())
        self.error(422, "employee", "feedback", publication["id"], {**data, "feedback_text": ""}, uid())
        for actor in ("clerk", "admin", "cook"):
            self.error(403, actor, "feedback", publication["id"], data, uid())
        self.assertEqual(self.call("cook", "list_feedback", publication["id"])["feedback_count"], 1)

    def test_withdraw_hides_body_denies_old_feedback_replay_and_preserves_history(self):
        publication, feedback_key = self.publish(), uid()
        data = {"version": 1, "feedback_text": "撤回前工程意见"}
        self.call("employee", "feedback", publication["id"], data, feedback_key)
        self.error(404, "other_cook", "withdraw", publication["id"], {"expected_version": 1}, uid())
        key = uid()
        withdrawn = self.call("cook", "withdraw", publication["id"], {"expected_version": 1}, key)
        self.assertEqual((withdrawn["status"], withdrawn["version"]), ("withdrawn", 2))
        self.assertEqual(withdrawn, self.call("cook", "withdraw", publication["id"], {"expected_version": 1}, key))
        self.assertEqual(self.call("employee", "list_publications")["items"], [])
        self.error(404, "employee", "feedback", publication["id"], data, feedback_key)
        self.error(404, "employee", "feedback", publication["id"], data, uid())
        self.assertEqual(self.call("cook", "list_feedback", publication["id"])["feedback_count"], 1)
        self.assertEqual(self.call("cook", "list_publications")["items"][0]["status"], "withdrawn")

    def test_disabled_publisher_and_revoked_epoch_remove_access_without_fake_withdraw(self):
        publication = self.publish()
        with self.store.transaction() as connection:
            connection.execute("UPDATE accounts SET state='disabled',auth_epoch=auth_epoch+1 WHERE id=?", (self.actors["cook"]["id"],))
        self.assertEqual(self.call("employee", "list_publications")["items"], [])
        self.error(404, "employee", "feedback", publication["id"], {"version": 1, "feedback_text": "夹具"}, uid())
        self.error(401, "cook", "list_publications")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT status FROM menu_publications WHERE id=?", (publication["id"],)).fetchone()[0], "published")
        with self.store.transaction() as connection:
            connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (self.actors["employee"]["id"],))
        self.error(401, "employee", "list_publications")

    def test_new_publication_keeps_old_snapshot_and_feedback_version(self):
        old = self.publish()
        feedback = self.feedback(old)
        data = {**self.publish_data(version=2), "dishes": "明确修订后的夹具菜单"}
        new = self.call("cook", "publish", self.matter, data, uid())
        self.assertNotEqual(old["id"], new["id"])
        snapshots = {item["id"]: item for item in self.call("employee", "list_publications")["items"]}
        self.assertEqual(snapshots[old["id"]]["dishes"], old["dishes"])
        self.assertEqual(snapshots[new["id"]]["dishes"], data["dishes"])
        self.assertEqual(self.call("cook", "list_feedback", old["id"])["feedback_count"], 1)
        self.assertEqual(self.call("cook", "list_feedback", new["id"])["feedback_count"], 0)
        with self.store.connect() as connection:
            projection = connection.execute("SELECT publication_id,publication_version FROM menu_feedback WHERE matter_id=?", (feedback["id"],)).fetchone()
            self.assertEqual(tuple(projection), (old["id"], 1))

    def test_real_store_identity_migration_and_feedback_private_readback(self):
        store = Store(Path(self.temp.name) / "real-store")
        store.enable_identity(MENU_SCHEMA)
        with store.transaction() as connection:
            for name in ("cook", "employee"):
                actor = self.actors[name]
                connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active',?,0,'isolated-fixture')",
                                   (actor["id"], name, "隔离实际Store账号 " + name, dump([name])))
        service = MenuService(store)
        with store.as_actor(self.actors["cook"]):
            matter = store.mutate("create", None, {"goal_text": "真实Store隔离菜单准备夹具", "domain": "menu"}, uid())
            publication = service.publish(matter["id"], self.publish_data(matter["version"]), uid())
        with store.as_actor(self.actors["employee"]):
            feedback = service.feedback(publication["id"], {"version": 1, "feedback_text": "真实Store隔离自愿意见"}, uid())
            detail = store.detail(feedback["id"])
            self.assertEqual(detail["facts"]["feedback_text"]["value"], feedback["feedback_text"])
            self.assertEqual(detail["sources"][0]["recorded_by"], self.actors["employee"]["id"])
            self.assertEqual(detail["actions"], [])
        with store.as_actor(self.actors["cook"]):
            projection = service.list_feedback(publication["id"])
            self.assertEqual(projection["items"][0]["submitted_by"], "隔离实际Store账号 employee")
            self.assertEqual(projection["items"][0]["version"], 1)
            with self.assertRaises(AppError) as denied:
                store.detail(feedback["id"])
            self.assertEqual(denied.exception.status, 404)
        with store.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())


if __name__ == "__main__":
    unittest.main()
