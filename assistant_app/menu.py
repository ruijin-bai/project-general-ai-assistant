"""Explicit local menu publication and private voluntary feedback projections."""

import hashlib
import json
import re
from datetime import date

from .preparation import initial_facts
from .store import AppError, dump, now, string, uid

MENU_SCHEMA = """
CREATE TABLE menu_publications(
 id TEXT PRIMARY KEY, source_matter_id TEXT NOT NULL REFERENCES matters(id),
 source_revision INTEGER NOT NULL, publisher_id TEXT NOT NULL REFERENCES accounts(id),
 menu_date TEXT NOT NULL, meal_slot TEXT NOT NULL, dishes TEXT NOT NULL, notes TEXT NOT NULL,
 version INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('published','withdrawn')),
 published_at TEXT NOT NULL, withdrawn_at TEXT);
CREATE INDEX menu_publications_publisher ON menu_publications(publisher_id,status);
CREATE TABLE menu_feedback(
 matter_id TEXT PRIMARY KEY REFERENCES matters(id), publication_id TEXT NOT NULL REFERENCES menu_publications(id),
 publication_version INTEGER NOT NULL, actor_id TEXT NOT NULL REFERENCES accounts(id),
 feedback_text TEXT NOT NULL, dish TEXT NOT NULL, dietary_constraint TEXT NOT NULL,
 submitted_at TEXT NOT NULL, shared INTEGER NOT NULL DEFAULT 1 CHECK(shared IN (0,1)));
CREATE INDEX menu_feedback_publication ON menu_feedback(publication_id,shared);
"""
PUBLIC_SCOPE = "本地工作区菜单发布快照；不代表微信群发布、实际备餐或实供"
FEEDBACK_SCOPE = "本人自愿提交给本菜单厨师的反馈；仅当前菜单版本，不改变菜单或库存"
PRIVATE_FEEDBACK_SCOPE = "本人菜单意见私有记录；已撤回厨师共享，编辑不会重新公开，原菜单版本与历史保留"
RESPONSE_SCOPE = "本菜单厨师对明确意见版本的文字声明；不代表采纳、菜单变更、实际供餐或效果"
ADOPTION_SCOPE = "厨师本人声明在明确发布的新版菜单中采用所选意见；仅菜单选择声明，不证明实际备餐、供餐或厨房安全"


def _strict(data, required, optional=()):
    if not isinstance(data, dict) or not required.issubset(data) or set(data) - required - set(optional):
        raise AppError("invalid_input", "仅允许本接口列明字段，不能指定其他操作者或权限。")


def _optional_text(value, label, maximum=2000):
    if not isinstance(value, str) or len(value) > maximum:
        raise AppError("invalid_input", label + "须为文字且不超出长度限制。")
    return value.strip()


