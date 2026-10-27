"""Workspace search, local OCR and collections that follow a Google Drive folder."""

from __future__ import annotations

import io
import shutil
import unittest
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx

from .helpers import Harness
from .test_knowledge import POLICY, make_pdf

HAS_OCR = shutil.which("tesseract") is not None


def text_image(lines: list[str], fmt: str = "PNG") -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1400, 140 + 70 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((60, 60 + 70 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


class Search(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()

    def tearDown(self) -> None:
        self.h.close()

    def upload(self, files: dict[str, bytes]) -> dict:
        r = self.h.api("POST", f"/api/w/{self.ws}/documents", files=[("files", (n, b)) for n, b in files.items()])
        self.h.client.portal.call(self.h.engine.knowledge.wait_idle)
        return r

    @unittest.skipUnless(HAS_OCR, "tesseract is not installed")
    def test_ocr_for_images_and_scanned_pdfs(self) -> None:
        h, ws = self.h, self.ws
        scan = text_image(["Gulf Freight LLC", "Invoice 4471", "Total due 18,400.00"], "PDF")
        self.upload({"invoice-scan.pdf": scan, "receipt.png": text_image(["Hotel Riyadh receipt", "Amount 1,250.00"])})
        docs = {d["name"]: d for d in h.api("GET", f"/api/w/{ws}/documents")}
        self.assertEqual(docs["invoice-scan.pdf"]["status"], "indexed", docs["invoice-scan.pdf"].get("error"))
        self.assertEqual(docs["receipt.png"]["status"], "indexed", docs["receipt.png"].get("error"))
        hits = h.api("POST", f"/api/w/{ws}/documents/search", json={"query": "Gulf Freight invoice total due"})
        self.assertEqual(hits[0]["name"], "invoice-scan.pdf")
        self.assertEqual(hits[0]["page"], 1)
        self.assertIn("18,400", hits[0]["text"])
        self.assertIn("1,250", h.api("POST", f"/api/w/{ws}/documents/search", json={"query": "hotel receipt"})[0]["text"])

    def test_everything_search_and_cited_answer(self) -> None:
        h, ws = self.h, self.ws
        self.upload({"policy.docx": POLICY})
        h.llm.script = ["We agreed to accrue the Q3 audit fee of 7,250.50 in September."]
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "How do we treat the audit fee accrual?"})
        h.wait_run(t["id"])
        secret = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.llm.script = ["The audit fee for the CEO's private trust is separate."]
        h.api("POST", f"/api/w/{ws}/threads/{secret['id']}/messages", json={"text": "audit fee for the private trust?"})
        h.wait_run(secret["id"])
        h.engine.db.execute("UPDATE threads SET visibility = 'private', created_by = 'u_someone_else' WHERE id = ?",
                            (secret["id"],))
        h.api("POST", f"/api/w/{ws}/memories", json={"content": "Audit fee accruals are booked to 2300 Accrued expenses.",
                                                     "category": "mapping"})
        h.api("POST", f"/api/w/{ws}/files", json={"title": "Audit fee schedule", "sheets": [{"name": "Fees", "blocks": [
            {"type": "table", "id": "fees", "rows": [["Audit fee", 7250.5]],
             "columns": [{"label": "Item"}, {"label": "Amount", "format": "money"}]}]}]})
        r = h.api("POST", f"/api/w/{ws}/search", json={"query": "audit fee"})["results"]
        self.assertEqual([c["id"] for c in r["conversations"]], [t["id"]])  # the private thread stays hidden
        self.assertIn("«audit» «fee»", r["conversations"][0]["snippet"].lower())
        self.assertEqual(len(r["memory"]), 1)
        self.assertEqual(r["files"][0]["title"], "Audit fee schedule")
        self.assertIn("Audit fee", r["files"][0]["snippet"])
        hotel = h.api("POST", f"/api/w/{ws}/search", json={"query": "hotel per night", "sources": ["documents"]})
        self.assertEqual(list(hotel["results"]), ["documents"])
        self.assertEqual(hotel["results"]["documents"][0]["title"], "policy.docx")
        h.llm.script = ["Audit fee accruals go to 2300 Accrued expenses [1]; the Q3 fee was accrued in September [2]."]
        a = h.api("POST", f"/api/w/{ws}/search", json={"query": "where do audit fee accruals go?", "answer": True})["answer"]
        self.assertIn("[1]", a["text"])
        self.assertEqual([s["kind"] for s in a["sources"]], ["memory", "conversations"])
        self.assertIn("Answer the question using only the numbered sources", h.llm.calls[-1]["messages"][0]["content"])
        self.assertEqual(h.llm.calls[-1].get("tools"), None)  # no tool schemas: a small, cheap call


class DriveFolder(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()
        h = self.h
        h.api("PUT", "/api/oauth-apps/google", json={"client_id": "g-id", "client_secret": "g-secret"})
        url = h.api("POST", f"/api/w/{self.ws}/connections/google/authorize", json={})["url"]
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        h.router.add("POST", "https://oauth2.googleapis.com/token",
                     {"access_token": "ga", "refresh_token": "gr", "expires_in": 3600, "scope": q["scope"]})
        h.router.add("GET", "https://openidconnect.googleapis.com/v1/userinfo", {"email": "finance@acme.test"})
        self.assertEqual(h.client.get("/oauth/callback", params={"code": "c", "state": q["state"]}).status_code, 200)
        self.files = {
            "f1": {"id": "f1", "name": "Expense policy.docx", "mimeType": "application/vnd.openxmlformats-officedocument."
                   "wordprocessingml.document", "modifiedTime": "2026-10-01T10:00:00Z", "size": "100"},
            "f2": {"id": "f2", "name": "Board minutes", "mimeType": "application/vnd.google-apps.document",
                   "modifiedTime": "2026-10-02T10:00:00Z"},
            "f3": {"id": "f3", "name": "logo.svg", "mimeType": "image/svg+xml", "modifiedTime": "2026-10-03T10:00:00Z"}}
        self.content = {"f1": POLICY, "f2": make_pdf(["Board approved the capex budget of 2.4 million."])}
        d = "https://www.googleapis.com/drive/v3/files"

        def files(req: httpx.Request) -> httpx.Response:
            path = req.url.path.rstrip("/")
            if path.endswith("/files"):
                return httpx.Response(200, json={"files": list(self.files.values())})
            fid = path.split("/files/")[1].split("/")[0]
            if fid == "FOLDER123":
                return httpx.Response(200, json={"id": fid, "name": "Finance policies",
                                                 "mimeType": "application/vnd.google-apps.folder"})
            if path.endswith("/export"):
                return httpx.Response(200, content=b"PK-not-used")
            if req.url.params.get("alt") == "media":
                return httpx.Response(200, content=self.content[fid])
            meta = dict(self.files[fid])
            return httpx.Response(200, json=meta)

        h.router.add("GET", d, files)
        # Google Docs are exported as .docx; serve a real one
        h.router.add("GET", f"{d}/f2/export", lambda req: httpx.Response(200, content=self._board_docx()))

    def _board_docx(self) -> bytes:
        from .test_knowledge import make_docx

        return make_docx([("Board", "The board approved the capex budget of 2.4 million.")])

    def tearDown(self) -> None:
        self.h.close()

    def test_follow_folder(self) -> None:
        h, ws = self.h, self.ws
        coll = h.api("POST", f"/api/w/{ws}/collections", json={"name": "Policies"})
        r = h.api("POST", f"/api/w/{ws}/collections/{coll['id']}/drive",
                  json={"folder": "https://drive.google.com/drive/folders/FOLDER123?usp=sharing"})
        self.assertEqual((r["folder_name"], r["added"], r["skipped"]), ("Finance policies", 2, 1))
        h.client.portal.call(h.engine.knowledge.wait_idle)
        names = sorted(d["name"] for d in h.api("GET", f"/api/w/{ws}/documents"))
        self.assertEqual(names, ["Board minutes.docx", "Expense policy.docx"])
        self.assertIn("capex", h.api("POST", f"/api/w/{ws}/documents/search", json={"query": "capex budget"})[0]["text"])
        # nothing changed: nothing re-downloaded
        r = h.api("POST", f"/api/w/{ws}/collections/{coll['id']}/drive/sync", json={})
        self.assertEqual((r["added"], r["updated"], r["removed"]), (0, 0, 0))
        # a changed file is replaced, a removed file is removed
        self.files["f1"]["modifiedTime"] = "2026-10-20T10:00:00Z"
        del self.files["f2"]
        r = h.api("POST", f"/api/w/{ws}/collections/{coll['id']}/drive/sync", json={})
        self.assertEqual((r["updated"], r["removed"]), (1, 1))
        h.client.portal.call(h.engine.knowledge.wait_idle)
        self.assertEqual([d["name"] for d in h.api("GET", f"/api/w/{ws}/documents")], ["Expense policy.docx"])
        coll = next(c for c in h.api("GET", f"/api/w/{ws}/collections") if c["name"] == "Policies")
        self.assertIn("Finance policies", coll["sync_source"])
        h.api("DELETE", f"/api/w/{ws}/collections/{coll['id']}/drive", expect=None)


if __name__ == "__main__":
    unittest.main()
