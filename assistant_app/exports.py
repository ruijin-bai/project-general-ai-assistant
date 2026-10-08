"""Editable OpenXML text documents, without third-party runtime dependencies."""

from io import BytesIO, StringIO
import csv
import re
import zipfile
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
ET.register_namespace('w', W)


def rows_csv_bytes(snapshot):
    """Export fixed saved rows as UTF-8 text; neutralize spreadsheet formulas."""
    expense = snapshot['domain'] == 'expense'
    dimension = 'currency' if expense else 'unit'
    stream = StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['费用名称' if expense else '物品', '金额' if expense else '数量',
                     '币种' if expense else '单位', '期间', '来源ID', '来源位置', '原文件名', '明细修订', '状态说明'])
    def cell(value):
        text = str(value)
        leading = re.sub(r'^[\s\x00-\x1f\ufeff]*', '', text)
        return "'" + text if leading.startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text
    for row in snapshot['rows']:
        writer.writerow([cell(row['label']), row['value'], cell(row[dimension]), cell(row['period']),
                         cell(row['source_id']), cell(row['source_position']), cell(snapshot['source_files'].get(row['source_id'], {}).get('name', '')),
                         snapshot['input_revision'], '本地人工核对；未批准、支付或真实收发'])
    return stream.getvalue().encode('utf-8-sig')


def _xml(element):
    return ET.tostring(element, encoding='utf-8', xml_declaration=True)


def _word(parent, name, attributes=None, text=None):
    node = ET.SubElement(parent, '{' + W + '}' + name,
                         {'{' + W + '}' + key: value for key, value in (attributes or {}).items()})
    if text is not None:
        node.text = text
    return node


def docx_bytes(content):
    """Keep all saved text in editable paragraphs; only heading marks are styled.

    This is a preparation document, not an official template or signature. Tables
    and other Markdown stay as their actual saved text instead of guessed layout.
    """
    document = ET.Element('{' + W + '}document')
    body = _word(document, 'body')
    for line in content.split('\n'):
        line = line.rstrip('\r')
        line = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '\ufffd', line)
        paragraph = _word(body, 'p')
        heading = re.match(r'^(#{1,3}) (.+)$', line)
        if heading:
            properties = _word(paragraph, 'pPr')
            _word(properties, 'pStyle', {'val': 'Heading' + str(len(heading[1]))})
            line = heading[2]
        text = _word(_word(paragraph, 'r'), 't', text=line)
        text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    section = _word(body, 'sectPr')
    _word(section, 'pgSz', {'w': '11906', 'h': '16838'})
    _word(section, 'pgMar', {'top': '1440', 'right': '1440', 'bottom': '1440', 'left': '1440'})

    styles = ET.Element('{' + W + '}styles')
    for name, size in [('Normal', '22'), ('Heading1', '32'), ('Heading2', '28'), ('Heading3', '24')]:
        style = _word(styles, 'style', {'type': 'paragraph', 'styleId': name, **({'default': '1'} if name == 'Normal' else {})})
        _word(style, 'name', {'val': name})
        if name != 'Normal':
            _word(style, 'basedOn', {'val': 'Normal'})
        run = _word(style, 'rPr')
        _word(run, 'rFonts', {'ascii': 'Calibri', 'hAnsi': 'Calibri', 'eastAsia': '宋体'})
        _word(run, 'sz', {'val': size})
        if name != 'Normal':
            _word(run, 'b')

    types = ET.Element('Types', xmlns=CT)
    ET.SubElement(types, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    ET.SubElement(types, 'Default', Extension='xml', ContentType='application/xml')
    ET.SubElement(types, 'Override', PartName='/word/document.xml', ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml')
    ET.SubElement(types, 'Override', PartName='/word/styles.xml', ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml')
    root_rels = ET.Element('Relationships', xmlns=REL)
    ET.SubElement(root_rels, 'Relationship', Id='rId1', Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument', Target='word/document.xml')
    doc_rels = ET.Element('Relationships', xmlns=REL)
    ET.SubElement(doc_rels, 'Relationship', Id='rId1', Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles', Target='styles.xml')
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
        for path, value in [('[Content_Types].xml', types), ('_rels/.rels', root_rels),
                            ('word/document.xml', document), ('word/styles.xml', styles), ('word/_rels/document.xml.rels', doc_rels)]:
            package.writestr(path, _xml(value))
    return output.getvalue()
