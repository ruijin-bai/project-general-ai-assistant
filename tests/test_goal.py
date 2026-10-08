"""One affected goal-correction contract with actual local/protected Stores."""
import tempfile
import unittest
from pathlib import Path

from assistant_app.model import ModelClient, ModelError
from assistant_app.source_lifecycle import SourceLifecycleService
from assistant_app.store import AppError, Store, dump, uid


class GoalCorrectionSmoke(unittest.TestCase):
    def test_goal_history_selected_facts_artifacts_fences_and_permissions(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-goal-", dir=Path(__file__).resolve().parent) as folder:
                store = Store(folder)
                actors = {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    store.enable_services()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active',?,0,'engineering')", (actor_id, name, name, dump(["employee"])))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}
                def mutate(operation, object_id, data, key=None):
                    return store.mutate(operation, object_id, data, key or uid())
                with store.as_actor(actors["owner"]):
                    old_goal = "工程演练：准备房间A空调滴水报修，未实际报修"
                    matter = mutate("create", None, {"goal_text": old_goal, "domain": "repair"})
                    old_source = matter["sources"][0]["id"]
                    matter = mutate("facts", matter["id"], {"expected_version": matter["version"], "field_changes": {"location": "工程房间A", "problem": "空调滴水"}})
                    matter = mutate("manual_artifact", matter["id"], {"expected_version": matter["version"], "input_revision": matter["revision_no"], "edited_content": "工程人工稿原文，不得覆盖"})
                    original_artifacts = matter["artifacts"]
                    matter = mutate("prepare", matter["id"], {"expected_version": matter["version"], "kind": "prepare", "mode": "model"})
                    new_goal = "工程修正：改为房间B，先准备现场检查联系文字，不记已报修"
                    key, data = uid(), {"expected_version": matter["version"], "goal_text": new_goal, "recheck_fields": ["location"]}
                    changed = mutate("goal", matter["id"], data, key)
                    self.assertEqual(changed["goal_text"], new_goal)
                    self.assertEqual(changed["facts"]["location"]["value"], "工程房间A")
                    self.assertEqual(changed["facts"]["location"]["status"], "candidate")
                    self.assertEqual(changed["facts"]["problem"], matter["facts"]["problem"])
                    self.assertEqual([a["content"] for a in changed["artifacts"]], [a["content"] for a in original_artifacts])
                    self.assertTrue(changed["artifacts"][0]["stale"])
                    self.assertTrue(all(a["status"] == "cancelled" for a in changed["actions"]))
                    self.assertEqual(changed["tracks"], matter["tracks"])
                    self.assertEqual(changed["sources"][0]["text"], old_goal)
                    self.assertEqual(changed["sources"][0]["source_state"], "superseded")
                    client = ModelClient()
                    raw = client.preview_input(changed)["input"]
                    self.assertEqual(raw["goal_text"], new_goal)
                    self.assertNotIn(old_goal, [s["text"] for s in raw["sources"]])
                    self.assertIn(new_goal, [s["text"] for s in raw["sources"]])
                    self.assertEqual(changed, mutate("goal", matter["id"], data, key))
                    paused = mutate("control", matter["id"], {"expected_version": changed["version"], "command": "pause"})
                    second = mutate("goal", matter["id"], {"expected_version": paused["version"], "goal_text": "工程再次修正：只准备检查计划，保持暂停", "recheck_fields": []})
                    self.assertEqual(second["assistant_status"], "paused")
                    self.assertEqual(mutate("goal", matter["id"], data, key)["goal_text"], second["goal_text"])
                    with self.assertRaises(AppError) as conflict:
                        mutate("goal", matter["id"], data)
                    self.assertEqual(conflict.exception.status, 409)
                    with self.assertRaises(AppError):
                        mutate("goal", matter["id"], {"expected_version": second["version"], "goal_text": "无效字段", "recheck_fields": ["closure"]})
                    held_source = second["sources"][-1]["id"]
                    SourceLifecycleService(store).set_source_state(matter["id"], held_source, {"expected_version": second["version"], "input_revision": second["revision_no"], "state": "withheld", "replacement_source_id": None, "note": "工程来源暂不用于模型"}, uid())
                    held = store.detail(matter["id"])
                    third = mutate("goal", matter["id"], {"expected_version": held["version"], "goal_text": "工程修正仍不得释放暂不用来源", "recheck_fields": []})
                    self.assertTrue(third["model_disclosure_blocked"])
                    self.assertEqual(third["assistant_status"], "paused")
                    self.assertEqual(next(s for s in third["sources"] if s["id"] == held_source)["source_state"], "withheld")
                    with self.assertRaises(ModelError):
                        client.preview_input(third)
                    with Store(folder).as_actor(actors["owner"]):
                        reopened = Store(folder).detail(matter["id"])
                    self.assertEqual(reopened["goal_text"], third["goal_text"])
                    self.assertEqual(reopened["artifacts"][0]["content"], "工程人工稿原文，不得覆盖")
                    self.assertEqual(len([e for e in reopened["activities"] if e["event_type"] == "goal_revised"]), 3)
                if protected:
                    with store.as_actor(actors["other"]), self.assertRaises(AppError) as denied:
                        mutate("goal", matter["id"], {"expected_version": third["version"], "goal_text": "越权修正", "recheck_fields": []})
                    self.assertEqual(denied.exception.status, 404)
                    with store.transaction() as connection:
                        connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (actors["owner"]["id"],))
                    with store.as_actor(actors["owner"]), self.assertRaises(AppError) as stale:
                        mutate("goal", matter["id"], data, key)
                    self.assertEqual(stale.exception.status, 401)
