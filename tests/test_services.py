"""Actual Store service contracts with isolated synthetic independent accounts."""

import json
import tempfile
import unittest
from pathlib import Path

from assistant_app.menu import MENU_SCHEMA
from assistant_app.services import SERVICE_SCHEMA, ServiceService
from assistant_app.store import AppError, Store, dump, uid


class ServiceContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-services-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.enable_identity(MENU_SCHEMA + SERVICE_SCHEMA)
        self.services = ServiceService(self.store)
        self.actors = {}
        with self.store.transaction() as connection:
            for name, capabilities in (("owner", ["employee"]), ("clerk", ["clerk"]), ("other_clerk", ["clerk"]),
                                       ("executor", ["executor"]), ("other_executor", ["executor"]), ("admin", [])):
                actor_id = uid()
                connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,is_admin,created_at,created_by) VALUES(?,?,?,'active',?,?,0,'isolated-fixture')",
                                   (actor_id, name, "隔离工程账号 " + name, dump(capabilities), int(name == "admin")))
                self.actors[name] = {"id": actor_id, "auth_epoch": 1}

    def call(self, name, method, *args):
        with self.store.as_actor(self.actors[name] if name else None):
            return getattr(self.services, method)(*args)

    def error(self, status, name, method, *args):
        with self.assertRaises(AppError) as caught:
            self.call(name, method, *args)
        self.assertEqual(caught.exception.status, status, caught.exception.payload)
        return caught.exception

    def matter(self, domain="repair", complete=True):
        with self.store.as_actor(self.actors["owner"]):
            detail = self.store.mutate("create", None, {"goal_text": "private-marker 私人来源与材料不可共享", "domain": domain}, uid())
            if complete:
                changes = {"location": "夹具房间", "problem" if domain == "repair" else "scope": "夹具问题或清洁范围", "access_window": "夹具约定窗口"}
                if domain == "repair":
                    changes["contact"] = "private-contact 不默认复制到服务"
                detail = self.store.mutate("facts", detail["id"], {"expected_version": detail["version"], "field_changes": changes}, uid())
            return detail

    def submit(self, matter, key=None):
        data = {"expected_version": matter["version"], "input_revision": matter["revision_no"], "clerk_id": self.actors["clerk"]["id"]}
        return self.call("owner", "submit", matter["id"], data, key or uid())

    def assign(self, case, executor="executor", note=""):
        return self.call("clerk", "assign", case["id"], {"expected_version": case["version"], "executor_id": self.actors[executor]["id"], "note": note}, uid())

    def respond(self, case, command, note="", name="executor", key=None):
        return self.call(name, "respond", case["id"], {"expected_version": case["version"], "assignment_epoch": case["assignment_epoch"], "command": command, "note": note}, key or uid())

    def test_repair_cleaning_projection_private_isolation_and_candidates(self):
        for domain in ("repair", "cleaning"):
            matter = self.matter(domain)
            case = self.submit(matter)
            projection = case["projection"]
            self.assertEqual(projection["domain"], domain)
            self.assertEqual(projection["request_text"], "夹具问题或清洁范围")
            self.assertEqual(projection["entry_permission"], {"status": "unknown"})
            self.assertEqual(projection["contact"], "")
            self.assertNotIn("private-marker", json.dumps(self.call("clerk", "get_service", case["id"]), ensure_ascii=False))
            self.assertNotIn("private-contact", json.dumps(projection, ensure_ascii=False))
            self.assertNotIn("matter_id", self.call("clerk", "get_service", case["id"]))
            case = self.assign(case)
            for actor in ("clerk", "executor"):
                with self.store.as_actor(self.actors[actor]), self.assertRaises(AppError) as denied:
                    self.store.detail(matter["id"])
                self.assertEqual(denied.exception.status, 404)
            self.assertEqual(len(self.call("executor", "list_services")["items"]), 1 if domain == "repair" else 2)
        self.assertEqual(set(self.call("owner", "eligible_accounts", "clerk")["items"][0]), {"id", "display_name"})
        self.assertEqual(len(self.call("clerk", "eligible_accounts", "executor")["items"]), 2)
        self.error(403, "admin", "eligible_accounts", "clerk")
        self.error(403, "owner", "eligible_accounts", "executor")
        incomplete = self.matter(complete=False)
        self.error(422, "owner", "submit", incomplete["id"], {"expected_version": incomplete["version"], "input_revision": 1, "clerk_id": self.actors["clerk"]["id"]}, uid())

    def test_clerk_private_draft_save_history_conflicts_and_revocation(self):
        for domain in ("repair", "cleaning"):
            matter = self.matter(domain)
            case = self.submit(matter)
            original = self.call("clerk", "get_service", case["id"])
            preview = self.call("clerk", "get_draft", case["id"])
            self.assertEqual(preview["version"], 0)
            self.assertIn("夹具问题或清洁范围", preview["content"])
            self.assertNotIn("private-marker", preview["content"])
            self.assertNotIn("private-contact", preview["content"])
            self.assertIn("未正式关闭", preview["content"])
            self.assertIn("软件动作／记录说明", preview["content"])
            for actor in ("owner", "other_clerk", "admin"):
                self.error(403 if actor == "owner" else 404, actor, "get_draft", case["id"])
            text = "clerk-private-marker 工程经办跟进文字，未发送"
            data = {"expected_version": case["version"], "base_version": 0, "content": text}
            key = uid()
            saved = self.call("clerk", "save_draft", case["id"], data, key)
            self.assertEqual(saved["content"], text)
            self.assertEqual(saved, self.call("clerk", "save_draft", case["id"], data, key))
            self.error(409, "clerk", "save_draft", case["id"], data, uid())
            second = self.call("clerk", "save_draft", case["id"], {**data, "base_version": 1, "content": text + " v2"}, uid())
            self.assertEqual(second["version"], 2)
            self.assertEqual(self.call("clerk", "save_draft", case["id"], data, key)["content"], second["content"])
            self.assertEqual(self.call("clerk", "get_service", case["id"]), original)
            with self.store.as_actor(self.actors["owner"]):
                self.assertNotIn("clerk-private-marker", dump(self.store.detail(matter["id"])))
                current = self.store.detail(matter["id"])
                self.store.mutate("control", matter["id"], {"expected_version": current["version"], "command": "pause"}, uid())
            case = self.assign(case)
            self.error(403, "executor", "get_draft", case["id"])
            self.assertNotIn("clerk-private-marker", dump(self.call("executor", "get_service", case["id"])))
            self.assertTrue(self.call("clerk", "get_draft", case["id"])["stale"])
            self.error(409, "clerk", "save_draft", case["id"], {**data, "base_version": 2}, uid())
            third = self.call("clerk", "save_draft", case["id"], {"expected_version": case["version"], "base_version": 2, "content": text + " v3"}, uid())
            self.assertEqual(third["version"], 3)
            historical = self.call("clerk", "get_draft", case["id"], 1)
            self.assertEqual(historical["content"], text)
            self.assertTrue(historical["read_only_history"])
            self.assertTrue(historical["stale"])
            # Reopen the actual Store: no extra table or source/artifact write is needed.
            reopened = ServiceService(Store(self.temp.name))
            with reopened.store.as_actor(self.actors["clerk"]):
                self.assertEqual(reopened.get_draft(case["id"])["content"], third["content"])
            with self.store.as_actor(self.actors["owner"]):
                self.assertEqual(self.store.detail(matter["id"])["assistant_status"], "paused")
            self.call("owner", "withdraw", case["id"], {"expected_version": case["version"], "note": "工程撤回"}, uid())
            self.error(404, "clerk", "get_draft", case["id"])
            self.error(404, "clerk", "save_draft", case["id"], data, key)

    def test_separate_states_wrong_actor_no_close_and_pause_does_not_block_real_feedback(self):
        matter = self.matter()
        case = self.assign(self.submit(matter))
        respond_data = {"expected_version": case["version"], "assignment_epoch": case["assignment_epoch"], "command": "accept"}
        self.error(403, "clerk", "respond", case["id"], respond_data, uid())
        self.error(404, "admin", "assign", case["id"], {"expected_version": case["version"], "executor_id": self.actors["executor"]["id"]}, uid())
        self.error(404, "other_clerk", "get_service", case["id"])
        self.error(409, "executor", "respond", case["id"], {**respond_data, "command": "claimed_done", "note": "尚未接受"}, uid())
        case = self.respond(case, "accept")
        with self.store.as_actor(self.actors["owner"]):
            current = self.store.detail(matter["id"])
            self.store.mutate("control", matter["id"], {"expected_version": current["version"], "command": "pause"}, uid())
        case = self.respond(case, "start")
        case = self.respond(case, "claimed_done", "夹具本人声明已处理，未代表验收")
        self.assertEqual(case["tracks"]["requester_result"], "unknown")
        self.assertEqual(case["tracks"]["execution"], "claimed_done")
        self.assertEqual(case["tracks"]["inspection"], "unknown")
        self.error(403, "executor", "result", case["id"], {"expected_version": case["version"], "assignment_epoch": case["assignment_epoch"], "result": "resolved"}, uid())
        case = self.call("owner", "result", case["id"], {"expected_version": case["version"], "assignment_epoch": case["assignment_epoch"], "result": "resolved"}, uid())
        self.assertEqual((case["tracks"]["execution"], case["tracks"]["requester_result"], case["tracks"]["closure"]), ("claimed_done", "resolved", "waiting_rule"))
        self.assertEqual(self.error(409, "owner", "close", case["id"], {}, uid()).payload["code"], "contract_unconfirmed")
        self.error(404, "admin", "close", case["id"], {}, uid())
        with self.store.as_actor(self.actors["owner"]):
            self.assertEqual(self.store.detail(matter["id"])["assistant_status"], "paused")

    def test_idempotent_current_permission_reassign_epochs_and_executor_event_filter(self):
        matter, key = self.matter(), uid()
        case = self.submit(matter, key)
        self.assertEqual(self.submit(matter, key)["id"], case["id"])
        case = self.assign(case)
        response_key = uid()
        response_data = {"expected_version": case["version"], "assignment_epoch": case["assignment_epoch"], "command": "accept"}
        case = self.call("executor", "respond", case["id"], response_data, response_key)
        self.call("executor", "respond", case["id"], response_data, response_key)
        self.assertEqual(sum(event["command"] == "accept" for event in case["events"]), 1)
        case = self.respond(case, "blocked", "old-executor-private-marker 旧执行者个人说明")
        previous_epoch = case["assignment_epoch"]
        case = self.assign(case, "other_executor", "夹具改派原因")
        self.assertGreater(case["assignment_epoch"], previous_epoch)
        self.assertEqual(case["tracks"]["acceptance"], "pending")
        self.error(404, "executor", "get_service", case["id"])
        self.error(404, "executor", "respond", case["id"], response_data, response_key)
        self.assertEqual(self.call("executor", "list_services")["items"], [])
        current = self.call("other_executor", "get_service", case["id"])
        self.assertNotIn("old-executor-private-marker", json.dumps(current, ensure_ascii=False))
        self.error(409, "other_executor", "respond", case["id"], {"expected_version": case["version"], "assignment_epoch": previous_epoch, "command": "accept"}, uid())
        case = self.respond(case, "accept", name="other_executor")
        self.assertTrue(all(event["assignment_epoch"] == case["assignment_epoch"] for event in case["events"]))
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM service_cases").fetchone()[0], 1)
            records = [json.loads(row[0]) for row in connection.execute("SELECT message FROM events WHERE event_type='service_event'")]
            self.assertEqual(sum(record["command"] == "accept" and record["actor_id"] == self.actors["executor"]["id"] for record in records), 1)

    def test_revision_withdraw_assignment_and_sharing_preserve_history_and_revoke(self):
        matter = self.matter("cleaning")
        case = self.respond(self.assign(self.submit(matter)), "accept")
        with self.store.as_actor(self.actors["owner"]):
            changed = self.store.mutate("facts", matter["id"], {"expected_version": matter["version"], "field_changes": {"scope": "修订后的夹具清洁范围"}}, uid())
        self.assertTrue(self.call("owner", "get_service", case["id"])["stale"])
        case = self.call("owner", "revise", case["id"], {"expected_version": case["version"], "input_revision": changed["revision_no"], "note": "本人明确更新范围"}, uid())
        self.assertFalse(case["stale"])
        self.assertEqual(case["projection"]["request_text"], "修订后的夹具清洁范围")
        self.assertEqual(case["events"][-1]["projection_before"]["request_text"], "夹具问题或清洁范围")
        self.assertEqual(case["events"][-1]["projection_after"]["request_text"], "修订后的夹具清洁范围")
        self.error(404, "executor", "get_service", case["id"])
        case = self.assign(case, note="更新范围重新安排")
        case = self.respond(case, "accept")
        case = self.respond(case, "claimed_done", "夹具旧处理声明保留")
        case = self.call("clerk", "withdraw", case["id"], {"expected_version": case["version"], "note": "只撤派，不撤发起人共享"}, uid())
        self.assertEqual(case["sharing_status"], "shared")
        self.assertEqual(case["tracks"]["execution"], "claimed_done")
        self.error(404, "executor", "get_service", case["id"])
        statement_data = {"expected_version": case["version"], "reported_by": "外部夹具陈述人", "note": "转述不替执行者登入或推进结果"}
        statement_key = uid()
        before = case["tracks"]
        case = self.call("clerk", "statement", case["id"], statement_data, statement_key)
        self.assertEqual(case["tracks"], before)
        self.assertEqual(case["events"][-1]["recorded_by"], self.actors["clerk"]["id"])
        self.assertEqual(case["events"][-1]["evidence_class"], "unverified_transcription")
        case = self.call("owner", "withdraw", case["id"], {"expected_version": case["version"], "note": "本人撤回共享"}, uid())
        self.assertEqual(case["sharing_status"], "withdrawn")
        self.error(404, "clerk", "get_service", case["id"])
        self.error(404, "clerk", "statement", case["id"], statement_data, statement_key)
        self.assertEqual(self.call("clerk", "list_services")["items"], [])
        restored = self.submit(changed)
        self.assertEqual(restored["id"], case["id"])
        self.assertGreater(restored["assignment_epoch"], case["assignment_epoch"])
        self.assertTrue(any(event["command"] == "claimed_done" for event in restored["events"]))

    def test_decline_obstacle_result_and_disabled_accounts_keep_distinct_waiting(self):
        case = self.assign(self.submit(self.matter()))
        case = self.respond(case, "decline", "夹具拒接原因")
        self.assertTrue(any(item["who"] == self.actors["clerk"]["id"] for item in case["waiting"]))
        case = self.assign(case, note="拒接后重新明确安排")
        case = self.respond(case, "accept")
        case = self.respond(case, "blocked", "夹具进入条件不足")
        self.assertEqual(case["tracks"]["execution"], "blocked")
        case = self.call("owner", "result", case["id"], {"expected_version": case["version"], "assignment_epoch": case["assignment_epoch"], "result": "still_problem", "note": "本人现场仍有问题"}, uid())
        self.assertEqual(case["tracks"]["execution"], "blocked")
        self.assertTrue(any("仍有问题" in item["reason"] for item in case["waiting"]))
        with self.store.transaction() as connection:
            connection.execute("UPDATE accounts SET state='disabled',auth_epoch=auth_epoch+1 WHERE id=?", (self.actors["executor"]["id"],))
        self.error(401, "executor", "get_service", case["id"])
        current = self.call("clerk", "get_service", case["id"])
        self.assertEqual(current["tracks"]["execution"], "blocked")
        self.assertTrue(any("账号已停用" in item["reason"] and item["who"] == self.actors["clerk"]["id"] for item in current["waiting"]))
        with self.store.connect() as connection:
            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())


if __name__ == "__main__":
    unittest.main()
