"""Local service projections, independent actor statements, and revocable assignments."""

import hashlib
import json
import re
from datetime import datetime

from .store import AppError, dump, now, string, uid

SERVICE_SCHEMA = """
CREATE TABLE service_cases(
 id TEXT PRIMARY KEY,matter_id TEXT NOT NULL UNIQUE REFERENCES matters(id),
 requester_id TEXT NOT NULL REFERENCES accounts(id),clerk_id TEXT NOT NULL REFERENCES accounts(id),
 executor_id TEXT REFERENCES accounts(id),input_revision INTEGER NOT NULL,projection_json TEXT NOT NULL,
 sharing_status TEXT NOT NULL CHECK(sharing_status IN ('shared','withdrawn')),
 assignment_epoch INTEGER NOT NULL,version INTEGER NOT NULL,
 assignment_status TEXT NOT NULL CHECK(assignment_status IN ('unassigned','assigned','withdrawn')),
 acceptance_status TEXT NOT NULL CHECK(acceptance_status IN ('pending','accepted','declined')),
 execution_status TEXT NOT NULL CHECK(execution_status IN ('not_reported','in_progress','blocked','claimed_done')),
 requester_result TEXT NOT NULL CHECK(requester_result IN ('unknown','unverified','resolved','still_problem')),
 inspection_status TEXT NOT NULL CHECK(inspection_status='unknown'),
 closure_status TEXT NOT NULL CHECK(closure_status IN ('open','waiting_rule')),updated_at TEXT NOT NULL);
CREATE INDEX service_requester ON service_cases(requester_id);
CREATE INDEX service_clerk ON service_cases(clerk_id,sharing_status);
CREATE INDEX service_executor ON service_cases(executor_id,sharing_status,assignment_status);
"""
SCOPE = "本地独立账号服务记录；分派、本人声明、发起人结果分别记录，不代表现场系统同步或正式闭单"


def _strict(data, required, optional=()):
    if not isinstance(data, dict) or not required.issubset(data) or set(data) - required - set(optional):
        raise AppError("invalid_input", "请求仅允许当前接口列明字段，不能指定操作者或替他人反馈。")


def _optional(value, label, limit=2000):
    if not isinstance(value, str) or len(value) > limit:
        raise AppError("invalid_input", label + "须为限长文字。")
    return value.strip()


