"""Civil date arithmetic from saved human-confirmed values, never validity rules."""
import hashlib
from datetime import date, timedelta, timezone
from .journey import DATE_RE, LABELS, JourneyService, _instant
from .store import AppError, dump

SCOPE = '程序仅比较所录材料日期与本人确认的休假／离返日期；日历日差不判断证件有效、过期、最低有效期、签发、休假获准或可通行。'
ENDPOINTS = (('leave_start', '休假开始'), ('leave_end', '休假结束'), ('departure_at', '离营计划'), ('return_at', '返营计划'))


def _civil(value, offset):
    if isinstance(value, str) and DATE_RE.fullmatch(value):
        return date.fromisoformat(value), '明示年月日'
    instant = _instant(value)
    if not offset:
        raise ValueError('共同日期比较UTC偏移未明确，不能截断日期时间')
    sign = 1 if offset[0] == '+' else -1
    zone = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:])))
    return instant.astimezone(zone).date(), '按本人明确UTC偏移 ' + offset + ' 换算比较日期'


def compare_material_dates(store, connection, matter, facts, candidates):
    service = JourneyService(store)
    try:
        effective, semantics, link_stale = service._effective(connection, matter, facts)
    except AppError:
        effective, semantics, link_stale = {}, service._meta(facts)['plan_semantics'], True
    linked_source_blocked = bool(service._meta(facts)['travel_link'] and any(item.get('state') == 'withheld' for item in effective.get('__source_states', {}).get('items', {}).values()))
    rows = []
    for item in candidates:
        for field, label in ENDPOINTS:
            own = field in {'leave_start', 'leave_end'}
            entry = (facts if own else effective).get(field, {})
            row = {'kind': item['kind'], 'material_label': item['label'], 'material_date': item['date'],
                   'field': field, 'endpoint_label': label, 'endpoint_value': entry.get('value'), 'endpoint_status': entry.get('status', 'unknown'),
                   'source_id': item['source_id'], 'source_position': item['source_position'], 'excerpt': item['excerpt'],
                   'comparison_date': None, 'calendar_days': None, 'relation': None, 'status': 'pending', 'reason': None, 'formal_validity': 'unknown'}
            if not item['eligible']:
                row['reason'] = '材料日期、可用原处或本人核对待补'
            elif not own and link_stale:
                row['reason'] = '关联出行修订已变化，先重读并明确关联修订'
            elif not own and linked_source_blocked:
                row['reason'] = '关联行程来源暂不用，不作新的日期比较'
            elif entry.get('status') != 'confirmed' or not entry.get('value'):
                row['reason'] = '此端点日期未由本人确认，不使用模型候选'
            else:
                try:
                    endpoint, conversion = _civil(entry['value'], None if own else semantics.get('comparison_offset'))
                    material = date.fromisoformat(item['date'])
                    delta = (material - endpoint).days
                    row.update(status='computed', comparison_date=endpoint.isoformat(), calendar_days=delta,
                               relation='after' if delta > 0 else 'before' if delta < 0 else 'same_day', conversion=conversion)
                except (AppError, ValueError, TypeError, IndexError):
                    row['reason'] = '年月日、日期时间或共同日期比较偏移待核；不补默认时区'
            rows.append(row)
    result = {'items': rows, 'computed_count': sum(row['status'] == 'computed' for row in rows),
              'pending_count': sum(row['status'] == 'pending' for row in rows), 'scope': SCOPE,
              'comparison_offset': semantics.get('comparison_offset'), 'link_stale': link_stale}
    result['report_token'] = hashlib.sha256(dump(result).encode()).hexdigest()
    return result


def comparison_text(row):
    if row['status'] != 'computed':
        return row['endpoint_label'] + '：待核 — ' + row['reason']
    relation = {'after': '之后', 'before': '之前', 'same_day': '同日'}[row['relation']]
    delta = '' if row['calendar_days'] == 0 else str(abs(row['calendar_days'])) + '个日历日'
    return (row['endpoint_label'] + '：材料日期 ' + row['material_date'] + ' 在比较日期 ' + row['comparison_date'] + ' ' + relation + delta +
            '；原值 ' + row['endpoint_value'] + '；' + row['conversion'] + '。仅日期比较，具体时点及材料效力待核。')
