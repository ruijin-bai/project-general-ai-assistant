"""One explicitly selected worksheet, literal values only, no Excel runtime."""
import csv
from io import BytesIO, StringIO
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile
from zlib import error as ZlibError

from .store import AppError

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
DOCREL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PREFIX = '{' + NS + '}'
LIMIT = 4 * 1024 * 1024
CELL = re.compile(r'([A-Z]{1,3})([1-9][0-9]{0,6})')
FORMULA_MARKER = '【公式未计算】'


def _literal_string(node):
    # Phonetic annotations are separate from the literal cell text.
    parts = []
    for child in node:
        if child.tag == PREFIX + 't':
            parts.append(child.text or '')
        elif child.tag == PREFIX + 'r':
            parts.extend(text.text or '' for text in child.findall(PREFIX + 't'))
    return ''.join(parts)


def extract_worksheet(content, sheet_name=None):
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or any(entry.flag_bits & 1 for entry in entries):
                raise ValueError('complex/encrypted workbook')
            def xml(path, expected):
                matching = [entry for entry in entries if entry.filename == path]
                if len(matching) != 1 or not 0 < matching[0].file_size <= LIMIT:
                    raise ValueError('missing/duplicate/oversized part')
                raw = archive.read(matching[0])
                text = raw.decode('utf-8-sig')
                if len(raw) > LIMIT or '\x00' in text or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
                    raise ValueError('unsafe XML')
                node = ET.fromstring(text)
                if node.tag != expected:
                    raise ValueError('unsupported part namespace')
                return node
            workbook = xml('xl/workbook.xml', PREFIX + 'workbook')
            relationships = xml('xl/_rels/workbook.xml.rels', '{' + REL + '}Relationships')
            targets = {}
            for relation in relationships:
                rid = relation.get('Id')
                if rid in targets:
                    raise ValueError('duplicate relationship')
                targets[rid] = relation
            sheets, paths = [], {}
            for sheet in workbook.findall(PREFIX + 'sheets/' + PREFIX + 'sheet'):
                name = sheet.get('name')
                if not name or len(name) > 200 or name in paths:
                    raise ValueError('invalid worksheet name')
                relation = targets.get(sheet.get('{' + DOCREL + '}id'))
                if relation is None or relation.get('TargetMode') == 'External' or relation.get('Type') != DOCREL + '/worksheet':
                    raise ValueError('not a local worksheet')
                target = relation.get('Target', '')
                path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
                if not path.startswith('xl/') or '\\' in target or ':' in target or '?' in target or '#' in target:
                    raise ValueError('invalid worksheet path')
                paths[name] = path
                sheets.append({'name': name, 'state': sheet.get('state', 'visible')})
            if not sheets or len(sheets) > 100:
                raise ValueError('worksheet count')
            base = {'worksheets': sheets, 'sheet_name': sheet_name, 'method': 'xlsx_literal_worksheet', 'truncated': False,
                    'unread': ['仅当前明确工作表；其他工作表、图表、批注、外部链接和显示格式未读取。公式不计算，不采用缓存值；带格式数值须人工核对。']}
            if sheet_name is None:
                return {**base, 'text': '', 'positions': [], 'needs_sheet_selection': True}
            if not isinstance(sheet_name, str) or sheet_name not in paths:
                raise AppError('invalid_sheet', '请选择本原件实际存在的一个工作表。')
            worksheet = xml(paths[sheet_name], PREFIX + 'worksheet')
            strings = []
            if any(entry.filename == 'xl/sharedStrings.xml' for entry in entries):
                shared = xml('xl/sharedStrings.xml', PREFIX + 'sst')
                for item in shared.findall(PREFIX + 'si'):
                    strings.append(_literal_string(item))
            if worksheet.find(PREFIX + 'mergeCells') is not None:
                base['unread'].append('存在合并单元格；未向空格填充值或猜测列标题。')
            styled_columns = []
            for column in worksheet.findall(PREFIX + 'cols/' + PREFIX + 'col'):
                if column.get('style', '0') != '0':
                    start_col, end_col = int(column.get('min', '0')), int(column.get('max', '0'))
                    if not 1 <= start_col <= end_col <= 16384:
                        raise ValueError('invalid styled column')
                    styled_columns.append((start_col, end_col))
            output = StringIO(newline='')
            writer = csv.writer(output, delimiter='\t', lineterminator='\n')
            positions, rows_seen, cells_seen, nonempty, line = [], set(), set(), 0, 1
            for row in worksheet.findall(PREFIX + 'sheetData/' + PREFIX + 'row'):
                row_no = row.get('r', '')
                if not row_no.isascii() or not row_no.isdecimal() or not 0 < int(row_no) <= 1048576 or int(row_no) in rows_seen:
                    raise ValueError('invalid row position')
                row_no = int(row_no)
                if rows_seen and row_no <= max(rows_seen):
                    raise ValueError('unordered rows')
                rows_seen.add(row_no)
                if len(rows_seen) > 1000:
                    raise ValueError('row limit')
                values, last_col = {}, 0
                for cell in row.findall(PREFIX + 'c'):
                    ref = cell.get('r', '')
                    match = CELL.fullmatch(ref)
                    if not match or int(match[2]) != row_no or ref in cells_seen:
                        raise ValueError('invalid cell position')
                    cells_seen.add(ref)
                    if len(cells_seen) > 4000:
                        raise ValueError('cell limit')
                    col = 0
                    for letter in match[1]:
                        col = col * 26 + ord(letter) - 64
                    if col > 64 or col <= last_col:
                        raise ValueError('column limit/order')
                    last_col = col
                    formula = cell.find(PREFIX + 'f')
                    value = cell.findtext(PREFIX + 'v', '')
                    kind = cell.get('t', 'n')
                    if formula is not None:
                        value = FORMULA_MARKER + (formula.text or '共享／数组公式')
                    elif kind == 's':
                        if not value.isascii() or not value.isdecimal() or int(value) >= len(strings):
                            raise ValueError('invalid shared string')
                        value = strings[int(value)]
                    elif kind == 'inlineStr':
                        inline = cell.find(PREFIX + 'is')
                        value = _literal_string(inline) if inline is not None else ''
                    elif kind == 'e':
                        value = '【Excel错误待核】' + value
                    elif kind not in {'n', 'str', 'b', 'd'}:
                        raise ValueError('unsupported cell type')
                    elif value and kind in {'n', 'b', 'd'} and (kind != 'n' or cell.get('s', row.get('s', '1' if any(start <= col <= end for start, end in styled_columns) else '0')) != '0'):
                        value = '【类型／格式值待核】' + value
                    values[col] = value
                if not any(value.strip() for value in values.values()):
                    continue
                nonempty += 1
                if nonempty > 201:
                    raise AppError('worksheet_limit', '所选表超过201个非空行，未返回部分明细；请先明确本次文件范围。')
                row_values = [values.get(col, '') for col in range(1, max(values) + 1)]
                start = output.tell()
                writer.writerow(row_values)
                rendered = output.getvalue()[start:]
                end_line = line + rendered.count('\n') - 1
                positions.append({'start_line': line, 'end_line': end_line, 'position': f'工作表 {sheet_name} · 原行 {row_no}'})
                line = end_line + 1
                if output.tell() > 12000:
                    raise AppError('worksheet_limit', '所选表文字超过12000字，未截断数值或返回部分明细；请先明确文件范围。')
            if not positions:
                raise AppError('empty_attachment_text', '所选工作表没有非空文字，不推定没有费用或库存。')
            return {**base, 'text': output.getvalue().rstrip('\n'), 'positions': positions, 'needs_sheet_selection': False}
    except AppError:
        raise
    except (BadZipFile, ValueError, ET.ParseError, RuntimeError, OSError, RecursionError, EOFError, NotImplementedError, UnicodeError, ZlibError):
        raise AppError('unreadable_attachment', 'XLSX结构、位置或读取范围不可核，原件保留；可人工提供四列表格文字继续。') from None
