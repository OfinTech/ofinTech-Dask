"""Google: Gmail (search, read, drafts, send), Drive and Sheets (import spreadsheets as datasets)."""

from __future__ import annotations

import base64
import html
import re
from email.message import EmailMessage
from email.utils import getaddresses
from typing import TYPE_CHECKING, Any

from ..errors import Invalid
from .accounting import HttpBase

if TYPE_CHECKING:
    from ..app import Engine

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE = "https://www.googleapis.com/drive/v3"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SPREADSHEET_MIMES = {SHEET_MIME, XLSX_MIME, "text/csv", "application/vnd.ms-excel.sheet.macroEnabled.12"}


def html_to_text(raw: str) -> str:
    """Plain-text rendering of an HTML e-mail body. The app never renders e-mail HTML."""
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def parse_message(msg: dict[str, Any]) -> dict[str, Any]:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    plain: list[str] = []
    htmls: list[str] = []
    attachments: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        if part.get("filename"):
            attachments.append({"filename": part["filename"], "mime_type": mime,
                                "attachment_id": body.get("attachmentId"), "size": body.get("size", 0)})
        elif mime == "text/plain" and body.get("data"):
            plain.append(_b64(body["data"]).decode("utf-8", errors="replace"))
        elif mime == "text/html" and body.get("data"):
            htmls.append(_b64(body["data"]).decode("utf-8", errors="replace"))
        for p in part.get("parts", []) or []:
            walk(p)

    walk(msg.get("payload", {}))
    text = "\n".join(plain).strip() or html_to_text("\n".join(htmls))
    return {"id": msg.get("id"), "thread_id": msg.get("threadId"), "from": headers.get("from"),
            "to": headers.get("to"), "cc": headers.get("cc"), "subject": headers.get("subject"),
            "date": headers.get("date"), "snippet": html.unescape(msg.get("snippet", "")), "body": text[:20000],
            "attachments": attachments, "labels": msg.get("labelIds", [])}


def build_raw_email(*, sender: str | None, to: str, subject: str, body: str, cc: str | None = None,
                    in_reply_to: str | None = None) -> str:
    for field_value in (to, cc or ""):
        for _, addr in getaddresses([field_value]):
            if addr and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", addr):
                raise Invalid(f"'{addr}' is not a valid e-mail address.")
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject.replace("\n", " ")[:250]
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


