"""Private requirements/material cross-checking; never a compliance decision."""

import json

from .store import AppError, dump, now, string, uid
from .structured import SAFE_ID, StructuredService, _strict

SCOPE = "本人私有要求与材料来源逐项核对；不代表正式适用、签发、休假批准、可通行、合规、资格、录用、处罚、付款、收发或权威记录更新"


class ChecklistService(StructuredService):
    def _owner(self, connection, matter_id, domain):
        if domain == 'leave':
            from .material_records import MaterialRecordsService
            return MaterialRecordsService(self.store)._owned(connection, matter_id)
        return super()._owner(connection, matter_id, domain)

    def _owned(self, connection, matter_id):
        matter = self.store._matter(connection, matter_id)
        if matter["domain"] not in {"hr", "leave", "expense", "inventory", "general"}:
            raise AppError("invalid_domain", "本核对接口仅用于人事、签证休假返岗、费用、物资或综合准备事项。")
        return self._owner(connection, matter_id, matter["domain"])

    def _checks(self, facts):
        return facts.get("__checks", {"schema_version": 1, "input_revision": None, "structure_version": 0, "items": []})

    def _view(self, connection, matter, facts):
        meta = self._checks(facts)
        stale = meta["input_revision"] is not None and meta["input_revision"] != matter["revision_no"]
        items, gaps = [], []
        for saved in meta["items"]:
            item = dict(saved)
            item["requirement_refs"] = self._refs(connection, matter["id"], item["requirement_refs"])
            item["provided_refs"] = self._refs(connection, matter["id"], item["provided_refs"]) if item["provided_refs"] else []
            item["requirement_excerpt"] = "\n\n".join(ref["excerpt"] for ref in item["requirement_refs"])
            item["provided_excerpt"] = "\n\n".join(ref["excerpt"] for ref in item["provided_refs"])
            item["compliance"] = "unknown"
            reason = "要求或材料来源已修订，原核对范围待重核" if stale else "未提供对应材料" if not item["provided_refs"] else "所给来源尚待本人逐项核对" if item["check"] != "local_source_checked" else None
            if reason:
                gaps.append({"id": item["id"], "requirement_text": item["requirement_text"], "reason": reason})
            items.append(item)
        return {"id": matter["id"], "version": matter["version"], "revision_no": matter["revision_no"], "input_revision": meta["input_revision"],
                "structure_version": meta["structure_version"], "stale": stale, "items": items, "gaps": gaps, "scope": SCOPE}

    def get_checks(self, matter_id):
        with self.store.connect() as connection:
            _, matter, facts = self._owned(connection, matter_id)
            return self._view(connection, matter, facts)

    def get_model_candidates(self, matter_id):
        with self.store.connect() as connection:
            _, matter, _ = self._owned(connection, matter_id)
            domain = matter['domain']
        if domain not in {'hr', 'leave'}:
            raise AppError('invalid_domain', '模型要求匹配仅用于人事或签证休假返岗事项。')
        result = self._model_candidates(matter_id, domain, 'check_candidates', ('requirement_refs', 'provided_refs'))
        if domain == 'leave':
            from .model_checks import leave_note
            for item in result['items']:
                item['model_note'] = item.get('model_note', item['note'])
                item['note'] = leave_note(item['provided_refs'])
        result['scope'] = ('已保存的签证休假返岗材料匹配候选；读取不调用模型。要求原述与出处逐字核对，适用、原件与匹配含义须本人核对；缺材料／提交／回执各自待核，不判断办妥、签发或可通行。' if domain == 'leave' else '已保存的人事材料核对候选；读取不调用模型。要求原述与逐字出处已核对，材料匹配及实际适用仍须人工核对；全部待核，不作合规、资格、录用、待遇或处罚决定。')
        return result

    def _limit(self, data):
        try:
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 131072:
                raise ValueError()
        except (TypeError, ValueError, UnicodeError):
            raise AppError("check_limit", "核对请求须为有效JSON且最多128KiB。") from None

    def _baseline(self, matter, meta, data):
        self.store._check_version(matter, data)
        structure_key = "base_structure_version" if "base_structure_version" in data else "structure_version"
        if type(data["input_revision"]) is not int or data["input_revision"] != matter["revision_no"] or type(data[structure_key]) is not int or data[structure_key] != meta["structure_version"]:
            raise AppError("check_conflict", "事实或核对结构基线已变化，请读回后保留人工修改。", 409)

    def _item(self, connection, matter_id, item):
        _strict(item, {"id", "requirement_text", "requirement_refs", "provided_refs", "check", "note"})
        if not isinstance(item["id"], str) or not SAFE_ID.fullmatch(item["id"]):
            raise AppError("invalid_id", "核对项须有稳定安全ID，最多80字符。")
        requirement_refs = self._refs(connection, matter_id, item["requirement_refs"], require_usable=True)
        if not isinstance(item["provided_refs"], list):
            raise AppError("invalid_refs", "材料引用须为列表，缺材料可为空。")
        provided_refs = self._refs(connection, matter_id, item["provided_refs"], require_usable=True) if item["provided_refs"] else []
        if item["check"] not in ("pending", "source_provided", "local_source_checked"):
            raise AppError("invalid_check", "仅可登记待核、已给来源或本人核对所给来源。")
        if item["check"] != "pending" and not provided_refs:
            raise AppError("proof_missing", "未提供真实本事项材料引用，不能记已提供或已核对。")
        if not isinstance(item["note"], str) or len(item["note"]) > 6000:
            raise AppError("invalid_input", "备注须为最多6000字符文字，可空。")
        def pointers(refs):
            return [{key: ref[key] for key in ("source_id", "start_line", "end_line")} for ref in refs]
        return {"id": item["id"], "requirement_text": string(item["requirement_text"], "原要求或本次核对目标", 6000), "requirement_refs": pointers(requirement_refs),
                "provided_refs": pointers(provided_refs), "check": item["check"], "note": item["note"].strip()}

    def save_checks(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision", "base_structure_version", "upserts", "removed_ids"})
        self._limit(data)
        if not isinstance(data["upserts"], list) or not isinstance(data["removed_ids"], list) or len(data["upserts"]) > 100 or len(data["removed_ids"]) > 100 or not (data["upserts"] or data["removed_ids"]):
            raise AppError("check_limit", "每次最多100项，删除须明确列ID，空列表不会删除。")
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, "checklist_save", matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter, facts)
            meta = self._checks(facts)
            self._baseline(matter, meta, data)
            saved = {item["id"]: item for item in meta["items"]}
            removed, seen = data["removed_ids"], set()
            if any(not isinstance(item_id, str) or item_id not in saved for item_id in removed) or len(set(removed)) != len(removed):
                raise AppError("invalid_removed_ids", "仅可显式删除本事项当前项，ID不得重复。")
            for incoming in data["upserts"]:
                item = self._item(connection, matter_id, incoming)
                if item["id"] in seen or item["id"] in removed:
                    raise AppError("invalid_id", "项ID重复或同时删除，请保留明确修改。")
                seen.add(item["id"])
                saved[item["id"]] = item
            for item_id in removed:
                del saved[item_id]
            if len(saved) > 100:
                raise AppError("check_limit", "本事项最多100个核对项。")
            revision = matter["revision_no"] + 1
            facts["__checks"] = {"schema_version": 1, "input_revision": revision, "structure_version": meta["structure_version"] + 1, "items": list(saved.values())}
            connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), now(), actor["id"]))
            self.store._cancel(connection, matter_id, "要求材料核对已修订；旧准备停止，人工成果保留")
            status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
            self.store._bump(connection, matter_id, revision_no=revision, preparation_status="needs_review", assistant_status=status)
            self.store._event(connection, matter_id, "checks_saved", "已保存有真实原文位置的要求材料核对；未列出的项保留，未作正式合规或办理决定。")
            return self._remember(connection, key, fingerprint, "checklist_save", matter_id, self._view(connection, self.store._matter(connection, matter_id), facts))

    def render_checks(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision", "structure_version"})
        self._limit(data)
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, "checklist_render", matter_id, data, key)
            if previous is not None:
                return previous
            self._baseline(matter, self._checks(facts), data)
            if matter["assistant_status"] in {"paused", "handoff"}:
                raise AppError("assistant_stopped", "未来准备已停止；原成果与人工修正保留。", 409)
            view = self._view(connection, matter, facts)
            if view["stale"]:
                raise AppError("checks_stale", "原事实或来源已修订，请明确重核后保存，旧稿保留。", 409)
            if not view["items"]:
                raise AppError("checks_empty", "未提供有真实出处的要求，不能生成空核对稿。")
            self._ensure_preparation_sources(connection, matter_id, [ref['source_id'] for item in view['items'] for ref in item['requirement_refs'] + item['provided_refs']])
            lines = ["# 要求与材料核对准备稿", "", SCOPE, "事实修订：" + str(matter["revision_no"]), "生成方式：本地确定性模板；正式适用、合规与处理结果未知。", ""]
            for item in view["items"]:
                check_label = {'pending': '来源待核', 'source_provided': '已提供对应材料来源', 'local_source_checked': '本人已核所给来源'}[item['check']]
                lines += ["## " + item["requirement_text"], "本地核对：" + check_label, "正式合规：未知", "备注：" + (item["note"] or "未补充"), "要求依据："]
                for ref in item["requirement_refs"]:
                    lines += ["来源 " + ref["source_id"] + " 第" + str(ref["start_line"]) + "–" + str(ref["end_line"]) + "行", ref["excerpt"]]
                lines.append("材料来源：" if item["provided_refs"] else "材料未提供，待补。")
                for ref in item["provided_refs"]:
                    lines += ["来源 " + ref["source_id"] + " 第" + str(ref["start_line"]) + "–" + str(ref["end_line"]) + "行", ref["excerpt"]]
                lines.append("")
            lines += ["## 缺项与待核"] + ["- " + gap["requirement_text"] + "：" + gap["reason"] for gap in view["gaps"]]
            if not view["gaps"]:
                lines.append("所给来源已逐项本地核对；正式适用性与合规仍待实际责任人核实。")
            content, artifact_id = "\n".join(lines), uid()
            connection.execute("INSERT INTO artifacts(id,version,matter_id,type,title,content,status,input_revision,created_at) VALUES(?,1,?,?,?,?,?,?,?)", (artifact_id, matter_id, "checklist_preparation", "要求与材料核对准备稿", content, "structured_draft", matter["revision_no"], now()))
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, "checks_rendered", "已保存新的来源核对准备稿；人工版本保留，未付款、收发、录用或处罚。")
            return self._remember(connection, key, fingerprint, "checklist_render", matter_id, {"artifact_id": artifact_id, "version": 1, "type": "checklist_preparation", "input_revision": matter["revision_no"], "content": content, "scope": SCOPE})
