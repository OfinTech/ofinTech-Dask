"""File storage: the local data folder or any S3-compatible object store (MinIO, AWS S3, Cloudflare R2,
Wasabi, SeaweedFS, Garage, Ceph RGW).

Keys look like ``documents/<workspace>/<document>``. Objects are encrypted by the engine before they leave
it (AES-256-GCM with a key derived from the master key, the object key as associated data), so the storage
operator — or anyone holding a copy of the bucket — cannot read them, and an object copied to another key
does not decrypt. Files written before encryption was turned on are still read.

The S3 client is a small Signature Version 4 implementation over httpx: no extra dependency, and it is
checked against AWS's published signing examples in the tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx2 as httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .errors import AppError, ConfigError, Invalid, NotFound, UpstreamError

KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*(/[A-Za-z0-9_][A-Za-z0-9_.\-]*)*$")
MAGIC = b"\x00DSKENC1"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def check_key(key: str) -> str:
    if not KEY_RE.match(key) or ".." in key or len(key) > 512:
        raise Invalid("Bad storage key.")
    return key


@dataclass
class ObjectInfo:
    key: str
    size: int
    modified: float


class Storage(Protocol):
    kind: str

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def list(self, prefix: str) -> list[ObjectInfo]: ...

    async def check(self) -> dict[str, Any]: ...

    def describe(self) -> dict[str, Any]: ...


async def delete_prefix(storage: Storage, prefix: str) -> int:
    items = await storage.list(prefix)
    for item in items:
        await storage.delete(item.key)
    return len(items)


# --- local folder --------------------------------------------------------------------------------------


class LocalStorage:
    kind = "local"

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + f".tmp{os.getpid()}")
            tmp.write_bytes(data)
            tmp.chmod(0o600)
            tmp.replace(path)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise NotFound("The file is no longer in storage.") from exc

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def list(self, prefix: str) -> list[ObjectInfo]:
        base = self._path(prefix.rstrip("/"))
        if not base.exists():
            return []
        out = []
        for p in sorted(base.rglob("*")) if base.is_dir() else [base]:
            if p.is_file() and ".tmp" not in p.name:
                st = p.stat()
                out.append(ObjectInfo(str(p.relative_to(self.root)), st.st_size, st.st_mtime))
        return out

    async def check(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        return {"ok": True}

    def describe(self) -> dict[str, Any]:
        return {"kind": "local", "location": str(self.root)}


# --- S3 (Signature Version 4) ------------------------------------------------------------------------------


def _uri_encode(value: str, *, keep_slash: bool) -> str:
    return quote(value, safe="/-_.~" if keep_slash else "-_.~")


def sigv4_headers(*, method: str, url: str, headers: dict[str, str], payload_sha256: str, access_key: str,
                  secret_key: str, region: str, service: str = "s3", amz_date: str | None = None) -> dict[str, str]:
    """Returns the headers to send, including ``Authorization`` (AWS Signature Version 4, header form)."""
    parts = urlsplit(url)
    amz_date = amz_date or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    host = parts.netloc
    out = {k: v for k, v in headers.items()}
    out["host"] = host
    out["x-amz-date"] = amz_date
    out["x-amz-content-sha256"] = payload_sha256
    canonical_uri = _uri_encode(_unquote(parts.path or "/"), keep_slash=True)
    query_pairs = []
    if parts.query:
        for item in parts.query.split("&"):
            k, _, v = item.partition("=")
            query_pairs.append((_uri_encode(_unquote(k), keep_slash=False), _uri_encode(_unquote(v), keep_slash=False)))
    canonical_query = "&".join(f"{k}={v}" for k, v in sorted(query_pairs))
    lower = {k.lower(): " ".join(str(v).strip().split()) for k, v in out.items()}
    signed = sorted(lower)
    canonical_headers = "".join(f"{k}:{lower[k]}\n" for k in signed)
    signed_headers = ";".join(signed)
    canonical_request = "\n".join([method, canonical_uri, canonical_query, canonical_headers, signed_headers,
                                   payload_sha256])
    scope = f"{day}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                                hashlib.sha256(canonical_request.encode()).hexdigest()])

    def h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k = h(h(h(h(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    out["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, "
                            f"Signature={signature}")
    out.pop("host")  # httpx sets Host itself (same value)
    return out


def _unquote(s: str) -> str:
    from urllib.parse import unquote

    return unquote(s)


def _xml_text(root: ET.Element, name: str) -> str | None:
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == name:
            return el.text
    return None


class S3Storage:
    kind = "s3"

    def __init__(self, *, endpoint: str, bucket: str, access_key: str, secret_key: str, region: str = "us-east-1",
                 prefix: str = "", path_style: bool = True, create_bucket: bool = False, sse: str = "",
                 transport: Any = None) -> None:
        if not bucket or not access_key or not secret_key:
            raise ConfigError("S3 storage needs DESK_S3_BUCKET, DESK_S3_ACCESS_KEY and DESK_S3_SECRET_KEY.")
        endpoint = (endpoint or f"https://s3.{region}.amazonaws.com").rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise ConfigError("DESK_S3_ENDPOINT must start with http:// or https://")
        self.endpoint = endpoint
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region or "us-east-1"
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.path_style = path_style
        self.create_bucket = create_bucket
        self.sse = sse
        self.transport = transport

    # -- request plumbing --------------------------------------------------------------------------------
    def _url(self, key: str | None = None, query: str = "") -> str:
        scheme, host = self.endpoint.split("://", 1)
        path = "" if key is None else "/" + _uri_encode(self.prefix + key, keep_slash=True)
        if self.path_style:
            url = f"{scheme}://{host}/{self.bucket}{path}"
        else:
            url = f"{scheme}://{self.bucket}.{host}{path or '/'}"
        return url + (f"?{query}" if query else "")

    async def _request(self, method: str, url: str, body: bytes = b"", headers: dict[str, str] | None = None
                       ) -> httpx.Response:
        payload_hash = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
        signed = sigv4_headers(method=method, url=url, headers=dict(headers or {}), payload_sha256=payload_hash,
                               access_key=self.access_key, secret_key=self.secret_key, region=self.region)
        try:
            async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
                return await client.request(method, url, content=body or None, headers=signed)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Object storage is not reachable ({type(exc).__name__}).",
                                code="storage_unreachable") from exc

    @staticmethod
    def _error(resp: httpx.Response, what: str) -> AppError:
        code = None
        try:
            code = _xml_text(ET.fromstring(resp.content), "Code") if resp.content else None
        except ET.ParseError:
            pass
        if resp.status_code == 404:
            return NotFound("The file is no longer in storage.")
        return UpstreamError(f"Object storage refused to {what} (HTTP {resp.status_code}{', ' + code if code else ''}).",
                             code="storage_error")

    # -- API ------------------------------------------------------------------------------------------------
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        headers = {"content-type": content_type, "content-length": str(len(data))}
        if self.sse:
            headers["x-amz-server-side-encryption"] = self.sse
        resp = await self._request("PUT", self._url(check_key(key)), data, headers)
        if resp.status_code >= 300:
            raise self._error(resp, "store the file")

    async def get(self, key: str) -> bytes:
        resp = await self._request("GET", self._url(check_key(key)))
        if resp.status_code >= 300:
            raise self._error(resp, "return the file")
        return resp.content

    async def delete(self, key: str) -> None:
        resp = await self._request("DELETE", self._url(check_key(key)))
        if resp.status_code >= 300 and resp.status_code != 404:
            raise self._error(resp, "delete the file")

    async def list(self, prefix: str) -> list[ObjectInfo]:
        check_key(prefix.rstrip("/"))
        out: list[ObjectInfo] = []
        token: str | None = None
        while True:
            query = "list-type=2&max-keys=1000&prefix=" + _uri_encode(self.prefix + prefix, keep_slash=False)
            if token:
                query += "&continuation-token=" + _uri_encode(token, keep_slash=False)
            resp = await self._request("GET", self._url(None, query))
            if resp.status_code >= 300:
                raise self._error(resp, "list files")
            root = ET.fromstring(resp.content)
            for el in root:
                if el.tag.rsplit("}", 1)[-1] != "Contents":
                    continue
                key = _xml_text(el, "Key") or ""
                size = int(_xml_text(el, "Size") or 0)
                modified = _xml_text(el, "LastModified") or ""
                try:
                    ts = datetime.fromisoformat(modified.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = time.time()
                out.append(ObjectInfo(key[len(self.prefix):], size, ts))
            if (_xml_text(root, "IsTruncated") or "false").lower() == "true":
                token = _xml_text(root, "NextContinuationToken")
                if not token:
                    break
            else:
                break
        return out

    async def check(self) -> dict[str, Any]:
        resp = await self._request("HEAD", self._url())
        if resp.status_code == 404 and self.create_bucket:
            body = b""
            if self.region != "us-east-1":
                body = (f'<CreateBucketConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                        f"<LocationConstraint>{self.region}</LocationConstraint></CreateBucketConfiguration>").encode()
            created = await self._request("PUT", self._url(), body)
            if created.status_code >= 300:
                raise self._error(created, "create the bucket")
            return {"ok": True, "created_bucket": True}
        if resp.status_code == 404:
            raise ConfigError(f"The bucket '{self.bucket}' does not exist. Create it or set DESK_S3_CREATE_BUCKET=true.")
        if resp.status_code in (401, 403):
            raise ConfigError("Object storage rejected the access key (check DESK_S3_ACCESS_KEY / DESK_S3_SECRET_KEY "
                              "and the bucket policy).")
        if resp.status_code >= 300:
            raise self._error(resp, "open the bucket")
        return {"ok": True}

    def describe(self) -> dict[str, Any]:
        return {"kind": "s3", "endpoint": self.endpoint, "bucket": self.bucket, "prefix": self.prefix,
                "region": self.region, "server_side_encryption": self.sse or None}


# --- encryption wrapper -----------------------------------------------------------------------------------


class EncryptedStorage:
    """AES-256-GCM per object; the storage key is the associated data."""

    def __init__(self, inner: Storage, master_key: bytes) -> None:
        self.inner = inner
        self.kind = inner.kind
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"ofintech-desk storage v1").derive(master_key)
        self._aead = AESGCM(key)

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        nonce = os.urandom(12)
        blob = MAGIC + nonce + self._aead.encrypt(nonce, data, check_key(key).encode())
        await self.inner.put(key, blob, "application/octet-stream")

    async def get(self, key: str) -> bytes:
        blob = await self.inner.get(key)
        if not blob.startswith(MAGIC):
            return blob  # written before encryption was turned on
        try:
            return self._aead.decrypt(blob[len(MAGIC):len(MAGIC) + 12], blob[len(MAGIC) + 12:], key.encode())
        except Exception as exc:
            raise AppError("A stored file could not be decrypted (wrong master key, or the file was moved).",
                           code="storage_decrypt", status=500) from exc

    async def delete(self, key: str) -> None:
        await self.inner.delete(key)

    async def list(self, prefix: str) -> list[ObjectInfo]:
        return await self.inner.list(prefix)

    async def check(self) -> dict[str, Any]:
        return await self.inner.check()

    def describe(self) -> dict[str, Any]:
        return self.inner.describe() | {"encrypted": True}


def build_storage(settings: Any, transport: Any = None) -> Storage:
    s = settings.storage
    if s.get("kind", "local") == "s3":
        inner: Storage = S3Storage(endpoint=s.get("endpoint", ""), bucket=s.get("bucket", ""),
                                   access_key=s.get("access_key", ""), secret_key=s.get("secret_key", ""),
                                   region=s.get("region", "us-east-1"), prefix=s.get("prefix", ""),
                                   path_style=s.get("path_style", True), create_bucket=s.get("create_bucket", False),
                                   sse=s.get("sse", ""), transport=transport)
    else:
        inner = LocalStorage(settings.files_dir)
    if s.get("encrypt", True):
        return EncryptedStorage(inner, settings.master_key)
    return inner
