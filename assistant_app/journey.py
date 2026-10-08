"""Private journey preparation, voluntary meal requests and source-linked materials."""

import json
import re
from datetime import date, datetime, timedelta, timezone

from .preparation import DOMAINS
from .store import AppError, dump, now, string, uid
from .structured import SAFE_ID, StructuredService, _nullable, _strict

SCOPE = "本人私有行程与材料准备；声明不代表现场核验、安全批准、派车、扣餐、证明签发或通行许可"
MATERIAL_KINDS = ("passport", "visa", "domestic_proof", "nis_entry", "nis_exit")
SEMANTICS = {"departure_kind": "camp_departure", "return_kind": "camp_return", "comparison_offset": None}
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
POSITION_RE = re.compile(r"第([1-9][0-9]*)–([1-9][0-9]*)行")
LABELS = {'needs_review': '信息或口径不足，待核', 'explicit_instants': '明确带偏移时间', 'legacy_timezone_pending': '旧时间口径或时区待核', 'date_range_time_pending': '日期范围明确，具体时间待核', 'mixed_precision_pending': '混合日期精度，具体时段待核', 'unknown': '未知，待核', 'candidate': '候选待核', 'confirmed': '本人已确认', 'departed': '已离营声明', 'returned': '已返营声明', 'not_travelled': '未出行声明', 'self_statement': '本人声明', 'transcribed_statement': '人工转录', 'keep': '希望保餐', 'request_reduce': '申请调整需求', 'pending': '待核', 'passport': '护照材料', 'visa': '签证材料', 'domestic_proof': '国内真实证明', 'nis_entry': '入境卡材料', 'nis_exit': '离境卡材料', 'missing': '未提供', 'source_provided': '已提供来源', 'local_source_checked': '本人已核所给来源'}


def _date(value, label):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise AppError("invalid_date", label + "须为明确ISO年月日。")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise AppError("invalid_date", label + "日期无效。") from None


def _instant(value):
    if not isinstance(value, str) or not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T", value):
        raise AppError("invalid_datetime", "发生时间须为带明确UTC偏移的ISO日期时间，未知可留null。")
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except ValueError:
        raise AppError("invalid_datetime", "日期时间缺失明确UTC偏移或无效，不能补默认时区。") from None


def _id(value):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise AppError("invalid_id", "明细ID须为1至80位安全字母数字、下划线或短横线。")
    return value


