"""Owner-declared source usability, preserving originals and revoking local grants."""

from .store import AppError, dump, now, string
from .structured import StructuredService, _strict

SCOPE = "本人对当前准备引用的来源现行性说明；原文及人工历史保留，不判制度效力或法律撤权。暂不使用会撤回本事项有效准备文字分享，已取出副本无法召回；菜单、意见和服务状态仍沿各自操作记录。"


class SourceLifecycleService(StructuredService):
    def _owned(self, connection, matter_id):
        matter = self.store._matter(connection, matter_id)
        return self._owner(connection, matter_id, matter["domain"])

    def _states(self, facts):
        return facts.get("__source_states", {"schema_version": 1, "input_revision": None, "items": {}})

    def _view(self, connection, matter, facts):
        states = self._states(facts)["items"]
        items = []
        for row in connection.execute("SELECT id,kind,text,reported_by,recorded_by,recorded_at FROM sources WHERE matter_id=? ORDER BY recorded_at,rowid", (matter["id"],)):
            item = dict(row)
            state = states.get(item["id"], {})
            item.update(source_state=state.get("state", "usable"), replacement_source_id=state.get("replacement_source_id"), state_note=state.get("note", ""),
                        changed_by=state.get("changed_by"), changed_by_name=state.get("changed_by_name"), changed_at=state.get("changed_at"))
            items.append(item)
        return {"id": matter["id"], "version": matter["version"], "revision_no": matter["revision_no"], "items": items, "scope": SCOPE}

    def get_sources(self, matter_id):
        with self.store.connect() as connection:
            _, matter, facts = self._owned(connection, matter_id)
            return self._view(connection, matter, facts)

    def set_source_state(self, matter_id, source_id, data, key):
        _strict(data, {"expected_version", "input_revision", "state", "replacement_source_id", "note"})
        source_id = string(source_id, "来源ID", 80)
        note = string(data["note"], "本人现行性说明", 1000)
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            if not connection.execute("SELECT id FROM sources WHERE id=? AND matter_id=?", (source_id, matter_id)).fetchone():
                raise AppError("not_found", "本事项来源不存在。", 404)
            key, fingerprint, previous = self._request(connection, actor, "source_state:" + source_id, matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter, facts)
            self.store._check_version(matter, data)
            if type(data["input_revision"]) is not int or data["input_revision"] != matter["revision_no"]:
                raise AppError("revision_conflict", "来源或事实修订已变化，请读回后保留人工说明。", 409)
            state, replacement = data["state"], data["replacement_source_id"]
            if state not in ("usable", "superseded", "withheld"):
                raise AppError("invalid_source_state", "只可注明可用于准备、被本事项另一来源替代或暂不用于准备。")
            if state == "superseded":
                replacement = string(replacement, "替代来源ID", 80)
                if replacement == source_id:
                    raise AppError("source_cycle", "来源不能替代自己。")
                if not connection.execute("SELECT id FROM sources WHERE id=? AND matter_id=?", (replacement, matter_id)).fetchone():
                    raise AppError("not_found", "本事项替代来源不存在。", 404)
            elif replacement is not None:
                raise AppError("invalid_replacement", "可用或暂不使用状态的替代来源须为null。")
            revision, stamp = matter["revision_no"] + 1, now()
            states = dict(self._states(facts)["items"])
            states[source_id] = {"state": state, "replacement_source_id": replacement, "note": note,
                                 "changed_by": actor["id"], "changed_by_name": actor["display_name"], "changed_at": stamp}
            # Every replacement edge is an explicit same-matter statement, never an inferred rule.
            for start in states:
                visited, cursor = set(), start
                while cursor in states and states[cursor]["state"] == "superseded":
                    if cursor in visited:
                        raise AppError("source_cycle", "替代来源链形成循环，请保留原说明并明确正确来源。")
                    visited.add(cursor)
                    cursor = states[cursor]["replacement_source_id"]
            facts["__source_states"] = {"schema_version": 1, "input_revision": revision, "items": states}
            revoked = 0
            if state == "withheld" and connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='matter_grants'").fetchone():
                revoked = connection.execute("UPDATE matter_grants SET status='withdrawn',version=version+1,revoked_at=? WHERE matter_id=? AND status='active'", (stamp, matter_id)).rowcount
            connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), stamp, actor["id"]))
            self.store._cancel(connection, matter_id, "本人改变来源准备引用范围，停止旧准备；不删除原文或人工成果")
            status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
            self.store._bump(connection, matter_id, revision_no=revision, control_epoch=matter["control_epoch"] + 1, preparation_status="needs_review", assistant_status=status)
            self.store._event(connection, matter_id, "source_state_changed", dump({"source_id": source_id, **states[source_id], "input_revision": revision, "revoked_share_count": revoked, "scope": "本人当前准备引用控制；未判正式效力或召回已取出副本"}))
            return self._remember(connection, key, fingerprint, "source_state", matter_id, self._view(connection, self.store._matter(connection, matter_id), facts))
