"""Revocable account-bound grants for explicit immutable preparation snapshots."""

import json
import sqlite3

from .auth import CAPABILITIES
from .store import AppError, dump, now, string, uid
from .structured import StructuredService, _strict

SHARING_SCHEMA = """
CREATE TABLE matter_grants(
 id TEXT PRIMARY KEY,
 matter_id TEXT NOT NULL REFERENCES matters(id),
 owner_id TEXT NOT NULL REFERENCES accounts(id),
 recipient_id TEXT NOT NULL REFERENCES accounts(id),
 artifact_id TEXT NOT NULL, artifact_version INTEGER NOT NULL CHECK(artifact_version>0),
 scope TEXT NOT NULL CHECK(scope='read_preparation'),
 snapshot_title TEXT NOT NULL, snapshot_content TEXT NOT NULL,
 input_revision INTEGER NOT NULL, dependency_refs TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 status TEXT NOT NULL CHECK(status IN ('active','withdrawn')),
 owner_epoch INTEGER NOT NULL CHECK(owner_epoch>0),
 recipient_epoch INTEGER NOT NULL CHECK(recipient_epoch>0),
 created_at TEXT NOT NULL, revoked_at TEXT,
 FOREIGN KEY(artifact_id,artifact_version) REFERENCES artifacts(id,version));
CREATE UNIQUE INDEX grants_active_snapshot ON matter_grants(artifact_id,artifact_version,recipient_id,scope) WHERE status='active';
CREATE INDEX grants_owner_matter ON matter_grants(owner_id,matter_id);
CREATE INDEX grants_recipient ON matter_grants(recipient_id,status);
"""
SCOPE = "read_preparation"


