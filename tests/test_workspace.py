"""Isolated v0.1 HTTP acceptance; no external model or business data."""
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from assistant_app.server import create_server
from assistant_app.preparation import prepare as real_prepare


ROOT = Path(__file__).resolve().parents[1]


class WorkspaceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-v01-", dir=ROOT / "tests")
        self.addCleanup(self.temp.cleanup)
        self.server = None
        self.start()
        self.addCleanup(self.stop)

    def start(self):
        self.server = create_server(port=0, data_dir=Path(self.temp.name), web_dir=ROOT / "web")
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        status, session = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(session["mode"], "local_preview")
        self.assertIs(session["model_configured"], False)
        self.token = session["csrf_token"]

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
            self.server = None

    def request(self, method, path, data=None, key=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {}
        if data is not None:
            request_headers.update({"Content-Type": "application/json", "X-CSRF-Token": getattr(self, "token", ""), "Idempotency-Key": key or str(uuid.uuid4())})
        request_headers.update(headers or {})
        try:
            connection.request(method, path, body=json.dumps(data, ensure_ascii=False).encode() if data is not None else None, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw.decode()
        finally:
            connection.close()

    def create(self, domain="travel", key=None):
        status, detail = self.request("POST", "/api/matters", {"goal_text": "测试夹具：准备出行材料", "domain": domain, "source_kind": "user_text"}, key=key)
        self.assertEqual(status, 201, detail)
        return detail

    def get(self, matter):
        status, detail = self.request("GET", "/api/matters/" + matter["id"])
        self.assertEqual(status, 200, detail)
        return detail

    def post(self, matter, suffix, **data):
        return self.request("POST", "/api/matters/" + matter["id"] + "/" + suffix, {"expected_version": matter["version"], **data})

    def completed(self, matter):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            detail = self.get(matter)
            if detail["actions"] and detail["actions"][-1]["status"] == "completed":
                return detail
            time.sleep(0.025)
        self.fail("后台准备未在4秒内完成")

    def test_prepare_edit_restart_and_stale_revision(self):
        detail = self.create()
        status, detail = self.post(detail, "actions", kind="prepare")
        self.assertEqual(status, 202)
        detail = self.completed(detail)
        self.assertEqual(detail["preparation_status"], "needs_review")
        status, detail = self.post(detail, "facts/confirm", field_changes={"destination": "测试地点", "departure_at": "2026-10-10T08:00:00+01:00", "return_at": "2026-10-10T18:00:00+01:00", "participants": "夹具人员", "purpose": "测试准备"})
        self.assertEqual(status, 200, detail)
        self.assertTrue(detail["artifacts"][0]["stale"])
        status, detail = self.post(detail, "actions", kind="prepare")
        self.assertEqual(status, 202)
        detail = self.completed(detail)
        self.assertEqual(detail["preparation_status"], "ready")
        artifact = detail["artifacts"][0]
        edit = {"base_version": artifact["version"], "edited_content": "人工测试修改；未正式办理"}
        status, detail = self.request("PATCH", "/api/artifacts/" + artifact["id"], edit)
        self.assertEqual(status, 200, detail)
        status, error = self.request("PATCH", "/api/artifacts/" + artifact["id"], {**edit, "edited_content": "过期正文"})
        self.assertEqual(status, 409, error)
        self.stop()
        self.start()
        detail = self.get(detail)
        saved = next(item for item in detail["artifacts"] if item["id"] == artifact["id"])
        self.assertEqual(saved["content"], edit["edited_content"])
        self.assertEqual(saved["version"], 2)
        status, content = self.request("GET", "/api/artifacts/" + artifact["id"] + "/download")
        self.assertEqual((status, content), (200, edit["edited_content"]))
        self.assertEqual(len(detail["actions"]), 2)
        self.assertTrue(all(track["status"] == "unknown" for track in detail["tracks"].values()))
        self.assertTrue(detail["activities"])

    def test_idempotency_and_optimistic_conflict(self):
        key = str(uuid.uuid4())
        detail = self.create(key=key)
        repeated = self.create(key=key)
        self.assertEqual(detail, repeated)
        status, error = self.request("POST", "/api/matters", {"goal_text": "不同内容", "domain": "travel", "source_kind": "user_text"}, key=key)
        self.assertEqual(status, 409, error)
        self.assertEqual(len(self.request("GET", "/api/worklist")[1]["items"]), 1)
        old_version = detail["version"]
        status, updated = self.post(detail, "sources", text="人工来源夹具", kind="oral_note", reported_by="测试陈述人")
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["sources"][-1]["reported_by"], "测试陈述人")
        status, error = self.post(detail, "facts/confirm", field_changes={"destination": "陈旧修改"})
        self.assertEqual(status, 409, error)
        self.assertEqual(self.get(detail)["facts"]["destination"]["value"], None)
        self.assertGreater(updated["version"], old_version)
        key = str(uuid.uuid4())
        data = {"expected_version": updated["version"], "kind": "prepare"}
        path = "/api/matters/" + detail["id"] + "/actions"
        self.assertEqual(self.request("POST", path, data, key=key)[0], 202)
        self.assertEqual(self.request("POST", path, data, key=key)[0], 202)
        self.assertEqual(len(self.completed(detail)["actions"]), 1)

    def test_pause_handoff_and_inflight_stop_fence(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(detail):
            entered.set()
            if not release.wait(4):
                raise TimeoutError("测试栅栏未释放")
            return real_prepare(detail)
        detail = self.create()
        with patch("assistant_app.server.prepare", blocked):
            try:
                status, detail = self.post(detail, "actions", kind="prepare")
                self.assertEqual(status, 202)
                self.assertTrue(entered.wait(3), "未进入后台准备")
                status, detail = self.post(self.get(detail), "control", command="pause")
                self.assertEqual(status, 200, detail)
                self.assertEqual(detail["assistant_status"], "paused")
                self.assertEqual(self.post(detail, "actions", kind="prepare")[0], 409)
            finally:
                release.set()
            time.sleep(0.25)
        detail = self.get(detail)
        self.assertEqual(detail["artifacts"], [])
        self.assertEqual(detail["actions"][0]["status"], "cancelled")
        self.assertEqual(detail["assistant_status"], "paused")
        status, detail = self.post(detail, "control", command="handoff")
        self.assertEqual(status, 200)
        self.assertEqual(self.post(detail, "actions", kind="prepare")[0], 409)
        status, detail = self.post(detail, "control", command="resume")
        self.assertEqual(status, 200)
        self.assertEqual(self.post(detail, "actions", kind="prepare")[0], 202)
        self.assertEqual(len(self.completed(detail)["artifacts"]), 1)

    def test_concurrent_fact_writes_have_one_winner(self):
        detail = self.create()
        with ThreadPoolExecutor(max_workers=2) as executor:
            requests = [executor.submit(self.post, detail, "facts/confirm", field_changes={"destination": value}) for value in ("并发甲", "并发乙")]
            results = [request.result(timeout=5) for request in requests]
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        current = self.get(detail)
        self.assertEqual(current["revision_no"], detail["revision_no"] + 1)
        winner = next(body for status, body in results if status == 200)
        self.assertEqual(current["facts"]["destination"], winner["facts"]["destination"])

    def test_restart_resumes_durable_running_preparation(self):
        detail = self.create(domain="general")
        self.stop()
        database = Path(self.temp.name) / "assistant.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE matters SET assistant_status='processing',version=version+1 WHERE id=?", (detail["id"],))
            connection.execute("INSERT INTO actions(id,matter_id,kind,status,input_revision,control_epoch,attempts,created_at) VALUES(?,?,'prepare','running',1,0,1,'2026-10-08T00:00:00+00:00')", (str(uuid.uuid4()), detail["id"]))
            connection.commit()
        finally:
            connection.close()
        self.start()
        detail = self.completed(detail)
        self.assertEqual(len(detail["artifacts"]), 1)
        self.assertEqual(detail["preparation_status"], "ready")

    def test_entry_guards_static_boundary_and_business_limits(self):
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "attacker.invalid"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session", headers={"Origin": "https://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("POST", "/api/matters", {"goal_text": "不能保存"}, headers={"X-CSRF-Token": "wrong"})[0], 403)
        for path in ("/../docs/README.md", "/%2e%2e/docs/README.md", "/../.runtime/assistant.sqlite3", "/%5c..%5cdocs%5cREADME.md"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        detail = self.create()
        for kind in ("approve", "dispatch", "deduct_meal"):
            with self.subTest(kind=kind):
                self.assertEqual(self.post(detail, "actions", kind=kind)[0], 422)
        self.assertEqual(self.post(detail, "events", event_type="approved", message="不可冒记批准")[0], 422)
        self.assertEqual(self.request("POST", "/api/matters", {"goal_text": "x" * 12001})[0], 422)
        self.assertEqual(self.request("POST", "/api/matters", {"goal_text": "x" * 131073})[0], 413)
        self.assertEqual(self.request("GET", "/api/worklist")[1]["items"][0]["id"], detail["id"])

    def test_explicit_dates_and_return_order(self):
        detail = self.create()
        self.assertEqual(self.post(detail, "facts/confirm", field_changes={"departure_at": "明天"})[0], 422)
        self.assertEqual(self.post(detail, "facts/confirm", field_changes={"departure_at": "2026-02-30"})[0], 422)
        self.assertEqual(self.post(detail, "facts/confirm", field_changes={"departure_at": "2026-10-11", "return_at": "2026-10-10"})[0], 422)
        self.assertEqual(self.post(detail, "facts/confirm", field_changes={"departure_at": "2026-10-11T08:00:00", "return_at": "2026-10-10"})[0], 422)
        self.assertEqual(self.get(detail)["revision_no"], 1)
        status, detail = self.post(detail, "facts/confirm", field_changes={"departure_at": "2026-10-10", "return_at": "2026-10-11"})
        self.assertEqual(status, 200, detail)
        self.assertEqual(detail["facts"]["departure_at"], {"value": "2026-10-10", "status": "confirmed"})
        for departure, returning in (("2026-10-10", "2026-10-11"), ("2026-10-10T08:00:00+01:00", "2026-10-10T18:00:00")):
            with self.subTest(departure=departure, returning=returning):
                status, detail = self.post(self.get(detail), "facts/confirm", field_changes={"destination": "测试地点", "participants": "夹具人员", "purpose": "测试准备", "departure_at": departure, "return_at": returning})
                self.assertEqual(status, 200, detail)
                status, detail = self.post(detail, "actions", kind="prepare")
                self.assertEqual(status, 202)
                detail = self.completed(detail)
                self.assertEqual(detail["preparation_status"], "needs_review")
                self.assertTrue(detail["waiting"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
