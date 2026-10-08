"""Source-literal travel plan proposals; human confirmation stays in JourneyService."""
import re
from datetime import date, datetime
from .meeting_candidates import normalize_refs

FIELDS = {'destination','departure_at','return_at','participants','purpose','meal_request'}
INSTRUCTIONS = '''
本事项为出行准备，另返回第四键journey_candidates数组（最多6项）。candidates必须为空对象，所有拟填值只放journey_candidates，不自动写事实。
每项只有field,value,refs；field限destination,departure_at,return_at,participants,purpose,meal_request，同字段最多一项。value须为所引原文逐字出现的原值，最多2000字，不补未知、人物、日期、时区或过敏。
refs可单个对象或1至5个对象数组，每处只有source_index,start_line,end_line,quote；序号沿sources.source_index，实际换行从1计，quote逐字等于起止行（多行以换行连接）。值必须逐字见于引用，不从facts或模型稿编引用。
departure_at/return_at只选明确营地计划离营／返营日期，不取航班、实际出返或休假起止；原文须已明示完整ISO年月日或带UTC偏移ISO日期时间。相对时间、缺年、缺时区只问待核，不换算或截短时间；仍是未确认计划，不表示批准可执行。
不选任何已确认值，不覆盖人工文字。目的地、同行、用途和餐次只保留明确原述，不把需求当已派车或已扣餐。content100字内概要，questions最多3条合并缺项；严格四键JSON content,candidates,questions,journey_candidates，不输出围栏。'''


def normalize_journey(items, detail):
    if not isinstance(items,list) or len(items)>6:raise ValueError('invalid journey candidates')
    result=[];seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)!={'field','value','refs'}:raise ValueError('invalid journey candidate shape')
        field,value=item['field'],item['value']
        if not isinstance(field,str) or field not in FIELDS or field in seen:raise ValueError('invalid journey field')
        seen.add(field)
        if not isinstance(value,str) or not value.strip() or value!=value.strip() or len(value)>2000 or value.casefold() in {'unknown','pending','待核','未知'}:raise ValueError('invalid journey value')
        incoming=[item['refs']] if isinstance(item['refs'],dict) else item['refs'];refs=normalize_refs(incoming,detail)
        if not any(value in ref['quote'] for ref in incoming):raise ValueError('journey value missing from quote')
        if field in {'departure_at','return_at'}:
            try:
                if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):date.fromisoformat(value)
                elif re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:Z|[+-][0-9]{2}:[0-9]{2})',value):
                    if datetime.fromisoformat(value).utcoffset() is None:raise ValueError()
                else:raise ValueError()
            except ValueError:raise ValueError('journey date precision pending') from None
            if not any(re.search(r'(?<![0-9A-Za-z_+./:-])'+re.escape(value)+r'(?![0-9A-Za-z_+./:-])',ref['quote']) for ref in incoming):raise ValueError('journey date truncated from quote')
        if detail.get('facts',{}).get(field,{}).get('status')=='confirmed':continue
        result.append({'field':field,'value':value,'refs':refs,'confirmation':'candidate'})
    return result
