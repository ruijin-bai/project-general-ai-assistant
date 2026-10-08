"""Explicit private quiet preferences for local reminder summaries only."""

import hashlib
import json
from datetime import datetime, timezone

from .context import ContextService
from .store import AppError, dump, now
from .structured import _strict
from .waiting import _deadline


SCOPE = "仅抑制本人本地工作列表的到时提醒汇总；关键问题与等待点仍保留在事项中。不改变业务、准备或暂停状态，不发送外部通知，不代表已有消息送达。"


class ReminderPreferencesService(ContextService):
    def _preferences_id(self, actor):
        return "reminder-preferences:" + hashlib.sha256(actor["id"].encode("utf-8")).hexdigest()

    def _preferences(self, connection, actor):
        row = connection.execute("SELECT kind,response FROM actions WHERE id=?", (self._preferences_id(actor),)).fetchone()
        if row is None:
            payload = {"version": 0, "mode": "normal", "quiet_until": None}
        else:
            if row["kind"] != "reminder_preferences":
                raise AppError("preferences_conflict", "本地提醒偏好记录冲突，请保留设置并核查。", 409)
            payload = json.loads(row["response"])
        deadline = _deadline(payload["quiet_until"])
        active = payload["mode"] == "quiet" and (deadline is None or datetime.now(timezone.utc) < deadline)
        return {"version": payload["version"], "mode": payload["mode"], "quiet_until": payload["quiet_until"],
                "quiet_active": active, "scope": SCOPE}

    def get_preferences(self):
        with self.store.connect() as connection:
            actor = self._actor(connection)
            return self._preferences(connection, actor)

    def save_preferences(self, data, key):
        _strict(data, {"expected_version", "mode", "quiet_until"})
        if type(data["expected_version"]) is not int or data["expected_version"] < 0:
            raise AppError("invalid_version", "提醒偏好版本须为非负整数，不能使用布尔或文字。")
        if not isinstance(data["mode"], str) or data["mode"] not in ("normal", "quiet"):
            raise AppError("invalid_mode", "本地提醒模式仅允许 normal 或 quiet。")
        if data["mode"] == "normal" and data["quiet_until"] is not None:
            raise AppError("invalid_quiet_until", "正常提醒的静默期限须明确为 null。")
        _deadline(data["quiet_until"])
        with self.store.transaction() as connection:
            actor = self._actor(connection)
            key, fingerprint, previous = self._request(connection, actor, "reminder_preferences_save", None, data, key)
            current = self._preferences(connection, actor)
            if previous is not None:
                return current
            if data["expected_version"] != current["version"]:
                raise AppError("version_conflict", "本地提醒偏好已变化，请读回当前版本后修改。", 409)
            payload = {"version": current["version"] + 1, "mode": data["mode"], "quiet_until": data["quiet_until"]}
            connection.execute("INSERT INTO actions(id,matter_id,kind,status,response,created_at) VALUES(?,NULL,'reminder_preferences','completed',?,?) "
                               "ON CONFLICT(id) DO UPDATE SET response=excluded.response", (self._preferences_id(actor), dump(payload), now()))
            self._remember(connection, key, fingerprint, "reminder_preferences_save", None, {"preferences_version": payload["version"]})
            return self._preferences(connection, actor)
