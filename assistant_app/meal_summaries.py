"""Explicit scoped meal requests, deterministic deduplication and safe cook projection."""
import hashlib
import json
from collections import defaultdict
from .journey import JourneyService, _date
from .structured import StructuredService, _strict
from .store import AppError, dump, now, string, uid

OP = 'meal_summary_render'


def meal_record(connection, matter_id, artifact_id):
    for row in connection.execute("SELECT response FROM actions WHERE matter_id=? AND kind='request:structured_meal_summary_render' ORDER BY rowid DESC", (matter_id,)):
        record = json.loads(row['response'])
        if record['artifact_id'] == artifact_id:
            return record
    return None


def basis_current(connection, owner_id, basis):
    for item in basis:
        row = connection.execute('SELECT owner,revision_no FROM matters WHERE id=?', (item['id'],)).fetchone()
        if not row or row['owner'] != owner_id or row['revision_no'] != item['revision_no']:
            return False
        if item['linked_id']:
            link = connection.execute('SELECT owner,revision_no FROM matters WHERE id=?', (item['linked_id'],)).fetchone()
            if not link or link['owner'] != owner_id or link['revision_no'] != item['linked_revision']:
                return False
    return True


class MealSummaryService(StructuredService):
    def _owned(self, connection, matter_id):
        actor, matter, facts = JourneyService(self.store)._owned(connection, matter_id)
        if self.store.requires_auth and 'clerk' not in json.loads(connection.execute('SELECT capabilities FROM accounts WHERE id=?', (actor['id'],)).fetchone()[0]):
            raise AppError('forbidden', '跨行程食堂准备汇总由获授权综合经办明确选择，个人餐次仍沿原行程记录。', 403)
        return actor, matter, facts

    def inputs(self, matter_id):
        with self.store.connect() as connection:
            actor, matter, _ = self._owned(connection, matter_id)
            items = [dict(row) for row in connection.execute("SELECT id,goal_text,revision_no FROM matters WHERE owner=? AND domain IN ('travel','leave') ORDER BY updated_at DESC LIMIT 40", (self.store.actor_id(),))]
            reports = [json.loads(row[0]) for row in connection.execute("SELECT response FROM actions WHERE matter_id=? AND kind='request:structured_meal_summary_render' ORDER BY rowid DESC", (matter_id,))]
            for report in reports:
                report['stale'] = not basis_current(connection, self.store.actor_id(), report['basis'])
            cooks = []
            if self.store.requires_auth:
                cooks = [{'id': row['id'], 'display_name': row['display_name']} for row in connection.execute("SELECT id,display_name,capabilities FROM accounts WHERE state='active' ORDER BY display_name,id") if 'cook' in json.loads(row['capabilities'])]
            return {'expected_version': matter['version'], 'items': items, 'reports': reports, 'cooks': cooks,
                    'scope': '只选择本人权限内已保存行程／休假餐次；不同人员引用不证明实际人数，无应报基线，不推未申报或实供。'}

    def _preview(self, connection, matter_id, data):
        _strict(data, {'selected_ids', 'meal_date'})
        ids = data['selected_ids']
        if not isinstance(ids, list) or not 1 <= len(ids) <= 12 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise AppError('invalid_input', '请明确选择1至12个本人行程／休假，不重复选择。')
        _date(data['meal_date'], '本次用餐日期')
        actor, target, _ = self._owned(connection, matter_id)
        self._ensure_preparation_sources(connection, matter_id)
        journey = JourneyService(self.store)
        basis, scoped, total = [], [], 0
        for identifier in ids:
            _, matter, facts = self._owned(connection, identifier)
            self._ensure_preparation_sources(connection, identifier)
            view = journey._view(connection, matter, facts)['journey']
            if view['meal_needs_review']:
                raise AppError('basis_changed', '所选行程餐次待重核，请先在原行程核对保存；保留其他编辑。', 409)
            link = view['travel_link']
            if link:
                _, linked, _ = self._owned(connection, link['matter_id'])
                self._ensure_preparation_sources(connection, linked['id'])
            basis.append({'id': identifier, 'revision_no': matter['revision_no'], 'linked_id': link['matter_id'] if link else None, 'linked_revision': view['current_travel_revision']})
            for row in view['meal_requests']:
                total += 1
                if total > 500:
                    raise AppError('input_limit', '所选餐次记录超过500条，请缩小行程范围。')
                if row['meal_date'] == data['meal_date']:
                    scoped.append({**row, 'matter_id': identifier})
        grouped = defaultdict(list)
        for row in scoped:
            grouped[(row['person_ref'], row['meal_slot'])].append(row)
        details, groups = [], {}
        for (person, slot), rows in grouped.items():
            requests = sorted({row['request'] for row in rows})
            state = requests[0] if len(requests) == 1 else 'conflict'
            group = groups.setdefault(slot, {'meal_slot': slot, 'keep': 0, 'request_reduce': 0, 'pending': 0, 'conflict': 0, 'unique_refs': 0, 'duplicates': 0})
            group[state] += 1
            group['unique_refs'] += 1
            group['duplicates'] += len(rows) - 1
            details.append({'person_ref': person, 'meal_slot': slot, 'state': state, 'requests': requests, 'records': rows})
        groups = sorted(groups.values(), key=lambda row: row['meal_slot'])
        title = data['meal_date'] + '所选餐次需求准备汇总'
        lines = ['# '+title, '仅明确选择的'+str(len(ids))+'份已保存行程／休假原述需求，不代表完整食堂人数、有效扣餐、备餐或实供。',
                 '同一人员原述引用＋日期＋原述餐别只计一次；引用是否对应唯一真实人员由经办核对，不同标签不自动当不同人。',
                 '保餐与调整／待核冲突单列，不自动覆盖手工保餐。饭点、触发、截止、冻结、应报基线仍待核。', '',
                 '| 原述餐别 | 希望保餐引用 | 调整申请引用 | 待核引用 | 冲突引用 | 不同引用数 | 重复原记录数 |', '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
        for group in groups:
            slot = group['meal_slot'].replace('|', '\\|').replace('\n', ' ')
            lines.append('| '+slot+' | '+' | '.join(str(group[key]) for key in ('keep', 'request_reduce', 'pending', 'conflict', 'unique_refs', 'duplicates'))+' |')
        if not groups:
            lines.append('本次所选范围在该日期没有已保存餐次记录；人数未知，不以缺记录推人数0。')
        content = '\n'.join(lines)
        if len(content) > 20000:
            raise AppError('input_limit', '固定汇总超过20000字，请缩小范围；原餐次记录保留，不截断。')
        token = hashlib.sha256(dump([matter_id, data, basis, scoped]).encode()).hexdigest()
        return {'expected_version': target['version'], 'input_revision': target['revision_no'], 'selected_ids': ids, 'meal_date': data['meal_date'],
                'basis': basis, 'basis_token': token, 'groups': groups, 'details': details, 'title': title, 'cook_content': content, 'has_records': bool(scoped)}

    def preview(self, matter_id, data):
        with self.store.lock, self.store.connect() as connection:
            return self._preview(connection, matter_id, data)

    def render_report(self, matter_id, data, key):
        _strict(data, {'expected_version', 'selected_ids', 'meal_date', 'basis_token'})
        with self.store.transaction() as connection:
            actor, matter, _ = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, OP, matter_id, data, key)
            if previous is not None:
                return previous
            view = self._preview(connection, matter_id, {key: data[key] for key in ('selected_ids', 'meal_date')})
            if view['basis_token'] != data['basis_token']:
                raise AppError('basis_changed', '所选行程或餐次已变化，请重读核对，旧稿保留。', 409)
            self.store._check_version(matter, data)
            if matter['assistant_status'] in {'paused', 'handoff', 'processing'}:
                raise AppError('assistant_stopped', '请恢复或等当前准备结束后另存新汇总；已保存稿可人工编辑。', 409)
            if not view['has_records']:
                raise AppError('no_records', '该日期所选范围没有餐次记录；不生成假人数0。')
            artifact_id = uid()
            connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)', (artifact_id, 1, matter_id, 'meal_summary', view['title'], view['cook_content'], 'structured_draft', matter['revision_no'], now()))
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, 'meal_summary_saved', '本人所选行程餐次需求已程序去重／冲突单列并另存可编辑稿；仅原述引用计数，无完整人数基线，不减餐或登记实供。')
            result = {key: view[key] for key in ('basis', 'meal_date', 'groups', 'title', 'cook_content')}
            result['artifact_id'] = artifact_id
            return self._remember(connection, key, fingerprint, OP, matter_id, result)