class ServiceService:
    def __init__(self, store):
        self.store = store

    def _draft_scope(self, connection, service_id):
        actor = self._actor(connection)
        case = self._case(connection, service_id, actor)
        self._capability(actor, "clerk")
        if case["clerk_id"] != actor["id"] or case["sharing_status"] != "shared":
            raise AppError("forbidden", "仅当前选定经办可维护本人服务跟进稿。", 403)
        return actor, case

    def _draft_kind(self, service_id, actor_id):
        return "request:service_draft:" + service_id + ":" + actor_id

    def _draft_view(self, connection, case, actor):
        rows = connection.execute("SELECT response FROM actions WHERE kind=? ORDER BY rowid DESC LIMIT 20", (self._draft_kind(case["id"], actor["id"]),)).fetchall()
        saved = [json.loads(row["response"]) for row in rows]
        view = self._view(connection, case, actor)
        if saved:
            draft = saved[0]
        else:
            labels = {"unassigned": "尚未安排", "assigned": "已在本工作区安排", "withdrawn": "安排已撤回", "pending": "等待本人承接", "accepted": "本人已接受", "declined": "本人拒接",
                      "not_reported": "尚无处理声明", "in_progress": "本人声明处理中", "blocked": "本人报告障碍", "claimed_done": "本人声明已处理", "unknown": "未知", "unverified": "本人未验证", "resolved": "本人认为已解决", "still_problem": "本人仍有问题", "open": "未正式关闭", "waiting_rule": "关闭规则待核"}
            projection = view["projection"]
            lines = ["# 服务跟进准备稿", "", "本稿仅为经办私人文字准备，未发送、未送达，不构成验收或正式闭单。", "",
                     "## 已共享的必要需求", "地点：" + (projection.get("location") or "待核"), "问题／清洁范围：" + projection["request_text"],
                     "可进入时段：" + (projection.get("access_window") or "待核"), "进入许可：" + (projection["entry_permission"].get("note") or "待核，不能据此入室"),
                     "", "## 当前独立记录（服务版本 " + str(case["version"]) + "，分派轮次 " + str(case["assignment_epoch"]) + "）"]
            for track, label in (("assignment", "安排"), ("acceptance", "承接"), ("execution", "处理声明"), ("requester_result", "发起人结果"), ("inspection", "专业验收"), ("closure", "正式关闭")):
                value = view["tracks"][track]
                lines.append(label + "：" + labels.get(value, "待核"))
            lines.extend(["", "## 已获授权可见的最近声明"])
            for event in view["events"][-10:]:
                if event.get("note"):
                    evidence = event.get("evidence_class")
                    label = "本人声明原话" if evidence in {"executor_statement", "requester_statement"} else "软件动作／记录说明"
                    if evidence == "unverified_transcription":
                        label = "代录候选，原陈述人：" + event.get("reported_by", "待核")
                    lines.append(event["actor_display_name"] + "（" + event["recorded_at"] + "，" + label + "）：" + event["note"])
            lines.extend(["", "## 等待与人工下一步"])
            lines.extend("- " + item["who_name"] + "：" + item["reason"] for item in view["waiting"])
            lines.append("请在此补充实际跟进文字；发送、结果与正式关闭各沿实际渠道核对。")
            content = "\n".join(lines)
            if len(content) > 12000:
                content = content[:11900] + "\n\n预览长度已达上限，后续内容未列入；请在服务原记录中核对完整历史。"
            draft = {"service_id": case["id"], "version": 0, "basis_service_version": case["version"], "basis_input_revision": case["input_revision"], "content": content, "saved_at": None}
        return {**draft, "current_service_version": case["version"], "current_input_revision": case["input_revision"],
                "stale": draft["basis_service_version"] != case["version"] or view["stale"],
                "history": [{key: item[key] for key in ("version", "basis_service_version", "saved_at")} for item in saved],
                "history_limit": 20, "scope": "仅当前经办本人可读的服务跟进文字；历史另存，不共享给发起人或执行者，不进入模型，不改变服务状态。"}

    def get_draft(self, service_id, version=None):
        with self.store.connect() as connection:
            actor, case = self._draft_scope(connection, service_id)
            result = self._draft_view(connection, case, actor)
            if version is None:
                return result
            if type(version) is not int or not 1 <= version <= 1000000:
                raise AppError("invalid_input", "历史稿版本须为有效正整数。")
            for row in connection.execute("SELECT response FROM actions WHERE kind=? ORDER BY rowid DESC", (self._draft_kind(service_id, actor["id"]),)):
                record = json.loads(row["response"])
                if record["version"] == version:
                    return {**result, **record, "latest_version": result["version"], "stale": result["stale"] or record["basis_service_version"] != case["version"], "read_only_history": True}
            raise AppError("not_found", "本人历史跟进稿版本不存在。", 404)

    def save_draft(self, service_id, data, key):
        _strict(data, {"expected_version", "base_version", "content"})
        content = string(data["content"], "跟进正文", 12000)
        with self.store.transaction() as connection:
            actor, case = self._draft_scope(connection, service_id)
            key, fingerprint, replay = self._request(connection, actor, "draft_save", service_id, data, key)
            current = self._draft_view(connection, case, actor)
            if replay:
                return current
            self._version(case, data)
            if type(data["base_version"]) is not int or data["base_version"] != current["version"]:
                raise AppError("version_conflict", "跟进稿已有新保存版本；保留你的正文，读回后明确核对采用当前基版。", 409)
            saved = {"service_id": service_id, "version": current["version"] + 1, "basis_service_version": case["version"], "basis_input_revision": case["input_revision"], "content": content, "saved_at": now()}
            # Private text is kept in actor-scoped request records, never in the
            # requester-visible matter artifacts/events or shared service projection.
            connection.execute("INSERT INTO actions(id,matter_id,kind,status,idempotency_key,fingerprint,response,created_at) VALUES(?,?,?,'completed',?,?,?,?)",
                               (uid(), case["matter_id"], self._draft_kind(service_id, actor["id"]), key, fingerprint, dump(saved), now()))
            return self._draft_view(connection, case, actor)

    def _actor(self, connection):
        principal = self.store.principal()
        if not isinstance(principal, dict):
            raise AppError("authentication_required", "请先登录自己的账号。", 401)
        row = connection.execute("SELECT * FROM accounts WHERE id=?", (principal.get("id"),)).fetchone()
        if not row or row["state"] != "active" or row["auth_epoch"] != principal.get("auth_epoch"):
            raise AppError("authentication_required", "登录或职责已失效，请重新登录。", 401)
        return {"id": row["id"], "display_name": row["display_name"], "capabilities": json.loads(row["capabilities"])}

    def _capability(self, actor, capability):
        if capability not in actor["capabilities"]:
            raise AppError("forbidden", "当前账号没有此服务职责。", 403)

    def _selected(self, connection, account_id, capability):
        if not isinstance(account_id, str):
            raise AppError("invalid_input", "须明确选择实际账号。")
        row = connection.execute("SELECT state,capabilities FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row or row["state"] != "active" or capability not in json.loads(row["capabilities"]):
            raise AppError("invalid_input", "选定账号不存在、已停用或缺少对应职责。")

    def eligible_accounts(self, capability):
        with self.store.connect() as connection:
            actor = self._actor(connection)
            if capability == "clerk":
                if not {"employee", "clerk"}.intersection(actor["capabilities"]):
                    raise AppError("forbidden", "仅服务发起人职责可选择经办。", 403)
            elif capability == "executor":
                self._capability(actor, "clerk")
            else:
                raise AppError("invalid_input", "仅提供经办或执行者的必要候选。")
            rows = connection.execute("SELECT id,display_name,capabilities FROM accounts WHERE state='active' ORDER BY display_name,id").fetchall()
            return {"items": [{"id": row["id"], "display_name": row["display_name"]} for row in rows if capability in json.loads(row["capabilities"])],
                    "scope": "仅已启用且具有选定职责的账号称呼；不代表现场自动职责映射"}

    def _readable(self, case, actor):
        if case["requester_id"] == actor["id"]:
            return True
        return case["sharing_status"] == "shared" and ((case["clerk_id"] == actor["id"] and "clerk" in actor["capabilities"]) or
               (case["executor_id"] == actor["id"] and case["assignment_status"] == "assigned" and "executor" in actor["capabilities"]))

    def _case(self, connection, service_id, actor):
        row = connection.execute("SELECT * FROM service_cases WHERE id=?", (service_id,)).fetchone()
        if not row or not self._readable(row, actor):
            raise AppError("not_found", "服务不存在或当前无权访问。", 404)
        return dict(row)

    def _tracks(self, case):
        return {"assignment": case["assignment_status"], "acceptance": case["acceptance_status"], "execution": case["execution_status"],
                "requester_result": case["requester_result"], "inspection": case["inspection_status"], "closure": case["closure_status"]}

    def _view(self, connection, case, actor):
        current_revision = connection.execute("SELECT revision_no FROM matters WHERE id=?", (case["matter_id"],)).fetchone()[0]
        projection = json.loads(case['projection_json'])
        events = []
        executor_view = actor["id"] != case["requester_id"] and actor["id"] != case["clerk_id"]
        for row in connection.execute("SELECT message FROM events WHERE matter_id=? AND event_type='service_event' ORDER BY recorded_at,rowid", (case["matter_id"],)):
            event = json.loads(row[0])
            if event["service_id"] != case["id"]:
                continue
            if executor_view and (event["assignment_epoch"] != case["assignment_epoch"] or
                                  (event["actor_id"] != actor["id"] and event["command"] not in {"assign", "revise"})):
                continue
            if not event.get('actor_display_name'):
                recorded = connection.execute('SELECT display_name FROM accounts WHERE id=?', (event['actor_id'],)).fetchone()
                event['actor_display_name'] = recorded['display_name'] if recorded else '原操作者名称待核'
            if actor['id'] != case['requester_id'] and event['actor_id'] == case['requester_id']:
                event['actor_display_name'] = projection.get('requester_display_name') or '服务发起人'
            events.append(event)
        waiting, steps = [], []
        if projection["entry_permission"]["status"] == "unknown":
            waiting.append({"who": case["requester_id"], "reason": "进入许可待核，不能据此入室"})
        if case["sharing_status"] == "withdrawn":
            steps.append("共享已撤回；历史保留，不表示现场工作已经停止")
        elif case["assignment_status"] != "assigned" or case["acceptance_status"] == "declined":
            waiting.append({"who": case["clerk_id"], "reason": "等待实际经办安排或重新安排执行者"})
            steps.append("经办明确选择执行者，保留安排说明")
        elif case["acceptance_status"] == "pending":
            waiting.append({"who": case["executor_id"], "reason": "等待本人接受或拒接"})
        elif case["execution_status"] == "blocked":
            waiting.append({"who": case["clerk_id"], "reason": "执行者报告障碍，待实际经办处置"})
        elif case["execution_status"] == "claimed_done" and case["requester_result"] in {"unknown", "unverified"}:
            waiting.append({"who": case["requester_id"], "reason": "执行者仅声明已处理，等待发起人本人结果"})
        if case["requester_result"] == "still_problem":
            waiting.append({"who": case["clerk_id"], "reason": "发起人仍有问题，等待重新安排；历史处理声明保留"})
        if case["closure_status"] == "waiting_rule":
            waiting.append({"who": "实际关闭或验收责任人待核", "reason": "关闭口径未核，不能正式闭单"})
        stale = current_revision != case["input_revision"]
        if stale:
            waiting.append({"who": case["requester_id"], "reason": "原事项已修订，服务仍为旧快照；须明确更新需求"})
        if case["sharing_status"] == "shared":
            clerk = connection.execute("SELECT state,capabilities FROM accounts WHERE id=?", (case["clerk_id"],)).fetchone()
            if not clerk or clerk["state"] != "active" or "clerk" not in json.loads(clerk["capabilities"]):
                waiting.append({"who": case["requester_id"], "reason": "原经办账号已停用或职责失效，待本人重新明确交办；不会自动替换"})
            if case["executor_id"]:
                executor = connection.execute("SELECT state,capabilities FROM accounts WHERE id=?", (case["executor_id"],)).fetchone()
                if not executor or executor["state"] != "active" or "executor" not in json.loads(executor["capabilities"]):
                    waiting = [item for item in waiting if item["who"] != case["executor_id"]]
                    waiting.append({"who": case["clerk_id"], "reason": "当前执行者账号已停用或职责失效，待实际经办处置；原声明保留，不自动替换"})
        participants = {}
        for role in ('requester', 'clerk', 'executor'):
            account_id = case[role + '_id']
            if account_id:
                participant = connection.execute('SELECT display_name FROM accounts WHERE id=?', (account_id,)).fetchone()
                participants[role] = {'id': account_id, 'display_name': participant['display_name'] if participant else '名称待核'}
                if role == 'requester' and actor['id'] != account_id:
                    participants[role]['display_name'] = projection.get('requester_display_name') or '服务发起人'
        names = {person['id']: person['display_name'] for person in participants.values()}
        for item in waiting:
            item['who_name'] = names.get(item['who'], item['who'])
        view = {"id": case["id"], "version": case["version"], "assignment_epoch": case["assignment_epoch"], "input_revision": case["input_revision"],
                "stale": stale, "projection": projection, "sharing_status": case["sharing_status"], "requester_id": case["requester_id"],
                "clerk_id": case["clerk_id"], "executor_id": case["executor_id"], "tracks": self._tracks(case), "events": events,
                "waiting": waiting, "participants": participants, "next_steps": steps, "scope": SCOPE}
        if actor["id"] == case["requester_id"]:
            view["matter_id"] = case["matter_id"]
        return view

    def list_services(self):
        with self.store.connect() as connection:
            actor = self._actor(connection)
            cases = connection.execute("SELECT * FROM service_cases ORDER BY updated_at DESC,rowid DESC").fetchall()
            return {"items": [self._view(connection, dict(case), actor) for case in cases if self._readable(case, actor)], "scope": SCOPE}

    def get_service(self, service_id):
        with self.store.connect() as connection:
            actor = self._actor(connection)
            return self._view(connection, self._case(connection, service_id, actor), actor)

    def _version(self, case, data, epoch=False):
        if type(data.get("expected_version")) is not int or data["expected_version"] != case["version"]:
            raise AppError("version_conflict", "服务记录已变化，请读回后保留你的原输入再核对。", 409)
        if epoch and (type(data.get("assignment_epoch")) is not int or data["assignment_epoch"] != case["assignment_epoch"]):
            raise AppError("assignment_conflict", "分派已变化，原接受不能适用于新范围。", 409)

    def _request(self, connection, actor, operation, object_id, data, key):
        key = string(key, "请求键", 200)
        fingerprint = hashlib.sha256(dump([actor["id"], "service:" + operation, object_id, data]).encode()).hexdigest()
        previous = connection.execute("SELECT fingerprint,response FROM actions WHERE idempotency_key=?", (key,)).fetchone()
        if previous and previous["fingerprint"] != fingerprint:
            raise AppError("idempotency_conflict", "请求键已用于不同内容或身份。", 409)
        return key, fingerprint, previous is not None

    def _event(self, connection, case, actor, command, before, note="", **extra):
        record = {"service_id": case["id"], "assignment_epoch": case["assignment_epoch"], "command": command,
                  "actor_id": actor["id"], "actor_display_name": actor['display_name'], "recorded_by": actor["id"], "evidence_class": "independent_account_action",
                  "recorded_at": now(), "note": note, "before": before, "after": self._tracks(case), **extra}
        connection.execute("INSERT INTO events(id,matter_id,event_type,message,recorded_by,recorded_at) VALUES(?,?,'service_event',?,?,?)",
                           (uid(), case["matter_id"], dump(record), actor["id"], now()))

    def _update(self, connection, case, **changes):
        changes.update(version=case["version"] + 1, updated_at=now())
        connection.execute("UPDATE service_cases SET " + ",".join(key + "=?" for key in changes) + " WHERE id=?", (*changes.values(), case["id"]))
        return {**case, **changes}

    def _remember(self, connection, key, fingerprint, operation, case, actor):
        response = self._view(connection, case, actor)
        connection.execute("INSERT INTO actions(id,matter_id,kind,status,idempotency_key,fingerprint,response,created_at) VALUES(?,?,?,'completed',?,?,?,?)",
                           (uid(), case["matter_id"], "request:service_" + operation, key, fingerprint, dump(response), now()))
        return response

    def _projection(self, connection, matter, data, prior=None):
        if matter["domain"] not in {"repair", "cleaning"}:
            raise AppError("invalid_input", "当前仅报修与清洁支持服务交办。")
        if type(data.get("input_revision")) is not int or data["input_revision"] != matter["revision_no"]:
            raise AppError("version_conflict", "原事项事实修订已变化，请重新核对。", 409)
        facts = json.loads(connection.execute("SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?", (matter["id"], matter["revision_no"])).fetchone()[0])
        def confirmed(key):
            item = facts.get(key, {})
            return item.get("value") if item.get("status") == "confirmed" and isinstance(item.get("value"), str) else ""
        request_field = "problem" if matter["domain"] == "repair" else "scope"
        missing = [label for key, label in (("location", "地点"), (request_field, "问题原述" if request_field == "problem" else "清洁范围")) if not confirmed(key).strip()]
        if missing:
            raise AppError("missing_service_facts", "请补充或核对：" + "、".join(missing))
        permission_note = confirmed("access_permission")
        permission = data.get("entry_permission", {"status": "reported_permission", "note": permission_note} if permission_note else {"status": "unknown"})
        _strict(permission, {"status"}, {"note"})
        if not isinstance(permission["status"], str) or permission["status"] not in {"unknown", "reported_permission"}:
            raise AppError("invalid_input", "进入许可仅区分待核或原述许可，不能当正式核准。")
        if "note" in permission:
            _optional(permission["note"], "许可原述", 2000)
        permission = {"status": permission["status"], **({"note": string(permission.get("note"), "许可原述", 2000)} if permission["status"] == "reported_permission" else {})}
        prior = prior or {}
        kind = confirmed("request_kind") if matter["domain"] == "cleaning" else "unknown"
        projection = {"domain": matter["domain"], "location": confirmed("location"), "request_text": confirmed(request_field),
                      "access_window": confirmed("access_window"), "entry_permission": permission,
                      "contact": _optional(data.get("contact", prior.get("contact", "")), "本次允许服务人员使用的联系", 500),
                      "request_kind": kind if kind in {"temporary", "routine_ref"} else "unknown",
                      "notes": _optional(data.get("notes", prior.get("notes", "")), "显式服务备注")}
        name = _optional(data.get("requester_display_name", prior.get("requester_display_name", "")), "提交称呼", 200)
        if name:
            projection["requester_display_name"] = name
        return projection

    def submit(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision", "clerk_id"}, {"contact", "notes", "entry_permission", "requester_display_name"})
        with self.store.transaction() as connection:
            actor = self._actor(connection)
            if not {"employee", "clerk"}.intersection(actor["capabilities"]):
                raise AppError("forbidden", "仅事项发起人或实际经办职责可交办本人服务。", 403)
            matter = self.store._matter(connection, matter_id)
            old = connection.execute("SELECT * FROM service_cases WHERE matter_id=?", (matter_id,)).fetchone()
            key, fingerprint, replay = self._request(connection, actor, "submit", matter_id, data, key)
            if replay:
                return self._view(connection, self._case(connection, old["id"], actor), actor)
            self.store._check_version(matter, data)
            self._selected(connection, data["clerk_id"], "clerk")
            projection = self._projection(connection, matter, data)
            if old and old["sharing_status"] != "withdrawn":
                raise AppError("service_exists", "已交办此事项；修改请明确更新需求，变更经办先撤共享。", 409)
            if old:
                old = dict(old)
                prior_projection = json.loads(old["projection_json"])
                before = self._tracks(old)
                case = self._update(connection, old, clerk_id=data["clerk_id"], executor_id=None, input_revision=matter["revision_no"],
                                    projection_json=dump(projection), sharing_status="shared", assignment_epoch=old["assignment_epoch"] + 1,
                                    assignment_status="unassigned", acceptance_status="pending", execution_status="not_reported", requester_result="unknown", closure_status="open")
            else:
                prior_projection = None
                case = {"id": uid(), "matter_id": matter_id, "requester_id": actor["id"], "clerk_id": data["clerk_id"], "executor_id": None,
                        "input_revision": matter["revision_no"], "projection_json": dump(projection), "sharing_status": "shared", "assignment_epoch": 1, "version": 1,
                        "assignment_status": "unassigned", "acceptance_status": "pending", "execution_status": "not_reported", "requester_result": "unknown",
                        "inspection_status": "unknown", "closure_status": "open", "updated_at": now()}
                before = {}
                connection.execute("INSERT INTO service_cases VALUES(" + ",".join("?" for _ in case) + ")", tuple(case.values()))
            self._event(connection, case, actor, "submit", before, "发起人明确交办固定服务投影，不共享私人原文或全部成果。",
                        projection_before=prior_projection, projection_after=projection)
            return self._remember(connection, key, fingerprint, "submit", case, actor)

    def assign(self, service_id, data, key):
        _strict(data, {"expected_version", "executor_id"}, {"note"})
        with self.store.transaction() as connection:
            actor = self._actor(connection)
            case = self._case(connection, service_id, actor)
            if actor["id"] != case["clerk_id"] or case["sharing_status"] != "shared":
                raise AppError("forbidden", "仅选定经办可分派此服务。", 403)
            self._capability(actor, "clerk")
            key, fingerprint, replay = self._request(connection, actor, "assign", service_id, data, key)
            if replay:
                return self._view(connection, case, actor)
            self._version(case, data)
            if connection.execute("SELECT revision_no FROM matters WHERE id=?", (case["matter_id"],)).fetchone()[0] != case["input_revision"]:
                raise AppError("service_stale", "原事项已修订，发起人须明确更新服务需求后再安排。", 409)
            self._selected(connection, data["executor_id"], "executor")
            note = _optional(data.get("note", ""), "安排说明")
            if case["assignment_status"] != "unassigned" and not note:
                raise AppError("missing_reason", "改派或重新安排须注明实际原因。")
            before = self._tracks(case)
            case = self._update(connection, case, executor_id=data["executor_id"], assignment_epoch=case["assignment_epoch"] + 1,
                                assignment_status="assigned", acceptance_status="pending", execution_status="not_reported", requester_result="unknown", closure_status="open")
            self._event(connection, case, actor, "assign", before, note, executor_id=data["executor_id"])
            return self._remember(connection, key, fingerprint, "assign", case, actor)

    def respond(self, service_id, data, key):
        _strict(data, {"expected_version", "assignment_epoch", "command"}, {"note", "occurred_at"})
        with self.store.transaction() as connection:
            actor = self._actor(connection)
            case = self._case(connection, service_id, actor)
            if actor["id"] != case["executor_id"] or case["assignment_status"] != "assigned":
                raise AppError("forbidden", "仅当前执行者本人可承接或声明处理。", 403)
            self._capability(actor, "executor")
            key, fingerprint, replay = self._request(connection, actor, "respond", service_id, data, key)
            if replay:
                if data["assignment_epoch"] != case["assignment_epoch"]:
                    raise AppError("assignment_conflict", "旧分派已失效，不能回放为当前承接。", 409)
                return self._view(connection, case, actor)
            self._version(case, data, epoch=True)
            command = data["command"]
            if not isinstance(command, str) or command not in {"accept", "decline", "start", "blocked", "claimed_done"}:
                raise AppError("invalid_input", "不支持此执行者回应。")
            note = _optional(data.get("note", ""), "本人说明", 6000)
            occurred = data.get("occurred_at")
            if occurred is not None:
                occurred = string(occurred, "发生时间", 100)
                try:
                    if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:$|T| )", occurred):
                        raise ValueError()
                    datetime.fromisoformat(occurred)
                except ValueError:
                    raise AppError("date_unconfirmed", "发生时间须为明确日期或ISO日期时间。") from None
            if command in {"accept", "decline"} and case["acceptance_status"] != "pending":
                raise AppError("invalid_transition", "此分派已有本人承接结果；重新安排须由经办建立新分派。", 409)
            if command in {"start", "blocked", "claimed_done"} and case["acceptance_status"] != "accepted":
                raise AppError("acceptance_required", "请先由本人接受当前分派。", 409)
            if command in {"blocked", "claimed_done"} and not note:
                raise AppError("missing_statement", "障碍或已处理声明须附真实说明。")
            if command == "start" and case["execution_status"] == "claimed_done":
                raise AppError("invalid_transition", "已保存处理声明；再次安排须由经办建立新分派。", 409)
            before = self._tracks(case)
            changes = {"acceptance_status": "accepted" if command == "accept" else "declined"} if command in {"accept", "decline"} else {"execution_status": {"start": "in_progress", "blocked": "blocked", "claimed_done": "claimed_done"}[command]}
            if command == "claimed_done":
                changes["closure_status"] = "waiting_rule"
            case = self._update(connection, case, **changes)
            extra = {"occurred_at": occurred} if occurred else {}
            if command in {"start", "blocked", "claimed_done"}:
                extra["evidence_class"] = "executor_statement"
            self._event(connection, case, actor, command, before, note, **extra)
            return self._remember(connection, key, fingerprint, "respond", case, actor)

    def result(self, service_id, data, key):
        _strict(data, {"expected_version", "assignment_epoch", "result"}, {"note"})
        with self.store.transaction() as connection:
            actor = self._actor(connection)
            case = self._case(connection, service_id, actor)
            if actor["id"] != case["requester_id"]:
                raise AppError("forbidden", "仅原发起人本人可反馈结果。", 403)
            key, fingerprint, replay = self._request(connection, actor, "result", service_id, data, key)
            if replay:
                if data["assignment_epoch"] != case["assignment_epoch"]:
                    raise AppError("assignment_conflict", "原结果属于旧分派，不能回放为新结果。", 409)
                return self._view(connection, case, actor)
            self._version(case, data, epoch=True)
            if not isinstance(data["result"], str) or data["result"] not in {"unverified", "resolved", "still_problem"}:
                raise AppError("invalid_input", "仅区分本人未验证、已解决或仍有问题。")
            before = self._tracks(case)
            case = self._update(connection, case, requester_result=data["result"], **({"closure_status": "waiting_rule"} if data["result"] == "resolved" else {}))
            self._event(connection, case, actor, "result", before, _optional(data.get("note", ""), "本人结果说明", 6000), evidence_class="requester_statement")
            return self._remember(connection, key, fingerprint, "result", case, actor)

    def revise(self, service_id, data, key):
        _strict(data, {"expected_version", "input_revision", "note"})
        with self.store.transaction() as connection:
            actor = self._actor(connection)
            case = self._case(connection, service_id, actor)
            if actor["id"] != case["requester_id"] or case["sharing_status"] != "shared":
                raise AppError("forbidden", "仅仍共享的原发起人可明确更新服务需求。", 403)
            key, fingerprint, replay = self._request(connection, actor, "revise", service_id, data, key)
            if replay:
                return self._view(connection, case, actor)
            self._version(case, data)
            matter = self.store._matter(connection, case["matter_id"])
            prior_projection = json.loads(case["projection_json"])
            projection = self._projection(connection, matter, data, prior_projection)
            note = string(data["note"], "需求修订说明", 2000)
            before = self._tracks(case)
            case = self._update(connection, case, projection_json=dump(projection), input_revision=matter["revision_no"], executor_id=None,
                                assignment_epoch=case["assignment_epoch"] + 1, assignment_status="unassigned", acceptance_status="pending",
                                execution_status="not_reported", requester_result="unknown", closure_status="open")
            self._event(connection, case, actor, "revise", before, note, projection_before=prior_projection, projection_after=projection)
            return self._remember(connection, key, fingerprint, "revise", case, actor)

    def withdraw(self, service_id, data, key):
        _strict(data, {"expected_version", "note"})
        with self.store.transaction() as connection:
            actor = self._actor(connection)
            case = self._case(connection, service_id, actor)
            if actor["id"] not in {case["requester_id"], case["clerk_id"]}:
                raise AppError("forbidden", "仅发起人可撤共享，选定经办可撤派。", 403)
            key, fingerprint, replay = self._request(connection, actor, "withdraw", service_id, data, key)
            if replay:
                return self._view(connection, case, actor)
            self._version(case, data)
            note = string(data["note"], "撤回原因", 2000)
            before = self._tracks(case)
            is_requester = actor["id"] == case["requester_id"]
            case = self._update(connection, case, executor_id=None, assignment_epoch=case["assignment_epoch"] + 1,
                                assignment_status="withdrawn", **({"sharing_status": "withdrawn"} if is_requester else {}))
            self._event(connection, case, actor, "withdraw_sharing" if is_requester else "withdraw_assignment", before, note)
            return self._remember(connection, key, fingerprint, "withdraw", case, actor)

    def statement(self, service_id, data, key):
        _strict(data, {"expected_version", "reported_by", "note"}, {"evidence_refs"})
        with self.store.transaction() as connection:
            actor = self._actor(connection)
            case = self._case(connection, service_id, actor)
            if actor["id"] not in {case["requester_id"], case["clerk_id"]}:
                raise AppError("forbidden", "执行者本人反馈请用当前分派回应，不能代录他人声明。", 403)
            key, fingerprint, replay = self._request(connection, actor, "statement", service_id, data, key)
            if replay:
                return self._view(connection, case, actor)
            self._version(case, data)
            refs = data.get("evidence_refs", [])
            if not isinstance(refs, list) or len(refs) > 10:
                raise AppError("invalid_input", "凭据候选引用最多10项，不会自动读取原件。")
            refs = [string(ref, "凭据候选引用", 200) for ref in refs]
            before = self._tracks(case)
            case = self._update(connection, case)
            self._event(connection, case, actor, "statement", before, string(data["note"], "转录原话", 6000),
                        reported_by=string(data["reported_by"], "实际陈述人", 200), evidence_refs=refs, evidence_class="unverified_transcription")
            return self._remember(connection, key, fingerprint, "statement", case, actor)

    def close(self, service_id, data, key):
        with self.store.connect() as connection:
            actor = self._actor(connection)
            self._case(connection, service_id, actor)
            raise AppError("contract_unconfirmed", "正式关闭与必要验收口径尚未核定；处理声明和本人结果保留，不能自动闭单。", 409)