class MenuService:
    def __init__(self, store):
        self.store = store

    def _actor(self, connection, capability=None):
        principal = self.store.principal()
        if not isinstance(principal, dict) or not isinstance(principal.get("id"), str):
            raise AppError("authentication_required", "请先登录自己的账号。", 401)
        row = connection.execute("SELECT * FROM accounts WHERE id=?", (principal["id"],)).fetchone()
        if not row or row["state"] != "active" or row["auth_epoch"] != principal.get("auth_epoch"):
            raise AppError("authentication_required", "登录或职责已失效，请重新登录。", 401)
        actor = dict(row)
        actor["capabilities"] = json.loads(actor["capabilities"])
        if capability and capability not in actor["capabilities"]:
            raise AppError("forbidden", "当前账号没有此业务职责。", 403)
        return actor

    def _publication(self, connection, publication_id):
        row = connection.execute("SELECT p.*,a.display_name publisher_display_name,a.state publisher_state,a.capabilities publisher_capabilities FROM menu_publications p JOIN accounts a ON a.id=p.publisher_id WHERE p.id=?", (publication_id,)).fetchone()
        if not row:
            raise AppError("not_found", "菜单不存在或当前无权查看。", 404)
        return self._revision_projection(dict(row), self._revision_links(connection))

    def _revision_links(self, connection):
        links = {}
        for row in connection.execute("SELECT message FROM events WHERE event_type='menu_revised_local' ORDER BY rowid"):
            record = json.loads(row["message"])
            links.setdefault(record["previous_publication_id"], {})["replacement_publication_id"] = record["publication_id"]
            links.setdefault(record["publication_id"], {})["previous_publication_id"] = record["previous_publication_id"]
        return links

    def _revision_projection(self, publication, links):
        return {**publication, **links.get(publication["id"], {})}

    def _available(self, publication):
        return publication["status"] == "published" and publication["publisher_state"] == "active" and "cook" in json.loads(publication["publisher_capabilities"])

    def _projection(self, publication):
        projection = {key: publication[key] for key in ("id", "publisher_id", "menu_date", "meal_slot", "dishes", "notes", "version", "status", "published_at", "withdrawn_at", "publisher_display_name")}
        projection["publisher_name"] = publication["publisher_display_name"]
        for field in ("previous_publication_id", "replacement_publication_id"):
            if field in publication:
                projection[field] = publication[field]
        return projection

    def _request(self, connection, actor, operation, object_id, data, key):
        key = string(key, "请求键", 200)
        fingerprint = hashlib.sha256(dump([actor["id"], "menu:" + operation, object_id, data]).encode("utf-8")).hexdigest()
        previous = connection.execute("SELECT fingerprint,response FROM actions WHERE idempotency_key=?", (key,)).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise AppError("idempotency_conflict", "此请求键已用于其他内容或身份。", 409)
            return key, fingerprint, json.loads(previous["response"])
        return key, fingerprint, None

    def _remember(self, connection, key, fingerprint, operation, matter_id, response):
        connection.execute("INSERT INTO actions(id,matter_id,kind,status,idempotency_key,fingerprint,response,created_at) VALUES(?,?,?,'completed',?,?,?,?)",
                           (uid(), matter_id, "request:menu_" + operation, key, fingerprint, dump(response), now()))
        return response

    def list_publications(self):
        with self.store.connect() as connection:
            actor = self._actor(connection)
            if not {"employee", "cook"}.intersection(actor["capabilities"]):
                raise AppError("forbidden", "菜单列表仅向员工或厨师职责提供。", 403)
            rows = connection.execute("SELECT p.*,a.display_name publisher_display_name,a.state publisher_state,a.capabilities publisher_capabilities FROM menu_publications p JOIN accounts a ON a.id=p.publisher_id ORDER BY p.published_at DESC,p.rowid DESC").fetchall()
            links = self._revision_links(connection)
            items = [self._projection(self._revision_projection(dict(row), links)) for row in rows if self._available(dict(row)) or ("cook" in actor["capabilities"] and row["publisher_id"] == actor["id"])]
            return {"items": items, "scope": PUBLIC_SCOPE}

    def publish(self, matter_id, data, key):
        _strict(data, {"expected_version", "menu_date", "meal_slot", "dishes", "notes"}, {"expected_revision"})
        with self.store.transaction() as connection:
            actor = self._actor(connection, "cook")
            matter = self.store._matter(connection, matter_id)
            if matter["domain"] != "menu":
                raise AppError("invalid_input", "只能发布本人菜单事项的显式公开快照。")
            key, fingerprint, previous = self._request(connection, actor, "publish", matter_id, data, key)
            if previous is not None:
                publication = self._publication(connection, previous["id"])
                return {**self._projection(publication), "scope": PUBLIC_SCOPE}
            self.store._check_version(matter, data)
            if "expected_revision" in data and (type(data["expected_revision"]) is not int or data["expected_revision"] != matter["revision_no"]):
                raise AppError("version_conflict", "菜单事实修订已变化，请重新核对。", 409)
            menu_date = string(data["menu_date"], "菜单日期", 10)
            try:
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", menu_date):
                    raise ValueError()
                date.fromisoformat(menu_date)
            except ValueError:
                raise AppError("date_unconfirmed", "菜单日期须为明确ISO年月日，不能推断相对日期。") from None
            publication_id = uid()
            values = (publication_id, matter_id, matter["revision_no"], actor["id"], menu_date,
                      string(data["meal_slot"], "餐别", 100), string(data["dishes"], "菜品", 6000),
                      _optional_text(data["notes"], "公开备注"), now())
            connection.execute("INSERT INTO menu_publications(id,source_matter_id,source_revision,publisher_id,menu_date,meal_slot,dishes,notes,version,status,published_at) VALUES(?,?,?,?,?,?,?,?,1,'published',?)", values)
            self.store._event(connection, matter_id, "menu_published_local", "本人厨师账号显式发布了本地菜单快照；不代表实际备餐、实供或外部发送。")
            self.store._bump(connection, matter_id)
            response = {**self._projection(self._publication(connection, publication_id)), "scope": PUBLIC_SCOPE}
            return self._remember(connection, key, fingerprint, "publish", matter_id, response)

    def withdraw(self, publication_id, data, key):
        _strict(data, {"expected_version"})
        with self.store.transaction() as connection:
            actor = self._actor(connection, "cook")
            publication = self._publication(connection, publication_id)
            if publication["publisher_id"] != actor["id"]:
                raise AppError("not_found", "菜单不存在或当前无权操作。", 404)
            key, fingerprint, previous = self._request(connection, actor, "withdraw", publication_id, data, key)
            if previous is not None:
                return {**self._projection(publication), "scope": PUBLIC_SCOPE}
            if type(data["expected_version"]) is not int or data["expected_version"] != publication["version"]:
                raise AppError("version_conflict", "菜单版本已变化，请重新读取。", 409)
            if publication["status"] != "published":
                raise AppError("menu_withdrawn", "菜单已撤回。", 409)
            connection.execute("UPDATE menu_publications SET status='withdrawn',version=version+1,withdrawn_at=? WHERE id=?", (now(), publication_id))
            self.store._event(connection, publication["source_matter_id"], "menu_withdrawn_local", "原厨师账号撤回本地发布；历史与旧版反馈保留，不声称召回已下载副本。")
            response = {**self._projection(self._publication(connection, publication_id)), "scope": PUBLIC_SCOPE}
            return self._remember(connection, key, fingerprint, "withdraw", publication["source_matter_id"], response)

    def revise(self, publication_id, data, key):
        """Replace an explicit public snapshot, without moving old opinions."""
        _strict(data, {"expected_version", "menu_date", "meal_slot", "dishes", "notes"}, {"adoptions"})
        menu_date = string(data["menu_date"], "菜单日期", 10)
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", menu_date):
                raise ValueError()
            date.fromisoformat(menu_date)
        except ValueError:
            raise AppError("date_unconfirmed", "菜单日期须为明确ISO年月日。") from None
        meal_slot = string(data["meal_slot"], "餐别", 100)
        dishes = string(data["dishes"], "菜品", 6000)
        notes = _optional_text(data["notes"], "公开备注")
        with self.store.transaction() as connection:
            actor = self._actor(connection, "cook")
            publication = self._publication(connection, publication_id)
            if publication["publisher_id"] != actor["id"]:
                raise AppError("not_found", "菜单不存在或当前无权修订。", 404)
            key, fingerprint, previous = self._request(connection, actor, "revise", publication_id, data, key)
            if previous is not None:
                return {**self._projection(self._publication(connection, previous["id"])), "scope": PUBLIC_SCOPE}
            if type(data["expected_version"]) is not int or data["expected_version"] != publication["version"]:
                raise AppError("version_conflict", "菜单版本已变化，请重新核对；编辑内容保留。", 409)
            if not self._available(publication):
                raise AppError("menu_withdrawn", "旧菜单已撤回或被修订替代，请打开当前发布版本。", 409)
            adoptions = self._check_adoptions(connection, publication, data.get('adoptions', []))
            replacement_id, stamp = uid(), now()
            # The old dishes/date/version remain immutable for fixed-version feedback.
            connection.execute("UPDATE menu_publications SET status='withdrawn',withdrawn_at=? WHERE id=?", (stamp, publication_id))
            connection.execute("INSERT INTO menu_publications(id,source_matter_id,source_revision,publisher_id,menu_date,meal_slot,dishes,notes,version,status,published_at) VALUES(?,?,?,?,?,?,?,?,?,'published',?)",
                               (replacement_id, publication["source_matter_id"], publication["source_revision"], actor["id"], menu_date, meal_slot, dishes, notes, publication["version"] + 1, stamp))
            self.store._event(connection, publication["source_matter_id"], "menu_revised_local", dump({"previous_publication_id": publication_id, "publication_id": replacement_id,
                                   "previous_version": publication["version"], "version": publication["version"] + 1, "action": "原厨师本人修订公开快照；旧菜单与反馈原关联保留，采用声明另记，实际供餐待核"}))
            for item in adoptions:
                record = {**item, 'publication_id': publication_id, 'publication_version': publication['version'],
                          'target_publication_id': replacement_id, 'target_publication_version': publication['version'] + 1,
                          'actor_id': actor['id'], 'actor_display_name': actor['display_name'], 'evidence_class': 'cook_statement'}
                self.store._event(connection, item['feedback_id'], 'feedback_cook_adoption', dump(record))
                self.store._bump(connection, item['feedback_id'])
            if adoptions:
                self.store._event(connection, publication['source_matter_id'], 'menu_adoption_recorded', '本人明确在本地新版菜单登记'+str(len(adoptions))+'条采用声明；各员工原意见分别保留私有声明，不公开身份或约束，不代表实际供餐。')
            response = {**self._projection(self._publication(connection, replacement_id)), "scope": PUBLIC_SCOPE}
            return self._remember(connection, key, fingerprint, "revise", publication["source_matter_id"], response)

    def _check_adoptions(self, connection, publication, values):
        if not isinstance(values, list) or len(values) > 8:
            raise AppError('invalid_input', '一次菜单修订最多明确关联8条意见，不自动选择。')
        checked, seen = [], set()
        for value in values:
            _strict(value, {'feedback_id', 'feedback_correction_count', 'note'})
            feedback_id = string(value['feedback_id'], '意见引用', 200)
            if feedback_id in seen:
                raise AppError('invalid_input', '同一意见不能重复声明采用。')
            seen.add(feedback_id)
            note = string(value['note'], '本人采用说明', 3000)
            row = connection.execute('SELECT f.* FROM menu_feedback f JOIN matters m ON m.id=f.matter_id WHERE f.matter_id=? AND f.publication_id=? AND f.shared=1 AND m.owner=f.actor_id', (feedback_id, publication['id'])).fetchone()
            if not row:
                raise AppError('not_found', '所选意见不存在、属于其他菜单或已撤共享；原修订文字保留。', 404)
            corrections = connection.execute("SELECT COUNT(*) FROM events WHERE matter_id=? AND event_type='feedback_corrected_local'", (feedback_id,)).fetchone()[0]
            if type(value['feedback_correction_count']) is not int or value['feedback_correction_count'] != corrections or row['publication_version'] != publication['version']:
                raise AppError('version_conflict', '所选意见已更正，先读当前原话再核对采用声明；不直接采用旧意见。', 409)
            checked.append({'feedback_id': feedback_id, 'feedback_correction_count': corrections, 'note': note})
        return checked

    def feedback(self, publication_id, data, key):
        _strict(data, {"version", "feedback_text"}, {"dish", "dietary_constraint"})
        with self.store.transaction() as connection:
            actor = self._actor(connection, "employee")
            publication = self._publication(connection, publication_id)
            if not self._available(publication):
                raise AppError("not_found", "菜单已撤回、发布职责失效或当前不可见。", 404)
            key, fingerprint, previous = self._request(connection, actor, "feedback", publication_id, data, key)
            if previous is not None:
                record, matter = self._own_feedback(connection, previous["id"], actor)
                if not record["shared"] or record["feedback_text"] != previous["feedback_text"] or record["dish"] != previous["dish"] or record["dietary_constraint"] != previous["dietary_constraint"]:
                    return self._own_feedback_view(connection, record, matter)
                return previous
            if type(data["version"]) is not int or data["version"] != publication["version"]:
                raise AppError("version_conflict", "请针对实际查看的菜单版本反馈。", 409)
            text = string(data["feedback_text"], "自愿意见", 6000)
            dish = _optional_text(data.get("dish", ""), "对应菜品", 500)
            constraint = _optional_text(data.get("dietary_constraint", ""), "明确饮食约束", 2000)
            matter_id, stamp = uid(), now()
            connection.execute("INSERT INTO matters(id,owner,goal_text,domain,version,revision_no,control_epoch,assistant_status,preparation_status,updated_at) VALUES(?,?,?,'feedback',1,1,0,'idle','draft',?)", (matter_id, actor["id"], text, stamp))
            facts = initial_facts("feedback")
            for field, value in {"feedback_text": text, "menu_ref": publication_id + "/v" + str(publication["version"]), "menu_date": publication["menu_date"], "meal_slot": publication["meal_slot"], "dish": dish, "reported_by": actor["display_name"], "dietary_constraint": constraint}.items():
                facts[field] = {"value": value or None, "status": "confirmed" if value else "unknown"}
            facts["__workflow"] = {"enabled": False, "mode": "template"}
            connection.execute("INSERT INTO revisions(matter_id,revision_no,fields,recorded_at,confirmed_by) VALUES(?,1,?,?,?)", (matter_id, dump(facts), stamp, actor["id"]))
            self.store._source(connection, matter_id, "user_text", text, actor["display_name"])
            self.store._event(connection, matter_id, "feedback_submitted_local", "本人账号自愿向本菜单厨师提交固定反馈投影；不要求每日评价，不等待批准或厨师审核。")
            connection.execute("INSERT INTO menu_feedback VALUES(?,?,?,?,?,?,?,?,1)", (matter_id, publication_id, publication["version"], actor["id"], text, dish, constraint, stamp))
            response = {"id": matter_id, "publication_id": publication_id, "publication_version": publication["version"], "feedback_text": text,
                        "dish": dish, "dietary_constraint": constraint, "submitted_at": stamp, "shared_with_publisher": True, "scope": FEEDBACK_SCOPE}
            return self._remember(connection, key, fingerprint, "feedback", matter_id, response)

    def list_feedback(self, publication_id):
        with self.store.connect() as connection:
            actor = self._actor(connection, "cook")
            publication = self._publication(connection, publication_id)
            if publication["publisher_id"] != actor["id"]:
                raise AppError("not_found", "菜单不存在或当前无权查看反馈。", 404)
            rows = connection.execute("SELECT f.*,a.display_name,(SELECT COUNT(*) FROM events e WHERE e.matter_id=f.matter_id AND event_type='feedback_corrected_local') correction_count,(SELECT MAX(recorded_at) FROM events e WHERE e.matter_id=f.matter_id AND event_type='feedback_corrected_local') corrected_at FROM menu_feedback f JOIN accounts a ON a.id=f.actor_id WHERE publication_id=? AND shared=1 ORDER BY submitted_at,matter_id", (publication_id,)).fetchall()
            items = [{"id": row["matter_id"], "contributor_id": row["actor_id"], "display_name": row["display_name"], "submitted_by": row["display_name"],
                      "publication_version": row["publication_version"], "version": row["publication_version"], "feedback_text": row["feedback_text"], "dish": row["dish"],
                      "dietary_constraint": row["dietary_constraint"], "submitted_at": row["submitted_at"], "corrected_by_self": row["correction_count"] > 0,
                      "correction_count": row["correction_count"], "corrected_at": row["corrected_at"]} for row in rows]
            for item in items:
                item["responses"] = self._feedback_responses(connection, item["id"], publication_id, item["publication_version"], item["correction_count"])
                item['adoptions'] = self._feedback_adoptions(connection, item['id'], publication_id, item['publication_version'], item['correction_count'])
            return {"items": items, "feedback_count": len(items), "contributor_count": len({item["contributor_id"] for item in items}), "scope": FEEDBACK_SCOPE}

    def _feedback_responses(self, connection, matter_id, publication_id, publication_version, correction_count):
        responses = []
        for row in connection.execute("SELECT id,message,recorded_at FROM events WHERE matter_id=? AND event_type='feedback_cook_response' ORDER BY rowid", (matter_id,)):
            record = json.loads(row["message"])
            if record["publication_id"] != publication_id or record["publication_version"] != publication_version:
                continue
            responses.append({"id": row["id"], "publication_id": record["publication_id"], "publication_version": record["publication_version"],
                              "feedback_correction_count": record["feedback_correction_count"], "response_text": record["response_text"],
                              "cook_display_name": record["actor_display_name"], "recorded_at": row["recorded_at"],
                              "old_feedback_scope": record["feedback_correction_count"] != correction_count, "scope": RESPONSE_SCOPE})
        return responses

    def _feedback_adoptions(self, connection, matter_id, publication_id, publication_version, correction_count):
        records = []
        for row in connection.execute("SELECT id,message,recorded_at FROM events WHERE matter_id=? AND event_type='feedback_cook_adoption' ORDER BY rowid", (matter_id,)):
            record = json.loads(row['message'])
            if record['publication_id'] != publication_id or record['publication_version'] != publication_version:
                continue
            records.append({'id': row['id'], 'publication_id': publication_id, 'publication_version': publication_version,
                            'feedback_correction_count': record['feedback_correction_count'], 'note': record['note'],
                            'cook_display_name': record['actor_display_name'], 'recorded_at': row['recorded_at'],
                            'old_feedback_scope': record['feedback_correction_count'] != correction_count,
                            'target_menu_snapshot': self._projection(self._publication(connection, record['target_publication_id'])), 'scope': ADOPTION_SCOPE})
        return records

    def respond_feedback(self, publication_id, feedback_id, data, key):
        _strict(data, {"expected_publication_version", "feedback_correction_count", "response_text"})
        text = string(data["response_text"], "厨师本人回应", 3000)
        with self.store.transaction() as connection:
            actor = self._actor(connection, "cook")
            publication = self._publication(connection, publication_id)
            if publication["publisher_id"] != actor["id"] or not self._available(publication):
                raise AppError("not_found", "本人菜单当前不可回应。", 404)
            feedback = connection.execute("SELECT f.matter_id,f.publication_version,f.actor_id FROM menu_feedback f JOIN matters m ON m.id=f.matter_id WHERE f.matter_id=? AND f.publication_id=? AND f.shared=1 AND m.owner=f.actor_id", (feedback_id, publication_id)).fetchone()
            if not feedback:
                raise AppError("not_found", "本菜单意见不存在或共享已撤回。", 404)
            correction_count = connection.execute("SELECT COUNT(*) FROM events WHERE matter_id=? AND event_type='feedback_corrected_local'", (feedback_id,)).fetchone()[0]
            if type(data["expected_publication_version"]) is not int or data["expected_publication_version"] != publication["version"] or feedback["publication_version"] != publication["version"] or type(data["feedback_correction_count"]) is not int or data["feedback_correction_count"] != correction_count:
                raise AppError("version_conflict", "菜单或意见已修订，请针对当前明确版本重新回应。", 409)
            key, fingerprint, previous = self._request(connection, actor, "feedback_respond:" + publication_id, feedback_id, data, key)
            if previous is not None:
                return previous
            record = {"publication_id": publication_id, "publication_version": publication["version"], "feedback_correction_count": correction_count,
                      "response_text": text, "actor_id": actor["id"], "actor_display_name": actor["display_name"], "evidence_class": "cook_statement"}
            self.store._event(connection, feedback_id, "feedback_cook_response", dump(record))
            self.store._bump(connection, feedback_id)
            response = self._feedback_responses(connection, feedback_id, publication_id, feedback["publication_version"], correction_count)[-1]
            return self._remember(connection, key, fingerprint, "feedback_respond", feedback_id, response)

    def _own_feedback(self, connection, matter_id, actor):
        matter = self.store._matter(connection, matter_id)
        row = connection.execute("SELECT * FROM menu_feedback WHERE matter_id=? AND actor_id=?", (matter_id, actor["id"])).fetchone()
        if not row or matter["domain"] != "feedback":
            raise AppError("not_found", "本人已提交菜单意见不存在。", 404)
        return dict(row), matter

    def _own_feedback_view(self, connection, record, matter):
        corrections = connection.execute("SELECT COUNT(*),MAX(recorded_at) FROM events WHERE matter_id=? AND event_type='feedback_corrected_local'", (matter["id"],)).fetchone()
        shared = bool(record["shared"])
        return {"id": matter["id"], "expected_version": matter["version"], "version": matter["version"],
                "publication_id": record["publication_id"], "publication_version": record["publication_version"],
                "menu_snapshot": self._projection(self._publication(connection, record["publication_id"])),
                "feedback_text": record["feedback_text"], "dish": record["dish"], "dietary_constraint": record["dietary_constraint"],
                "submitted_at": record["submitted_at"], "shared": shared, "shared_with_publisher": shared,
                "corrected_by_self": corrections[0] > 0, "correction_count": corrections[0], "corrected_at": corrections[1],
                "responses": self._feedback_responses(connection, matter["id"], record["publication_id"], record["publication_version"], corrections[0]),
                'adoptions': self._feedback_adoptions(connection, matter['id'], record['publication_id'], record['publication_version'], corrections[0]),
                "scope": FEEDBACK_SCOPE if shared else PRIVATE_FEEDBACK_SCOPE}

    def get_own_feedback(self, matter_id):
        with self.store.connect() as connection:
            actor = self._actor(connection, "employee")
            record, matter = self._own_feedback(connection, matter_id, actor)
            return self._own_feedback_view(connection, record, matter)

    def update_feedback(self, matter_id, data, key):
        _strict(data, {"expected_version", "feedback_text"}, {"dish", "dietary_constraint"})
        with self.store.transaction() as connection:
            actor = self._actor(connection, "employee")
            record, matter = self._own_feedback(connection, matter_id, actor)
            key, fingerprint, previous = self._request(connection, actor, "feedback_revise", matter_id, data, key)
            if previous is not None:
                return self._own_feedback_view(connection, record, matter)
            self.store._check_version(matter, data)
            text = string(data["feedback_text"], "本人更正意见", 6000)
            dish = _optional_text(data.get("dish", record["dish"]), "对应菜品", 500)
            constraint = _optional_text(data.get("dietary_constraint", record["dietary_constraint"]), "明确饮食约束", 2000)
            facts = self.store._stored_facts(connection, matter)
            for field, value in {"feedback_text": text, "dish": dish, "dietary_constraint": constraint}.items():
                facts[field] = {"value": value or None, "status": "confirmed" if value else "unknown"}
            # Menu association is immutable; neither generic private fact edits nor
            # caller data can move an already submitted opinion to another menu.
            facts["menu_ref"] = {"value": record["publication_id"] + "/v" + str(record["publication_version"]), "status": "confirmed"}
            publication = self._publication(connection, record["publication_id"])
            for field, value in {"menu_date": publication["menu_date"], "meal_slot": publication["meal_slot"], "reported_by": actor["display_name"]}.items():
                facts[field] = {"value": value, "status": "confirmed"}
            revision, stamp = matter["revision_no"] + 1, now()
            connection.execute("INSERT INTO revisions(matter_id,revision_no,fields,recorded_at,confirmed_by) VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), stamp, actor["id"]))
            self.store._source(connection, matter_id, "user_text", text, actor["display_name"])
            self.store._cancel(connection, matter_id, "本人菜单意见已修正，停止旧准备动作；不会重新开放共享")
            status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
            self.store._bump(connection, matter_id, revision_no=revision, preparation_status="needs_review", assistant_status=status)
            connection.execute("UPDATE menu_feedback SET feedback_text=?,dish=?,dietary_constraint=? WHERE matter_id=?", (text, dish, constraint, matter_id))
            event = {"actor_id": actor["id"], "publication_id": record["publication_id"], "publication_version": record["publication_version"],
                     "input_revision": revision, "shared": bool(record["shared"]), "action": "本人更正；原文与来源历史保留，撤共享后只作私有保存"}
            self.store._event(connection, matter_id, "feedback_corrected_local", dump(event))
            record, matter = self._own_feedback(connection, matter_id, actor)
            response = self._own_feedback_view(connection, record, matter)
            return self._remember(connection, key, fingerprint, "feedback_revise", matter_id, response)

    def withdraw_feedback(self, matter_id, data, key):
        _strict(data, {"expected_version"})
        with self.store.transaction() as connection:
            actor = self._actor(connection, "employee")
            record, matter = self._own_feedback(connection, matter_id, actor)
            key, fingerprint, previous = self._request(connection, actor, "feedback_withdraw", matter_id, data, key)
            if previous is not None:
                return self._own_feedback_view(connection, record, matter)
            self.store._check_version(matter, data)
            if record["shared"]:
                connection.execute("UPDATE menu_feedback SET shared=0 WHERE matter_id=?", (matter_id,))
                self.store._bump(connection, matter_id)
                self.store._event(connection, matter_id, "feedback_shared_withdrawn", "本人撤回向原菜单厨师的反馈投影共享；私有原文、修订与来源保留，编辑不会重新公开。")
            record, matter = self._own_feedback(connection, matter_id, actor)
            return self._remember(connection, key, fingerprint, "feedback_withdraw", matter_id, self._own_feedback_view(connection, record, matter))
