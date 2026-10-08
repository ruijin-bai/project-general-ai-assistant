"""Private unparsed original BLOBs, separate from readable sources and models."""

import base64
import binascii
import hashlib
import sqlite3
import unicodedata
from contextlib import closing

from .store import AppError, dump, now, string, uid
from .structured import StructuredService, _strict


FILE_LIMIT = 2 * 1024 * 1024
MATTER_LIMIT = 20 * 1024 * 1024
FORMATS = {"txt", "md", "csv", "tsv", "pdf", "docx", "xlsx", "jpg", "jpeg", "png"}
TABLE = "matter_attachments_v1"
ATTACHMENT_SCHEMA = """CREATE TABLE matter_attachments_v1(
 id TEXT PRIMARY KEY NOT NULL,
 matter_id TEXT NOT NULL REFERENCES matters(id),
 filename TEXT NOT NULL, declared_format TEXT NOT NULL,
 byte_count INTEGER NOT NULL, sha256 TEXT NOT NULL,
 uploaded_by TEXT NOT NULL, uploaded_at TEXT NOT NULL, content BLOB NOT NULL
)"""
SCOPE = "本人保存的原始附件；保存本身不解析或OCR，不自动进入模型、搜索或分享。可明确读取支持格式的正文候选，核对转存后才成为独立文字来源；扩展名声明及文字读取不代表证据核真、批准或外部送达。"
METADATA = ("id", "filename", "declared_format", "byte_count", "sha256", "uploaded_by", "uploaded_at")
EXPECTED_COLUMNS = [("id", "TEXT", 1, 1), ("matter_id", "TEXT", 1, 0), ("filename", "TEXT", 1, 0),
                    ("declared_format", "TEXT", 1, 0), ("byte_count", "INTEGER", 1, 0), ("sha256", "TEXT", 1, 0),
                    ("uploaded_by", "TEXT", 1, 0), ("uploaded_at", "TEXT", 1, 0), ("content", "BLOB", 1, 0)]


