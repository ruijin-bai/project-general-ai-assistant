"""Explicit source-bound material date review nodes, reusing private waiting events."""
import hashlib
import json
from .journey import LABELS, MATERIAL_KINDS, JourneyService, _date
from .material_records import MaterialRecordsService
from .store import AppError, dump, string, uid, now
from .structured import _strict


def capture_basis(store, connection, matter, facts, material_id):
    item = next((item for item in facts.get('__materials', {}).get('items', []) if item['id'] == material_id), None)
    if not item: return None
    fields = ('person', 'flight_info', 'requirements_ref', 'leave_start', 'leave_end', 'departure_at', 'return_at')
    known = {key: {k: facts.get(key, {}).get(k) for k in ('value', 'status')} for key in fields}
    material = {key: item.get(key) for key in ('id', 'kind', 'availability', 'source_id', 'source_position', 'valid_until', 'check')}
    state = facts.get('__source_states', {}).get('items', {}).get(item.get('source_id'), {}).get('state', 'usable')
    link = facts.get('__journey', {}).get('travel_link')
    linked = None
    if link:
        try:
            target = store._matter(connection, link['matter_id'])
            target_facts = store._stored_facts(connection, target)
            linked = {'link': link, 'fields': {key: {k: target_facts.get(key, {}).get(k) for k in ('value', 'status')} for key in ('participants', 'destination', 'departure_at', 'return_at')}}
        except AppError:
            linked = {'unavailable': True}
    result = {'material': material, 'source_state': state, 'facts': known, 'linked_travel': linked}
    checks = facts.get('__checks', {}).get('items', [])
    if checks:
        states = facts.get('__source_states', {}).get('items', {})
        result['requirements'] = [{key: item.get(key) for key in ('requirement_text', 'requirement_refs', 'provided_refs')} for item in checks]
        result['requirement_source_states'] = {ref['source_id']: states.get(ref['source_id'], {}).get('state', 'usable') for item in checks for ref in item['requirement_refs'] + item['provided_refs']}
    return result


def waiting_bindings(connection, matter_id):
    result = {}
    for row in connection.execute("SELECT response FROM actions WHERE matter_id=? AND kind='request:structured_material_node_bind' ORDER BY rowid", (matter_id,)):
        bound = json.loads(row['response'])
        result[bound['point_id']] = bound
    return result


