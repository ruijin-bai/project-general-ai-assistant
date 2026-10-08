"""Actual Store, synthetic owner accounts, source excerpts, and version fences."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from assistant_app.menu import MENU_SCHEMA
from assistant_app.structured import StructuredService
from assistant_app.store import AppError, Store, dump, uid


class StructuredContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-structured-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.enable_identity(MENU_SCHEMA)
        self.service = StructuredService(self.store)
        self.actors = {}
        with self.store.transaction() as connection:
            for name in ("owner", "other"):
                actor_id = uid()
                connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'isolated-fixture')",
                                   (actor_id, name, "隔离结构账号 " + name))
                self.actors[name] = {"id": actor_id, "auth_epoch": 1}

    def call(self, method, *args, actor="owner", **kwargs):
        with self.store.as_actor(self.actors[actor]):
            return getattr(self.service, method)(*args, **kwargs)

    def error(self, status, method, *args, actor="owner", **kwargs):
        with self.assertRaises(AppError) as caught:
            self.call(method, *args, actor=actor, **kwargs)
        self.assertEqual(caught.exception.status, status, caught.exception.payload)

    def matter(self, domain="meeting", text=None):
        source_text = text or "讨论：通风情况\n提议：添置风扇\n决定：先核原记录\n分歧：有人反对立即采购\n行动：本人检查，截止2026-10-09\n行动：张工尽快处理\n行动：本人核对，截止2026-10-10T00:30:00+01:00"
        with self.store.as_actor(self.actors["owner"]):
            detail = self.store.mutate("create", None, {"goal_text": "隔离工程结构准备", "domain": domain}, uid())
            detail = self.store.mutate("source", detail["id"], {"expected_version": detail["version"], "kind": "oral_note", "text": source_text, "reported_by": "原始工程记录者"}, uid())
            source_id = detail["sources"][-1]["id"]
        return detail, source_id

    def item(self, source_id, line=5, kind="action", owner=True, deadline=None):
        return {"id": uid(), "kind": kind, "text": "工程记录中的" + kind, "refs": [{"source_id": source_id, "start_line": line, "end_line": line}],
                "confirmation": "local_checked", "responsible_text": "本人" if owner else "张工原述", "responsible_account_id": self.actors["owner"]["id"] if owner else None,
                "deadline_text": "明示期限" if deadline else "尽快，缺少明确日期", "deadline": deadline or {"kind": "unknown", "value": None}}

    def save_data(self, detail, upserts, structure_version=0, removed=None):
        return {"expected_version": detail["version"], "input_revision": detail.get("revision_no"), "base_structure_version": structure_version,
                "upserts": upserts, "removed_ids": removed or []}

    def progress(self, detail, item, command, **extra):
        return self.call("meeting_progress", detail["id"], item["id"], {"expected_version": detail["version"], "item_revision": item["item_revision"],
                          "command": command, "note": "实际工程夹具说明", **extra}, uid())

    def test_meeting_kinds_real_excerpts_strict_refs_and_incremental_retention(self):
        matter, source_id = self.matter()
        items = [self.item(source_id, line, kind, owner=False) for line, kind in enumerate(("discussion", "proposal", "decision", "dissent", "action"), 1)]
        items[2]["confirmation"] = "candidate"
        saved = self.call("save_meeting_structure", matter["id"], self.save_data(matter, items), uid())
        self.assertFalse(saved["stale"])
        self.assertEqual(saved["input_revision"], saved["revision_no"])
        self.assertEqual([item["kind"] for item in saved["items"]], ["discussion", "proposal", "decision", "dissent", "action"])
        self.assertEqual(saved["items"][1]["refs"][0]["excerpt"], "提议：添置风扇")
        self.assertEqual(saved["items"][4]["deadline_hint"]["overdue"], None)
        new_item = self.item(source_id, 6, owner=False)
        updated = self.call("save_meeting_structure", matter["id"], self.save_data(saved, [new_item], saved["structure_version"]), uid())
        self.assertEqual(len(updated["items"]), 6)
        self.assertEqual(updated["items"][0]["id"], saved["items"][0]["id"])
        foreign, foreign_source = self.matter()
        for refs in ([{"source_id": foreign_source, "start_line": 1, "end_line": 1}],
                     [{"source_id": source_id, "start_line": 0, "end_line": 1}],
                     [{"source_id": source_id, "start_line": True, "end_line": 1}],
                     [{"source_id": source_id, "start_line": 1, "end_line": 8}],
                     [{"source_id": source_id, "start_line": 1, "end_line": 1, "excerpt": "伪造原文"}]):
            bad = {**self.item(source_id), "refs": refs}
            self.error(422, "save_meeting_structure", matter["id"], self.save_data(updated, [bad], updated["structure_version"]), uid())
        self.error(422, "save_meeting_structure", foreign["id"], self.save_data(foreign, [{**items[0], "refs": [{"source_id": foreign_source, "start_line": 1, "end_line": 1}]}]), uid())
        rendered = self.call("meeting_render", matter["id"], {"expected_version": updated["version"], "input_revision": updated["revision_no"], "structure_version": updated["structure_version"]}, uid())
        self.assertIn("待核决定候选", rendered["content"])
        self.assertIn("原记录未提供已核明确决定", rendered["content"])
        self.assertIn("## 提议", rendered["content"])
        self.assertIn("## 分歧", rendered["content"])

    def test_action_self_transcribed_date_timezone_and_item_revision_fences(self):
        matter, source_id = self.matter()
        own = self.item(source_id, 5, deadline={"kind": "date", "value": "2026-10-09", "confirmed": True})
        other = self.item(source_id, 6, owner=False)
        timed = self.item(source_id, 7, deadline={"kind": "datetime", "value": "2026-10-10T00:30:00+01:00", "confirmed": True})
        saved = self.call("save_meeting_structure", matter["id"], self.save_data(matter, [own, other, timed]), uid())
        same_day = self.call("get_meeting_structure", matter["id"], as_of_date="2026-10-09", display_timezone="+01:00")
        self.assertFalse(same_day["items"][0]["deadline_hint"]["overdue"])
        later = self.call("get_meeting_structure", matter["id"], as_of_date="2026-10-10", display_timezone="+01:00", as_of_datetime="2026-10-09T23:45:00+00:00")
        self.assertTrue(later["items"][0]["deadline_hint"]["overdue"])
        self.assertTrue(later["items"][2]["deadline_hint"]["overdue"])
        self.assertIsNone(later["items"][1]["deadline_hint"]["overdue"])
        self.assertIsNone(self.call("get_meeting_structure", matter["id"], as_of_date="2026-10-10")["items"][0]["deadline_hint"]["overdue"])
        self.error(422, "get_meeting_structure", matter["id"], as_of_datetime="2026-10-10T10:00:00")
        bad_deadline = {**self.item(source_id, 7), "deadline": {"kind": "datetime", "value": "2026-10-10T00:30:00", "confirmed": True}}
        self.error(422, "save_meeting_structure", matter["id"], self.save_data(saved, [bad_deadline], 1), uid())
        self.error(422, "save_meeting_structure", matter["id"], self.save_data(saved, [{**self.item(source_id), "deadline": {"kind": "date", "value": "2030-01-01", "confirmed": True}}], 1), uid())
        old_revision = saved["revision_no"]
        saved = self.progress(saved, saved["items"][0], "self_accept")
        self.assertEqual(saved["revision_no"], old_revision)
        self.assertFalse(saved["stale"])
        self.error(403, "meeting_progress", matter["id"], other["id"], {"expected_version": saved["version"], "item_revision": 1, "command": "self_accept", "note": "不能冒他人"}, uid())
        saved = self.progress(saved, saved["items"][1], "reported_progress", reported_by="张工实际原述")
        self.assertEqual(saved["items"][1]["progress"]["reported_progress"][0]["evidence_class"], "transcribed_statement")
        changed = {**own, "responsible_text": "改为他人原述", "responsible_account_id": None}
        saved = self.call("save_meeting_structure", matter["id"], self.save_data(saved, [changed], saved["structure_version"]), uid())
        self.assertEqual(saved["items"][0]["item_revision"], 2)
        self.assertEqual(saved["items"][1]["item_revision"], 1)
        self.assertTrue(saved["items"][0]["progress"]["events"][0]["old_item_scope"])
        self.assertEqual(saved["items"][0]["progress"]["acceptance"], "unknown")
        self.error(409, "meeting_progress", matter["id"], own["id"], {"expected_version": saved["version"], "item_revision": 1, "command": "result_note", "note": "陈旧范围"}, uid())
        removed = self.call("save_meeting_structure", matter["id"], self.save_data(saved, [], saved["structure_version"], [own["id"]]), uid())
        restored = self.call("save_meeting_structure", matter["id"], self.save_data(removed, [own], removed["structure_version"]), uid())
        action = next(item for item in restored["items"] if item["id"] == own["id"])
        self.assertEqual(action["item_revision"], 3)
        self.assertEqual(action["progress"]["acceptance"], "unknown")
        self.assertTrue(all(event["old_item_scope"] for event in action["progress"]["events"]))

    def test_document_render_new_artifacts_preserves_human_edits_and_meta(self):
        matter, source_id = self.matter("document", "第一段事实：工程原始底稿。\n第二段事实：需要人工核对，不增正式签发。")
        with self.store.transaction() as connection:
            fields = self.store._stored_facts(connection, matter)
            fields.update(__workflow={"enabled": False, "mode": "template"}, __rows=[{"fixture_metadata": "保全原值，不参与本域计算"}], __custom={"value": "既有元数据"})
            connection.execute("UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?", (dump(fields), matter["id"], matter["revision_no"]))
        sections = [{"id": uid(), "heading": "事实说明", "body": "中文正文第一段\n第二行。", "refs": [{"source_id": source_id, "start_line": 1, "end_line": 1}], "confirmation": "local_checked"},
                    {"id": uid(), "heading": "待核事项", "body": "未经正式签发的准备正文。", "refs": [{"source_id": source_id, "start_line": 2, "end_line": 2}], "confirmation": "candidate"}]
        saved = self.call("save_document_structure", matter["id"], self.save_data(matter, sections), uid())
        render = {"expected_version": saved["version"], "input_revision": saved["revision_no"], "structure_version": saved["structure_version"]}
        first = self.call("document_render", matter["id"], render, uid())
        self.assertIn("中文正文第一段", first["content"])
        self.assertIn("通用准备稿", first["content"])
        with self.store.transaction() as connection:
            connection.execute("INSERT INTO artifacts VALUES(?,2,?,?,?,?,?,?,?)", (first["artifact_id"], matter["id"], first["type"], "独立人工稿", "人工修改不能覆盖", "human_saved", saved["revision_no"], "2026-10-08T00:00:00+00:00"))
        current = self.call("get_document_structure", matter["id"])
        second = self.call("document_render", matter["id"], {**render, "expected_version": current["version"]}, uid())
        self.assertNotEqual(first["artifact_id"], second["artifact_id"])
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT content FROM artifacts WHERE id=? AND version=2", (first["artifact_id"],)).fetchone()[0], "人工修改不能覆盖")
            latest = json.loads(connection.execute("SELECT fields FROM revisions WHERE matter_id=? ORDER BY revision_no DESC LIMIT 1", (matter["id"],)).fetchone()[0])
            self.assertEqual(latest["__rows"], fields["__rows"])
            self.assertEqual(latest["__workflow"], fields["__workflow"])
            self.assertEqual(latest["__custom"], fields["__custom"])
            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())
        with self.store.as_actor(self.actors["owner"]):
            self.assertEqual(self.store.download(second["artifact_id"]), second["content"])
        with self.store.as_actor(self.actors["other"]), self.assertRaises(AppError) as denied:
            self.store.download(second["artifact_id"])
        self.assertEqual(denied.exception.status, 404)

    def test_owner_isolation_progress_pause_source_stale_restart_and_limits(self):
        matter, source_id = self.matter()
        item = self.item(source_id)
        data, key = self.save_data(matter, [item]), uid()
        saved = self.call("save_meeting_structure", matter["id"], data, key)
        self.assertEqual(self.call("save_meeting_structure", matter["id"], data, key)["structure_version"], 1)
        self.error(404, "save_meeting_structure", matter["id"], data, key, actor="other")
        self.error(404, "get_meeting_structure", matter["id"], actor="other")
        self.error(404, "meeting_progress", matter["id"], item["id"], {"expected_version": saved["version"], "item_revision": 1, "command": "result_note", "note": "越权"}, uid(), actor="other")
        self.error(409, "save_meeting_structure", matter["id"], {**data, "expected_version": saved["version"]}, uid())
        with self.store.as_actor(self.actors["owner"]):
            detail = self.store.detail(matter["id"])
            paused = self.store.mutate("control", matter["id"], {"expected_version": detail["version"], "command": "pause"}, uid())
        progressed = self.progress(paused, saved["items"][0], "self_claimed_done")
        self.assertEqual(progressed["items"][0]["progress"]["execution"], "claimed_done")
        self.assertFalse(progressed["stale"])
        self.error(409, "meeting_render", matter["id"], {"expected_version": progressed["version"], "input_revision": progressed["revision_no"], "structure_version": 1}, uid())
        with self.store.as_actor(self.actors["owner"]):
            detail = self.store.detail(matter["id"])
            resumed = self.store.mutate("control", matter["id"], {"expected_version": detail["version"], "command": "resume"}, uid())
            changed = self.store.mutate("source", matter["id"], {"expected_version": resumed["version"], "kind": "oral_note", "text": "新来源须核原结构依据"}, uid())
        stale = self.call("get_meeting_structure", matter["id"], as_of_date="2026-12-01", display_timezone="+01:00")
        self.assertTrue(stale["stale"])
        self.assertIsNone(stale["items"][0]["deadline_hint"]["overdue"])
        self.error(409, "meeting_render", matter["id"], {"expected_version": changed["version"], "input_revision": changed["revision_no"], "structure_version": 1}, uid())
        refreshed = self.call("save_meeting_structure", matter["id"], self.save_data(changed, [item], 1), uid())
        self.assertFalse(refreshed["stale"])
        restarted = StructuredService(Store(self.temp.name))
        with restarted.store.as_actor(self.actors["owner"]):
            restored = restarted.get_meeting_structure(matter["id"])
            self.assertEqual(restored["items"][0]["id"], item["id"])
            self.assertEqual(len(restored["items"][0]["progress"]["events"]), 1)
        self.error(422, "save_meeting_structure", matter["id"], self.save_data(refreshed, [item] * 101, 2), uid())
        self.error(422, "save_meeting_structure", matter["id"], self.save_data(refreshed, [], 2, ["missing-id"]), uid())


class LocalPreviewStructure(unittest.TestCase):
    def test_default_six_table_meeting_progress_and_document_render(self):
        with tempfile.TemporaryDirectory(prefix="qa-structured-local-", dir=Path(__file__).resolve().parent) as directory:
            store = Store(directory)
            service = StructuredService(store)
            self.assertFalse(store.requires_auth)
            with store.connect() as connection:
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='accounts'").fetchone())
            for domain in ("meeting", "document"):
                detail = store.mutate("create", None, {"goal_text": "隔离本机结构准备", "domain": domain}, uid())
                detail = store.mutate("source", detail["id"], {"expected_version": detail["version"], "kind": "oral_note", "text": "本人核对本机原记录。"}, uid())
                item = {"id": uid(), "refs": [{"source_id": detail["sources"][-1]["id"], "start_line": 1, "end_line": 1}], "confirmation": "local_checked"}
                if domain == "meeting":
                    item.update(kind="action", text="核对原记录", responsible_text="本机预览操作者", responsible_account_id="local-preview", deadline_text=None, deadline={"kind": "unknown", "value": None})
                else:
                    item.update(heading="准备正文", body="本机原文核对结果，未正式签发。")
                saved = getattr(service, "save_" + domain + "_structure")(detail["id"], {"expected_version": detail["version"], "input_revision": detail["revision_no"], "base_structure_version": 0, "upserts": [item], "removed_ids": []}, uid())
                self.assertFalse(saved["stale"])
                if domain == "meeting":
                    revision = saved["revision_no"]
                    saved = service.meeting_progress(detail["id"], item["id"], {"expected_version": saved["version"], "item_revision": 1, "command": "self_accept", "note": "隔离本机本人声明"}, uid())
                    self.assertEqual(saved["revision_no"], revision)
                    self.assertEqual(saved["items"][0]["progress"]["acceptance"], "self_accepted")
                    self.assertEqual(saved["items"][0]["progress"]["events"][0]["actor_id"], "local-preview")
                rendered = getattr(service, domain + "_render")(detail["id"], {"expected_version": saved["version"], "input_revision": saved["revision_no"], "structure_version": saved["structure_version"]}, uid())
                self.assertIn("本机", rendered["content"])
                with store.connect() as connection:
                    self.assertEqual(connection.execute("SELECT owner FROM matters WHERE id=?", (detail["id"],)).fetchone()[0], "local-preview")
                    self.assertEqual(connection.execute("SELECT confirmed_by FROM revisions WHERE matter_id=? ORDER BY revision_no DESC LIMIT 1", (detail["id"],)).fetchone()[0], "local-preview")
            self.assertFalse(store.requires_auth)
            with store.connect() as connection:
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='accounts'").fetchone())


if __name__ == "__main__":
    unittest.main()
