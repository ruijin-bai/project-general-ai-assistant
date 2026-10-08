"""Validate model proposals against the exact source text sent this turn."""

MEETING_INSTRUCTIONS = '''
本事项为会议准备，另返回第四个键meeting_items数组（最多8项，无依据则空数组）。
每项仅含kind,text,responsible_text,deadline_text,refs。kind只可discussion/proposal/decision/dissent/action，保留提议、分歧，不把拟办当已决定。
text为最多2000字的候选原述或整理；responsible_text和deadline_text只保留已有责任和期限原话（无则null），不确认日期、不绑定账号、不登记接受或完成。
refs为1至5处引用，每处只有source_index,start_line,end_line,quote。source_index使用sources中的source_index；行号按该来源text实际换行从1开始，quote必须完全等于起止行的原文（多行以换行连接）。不引用facts或本次生成稿，不编出处。单处最多3000字。
content请简明，完整输出严格JSON，四个键content,candidates,questions,meeting_items，不输出Markdown围栏。'''


def normalize_refs(refs, detail):
    sources = [source for source in detail['sources'] if source.get('source_state', 'usable') == 'usable']
    if not isinstance(refs, list) or not 1 <= len(refs) <= 5:
        raise ValueError('invalid references')
    actual, seen = [], set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {'source_index', 'start_line', 'end_line', 'quote'}:
            raise ValueError('invalid reference shape')
        index, start, end = ref['source_index'], ref['start_line'], ref['end_line']
        if any(type(value) is not int for value in (index, start, end)) or not 1 <= index <= len(sources):
            raise ValueError('invalid source index')
        source = sources[index - 1]
        lines = source['text'].splitlines()
        if not 1 <= start <= end <= len(lines) or not isinstance(ref['quote'], str) or not ref['quote'] or len(ref['quote']) > 3000:
            raise ValueError('invalid source range')
        if ref['quote'] != '\n'.join(lines[start - 1:end]) or (index, start, end) in seen:
            raise ValueError('quote mismatch')
        seen.add((index, start, end))
        actual.append({'source_id': source['id'], 'start_line': start, 'end_line': end})
    return actual


def normalize_meeting_items(items, detail):
    if not isinstance(items, list) or len(items) > 8:
        raise ValueError('invalid meeting candidates')
    result = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {'kind', 'text', 'responsible_text', 'deadline_text', 'refs'}:
            raise ValueError('invalid meeting item')
        if not isinstance(item['kind'], str) or item['kind'] not in {'discussion', 'proposal', 'decision', 'dissent', 'action'}:
            raise ValueError('invalid meeting kind')
        if not isinstance(item['text'], str) or not item['text'].strip() or len(item['text']) > 2000:
            raise ValueError('invalid meeting text')
        for field in ('responsible_text', 'deadline_text'):
            if item[field] is not None and (not isinstance(item[field], str) or not item[field].strip() or len(item[field]) > 2000):
                raise ValueError('invalid attributed text')
        refs = item['refs']
        actual = normalize_refs(refs, detail)
        excerpts = [ref['quote'] for ref in refs]
        if any(item[field] is not None and not any(item[field] in quote for quote in excerpts) for field in ('responsible_text', 'deadline_text')):
            raise ValueError('attribution missing from quotes')
        result.append({'kind': item['kind'], 'text': item['text'], 'responsible_text': item['responsible_text'],
                       'deadline_text': item['deadline_text'], 'refs': actual, 'confirmation': 'candidate',
                       'responsible_account_id': None, 'deadline': {'kind': 'unknown', 'value': None}})
    return result

DOCUMENT_INSTRUCTIONS = """
本事项为公文文字准备，另返回第四个键document_sections数组（最多8节，无依据则空数组）。
每节只有heading,body,refs。heading为最多500字标题，body为最多2000字的可编辑通用草稿；依实际来文要求和事实底稿起草，不补数字、成绩、引语、正式文号、署名、立场、签发或送达结果。未知材料、对象和披露范围明确待核。
refs为1至5处引用，每处只有source_index,start_line,end_line,quote。source_index使用sources中序号；行号按来源text换行从1开始，quote须逐字等于起止行原文，多行以换行连接，单处最多3000字。不引用facts或生成稿，不编出处。
分节全为候选，不确认事实。content简明且标通用草稿。输出严格JSON，四个键content,candidates,questions,document_sections，不输出Markdown围栏。
"""


def normalize_document_sections(sections, detail):
    if not isinstance(sections, list) or len(sections) > 8:
        raise ValueError('invalid document sections')
    result = []
    for section in sections:
        if not isinstance(section, dict) or set(section) != {'heading', 'body', 'refs'}:
            raise ValueError('invalid document section shape')
        for field, limit in (('heading', 500), ('body', 2000)):
            if not isinstance(section[field], str) or not section[field].strip() or len(section[field]) > limit:
                raise ValueError('invalid document section text')
        result.append({'heading': section['heading'], 'body': section['body'], 'refs': normalize_refs(section['refs'], detail), 'confirmation': 'candidate'})
    return result
