"""One combined real Store smoke of bounded, explicit, private source pointers."""

import tempfile
import unittest
from pathlib import Path

from assistant_app.source_impact import SourceImpactService
from assistant_app.source_lifecycle import SourceLifecycleService
from assistant_app.store import AppError, Store, dump, now, uid


class SourceImpactSmoke(unittest.TestCase):
    def test_two_modes_explicit_refs_history_limits_and_read_only(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-impact-", dir=Path(__file__).resolve().parent) as directory:
                store, actors = Store(directory), {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'fixture')", (actor_id, name, "隔离 " + name))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}
                service = SourceImpactService(store)
                with store.as_actor(actors["owner"]):
                    detail = store.mutate("create", None, {"goal_text": "隔离来源关系", "domain": "expense"}, uid())
                    detail = store.mutate("source", detail["id"], {"expected_version": detail["version"], "kind": "user_text", "text": "\n".join("真实夹具原文第" + str(number) + "行" for number in range(1, 11))}, uid())
                    source_id = next(source["id"] for source in detail["sources"] if source["text"].startswith("真实夹具"))
                    other = store.mutate("create", None, {"goal_text": "另一事项隔离", "domain": "general"}, uid())
                    foreign_id = other["sources"][0]["id"]
                    revision, manual_id = detail["revision_no"], uid()
                    ref = {"source_id": source_id, "start_line": 1, "end_line": 2}
                    with store.transaction() as connection:
                        facts = store._stored_facts(connection, detail)
                        facts.update({
                            "__meeting": {"input_revision": revision, "items": [{"id": "discussion", "text": "会议原述" * 100, "refs": [ref]}]},
                            "__document": {"input_revision": revision, "sections": [{"id": "section", "heading": "人工段落", "body": "文字保留", "refs": [ref]}, {"id": "untraced", "body": "无明确位置，不能猜", "refs": []}]},
                            "__checks": {"input_revision": revision, "items": [{"id": "requirement", "requirement_text": "核对材料", "requirement_refs": [ref], "provided_refs": [{**ref, "start_line": 3, "end_line": 3}]}]},
                            "__readings": {"input_revision": revision, "items": [{"id": "reading", "meter_ref": "水表A", "refs": [ref]}]},
                            "__materials": {"input_revision": revision, "items": [{"id": "material", "label": "已提供材料", "source_id": source_id, "source_position": "第4–5行"}, {"id": "old-material", "label": "旧位置原话", "source_id": source_id, "source_position": "原附件页脚"}]},
                            "__rows": [{"id": "row", "label": "本人明细", "value": "0.3", "currency": "NGN", "period": "本次", "source_id": source_id, "source_position": "本人原表第二行"}], "__rows_revision": revision - 1,
                            "__fact_candidates": {"input_revision": revision, "items": {"period": {"value": "本次", "refs": [ref]}}},
                            "__unrelated": {"refs": [ref], "must_not_parse": "不是模块引用"},
                            "__workflow": {"enabled": False},
                        })
                        connection.execute("UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?", (dump(facts), detail["id"], revision))
                        for artifact_version, text in ((1, "第一版人工文字"), (2, "第二版人工文字，不能解析为精确原文引用")):
                            connection.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)", (manual_id, artifact_version, detail["id"], "expense", "人工稿", text, "human_saved", revision - 1, now()))
                    before = store.detail(detail["id"])
                    view = service.get_impact(detail["id"])
                    kinds = {item["kind"] for item in view["components"]}
                    self.assertEqual(kinds, {"meeting_item", "document_section", "checklist_item", "utility_reading", "leave_material", "calculation_row", "fact_candidate"})
                    source = next(item for item in view["sources"] if item["id"] == source_id)
                    self.assertEqual(source["reference_count"], 9)
                    self.assertFalse(view["limited"])
                    self.assertTrue(all(len(item["label"]) <= 160 for item in view["components"]))
                    row = next(item for item in view["components"] if item["kind"] == "calculation_row")
                    self.assertTrue(row["stale"])
                    self.assertIsNone(row["refs"][0]["start_line"])
                    self.assertEqual(row["refs"][0]["source_position"], "本人原表第二行")
                    legacy = next(item for item in view["components"] if item["id"] == "old-material")
                    self.assertIsNone(legacy["refs"][0]["start_line"])
                    self.assertEqual(legacy["refs"][0]["source_position"], "原附件页脚")
                    self.assertIn("nottraced", legacy["scope"])
                    untraced = next(item for item in view["components"] if item["id"] == "untraced")
                    self.assertEqual(untraced["refs"], [])
                    self.assertIn("nottraced", untraced["scope"])
                    self.assertEqual(len(view["artifacts"]), 1)
                    self.assertEqual(view["artifacts"][0]["version"], 2)
                    self.assertEqual(view["artifacts"][0]["dependency_scope"], "whole_input_revision")
                    self.assertTrue(view["artifacts"][0]["stale"])
                    self.assertEqual(store.detail(detail["id"]), before)
                    self.assertEqual(store.download(manual_id, 2), "第二版人工文字，不能解析为精确原文引用")
                    changed = SourceLifecycleService(store).set_source_state(detail["id"], source_id, {"expected_version": before["version"], "input_revision": revision, "state": "withheld", "replacement_source_id": None, "note": "本人暂不用于新准备"}, uid())
                    history = service.get_impact(detail["id"])
                    self.assertEqual(history["revision_no"], changed["revision_no"])
                    self.assertTrue(all(item["stale"] for item in history["components"]))
                    self.assertTrue(all(item["source_issues"] == [{"source_id": source_id, "state": "withheld"}] for item in history["components"] if item["refs"]))
                    self.assertEqual(store.detail(detail["id"])["version"], changed["version"])
                    # Valid module line ranges are checked, arbitrary and cross-matter pointers are never invented.
                    with store.transaction() as connection:
                        current = store._matter(connection, detail["id"])
                        raw = store._stored_facts(connection, current)
                        raw["__document"]["sections"].append({"id": "bad-range", "body": "未追踪", "refs": [{**ref, "end_line": 99}, {**ref, "source_id": foreign_id}]})
                        raw["__checks"]["items"] = [{"id": "limit-" + str(number), "requirement_text": "有明确位置", "requirement_refs": [{"source_id": source_id, "start_line": line, "end_line": line} for line in range(1, 11)], "provided_refs": [{"source_id": source_id, "start_line": line, "end_line": line} for line in range(1, 11)]} for number in range(30)]
                        connection.execute("UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?", (dump(raw), detail["id"], current["revision_no"]))
                        for number in range(31):
                            connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (uid(), detail["id"], "expense", "保留人工稿" + str(number), "纯文字", "human_saved", revision, now()))
                    bounded_before = store.detail(detail["id"])
                    bounded = service.get_impact(detail["id"])
                    self.assertTrue(bounded["limited"])
                    self.assertEqual(sum(len(item["refs"]) for item in bounded["components"]), 500)
                    self.assertEqual(sum(item["reference_count"] for item in bounded["sources"]), 500)
                    self.assertEqual(len(bounded["artifacts"]), 30)
                    self.assertNotIn(foreign_id, {item["id"] for item in bounded["sources"]})
                    self.assertEqual(next(item for item in bounded["components"] if item["id"] == "bad-range")["refs"], [])
                    self.assertEqual(store.detail(detail["id"]), bounded_before)
                if protected:
                    with store.as_actor(actors["other"]), self.assertRaises(AppError) as caught:
                        service.get_impact(detail["id"])
                    self.assertEqual(caught.exception.status, 404)
                    with store.transaction() as connection:
                        connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (actors["owner"]["id"],))
                    with store.as_actor(actors["owner"]), self.assertRaises(AppError) as caught:
                        service.get_impact(detail["id"])
                    self.assertEqual(caught.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
