"""Private source-linked meeting/document structures and independent action statements."""

import hashlib
import json
import re
from datetime import date, datetime, timezone

from .store import AppError, OWNER, dump, now, string, uid

SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
MEETING_KINDS = {"discussion", "proposal", "decision", "dissent", "action"}
SCOPE = "本人私有资料的结构准备与本地原文核对；不代表组织决定、正式签发、派遣、验收或外部送达"
PROGRESS_LABELS = {'unknown': '未知，待核', 'self_accepted': '本人已声明接受', 'in_progress': '本人声明处理中', 'blocked': '本人报告障碍', 'claimed_done': '本人声明已处理', 'self_accept': '本人接受', 'self_start': '本人声明开始', 'self_blocked': '本人报告障碍', 'self_claimed_done': '本人声明已完成', 'reported_progress': '代录进度', 'result_note': '核对说明', 'self_statement': '本人声明', 'transcribed_statement': '人工转录'}


def _strict(data, required, optional=()):
    if not isinstance(data, dict) or not required.issubset(data) or set(data) - required - set(optional):
        raise AppError("invalid_input", "结构请求仅允许列明字段，不能提交伪造摘录、操作者或正式状态。")


def _nullable(value, label, maximum=2000):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise AppError("invalid_input", label + "须为文字或明确未知null。")
    return value.strip() or None


