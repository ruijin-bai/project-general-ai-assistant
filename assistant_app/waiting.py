"""Owner-editable waiting points and local-only reminders, recorded as events."""

import json
import re
from datetime import datetime, timezone

from .store import AppError, dump, string, uid, now
from .structured import SAFE_ID, StructuredService, _strict

SCOPE = "本人注明的等待对象、下一步与本地提醒；不授他人权限，不代表真实受理、业务办结、自动催办或消息送达"


def _deadline(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 100 or not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T", value):
        raise AppError("invalid_due_at", "本地提醒时间须为带明确UTC偏移的ISO日期时间；未知可留null。")
    try:
        deadline = datetime.fromisoformat(value)
        if deadline.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise AppError("invalid_due_at", "本地提醒缺明确时区或日期无效，不能补默认时区。") from None
    return deadline


class WaitingService(StructuredService):
    def _owned(self, connection, matter_id):
        matter = self.store._matter(connection, matter_id)
        return self._owner(connection, matter_id, matter["domain"])

    def _latest(self, connection, matter_id):
        items = {}
        for event in connection.execute("SELECT message,recorded_by,recorded_at FROM events WHERE matter_id=? AND event_type='waiting_changed' ORDER BY rowid", (matter_id,)):
            change = json.loads(event["message"])
            recorded = {"recorded_by": event["recorded_by"], "recorded_by_name": change["actor_display_name"], "recorded_at": event["recorded_at"]}
            for item in change["upserts"]:
                items[item["id"]] = {**item, "active": True, **recorded}
            for item_id in change["removed_ids"]:
                items[item_id] = {**items[item_id], "active": False, **recorded}
        return items

    def _view(self, connection, matter):
        timestamp = datetime.now(timezone.utc)
        items = []
        from .material_nodes import waiting_bindings, capture_basis
        bindings = waiting_bindings(connection, matter['id'])
        facts = self.store._stored_facts(connection, matter) if bindings else None
        for saved in self._latest(connection, matter["id"]).values():
            item = dict(saved)
            deadline = _deadline(item["due_at"])
            item["due_now"] = False if not item["active"] or item["state"] == "resolved" else None if deadline is None else timestamp >= deadline
            bound = bindings.get(item['id'])
            if bound:
                current_basis = capture_basis(self.store, connection, matter, facts, bound['basis']['material']['id'])
                item['material_basis'] = {'kind': bound['basis']['material']['kind'], 'date': bound['basis']['material']['valid_until'], 'input_revision': bound['input_revision']}
                item['basis_stale'] = current_basis != bound['basis']
            items.append(item)
        return {"id": matter["id"], "version": matter["version"], "items": items, "scope": SCOPE}

    def get_waiting(self, matter_id):
        with self.store.connect() as connection:
            _, matter, _ = self._owned(connection, matter_id)
            return self._view(connection, matter)

    def get_followup(self, matter_id, point_id):
        with self.store.connect() as connection:
            connection.execute('BEGIN')
            _, matter, _ = self._owned(connection, matter_id)
            self._ensure_preparation_sources(connection, matter_id)
            if matter['assistant_status'] in {'paused', 'handoff'}:
                raise AppError('assistant_stopped', '未来文字准备已停止；已有稿仍可手工编辑保存。', 409)
            point = next((item for item in self._view(connection, matter)['items'] if item['id'] == point_id), None)
            if not point or not point['active']:
                raise AppError('not_found', '此已保存等待点不存在或已移除。', 404)
            if point.get('basis_stale'):
                raise AppError('waiting_basis_changed', '材料日期、原处或相关事实已变化，请先核对旧待办文字与提醒，再明确采用当前依据；原人工稿保留。', 409)
            if point['state'] != 'waiting':
                raise AppError('waiting_resolved', '此等待点已由本人标处理，不生成新催办文字。', 409)
            goal = matter['goal_text']
            title = goal[:500] + ('（仅显示长目标开头，完整目标请在原事项核对）' if len(goal) > 500 else '')
            lines = ['# 等待跟进文字 · 待本人编辑', '', '事项：' + title,
                     '本人注明的等待对象：' + point['who'], '', '当前记录的等待：' + point['reason'],
                     '建议沿原渠道核对：' + point['next_step'],
                     '本人设置的本地提醒：' + (point['due_at'] or '未设；不猜业务截止时间'),
                     '补充备注：' + (point['note'] or '未补充'), '',
                     '等待点记录时间：' + point['recorded_at'],
                     '本地事项版本：' + str(matter['version']),
                     '仅从当前已保存等待点准备，未使用当前未保存编辑；不是模型生成或实际责任人的答复。',
                     '请核对后按实际授权沿原渠道使用；本系统未发送、未催办、未证明受理或完成。']
            return {'id': matter_id, 'version': matter['version'], 'input_revision': matter['revision_no'],
                    'point_id': point_id, 'recorded_at': point['recorded_at'], 'content': '\n'.join(lines), 'scope': SCOPE}

    def save_followup(self, matter_id, point_id, data, key):
        _strict(data, {'expected_version', 'input_revision', 'content'})
        content = string(data['content'], '本人等待跟进文字', 16000)
        with self.store.transaction() as connection:
            actor, matter, _ = self._owned(connection, matter_id)
            request_data = {**data, 'point_id': point_id}
            key, fingerprint, previous = self._request(connection, actor, 'waiting_followup_save', matter_id, request_data, key)
            if previous is not None:
                return previous
            self.store._check_version(matter, data)
            if type(data['input_revision']) is not int or data['input_revision'] != matter['revision_no']:
                raise AppError('revision_conflict', '事实修订已变化，请保留人工文字并核对当前等待。', 409)
            if point_id not in self._latest(connection, matter_id):
                raise AppError('not_found', '原等待点不存在，不能代造已保存等待。', 404)
            artifact_id = uid()
            connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)',
                               (artifact_id, 1, matter_id, 'waiting_followup', '等待跟进人工文字', content, 'human_saved', matter['revision_no'], now()))
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, 'waiting_followup_saved', '本人另存了私有等待跟进文字；未发送、未催办，等待与实际业务状态未改变。')
            return self._remember(connection, key, fingerprint, 'waiting_followup_save', matter_id,
                                  {'artifact_id': artifact_id, 'version': 1, 'type': 'waiting_followup',
                                   'input_revision': matter['revision_no'], 'content': content, 'scope': SCOPE})

    def _item(self, item):
        _strict(item, {"id", "who", "reason", "next_step", "due_at", "state", "note"})
        if not isinstance(item["id"], str) or not SAFE_ID.fullmatch(item["id"]):
            raise AppError("invalid_id", "等待点须有稳定安全ID，最多80字符。")
        if item["state"] not in ("waiting", "resolved"):
            raise AppError("invalid_state", "只可标本地等待或本人认为已处理，不代表业务办结。")
        _deadline(item["due_at"])
        if not isinstance(item["note"], str) or len(item["note"]) > 2000:
            raise AppError("invalid_note", "等待备注须为最多2000字符文字，可空。")
        return {"id": item["id"], "who": string(item["who"], "本人注明等待对象", 200), "reason": string(item["reason"], "等待原因", 2000),
                "next_step": string(item["next_step"], "下一步", 2000), "due_at": item["due_at"], "state": item["state"], "note": item["note"].strip()}

    def save_waiting(self, matter_id, data, key):
        _strict(data, {"expected_version", "upserts", "removed_ids"})
        try:
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 131072:
                raise ValueError()
        except (TypeError, ValueError, UnicodeError):
            raise AppError("waiting_limit", "等待请求须为有效JSON且最多128KiB。") from None
        if not isinstance(data["upserts"], list) or not isinstance(data["removed_ids"], list) or len(data["upserts"]) > 20 or len(data["removed_ids"]) > 20 or not (data["upserts"] or data["removed_ids"]):
            raise AppError("waiting_limit", "每次最多20项，删除须明确列ID，空列表不会清空。")
        with self.store.transaction() as connection:
            actor, matter, _ = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, "waiting_save", matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter)
            self.store._check_version(matter, data)
            items = self._latest(connection, matter_id)
            removed, seen = data["removed_ids"], set()
            if any(not isinstance(item_id, str) or item_id not in items for item_id in removed) or len(set(removed)) != len(removed):
                raise AppError("invalid_removed_ids", "只能明确移除本事项已有等待点，ID不能重复。")
            upserts = []
            for incoming in data["upserts"]:
                item = self._item(incoming)
                if item["id"] in seen or item["id"] in removed:
                    raise AppError("invalid_id", "等待点ID重复或同时移除，不能隐式合并。")
                seen.add(item["id"])
                items[item["id"]] = {**item, "active": True}
                upserts.append(item)
            for item_id in removed:
                items[item_id]["active"] = False
            if sum(item["active"] for item in items.values()) > 20:
                raise AppError("waiting_limit", "每个事项最多20个未移除等待点，请明确修改或移除已有点。")
            self.store._event(connection, matter_id, "waiting_changed", dump({"actor_id": actor["id"], "actor_display_name": actor["display_name"], "upserts": upserts, "removed_ids": removed}))
            self.store._bump(connection, matter_id)
            return self._remember(connection, key, fingerprint, "waiting_save", matter_id, self._view(connection, self.store._matter(connection, matter_id)))