class JourneyService(StructuredService):
    def _owned(self, connection, matter_id, leave_only=False):
        matter = self.store._matter(connection, matter_id)
        if matter["domain"] not in ({"leave"} if leave_only else {"travel", "leave"}):
            raise AppError("invalid_domain", "本接口仅适用于出行或休假事项。")
        return self._owner(connection, matter_id, matter["domain"])

    def _limit(self, data):
        try:
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 131072:
                raise ValueError()
        except (TypeError, ValueError, UnicodeError):
            raise AppError("invalid_input", "请求须为有效JSON，最多128KiB。") from None

    def _baseline(self, matter, data, revision=True):
        self.store._check_version(matter, data)
        if revision and (type(data["input_revision"]) is not int or data["input_revision"] != matter["revision_no"]):
            raise AppError("revision_conflict", "计划或来源修订已变化，请读回后保留人工修改。", 409)

    def _meta(self, facts):
        return facts.get("__journey", {"schema_version": 1, "input_revision": None, "plan_semantics": dict(SEMANTICS), "meal_requests": [], "travel_link": None})

    def _position(self, connection, matter_id, source_id, position, require_usable=False):
        source_id = string(source_id, "来源ID", 80)
        if not connection.execute("SELECT id FROM sources WHERE id=? AND matter_id=?", (source_id, matter_id)).fetchone():
            raise AppError("not_found", "本事项来源不存在。", 404)
        position = string(position, "来源位置", 100)
        match = POSITION_RE.fullmatch(position)
        if not match:
            raise AppError("invalid_position", "来源位置须为第N–M行，1-based含首尾，连接符为–。")
        start, end = map(int, match.groups())
        refs = self._refs(connection, matter_id, [{"source_id": source_id, "start_line": start, "end_line": end}], require_usable=require_usable)
        return refs[0]["excerpt"]

    def _effective(self, connection, matter, facts):
        link = self._meta(facts)["travel_link"]
        if link is None:
            return facts, self._meta(facts)["plan_semantics"], False
        target = self.store._matter(connection, link["matter_id"])
        if target["domain"] != "travel":
            raise AppError("not_found", "关联出行不存在。", 404)
        row = connection.execute("SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?", (target["id"], link["revision_no"])).fetchone()
        if not row:
            raise AppError("not_found", "关联出行修订不存在。", 404)
        linked = json.loads(row[0])
        return linked, self._meta(linked)["plan_semantics"], target["revision_no"] != link["revision_no"]

    def _time_assessment(self, facts, semantics):
        def value(key):
            entry = facts.get(key, {})
            return entry.get("value") if entry.get("status") == "confirmed" else None
        start, end = value("departure_at"), value("return_at")
        if not start or not end:
            return "needs_review"
        try:
            sd, ed = bool(DATE_RE.fullmatch(start)), bool(DATE_RE.fullmatch(end))
            first = date.fromisoformat(start) if sd else datetime.fromisoformat(start)
            last = date.fromisoformat(end) if ed else datetime.fromisoformat(end)
        except (TypeError, ValueError):
            return "needs_review"
        offset = semantics.get("comparison_offset")
        if not sd and not ed:
            if (first.utcoffset() is None) != (last.utcoffset() is None):
                return "legacy_timezone_pending"
            if last < first:
                raise AppError("time_conflict", "计划返营早于相同口径的离营，请核对原输入。")
            return "explicit_instants" if first.utcoffset() is not None else "legacy_timezone_pending"
        if sd and ed:
            if last < first:
                raise AppError("time_conflict", "明确返营日期早于离营日期。")
            return "date_range_time_pending"
        # Mixed precision proves reversal only in an explicitly declared common date offset.
        if offset:
            sign = 1 if offset[0] == "+" else -1
            zone = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:])))
            first_date = first if sd else first.astimezone(zone).date() if first.utcoffset() is not None else first.date()
            last_date = last if ed else last.astimezone(zone).date() if last.utcoffset() is not None else last.date()
            if last_date < first_date:
                raise AppError("time_conflict", "明确同日期口径下，返营日期早于离营日期。")
        elif (sd and not ed and last.utcoffset() is None and last.date() < first) or (ed and not sd and first.utcoffset() is None and last < first.date()):
            raise AppError("time_conflict", "旧本地同口径计划的日期已证明倒置，请核对。")
        return "mixed_precision_pending"

    def _events(self, connection, matter):
        result = []
        for row in connection.execute("SELECT * FROM events WHERE matter_id=? AND event_type='journey_movement' ORDER BY rowid", (matter["id"],)):
            record = json.loads(row["message"])
            record.update(id=row["id"], recorded_by=row["recorded_by"], recorded_at=row["recorded_at"], stale=record["input_revision"] != matter["revision_no"])
            if record.get("source_id"):
                record["excerpt"] = self._position(connection, matter["id"], record["source_id"], record["source_position"])
            result.append(record)
        replacements = {record["supersedes_event_id"]: record["id"] for record in result if record.get("supersedes_event_id")}
        for record in result:
            record["superseded_by"] = replacements.get(record["id"])
        return result

    def _view(self, connection, matter, facts):
        meta = self._meta(facts)
        effective, semantics, link_stale = self._effective(connection, matter, facts)
        plan_fields = {key: effective.get(key, {"value": None, "status": "unknown"}) for key in DOMAINS["travel"]["fields"]}
        for key in ("person", "leave_start", "leave_end", "flight_info"):
            if key in facts:
                plan_fields[key] = facts[key]
        try:
            assessment = self._time_assessment(effective, semantics)
        except AppError:
            assessment = "legacy_time_conflict_pending"
        journey_stale = meta["input_revision"] is not None and meta["input_revision"] != matter["revision_no"]
        last_plan, last_meals = 0, 0
        for row in connection.execute("SELECT event_type,message FROM events WHERE matter_id=? AND event_type IN ('journey_plan_saved','journey_meals_saved','journey_link_saved') ORDER BY rowid", (matter["id"],)):
            record = json.loads(row["message"])
            if row["event_type"] == "journey_meals_saved":
                last_meals = record["input_revision"]
            elif record.get("meal_recheck_required"):
                last_plan = record["input_revision"]
        meal_review = bool(meta["meal_requests"]) and (journey_stale or link_stale or last_plan > last_meals)
        journey = {**meta, "plan_fields": plan_fields, "effective_plan_semantics": semantics, "time_assessment": assessment,
                   "meal_rule_status": "unconfirmed", "meal_needs_review": meal_review, "link_stale": link_stale, "stale": journey_stale}
        journey["current_travel_revision"] = self.store._matter(connection, meta["travel_link"]["matter_id"])["revision_no"] if meta["travel_link"] else None
        materials = facts.get("__materials", {"schema_version": 1, "input_revision": None, "items": []})
        material_items = []
        for kind in MATERIAL_KINDS:
            item = next((dict(item) for item in materials["items"] if item["kind"] == kind), None)
            if item is None:
                item = {"id": None, "kind": kind, "availability": "missing", "check": "pending", "valid_until": None}
            if item.get("source_id"):
                item["excerpt"] = self._position(connection, matter["id"], item["source_id"], item["source_position"])
            item.update(applicability="unknown", official_receipt="unknown")
            material_items.append(item)
        materials = {**materials, "items": material_items, "missing": [item["kind"] for item in material_items if item["availability"] == "missing"], "stale": materials["input_revision"] is not None and materials["input_revision"] != matter["revision_no"] or link_stale}
        tracks = {key: {"status": "unknown", "label": label} for key, label in DOMAINS[matter["domain"]]["tracks"].items()}
        tracks.update({key: tracks.get(key, {"status": "unknown", "label": label}) for key, label in DOMAINS["travel"]["tracks"].items()})
        missing = [DOMAINS["travel"]["fields"][key] for key in DOMAINS["travel"]["required"] if plan_fields[key].get("status") != "confirmed"]
        waiting = [{"who": "本事项操作者", "reason": "计划缺项/口径待核：" + "、".join(missing + ([assessment] if assessment != "explicit_instants" else []))},
                   {"who": "实际责任人或原渠道", "reason": "安全批准、车辆、现场出返与餐次正式状态分别待核；无人数基线，不算扣餐人数。"}]
        if matter["domain"] == "leave":
            waiting.append({"who": "本人或实际提供者", "reason": "国内证明与入出境卡名称、适用、签发及真实回执逐项待核。"})
        return {"id": matter["id"], "version": matter["version"], "revision_no": matter["revision_no"], "journey": journey, "materials": materials,
                "stale": journey_stale or materials["stale"] or link_stale, "movements": self._events(connection, matter), "tracks": tracks, "waiting": waiting, "scope": SCOPE}

    def get_model_candidates(self, matter_id):
        with self.store.connect() as connection:
            _, matter, _ = self._owned(connection, matter_id)
            if matter['domain'] != 'travel':
                raise AppError('invalid_domain', '此入口仅用于出行计划候选。')
            if self.store.requires_auth and not set(self.store.principal()['capabilities']) & {'employee', 'clerk', 'cook'}:
                raise AppError('permission_denied', '当前账号未获私人出行准备职责。', 403)
        result = self._model_candidates(matter_id, 'travel', 'journey_candidates')
        result['scope'] = '已保存出行计划模型候选；读取不再调用。值和引用仅核逐字原文，不证明时间含义、授权同行、批准、派车、扣餐或实际出返。本人选择后填未保存草稿，逐项核对。'
        return result

    def get_journey(self, matter_id):
        with self.store.connect() as connection:
            _, matter, facts = self._owned(connection, matter_id)
            return self._view(connection, matter, facts)

    def _commit(self, connection, actor, matter, facts, operation, meal_recheck=False):
        revision = matter["revision_no"] + 1
        # Unrelated meal/material edits rebase only metadata that was already current.
        for name in ("__journey", "__materials"):
            if name in facts and facts[name]["input_revision"] == matter["revision_no"] and operation not in {"plan", "link"}:
                facts[name]["input_revision"] = revision
        name = "__materials" if operation == "materials" else "__journey"
        facts[name]["input_revision"] = revision
        connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter["id"], revision, dump(facts), now(), actor["id"]))
        self.store._cancel(connection, matter["id"], "行程或材料修订，停止旧准备；人工成果保留")
        status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
        self.store._bump(connection, matter["id"], revision_no=revision, preparation_status="needs_review", assistant_status=status)
        self.store._event(connection, matter["id"], "journey_" + operation + "_saved", dump({"input_revision": revision, "actor_id": actor["id"], "meal_recheck_required": meal_recheck}))
        return self._view(connection, self.store._matter(connection, matter["id"]), facts)

    def save_plan(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision", "field_changes", "plan_semantics"})
        self._limit(data)
        _strict(data["plan_semantics"], set(SEMANTICS))
        semantics = dict(data["plan_semantics"])
        if semantics["departure_kind"] != "camp_departure" or semantics["return_kind"] != "camp_return":
            raise AppError("invalid_semantics", "离返营含义须明确，航班时间不能替代营地出返。")
        offset = semantics["comparison_offset"]
        if offset is not None and (not isinstance(offset, str) or not re.fullmatch(r"[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]", offset)):
            raise AppError("invalid_offset", "比较口径须明确±HH:MM，未核可为null。")
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, "journey_plan", matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter, facts)
            self._baseline(matter, data)
            changes = data["field_changes"]
            if not isinstance(changes, dict) or set(changes) - set(DOMAINS[matter["domain"]]["fields"]):
                raise AppError("invalid_input", "仅允许本域列明计划字段，不能写元数据或正式状态。")
            meta = self._meta(facts)
            if meta["travel_link"] and set(changes) & {"departure_at", "return_at"}:
                raise AppError("linked_plan", "计划已固定引用出行修订，请回原出行修改或明确解除关联。", 409)
            recheck = semantics != meta["plan_semantics"]
            for field, entry in changes.items():
                _strict(entry, {"value", "status"})
                if entry["status"] not in ("unknown", "candidate", "confirmed"):
                    raise AppError("invalid_input", "事实只可明确未知、待核候选或本人核对。")
                value = _nullable(entry["value"], "计划原述", 2000)
                if entry["status"] == "confirmed" and value is None:
                    raise AppError("invalid_input", "未知值不能确认为已核。")
                if field in {"departure_at", "return_at", "leave_start", "leave_end"} and value:
                    if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ]", value):
                        _instant(value)
                    if entry["status"] == "confirmed":
                        _date(value, field) if field in {"leave_start", "leave_end"} or DATE_RE.fullmatch(value) else _instant(value)
                normalized = {"value": value, "status": entry["status"]}
                recheck = recheck or (field in {"departure_at", "return_at", "participants", "person"} and facts.get(field) != normalized)
                facts[field] = normalized
            self._time_assessment(facts, semantics)
            if matter["domain"] == "leave":
                first, last = facts.get("leave_start", {}), facts.get("leave_end", {})
                # Existing leave datetime text remains intact; new leave dates were validated above.
                if first.get("status") == last.get("status") == "confirmed" and first.get("value") and last.get("value") and _date(last["value"][:10], "休假结束") < _date(first["value"][:10], "休假开始"):
                    raise AppError("time_conflict", "休假结束日期早于开始日期。")
            facts["__journey"] = {**meta, "plan_semantics": semantics}
            return self._remember(connection, key, fingerprint, "journey_plan", matter_id, self._commit(connection, actor, matter, facts, "plan", recheck))

    def _incremental(self, previous, data, normalize):
        upserts, removed = data["upserts"], data["removed_ids"]
        if not isinstance(upserts, list) or not isinstance(removed, list) or len(upserts) > 100 or len(removed) > 100 or not (upserts or removed):
            raise AppError("invalid_rows", "每次最多100项；删除须明确列ID，空列表不会删除。")
        saved = {item["id"]: item for item in previous}
        if any(not isinstance(item_id, str) or item_id not in saved for item_id in removed) or len(set(removed)) != len(removed):
            raise AppError("invalid_rows", "只能明确删除本事项当前已有ID，不能重复。")
        seen = set()
        for item in upserts:
            item = normalize(item)
            if item["id"] in seen or item["id"] in removed:
                raise AppError("invalid_rows", "同次ID重复或同时删除，不能隐式合并。")
            seen.add(item["id"])
            saved[item["id"]] = item
        for item_id in removed:
            del saved[item_id]
        if len(saved) > 100:
            raise AppError("invalid_rows", "本事项最多100项。")
        return list(saved.values())

    def save_meals(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision", "upserts", "removed_ids"})
        self._limit(data)
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, "journey_meals", matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter, facts)
            self._baseline(matter, data)
            def normalize(item):
                _strict(item, {"id", "person_ref", "meal_date", "meal_slot", "request", "note"})
                _date(item["meal_date"], "餐次日期")
                if item["request"] not in ("keep", "request_reduce", "pending"):
                    raise AppError("invalid_rows", "仅可保存保餐希望、调整申请或待核需求。")
                if not isinstance(item["note"], str) or len(item["note"]) > 2000:
                    raise AppError("invalid_rows", "需求备注须为最多2000字文字，可空。")
                person = string(item["person_ref"], "本次已核人员原述", 200)
                slot = string(item["meal_slot"], "明确餐别标签", 100)
                if person in {"未知", "待核", "unknown"} or slot in {"未知", "待核", "unknown"}:
                    raise AppError("invalid_rows", "人员或餐别未知请保留需求原文，不进入已核明细。")
                return {**item, "id": _id(item["id"]), "person_ref": person, "meal_slot": slot, "note": item["note"].strip()}
            meta = self._meta(facts)
            items = self._incremental(meta["meal_requests"], data, normalize)
            identities = {(item["person_ref"], item["meal_date"], item["meal_slot"]) for item in items}
            if len(identities) != len(items):
                raise AppError("duplicate_meal", "相同明确人员、日期、餐别已存在需求，请保留一条而不重复计人数。")
            facts["__journey"] = {**meta, "meal_requests": items}
            return self._remember(connection, key, fingerprint, "journey_meals", matter_id, self._commit(connection, actor, matter, facts, "meals"))

    def save_materials(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision", "upserts", "removed_ids"})
        self._limit(data)
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id, True)
            key, fingerprint, previous = self._request(connection, actor, "journey_materials", matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter, facts)
            self._baseline(matter, data)
            def normalize(item):
                _strict(item, {"id", "kind", "availability", "provider_class", "valid_until", "check"}, {"provider_name", "source_id", "source_position", "label", "note"})
                if item["kind"] not in MATERIAL_KINDS or item["availability"] not in ("missing", "source_provided") or item["provider_class"] not in ("current_actor_statement", "transcribed_statement") or item["check"] not in ("pending", "local_source_checked"):
                    raise AppError("invalid_material", "材料类型、提供声明与本地核对状态须按列明值。")
                if item["valid_until"] is not None:
                    _date(item["valid_until"], "明示有效日期")
                source_id, position = item.get("source_id"), item.get("source_position")
                if item["availability"] == "missing":
                    if source_id or position or item["check"] != "pending" or item["valid_until"] is not None:
                        raise AppError("invalid_material", "未提供材料不能带来源、已核状态或推定有效日期。")
                else:
                    self._position(connection, matter_id, source_id, position, require_usable=True)
                if item["provider_class"] == "current_actor_statement":
                    if item.get("provider_name") not in (None, actor["display_name"]):
                        raise AppError("invalid_material", "本人提供声明不得填他人身份。")
                    provider = actor["display_name"]
                else:
                    provider = string(item.get("provider_name"), "实际材料提供者", 200)
                return {"id": _id(item["id"]), "kind": item["kind"], "availability": item["availability"], "provider_class": item["provider_class"], "provider_name": provider,
                        "source_id": source_id, "source_position": position, "label": _nullable(item.get("label"), "必要材料名称", 200), "valid_until": item["valid_until"], "check": item["check"], "note": _nullable(item.get("note"), "材料备注", 2000)}
            meta = facts.get("__materials", {"schema_version": 1, "input_revision": None, "items": []})
            items = self._incremental(meta["items"], data, normalize)
            if len({item["kind"] for item in items}) != len(items):
                raise AppError("duplicate_material", "每种材料仅一条当前记录，更正原ID并保留修订历史。")
            facts["__materials"] = {**meta, "items": items}
            return self._remember(connection, key, fingerprint, "journey_materials", matter_id, self._commit(connection, actor, matter, facts, "materials"))

    def record_movement(self, matter_id, data, key):
        _strict(data, {"expected_version", "kind", "note"}, {"person_ref", "occurred_at", "reported_by", "source_id", "source_position", "related_departure_event_id", "supersedes_event_id", "correction_reason"})
        self._limit(data)
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, "journey_movement", matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter, facts)
            self._baseline(matter, data, False)
            if data["kind"] not in ("departed", "returned", "not_travelled"):
                raise AppError("invalid_movement", "只能登记实际离营、返营或未出行声明。")
            transcribed = data.get("reported_by") is not None
            person = string(data.get("person_ref"), "本次人员原述", 200) if transcribed else actor["id"]
            if not transcribed and data.get("person_ref") not in (None, actor["id"]):
                raise AppError("invalid_movement", "本人声明人员由登录身份指定，他人原述须明确代录。")
            occurred = data.get("occurred_at")
            if occurred is not None:
                _instant(occurred)
            record = {"person_ref": person, "kind": data["kind"], "occurred_at": occurred, "evidence_class": "transcribed_statement" if transcribed else "self_statement",
                      "actor_id": actor["id"], "actor_name": actor["display_name"], "note": string(data["note"], "真实声明原话", 6000), "input_revision": matter["revision_no"]}
            if transcribed:
                record["reported_by"] = string(data["reported_by"], "实际陈述人", 200)
            if data.get("source_id") is not None or data.get("source_position") is not None:
                self._position(connection, matter_id, data.get("source_id"), data.get("source_position"), require_usable=True)
                record.update(source_id=data["source_id"], source_position=data["source_position"])
            events = self._events(connection, matter)
            event_id = data.get("related_departure_event_id")
            if event_id is not None:
                departure = next((event for event in events if event["id"] == event_id and event["kind"] == "departed" and event["person_ref"] == person and not event["superseded_by"]), None)
                if data["kind"] != "returned" or departure is None:
                    raise AppError("invalid_pair", "仅可明确关联本事项同人员仍适用的离营声明。")
                if occurred and departure["occurred_at"] and _instant(occurred) < _instant(departure["occurred_at"]):
                    raise AppError("time_conflict", "所配对实际返营时点早于离营，请核对实际声明。")
                record["related_departure_event_id"] = event_id
            if data.get("supersedes_event_id") is not None:
                old = next((event for event in events if event["id"] == data["supersedes_event_id"] and event["person_ref"] == person and not event["superseded_by"]), None)
                if old is None:
                    raise AppError("invalid_correction", "只能更正本事项同人员尚未替代的声明，原事件保留。")
                record.update(supersedes_event_id=old["id"], correction_reason=string(data.get("correction_reason"), "更正原因", 2000))
            elif "correction_reason" in data:
                raise AppError("invalid_correction", "更正原因须同时明确被替代原事件。")
            self.store._event(connection, matter_id, "journey_movement", dump(record))
            self.store._bump(connection, matter_id)
            return self._remember(connection, key, fingerprint, "journey_movement", matter_id, self._view(connection, self.store._matter(connection, matter_id), facts))

    def set_travel_link(self, matter_id, data, key):
        _strict(data, {"expected_version", "travel_matter_id", "travel_revision"})
        self._limit(data)
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id, True)
            key, fingerprint, previous = self._request(connection, actor, "journey_link", matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter, facts)
            self._baseline(matter, data, False)
            target_id, revision = data["travel_matter_id"], data["travel_revision"]
            if target_id is None:
                if revision is not None:
                    raise AppError("invalid_link", "解除关联时两引用须皆为null。")
                link = None
            else:
                target_id = string(target_id, "关联出行ID", 80)
                target = self.store._matter(connection, target_id)
                if target["domain"] != "travel" or type(revision) is not int or revision < 1:
                    raise AppError("invalid_link", "须明确同owner出行事项及固定修订号。")
                row = connection.execute("SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?", (target_id, revision)).fetchone()
                if not row:
                    raise AppError("not_found", "出行修订不存在。", 404)
                linked = json.loads(row[0])
                for field in ("departure_at", "return_at"):
                    old, new = facts.get(field, {}), linked.get(field, {})
                    if old.get("value") and old.get("value") != new.get("value"):
                        raise AppError("link_conflict", "本事项已有不同出返原述，请明确选定权威计划并保留历史后再关联。", 409)
                link = {"matter_id": target_id, "revision_no": revision}
            facts["__journey"] = {**self._meta(facts), "travel_link": link}
            return self._remember(connection, key, fingerprint, "journey_link", matter_id, self._commit(connection, actor, matter, facts, "link", True))

    def render(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision"})
        self._limit(data)
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, "journey_render", matter_id, data, key)
            if previous is not None:
                return previous
            self._baseline(matter, data)
            if matter["assistant_status"] in {"paused", "handoff"}:
                raise AppError("assistant_stopped", "未来准备已停止；实际声明与人工修正仍可保存。", 409)
            self._ensure_preparation_sources(connection, matter_id, [item['source_id'] for item in facts.get('__materials', {}).get('items', []) if item.get('source_id')])
            view = self._view(connection, matter, facts)
            lines = ["# 行程与材料联动准备稿", "", SCOPE, "共同事项修订：" + str(matter["revision_no"]), "生成方式：本地确定性模板。", ""]
            for heading, body in (("安全准备", "安全真实批准未知；沿原渠道核适用人员、地点、时段和修订。"), ("用车需求", "真实车辆安排未知；未派车，不保证车辆可用。")):
                lines += ["## " + heading, body, ""]
            lines += ["## 计划与实际声明", "计划时间核对：" + LABELS.get(view["journey"]["time_assessment"], '具体时间口径待核')]
            if view["stale"]:
                lines.append("旧来源/计划/关联已变化，以下内容待重核，不自动跟随新行程。")
            for field, entry in view["journey"]["plan_fields"].items():
                field_label = DOMAINS['leave']['fields'].get(field, DOMAINS['travel']['fields'].get(field, field))
                lines.append("- " + field_label + "：" + (entry.get("value") or "未知") + " / " + LABELS.get(entry.get("status", "unknown"), '待核'))
            lines.append("实际现场核验未知；以下各为独立本人/代录声明，计划不填实际。")
            for record in view["movements"]:
                person = actor['display_name'] if record['person_ref'] == actor['id'] else record['person_ref']
                lines.append("- " + LABELS[record["kind"]] + " / " + person + " / " + LABELS[record["evidence_class"]] + " / " + (record["occurred_at"] or "发生时间未知") + " / " + record["note"] + (" / 旧修订声明" if record["stale"] else "") + (" / 已由新声明更正" if record["superseded_by"] else ""))
            lines += ["", "## 餐次人工需求", "饭点、触发、截止、应报基线待核；不计算扣餐人数或实际供餐。"]
            if view["journey"]["meal_needs_review"]:
                lines.append("计划已变化，原需求待重核；保餐人工例外保留。")
            for item in view["journey"]["meal_requests"]:
                lines.append("- " + " / ".join((item['person_ref'], item['meal_date'], item['meal_slot'], LABELS[item['request']], item['note'])))
            if matter["domain"] == "leave":
                lines += ["", "## 材料清单", "名称、适用与正式回执待核；本地来源核对不当官方办理。"]
                for item in view["materials"]["items"]:
                    lines.append("- " + LABELS[item["kind"]] + " / " + LABELS[item["availability"]] + " / " + LABELS[item["check"]] + " / 明示有效日期：" + (item["valid_until"] or "未知"))
                    if item.get("source_id"):
                        lines.append("来源 " + item["source_id"] + " " + item["source_position"] + "（仅私有来源引用；未复制证照完整原件）。")
            content, artifact_id = "\n".join(lines), uid()
            connection.execute("INSERT INTO artifacts(id,version,matter_id,type,title,content,status,input_revision,created_at) VALUES(?,1,?,?,?,?,?,?,?)", (artifact_id, matter_id, "journey_preparation", "行程与材料联动准备稿", content, "structured_draft", matter["revision_no"], now()))
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, "journey_rendered", "已保存同修订四轨准备成果；旧稿及人工稿保留，未推进任何正式状态。")
            return self._remember(connection, key, fingerprint, "journey_render", matter_id, {"artifact_id": artifact_id, "version": 1, "type": "journey_preparation", "input_revision": matter["revision_no"], "content": content, "scope": SCOPE})
