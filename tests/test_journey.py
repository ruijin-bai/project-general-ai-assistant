"""One combined local/identity Store smoke; fixtures are not real business evidence."""

import tempfile
import unittest
from pathlib import Path

from assistant_app.journey import JourneyService, SEMANTICS
from assistant_app.store import AppError, Store, uid


class JourneySmoke(unittest.TestCase):
    def test_combined_preparation_time_owner_and_history(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-journey-", dir=Path(__file__).resolve().parent) as directory:
                store = Store(directory)
                service = JourneyService(store)
                actors = {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'fixture')", (actor_id, name, "隔离 " + name))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}

                def call(method, *args, actor="owner"):
                    with store.as_actor(actors[actor]):
                        return getattr(service, method)(*args)

                def error(status, method, *args, actor="owner"):
                    with self.assertRaises(AppError) as caught:
                        call(method, *args, actor=actor)
                    self.assertEqual(caught.exception.status, status)

                def create(domain, actor="owner"):
                    with store.as_actor(actors[actor]):
                        matter = store.mutate("create", None, {"goal_text": "隔离联动准备", "domain": domain}, uid())
                        return store.mutate("source", matter["id"], {"expected_version": matter["version"], "kind": "oral_note", "text": "本次必要材料引用原文\n国内证明尚未提供"}, uid())

                def plan(view, changes):
                    return {"expected_version": view["version"], "input_revision": view["revision_no"], "plan_semantics": dict(SEMANTICS), "field_changes": {key: {"value": value, "status": "confirmed"} for key, value in changes.items()}}

                travel = create("travel")
                error(422, "save_plan", travel["id"], plan(travel, {"departure_at": "2026-10-08T10:00:00+01:00", "return_at": "2026-10-08T09:30:00+01:00"}), uid())
                error(422, "save_plan", travel["id"], plan(travel, {"departure_at": "2026-10-08T10:00:00"}), uid())
                # UTC 09:00 -> 09:30 despite the return local clock showing an earlier hour.
                travel = call("save_plan", travel["id"], plan(travel, {"destination": "隔离地点", "participants": "本次明确人员原述", "purpose": "准备核对", "departure_at": "2026-10-08T11:00:00+02:00", "return_at": "2026-10-08T10:30:00+01:00"}), uid())
                self.assertEqual(travel["journey"]["time_assessment"], "explicit_instants")
                meal = {"id": uid(), "person_ref": "本次明确人员原述", "meal_date": "2026-10-08", "meal_slot": "午餐", "request": "keep", "note": "人工保餐例外"}
                meals_data = {"expected_version": travel["version"], "input_revision": travel["revision_no"], "upserts": [meal], "removed_ids": []}
                meal_key = uid()
                travel = call("save_meals", travel["id"], meals_data, meal_key)
                self.assertEqual(call("save_meals", travel["id"], meals_data, meal_key)["revision_no"], travel["revision_no"])
                error(422, "save_meals", travel["id"], {**meals_data, "expected_version": travel["version"], "input_revision": travel["revision_no"], "upserts": [{**meal, "id": uid()}]}, uid())
                movement = {"expected_version": travel["version"], "kind": "departed", "occurred_at": "2026-10-08T09:00:00+00:00", "note": "本人实际声明，批准另核"}
                movement_key, revision = uid(), travel["revision_no"]
                travel = call("record_movement", travel["id"], movement, movement_key)
                self.assertEqual(travel["revision_no"], revision)
                departure_id = travel["movements"][0]["id"]
                self.assertEqual(len(call("record_movement", travel["id"], movement, movement_key)["movements"]), 1)
                error(422, "record_movement", travel["id"], {"expected_version": travel["version"], "kind": "returned", "occurred_at": "2026-10-08T08:00:00+00:00", "related_departure_event_id": departure_id, "note": "倒置不能入库"}, uid())
                travel = call("save_plan", travel["id"], plan(travel, {"return_at": "2026-10-09"}), uid())
                self.assertTrue(travel["journey"]["meal_needs_review"])
                self.assertEqual(travel["journey"]["meal_requests"][0]["request"], "keep")
                self.assertTrue(travel["movements"][0]["stale"])
                leave = create("leave")
                material = {"id": uid(), "kind": "passport", "availability": "source_provided", "provider_class": "current_actor_statement", "valid_until": "2028-01-01", "check": "local_source_checked", "source_id": leave["sources"][-1]["id"], "source_position": "第1–1行", "label": "本次必要引用"}
                materials_data = {"expected_version": leave["version"], "input_revision": leave["revision_no"], "upserts": [material], "removed_ids": []}
                error(404, "save_materials", leave["id"], {**materials_data, "upserts": [{**material, "source_id": create("leave")["sources"][-1]["id"]}]}, uid())
                leave = call("save_materials", leave["id"], materials_data, uid())
                self.assertEqual(leave["materials"]["items"][0]["excerpt"], "本次必要材料引用原文")
                self.assertIn("domestic_proof", leave["materials"]["missing"])
                leave = call("set_travel_link", leave["id"], {"expected_version": leave["version"], "travel_matter_id": travel["id"], "travel_revision": travel["revision_no"]}, uid())
                self.assertEqual(leave["journey"]["plan_fields"]["return_at"]["value"], "2026-10-09")
                rendered = call("render", leave["id"], {"expected_version": leave["version"], "input_revision": leave["revision_no"]}, uid())
                self.assertEqual(rendered["input_revision"], leave["revision_no"])
                self.assertIn("## 餐次人工需求", rendered["content"])
                if protected:
                    error(404, "get_journey", leave["id"], actor="other")
                    foreign = create("travel", actor="other")
                    current = call("get_journey", leave["id"])
                    error(404, "set_travel_link", leave["id"], {"expected_version": current["version"], "travel_matter_id": foreign["id"], "travel_revision": foreign["revision_no"]}, uid())
                    error(404, "record_movement", travel["id"], movement, movement_key, actor="other")
                travel = call("save_plan", travel["id"], plan(travel, {"return_at": "2026-10-10"}), uid())
                self.assertTrue(call("get_journey", leave["id"])["journey"]["link_stale"])
                with store.as_actor(actors["owner"]):
                    paused = store.mutate("control", travel["id"], {"expected_version": travel["version"], "command": "pause"}, uid())
                error(409, "render", travel["id"], {"expected_version": paused["version"], "input_revision": paused["revision_no"]}, uid())
                actual = call("record_movement", travel["id"], {"expected_version": paused["version"], "kind": "returned", "person_ref": "张工原述", "reported_by": "张工", "note": "回来时间未记，不能拿计划代填", "occurred_at": None}, uid())
                self.assertIsNone(actual["movements"][-1]["occurred_at"])
                self.assertTrue(all(track["status"] == "unknown" for track in actual["tracks"].values()))
                reopened = JourneyService(Store(directory))
                with reopened.store.as_actor(actors["owner"]):
                    restored = reopened.get_journey(travel["id"])
                    self.assertEqual(len(restored["movements"]), 2)
                    self.assertEqual(restored["journey"]["meal_requests"][0]["note"], "人工保餐例外")
                    self.assertEqual(reopened.store.download(rendered["artifact_id"], 1), rendered["content"])


if __name__ == "__main__":
    unittest.main()
