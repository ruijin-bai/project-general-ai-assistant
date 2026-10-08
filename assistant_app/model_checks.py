"""Source-linked requirement proposals, never employment or travel decisions."""
from .meeting_candidates import normalize_refs

INSTRUCTIONS = '''
本事项为人事材料核对，另返回第四键check_candidates数组（最多8项，无真实要求依据则空数组）。
每项只有requirement_text,requirement_refs,provided_refs,note。requirement_text逐字摘取来源中明确要求或本人本次核对目标，不编岗位条件、资格标准、证明要求、国籍推断、工资、录用或处罚结论。note最多500字，明确是材料匹配候选而非合规或聘用决定。
requirement_refs须1至5处真实要求引用；provided_refs为已有材料的匹配候选，无材料则空数组，不能以要求本身作已提供材料，不因材料提及名称就称满足要求。每个引用可为单个对象或有限数组，只有source_index,start_line,end_line,quote；行号按sources.text实际换行从1开始，quote逐字等于所引行，不引用生成稿或facts，不编出处。requirement_text须见于至少一处要求摘录。所有条目的本地核对状态由程序保持pending，由本人核对后另行保存；不返回check或正式状态字段。
content只写100字以内概要，不重复完整原文或字段表；candidates无明确依据用空对象，不填未知；questions至多3条，合并缺项。总输出尽量在1800字符内，完整闭合严格JSON，只有content,candidates,questions,check_candidates四键，不输出围栏或工具调用。
'''


def instructions(domain):
    if domain != 'leave':
        return INSTRUCTIONS
    return INSTRUCTIONS.replace('本事项为人事材料核对', '本事项为签证休假返岗要求与材料核对').replace(
        '不编岗位条件、资格标准、证明要求、国籍推断、工资、录用或处罚结论',
        '只摘取本人提供的具体要求，不编护照签证要求、国内证明正式名称、NIS适用人员／申请窗口／更正规则或休假批准条件；不以国籍推断适用，不生成证明、卡、签发代码或通行结论').replace(
        '而非合规或聘用决定', '而非正式适用、签发、休假批准或可通行决定') + '\n不计算或判断日期先后、有效期是否覆盖或证件是否过期；note只说明匹配原文与缺项，日期计算另由程序按人工确认值办理。\n'


def leave_note(provided_refs):
    return ('所给材料原文的匹配候选；日期先后、正式适用和效力待核，未判断签发或可通行。' if provided_refs else
            '未匹配已有材料来源，保持待补；正式适用及办理口径未知，不生成证明或卡。')


def _refs(value, detail, empty=False):
    if empty and (value is None or value == []):
        return []
    return normalize_refs([value] if isinstance(value, dict) else value, detail)


def normalize_checks(items, detail):
    if not isinstance(items, list) or len(items) > 8:
        raise ValueError('invalid check candidates')
    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'requirement_text', 'requirement_refs', 'provided_refs', 'note'}:
            raise ValueError('invalid check candidate shape')
        text = item['requirement_text']
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError('invalid requirement text')
        refs = _refs(item['requirement_refs'], detail)
        originals = [item['requirement_refs']] if isinstance(item['requirement_refs'], dict) else item['requirement_refs']
        if not any(text in ref['quote'] for ref in originals):
            raise ValueError('requirement missing from quote')
        provided = _refs(item['provided_refs'], detail, empty=True)
        if detail.get('domain') == 'leave' and any(a['source_id'] == b['source_id'] and max(a['start_line'], b['start_line']) <= min(a['end_line'], b['end_line']) for a in refs for b in provided):
            raise ValueError('requirement cannot be provided material')
        note = '' if item['note'] is None else item['note']
        if not isinstance(note, str) or len(note) > 1000:
            raise ValueError('invalid check candidate note')
        key = (text, tuple((ref['source_id'], ref['start_line'], ref['end_line']) for ref in refs))
        if key in seen:
            raise ValueError('duplicate check candidate')
        seen.add(key)
        normalized = {'requirement_text': text, 'requirement_refs': refs, 'provided_refs': provided, 'note': note, 'check': 'pending'}
        if detail.get('domain') == 'leave':
            normalized.update(note=leave_note(provided), model_note=note)
        result.append(normalized)
    return result
