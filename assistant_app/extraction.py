"""Explicit local label extraction with source positions; never narrative understanding."""

import json
import re

from .preparation import DOMAINS
from .store import AppError, dump, now, string
from .structured import StructuredService, _strict

SCOPE = "本地明确标签规则提取的原文候选，不是模型理解；仅字段键/中文标签及出行休假“离营时间、返营时间”别名，不推相对日期、批准或叙述含义。本人已确认值保留。"
ALIASES = {"离营时间": "departure_at", "返营时间": "return_at"}
LINE = re.compile(r"^\s*([^:：=]{1,100}?)\s*[:：=]\s*(.*?)\s*$")


class ExtractionService(StructuredService):
    def _owned(self, connection, matter_id):
        matter = self.store._matter(connection, matter_id)
        return self._owner(connection, matter_id, matter["domain"])

    def _view(self, connection, matter, facts):
        self._ensure_preparation_sources(connection, matter["id"])
        fields = DOMAINS[matter["domain"]]["fields"]
        labels = {**{key: key for key in fields}, **{label: key for key, label in fields.items()}}
        if matter["domain"] in {"travel", "leave"}:
            labels.update(ALIASES)
        states, extracted = facts.get("__source_states", {}).get("items", {}), {}
        for source in connection.execute("SELECT id,text FROM sources WHERE matter_id=? ORDER BY recorded_at,rowid", (matter["id"],)):
            if states.get(source["id"], {}).get("state", "usable") != "usable":
                continue
            for line_number, line in enumerate(source["text"].splitlines(), 1):
                match = LINE.fullmatch(line)
                if not match:
                    continue
                field = labels.get(match[1].strip())
                value = match[2].strip()
                if not field or not value or len(value) > 2000:
                    continue
                extracted.setdefault((field, value), []).append({"source_id": source["id"], "start_line": line_number, "end_line": line_number, "excerpt": line})
                if len(extracted) > 40:
                    raise AppError("candidate_limit", "当前来源超过40个不同原文候选，请先明确本次来源范围；未静默截断冲突。", 409)
        values = {}
        for field, value in extracted:
            values.setdefault(field, []).append(value)
        conflicts = []
        for field, candidates in values.items():
            existing = facts.get(field, {"value": None, "status": "unknown"})
            if len(candidates) > 1:
                conflicts.append({"field": field, "label": fields[field], "values": candidates, "existing_status": existing["status"], "existing_value": existing["value"], "reason": "同一字段有不同原文值，未择取最后一行；请沿来源核对后人工修正。"})
        items = []
        for (field, value), refs in extracted.items():
            existing = facts.get(field, {"value": None, "status": "unknown"})
            items.append({"field": field, "label": fields[field], "value": value, "refs": refs, "existing_status": existing["status"], "existing_value": existing["value"],
                          "selectable": existing["status"] != "confirmed" and len(values[field]) == 1})
        return {"id": matter["id"], "version": matter["version"], "revision_no": matter["revision_no"], "items": items, "conflicts": conflicts, "scope": SCOPE}

    def get_candidates(self, matter_id):
        with self.store.connect() as connection:
            _, matter, facts = self._owned(connection, matter_id)
            return self._view(connection, matter, facts)

    def apply_candidates(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision", "selections"})
        try:
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 131072:
                raise ValueError()
        except (TypeError, ValueError, UnicodeError):
            raise AppError("candidate_limit", "选择请求须为有效JSON且最多128KiB。") from None
        if not isinstance(data["selections"], list) or not 1 <= len(data["selections"]) <= 40:
            raise AppError("invalid_selection", "请明确选择1至40个原文候选，不支持空选择。")
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, "source_candidates_apply", matter_id, data, key)
            if previous is not None:
                return self.store._detail(connection, matter_id)
            self._ensure_preparation_sources(connection, matter_id)
            self.store._check_version(matter, data)
            if type(data["input_revision"]) is not int or data["input_revision"] != matter["revision_no"]:
                raise AppError("revision_conflict", "来源或事实修订已变化，请读回当前原文候选。", 409)
            candidates = {(item["field"], item["value"]): item for item in self._view(connection, matter, facts)["items"]}
            selected, seen = [], set()
            for selection in data["selections"]:
                _strict(selection, {"field", "value"})
                field, value = string(selection["field"], "候选字段", 80), string(selection["value"], "原文候选值", 2000)
                if field in seen:
                    raise AppError("invalid_selection", "同一字段不能重复选择或以多值隐式覆盖。")
                seen.add(field)
                item = candidates.get((field, value))
                if item is None:
                    raise AppError("invalid_selection", "所选值并非当前可用来源的明确标签原文，不能创建伪造候选。")
                if not item["selectable"]:
                    raise AppError("candidate_not_selectable", "该字段原文冲突或已有本人确认值，不能用候选覆盖。", 409)
                selected.append(item)
            revision, stamp = matter["revision_no"] + 1, now()
            prior = facts.get("__fact_candidates", {"items": {}})
            provenance = dict(prior["items"])
            event_fields = []
            for item in selected:
                field = item["field"]
                facts[field] = {"value": item["value"], "status": "candidate"}
                refs = [{name: ref[name] for name in ("source_id", "start_line", "end_line")} for ref in item["refs"]]
                provenance[field] = {"value": item["value"], "refs": refs, "recorded_by": actor["id"], "recorded_at": stamp}
                event_fields.append({"field": field, "refs": refs})
            facts["__fact_candidates"] = {"schema_version": 1, "input_revision": revision, "method": "explicit_label_local_rule", "items": provenance}
            connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), stamp, actor["id"]))
            self.store._cancel(connection, matter_id, "已应用本地标签原文候选，旧事实修订准备停止；人工成果保留")
            status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
            self.store._bump(connection, matter_id, revision_no=revision, preparation_status="needs_review", assistant_status=status)
            self.store._event(connection, matter_id, "source_candidates_applied", dump({"actor_id": actor["id"], "actor_name": actor["display_name"], "input_revision": revision, "method": "explicit_label_local_rule", "fields": event_fields, "action": "本人选入本地明确标签原文候选；非模型理解，尚未确认，不代表真实业务结果"}))
            self.store._continue(connection, matter_id)
            response = self.store._detail(connection, matter_id)
            return self._remember(connection, key, fingerprint, "source_candidates_apply", matter_id, response)
