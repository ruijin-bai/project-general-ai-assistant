"""One Store smoke of literal label extraction; no model/business evidence."""

import tempfile
import unittest
from pathlib import Path

from assistant_app.extraction import ExtractionService
from assistant_app.source_lifecycle import SourceLifecycleService
from assistant_app.store import AppError, Store, dump, now, uid


class ExtractionSmoke(unittest.TestCase):
    def test_literal_candidates_conflicts_permissions_and_preservation(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-extraction-", dir=Path(__file__).resolve().parent) as directory:
                store, actors = Store(directory), {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'fixture')", (actor_id, name, "隔离 " + name))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}
                service, lifecycle = ExtractionService(store), SourceLifecycleService(store)

                def call(method, *args, actor="owner"):
                    with store.as_actor(actors[actor]):
                        return getattr(service, method)(*args)

                def error(status, method, *args, actor="owner"):
                    with self.assertRaises(AppError) as caught:
                        call(method, *args, actor=actor)
                    self.assertEqual(caught.exception.status, status)

                text = "目的地：原文地点\nparticipants=甲与乙\n参与人员：甲与乙\n用途：巡查\npurpose=探亲\n离营时间：明天\n预计后天也许返营。\n安全批准：已批准\n说明：返营时间可能很晚"
                with store.as_actor(actors["owner"]):
                    detail = store.mutate("create", None, {"goal_text": "隔离标签候选", "domain": "travel"}, uid())
                    detail = store.mutate("source", detail["id"], {"expected_version": detail["version"], "kind": "oral_note", "text": text}, uid())
                    source_id = next(source["id"] for source in detail["sources"] if source["text"] == text)
                    detail = store.mutate("source", detail["id"], {"expected_version": detail["version"], "kind": "oral_note", "text": "参与人员：旧人员原述"}, uid())
                    old_source = next(source["id"] for source in detail["sources"] if source["text"] == "参与人员：旧人员原述")
                    lifecycle.set_source_state(detail["id"], old_source, {"expected_version": detail["version"], "input_revision": detail["revision_no"], "state": "superseded", "replacement_source_id": source_id, "note": "本人注明旧原文由新记录替代"}, uid())
                    detail = store.detail(detail["id"])
                    detail = store.mutate("facts", detail["id"], {"expected_version": detail["version"], "field_changes": {"destination": "人工已确认地点"}}, uid())
                    with store.transaction() as connection:
                        facts = store._stored_facts(connection, detail)
                        facts.update(__workflow={"enabled": False, "mode": "template"}, __rows=[{"保留": "既有人工元信息"}])
                        connection.execute("UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?", (dump(facts), detail["id"], detail["revision_no"]))
                        manual_id = uid()
                        connection.execute("INSERT INTO artifacts VALUES(?,1,?,?,?,?,?,?,?)", (manual_id, detail["id"], "travel", "人工稿", "原人工稿不覆盖", "human_saved", detail["revision_no"], now()))
                candidates = call("get_candidates", detail["id"])
                items = {(item["field"], item["value"]): item for item in candidates["items"]}
                self.assertEqual(len(items[("participants", "甲与乙")]["refs"]), 2)
                self.assertEqual(items[("participants", "甲与乙")]["refs"][0]["start_line"], 2)
                self.assertEqual(items[("participants", "甲与乙")]["refs"][0]["excerpt"], "participants=甲与乙")
                self.assertNotIn(("participants", "旧人员原述"), items)
                self.assertEqual({item["field"] for item in candidates["items"]}, {"destination", "participants", "purpose", "departure_at"})
                self.assertFalse(items[("destination", "原文地点")]["selectable"])
                self.assertFalse(items[("purpose", "巡查")]["selectable"])
                self.assertEqual(candidates["conflicts"][0]["values"], ["巡查", "探亲"])
                data = {"expected_version": candidates["version"], "input_revision": candidates["revision_no"], "selections": [{"field": "participants", "value": "甲与乙"}, {"field": "departure_at", "value": "明天"}]}
                for selection, status in (({"field": "participants", "value": "伪造值"}, 422), ({"field": "destination", "value": "原文地点"}, 409), ({"field": "purpose", "value": "巡查"}, 409)):
                    error(status, "apply_candidates", detail["id"], {**data, "selections": [selection]}, uid())
                error(422, "apply_candidates", detail["id"], {**data, "selections": [data["selections"][0]] * 2}, uid())
                if protected:
                    error(404, "get_candidates", detail["id"], actor="other")
                with store.as_actor(actors["owner"]):
                    paused = store.mutate("control", detail["id"], {"expected_version": candidates["version"], "command": "pause"}, uid())
                data["expected_version"] = paused["version"]
                key = uid()
                applied = call("apply_candidates", detail["id"], data, key)
                self.assertEqual(applied["facts"]["participants"], {"value": "甲与乙", "status": "candidate"})
                self.assertEqual(applied["facts"]["departure_at"], {"value": "明天", "status": "candidate"})
                self.assertEqual(applied["facts"]["destination"]["value"], "人工已确认地点")
                self.assertEqual(applied["assistant_status"], "paused")
                with store.as_actor(actors["owner"]):
                    self.assertEqual(store.download(manual_id, 1), "原人工稿不覆盖")
                    with store.connect() as connection:
                        latest = store._stored_facts(connection, applied)
                        self.assertEqual(latest["__rows"], facts["__rows"])
                        self.assertEqual(latest["__workflow"], facts["__workflow"])
                        self.assertEqual(latest["__fact_candidates"]["items"]["participants"]["refs"][0]["source_id"], source_id)
                    confirmed = store.mutate("facts", detail["id"], {"expected_version": applied["version"], "field_changes": {"participants": "本人后续已核人员"}}, uid())
                replay = call("apply_candidates", detail["id"], data, key)
                self.assertEqual(replay["facts"]["participants"], {"value": "本人后续已核人员", "status": "confirmed"})
                with store.as_actor(actors["owner"]):
                    lifecycle.set_source_state(detail["id"], source_id, {"expected_version": confirmed["version"], "input_revision": confirmed["revision_no"], "state": "withheld", "replacement_source_id": None, "note": "本人暂不用于准备"}, uid())
                error(409, "get_candidates", detail["id"])
                error(409, "apply_candidates", detail["id"], data, uid())
                self.assertEqual(call("apply_candidates", detail["id"], data, key)["facts"]["participants"]["status"], "confirmed")
                if protected:
                    error(404, "apply_candidates", detail["id"], data, key, actor="other")


if __name__ == "__main__":
    unittest.main()
