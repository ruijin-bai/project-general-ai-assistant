"""A bounded workbook -> source -> reviewed calculation smoke, not business data."""
import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from assistant_app.attachments import AttachmentService
from assistant_app.calculations import summarize_rows
from assistant_app.store import AppError, Store, uid
from assistant_app.tabular import TabularService
from assistant_app.xlsx_text import extract_worksheet, NS, DOCREL, REL


def workbook_fixture(domain='expense', rows=None, duplicate=False, external=False):
    headers = ['费用名称', '金额', '币种', '期间'] if domain == 'expense' else ['物品', '数量', '单位', '期间']
    dimension = 'USD' if domain == 'expense' else '件'
    rows = rows or [headers, ['工程测试', '0.1', dimension, '2026-10'], ['工程测试', '0.2', dimension, '2026-10'],
                    ['公式行', ('formula', 'SUM(B2:B3)', '999'), dimension, '2026-10'],
                    ['缺数', '', dimension, '2026-10'], ['格式日期', '1', dimension, ('styled', '46000')]]
    body = []
    for number, values in enumerate(rows, 1):
        cells = []
        for col, value in enumerate(values):
            ref = chr(65 + col) + str(number)
            if isinstance(value, tuple):
                if value[0] == 'formula':
                    cells.append(f'<c r="{ref}"><f>{escape(value[1])}</f><v>{value[2]}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" s="1"><v>{value[1]}</v></c>')
            elif col == 0 and number == 1:
                cells.append(f'<c r="{ref}" t="s"><v>0</v></c>')
            elif col == 1 and number > 1 and value:
                cells.append(f'<c r="{ref}"><v>{escape(value)}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        body.append(f'<row r="{number}">{"".join(cells)}</row>')
    content = BytesIO()
    with ZipFile(content, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
        archive.writestr('_rels/.rels', f'<Relationships xmlns="{REL}"><Relationship Id="r1" Type="{DOCREL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/sharedStrings.xml', f'<sst xmlns="{NS}"><si><t>{escape(rows[0][0])}</t><rPh sb="0" eb="1"><t>拼音注释不并入表头</t></rPh></si></sst>')
        archive.writestr('xl/styles.xml', f'<styleSheet xmlns="{NS}"><fonts count="1"><font/></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>')
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{NS}" xmlns:r="{DOCREL}"><sheets><sheet name="明细" sheetId="1" r:id="r1"/><sheet name="其他表" state="hidden" sheetId="2" r:id="r2"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{REL}"><Relationship Id="r1" Type="{DOCREL}/worksheet" Target="worksheets/sheet1.xml"'+ (' TargetMode="External"' if external else '') + f'/><Relationship Id="r2" Type="{DOCREL}/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        worksheet = f'<worksheet xmlns="{NS}"><sheetData>{"".join(body)}</sheetData></worksheet>'
        archive.writestr('xl/worksheets/sheet1.xml', worksheet)
        archive.writestr('xl/worksheets/sheet2.xml', f'<worksheet xmlns="{NS}"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>未选择表的原文</t></is></c></row></sheetData></worksheet>')
        if duplicate:
            archive.writestr('xl/worksheets/sheet1.xml', worksheet)
    return content.getvalue()


class XlsxTextSmoke(unittest.TestCase):
    def test_selected_literals_formula_limits_and_private_calculation(self):
        raw = workbook_fixture()
        directory = extract_worksheet(raw)
        self.assertTrue(directory['needs_sheet_selection'])
        self.assertEqual(directory['text'], '')
        preview = extract_worksheet(raw, '明细')
        self.assertNotIn('999', preview['text'])
        self.assertNotIn('未选择表的原文', preview['text'])
        self.assertIn('【公式未计算】SUM', preview['text'])
        self.assertIn('【类型／格式值待核】46000', preview['text'])
        inherited = BytesIO()
        with ZipFile(BytesIO(raw)) as original, ZipFile(inherited, 'w', ZIP_DEFLATED) as edited:
            for entry in original.infolist():
                body = original.read(entry)
                if entry.filename == 'xl/worksheets/sheet1.xml':
                    body = body.replace(b'<sheetData>', b'<cols><col min="2" max="2" style="1"/></cols><sheetData>')
                edited.writestr(entry.filename, body)
        self.assertIn('【类型／格式值待核】0.1', extract_worksheet(inherited.getvalue(), '明细')['text'])
        for content, name in ((raw, 'missing'), (workbook_fixture(external=True), '明细'),
                              (workbook_fixture(rows=[['x'*12001]]), '明细'),
                              (workbook_fixture(rows=[['x'] for _ in range(202)]), '明细'), (b'fake Excel', '明细')):
            with self.assertRaises(AppError):
                extract_worksheet(content, name)
        with tempfile.TemporaryDirectory(prefix='qa-xlsx-', dir=Path(__file__).resolve().parent) as folder:
            store = Store(folder)
            store.enable_identity()
            actors = []
            with store.transaction() as connection:
                for name in ('owner', 'other'):
                    actor_id = uid()
                    connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')", (actor_id, name, name))
                    actors.append({'id': actor_id, 'auth_epoch': 1})
            attachments = AttachmentService(store)
            for domain in ('expense', 'inventory'):
                with store.as_actor(actors[0]):
                    matter = store.mutate('create', None, {'domain': domain, 'goal_text': '工程测试Excel输入，未支付或收发'}, uid())
                    matter = store.mutate('control', matter['id'], {'expected_version': matter['version'], 'command': 'pause'}, uid())
                    content = workbook_fixture(domain)
                    uploaded = attachments.upload(matter['id'], {'expected_version': matter['version'], 'filename': '工程明细.xlsx', 'content_base64': base64.b64encode(content).decode()}, uid())
                    aid = uploaded['attachments'][0]['id']
                    before = store.detail(matter['id'])
                    preview = attachments.preview_text(matter['id'], aid, '明细')
                    self.assertEqual(store.detail(matter['id']), before)
                    key = uid()
                    data = {'expected_version': preview['version'], 'sheet_name': '明细', 'text': preview['text']}
                    saved = attachments.save_text(matter['id'], aid, data, key)
                    self.assertEqual(saved['assistant_status'], 'paused')
                    self.assertEqual(attachments.save_text(matter['id'], aid, data, key), saved)
                    self.assertEqual(attachments.download(matter['id'], aid)['content'], content)
                    source = saved['sources'][-1]
                    candidates = TabularService(store).get_candidates(matter['id'], source['id'])
                    self.assertEqual(len(candidates['rows']), 2)
                    self.assertEqual(len(candidates['issues']), 3)
                    self.assertIn('工作表 明细 · 原行 2', candidates['rows'][0]['row']['source_position'])
                    summary = summarize_rows(domain, [item['row'] for item in candidates['rows']])
                    self.assertEqual(summary['groups'][0]['total'], '0.3')
                    with self.assertRaises(AppError):
                        attachments.save_text(matter['id'], aid, {'expected_version': saved['version'], 'text': '无明确工作表'}, uid())
                    with self.assertRaises(AppError) as conflict:
                        attachments.save_text(matter['id'], aid, data, uid())
                    self.assertEqual(conflict.exception.status, 409)
                    reopened = Store(folder)
                    with reopened.as_actor(actors[0]):
                        self.assertEqual(reopened.detail(matter['id'])['sources'][-1]['attachment_text']['sheet_name'], '明细')
                with store.as_actor(actors[1]), self.assertRaises(AppError) as denied:
                    attachments.preview_text(matter['id'], aid, '明细')
                self.assertEqual(denied.exception.status, 404)