class AttachmentService(StructuredService):
    def preview_text(self, matter_id, attachment_id, sheet_name=None):
        from .attachment_text import extract_text
        original = self.download(matter_id, attachment_id)
        if original["declared_format"] == 'xlsx':
            from .xlsx_text import extract_worksheet
            result = extract_worksheet(original['content'], sheet_name)
        else:
            if sheet_name is not None:
                raise AppError('invalid_sheet', '只有XLSX原件可指定工作表。')
            result = extract_text(original["content"], original["declared_format"])
        # Read again after parsing, so stale identity/ownership cannot receive text.
        with self.store.connect() as connection:
            actor, matter, _ = self._owned(connection, matter_id)
            if actor["id"] != original["uploaded_by"]:
                raise AppError("not_found", "原件当前不可读取。", 404)
            return {**result, "id": matter_id, "version": matter["version"], "input_revision": matter["revision_no"],
                    "attachment_id": attachment_id, "filename": original["filename"], "sha256": original["sha256"], "declared_format": original["declared_format"],
                    "scope": "本人明确读取的本地正文候选；未保存为文字来源、未发送模型或分享，不等于事实确认。正文之外未读取部分与截断明示。"}

    def save_text(self, matter_id, attachment_id, data, key):
        _strict(data, {"expected_version", "text"}, {'sheet_name'})
        text = string(data["text"], "本人核对的正文片段", 12000)
        preview = self.preview_text(matter_id, attachment_id, data.get('sheet_name'))
        if preview.get('needs_sheet_selection'):
            raise AppError('invalid_sheet', '须先明确选择并读取一个工作表，再核对转存。')
        with self.store.transaction() as connection:
            actor, matter, facts = self._owned(connection, matter_id)
            row = connection.execute("SELECT id FROM matter_attachments_v1 WHERE id=? AND matter_id=? AND uploaded_by=?", (attachment_id, matter_id, actor["id"])).fetchone()
            if not row:
                raise AppError("not_found", "本人原件当前不可转存正文。", 404)
            key, fingerprint, previous = self._request(connection, actor, "attachment_text:" + attachment_id, matter_id, data, key)
            if previous is not None:
                return self.store._detail(connection, matter_id)
            self.store._check_version(matter, data)
            source_id = self.store._source(connection, matter_id, "user_text", text, None)
            edited = text != preview["text"]
            origin = {"attachment_id": attachment_id, "filename": preview["filename"], "sha256": preview["sha256"], "declared_format": preview["declared_format"],
                      "method": preview["method"], "truncated": preview["truncated"], "unread": preview["unread"], "human_edited": edited,
                      "positions": [] if edited else preview["positions"], "saved_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                      "scope": "本人转存的文字候选；原件不变，未确认事实。" + ("文字已人工修改，对原件位置须重新核对。" if edited else "位置仅对应已读原件片段，不代表完整文档核验。")}
            if preview.get('sheet_name') is not None:
                origin['sheet_name'] = preview['sheet_name']
            facts.setdefault("__attachment_text", {})[source_id] = origin
            revision, stamp = matter["revision_no"] + 1, now()
            connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), stamp, actor["id"]))
            self.store._cancel(connection, matter_id, "本人核对并转存附件文字，停止旧输入准备；原件和人工稿保留")
            status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
            self.store._bump(connection, matter_id, revision_no=revision, preparation_status="needs_review", assistant_status=status)
            self.store._event(connection, matter_id, "attachment_text_saved", dump({"source_id": source_id, **origin}))
            self._remember(connection, key, fingerprint, "attachment_text", matter_id, {"source_id": source_id})
            self.store._continue(connection, matter_id)
            return self.store._detail(connection, matter_id)

    def _owned(self, connection, matter_id):
        matter = self.store._matter(connection, matter_id)
        return self._owner(connection, matter_id, matter["domain"])

    def _schema(self, connection):
        entry = connection.execute("SELECT type FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()
        if not entry:
            return False
        columns = connection.execute("PRAGMA table_xinfo(matter_attachments_v1)").fetchall()
        actual = [(row["name"], row["type"], row["notnull"], row["pk"]) for row in columns]
        foreign_keys = connection.execute("PRAGMA foreign_key_list(matter_attachments_v1)").fetchall()
        if (entry["type"] != "table" or actual != EXPECTED_COLUMNS or any(row["dflt_value"] is not None or row["hidden"] != 0 for row in columns)
                or len(foreign_keys) != 1 or foreign_keys[0]["table"] != "matters" or foreign_keys[0]["from"] != "matter_id" or foreign_keys[0]["to"] != "id"
                or foreign_keys[0]["on_update"] != "NO ACTION" or foreign_keys[0]["on_delete"] != "NO ACTION"):
            raise AppError("attachment_schema_conflict", "原始附件表结构不兼容，已保留原文件，请核查后再试。", 409)
        return True

    def _view(self, connection, actor, matter):
        attachments = []
        if self._schema(connection):
            rows = connection.execute("SELECT id,filename,declared_format,byte_count,sha256,uploaded_by,uploaded_at FROM matter_attachments_v1 WHERE matter_id=? AND uploaded_by=? ORDER BY uploaded_at,id", (matter["id"], actor["id"]))
            attachments = [{field: row[field] for field in METADATA} for row in rows]
        return {"id": matter["id"], "version": matter["version"], "input_revision": matter["revision_no"],
                "attachments": attachments, "scope": SCOPE}

    def get_attachments(self, matter_id):
        with self.store.connect() as connection:
            actor, matter, _ = self._owned(connection, matter_id)
            return self._view(connection, actor, matter)

    def _input(self, data):
        _strict(data, {"expected_version", "filename", "content_base64"})
        if type(data["expected_version"]) is not int or data["expected_version"] < 1:
            raise AppError("invalid_version", "附件上传须有明确正整数事项版本。")
        filename = data["filename"]
        if (not isinstance(filename, str) or not filename or filename != filename.strip() or len(filename) > 200
                or filename in {".", ".."} or any(char in filename for char in '/\\:<>"|?*')
                or filename.endswith(".") or any(unicodedata.category(char).startswith("C") for char in filename)):
            raise AppError("invalid_filename", "文件名须为无路径、控制字符的安全名称，最多200字符。")
        parts = filename.rsplit(".", 1)
        if len(parts) != 2 or not parts[0] or parts[1].lower() not in FORMATS:
            raise AppError("invalid_format", "仅允许声明为txt/md/csv/tsv/pdf/docx/xlsx/jpg/jpeg/png的原件。")
        encoded = data["content_base64"]
        if not isinstance(encoded, str):
            raise AppError("invalid_base64", "原件内容须为严格Base64文字。")
        if len(encoded) > 4 * ((FILE_LIMIT + 2) // 3):
            raise AppError("attachment_limit", "每件原始附件最多2MiB。", 413)
        try:
            content = base64.b64decode(encoded, validate=True)
            if base64.b64encode(content).decode("ascii") != encoded:
                raise ValueError()
        except (ValueError, binascii.Error):
            raise AppError("invalid_base64", "原件Base64无效，不能容忍空白、非标准编码或补猜字节。") from None
        if len(content) > FILE_LIMIT:
            raise AppError("attachment_limit", "每件原始附件最多2MiB。", 413)
        return filename, parts[1].lower(), content, hashlib.sha256(content).hexdigest()

    def _preflight(self, connection, matter_id, data, filename, content, digest, key):
        actor, matter, facts = self._owned(connection, matter_id)
        table_exists = self._schema(connection)
        # Only the verified hash and size enter the request fingerprint, never the bytes/Base64.
        request = {"expected_version": data["expected_version"], "filename": filename, "byte_count": len(content), "sha256": digest}
        key, fingerprint, previous = self._request(connection, actor, "attachment_upload", matter_id, request, key)
        if previous is not None:
            attachment_id = previous.get("attachment_id")
            if not table_exists or not connection.execute("SELECT id FROM matter_attachments_v1 WHERE id=? AND matter_id=? AND uploaded_by=?", (attachment_id, matter_id, actor["id"])).fetchone():
                raise AppError("not_found", "本人原始附件不存在或不可访问。", 404)
            return actor, matter, facts, key, fingerprint, self._view(connection, actor, matter), table_exists
        self.store._check_version(matter, data)
        total = connection.execute("SELECT COALESCE(SUM(byte_count),0) FROM matter_attachments_v1 WHERE matter_id=?", (matter_id,)).fetchone()[0] if table_exists else 0
        if total + len(content) > MATTER_LIMIT:
            raise AppError("attachment_limit", "本事项原始附件合计最多20MiB，不自动删除已有原件。", 413)
        return actor, matter, facts, key, fingerprint, None, table_exists

    def upload(self, matter_id, data, key):
        filename, declared_format, content, digest = self._input(data)
        # The instance lock covers first-write validation, a consistent pre-DDL backup,
        # and the subsequent transaction. Fresh ownership is checked again inside it.
        with self.store.lock:
            with self.store.connect() as connection:
                checked = self._preflight(connection, matter_id, data, filename, content, digest, key)
                if checked[5] is not None:
                    return checked[5]
                if not checked[6]:
                    backup_dir = self.store.path.parent / "backups"
                    backup_dir.mkdir(exist_ok=True)
                    with closing(sqlite3.connect(backup_dir / ("before-attachments-" + uid() + ".sqlite3"))) as backup:
                        connection.backup(backup)
            with self.store.transaction() as connection:
                actor, matter, facts, key, fingerprint, previous, table_exists = self._preflight(connection, matter_id, data, filename, content, digest, key)
                if previous is not None:
                    return previous
                if not table_exists:
                    connection.execute(ATTACHMENT_SCHEMA)
                attachment_id, stamp = uid(), now()
                connection.execute("INSERT INTO matter_attachments_v1 VALUES(?,?,?,?,?,?,?,?,?)",
                                   (attachment_id, matter_id, filename, declared_format, len(content), digest, actor["id"], stamp, sqlite3.Binary(content)))
                revision = matter["revision_no"] + 1
                # Copy the exact stored JSON, without introducing filename/BLOB/model facts.
                original = connection.execute("SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?", (matter_id, matter["revision_no"])).fetchone()[0]
                connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, revision, original, stamp, actor["id"]))
                self.store._cancel(connection, matter_id, "本人新增原始附件，旧输入修订的准备停止；附件不自动解析。")
                status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
                self.store._bump(connection, matter_id, revision_no=revision, assistant_status=status, preparation_status="needs_review")
                self.store._event(connection, matter_id, "attachment_uploaded", dump({"attachment_id": attachment_id,
                    "filename": filename, "declared_format": declared_format, "byte_count": len(content), "sha256": digest,
                    "actor_display_name": actor["display_name"], "scope": SCOPE}))
                self._remember(connection, key, fingerprint, "attachment_upload", matter_id, {"attachment_id": attachment_id})
                return self._view(connection, actor, self.store._matter(connection, matter_id))

    def download(self, matter_id, attachment_id):
        with self.store.connect() as connection:
            actor, _, _ = self._owned(connection, matter_id)
            if not self._schema(connection):
                raise AppError("not_found", "本人原始附件不存在。", 404)
            if not isinstance(attachment_id, str) or len(attachment_id) > 80:
                raise AppError("not_found", "本人原始附件不存在。", 404)
            row = connection.execute("SELECT * FROM matter_attachments_v1 WHERE id=? AND matter_id=? AND uploaded_by=?", (attachment_id, matter_id, actor["id"])).fetchone()
            if not row:
                raise AppError("not_found", "本人原始附件不存在或不可访问。", 404)
            return {"content": bytes(row["content"]), **{field: row[field] for field in METADATA}, "scope": SCOPE}
