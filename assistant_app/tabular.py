"""Read-only literal CSV/TSV candidates from an owner-provided source."""

import csv
import hashlib
import io
import json

from .calculations import CalculationError, validate_rows
from .store import AppError, string
from .structured import StructuredService

SCOPE = "本人明确表头原文的本地CSV/TSV候选；须明确放入可编辑草稿并另行人工核对，不代表确认、批准、真实收发、计算或Excel同步"
HEADERS = {
    "expense": {"label": "label", "value": "value", "currency": "currency", "period": "period", "费用名称": "label", "金额": "value", "币种": "currency", "期间": "period"},
    "inventory": {"label": "label", "value": "value", "unit": "unit", "period": "period", "物品": "label", "数量": "value", "单位": "unit", "期间": "period"},
}


class TabularService(StructuredService):
    def get_candidates(self, matter_id, source_id):
        source_id = string(source_id, "来源ID", 80)
        with self.store.connect() as connection:
            matter = self.store._matter(connection, matter_id)
            if matter["domain"] not in HEADERS:
                raise AppError("invalid_domain", "本表格候选接口仅支持费用或物资事项。")
            _, matter, facts = self._owner(connection, matter_id, matter["domain"])
            source = connection.execute("SELECT id,text FROM sources WHERE id=? AND matter_id=?", (source_id, matter_id)).fetchone()
            if not source:
                raise AppError("not_found", "本事项来源不存在。", 404)
            self._ensure_preparation_sources(connection, matter_id, [source_id])
            result = self._parse(matter, dict(source))
            origin = facts.get('__attachment_text', {}).get(source_id, {})
            if origin.get('sheet_name') is not None and not origin.get('human_edited'):
                for item in result['rows']:
                    position = next((entry for entry in origin.get('positions', []) if entry['start_line'] == item['start_line'] and entry['end_line'] == item['end_line']), None)
                    if position:
                        item['row']['source_position'] = position['position'] + ' · ' + item['row']['source_position']
            return result

    def _parse(self, matter, source):
        result = {"id": matter["id"], "version": matter["version"], "revision_no": matter["revision_no"], "source_id": source["id"], "columns": [], "rows": [], "issues": [], "scope": SCOPE}
        text = source["text"]
        lines = text.splitlines(keepends=True)

        def excerpt(start, end):
            return "".join(lines[start - 1:end]).rstrip("\r\n")

        def issue(kind, reason, start, end):
            result["issues"].append({"kind": kind, "reason": reason, "start_line": start, "end_line": end, "excerpt": excerpt(start, end) if start is not None else ""})

        if any(separator in text for separator in ("\v", "\f", "\x85", "\u2028", "\u2029")):
            issue("unsupported_line_break", "原文含非CSV标准行分隔符，未猜测行位置；请明确原文格式后再取候选。", 1, len(lines))
            return result
        base = next((index for index, line in enumerate(lines) if line.strip()), None)
        if base is None:
            issue("empty_source", "未提供非空表头和记录，不将未知当无费用或零数量。", None, None)
            return result
        delimiter = "\t" if "\t" in lines[base] else ","
        reader = csv.reader(io.StringIO("".join(lines[base:]), newline=""), delimiter=delimiter, strict=True)
        try:
            header = next(reader)
        except (csv.Error, StopIteration):
            issue("invalid_header", "表头CSV/TSV结构无效，未映射任何列。", base + 1, max(base + 1, base + reader.line_num))
            return result
        header_end = base + reader.line_num
        labels = [value.strip() for value in header]
        mapping = HEADERS[matter["domain"]]
        columns = [mapping.get(label) for label in labels]
        dimension = "currency" if matter["domain"] == "expense" else "unit"
        if len(columns) != 4 or any(column is None for column in columns) or len(set(columns)) != 4 or set(columns) != {"label", "value", dimension, "period"}:
            issue("invalid_header", "须恰好四个明确字段列；缺项、重复、额外或未知表头均不猜映射，当前未读数据行。", base + 1, header_end)
            return result
        result["columns"] = columns
        records, previous_line = 0, reader.line_num
        while True:
            start = base + previous_line + 1
            try:
                values = next(reader)
            except StopIteration:
                break
            except csv.Error:
                issue("csv_syntax", "此处CSV/TSV引号或记录结构错误；以下原文未继续提取，不隐式修正。", start, len(lines))
                break
            end = base + reader.line_num
            previous_line = reader.line_num
            raw = excerpt(start, end)
            if not raw.strip():
                continue
            records += 1
            if records > 200:
                result["rows"] = []
                issue("record_limit", "非空数据记录超过200项（含无效记录）；未返回截断候选，请明确本次来源范围。", header_end + 1, len(lines))
                break
            if len(values) != 4:
                issue("invalid_record", "此记录列数与明确四列表头不一致，未生成候选。", start, end)
                continue
            stable_input = json.dumps([source["id"], start, end, raw], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            row = dict(zip(columns, values))
            if any(marker in value for value in values for marker in ('【公式未计算】', '【Excel错误待核】', '【类型／格式值待核】')):
                issue('unreviewed_excel_cell', '此记录含公式、错误或类型／格式待核值，整行不取明细候选；不采用缓存或补数。请先人工核对实际值。', start, end)
                continue
            row.update(id="import-" + hashlib.sha256(stable_input).hexdigest()[:32], source_id=source["id"], source_position="第" + str(start) + "–" + str(end) + "行")
            try:
                row = validate_rows(matter["domain"], [row], [{"id": source["id"]}])[0]
            except CalculationError as error:
                issue("invalid_record", str(error) + " 当前原文保留，未填零或替代未知值。", start, end)
                continue
            result["rows"].append({"row": row, "start_line": start, "end_line": end, "excerpt": raw})
        return result
