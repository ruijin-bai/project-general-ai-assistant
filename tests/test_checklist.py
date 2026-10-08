"""Short Store smoke of source truth, privacy and preservation."""

import json
import tempfile
import unittest
from pathlib import Path

from assistant_app.checklist import ChecklistService
from assistant_app.store import AppError, Store, dump, now, uid


class ChecklistSmoke(unittest.TestCase):
    def test_sources_gaps_owner_and_existing_calculation_preserved(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-checklist-", dir=Path(__file__).resolve().parent) as directory:
                store = Store(directory)
                service = ChecklistService(store)
                owner = other = None
                if protected:
                    store.enable_identity()
                    owner, other = ({"id": uid(), "auth_epoch": 1}, {"id": uid(), "auth_epoch": 1})
                    with store.transaction() as connection:
                        for number, actor in enumerate((owner, other)):
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'fixture')", (actor["id"], "fixture" + str(number), "隔离核对账号"))
                with store.as_actor(owner):
                    detail = store.mutate("create", None, {"goal_text": "隔离要求材料核对", "domain": "expense"}, uid())
                    detail = store.mutate("source", detail["id"], {"expected_version": detail["version"], "kind": "oral_note", "text": "原要求：本次材料须有票据来源。\n票据甲0.1，票据乙0.2。"}, uid())
                    source_id = detail["sources"][-1]["id"]
                    rows = [{"id": uid(), "label": "必要费用", "value": value, "currency": "NGN", "period": "2026-10", "source_id": source_id, "source_position": "第2行"} for value in ("0.1", "0.2")]
                    detail = store.mutate("rows", detail["id"], {"expected_version": detail["version"], "rows": rows}, uid())
                    with store.transaction() as connection:
                        facts = store._stored_facts(connection, detail)
                        facts.update(__workflow={"enabled": False, "mode": "template"}, __model_note={"value": "既有候选元信息"})
                        connection.execute("UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?", (dump(facts), detail["id"], detail["revision_no"]))
                        manual_id = uid()
                        connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (manual_id, detail["id"], "expense", "已有人工稿", "人工原文不可覆盖", "human_saved", detail["revision_no"], now()))
                    pointer = {"source_id": source_id, "start_line": 1, "end_line": 1}
                    item = {"id": uid(), "requirement_text": "本次提供必要票据", "requirement_refs": [pointer], "provided_refs": [], "check": "pending", "note": "保留未核部分"}
                    data = {"expected_version": detail["version"], "input_revision": detail["revision_no"], "base_structure_version": 0, "upserts": [item], "removed_ids": []}
                    for bad in ({**item, "requirement_refs": []}, {**item, "check": "local_source_checked"}, {**item, "requirement_refs": [{**pointer, "excerpt": "模型伪造"}]}):
                        with self.assertRaises(AppError):
                            service.save_checks(detail["id"], {**data, "upserts": [bad]}, uid())
                    key = uid()
                    saved = service.save_checks(detail["id"], data, key)
                    self.assertEqual(service.save_checks(detail["id"], data, key)["revision_no"], saved["revision_no"])
                    self.assertEqual(saved["items"][0]["requirement_excerpt"], "原要求：本次材料须有票据来源。")
                    self.assertEqual(len(saved["gaps"]), 1)
                    checked = {**item, "provided_refs": [{**pointer, "start_line": 2, "end_line": 2}], "check": "local_source_checked"}
                    saved = service.save_checks(detail["id"], {"expected_version": saved["version"], "input_revision": saved["revision_no"], "base_structure_version": saved["structure_version"], "upserts": [checked], "removed_ids": []}, uid())
                    self.assertFalse(saved["stale"])
                    self.assertEqual(saved["gaps"], [])
                    self.assertEqual(saved["items"][0]["provided_excerpt"], "票据甲0.1，票据乙0.2。")
                    artifact = service.render_checks(detail["id"], {"expected_version": saved["version"], "input_revision": saved["revision_no"], "structure_version": saved["structure_version"]}, uid())
                    self.assertIn("正式合规：未知", artifact["content"])
                    self.assertEqual(store.download(manual_id, 1), "人工原文不可覆盖")
                    current = store.detail(detail["id"])
                    self.assertEqual(current["calculation"]["groups"][0]["total"], "0.3")
                    self.assertTrue(all(track["status"] == "unknown" for track in current["tracks"].values()))
                    with store.connect() as connection:
                        latest = json.loads(connection.execute("SELECT fields FROM revisions WHERE matter_id=? ORDER BY revision_no DESC LIMIT 1", (detail["id"],)).fetchone()[0])
                        for field in ("__rows", "__rows_revision", "__workflow", "__model_note"):
                            self.assertEqual(latest[field], facts[field])
                    paused = store.mutate("control", detail["id"], {"expected_version": current["version"], "command": "pause"}, uid())
                    with self.assertRaises(AppError) as caught:
                        service.render_checks(detail["id"], {"expected_version": paused["version"], "input_revision": paused["revision_no"], "structure_version": saved["structure_version"]}, uid())
                    self.assertEqual(caught.exception.status, 409)
                if protected:
                    with store.as_actor(other), self.assertRaises(AppError) as caught:
                        service.get_checks(detail["id"])
                    self.assertEqual(caught.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
