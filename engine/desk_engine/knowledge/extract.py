"""Text extraction for the knowledge base. Files are parsed, never executed: PDF (text layer),
Word (.docx), Excel/CSV, plain text and Markdown, HTML, e-mail (.eml) and JSON."""

from __future__ import annotations

import csv
import email
import email.policy
import io
import json
import re
from dataclasses import dataclass

from ..errors import Invalid
from ..files.limits import check_office_zip

MAX_PAGES = 3000
OCR_BUDGET_SECONDS = 15 * 60   # OCR of one scanned PDF stops after this long; pages past it are left empty
MAX_CHARS = 5_000_000

MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".eml": "message/rfc822",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass
class Section:
    text: str
    page: int | None = None
    heading: str | None = None


def extension(filename: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]+)$", filename.lower())
    return m.group(1) if m else ""


def mime_for(filename: str) -> str:
    ext = extension(filename)
    if ext not in MIME:
        raise Invalid("Supported documents: PDF, Word (.docx), Excel (.xlsx), CSV, text, Markdown, HTML, e-mail "
                      "(.eml), JSON, and images (PNG, JPEG, TIFF; read through PipesHub). Save older formats "
                      "(.doc, .xls) in the newer format first.",
                      code="unsupported_document")
    return MIME[ext]


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r", "")
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract(data: bytes, filename: str) -> list[Section]:
    ext = extension(filename)
    mime_for(filename)
    if ext in IMAGES:
        from . import ocr

        if not ocr.available():
            raise Invalid("Images have no text layer and OCR is not installed here. Install Tesseract, or connect PipesHub "
                          "(Connections → Knowledge) to read them.", code="needs_ocr")
        try:
            text = ocr.image_text(data)
        except Exception as exc:
            raise Invalid("This image could not be read.", code="unreadable_document") from exc
        if not text.strip():
            raise Invalid("No text was found in this image.", code="empty_document")
        return [Section(_clean(text), page=1, heading="OCR")]
    if ext == ".pdf":
        sections = _pdf(data)
    elif ext == ".docx":
        sections = _docx(data)
    elif ext in (".xlsx", ".xlsm"):
        sections = _xlsx(data)
    elif ext == ".csv":
        sections = _csv(data)
    elif ext in (".html", ".htm"):
        from ..connectors.google import html_to_text

        sections = [Section(html_to_text(_decode(data)))]
    elif ext == ".eml":
        sections = _eml(data)
    elif ext == ".json":
        try:
            sections = [Section(json.dumps(json.loads(_decode(data)), indent=1, ensure_ascii=False))]
        except json.JSONDecodeError as exc:
            raise Invalid("The JSON file is not valid.", code="unreadable_document") from exc
    else:
        sections = _markdown(_decode(data))
    out = [Section(_clean(s.text), s.page, s.heading) for s in sections]
    out = [s for s in out if s.text]
    total = sum(len(s.text) for s in out)
    if total > MAX_CHARS:
        raise Invalid("The document has more than 5 million characters of text. Split it into smaller files.",
                      code="document_too_large")
    return out


def _pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise Invalid("The PDF is password-protected. Remove the password and upload it again.",
                              code="encrypted_document") from exc
        if len(reader.pages) > MAX_PAGES:
            raise Invalid(f"The PDF has more than {MAX_PAGES} pages.", code="document_too_large")
        sections = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # one damaged page should not lose the rest
                text = ""
            sections.append(Section(text, page=i))
    except PdfReadError as exc:
        raise Invalid("This file could not be read as a PDF.", code="unreadable_document") from exc
    if sections and not any(s.text.strip() for s in sections):
        from . import ocr

        if not ocr.available():
            raise Invalid("This PDF has no text layer (it is probably a scan) and OCR is not installed here. Install "
                          "Tesseract, connect PipesHub, or upload a searchable PDF.", code="needs_ocr")
        import time

        ocr_sections = []
        deadline = time.monotonic() + OCR_BUDGET_SECONDS
        for i, images in enumerate(ocr.pdf_page_images(data), start=1):
            if time.monotonic() > deadline:
                ocr_sections.append(Section("", page=i, heading="OCR"))   # the rest is skipped; the file is still indexed
                continue
            text = "\n".join(ocr.image_text(img) for img in images[:4])
            ocr_sections.append(Section(text, page=i, heading="OCR"))
        if not any(s.text.strip() for s in ocr_sections):
            raise Invalid("This PDF has no text layer and OCR found no text in it.", code="needs_ocr")
        return ocr_sections
    return sections


def _docx(data: bytes) -> list[Section]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    check_office_zip(data, what="document")
    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise Invalid("This file could not be read as a Word document.", code="unreadable_document") from exc
    sections: list[Section] = []
    heading: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if buf:
            sections.append(Section("\n".join(buf), heading=heading))
            buf.clear()

    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            style = (para.style.name if para.style is not None else "") or ""
            text = para.text.strip()
            if not text:
                continue
            if style.lower().startswith(("heading", "title")):
                flush()
                heading = text[:200]
            else:
                buf.append(text)
        elif tag == "tbl":
            table = Table(child, doc)
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    t = cell.text.strip().replace("\n", " ")
                    if not cells or cells[-1] != t:  # merged cells repeat their text
                        cells.append(t)
                buf.append(" | ".join(cells))
    flush()
    return sections


def _xlsx(data: bytes) -> list[Section]:
    from openpyxl import load_workbook

    check_office_zip(data, what="workbook")
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise Invalid("This file could not be read as an Excel workbook.", code="unreadable_document") from exc
    sections = []
    for ws in wb.worksheets:
        lines = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 20000:
                break
            cells = ["" if v is None else str(v) for v in row]
            while cells and cells[-1] == "":
                cells.pop()
            if any(cells):
                lines.append(" | ".join(cells))
        if lines:
            sections.append(Section("\n".join(lines), heading=f"Sheet: {ws.title}"))
    wb.close()
    return sections


def _csv(data: bytes) -> list[Section]:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    lines = [" | ".join(r) for i, r in enumerate(csv.reader(io.StringIO(text), dialect)) if i < 50000 and any(r)]
    return [Section("\n".join(lines))]


def _eml(data: bytes) -> list[Section]:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    head = "\n".join(f"{k}: {msg.get(k)}" for k in ("From", "To", "Date", "Subject") if msg.get(k))
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        content = body.get_content()
        if body.get_content_type() == "text/html":
            from ..connectors.google import html_to_text

            content = html_to_text(content)
        text = content
    attachments = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    if attachments:
        text += "\n\nAttachments (not indexed): " + ", ".join(attachments)
    return [Section(f"{head}\n\n{text}", heading=str(msg.get("Subject") or "")[:200] or None)]


def _markdown(text: str) -> list[Section]:
    sections: list[Section] = []
    heading: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.+)$", line)
        if m:
            if buf:
                sections.append(Section("\n".join(buf), heading=heading))
                buf = []
            heading = m.group(1).strip()[:200]
        else:
            buf.append(line)
    if buf:
        sections.append(Section("\n".join(buf), heading=heading))
    return sections
