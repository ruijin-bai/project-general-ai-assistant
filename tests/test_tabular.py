"""One real Store smoke of literal CSV/TSV candidates and private sources."""

import tempfile
import unittest
from pathlib import Path

from assistant_app.calculations import validate_rows
from assistant_app.source_lifecycle import SourceLifecycleService
from assistant_app.store import AppError, Store, uid
from assistant_app.tabular import TabularService


class TabularSmoke(unittest.TestCase):
    def test_two_modes_csv_tsv_positions_validation_and_read_only(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-tabular-", dir=Path(__file__).resolve().parent) as directory:
                store, actors = Store(directory), {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'fixture')", (actor_id, name, "隔离 " + name))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}
                service = TabularService(store)

                def create(domain):
                    with store.as_actor(actors["owner"]):
                        return store.mutate("create", None, {"goal_text": "隔离原文表格", "domain": domain}, uid())

                def source(matter_id, text):
                    with store.as_actor(actors["owner"]):
                        detail = store.detail(matter_id)
                        detail = store.mutate("source", matter_id, {"expected_version": detail["version"], "kind": "excel_excerpt", "text": text}, uid())
                        return detail, next(item["id"] for item in detail["sources"] if item["text"] == text.strip())

                def get(matter_id, source_id, actor="owner"):
                    with store.as_actor(actors[actor]):
                        return service.get_candidates(matter_id, source_id)

                def error(status, matter_id, source_id, actor="owner"):
                    with self.assertRaises(AppError) as caught:
                        get(matter_id, source_id, actor)
                    self.assertEqual(caught.exception.status, status)

                expense = create("expense")
                csv_text = '\n\n费用名称,金额,币种,期间\n"交通,\n复核",0.1,NGN,2026-10\n餐费,0.2,NGN,2026-10\n\n缺金额,,NGN,2026-10\n未知币种,1,待核,2026-10\n冲销,-0.3,NGN,2026-10\n\n'
                before, csv_id = source(expense["id"], csv_text)
                candidates = get(expense["id"], csv_id)
                self.assertEqual(len(candidates["rows"]), 3)
                self.assertEqual(len(candidates["issues"]), 2)
                first = candidates["rows"][0]
                self.assertEqual((first["start_line"], first["end_line"]), (2, 3))
                self.assertEqual(first["row"]["source_position"], "第2–3行")
                self.assertEqual(first["row"]["label"], "交通,\n复核")
                self.assertIn('"交通,\n复核",0.1', first["excerpt"])
                self.assertEqual(candidates["rows"][-1]["row"]["value"], "-0.3")
                self.assertEqual(get(expense["id"], csv_id)["rows"], candidates["rows"])
                self.assertEqual(validate_rows("expense", [item["row"] for item in candidates["rows"]], [{"id": csv_id}]), [item["row"] for item in candidates["rows"]])
                with store.as_actor(actors["owner"]):
                    after = store.detail(expense["id"])
                    self.assertEqual(after["version"], before["version"])
                    self.assertEqual(after["revision_no"], before["revision_no"])
                    self.assertEqual(after["rows"], [])
                    self.assertEqual(after["activities"], before["activities"])
                inventory = create("inventory")
                _, tsv_id = source(inventory["id"], "物品\t数量\t单位\t期间\n苹果\t0.1\tkg\t2026-10\n螺丝\t0.2\t件\t2026-10\n坏量\tNaN\t件\t2026-10\n未知单位\t2\t未知\t2026-10")
                stock = get(inventory["id"], tsv_id)
                self.assertEqual(stock["columns"], ["label", "value", "unit", "period"])
                self.assertEqual(len(stock["rows"]), 2)
                self.assertEqual(len(stock["issues"]), 2)
                error(404, expense["id"], tsv_id)
                if protected:
                    error(404, expense["id"], csv_id, actor="other")
                for header in ("label,value,currency,currency\nx,1,NGN,NGN", "label,value,currency,period,extra\nx,1,NGN,2026,ignored"):
                    _, bad_id = source(expense["id"], header)
                    invalid = get(expense["id"], bad_id)
                    self.assertEqual(invalid["rows"], [])
                    self.assertEqual(invalid["issues"][0]["kind"], "invalid_header")
                _, excessive_id = source(expense["id"], "label,value,currency,period\n" + "\n".join("x,1,NGN,2026" for _ in range(201)))
                excessive = get(expense["id"], excessive_id)
                self.assertEqual(excessive["rows"], [])
                self.assertEqual(excessive["issues"][-1]["kind"], "record_limit")
                with store.as_actor(actors["owner"]):
                    current = store.detail(expense["id"])
                    SourceLifecycleService(store).set_source_state(expense["id"], csv_id, {"expected_version": current["version"], "input_revision": current["revision_no"], "state": "superseded", "replacement_source_id": excessive_id, "note": "本次已另给来源"}, uid())
                error(409, expense["id"], csv_id)
                with store.as_actor(actors["owner"]):
                    current = store.detail(expense["id"])
                    SourceLifecycleService(store).set_source_state(expense["id"], excessive_id, {"expected_version": current["version"], "input_revision": current["revision_no"], "state": "withheld", "replacement_source_id": None, "note": "本次暂不准备"}, uid())
                error(409, expense["id"], csv_id)


if __name__ == "__main__":
    unittest.main()