class MaterialNodesService(MaterialRecordsService):
    def _candidates(self, connection, matter, facts):
        from .waiting import WaitingService
        waiting = WaitingService(self.store)._latest(connection, matter['id'])
        items = []
        for kind in MATERIAL_KINDS:
            material = next((item for item in facts.get('__materials', {}).get('items', []) if item['kind'] == kind), None)
            basis = capture_basis(self.store, connection, matter, facts, material['id']) if material else None
            eligible = bool(material and material['availability'] == 'source_provided' and material.get('valid_until') and material['check'] == 'local_source_checked' and basis['source_state'] == 'usable')
            point_id = 'mn-' + hashlib.sha256((matter['id']+'\0'+material['id']).encode()).hexdigest()[:32] if material else None
            excerpt = JourneyService(self.store)._position(connection, matter['id'], material['source_id'], material['source_position']) if material and material.get('source_id') else None
            items.append({'kind': kind, 'label': LABELS[kind], 'material_id': material['id'] if material else None, 'date': material.get('valid_until') if material else None,
                          'eligible': eligible, 'source_id': material.get('source_id') if material else None, 'source_position': material.get('source_position') if material else None,
                          'excerpt': excerpt, 'point_id': point_id, 'existing_point': waiting.get(point_id), 'basis': basis,
                          'basis_token': hashlib.sha256(dump(basis).encode()).hexdigest()})
        return {'id': matter['id'], 'version': matter['version'], 'revision_no': matter['revision_no'], 'items': items,
                'scope': '从本人已核材料日期建立个人核对待办；提醒时点另由本人明确选择，未判断有效、过期、办理截止或可通行，未发送通知。'}

    def get_candidates(self, matter_id):
        with self.store.connect() as connection:
            _, matter, facts = self._owned(connection, matter_id)
            result = self._candidates(connection, matter, facts)
            bindings = waiting_bindings(connection, matter_id)
            for item in result['items']:
                old = bindings.get(item['point_id'])
                item['bound_date'] = old['basis']['material']['valid_until'] if old else None
                item['basis_stale'] = old['basis'] != item['basis'] if old else None
            from .material_dates import compare_material_dates
            result['date_report'] = compare_material_dates(self.store, connection, matter, facts, result['items'])
            return result

    def render_dates(self, matter_id, data, key):
        _strict(data, {'expected_version', 'input_revision', 'report_token'})
        from .material_dates import compare_material_dates, comparison_text
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, 'material_dates_render', matter_id, data, key)
            if previous is not None: return previous
            self._baseline(matter, data)
            if matter['assistant_status'] in {'paused', 'handoff'}:
                raise AppError('assistant_stopped', '未来准备已停止，旧稿仍可人工修改；恢复后再另存日期核对稿。', 409)
            report = compare_material_dates(self.store, connection, matter, facts, self._candidates(connection, matter, facts)['items'])
            if data['report_token'] != report['report_token']:
                raise AppError('material_dates_changed', '日期比较依据或关联行程已变化，请重读并核对；旧稿与输入保留。', 409)
            if not report['computed_count']:
                raise AppError('material_dates_pending', '尚无可比较的本人确认日期；补清单与计划后重读，不生成空计算稿。', 409)
            self._ensure_preparation_sources(connection, matter_id, [row['source_id'] for row in report['items'] if row['status'] == 'computed'])
            lines = ['# 材料日期程序核对稿', '', report['scope'], '事实修订：' + str(matter['revision_no']),
                     '程序已比较 ' + str(report['computed_count']) + ' 项；待核 ' + str(report['pending_count']) + ' 项。', '']
            for kind in MATERIAL_KINDS:
                lines.append('## ' + LABELS[kind])
                rows = [row for row in report['items'] if row['kind'] == kind]
                for row in rows: lines.append('- ' + comparison_text(row))
                if any(row['status'] == 'computed' for row in rows):
                    first = rows[0]
                    lines += ['已录材料原处：' + first['source_id'] + ' ' + first['source_position'], first['excerpt']]
                lines.append('')
            content, artifact_id = '\n'.join(lines), uid()
            connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)', (artifact_id, 1, matter_id, 'material_date_review', '材料日期程序核对稿', content, 'structured_draft', matter['revision_no'], now()))
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, 'material_dates_rendered', '已另存基于本人确认存值的程序日期核对稿；原稿保留，未判断效力、过期或可通行。')
            return self._remember(connection, key, fingerprint, 'material_dates_render', matter_id, {'artifact_id': artifact_id, 'version': 1, 'content': content, 'report_token': report['report_token'], 'input_revision': matter['revision_no']})

    def bind(self, matter_id, data, key):
        _strict(data, {'expected_version', 'input_revision', 'material_id', 'mode', 'basis_token'}, {'who', 'due_at', 'note'})
        if data['mode'] not in ('create', 'rebind') or not isinstance(data['material_id'], str):
            raise AppError('invalid_node', '节点须明确选择已保存材料及创建／本人重核依据方式。')
        expected_keys = {'expected_version', 'input_revision', 'material_id', 'mode', 'basis_token'} | ({'who', 'due_at', 'note'} if data['mode'] == 'create' else set())
        if set(data) != expected_keys: raise AppError('invalid_node', '创建须明确提醒与对象；重核依据不接受改写旧待办。')
        from .waiting import WaitingService
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, 'material_node_bind', matter_id, data, key)
            if previous is not None: return previous
            self._baseline(matter, data)
            candidate = next((item for item in self._candidates(connection, matter, facts)['items'] if item['material_id'] == data['material_id']), None)
            if not candidate or not candidate['eligible']:
                raise AppError('material_date_pending', '须先保存并本人核对材料来源和明确日期；缺日期、旧来源或未知口径不猜节点。', 409)
            if data['basis_token'] != candidate['basis_token']:
                raise AppError('material_basis_changed', '所读材料或关联行程已变化，请重新读取核对；旧待办与人工输入保留。', 409)
            _date(candidate['date'], '已录材料日期')
            service = WaitingService(self.store)
            items = service._latest(connection, matter_id)
            point_id = candidate['point_id']
            point = items.get(point_id)
            if data['mode'] == 'create' and point:
                return {'point_id': point_id, 'created': False, 'version': matter['version'], 'scope': '同一材料已有待办及人工修改，未重复创建、恢复已移除项或覆盖。'}
            if data['mode'] == 'rebind' and (not point or not point['active']):
                raise AppError('node_unavailable', '原待办不存在或已明确移除；先沿原等待点核对，不自动恢复。', 409)
            if data['mode'] == 'create':
                if sum(item['active'] for item in items.values()) >= 20: raise AppError('waiting_limit', '当前事项已有20个等待点，请先核对现有待办。')
                point = service._item({'id': point_id, 'who': string(data['who'], '本人选择等待对象', 200),
                    'reason': candidate['label']+'本人已录材料日期 '+candidate['date']+'；核对当前适用与办理缺项。',
                    'next_step': '核对当前原处、材料日期、适用要求、缺项与真实回执；不据此判断有效或可通行。',
                    'due_at': data['due_at'], 'state': 'waiting', 'note': data['note']})
                self.store._event(connection, matter_id, 'waiting_changed', dump({'actor_id': actor['id'], 'actor_display_name': actor['display_name'], 'upserts': [point], 'removed_ids': []}))
            else:
                self.store._event(connection, matter_id, 'material_node_rebound', '本人明确重核材料节点依据；旧待办文字、对象、提醒与处理状态保留，未改外部申请。')
            self.store._bump(connection, matter_id)
            result = {'point_id': point_id, 'created': data['mode'] == 'create', 'basis': candidate['basis'], 'input_revision': matter['revision_no'], 'version': matter['version']+1,
                      'scope': '本地核对节点；未把材料日期当业务截止或通知已送达。'}
            return self._remember(connection, key, fingerprint, 'material_node_bind', matter_id, result)
