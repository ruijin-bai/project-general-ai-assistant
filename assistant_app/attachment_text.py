"""Bounded local text candidates; no file execution, OCR or external access."""
from io import BytesIO
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET
from zlib import error as ZlibError

from .store import AppError

TEXT_LIMIT = 12000
XML_LIMIT = 4 * 1024 * 1024
WORD_NAMESPACES = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main", "http://purl.oclc.org/ooxml/wordprocessingml/main"}


def extract_text(content, declared_format):
    if declared_format in {"txt", "md", "csv", "tsv"}:
        try:
            text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError:
            raise AppError("unreadable_attachment", "仅支持明确UTF-8文字；原件保留，请人工提供可读正文。") from None
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            raise AppError("unreadable_attachment", "文件含非文字控制内容，未作为正文读取。")
        if not text.strip():
            raise AppError("empty_attachment_text", "原件未读到非空文字。")
        return {"text": text[:TEXT_LIMIT], "truncated": len(text) > TEXT_LIMIT, "positions": [], "unread": [], "method": "utf8_text"}
    if declared_format != "docx":
        raise AppError("unsupported_text_format", "当前只读取DOCX正文或UTF-8文字；PDF、图片、XLSX与扫描内容仍未解析。")
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or any(entry.flag_bits & 1 for entry in entries):
                raise ValueError("complex/encrypted archive")
            main = [entry for entry in entries if entry.filename == "word/document.xml"]
            if len(main) != 1 or not 0 < main[0].file_size <= XML_LIMIT:
                raise ValueError("missing/duplicate/oversized main document")
            xml = archive.read(main[0])
            decoded_xml = xml.decode("utf-8-sig")
            if len(xml) > XML_LIMIT or "\x00" in decoded_xml or "<!DOCTYPE" in decoded_xml.upper() or "<!ENTITY" in decoded_xml.upper():
                raise ValueError("unsupported XML declaration")
            omitted = [entry.filename for entry in entries if entry.filename.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes", "word/comments", "word/media/"))]
        root = ET.fromstring(decoded_xml)
        namespace = root.tag.partition("}")[0].lstrip("{")
        if namespace not in WORD_NAMESPACES or root.tag != "{" + namespace + "}document":
            raise ValueError("not a supported Word document")
        prefix = "{" + namespace + "}"
        body = root.find(prefix + "body")
        if body is None:
            raise ValueError("missing document body")
        if any(node.tag in {prefix + name for name in ("ins", "del", "moveFrom", "moveTo")} for node in body.iter()):
            raise AppError("unreviewed_word_changes", "正文含修订痕迹，未合并成事实；请先人工核对或提供已确认的正文。")
        skipped = {prefix + name for name in ("drawing", "pict", "object", "txbxContent", "altChunk")}
        has_skipped = any(node.tag in skipped for node in body.iter())
        def paragraphs(node):
            if node.tag in skipped:
                return
            if node.tag == prefix + "p":
                yield node
                return
            for child in node:
                yield from paragraphs(child)
        def paragraph_text(node):
            if node.tag in skipped:
                return ""
            if node.tag == prefix + "t":
                return node.text or ""
            if node.tag == prefix + "tab":
                return "\t"
            if node.tag in {prefix + "br", prefix + "cr"}:
                return "\n"
            return "".join(paragraph_text(child) for child in node)
        lines, positions, length, truncated = [], [], 0, False
        for number, paragraph in enumerate(paragraphs(body), 1):
            if number > 500:
                truncated = True
                break
            text = paragraph_text(paragraph)
            if not text.strip():
                continue
            room = TEXT_LIMIT - length - (1 if lines else 0)
            if room <= 0:
                truncated = True
                break
            clipped = text[:room]
            start = sum(item.count("\n") + 1 for item in lines) + 1
            positions.append({"start_line": start, "end_line": start + clipped.count("\n"), "position": "Word正文段落 " + str(number)})
            lines.append(clipped)
            length += len(clipped) + (1 if len(lines) > 1 else 0)
            if len(clipped) < len(text):
                truncated = True
                break
        text = "\n".join(lines)
        if not text.strip():
            raise AppError("empty_attachment_text", "DOCX正文未读到非空文字；可能为扫描、图片或非正文内容，原件保留。")
        unread = ["分页、版式、表格合并关系、域指令和作者真实性未核"]
        if omitted:
            unread.append("页眉页脚、脚注尾注、批注或图片附件未读取")
        if has_skipped:
            unread.append("正文图片、绘图、文本框、对象或外嵌内容未读取")
        return {"text": text, "truncated": truncated, "positions": positions, "unread": unread, "method": "word_body_paragraphs"}
    except AppError:
        raise
    except (BadZipFile, ValueError, ET.ParseError, RuntimeError, OSError, RecursionError, EOFError, NotImplementedError, UnicodeError, ZlibError):
        raise AppError("unreadable_attachment", "DOCX正文不可读、结构重复、加密或超读取范围；原件保留，可人工提供正文。") from None
