"""Explicit saved-row selections with exact groups and a fixed CSV snapshot."""
import json
import re
from .calculations import summarize_rows
from .store import AppError, dump, now, uid
from .structured import StructuredService, _strict
from .expense_journey import expense_context, context_lines

SCOPE = '仅按本次明确选中的已保存明细累计；币种／物品／单位／期间分别计算，项目组织范围及应报清单待核，不判缺报、报销获批、支付、实际收发或库存余额。'


def duplicates(rows, domain):
    dimension = 'currency' if domain == 'expense' else 'unit'
    groups = {}
    for row in rows:
        key = tuple(row[name] for name in ('source_id', 'source_position', 'label', 'value', dimension, 'period'))
        groups.setdefault(key, []).append(row['id'])
    return [ids for ids in groups.values() if len(ids) > 1]


class RowReportsService(StructuredService):
    def _owned(self, connection, matter_id):
        matter = self.store._matter(connection, matter_id)
        if matter['domain'] not in {'expense', 'inventory'}:
            raise AppError('invalid_domain', '选期明细汇总仅用于费用或物资事项。')
        if self.store.requires_auth and not set(self.store.principal()['capabilities']) & {'employee', 'clerk', 'cook'}:
            raise AppError('permission_denied', '当前账号未获私有明细准备职责。', 403)
        return self._owner(connection, matter_id, matter['domain'])

    def _reports(self, connection, matter_id):
        return [json.loads(row['response']) for row in connection.execute("SELECT response FROM actions WHERE matter_id=? AND kind='request:structured_rows_report_render' ORDER BY rowid DESC", (matter_id,))]

    def get_inputs(self, matter_id):
        with self.store.connect() as connection:
            _, matter, facts = self._owned(connection, matter_id)
            rows = facts.get('__rows', [])
            return {'id': matter_id, 'version': matter['version'], 'revision_no': matter['revision_no'], 'rows_revision': facts.get('__rows_revision'),
                    'stale': facts.get('__rows_revision') != matter['revision_no'], 'rows': rows,
                    'periods': list(dict.fromkeys(row['period'] for row in rows)), 'duplicate_groups': duplicates(rows, matter['domain']),
                    'reports': [{key: report[key] for key in ('artifact_id', 'rows_revision', 'selected_count', 'periods', 'note')} for report in self._reports(connection, matter_id)], 'scope': SCOPE}

    def render_report(self, matter_id, data, key):
        _strict(data, {'expected_version', 'input_revision', 'rows_revision', 'row_ids', 'note', 'duplicate_acknowledged'})
        ids = data['row_ids']
        if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise AppError('invalid_rows', '须明确选择1至200个不同的已保存行ID，不自动选择全部。')
        if type(data['duplicate_acknowledged']) is not bool or not isinstance(data['note'], str) or len(data['note']) > 2000:
            raise AppError('invalid_scope', '重复口径须明确布尔值；本人范围备注最多2000字，可空。')
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            key, fingerprint, previous = self._request(connection, actor, 'rows_report_render', matter_id, data, key)
            if previous is not None: return previous
            self.store._check_version(matter, data)
            if type(data['input_revision']) is not int or data['input_revision'] != matter['revision_no'] or type(data['rows_revision']) is not int or data['rows_revision'] != facts.get('__rows_revision') or data['rows_revision'] != matter['revision_no']:
                raise AppError('rows_report_stale', '来源或事实已换版，明细须明确重核保存；旧选择、人工稿与历史报告保留。', 409)
            if matter['assistant_status'] in {'paused', 'handoff'}:
                raise AppError('assistant_stopped', '新汇总准备已停止；旧稿仍可编辑保存，恢复后再另存。', 409)
            saved = facts.get('__rows', [])
            if any(i not in {row['id'] for row in saved} for i in ids):
                raise AppError('invalid_rows', '所选行已变化或不属于本事项，先读取核对；原稿保留。')
            selected = [row for row in saved if row['id'] in ids]
            repeated = duplicates(selected, matter['domain'])
            if repeated and (not data['duplicate_acknowledged'] or not data['note'].strip()):
                raise AppError('duplicate_scope_pending', '选中行有同原处相同明细，请排除重复候选或明确累计口径并备注；不自动去重或双计。', 409)
            self._ensure_preparation_sources(connection, matter_id, [row['source_id'] for row in selected])
            journey_context = expense_context(connection,self.store,matter,facts,require_current=True)
            calculation = summarize_rows(matter['domain'], selected)
            expense = matter['domain'] == 'expense'
            dimension = 'currency' if expense else 'unit'
            title = '所选期间费用汇总准备稿' if expense else '所选期间物资汇总准备稿'
            periods = list(dict.fromkeys(row['period'] for row in selected))
            lines = ['# ' + title, '', SCOPE, '事项原目标：' + matter['goal_text'], '基础明细修订：' + str(data['rows_revision']),
                     '本次选中 ' + str(len(selected)) + ' 行／当前已保存 ' + str(len(saved)) + ' 行；范围只含下列选择。',
                     '所选期间（原述，不转换月季口径）：' + '、'.join(periods), '项目／组织及范围补充：' + (data['note'].strip() or '未另提供；沿本事项目标核对，正式项目／组织引用待核。'),
                     '重复口径：' + ('本人明确按所选行累计，保留重复原处及备注。' if repeated else '未发现选中行同原处的完全相同明细；未作跨来源或语义去重。'), '', '## 程序精确分组汇总']
            for group in calculation['groups']:
                label = group['currency'] if expense else group['label'] + '／' + group['unit']
                lines.append('- ' + label + '／' + group['period'] + '：' + group['total'] + (' ' + group['unit'] if not expense else '') + '；' + str(len(group['row_ids'])) + ' 行')
            lines += ['', '## 本次选中明细与原处']
            for row in selected:
                lines += ['- 行 ' + row['id'] + '：' + row['label'] + '／' + row['value'] + ' ' + row[dimension] + '／' + row['period'], '  来源：' + row['source_id'] + '；原位置：' + row['source_position']]
                match = re.fullmatch(r'第([1-9][0-9]*)–([1-9][0-9]*)行', row['source_position'])
                if match:
                    excerpt = self._refs(connection, matter_id, [{'source_id': row['source_id'], 'start_line': int(match[1]), 'end_line': int(match[2])}], require_usable=True)[0]['excerpt']
                    lines.append('  原文节选：' + excerpt[:300] + ('（仅展示前300字，请核原来源全文）' if len(excerpt) > 300 else ''))
                else: lines.append('  原位置为人工引用；无法程序定位行号，原文位置待本人核对。')
            lines += context_lines(journey_context)
            content = '\n'.join(lines)
            if len(content) > 50000: raise AppError('report_limit', '正文超过可编辑范围，请减少所选行或期间；明细与旧稿保留。')
            artifact_id = uid()
            connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)', (artifact_id, 1, matter_id, 'selected_rows_report', title, content, 'structured_draft', matter['revision_no'], now()))
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, 'rows_report_saved', '本人已按明确选择另存期间明细汇总；原明细、人工稿和真实状态保留，未付款或登记收发。')
            result = {'artifact_id': artifact_id, 'rows_revision': data['rows_revision'], 'selected_count': len(selected), 'periods': periods, 'note': data['note'].strip(),
                      'snapshot': {'domain': matter['domain'], 'input_revision': data['rows_revision'], 'rows': selected, 'source_files': facts.get('__source_files', {}), 'journey_context': journey_context}, 'calculation': calculation, 'content': content}
            return self._remember(connection, key, fingerprint, 'rows_report_render', matter_id, result)

    def csv_snapshot(self, matter_id, artifact_id):
        with self.store.connect() as connection:
            self._owned(connection, matter_id)
            report = next((r for r in self._reports(connection, matter_id) if r['artifact_id'] == artifact_id), None)
            if not report: raise AppError('not_found', '本事项的固定选行汇总不存在。', 404)
            return report['snapshot']
