"""Literal source evidence for model-proposed rows; arithmetic stays in calculations."""
import re

from .calculations import CalculationError, validate_rows
from .meeting_candidates import normalize_refs


def instructions(domain):
    dimension = 'currency' if domain == 'expense' else 'unit'
    return f'''
本事项为费用／物资核对，另返回第四键row_candidates数组（最多8行，无完整依据则空数组）。
本轮以明细提取为主：content仅写100字以内概要，不重复完整来源、字段表或逐行金额；questions至多3条并合并缺项。candidates没有明确依据用空对象，不填“未知”。总输出尽量短，控制在1800字符内，完整闭合四键JSON；不反复列出相同引用。
每行只有label,value,{dimension},period,refs。label为原费用或物品名称；value为原文已明示十进制字符串，不能计算、换算、补0或提取合计行。{dimension}和period必须逐字使用原文明确币种／单位和期间，不猜日期、汇率、含税或库存基线，不归一改写。
每行refs可为单个引用对象或1至5处引用对象数组，每处只有source_index,start_line,end_line,quote；来源序号用sources.source_index，行号按来源text实际换行从1开始，quote逐字等于起止行原文（多行以换行连接，最多3000字）。上述四个值都必须逐字见于本行的至少一处完整引用，数值不能取其他数字的子串；不要拆成四处零碎字段引用。
无法定位或缺项只列questions，不写成完整明细。相同原位置不重复，不按同值跨来源去重，不汇总任何数值。content简明；全部候选不确认、不计算、不批准支付或登记收发。输出严格JSON四键content,candidates,questions,row_candidates，不输出Markdown围栏。'''


def normalize_rows(rows, detail):
    domain = detail['domain']
    dimension = 'currency' if domain == 'expense' else 'unit'
    if not isinstance(rows, list) or len(rows) > 8:
        raise ValueError('invalid model rows')
    result, seen = [], set()
    for item in rows:
        if not isinstance(item, dict) or set(item) != {'label', 'value', dimension, 'period', 'refs'}:
            raise ValueError('invalid model row shape')
        incoming_refs = [item['refs']] if isinstance(item['refs'], dict) else item['refs']
        refs = normalize_refs(incoming_refs, detail)
        if any(not isinstance(item[key], str) or not item[key].strip() or item[key] != item[key].strip() for key in ('label', 'value', dimension, 'period')):
            raise ValueError('row value missing from quote')
        matching = [(actual, incoming['quote']) for actual, incoming in zip(refs, incoming_refs) if all(item[key] in incoming['quote'] for key in ('label', 'value', dimension, 'period'))]
        if not matching:
            raise ValueError('row value missing from quote')
        matching = [(ref, quote) for ref, quote in matching if re.search(r'(?<![0-9A-Za-z_.,+\-/:])' + re.escape(item['value']) + r'(?![0-9A-Za-z_.,+\-/:])', quote)]
        if not matching:
            raise ValueError('row number missing from quote')
        ref = matching[0][0]
        signature = (ref['source_id'], ref['start_line'], ref['end_line'], item['label'], item['value'], item[dimension], item['period'])
        if signature in seen:
            raise ValueError('duplicate model row')
        seen.add(signature)
        row = {key: item[key] for key in ('label', 'value', dimension, 'period')}
        row.update(id='validated-row', source_id=ref['source_id'], source_position=f"第{ref['start_line']}–{ref['end_line']}行")
        try:
            validate_rows(domain, [row], detail['sources'])
        except CalculationError:
            raise ValueError('invalid model row value') from None
        row.pop('id')
        result.append({**row, 'refs': refs, 'confirmation': 'candidate'})
    return result
