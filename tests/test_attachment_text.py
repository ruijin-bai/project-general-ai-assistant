"""One original -> candidate -> private source contract and parser risk checks."""
import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from assistant_app.attachment_text import extract_text, XML_LIMIT
from assistant_app.attachments import AttachmentService
from assistant_app.model import ModelClient
from assistant_app.store import AppError, Store, dump, uid


def word_fixture(body, extra=None):
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        if extra:
            archive.writestr(*extra)
    return output.getvalue()


class AttachmentTextSmoke(unittest.TestCase):
    def test_private_docx_preview_transfer_history_and_untrusted_limits(self):
        original = word_fixture('<w:p><w:r><w:t>工程会议原文，非真实会议</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>议题：工程检查</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:pict><w:txbxContent><w:p><w:r><w:t>文本框未读取标记</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>', ("word/header1.xml", "页眉不读取标记"))
        candidate = extract_text(original, "docx")
        self.assertEqual(candidate["text"], "工程会议原文，非真实会议\n议题：工程检查")
        self.assertEqual(candidate["positions"][1]["position"], "Word正文段落 2")
        self.assertTrue(candidate["unread"])
        self.assertNotIn("文本框未读取标记", candidate["text"])
        self.assertNotIn("页眉不读取标记", candidate["text"])
        for content, declared_format in ((word_fixture('<w:ins><w:p><w:r><w:t>未确认修订</w:t></w:r></w:p></w:ins>'), "docx"),
                                         (b"fake Word", "docx"), (b"%PDF original", "pdf"), (b"\xff", "txt"),
                                         (word_fixture('<w:p><w:r><w:t>' + "x" * XML_LIMIT + '</w:t></w:r></w:p>'), "docx")):
            with self.assertRaises(AppError):
                extract_text(content, declared_format)
        self.assertTrue(extract_text(word_fixture('<w:p><w:r><w:t>' + "字" * 12001 + '</w:t></w:r></w:p>'), "docx")["truncated"])
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix="qa-attachment-text-", dir=Path(__file__).resolve().parent) as directory:
                store, actors = Store(directory), {"owner": None, "other": None}
                if protected:
                    store.enable_identity()
                    store.enable_services()
                    with store.transaction() as connection:
                        for name in actors:
                            actor_id = uid()
                            connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')", (actor_id, name, name))
                            actors[name] = {"id": actor_id, "auth_epoch": 1}
                service = AttachmentService(store)
                with store.as_actor(actors["owner"]):
                    matter = store.mutate("create", None, {"goal_text": "工程演练：整理本人会议原件", "domain": "meeting"}, uid())
                    matter = store.mutate("manual_artifact", matter["id"], {"expected_version": matter["version"], "input_revision": matter["revision_no"], "edited_content": "已保存人工稿不可覆盖"}, uid())
                    matter = store.mutate("control", matter["id"], {"expected_version": matter["version"], "command": "pause"}, uid())
                    uploaded = service.upload(matter["id"], {"expected_version": matter["version"], "filename": "工程会议原件.docx", "content_base64": base64.b64encode(original).decode()}, uid())
                    attachment_id = uploaded["attachments"][0]["id"]
                    before = store.detail(matter["id"])
                    preview = service.preview_text(matter["id"], attachment_id)
                    self.assertEqual(store.detail(matter["id"]), before)
                    self.assertNotIn(candidate["text"], [s["text"] for s in ModelClient().preview_input(before)["input"]["sources"]])
                    key, data = uid(), {"expected_version": preview["version"], "text": preview["text"]}
                    saved = service.save_text(matter["id"], attachment_id, data, key)
                    self.assertEqual(saved["assistant_status"], "paused")
                    self.assertEqual(saved["facts"], before["facts"])
                    self.assertEqual(saved["artifacts"][0]["content"], "已保存人工稿不可覆盖")
                    self.assertEqual(saved["sources"][-1]["attachment_text"]["sha256"], preview["sha256"])
                    self.assertEqual(saved["sources"][-1]["attachment_text"]["positions"], preview["positions"])
                    self.assertEqual(service.download(matter["id"], attachment_id)["content"], original)
                    self.assertEqual(service.save_text(matter["id"], attachment_id, data, key), saved)
                    with self.assertRaises(AppError) as conflict:
                        service.save_text(matter["id"], attachment_id, data, uid())
                    self.assertEqual(conflict.exception.status, 409)
                    edited = service.save_text(matter["id"], attachment_id, {"expected_version": saved["version"], "text": preview["text"] + "\n本人补充：未外发"}, uid())
                    self.assertTrue(edited["sources"][-1]["attachment_text"]["human_edited"])
                    self.assertEqual(edited["sources"][-1]["attachment_text"]["positions"], [])
                    self.assertEqual(edited["sources"][-2]["text"], preview["text"])
                    reopened = Store(directory)
                    with reopened.as_actor(actors["owner"]):
                        self.assertEqual(reopened.detail(matter["id"])["sources"][-1]["text"], edited["sources"][-1]["text"])
                if protected:
                    with store.as_actor(actors["other"]), self.assertRaises(AppError) as denied:
                        service.preview_text(matter["id"], attachment_id)
                    self.assertEqual(denied.exception.status, 404)
                    with store.transaction() as connection:
                        connection.execute("UPDATE accounts SET auth_epoch=auth_epoch+1 WHERE id=?", (actors["owner"]["id"],))
                    with store.as_actor(actors["owner"]), self.assertRaises(AppError) as stale:
                        service.save_text(matter["id"], attachment_id, data, key)
                    self.assertEqual(stale.exception.status, 401)
