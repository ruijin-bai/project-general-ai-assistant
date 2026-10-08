"""Private, separate material submission statements and source receipt reviews."""
import json
from .journey import MATERIAL_KINDS, LABELS, _date, _instant
from .structured import StructuredService, _strict
from .store import AppError, dump, now, string, uid

RECORD_TYPES = {'submission_statement', 'receipt_reference', 'receipt_source_checked', 'needs_review'}
TYPE_LABELS = {'submission_statement': '本人记录提交声明', 'receipt_reference': '已提供回执来源', 'receipt_source_checked': '本人已核回执原文', 'needs_review': '本人标记材料或手续待重核'}
SCOPE = '各材料的提交声明、回执来源与本人核对分别保留；仅私有记录，不提交外部申请，不核真签发或判断获准、可通行。'


class MaterialRecordsService(StructuredService):
    def _owned(self, connection, matter_id):
        matter = self.store._matter(connection, matter_id)
        if matter['domain'] != 'leave':
            raise AppError('invalid_domain', '材料办理记录仅用于签证休假返岗事项。')
        if self.store.requires_auth and not set(self.store.principal()['capabilities']) & {'employee', 'clerk', 'cook'}:
            raise AppError('permission_denied', '当前账号未获私人材料准备职责。', 403)
        return self._owner(connection, matter_id, 'leave')

    def _baseline(self, matter, data):
        self.store._check_version(matter, data)
        if type(data['input_revision']) is not int or data['input_revision'] != matter['revision_no']:
            raise AppError('version_conflict', '材料依据修订已变化，请保留说明并读回核对。', 409)

    def _view(self, connection, matter, facts):
        records = []
        states = facts.get('__source_states', {}).get('items', {})
        for row in connection.execute("SELECT * FROM events WHERE matter_id=? AND event_type='material_record' ORDER BY rowid", (matter['id'],)):
            item = json.loads(row['message'])
            item.update(id=row['id'], recorded_at=row['recorded_at'], recorded_by=row['recorded_by'])
            refs = self._refs(connection, matter['id'], [item['ref']])
            item['excerpt'] = refs[0]['excerpt']
            item['source_state'] = states.get(item['ref']['source_id'], {}).get('state', 'usable')
            item['stale'] = item['input_revision'] != matter['revision_no'] or item['source_state'] != 'usable'
            records.append(item)
        material_states = []
        for kind in MATERIAL_KINDS:
            items = [item for item in records if item['material_kind'] == kind]
            def latest(record_type): return next((item for item in reversed(items) if item['record_type'] == record_type), None)
            submission, receipt, review = latest('submission_statement'), latest('receipt_reference'), latest('needs_review')
            def obsolete(item): return bool(item and (item['stale'] or review and items.index(review) > items.index(item)))
            submission_status = 'needs_review' if obsolete(submission) else 'statement_recorded' if submission else 'unknown'
            receipt_status = 'unknown'
            if receipt:
                checked = next((item for item in reversed(items) if item['record_type'] == 'receipt_source_checked' and item['receipt_id'] == receipt['id']), None)
                receipt_status = 'needs_review' if obsolete(receipt) or checked and obsolete(checked) else 'local_source_checked' if checked else 'source_provided'
            material_states.append({'kind': kind, 'label': LABELS[kind], 'submission': submission_status, 'receipt': receipt_status,
                                    'receipt_id': receipt['id'] if receipt else None, 'formal_validity': 'unknown', 'review_note': review['note'] if review else None})
        return {'id': matter['id'], 'version': matter['version'], 'revision_no': matter['revision_no'], 'items': material_states, 'records': records, 'scope': SCOPE}

    def get_records(self, matter_id):
        with self.store.connect() as connection:
            _, matter, facts = self._owned(connection, matter_id)
            return self._view(connection, matter, facts)

    def record(self, matter_id, data, key):
        _strict(data, {'expected_version', 'input_revision', 'material_kind', 'record_type', 'note', 'occurred_at', 'ref'}, {'receipt_id'})
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, 'material_record', matter_id, data, key)
            if previous is not None:
                return {**self._view(connection, matter, facts), 'saved_record_id': previous['saved_record_id']}
            self._baseline(matter, data)
            if not isinstance(data['material_kind'], str) or data['material_kind'] not in MATERIAL_KINDS or not isinstance(data['record_type'], str) or data['record_type'] not in RECORD_TYPES:
                raise AppError('invalid_material_record', '材料类型与记录性质须为列明值，不能登记模型批准或通行结论。')
            ref = self._refs(connection, matter_id, [data['ref']])[0]
            state = facts.get('__source_states', {}).get('items', {}).get(ref['source_id'], {}).get('state', 'usable')
            if state != 'usable':
                raise AppError('source_not_current', '选定原处已暂不用或被替代，请引用当前来源；历史保留。', 409)
            # Actual manual records do not disclose unrelated withheld sources.
            occurred = data['occurred_at']
            if occurred is not None:
                _date(occurred, '实际日期') if isinstance(occurred, str) and len(occurred) == 10 else _instant(occurred)
            item = {'material_kind': data['material_kind'], 'record_type': data['record_type'], 'note': string(data['note'], '本人实际说明', 2000),
                    'occurred_at': occurred, 'input_revision': matter['revision_no'], 'actor_id': actor['id'], 'actor_name': actor['display_name'],
                    'ref': {key: ref[key] for key in ('source_id', 'start_line', 'end_line')}}
            if data['record_type'] == 'receipt_source_checked':
                view = self._view(connection, matter, facts)
                state = next(state for state in view['items'] if state['kind'] == data['material_kind'])
                receipt = next((record for record in view['records'] if record['id'] == data.get('receipt_id') and record['material_kind'] == data['material_kind'] and record['record_type'] == 'receipt_reference'), None)
                if not receipt or receipt['id'] != state['receipt_id'] or state['receipt'] == 'needs_review' or receipt['ref'] != item['ref']:
                    raise AppError('receipt_changed', '须核对同类当前适用的回执原处；旧版或待重核来源先明确重新引用，原记录保留。', 409)
                item['receipt_id'] = receipt['id']
            elif 'receipt_id' in data:
                raise AppError('invalid_material_record', '只有本人核对回执原文可关联明确回执ID。')
            event_id = uid()
            connection.execute('INSERT INTO events VALUES(?,?,?,?,?,?)', (event_id, matter_id, 'material_record', dump(item), actor['id'], now()))
            self.store._bump(connection, matter_id)
            result = {**self._view(connection, self.store._matter(connection, matter_id), facts), 'saved_record_id': event_id}
            return self._remember(connection, key, fingerprint, 'material_record', matter_id, result)

    def render(self, matter_id, data, key):
        _strict(data, {'expected_version', 'input_revision'})
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, 'material_records_render', matter_id, data, key)
            if previous is not None: return previous
            self._baseline(matter, data)
            if matter['assistant_status'] in {'paused', 'handoff'}:
                raise AppError('assistant_stopped', '助理已停止未来准备，请恢复后另存稿；已存记录保留。', 409)
            view = self._view(connection, matter, facts)
            if not view['records']: raise AppError('records_empty', '尚无来源记录，不能生成办理记录稿。')
            self._ensure_preparation_sources(connection, matter_id, [item['ref']['source_id'] for item in view['records']])
            lines = ['# 材料办理声明与回执核对稿', '', SCOPE, '事实修订：'+str(matter['revision_no']), '', '## 各材料独立情况']
            submission_labels = {'unknown': '未登记，实际提交未知', 'statement_recorded': '本人已登记提交声明，未核外部提交', 'needs_review': '原提交声明保留，当前依据待重核'}
            receipt_labels = {'unknown': '未提供，真实回执未知', 'source_provided': '已提供回执原处，待本人核对', 'local_source_checked': '本人已核所给原文，正式效力未知', 'needs_review': '原回执保留，当前依据待重核'}
            for state in view['items']:
                lines.append('- '+state['label']+'：'+submission_labels[state['submission']]+'；'+receipt_labels[state['receipt']]+'；正式适用及效力未知。')
            lines += ['', '## 来源与历史记录（不代表外部已更新）']
            for record in view['records']:
                ref = record['ref']
                lines += ['', '### '+LABELS[record['material_kind']]+' · '+TYPE_LABELS[record['record_type']], record['note'], '实际日期：'+(record['occurred_at'] or '未提供，待核'),
                          '来源 '+ref['source_id']+' 第'+str(ref['start_line'])+'–'+str(ref['end_line'])+'行', record['excerpt'], '本记录依据待重核。' if record['stale'] else '本人所给来源，不核真签发。']
            content, artifact_id = '\n'.join(lines), uid()
            connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)', (artifact_id, 1, matter_id, 'material_records_preparation', '材料办理声明与回执核对稿', content, 'structured_draft', matter['revision_no'], now()))
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, 'material_records_rendered', '材料办理记录另存可编辑稿；原人工稿保留，未提交申请、签发或更新外部内容。')
            return self._remember(connection, key, fingerprint, 'material_records_render', matter_id, {'artifact_id': artifact_id, 'version': 1, 'content': content})
