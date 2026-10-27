"""An in-memory S3 endpoint for tests (path-style). It checks each request's Signature V4 against the URL
httpx actually sent, so a mismatch between what the client signs and what it sends is caught."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit
from xml.sax.saxutils import escape

import httpx2 as httpx

from desk_engine.storage import sigv4_headers


class MockS3:
    def __init__(self, endpoint: str = "http://minio.test:9000", access_key: str = "desk-app",
                 secret_key: str = "desk-secret-key-123", bucket: str = "desk-files") -> None:
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.buckets: dict[str, dict[str, bytes]] = {bucket: {}}
        self.requests: list[httpx.Request] = []
        self.bad_signatures = 0

    def _verify(self, request: httpx.Request) -> bool:
        auth = request.headers.get("authorization", "")
        m = re.match(r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/([^/]+)/s3/aws4_request, "
                     r"SignedHeaders=([^,]+), Signature=([0-9a-f]{64})$", auth)
        if not m or m.group(1) != self.access_key:
            return False
        body = request.content or b""
        if request.headers.get("x-amz-content-sha256") != hashlib.sha256(body).hexdigest():
            return False
        signed = m.group(4).split(";")
        headers = {k: request.headers[k] for k in signed if k != "host"}
        url = str(request.url)
        expected = sigv4_headers(method=request.method, url=url, headers=headers,
                                 payload_sha256=request.headers["x-amz-content-sha256"], access_key=self.access_key,
                                 secret_key=self.secret_key, region=m.group(3),
                                 amz_date=request.headers["x-amz-date"])["Authorization"]
        return expected == auth

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._verify(request):
            self.bad_signatures += 1
            return httpx.Response(403, content=b"<Error><Code>SignatureDoesNotMatch</Code></Error>")
        parts = urlsplit(str(request.url))
        segs = parts.path.lstrip("/").split("/", 1)
        bucket = unquote(segs[0])
        key = unquote(segs[1]) if len(segs) > 1 else ""
        if bucket not in self.buckets:
            if request.method == "PUT" and not key:
                self.buckets[bucket] = {}
                return httpx.Response(200)
            return httpx.Response(404, content=b"<Error><Code>NoSuchBucket</Code></Error>")
        store = self.buckets[bucket]
        if not key:
            if request.method == "HEAD":
                return httpx.Response(200)
            params = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p)
            prefix = unquote(params.get("prefix", ""))
            keys = sorted(k for k in store if k.startswith(prefix))
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            items = "".join(f"<Contents><Key>{escape(k)}</Key><Size>{len(store[k])}</Size>"
                            f"<LastModified>{now}</LastModified></Contents>" for k in keys)
            xml = (f'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                   f"<Name>{bucket}</Name><IsTruncated>false</IsTruncated>{items}</ListBucketResult>")
            return httpx.Response(200, content=xml.encode())
        if request.method == "PUT":
            store[key] = request.content
            return httpx.Response(200)
        if request.method == "GET":
            if key not in store:
                return httpx.Response(404, content=b"<Error><Code>NoSuchKey</Code></Error>")
            return httpx.Response(200, content=store[key])
        if request.method == "DELETE":
            store.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(405)
