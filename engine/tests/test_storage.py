"""File storage: AWS Signature V4 examples, S3 round trips, encryption, and the engine on S3."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import tempfile
import unittest
from pathlib import Path

import httpx2 as httpx

from desk_engine.errors import NotFound
from desk_engine.storage import EMPTY_SHA256, MAGIC, EncryptedStorage, LocalStorage, S3Storage, sigv4_headers

from .helpers import Harness, Router
from .s3mock import MockS3

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def sig(headers: dict[str, str]) -> str:
    return headers["Authorization"].rsplit("Signature=", 1)[1]


class SignatureV4(unittest.TestCase):
    """Examples from the Amazon S3 API reference ("Signature Calculations for the Authorization Header")."""

    def test_get_object(self) -> None:
        h = sigv4_headers(method="GET", url="https://examplebucket.s3.amazonaws.com/test.txt",
                          headers={"Range": "bytes=0-9"}, payload_sha256=EMPTY_SHA256, access_key=AWS_KEY,
                          secret_key=AWS_SECRET, region="us-east-1", amz_date="20130524T000000Z")
        self.assertEqual(sig(h), "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
        self.assertIn("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date", h["Authorization"])

    def test_put_object(self) -> None:
        body = b"Welcome to Amazon S3."
        h = sigv4_headers(method="PUT", url="https://examplebucket.s3.amazonaws.com/test%24file.text",
                          headers={"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},
                          payload_sha256=hashlib.sha256(body).hexdigest(), access_key=AWS_KEY, secret_key=AWS_SECRET,
                          region="us-east-1", amz_date="20130524T000000Z")
        self.assertEqual(sig(h), "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")

    def test_list_objects(self) -> None:
        h = sigv4_headers(method="GET", url="https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J",
                          headers={}, payload_sha256=EMPTY_SHA256, access_key=AWS_KEY, secret_key=AWS_SECRET,
                          region="us-east-1", amz_date="20130524T000000Z")
        self.assertEqual(sig(h), "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")


class Backends(unittest.TestCase):
    def test_s3_round_trip_and_encryption(self) -> None:
        s3 = MockS3()
        raw = S3Storage(endpoint=s3.endpoint, bucket="desk-files", access_key=s3.access_key,
                        secret_key=s3.secret_key, prefix="team1", transport=httpx.MockTransport(s3))
        store = EncryptedStorage(raw, secrets.token_bytes(32))

        async def run() -> None:
            await store.check()
            await store.put("documents/ws_1/doc_a", b"%PDF-1.7 secret contract")
            await store.put("documents/ws_1/doc b.pdf".replace(" ", "_"), b"x")
            await store.put("documents/ws_2/doc_c", b"other workspace")
            self.assertEqual(await store.get("documents/ws_1/doc_a"), b"%PDF-1.7 secret contract")
            stored = s3.buckets["desk-files"]["team1/documents/ws_1/doc_a"]
            self.assertTrue(stored.startswith(MAGIC))
            self.assertNotIn(b"secret contract", stored)
            # a ciphertext copied to another key does not decrypt
            s3.buckets["desk-files"]["team1/documents/ws_1/doc_x"] = stored
            with self.assertRaises(Exception):
                await store.get("documents/ws_1/doc_x")
            keys = sorted(i.key for i in await store.list("documents/ws_1"))
            self.assertEqual(keys, ["documents/ws_1/doc_a", "documents/ws_1/doc_b.pdf", "documents/ws_1/doc_x"])
            await store.delete("documents/ws_1/doc_a")
            with self.assertRaises(NotFound):
                await store.get("documents/ws_1/doc_a")
            with self.assertRaises(Exception):
                await store.put("../escape", b"x")

        asyncio.run(run())
        self.assertEqual(s3.bad_signatures, 0)

    def test_wrong_secret_and_missing_bucket(self) -> None:
        s3 = MockS3()
        bad = S3Storage(endpoint=s3.endpoint, bucket="desk-files", access_key=s3.access_key, secret_key="wrong",
                        transport=httpx.MockTransport(s3))
        with self.assertRaises(Exception) as cm:
            asyncio.run(bad.check())
        self.assertIn("rejected", str(cm.exception))
        create = S3Storage(endpoint=s3.endpoint, bucket="new-bucket", access_key=s3.access_key,
                           secret_key=s3.secret_key, create_bucket=True, transport=httpx.MockTransport(s3))
        self.assertTrue(asyncio.run(create.check())["created_bucket"])
        self.assertIn("new-bucket", s3.buckets)

    def test_local_reads_files_written_before_encryption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "documents" / "ws_1").mkdir(parents=True)
            (Path(tmp) / "documents" / "ws_1" / "doc_old").write_bytes(b"plain old file")
            store = EncryptedStorage(LocalStorage(Path(tmp)), secrets.token_bytes(32))
            self.assertEqual(asyncio.run(store.get("documents/ws_1/doc_old")), b"plain old file")


class EngineOnS3(unittest.TestCase):
    def test_documents_and_imports_use_the_bucket(self) -> None:
        s3 = MockS3()
        router = Router()
        for method in ("GET", "PUT", "DELETE", "HEAD"):
            router.add(method, s3.endpoint, s3)
        h = Harness(router=router, storage={"kind": "s3", "endpoint": s3.endpoint, "bucket": "desk-files",
                             "access_key": s3.access_key, "secret_key": s3.secret_key, "encrypt": True})
        try:
            ws = h.setup_workspace()
            files = [("files", ("notes.md", b"# Travel\n\nHotels are capped at 180 per night.", "text/markdown"))]
            doc = h.api("POST", f"/api/w/{ws}/documents", files=files)["added"][0]
            import time
            for _ in range(100):
                if h.api("GET", f"/api/w/{ws}/documents/{doc['id']}")["document"]["status"] != "processing":
                    break
                time.sleep(0.05)
            self.assertEqual(h.api("GET", f"/api/w/{ws}/documents/{doc['id']}")["document"]["status"], "indexed")
            key = f"documents/{ws}/{doc['id']}"
            self.assertIn(key, s3.buckets["desk-files"])
            self.assertTrue(s3.buckets["desk-files"][key].startswith(MAGIC))
            self.assertFalse((h.settings.files_dir / "documents").exists())
            resp = h.client.get(f"/api/w/{ws}/documents/{doc['id']}/file")
            self.assertEqual(resp.content, b"# Travel\n\nHotels are capped at 180 per night.")
            up = h.api("POST", f"/api/w/{ws}/imports", files=[("file", ("tb.csv", b"Account,Amount\nSales,100\n",
                                                                         "text/csv"))])
            self.assertTrue(any(k.startswith(f"uploads/{ws}/{up['upload_id']}") for k in s3.buckets["desk-files"]))
            h.api("POST", f"/api/w/{ws}/imports/{up['upload_id']}/commit", json={})
            self.assertFalse(any(k.startswith(f"uploads/{ws}/") for k in s3.buckets["desk-files"]))
            h.api("DELETE", f"/api/w/{ws}", expect=None)
            self.assertFalse(any(ws in k for k in s3.buckets["desk-files"]))
            self.assertEqual(s3.bad_signatures, 0)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
