"""User-controlled private non-sensitive preferences; never auto-loaded by models."""

import hashlib
import json

from .store import AppError, OWNER, dump, now, string
from .structured import SAFE_ID, StructuredService, _strict

SCOPE = "仅本人明确输入的非敏感惯用写法或展示偏好；不代替身份、证照或批准，不自动用于模型或业务。删除不删除已有业务历史或曾显式复制的来源。"


class ContextService(StructuredService):
    def _actor(self, connection):
        if not self.store.requires_auth:
            if connection.execute("PRAGMA user_version").fetchone()[0] >= 3 or connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'").fetchone():
                raise AppError("authentication_required", "资料已启用独立身份，请以本人账号访问。", 401)
            return {"id": OWNER, "display_name": "本机隔离预览操作者"}
        principal = self.store.principal()
        actor = connection.execute("SELECT id,state,auth_epoch,capabilities FROM accounts WHERE id=?", (principal["id"],)).fetchone()
        if not actor or actor["state"] != "active" or actor["auth_epoch"] != principal["auth_epoch"]:
            raise AppError("authentication_required", "登录或职责已失效，请重新登录。", 401)
        if not {"employee", "clerk", "cook"}.intersection(json.loads(actor["capabilities"])):
            raise AppError("forbidden", "个人准备偏好仅用于本人业务职责，管理或仅执行职责不提供该入口。", 403)
        return dict(actor)

    def _context_id(self, actor):
        return "context:" + hashlib.sha256(actor["id"].encode("utf-8")).hexdigest()

    def _current(self, connection, actor):
        row = connection.execute("SELECT kind,response FROM actions WHERE id=?", (self._context_id(actor),)).fetchone()
        if not row:
            return {"version": 0, "notes": [], "scope": SCOPE}
        if row["kind"] != "personal_context":
            raise AppError("context_conflict", "个人上下文存储记录冲突，请保留输入并核查。", 409)
        stored = json.loads(row["response"])
        return {"version": stored["version"], "notes": stored["notes"], "scope": SCOPE}

    def get_context(self):
        with self.store.connect() as connection:
            actor = self._actor(connection)
            return self._current(connection, actor)

    def save_context(self, data, key):
        _strict(data, {"expected_version", "upserts", "removed_ids"})
        try:
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 65536:
                raise ValueError()
        except (TypeError, ValueError, UnicodeError):
            raise AppError("context_limit", "个人偏好请求须为有效JSON且最多64KiB。") from None
        if not isinstance(data["upserts"], list) or not isinstance(data["removed_ids"], list) or len(data["upserts"]) > 12 or len(data["removed_ids"]) > 12 or not (data["upserts"] or data["removed_ids"]):
            raise AppError("context_limit", "每次最多12条，移除须明确列ID，缺行不会清空。")
        with self.store.transaction() as connection:
            actor = self._actor(connection)
            key, fingerprint, previous = self._request(connection, actor, "personal_context_save", None, data, key)
            current = self._current(connection, actor)
            if previous is not None:
                return current
            if type(data["expected_version"]) is not int or data["expected_version"] != current["version"]:
                raise AppError("version_conflict", "个人偏好版本已变化，请读回后保留人工修改。", 409)
            notes = {note["id"]: note for note in current["notes"]}
            removed, seen = data["removed_ids"], set()
            if any(not isinstance(note_id, str) or note_id not in notes for note_id in removed) or len(set(removed)) != len(removed):
                raise AppError("invalid_removed_ids", "只能明确移除本人当前偏好ID，不能重复。")
            stamp = now()
            for incoming in data["upserts"]:
                _strict(incoming, {"id", "text"})
                note_id = incoming["id"]
                if not isinstance(note_id, str) or not SAFE_ID.fullmatch(note_id) or note_id in seen or note_id in removed:
                    raise AppError("invalid_id", "偏好ID须稳定安全、不重复且不同次移除，最多80字符。")
                seen.add(note_id)
                text = string(incoming["text"], "本人明确偏好", 1000)
                notes[note_id] = {"id": note_id, "text": text, "origin": "本人明确输入", "recorded_at": notes.get(note_id, {}).get("recorded_at", stamp), "updated_at": stamp}
            for note_id in removed:
                del notes[note_id]
            if len(notes) > 12:
                raise AppError("context_limit", "本人最多12条当前偏好，请明确修改或移除已有项。")
            payload = {"version": current["version"] + 1, "notes": list(notes.values())}
            connection.execute("INSERT INTO actions(id,matter_id,kind,status,response,created_at) VALUES(?,NULL,'personal_context','completed',?,?) ON CONFLICT(id) DO UPDATE SET response=excluded.response", (self._context_id(actor), dump(payload), stamp))
            # Never cache note text or previous DTO: removed preferences leave no logical request-body history.
            self._remember(connection, key, fingerprint, "personal_context_save", None, {"context_version": payload["version"]})
            return {**payload, "scope": SCOPE}