class ShareService(StructuredService):
    def _meal_projection(self, connection, grant):
        from .meal_summaries import meal_record
        if grant['artifact_version'] != 1 or json.loads(grant['dependency_refs']):
            return None
        artifact = connection.execute("SELECT type FROM artifacts WHERE id=? AND version=1 AND matter_id=?", (grant['artifact_id'], grant['matter_id'])).fetchone()
        record = meal_record(connection, grant['matter_id'], grant['artifact_id']) if artifact and artifact['type'] == 'meal_summary' else None
        return record if record and grant['snapshot_content'] == record['cook_content'] and grant['snapshot_title'] == record['title'] else None

    def _actor(self, connection):
        if not self.store.requires_auth:
            raise AppError("not_found", "独立账号分享尚未启用。", 404)
        principal = self.store.principal()
        row = connection.execute("SELECT id,display_name,state,capabilities,auth_epoch FROM accounts WHERE id=?", (principal["id"],)).fetchone()
        if not row or row["state"] != "active" or row["auth_epoch"] != principal["auth_epoch"]:
            raise AppError("authentication_required", "登录或职责已失效，请重新登录。", 401)
        actor = dict(row)
        actor["capabilities"] = set(json.loads(actor["capabilities"]))
        if not actor["capabilities"] & CAPABILITIES:
            raise AppError("forbidden", "账号管理职责不提供私有业务或分享权限。", 403)
        return actor

    def _owned(self, connection, matter_id):
        actor = self._actor(connection)
        matter = self.store._matter(connection, matter_id)
        return actor, matter

    def _grant(self, connection, grant_id):
        row = connection.execute("SELECT * FROM matter_grants WHERE id=?", (string(grant_id, "分享ID", 80),)).fetchone()
        if not row:
            raise AppError("not_found", "分享不存在或已不可访问。", 404)
        return dict(row)

    def _recipient_accessible(self, connection, grant):
        if grant["status"] != "active":
            return False
        owner = connection.execute("SELECT state,auth_epoch,capabilities FROM accounts WHERE id=?", (grant["owner_id"],)).fetchone()
        recipient = connection.execute("SELECT state,auth_epoch,capabilities FROM accounts WHERE id=?", (grant["recipient_id"],)).fetchone()
        matter = connection.execute("SELECT owner FROM matters WHERE id=?", (grant["matter_id"],)).fetchone()
        return bool(owner and recipient and matter and matter["owner"] == grant["owner_id"] and owner["state"] == "active" and recipient["state"] == "active"
                    and owner["auth_epoch"] == grant["owner_epoch"] and recipient["auth_epoch"] == grant["recipient_epoch"]
                    and set(json.loads(owner["capabilities"])) & CAPABILITIES and ("clerk" in json.loads(recipient["capabilities"]) or "cook" in json.loads(recipient['capabilities']) and 'clerk' in json.loads(owner['capabilities']) and self._meal_projection(connection, grant)))

    def _authorized(self, connection, grant_id):
        actor = self._actor(connection)
        grant = self._grant(connection, grant_id)
        if actor["id"] == grant["owner_id"]:
            self.store._matter(connection, grant["matter_id"])
            return actor, grant, True
        if actor["id"] != grant["recipient_id"] or not self._recipient_accessible(connection, grant):
            raise AppError("not_found", "分享不存在或已不可访问。", 404)
        return actor, grant, False

    def _projection(self, connection, grant, owner=False):
        owner_row = connection.execute("SELECT display_name FROM accounts WHERE id=?", (grant["owner_id"],)).fetchone()
        matter = connection.execute("SELECT revision_no FROM matters WHERE id=?", (grant["matter_id"],)).fetchone()
        result = {"id": grant["id"], "version": grant["version"], "title": grant["snapshot_title"], "content": grant["snapshot_content"],
                  "owner_display_name": owner_row["display_name"], "created_at": grant["created_at"], "input_revision": grant["input_revision"],
                  "stale": matter["revision_no"] != grant["input_revision"], "scope": SCOPE}
        meal = self._meal_projection(connection, grant)
        if meal:
            from .meal_summaries import basis_current
            result['stale'] = result['stale'] or not basis_current(connection, grant['owner_id'], meal['basis'])
            result['projection_kind'] = 'meal_summary'
        from .expense_journey import report_references, reference_current
        journey=report_references(connection,grant['matter_id']).get(grant['artifact_id'])
        if journey:result['stale']=result['stale'] or not reference_current(connection,grant['owner_id'],journey)
        if owner:
            recipient = connection.execute("SELECT display_name FROM accounts WHERE id=?", (grant["recipient_id"],)).fetchone()
            result.update(matter_id=grant["matter_id"], artifact_id=grant["artifact_id"], artifact_version=grant["artifact_version"], recipient_id=grant["recipient_id"],
                          recipient_display_name=recipient["display_name"], dependency_refs=json.loads(grant["dependency_refs"]), status=grant["status"],
                          owner_epoch=grant["owner_epoch"], recipient_epoch=grant["recipient_epoch"], revoked_at=grant["revoked_at"],
                          recipient_accessible=self._recipient_accessible(connection, grant), snapshot_is_separate=True)
        return result

    def eligible_recipients(self):
        with self.store.connect() as connection:
            self._actor(connection)
            return [{"id": row["id"], "display_name": row["display_name"]} for row in connection.execute("SELECT id,display_name,capabilities FROM accounts WHERE state='active' ORDER BY display_name,id") if "clerk" in json.loads(row["capabilities"])]

    def create_share(self, matter_id, data, key):
        _strict(data, {"expected_version", "artifact_id", "artifact_version", "recipient_id", "title", "content", "dependency_refs"})
        title, content = string(data["title"], "预览分享标题", 200), string(data["content"], "明确预览的独立分享正文", 20000)
        artifact_id, recipient_id = string(data["artifact_id"], "成果ID", 80), string(data["recipient_id"], "接收账号ID", 80)
        if type(data["artifact_version"]) is not int or data["artifact_version"] < 1:
            raise AppError("invalid_version", "须明确选定已保存成果版本。")
        refs = data["dependency_refs"]
        if not isinstance(refs, list) or len(refs) > 40 or any(not isinstance(ref, str) or not ref or len(ref) > 80 for ref in refs) or len(set(refs)) != len(refs):
            raise AppError("invalid_refs", "依赖只可明确列出最多40个本事项来源ID，不得重复。")
        with self.store.transaction() as connection:
            actor, matter = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, "sharing_create", matter_id, data, key)
            if previous is not None:
                grant = self._grant(connection, previous["id"])
                return self._projection(connection, grant, True)
            self.store._check_version(matter, data)
            self._ensure_preparation_sources(connection, matter_id)
            artifact = connection.execute("SELECT input_revision FROM artifacts WHERE id=? AND version=? AND matter_id=?", (artifact_id, data["artifact_version"], matter_id)).fetchone()
            if not artifact:
                raise AppError("not_found", "本人选定成果版本不存在。", 404)
            from .expense_journey import report_references, reference_current
            journey=report_references(connection,matter_id).get(artifact_id)
            if journey and not reference_current(connection,actor['id'],journey):
                raise AppError('journey_basis_changed','费用稿引用的原行程已变化，重核另存新稿后才能新分享；私人旧稿及固定历史副本保留。',409)
            recipient = connection.execute("SELECT state,capabilities,auth_epoch FROM accounts WHERE id=?", (recipient_id,)).fetchone()
            if not recipient or recipient['state'] != 'active':
                raise AppError('invalid_recipient', '须明确选择活跃接收账号。')
            if 'clerk' not in json.loads(recipient['capabilities']):
                grant = {'matter_id': matter_id, 'artifact_id': artifact_id, 'artifact_version': data['artifact_version'], 'dependency_refs': dump(refs), 'snapshot_title': title, 'snapshot_content': content}
                meal = self._meal_projection(connection, grant) if 'cook' in json.loads(recipient['capabilities']) else None
                if not meal:
                    raise AppError('invalid_recipient', '厨师只能收到程序固定餐次汇总；普通私稿仍只分享给明确经办。')
                if 'clerk' not in actor['capabilities']:
                    raise AppError('forbidden', '当前经办职责已变化，不能新分享餐次汇总。', 403)
                from .meal_summaries import basis_current
                if not basis_current(connection, actor['id'], meal['basis']):
                    raise AppError('basis_changed', '餐次依据已变化，请重核另存新汇总；旧稿保留。', 409)
            for source_id in refs:
                if not connection.execute("SELECT id FROM sources WHERE id=? AND matter_id=?", (source_id, matter_id)).fetchone():
                    raise AppError("not_found", "本事项选定来源不存在。", 404)
            grant_id = uid()
            try:
                connection.execute("INSERT INTO matter_grants(id,matter_id,owner_id,recipient_id,artifact_id,artifact_version,scope,snapshot_title,snapshot_content,input_revision,dependency_refs,version,status,owner_epoch,recipient_epoch,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,'active',?,?,?)",
                                   (grant_id, matter_id, actor["id"], recipient_id, artifact_id, data["artifact_version"], SCOPE, title, content, artifact["input_revision"], dump(refs), actor["auth_epoch"], recipient["auth_epoch"], now()))
            except sqlite3.IntegrityError:
                raise AppError("share_conflict", "该固定成果版本已有有效分享，请明确撤回原分享后再提交新快照。", 409) from None
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, "preparation_shared", dump({"share_id": grant_id, "recipient_id": recipient_id, "actor_id": actor["id"], "actor_name": actor["display_name"], "scope": SCOPE, "snapshot_is_separate": True}))
            response = self._projection(connection, self._grant(connection, grant_id), True)
            return self._remember(connection, key, fingerprint, "sharing_create", matter_id, response)

    def list_received(self):
        with self.store.connect() as connection:
            actor = self._actor(connection)
            rows = connection.execute("SELECT * FROM matter_grants WHERE recipient_id=? AND status='active' ORDER BY created_at DESC,id", (actor["id"],)).fetchall()
            return {"items": [self._projection(connection, dict(row)) for row in rows if self._recipient_accessible(connection, row)], "scope": SCOPE}

    def list_owned(self, matter_id):
        with self.store.connect() as connection:
            actor, _ = self._owned(connection, matter_id)
            return {"items": [self._projection(connection, dict(row), True) for row in connection.execute("SELECT * FROM matter_grants WHERE matter_id=? AND owner_id=? ORDER BY created_at DESC,id", (matter_id, actor["id"]))], "scope": SCOPE}

    def get_share(self, grant_id):
        with self.store.connect() as connection:
            _, grant, owner = self._authorized(connection, grant_id)
            return self._projection(connection, grant, owner)

    def withdraw_share(self, grant_id, data, key):
        _strict(data, {"expected_version"})
        with self.store.transaction() as connection:
            actor, grant, owner = self._authorized(connection, grant_id)
            if not owner:
                raise AppError("not_found", "分享不存在或不可撤回。", 404)
            key, fingerprint, previous = self._request(connection, actor, "sharing_withdraw:" + grant_id, grant["matter_id"], data, key)
            if previous is not None:
                return self._projection(connection, self._grant(connection, grant_id), True)
            if type(data["expected_version"]) is not int or data["expected_version"] != grant["version"]:
                raise AppError("version_conflict", "分享权限版本已变化，请读回后再撤回。", 409)
            if grant["status"] == "active":
                connection.execute("UPDATE matter_grants SET status='withdrawn',version=version+1,revoked_at=? WHERE id=?", (now(), grant_id))
                matter = self.store._matter(connection, grant["matter_id"])
                self.store._cancel(connection, matter["id"], "分享已撤回；停止旧准备，原人工稿仍归本人")
                self.store._bump(connection, matter["id"], control_epoch=matter["control_epoch"] + 1)
                self.store._event(connection, matter["id"], "preparation_share_withdrawn", dump({"share_id": grant_id, "actor_id": actor["id"], "actor_name": actor["display_name"], "scope": SCOPE}))
            response = self._projection(connection, self._grant(connection, grant_id), True)
            return self._remember(connection, key, fingerprint, "sharing_withdraw", grant["matter_id"], response)

    def download_share(self, grant_id, version):
        with self.store.connect() as connection:
            _, grant, owner = self._authorized(connection, grant_id)
            if type(version) is not int or version < 1 or version > grant["version"] or (not owner and version != grant["version"]):
                raise AppError("not_found", "分享版本不存在或已不可访问。", 404)
            return grant["snapshot_content"]
