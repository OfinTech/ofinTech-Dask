import hashlib
import io
import json
import re
import time
import unittest

import httpx2 as httpx
import numpy as np

from desk_engine.knowledge.base import chunk_sections, fts_query
from desk_engine.knowledge.extract import Section, extract
from tests.helpers import FakeLLM, Harness


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one line of Helvetica text per page (no external libraries)."""
    objs: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_id = 3 + 2 * n
    for i, text in enumerate(pages):
        stream = f"BT /F1 11 Tf 50 750 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(paragraphs: list[tuple[str, str]], table: list[list[str]] | None = None) -> bytes:
    import docx

    d = docx.Document()
    for style, text in paragraphs:
        if style == "h":
            d.add_heading(text, level=1)
        else:
            d.add_paragraph(text)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, val in enumerate(row):
                t.cell(r, c).text = val
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def fake_embedding(text: str) -> list[float]:
    """Deterministic bag-of-words vectors; 'lease' and 'rental' share a dimension to imitate semantics."""
    vec = np.zeros(64, dtype=np.float32)
    for w in re.findall(r"[a-z]+", text.lower()):
        w = {"rental": "lease", "rent": "lease", "leasing": "lease"}.get(w, w)
        vec[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
    return vec.tolist()


POLICY = make_docx([
    ("h", "Travel and expenses"),
    ("p", "Hotels may cost at most 180 per night in the base currency. Economy class is required for flights under six hours."),
    ("h", "Approvals"),
    ("p", "Expenses above 1,000 need approval from the finance manager before payment."),
], table=[["Category", "Limit"], ["Meals", "60 per day"], ["Taxi", "45 per trip"]])

CONTRACT = make_pdf([
    "Master services agreement between Harbour Logistics and Northwind Freight.",
    "Payment terms: invoices are payable within 45 days of receipt. Late payments accrue interest at 1.5 percent monthly.",
    "Term: the agreement runs for 36 months and renews automatically unless terminated with 90 days notice.",
])


class Units(unittest.TestCase):
    def test_extract_pdf_docx_and_more(self) -> None:
        pdf = extract(CONTRACT, "msa.pdf")
        self.assertEqual([s.page for s in pdf], [1, 2, 3])
        self.assertIn("45 days", pdf[1].text)
        docx_sections = extract(POLICY, "policy.docx")
        self.assertEqual(docx_sections[0].heading, "Travel and expenses")
        self.assertIn("Meals | 60 per day", docx_sections[-1].text)
        md = extract(b"# Close\nReconcile banks.\n## Accruals\nPost accruals by day 3.", "close.md")
        self.assertEqual([s.heading for s in md], ["Close", "Accruals"])
        eml = extract(b"From: ap@vendor.test\nSubject: Invoice 77\nContent-Type: text/plain\n\nTotal due 1,200.00", "m.eml")
        self.assertIn("Total due 1,200.00", eml[0].text)
        with self.assertRaises(Exception) as ctx:
            extract(make_pdf([""]), "scan.pdf")
        self.assertEqual(getattr(ctx.exception, "code", ""), "needs_ocr")
        with self.assertRaises(Exception):
            extract(b"MZ\x90\x00", "tool.exe")

    def test_chunking_and_query(self) -> None:
        long = " ".join(f"Sentence number {i} about revenue recognition." for i in range(400))
        chunks = chunk_sections([Section(long, page=7, heading="IFRS 15")])
        self.assertGreater(len(chunks), 5)
        self.assertTrue(all(len(t) <= 1900 for _, _, t in chunks))
        self.assertTrue(all(p == 7 and h == "IFRS 15" for p, h, _ in chunks))
        self.assertEqual(fts_query("What are the payment terms?"), '"payment" OR "terms"')
        self.assertIsNone(fts_query("the of and"))
        self.assertEqual(fts_query('x" OR 1=1; DROP'), '"1" OR "drop"')


class KnowledgeApi(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()
        self.emb_calls = 0

        def embeddings(req: httpx.Request) -> httpx.Response:
            self.emb_calls += 1
            body = json.loads(req.content)
            return httpx.Response(200, json={"data": [{"index": i, "embedding": fake_embedding(t)}
                                                      for i, t in enumerate(body["input"])]})

        self.h.router.add("POST", f"{FakeLLM.BASE}/embeddings", embeddings)

    def tearDown(self) -> None:
        self.h.close()

    def upload(self, files: dict[str, bytes], collection_id: str | None = None) -> dict:
        data = {"collection_id": collection_id} if collection_id else None
        r = self.h.api("POST", f"/api/w/{self.ws}/documents", files=[("files", (n, b)) for n, b in files.items()], data=data)
        self.h.settle()
        return r

    def test_upload_search_cite(self) -> None:
        h, ws = self.h, self.ws
        coll = h.api("POST", f"/api/w/{ws}/collections", json={"name": "Contracts"})
        r = self.upload({"msa.pdf": CONTRACT}, coll["id"])
        self.assertEqual(r["failed"], [])
        r = self.upload({"policy.docx": POLICY, "msa-copy.pdf": CONTRACT, "virus.exe": b"MZ"})
        self.assertEqual(len(r["added"]), 1)
        self.assertEqual({f["name"] for f in r["failed"]}, {"msa-copy.pdf", "virus.exe"})  # duplicate, unsupported
        docs = {d["name"]: d for d in h.api("GET", f"/api/w/{ws}/documents")}
        self.assertEqual(docs["msa.pdf"]["status"], "indexed")
        self.assertEqual(docs["msa.pdf"]["pages"], 3)
        self.assertEqual(docs["msa.pdf"]["collection_name"], "Contracts")
        hits = h.api("POST", f"/api/w/{ws}/documents/search", json={"query": "When are invoices payable?"})
        self.assertEqual((hits[0]["name"], hits[0]["page"]), ("msa.pdf", 2))
        self.assertEqual(hits[0]["match"], "keyword")
        hits = h.api("POST", f"/api/w/{ws}/documents/search", json={"query": "hotel limit per night"})
        self.assertEqual(hits[0]["name"], "policy.docx")
        self.assertEqual(hits[0]["heading"], "Travel and expenses")
        only = h.api("POST", f"/api/w/{ws}/documents/search", json={"query": "hotel", "collection_id": coll["id"]})
        self.assertTrue(all(x["name"] == "msa.pdf" for x in only))
        # page text and download
        detail = h.api("GET", f"/api/w/{ws}/documents/{docs['msa.pdf']['id']}?page=3")
        self.assertIn("36 months", detail["text"])
        self.assertNotIn("45 days", detail["text"])
        resp = h.client.get(f"/api/w/{ws}/documents/{docs['msa.pdf']['id']}/file")
        self.assertEqual(resp.headers["content-type"], "application/octet-stream")
        self.assertTrue(resp.content.startswith(b"%PDF"))
        # another workspace sees nothing
        other = h.api("POST", "/api/workspaces", json={"name": "Other Co"})["id"]
        self.assertEqual(h.api("POST", f"/api/w/{other}/documents/search", json={"query": "invoices payable"}), [])
        h.api("GET", f"/api/w/{other}/documents/{docs['msa.pdf']['id']}", expect=404)
        # delete removes chunks and the file
        h.api("DELETE", f"/api/w/{ws}/documents/{docs['msa.pdf']['id']}", expect=None)
        self.assertEqual(h.engine.db.scalar("SELECT COUNT(*) FROM doc_chunks WHERE document_id = ?",
                                            (docs["msa.pdf"]["id"],)), 0)
        self.assertEqual(h.engine.db.scalar("SELECT COUNT(*) FROM doc_chunks_fts WHERE doc_chunks_fts MATCH 'northwind'"), 0)
        self.assertFalse((h.settings.files_dir / "documents" / ws / docs["policy.docx"]["id"]).read_bytes()
                         .startswith(b"PK"))  # stored encrypted
        self.assertFalse((h.settings.files_dir / "documents" / ws / docs["msa.pdf"]["id"]).exists())

    def test_hybrid_with_embeddings(self) -> None:
        h, ws = self.h, self.ws
        self.upload({"msa.pdf": CONTRACT, "policy.docx": POLICY})
        r = h.api("PUT", f"/api/w/{ws}/knowledge/embeddings", json={"provider_id": h.api("GET", f"/api/w/{ws}/providers")
                                                                      ["providers"][0]["id"], "model": "fake-embed"})
        self.assertEqual(r["dimensions"], 64)
        h.settle()
        st = h.api("GET", f"/api/w/{ws}/knowledge/settings")
        self.assertEqual(st["mode"], "hybrid")
        self.assertEqual(st["stats"]["embedded"], 2)
        # "rental" is not in the contract; the fake embedding maps it next to "lease"/"agreement" wording
        hits = h.api("POST", f"/api/w/{ws}/documents/search", json={"query": "renews automatically notice"})
        self.assertEqual(hits[0]["page"], 3)
        self.assertEqual(hits[0]["match"], "keyword+semantic")
        # new uploads are embedded automatically
        self.upload({"note.md": b"# Leasing\nThe warehouse lease costs 12,000 per month."})
        hits = h.api("POST", f"/api/w/{ws}/documents/search", json={"query": "rental"})
        self.assertEqual(hits[0]["name"], "note.md")
        self.assertEqual(hits[0]["match"], "semantic")

    def test_agent_answers_with_citations(self) -> None:
        h, ws = self.h, self.ws
        self.upload({"msa.pdf": CONTRACT})
        h.llm.script = [[("search_documents", {"query": "payment terms Northwind"})],
                        "Invoices are payable within 45 days, and late payments accrue 1.5 percent monthly [1]."]
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "What are Northwind's payment terms?"})
        data = h.wait_run(t["id"])
        tool = [m for m in data["messages"] if m["role"] == "tool"][0]
        self.assertTrue(tool["content"].startswith("[1] msa.pdf, page 2"))
        src = tool["payload"]["blocks"][0]
        self.assertEqual(src["type"], "sources")
        self.assertEqual(src["items"][0]["page"], 2)
        self.assertEqual(data["messages"][-1]["payload"]["unverified_figures"], [])
        system = h.llm.calls[0]["messages"][0]["content"]
        self.assertIn("Knowledge base: 1 indexed documents", system)
        self.assertIn("search_documents", [x["function"]["name"] for x in h.llm.calls[0]["tools"]])

    def test_scanned_pdf_fails_clearly(self) -> None:
        self.upload({"scan.pdf": make_pdf(["", ""])})
        doc = self.h.api("GET", f"/api/w/{self.ws}/documents")[0]
        self.assertEqual(doc["status"], "failed")
        self.assertIn("OCR", doc["error"])


    def test_chat_attachments(self) -> None:
        h, ws = self.h, self.ws
        h.llm.script = [[("search_documents", {"query": "hotel limit", "document_ids": ["__ATTACHED__"]})],
                        "Hotels are capped at 180 per night [1]."]
        files = [("files", ("policy.docx", POLICY)), ("files", ("scan.pdf", make_pdf([""])))]
        r = h.api("POST", f"/api/w/{ws}/documents", files=files, data={"purpose": "chat"})
        ids = [d["id"] for d in r["added"]]
        self.assertEqual(len(ids), 2)
        again = h.api("POST", f"/api/w/{ws}/documents", files=[("files", ("policy (1).docx", POLICY))],
                      data={"purpose": "chat"})
        self.assertEqual(again["added"][0]["id"], ids[0])  # the same file attached twice is reused
        coll = next(c for c in h.api("GET", f"/api/w/{ws}/collections") if c["name"] == "Chat attachments")
        self.assertEqual(coll["documents"], 2)
        h.llm.script[0][0][1]["document_ids"] = [ids[0]]
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": "", "attachments": ids})
        data = h.wait_run(t["id"])
        user = data["messages"][0]
        self.assertEqual([a["name"] for a in user["payload"]["attachments"]], ["policy.docx", "scan.pdf"])
        self.assertEqual(data["thread"]["title"], "policy.docx")
        sent = h.llm.calls[0]["messages"][-1]["content"]
        self.assertIn("[Attached files]", sent)
        self.assertIn(f"document id {ids[0]}; readable", sent)
        self.assertIn("could not be read", sent)  # the scan: no text layer and no OCR here
        tool = next(m for m in data["messages"] if m["role"] == "tool")
        self.assertIn("180 per night", tool["content"])
        # attachments are checked against the workspace
        other = h.api("POST", "/api/workspaces", json={"name": "Other Co"})["id"]
        t2 = h.api("POST", f"/api/w/{other}/threads", json={})
        h.api("POST", f"/api/w/{other}/threads/{t2['id']}/messages", json={"text": "x", "attachments": [ids[0]]},
              expect=404)
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": " "}, expect=422)
