"""Isolated HTTP checks for local rows, evidence, and bounded continuation."""

import http.client
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from assistant_app.server import create_server


ROOT = Path(__file__).resolve().parents[1]


class OperationsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-operations-", dir=ROOT / "tests")
        self.addCleanup(self.temp.cleanup)
        self.server = create_server(port=0, data_dir=Path(self.temp.name), web_dir=ROOT / "web")
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        status, session = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertFalse(session["model_configured"])
        self.token = session["csrf_token"]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, data=None, key=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {}
        if data is not None:
            headers = {"Content-Type": "application/json", "X-CSRF-Token": getattr(self, "token", ""),
                       "Idempotency-Key": key or str(uuid.uuid4())}
        try:
            connection.request(method, path, body=json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None,
                               headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def create(self, domain="expense", auto=False):
        status, detail = self.request("POST", "/api/matters", {"domain": domain, "goal_text": "隔离工程夹具：整理准备资料", "auto_prepare": auto})
        self.assertEqual(status, 201, detail)
        return detail

    def get(self, detail):
        status, value = self.request("GET", "/api/matters/" + detail["id"])
        self.assertEqual(status, 200, value)
        return value

    def post(self, detail, suffix, key=None, **data):
        return self.request("POST", f"/api/matters/{detail['id']}/{suffix}",
                            {"expected_version": detail["version"], **data}, key=key)

    def completed(self, detail, count):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = self.get(detail)
            if len(result["actions"]) == count and all(action["status"] == "completed" for action in result["actions"]):
                self.assertTrue(all(action["kind"] == "prepare" for action in result["actions"]))
                return result
            time.sleep(0.025)
        self.fail(f"本地模板整理未在4秒内达到{count}次完成：{result['actions']}")

    def unknown_tracks(self, detail):
        self.assertEqual(detail["business_status"], "unknown")
        self.assertTrue(all(track["status"] == "unknown" for track in detail["tracks"].values()))

    def rows(self, detail, domain="expense"):
        dimension = "currency" if domain == "expense" else "unit"
        return [{"id": "row-a", "label": "夹具项目", "value": "0.1", dimension: "NGN" if domain == "expense" else "件",
                 "period": "2026-10", "source_id": detail["sources"][0]["id"], "source_position": "原表第2行"},
                {"id": "row-b", "label": "夹具项目", "value": "0.2", dimension: "NGN" if domain == "expense" else "件",
                 "period": "2026-10", "source_id": detail["sources"][0]["id"], "source_position": "原表第3行"}]

    def evidence(self, detail):
        return {"track": "execution", "record_type": "statement", "reported_by": "工程夹具陈述人",
                "source_id": detail["sources"][0]["id"], "source_position": "原记录第1段",
                "occurred_at": "2026-10-08T20:00:00+01:00", "text": "夹具原话：对方说已经处理；报修人尚未核对。"}

    def test_auto_template_continues_once_per_input_without_wait_loop(self):
        detail = self.completed(self.create(auto=True), 1)
        self.assertEqual(detail["workflow"], {"enabled": True, "mode": "template"})
        time.sleep(0.3)
        self.assertEqual(len(self.get(detail)["actions"]), 1)
        status, detail = self.post(detail, "facts/confirm", field_changes={"purpose": "夹具核对"})
        self.assertEqual(status, 200, detail)
        detail = self.completed(detail, 2)
        status, detail = self.post(detail, "sources", kind="oral_note", text="隔离新来源", reported_by="工程夹具")
        self.assertEqual(status, 200, detail)
        detail = self.completed(detail, 3)
        status, detail = self.post(detail, "rows/confirm", rows=self.rows(detail))
        self.assertEqual(status, 200, detail)
        detail = self.completed(detail, 4)
        self.assertEqual(detail["calculation"]["groups"][0]["total"], "0.3")
        self.assertFalse(detail["calculation"]["stale"])
        self.unknown_tracks(detail)

    def test_pause_and_handoff_allow_manual_changes_but_stop_auto_work(self):
        detail = self.completed(self.create(auto=True), 1)
        status, detail = self.post(detail, "control", command="pause")
        self.assertEqual(status, 200, detail)
        for suffix, data in (("facts/confirm", {"field_changes": {"purpose": "暂停期间人工修正"}}),
                             ("sources", {"kind": "oral_note", "text": "暂停期间人工来源"}),
                             ("rows/confirm", {"rows": self.rows(detail)}),
                             ("workflow", {"enabled": True})):
            status, detail = self.post(detail, suffix, **data)
            self.assertEqual(status, 200, detail)
            self.assertEqual(detail["assistant_status"], "paused")
        time.sleep(0.3)
        detail = self.get(detail)
        self.assertEqual(len(detail["actions"]), 1)
        self.assertEqual(detail["facts"]["purpose"]["value"], "暂停期间人工修正")
        status, detail = self.post(detail, "control", command="resume")
        self.assertEqual(status, 200, detail)
        detail = self.completed(detail, 2)
        status, detail = self.post(detail, "control", command="handoff")
        self.assertEqual(status, 200, detail)
        status, detail = self.post(detail, "facts/confirm", field_changes={"purpose": "人工接手后修正"})
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["assistant_status"], "handoff")
        self.assertEqual(len(self.get(detail)["actions"]), 2)
        self.unknown_tracks(detail)

    def test_calculation_revision_stale_reconfirm_and_old_request_idempotency(self):
        original = self.create()
        rows = self.rows(original)
        data = {"expected_version": original["version"], "rows": rows}
        key = str(uuid.uuid4())
        path = f"/api/matters/{original['id']}/rows/confirm"
        status, confirmed = self.request("POST", path, data, key)
        self.assertEqual(status, 200, confirmed)
        self.assertEqual(confirmed["calculation"]["groups"][0]["total"], "0.3")
        self.assertFalse(confirmed["calculation"]["stale"])
        status, changed = self.post(confirmed, "facts/confirm", field_changes={"purpose": "变更后须重核明细"})
        self.assertEqual(status, 200, changed)
        self.assertEqual(changed["rows"], confirmed["rows"])
        self.assertTrue(changed["calculation"]["stale"])
        status, replay = self.request("POST", path, data, key)
        self.assertEqual((status, replay), (200, confirmed))
        current = self.get(changed)
        self.assertEqual(current["revision_no"], changed["revision_no"])
        self.assertEqual(sum(event["event_type"] == "rows_confirmed" for event in current["activities"]), 1)
        status, current = self.post(current, "sources", kind="oral_note", text="人工补充来源")
        self.assertEqual(status, 200, current)
        self.assertTrue(current["calculation"]["stale"])
        self.assertEqual(current["rows"], confirmed["rows"])
        status, current = self.post(current, "rows/confirm", rows=current["rows"])
        self.assertEqual(status, 200, current)
        self.assertFalse(current["calculation"]["stale"])
        self.assertEqual(current["calculation"]["input_revision"], current["revision_no"])
        self.unknown_tracks(current)

    def test_inventory_groups_items_and_rejects_foreign_sources_or_missing_values(self):
        detail = self.create("inventory")
        foreign = self.create("inventory")
        rows = self.rows(detail, "inventory")
        rows[0]["label"], rows[1]["label"] = "苹果", "螺丝"
        status, detail = self.post(detail, "rows/confirm", rows=rows)
        self.assertEqual(status, 200, detail)
        self.assertEqual([(g["label"], g["total"]) for g in detail["calculation"]["groups"]], [("苹果", "0.1"), ("螺丝", "0.2")])
        for bad in ({**rows[0], "source_id": foreign["sources"][0]["id"]},
                    {**rows[0], "value": None}, {**rows[0], "unit": "待核"}):
            status, error = self.post(detail, "rows/confirm", rows=[bad])
            self.assertEqual(status, 422, error)
            self.assertEqual(self.get(detail)["rows"], rows)
        self.unknown_tracks(self.get(detail))

    def test_evidence_is_unverified_traceable_stale_and_never_business_status(self):
        original = self.create("repair")
        data = {"expected_version": original["version"], **self.evidence(original)}
        key = str(uuid.uuid4())
        path = f"/api/matters/{original['id']}/evidence"
        status, saved = self.request("POST", path, data, key)
        self.assertEqual(status, 200, saved)
        record = saved["evidence"][0]
        self.assertEqual(record["verification"], "unverified_transcription")
        self.assertEqual(record["reported_by"], data["reported_by"])
        self.assertEqual(record["source_id"], data["source_id"])
        self.assertTrue(record["recorded_by"])
        self.assertFalse(record["stale"])
        self.unknown_tracks(saved)
        status, changed = self.post(saved, "facts/confirm", field_changes={"problem": "现场仍待核"})
        self.assertEqual(status, 200, changed)
        self.assertTrue(changed["evidence"][0]["stale"])
        status, replay = self.request("POST", path, data, key)
        self.assertEqual((status, replay), (200, saved))
        current = self.get(changed)
        self.assertEqual(len(current["evidence"]), 1)
        self.assertTrue(current["evidence"][0]["stale"])
        foreign = self.create("repair")
        for changes in ({"reported_by": ""}, {"source_position": ""}, {"text": ""}, {"occurred_at": "明天"},
                        {"track": "payment"}, {"record_type": "approved"}, {"source_id": foreign["sources"][0]["id"]}):
            status, error = self.post(current, "evidence", **{**self.evidence(current), **changes})
            self.assertEqual(status, 422, error)
        self.assertEqual(len(self.get(current)["evidence"]), 1)
        self.unknown_tracks(self.get(current))

    def test_workflow_validation_disable_and_twenty_step_bound(self):
        detail = self.create("repair")
        status, error = self.post(detail, "workflow", enabled="true")
        self.assertEqual(status, 422, error)
        self.assertFalse(self.get(detail)["workflow"]["enabled"])
        status, detail = self.post(detail, "workflow", enabled=True)
        self.assertEqual(status, 200, detail)
        detail = self.completed(detail, 1)
        for expected in range(2, 21):
            status, detail = self.post(detail, "control", command="pause")
            self.assertEqual(status, 200, detail)
            status, detail = self.post(detail, "control", command="resume")
            self.assertEqual(status, 200, detail)
            detail = self.completed(detail, expected)
        status, detail = self.post(detail, "control", command="pause")
        self.assertEqual(status, 200, detail)
        status, detail = self.post(detail, "control", command="resume")
        self.assertEqual(status, 200, detail)
        self.assertEqual(len(detail["actions"]), 20)
        self.assertEqual(detail["assistant_status"], "waiting")
        self.assertTrue(any(event["event_type"] == "continuation_limit" for event in detail["activities"]))
        status, detail = self.post(detail, "workflow", enabled=False)
        self.assertEqual(status, 200, detail)
        status, detail = self.post(detail, "facts/confirm", field_changes={"problem": "关闭自动后人工记录"})
        self.assertEqual(status, 200, detail)
        self.assertFalse(detail["workflow"]["enabled"])
        self.assertEqual(len(detail["actions"]), 20)
        self.unknown_tracks(detail)


if __name__ == "__main__":
    unittest.main()