class StructuredService:
    def __init__(self, store):
        self.store = store

    def _owner(self, connection, matter_id, domain):
        if self.store.requires_auth:
            principal = self.store.principal()
            if not isinstance(principal, dict):
                raise AppError("authentication_required", "请先登录。", 401)
            actor = connection.execute("SELECT id,display_name,state,auth_epoch FROM accounts WHERE id=?", (principal.get("id"),)).fetchone()
            if not actor or actor["state"] != "active" or actor["auth_epoch"] != principal.get("auth_epoch"):
                raise AppError("authentication_required", "登录或授权已失效，请重新登录。", 401)
        else:
            actor = {"id": OWNER, "display_name": "本机隔离预览操作者"}
        matter = self.store._matter(connection, matter_id)
        if not self.store.requires_auth and matter["owner"] != OWNER:
            raise AppError("not_found", "事项不存在", 404)
        if matter["domain"] != domain:
            raise AppError("invalid_input", "本接口仅适用于对应会议或公文事项。")
        return dict(actor), matter, self.store._stored_facts(connection, matter)

    def _ensure_preparation_sources(self, connection, matter_id, source_ids=()):
        matter = self.store._matter(connection, matter_id)
        facts = self.store._stored_facts(connection, matter)
        from .related_tasks import origin_current
        if not origin_current(connection,matter,facts):
            raise AppError('related_basis_changed','原关联事项或来源已变化，请核对原事项；私人历史与人工编辑保留，暂不新准备。',409)
        states = facts.get('__source_states', {}).get('items', {})
        if any(item.get('state') == 'withheld' for item in states.values()):
            raise AppError('source_withheld', '本人已暂不使用原文，新准备暂缓；可继续人工修正和查看历史。', 409)
        if any(states.get(source_id, {}).get('state', 'usable') != 'usable' for source_id in source_ids):
            raise AppError('source_not_current', '选定原文已被本人标为替代来源，请引用当前原文或明确修正说明。', 409)

    def _refs(self, connection, matter_id, refs, require_usable=False):
        if not isinstance(refs, list) or not 1 <= len(refs) <= 10:
            raise AppError("invalid_refs", "每项须有1至10处本事项真实原文位置。")
        results, seen = [], set()
        for ref in refs:
            _strict(ref, {"source_id", "start_line", "end_line"})
            source_id = string(ref["source_id"], "来源ID", 80)
            start, end = ref["start_line"], ref["end_line"]
            if type(start) is not int or type(end) is not int or start < 1 or end < start:
                raise AppError("invalid_refs", "来源行号须为从1开始的整数，含首尾且不能倒置。")
            source = connection.execute("SELECT text FROM sources WHERE id=? AND matter_id=?", (source_id, matter_id)).fetchone()
            if not source:
                raise AppError("invalid_refs", "只能引用本人当前事项已经提供的来源。")
            if require_usable:
                self._ensure_preparation_sources(connection, matter_id, [source_id])
            lines = source["text"].splitlines()
            if end > len(lines):
                raise AppError("invalid_refs", "来源行号超出实际原文范围。")
            key = source_id, start, end
            if key in seen:
                raise AppError("invalid_refs", "同一条结构项的原文位置重复。")
            seen.add(key)
            results.append({"source_id": source_id, "start_line": start, "end_line": end,
                            "excerpt": "\n".join(lines[start - 1:end])})
        return results

    def _deadline(self, value, excerpts):
        if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
            raise AppError("invalid_deadline", "期限须明确区分unknown、date或datetime。")
        if value["kind"] == "unknown":
            _strict(value, {"kind", "value"})
            if value["value"] is not None:
                raise AppError("invalid_deadline", "未知期限value须为null，原话放deadline_text。")
            return {"kind": "unknown", "value": None}
        _strict(value, {"kind", "value", "confirmed"})
        if value["kind"] not in {"date", "datetime"} or value["confirmed"] is not True:
            raise AppError("invalid_deadline", "明示期限须由本人明确核对，不能使用模型候选日期。")
        text = string(value["value"], "明确ISO期限", 100)
        try:
            if value["kind"] == "date":
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
                    raise ValueError()
                date.fromisoformat(text)
            else:
                if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T", text):
                    raise ValueError()
                parsed = datetime.fromisoformat(text)
                if parsed.utcoffset() is None:
                    raise ValueError()
        except ValueError:
            raise AppError("invalid_deadline", "日期须为明确ISO年月日，日期时间还须有明确UTC偏移；其余保留unknown。") from None
        if not any(text in excerpt for excerpt in excerpts):
            raise AppError("deadline_source_missing", "明示期限未见于选定原文；请补实际确认来源或保留unknown。")
        return {"kind": value["kind"], "value": text, "confirmed": True}

    def _validate_item(self, connection, matter_id, item, domain, actor):
        common = {"id", "refs", "confirmation"}
        fields = common | ({"kind", "text", "responsible_text", "responsible_account_id", "deadline_text", "deadline"} if domain == "meeting" else {"heading", "body"})
        _strict(item, fields)
        if not isinstance(item["id"], str) or not SAFE_ID.fullmatch(item["id"]):
            raise AppError("invalid_item_id", "结构项须有稳定安全ID，不能按数组位置重编。")
        if not isinstance(item["confirmation"], str) or item["confirmation"] not in {"candidate", "local_checked"}:
            raise AppError("invalid_confirmation", "仅允许候选或本人核对原记录，不代表组织批准。")
        resolved = self._refs(connection, matter_id, item["refs"], require_usable=True)
        refs = [{key: ref[key] for key in ("source_id", "start_line", "end_line")} for ref in resolved]
        result = {"id": item["id"], "refs": refs, "confirmation": item["confirmation"]}
        if domain == "meeting":
            if not isinstance(item["kind"], str) or item["kind"] not in MEETING_KINDS:
                raise AppError("invalid_kind", "会议结构须分别保存讨论、提议、决定、分歧或行动。")
            responsible = item["responsible_account_id"]
            if responsible is not None and responsible != actor["id"]:
                raise AppError("invalid_responsible", "姓名原述不自动绑定他人账号；此切片只可明确绑定本人。")
            result.update(kind=item["kind"], text=string(item["text"], "会议原述", 10000),
                          responsible_text=_nullable(item["responsible_text"], "责任原述"), responsible_account_id=responsible,
                          deadline_text=_nullable(item["deadline_text"], "期限原话"),
                          deadline=self._deadline(item["deadline"], [ref["excerpt"] for ref in resolved]))
        else:
            if not isinstance(item["heading"], str) or len(item["heading"]) > 500 or not isinstance(item["body"], str) or len(item["body"]) > 10000:
                raise AppError("invalid_section", "段标题最多500字符，正文最多10000字符；待补正文可暂空。")
            result.update(heading=item["heading"].strip(), body=item["body"].strip())
        return result

    def _request(self, connection, actor, operation, matter_id, data, key):
        key = string(key, "请求键", 200)
        try:
            raw = json.dumps([actor["id"], "structured:" + operation, matter_id, data], ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise AppError("invalid_input", "请求须为有效JSON与UTF-8文字。") from None
        fingerprint = hashlib.sha256(raw).hexdigest()
        previous = connection.execute("SELECT fingerprint,response FROM actions WHERE idempotency_key=?", (key,)).fetchone()
        if previous and previous["fingerprint"] != fingerprint:
            raise AppError("idempotency_conflict", "请求键已用于不同内容或身份。", 409)
        return key, fingerprint, json.loads(previous["response"]) if previous else None

    def _remember(self, connection, key, fingerprint, operation, matter_id, response):
        connection.execute("INSERT INTO actions(id,matter_id,kind,status,idempotency_key,fingerprint,response,created_at) VALUES(?,?,?,'completed',?,?,?,?)",
                           (uid(), matter_id, "request:structured_" + operation, key, fingerprint, dump(response), now()))
        return response

    def _structure(self, facts, domain):
        collection = "items" if domain == "meeting" else "sections"
        return facts.get("__" + domain, {"schema_version": 1, "input_revision": None, "structure_version": 0, collection: []})

    def _comparison(self, as_of_date, as_of_datetime, display_timezone):
        result = {"date": None, "datetime": None, "display_timezone": None}
        try:
            if as_of_date is not None:
                if not isinstance(as_of_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", as_of_date):
                    raise ValueError()
                result["date"] = date.fromisoformat(as_of_date)
            if as_of_datetime is not None:
                if not isinstance(as_of_datetime, str) or not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T", as_of_datetime):
                    raise ValueError()
                result["datetime"] = datetime.fromisoformat(as_of_datetime)
                if result["datetime"].utcoffset() is None:
                    raise ValueError()
            if display_timezone is not None:
                result["display_timezone"] = string(display_timezone, "已声明显示时区", 100)
        except ValueError:
            raise AppError("invalid_as_of", "比较时点须为明示ISO日期或带偏移时间，不能推断相对时点。") from None
        return result

    def _hint(self, item, stale, comparison):
        deadline = item["deadline"]
        result = {"status": "unknown", "overdue": None, "as_of": None, "timezone": comparison["display_timezone"], "reason": "期限未知或未提供同精度明示比较时点"}
        if stale or item["confirmation"] != "local_checked":
            result["reason"] = "结构或期限原处待重核，不作逾期结论"
        elif deadline["kind"] == "date" and comparison["date"] is not None and comparison["display_timezone"]:
            passed = date.fromisoformat(deadline["value"]) < comparison["date"]
            result.update(status="past_explicit_deadline" if passed else "not_past", overdue=passed, as_of=comparison["date"].isoformat(), reason="按明示显示时区的日期范围比较；不代表失败、处罚或违反制度")
        elif deadline["kind"] == "datetime" and comparison["datetime"] is not None:
            passed = datetime.fromisoformat(deadline["value"]).astimezone(timezone.utc) < comparison["datetime"].astimezone(timezone.utc)
            result.update(status="past_explicit_deadline" if passed else "not_past", overdue=passed, as_of=comparison["datetime"].isoformat(), timezone="UTC偏移转UTC比较", reason="按明示带偏移时间比较；不代表失败、处罚或违反制度")
        return result

    def _view(self, connection, matter, facts, domain, comparison=None):
        structure = self._structure(facts, domain)
        stale = structure["input_revision"] is not None and structure["input_revision"] != matter["revision_no"]
        collection = "items" if domain == "meeting" else "sections"
        values = []
        for stored in structure[collection]:
            item = {**stored, "refs": self._refs(connection, matter["id"], stored["refs"])}
            if domain == "meeting":
                events = []
                for row in connection.execute("SELECT message FROM events WHERE matter_id=? AND event_type='meeting_action_progress' ORDER BY recorded_at,rowid", (matter["id"],)):
                    event = json.loads(row[0])
                    if event["item_id"] == item["id"]:
                        events.append({**event, "old_item_scope": event["item_revision"] != item["item_revision"]})
                current = [event for event in events if not event["old_item_scope"]]
                acceptance = "self_accepted" if any(event["command"] == "self_accept" for event in current) else "unknown"
                execution_events = [event for event in current if event["command"] in {"self_start", "self_blocked", "self_claimed_done"}]
                execution = {"self_start": "in_progress", "self_blocked": "blocked", "self_claimed_done": "claimed_done"}[execution_events[-1]["command"]] if execution_events else "unknown"
                item.update(progress={"acceptance": acceptance, "execution": execution, "events": events,
                                      "reported_progress": [event for event in current if event["command"] in {"reported_progress", "result_note"}], "inspection": "unknown"},
                            deadline_hint=self._hint(item, stale, comparison or self._comparison(None, None, None)),
                            waiting={"who": item["responsible_text"] or "原述责任人待核", "reason": "本人声明不等于实际验收；责任不明或他人原述需沿原渠道核对"})
            values.append(item)
        return {"id": matter["id"], "version": matter["version"], "revision_no": matter["revision_no"], "input_revision": structure["input_revision"],
                "structure_version": structure["structure_version"], "stale": stale, collection: values, "scope": SCOPE,
                "preparation_status": "not_structured" if not values else "needs_review" if stale or any(item["confirmation"] != "local_checked" or (domain == "document" and not item["body"]) for item in values) else "locally_checked"}

    def _save(self, matter_id, data, key, domain):
        _strict(data, {"expected_version", "input_revision", "base_structure_version", "upserts", "removed_ids"})
        try:
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 131072:
                raise ValueError()
        except (TypeError, ValueError, UnicodeError):
            raise AppError("structure_limit", "结构请求须为有效JSON且最多128KiB，请分段保存。") from None
        if not isinstance(data["upserts"], list) or not isinstance(data["removed_ids"], list) or len(data["upserts"]) > 100 or len(data["removed_ids"]) > 100 or not (data["upserts"] or data["removed_ids"]):
            raise AppError("structure_limit", "每次最多100项，删除须明确列出ID，空列表不自动删除。")
        with self.store.transaction() as connection:
            actor, matter, facts = self._owner(connection, matter_id, domain)
            key, fingerprint, previous = self._request(connection, actor, "save_" + domain, matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter, facts, domain)
            self.store._check_version(matter, data)
            prior = self._structure(facts, domain)
            if type(data["input_revision"]) is not int or data["input_revision"] != matter["revision_no"] or type(data["base_structure_version"]) is not int or data["base_structure_version"] != prior["structure_version"]:
                raise AppError("structure_conflict", "事实或结构基线已变化，请读回后保留人工修改。", 409)
            collection = "items" if domain == "meeting" else "sections"
            saved = {item["id"]: item for item in prior[collection]}
            removed = data["removed_ids"]
            if any(not isinstance(item_id, str) or item_id not in saved for item_id in removed) or len(set(removed)) != len(removed):
                raise AppError("invalid_removed_ids", "只能显式删除本事项当前已有项，ID不能重复。")
            other_ids = set()
            for row in connection.execute("SELECT fields FROM revisions WHERE matter_id<>?", (matter_id,)):
                other = json.loads(row[0])
                for meta, name in (("__meeting", "items"), ("__document", "sections")):
                    other_ids.update(item["id"] for item in other.get(meta, {}).get(name, []))
            historical_revisions = {}
            if domain == "meeting":
                for row in connection.execute("SELECT fields FROM revisions WHERE matter_id=?", (matter_id,)):
                    history = json.loads(row[0])
                    for item in history.get("__meeting", {}).get("items", []):
                        historical_revisions[item["id"]] = max(historical_revisions.get(item["id"], 0), item["item_revision"])
            incoming = set()
            for item in data["upserts"]:
                normalized = self._validate_item(connection, matter_id, item, domain, actor)
                item_id = normalized["id"]
                if item_id in incoming or item_id in removed or item_id in other_ids:
                    raise AppError("invalid_item_id", "结构ID重复、属于其他事项或同时删除，不能隐式合并。")
                incoming.add(item_id)
                if domain == "meeting":
                    old = saved.get(item_id)
                    semantic_keys = {"kind", "text", "responsible_text", "responsible_account_id", "deadline_text", "deadline"}
                    changed = old is not None and any(old[field] != normalized[field] for field in semantic_keys)
                    normalized["item_revision"] = (old["item_revision"] + int(changed)) if old else historical_revisions.get(item_id, 0) + 1
                saved[item_id] = normalized
            for item_id in removed:
                del saved[item_id]
            if len(saved) > 100:
                raise AppError("structure_limit", "每个事项最多100个结构项，请保留明确范围。")
            revision = matter["revision_no"] + 1
            facts["__" + domain] = {"schema_version": 1, "input_revision": revision, "structure_version": prior["structure_version"] + 1, collection: list(saved.values())}
            connection.execute("INSERT INTO revisions(matter_id,revision_no,fields,recorded_at,confirmed_by) VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), now(), actor["id"]))
            self.store._cancel(connection, matter_id, "结构已修订，停止旧准备动作；既有人工成果保留")
            status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
            self.store._bump(connection, matter_id, revision_no=revision, preparation_status="needs_review", assistant_status=status)
            self.store._event(connection, matter_id, domain + "_structure_saved", "本人保存有原文位置的结构修订；未列出的项保留，显式删除仍保留历史。")
            matter = self.store._matter(connection, matter_id)
            return self._remember(connection, key, fingerprint, "save_" + domain, matter_id, self._view(connection, matter, facts, domain))

    def save_meeting_structure(self, matter_id, data, key):
        return self._save(matter_id, data, key, "meeting")

    def save_document_structure(self, matter_id, data, key):
        return self._save(matter_id, data, key, "document")

    def get_meeting_structure(self, matter_id, *, as_of_date=None, as_of_datetime=None, display_timezone=None):
        with self.store.connect() as connection:
            _, matter, facts = self._owner(connection, matter_id, "meeting")
            comparison = self._comparison(as_of_date, as_of_datetime, display_timezone)
            return self._view(connection, matter, facts, "meeting", comparison)

    def get_meeting_candidates(self, matter_id):
        return self._model_candidates(matter_id, 'meeting', 'meeting_items')

    def get_document_candidates(self, matter_id):
        return self._model_candidates(matter_id, 'document', 'document_sections')

    def get_row_candidates(self, matter_id):
        with self.store.connect() as connection:
            matter = self.store._matter(connection, matter_id)
            if matter['domain'] not in {'expense', 'inventory'}:
                raise AppError('invalid_domain', '本入口仅支持费用或物资明细。')
        result = self._model_candidates(matter_id, matter['domain'], 'row_candidates')
        result['scope'] = '已保存模型明细候选；读取不调用模型。原值与逐字引用已检查，归属、期间和含义仍须人工核对。未确认、计算、批准支付或真实收发。'
        return result

    def _model_candidates(self, matter_id, domain, collection, ref_fields=('refs',)):
        with self.store.connect() as connection:
            _, matter, _ = self._owner(connection, matter_id, domain)
            self._ensure_preparation_sources(connection, matter_id)
            action = connection.execute("SELECT id,response FROM actions WHERE matter_id=? AND kind='prepare_model' AND status='completed' AND response IS NOT NULL ORDER BY created_at DESC,rowid DESC LIMIT 1", (matter_id,)).fetchone()
            result = {'id': matter_id, 'version': matter['version'], 'revision_no': matter['revision_no'], 'batch_id': None, 'items': [], 'stale': False,
                      'scope': '已保存模型结构候选；读取不调用模型，出处已按所发原文核对，内容解释仍须人工核对。未确认事实、签发或送达。' if domain == 'document' else '已保存模型条目候选；此读取不调用模型。原文摘录和行号已按所发文字核对，内容解释仍须人工核对；姓名不绑定账号，日期未确认，无接受或完成声明。'}
            if not action:
                return result
            saved = json.loads(action['response'])
            items = []
            for item in saved.get(collection, []):
                resolved = {field: [] if field == 'provided_refs' and not item[field] else self._refs(connection, matter_id, item[field], require_usable=True) for field in ref_fields}
                items.append({**item, **resolved})
            return {**result, 'batch_id': action['id'], 'items': items, 'model': saved['model'],
                    'basis_revision': saved['input_revision'], 'stale': saved['input_revision'] != matter['revision_no']}

    def get_document_structure(self, matter_id):
        with self.store.connect() as connection:
            _, matter, facts = self._owner(connection, matter_id, "document")
            return self._view(connection, matter, facts, "document")

    def meeting_progress(self, matter_id, item_id, data, key):
        _strict(data, {"expected_version", "item_revision", "command", "note"}, {"reported_by", "occurred_at"})
        with self.store.transaction() as connection:
            actor, matter, facts = self._owner(connection, matter_id, "meeting")
            item = next((item for item in self._structure(facts, "meeting")["items"] if item["id"] == item_id and item["kind"] == "action"), None)
            if item is None:
                raise AppError("not_found", "本事项行动项不存在。", 404)
            key, fingerprint, previous = self._request(connection, actor, "progress:" + item_id, matter_id, data, key)
            if previous is not None:
                if data["item_revision"] != item["item_revision"]:
                    raise AppError("item_conflict", "旧行动范围已变化，不能把旧声明回放为新行动。", 409)
                return self._view(connection, matter, facts, "meeting")
            self.store._check_version(matter, data)
            if type(data["item_revision"]) is not int or data["item_revision"] != item["item_revision"]:
                raise AppError("item_conflict", "行动正文、责任或期限已修订，旧声明不适用于新范围。", 409)
            command = data["command"]
            self_commands = {"self_accept", "self_start", "self_blocked", "self_claimed_done"}
            if not isinstance(command, str) or command not in self_commands | {"reported_progress", "result_note"}:
                raise AppError("invalid_progress", "只能登记本人声明、明确代录或结果说明。")
            if command in self_commands and item["responsible_account_id"] != actor["id"]:
                raise AppError("forbidden", "原述姓名或未知责任不能当本人账号承接。", 403)
            if command in self_commands and "reported_by" in data:
                raise AppError("invalid_progress", "本人声明不能另填他人陈述身份。")
            record = {"item_id": item_id, "item_revision": item["item_revision"], "command": command,
                      "evidence_class": "self_statement" if command in self_commands else "transcribed_statement",
                      "note": string(data["note"], "实际说明", 6000), "actor_id": actor["id"], "recorded_at": now()}
            if command == "reported_progress":
                record["reported_by"] = string(data.get("reported_by"), "实际陈述人", 200)
            elif command == "result_note" and "reported_by" in data:
                record["reported_by"] = string(data["reported_by"], "实际陈述人", 200)
            if "occurred_at" in data:
                occurred = string(data["occurred_at"], "明确发生时间", 100)
                try:
                    if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:$|T| )", occurred):
                        raise ValueError()
                    datetime.fromisoformat(occurred)
                except ValueError:
                    raise AppError("date_unconfirmed", "发生时间须为明示ISO日期或日期时间，不猜相对时间。") from None
                record["occurred_at"] = occurred
            self.store._event(connection, matter_id, "meeting_action_progress", dump(record))
            self.store._bump(connection, matter_id)
            matter = self.store._matter(connection, matter_id)
            return self._remember(connection, key, fingerprint, "progress", matter_id, self._view(connection, matter, facts, "meeting"))

    def _render_content(self, view, domain):
        lines = ["# 会议纪要结构准备稿" if domain == "meeting" else "# 公文分节结构准备稿", "", SCOPE, "当前为通用准备稿，无正式模板/文号/签发或送达声明。", ""]
        if view["stale"]:
            lines += ["原事实或来源已修订：结构及依据待重核。", ""]
        def section(item, heading):
            lines.extend(["## " + heading, "", item["text"] if domain == "meeting" else item["body"] or "正文待补", "", "核对范围：" + ("本人核对原记录" if item["confirmation"] == "local_checked" else "候选待核")])
            if domain == "meeting" and item["kind"] == "action":
                lines.extend(["原述责任：" + (item["responsible_text"] or "未知，待核"), "原述期限：" + (item["deadline_text"] or "未知，待核"),
                              "承接：" + PROGRESS_LABELS[item["progress"]["acceptance"]] + "；处理声明：" + PROGRESS_LABELS[item["progress"]["execution"]] + "；实际验收未知"])
                for event in item["progress"]["events"]:
                    lines.append("- " + ("旧条目范围：" if event["old_item_scope"] else "当前条目：") + PROGRESS_LABELS.get(event["command"], event["command"]) + " / " + PROGRESS_LABELS.get(event["evidence_class"], event["evidence_class"]) + " / " + event["note"])
            for ref in item["refs"]:
                lines.extend(["来源 " + ref["source_id"] + " 第" + str(ref["start_line"]) + "–" + str(ref["end_line"]) + "行：", ref["excerpt"]])
            lines.append("")
        if domain == "document":
            for item in view["sections"]:
                section(item, item["heading"] or "未命名段落")
        else:
            categories = [("discussion", "讨论"), ("proposal", "提议"), ("decision", "决定"), ("dissent", "分歧"), ("action", "行动与独立进度")]
            explicit = [item for item in view["items"] if item["kind"] == "decision" and item["confirmation"] == "local_checked"]
            if not explicit:
                lines += ["原记录未提供已核明确决定；提议与讨论不当决定。", ""]
            for kind, label in categories:
                for item in view["items"]:
                    if item["kind"] == kind:
                        heading = "记录中的明确决定（附依据）" if kind == "decision" and item["confirmation"] == "local_checked" else "待核决定候选" if kind == "decision" else label
                        section(item, heading)
        return "\n".join(lines)

    def _render(self, matter_id, data, key, domain):
        _strict(data, {"expected_version", "input_revision", "structure_version"})
        with self.store.transaction() as connection:
            actor, matter, facts = self._owner(connection, matter_id, domain)
            key, fingerprint, previous = self._request(connection, actor, "render_" + domain, matter_id, data, key)
            if previous is not None:
                return previous
            self.store._check_version(matter, data)
            if matter["assistant_status"] in {"paused", "handoff"}:
                raise AppError("assistant_stopped", "未来整理已停止；可继续编辑或取出已有成果，恢复后再渲染新稿。", 409)
            structure = self._structure(facts, domain)
            if type(data["input_revision"]) is not int or data["input_revision"] != matter["revision_no"] or type(data["structure_version"]) is not int or data["structure_version"] != structure["structure_version"]:
                raise AppError("structure_conflict", "事实或结构版本已变化，旧结果不能发布。", 409)
            view = self._view(connection, matter, facts, domain)
            if view["stale"]:
                raise AppError("structure_stale", "原事实或来源已修订；请明确重新保存核对结构后再生成新稿，旧稿保留。", 409)
            items = view["items"] if domain == "meeting" else view["sections"]
            self._ensure_preparation_sources(connection, matter_id, [ref['source_id'] for item in items for ref in item['refs']])
            if not items or (domain == "document" and not any(item["body"].strip() for item in items)):
                raise AppError("structure_empty", "未保存有实质内容的结构，不能生成空成果。")
            content, artifact_id = self._render_content(view, domain), uid()
            artifact_type = "meeting_minutes" if domain == "meeting" else "document_structured"
            connection.execute("INSERT INTO artifacts(id,version,matter_id,type,title,content,status,input_revision,created_at) VALUES(?,1,?,?,?,?,?,?,?)",
                               (artifact_id, matter_id, artifact_type, "会议纪要结构稿" if domain == "meeting" else "公文分节结构稿", content, "structured_draft", matter["revision_no"], now()))
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, "structure_rendered", "已生成新的私有结构成果；人工稿未覆盖，未正式签发或发送。")
            return self._remember(connection, key, fingerprint, "render_" + domain, matter_id,
                                  {"artifact_id": artifact_id, "version": 1, "type": artifact_type, "input_revision": matter["revision_no"], "content": content, "scope": SCOPE})

    def meeting_render(self, matter_id, data, key):
        return self._render(matter_id, data, key, "meeting")

    def document_render(self, matter_id, data, key):
        return self._render(matter_id, data, key, "document")
