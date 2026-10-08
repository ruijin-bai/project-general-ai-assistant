"""Read-only, explicit current source pointers; never infer textual dependencies."""

import re

from .structured import StructuredService


SCOPE = "仅展示当前保存结构的明确来源指针；不是历史全量影响图或原文核真。纯文字与人工稿未追踪细引用；不同输入修订保守待重核，不推定细粒度无影响。"
REF_LIMIT = 500
ARTIFACT_LIMIT = 30
POSITION = re.compile(r"第([1-9][0-9]*)–([1-9][0-9]*)行")


def _revision(value):
    return value if type(value) is int and value > 0 else None


def _label(item, fields):
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    return "未提供标题或原述"


class SourceImpactService(StructuredService):
    def get_impact(self, matter_id):
        # A read transaction keeps source ranges, facts and versions in one snapshot.
        with self.store.connect() as connection:
            connection.execute("BEGIN")
            initial = self.store._matter(connection, matter_id)
            _, matter, facts = self._owner(connection, matter_id, initial["domain"])
            sources = connection.execute("SELECT id,text FROM sources WHERE matter_id=? ORDER BY recorded_at,id", (matter_id,)).fetchall()
            source_lines = {source["id"]: len(source["text"].splitlines()) for source in sources}
            metadata = facts.get("__source_states", {})
            states = metadata.get("items", {}) if isinstance(metadata, dict) else {}
            if not isinstance(states, dict):
                states = {}
            source_views = {}
            for source in sources:
                state = states.get(source["id"], {})
                state = state if isinstance(state, dict) else {}
                status = state.get("state", "usable")
                status = status if status in {"usable", "superseded", "withheld"} else "usable"
                replacement = state.get("replacement_source_id")
                source_views[source["id"]] = {
                    "id": source["id"], "source_state": status,
                    "replacement_source_id": replacement if replacement in source_lines else None,
                    "reference_count": 0,
                }
            result = {"id": matter_id, "version": matter["version"], "revision_no": matter["revision_no"],
                      "sources": list(source_views.values()), "components": [], "artifacts": [],
                      "limited": False, "scope": SCOPE}
            reference_count = 0

            def pointer(raw, mode):
                if not isinstance(raw, dict) or not isinstance(raw.get("source_id"), str) or raw["source_id"] not in source_lines:
                    return None
                source_id = raw["source_id"]
                if mode == "row":
                    position = raw.get("source_position")
                    if not isinstance(position, str) or not position.strip():
                        return None
                    return {"source_id": source_id, "start_line": None, "end_line": None, "source_position": position}
                if mode == "material":
                    position = raw.get("source_position")
                    match = POSITION.fullmatch(position) if isinstance(position, str) else None
                    if not match:
                        return {"source_id": source_id, "start_line": None, "end_line": None,
                                "source_position": position if isinstance(position, str) else None}
                    start, end = map(int, match.groups())
                else:
                    start, end = raw.get("start_line"), raw.get("end_line")
                if type(start) is not int or type(end) is not int or not 1 <= start <= end <= source_lines[source_id]:
                    if mode == "material":
                        return {"source_id": source_id, "start_line": None, "end_line": None, "source_position": position}
                    return None
                return {"source_id": source_id, "start_line": start, "end_line": end,
                        "source_position": f"第{start}–{end}行"}

            def component(kind, item, revision, refs, fields, mode="lines"):
                nonlocal reference_count
                if not isinstance(item, dict):
                    return
                refs = refs if isinstance(refs, list) else []
                resolved, untraced, limited = [], not refs, False
                for raw in refs:
                    ref = pointer(raw, mode)
                    if ref is None:
                        untraced = True
                        continue
                    if reference_count >= REF_LIMIT:
                        result["limited"] = limited = True
                        continue
                    resolved.append(ref)
                    reference_count += 1
                    source_views[ref["source_id"]]["reference_count"] += 1
                issues = [{"source_id": source_id, "state": source_views[source_id]["source_state"]}
                          for source_id in dict.fromkeys(ref["source_id"] for ref in resolved)
                          if source_views[source_id]["source_state"] in {"superseded", "withheld"}]
                scope = "当前已保存来源指针；指向原文不代表事实核真或正式效力。"
                if mode == "row":
                    scope += "明细位置为本人自述，未验证行号。"
                if mode == "material" and any(ref["start_line"] is None for ref in resolved):
                    scope += "未追踪细引用（nottraced）：材料位置原话保留，行号未验证。"
                if untraced:
                    scope += "未追踪细引用（nottraced）：部分或全部内容无有效的已保存位置。"
                if limited:
                    scope += "仅显示500处总引用预算内的部分位置。"
                input_revision = _revision(revision)
                result["components"].append({"kind": kind, "id": item.get("id", item.get("field")),
                    "label": _label(item, fields), "input_revision": input_revision,
                    "stale": input_revision != matter["revision_no"], "refs": resolved,
                    "source_issues": issues, "scope": scope})

            for meta_key, collection, kind, labels in (
                ("__meeting", "items", "meeting_item", ("text",)),
                ("__document", "sections", "document_section", ("heading", "body")),
                ("__checks", "items", "checklist_item", ("requirement_text",)),
                ("__readings", "items", "utility_reading", ("meter_ref",)),
                ("__materials", "items", "leave_material", ("label", "kind")),
            ):
                meta = facts.get(meta_key, {})
                if not isinstance(meta, dict) or not isinstance(meta.get(collection, []), list):
                    continue
                for item in meta.get(collection, []):
                    if not isinstance(item, dict):
                        continue
                    if meta_key == "__checks":
                        refs = [ref for field in ("requirement_refs", "provided_refs") for ref in (item.get(field, []) if isinstance(item.get(field, []), list) else [])]
                    elif meta_key == "__materials":
                        refs = [item] if item.get("source_id") else []
                    else:
                        refs = item.get("refs", [])
                    component(kind, item, meta.get("input_revision"), refs, labels,
                              mode="material" if meta_key == "__materials" else "lines")
            if isinstance(facts.get("__rows", []), list):
                for item in facts.get("__rows", []):
                    component("calculation_row", item, facts.get("__rows_revision"), [item], ("label",), mode="row")
            candidates = facts.get("__fact_candidates", {})
            if isinstance(candidates, dict) and isinstance(candidates.get("items", {}), dict):
                for field, item in candidates.get("items", {}).items():
                    if isinstance(item, dict):
                        component("fact_candidate", {**item, "field": field}, candidates.get("input_revision"), item.get("refs", []), ("field",))
            artifacts = connection.execute(
                "SELECT a.id,a.version,a.title,a.input_revision FROM artifacts a "
                "JOIN (SELECT id,MAX(version) version FROM artifacts WHERE matter_id=? GROUP BY id) latest "
                "ON a.id=latest.id AND a.version=latest.version WHERE a.matter_id=? "
                "ORDER BY a.created_at DESC,a.id LIMIT ?", (matter_id, matter_id, ARTIFACT_LIMIT + 1)).fetchall()
            if len(artifacts) > ARTIFACT_LIMIT:
                result["limited"] = True
            for artifact in artifacts[:ARTIFACT_LIMIT]:
                input_revision = _revision(artifact["input_revision"])
                result["artifacts"].append({"id": artifact["id"], "version": artifact["version"],
                    "title": artifact["title"], "input_revision": input_revision,
                    "stale": input_revision != matter["revision_no"], "dependency_scope": "whole_input_revision"})
            if result["limited"]:
                result["scope"] += "已限制可见范围：最多500处引用、30个最新成果；引用计数仅统计本次返回位置。"
            else:
                result["scope"] += "引用计数仅统计本次返回位置，不包含历史修订。"
            return result