class GoogleClient(HttpBase):
    label = "Google"

    def __init__(self, engine: Engine, connection: dict[str, Any]) -> None:
        # every Gmail and Drive call passes here: tools, workflow e-mail steps, e-mail triggers, Drive collections
        from ..egress import workspace_sealed
        if workspace_sealed(engine, connection.get("workspace_id")):
            from ..errors import Forbidden
            raise Forbidden("This workspace keeps its data inside the organization, so Google services are off.",
                            code="egress_blocked")
        super().__init__(engine, connection)

    async def post(self, url: str, json_body: dict[str, Any]) -> Any:
        import httpx2 as httpx

        from ..errors import UpstreamError

        for attempt in (0, 1):
            token = await self.tokens.access_token(force_refresh=attempt == 1)
            async with httpx.AsyncClient(timeout=60, transport=self.engine.http_transport) as client:
                resp = await client.post(url, json=json_body, headers=self.auth_header(token))
            if resp.status_code == 401 and attempt == 0:
                continue
            if resp.status_code >= 400:
                raise UpstreamError(f"Google returned HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise UpstreamError("Google rejected the access token. Reconnect it.", code="reconnect")

    # Gmail
    async def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        data = await self.get(f"{GMAIL}/messages", {"q": query, "maxResults": max(1, min(max_results, 25))})
        out = []
        for ref in data.get("messages", []):
            m = await self.get(f"{GMAIL}/messages/{ref['id']}", {"format": "metadata",
                                                                  "metadataHeaders": ["From", "Subject", "Date"]})
            parsed = parse_message(m)
            parsed.pop("body", None)
            out.append(parsed)
        return out

    async def read(self, message_id: str) -> dict[str, Any]:
        if not re.match(r"^[A-Za-z0-9_-]+$", message_id):
            raise Invalid("Bad message id.")
        return parse_message(await self.get(f"{GMAIL}/messages/{message_id}", {"format": "full"}))

    async def attachment(self, message_id: str, attachment_id: str) -> bytes:
        data = await self.get(f"{GMAIL}/messages/{message_id}/attachments/{attachment_id}")
        return _b64(data.get("data", ""))

    async def create_draft(self, raw: str, thread_id: str | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        return await self.post(f"{GMAIL}/drafts", {"message": message})

    async def send(self, raw: str, thread_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        return await self.post(f"{GMAIL}/messages/send", body)

    # Drive / Sheets
    async def find_spreadsheets(self, name_contains: str = "", max_results: int = 20) -> list[dict[str, Any]]:
        mimes = " or ".join(f"mimeType = '{m}'" for m in sorted(SPREADSHEET_MIMES))
        q = f"({mimes}) and trashed = false"
        if name_contains:
            safe = name_contains.replace("\\", "\\\\").replace("'", "\\'")
            q += f" and name contains '{safe}'"
        data = await self.get(f"{DRIVE}/files", {"q": q, "pageSize": max(1, min(max_results, 50)),
                                                 "fields": "files(id,name,mimeType,modifiedTime,owners(displayName))",
                                                 "orderBy": "modifiedTime desc"})
        return data.get("files", [])

    async def list_folder(self, folder_id: str) -> list[dict[str, Any]]:
        """Files directly inside a Drive folder (not trashed), newest first, up to 1,000."""
        if not re.match(r"^[A-Za-z0-9_-]+$", folder_id):
            raise Invalid("Bad folder id.")
        out: list[dict[str, Any]] = []
        token = None
        while len(out) < 1000:
            params = {"q": f"'{folder_id}' in parents and trashed = false", "pageSize": 200,
                      "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)", "orderBy": "modifiedTime desc"}
            if token:
                params["pageToken"] = token
            data = await self.get(f"{DRIVE}/files", params)
            out += data.get("files", [])
            token = data.get("nextPageToken")
            if not token:
                break
        return out

    async def folder_name(self, folder_id: str) -> str:
        if not re.match(r"^[A-Za-z0-9_-]+$", folder_id):
            raise Invalid("Bad folder id.")
        meta = await self.get(f"{DRIVE}/files/{folder_id}", {"fields": "id,name,mimeType"})
        if meta.get("mimeType") != "application/vnd.google-apps.folder":
            raise Invalid("That Drive link is not a folder.")
        return str(meta.get("name") or folder_id)

    async def download_spreadsheet(self, file_id: str) -> tuple[bytes, str]:
        if not re.match(r"^[A-Za-z0-9_-]+$", file_id):
            raise Invalid("Bad file id.")
        meta = await self.get(f"{DRIVE}/files/{file_id}", {"fields": "id,name,mimeType,size"})
        mime = meta.get("mimeType")
        if mime == SHEET_MIME:
            content = await self.get(f"{DRIVE}/files/{file_id}/export", {"mimeType": XLSX_MIME}, raw=True)
            return content, meta["name"] + ".xlsx"
        if mime not in SPREADSHEET_MIMES:
            raise Invalid("Only Google Sheets, Excel (.xlsx/.xlsm) and CSV files can be imported.")
        if int(meta.get("size") or 0) > self.engine.settings.max_upload_mb * 1024 * 1024:
            raise Invalid("The file is larger than the upload limit.")
        content = await self.get(f"{DRIVE}/files/{file_id}", {"alt": "media"}, raw=True)
        name = meta["name"]
        if mime == "text/csv" and not name.lower().endswith(".csv"):
            name += ".csv"
        return content, name

    async def download_document(self, file_id: str) -> tuple[bytes, str]:
        """PDF, Word, text files; Google Docs are exported as .docx."""
        if not re.match(r"^[A-Za-z0-9_-]+$", file_id):
            raise Invalid("Bad file id.")
        meta = await self.get(f"{DRIVE}/files/{file_id}", {"fields": "id,name,mimeType,size"})
        mime = meta.get("mimeType", "")
        name = meta["name"]
        if mime == "application/vnd.google-apps.document":
            content = await self.get(f"{DRIVE}/files/{file_id}/export", {"mimeType": DOCX_MIME}, raw=True)
            return content, name + ".docx"
        if mime.startswith("application/vnd.google-apps"):
            raise Invalid("Only Google Docs can be exported to the knowledge base; download other Google files first.")
        if int(meta.get("size") or 0) > 50 * 1024 * 1024:
            raise Invalid("The file is larger than 50 MB.")
        return await self.get(f"{DRIVE}/files/{file_id}", {"alt": "media"}, raw=True), name
