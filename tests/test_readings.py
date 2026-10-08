"""One source-linked Decimal utility smoke in both real Store modes."""

import json
import tempfile
import unittest
from pathlib import Path

from assistant_app.readings import UtilitiesService
from assistant_app.store import AppError, Store, dump, now, uid


class ReadingsSmoke(unittest.TestCase):
    def test_decimal_conflicts_sources_and_preservation(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-readings-", dir=Path(__file__).resolve().parent) as directory:
                store, actors = Store(directory), {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'fixture')", (actor_id, name, "隔离 " + name))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}
                service = UtilitiesService(store)

                def call(method, *args, actor="owner"):
                    with store.as_actor(actors[actor]):
                        return getattr(service, method)(*args)

                def error(status, method, *args, actor="owner"):
                    with self.assertRaises(AppError) as caught:
                        call(method, *args, actor=actor)
                    self.assertEqual(caught.exception.status, status)

                def request(view, items):
                    return {"expected_version": view["version"], "input_revision": view["revision_no"], "base_structure_version": view.get("structure_version", 0), "upserts": items, "removed_ids": []}

                with store.as_actor(actors["owner"]):
                    detail = store.mutate("create", None, {"goal_text": "隔离水电核对", "domain": "utilities"}, uid())
                    detail = store.mutate("source", detail["id"], {"expected_version": detail["version"], "kind": "oral_note", "text": "仪表M1：初次0.1，次日0.4；后续0.2待核。\n同仪表同日0.9冲突，水表单位m3：20。"}, uid())
                    source_id = next(source["id"] for source in detail["sources"] if source["kind"] == "oral_note")
                    manual_id = uid()
                    with store.transaction() as connection:
                        facts = store._stored_facts(connection, detail)
                        facts.update(__workflow={"enabled": False, "mode": "template"}, __custom={"保留": "既有人工元数据"})
                        connection.execute("UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?", (dump(facts), detail["id"], detail["revision_no"]))
                        connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (manual_id, detail["id"], "utilities", "人工稿", "人工读数原稿保留", "human_saved", detail["revision_no"], now()))
                first = {"id": uid(), "meter_ref": "仪表M1", "unit": "kWh", "reading": "0.1", "observed_at": "2026-10-08T10:00:00+01:00", "refs": [{"source_id": source_id, "start_line": 1, "end_line": 1}], "confirmation": "local_checked", "note": "本地核对来源"}
                second = {**first, "id": uid(), "reading": "0.4", "observed_at": "2026-10-09T11:00:00+02:00"}
                for bad in ({**first, "reading": "-1"}, {**first, "reading": "NaN"}, {**first, "observed_at": "2026-10-08T10:00:00"}, {**first, "refs": [{"source_id": source_id, "start_line": 0, "end_line": 1}]}, {**first, "refs": [{"source_id": source_id, "start_line": 1, "end_line": 1, "excerpt": "伪造"}]}):
                    error(422, "save_readings", detail["id"], request(detail, [bad]), uid())
                saved = call("save_readings", detail["id"], request(detail, [first, second]), uid())
                self.assertEqual(saved["intervals"][0]["difference"], "0.3")
                self.assertEqual(saved["items"][0]["refs"][0]["excerpt"], "仪表M1：初次0.1，次日0.4；后续0.2待核。")
                error(422, "save_readings", detail["id"], request(saved, [{**second, "id": uid(), "observed_at": "2026-10-09T09:00:00+00:00"}]), uid())
                reversed_reading = {**first, "id": uid(), "reading": "0.2", "observed_at": "2026-10-10T10:00:00+01:00"}
                other_unit = {**first, "id": uid(), "unit": "m3", "reading": "20"}
                candidate = {**first, "id": uid(), "reading": "0.5", "observed_at": "2026-10-11T10:00:00+01:00", "confirmation": "candidate"}
                saved = call("save_readings", detail["id"], request(saved, [reversed_reading, other_unit, candidate]), uid())
                self.assertEqual(len(saved["items"]), 5)
                self.assertEqual(len(saved["intervals"]), 1)
                self.assertEqual(saved["intervals"][0]["unit"], "kWh")
                self.assertIn("reset_or_error_unknown", [issue["kind"] for issue in saved["issues"]])
                self.assertIn("confirmation_pending", [issue["kind"] for issue in saved["issues"]])
                rendered = call("render_readings", detail["id"], {"expected_version": saved["version"], "input_revision": saved["revision_no"], "structure_version": saved["structure_version"]}, uid())
                self.assertIn("0.3", rendered["content"])
                saved = call("get_readings", detail["id"])
                conflict = {**second, "id": uid(), "reading": "0.9"}
                saved = call("save_readings", detail["id"], request(saved, [conflict]), uid())
                self.assertEqual(saved["intervals"], [])
                self.assertIn("conflicting_same_instant", [issue["kind"] for issue in saved["issues"]])
                if protected:
                    error(404, "get_readings", detail["id"], actor="other")
                with store.as_actor(actors["owner"]):
                    current = store.detail(detail["id"])
                    self.assertEqual(store.download(manual_id, 1), "人工读数原稿保留")
                    self.assertEqual(store.download(rendered["artifact_id"], 1), rendered["content"])
                    self.assertTrue(all(track["status"] == "unknown" for track in current["tracks"].values()))
                    with store.connect() as connection:
                        latest = json.loads(connection.execute("SELECT fields FROM revisions WHERE matter_id=? ORDER BY revision_no DESC LIMIT 1", (detail["id"],)).fetchone()[0])
                        self.assertEqual(latest["__workflow"], facts["__workflow"])
                        self.assertEqual(latest["__custom"], facts["__custom"])
                    paused = store.mutate("control", detail["id"], {"expected_version": saved["version"], "command": "pause"}, uid())
                error(409, "render_readings", detail["id"], {"expected_version": paused["version"], "input_revision": paused["revision_no"], "structure_version": saved["structure_version"]}, uid())
                with store.as_actor(actors["owner"]):
                    changed = store.mutate("source", detail["id"], {"expected_version": paused["version"], "kind": "oral_note", "text": "新增记录，旧差值须重核"}, uid())
                self.assertTrue(call("get_readings", detail["id"])["stale"])
                self.assertEqual(len(call("get_readings", detail["id"])["items"]), 6)


if __name__ == "__main__":
    unittest.main()
