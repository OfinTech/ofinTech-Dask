"""Cryptography helpers: secret encryption, password hashing, tokens, URL safety checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .db import Database, new_id, now
from .errors import Invalid, NotFound

# --- secrets at rest ---------------------------------------------------------------------


class SecretBox:
    """AES-256-GCM with the engine master key. Each value gets a random 96-bit nonce and is
    bound to its purpose and workspace (associated data), so a ciphertext copied to another row
    does not decrypt."""

    VERSION = b"\x01"

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._aead = AESGCM(key)

    def seal(self, plaintext: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(12)
        return self.VERSION + nonce + self._aead.encrypt(nonce, plaintext, aad)

    def open(self, blob: bytes, aad: bytes) -> bytes:
        if not blob or blob[:1] != self.VERSION:
            raise ValueError("unknown secret format")
        return self._aead.decrypt(blob[1:13], blob[13:], aad)


class SecretStore:
    def __init__(self, db: Database, box: SecretBox) -> None:
        self.db = db
        self.box = box

    @staticmethod
    def _aad(secret_id: str, purpose: str, workspace_id: str | None) -> bytes:
        return f"{secret_id}|{purpose}|{workspace_id or '-'}".encode()

    def put(self, purpose: str, value: dict[str, Any] | str, *, workspace_id: str | None,
            secret_id: str | None = None) -> str:
        sid = secret_id or new_id("sec_")
        data = json.dumps(value).encode()
        blob = self.box.seal(data, self._aad(sid, purpose, workspace_id))
        with self.db.tx() as c:
            c.execute("DELETE FROM secrets WHERE id = ?", (sid,))
            c.execute(
                "INSERT INTO secrets (id, workspace_id, purpose, ciphertext, created_at) VALUES (?,?,?,?,?)",
                (sid, workspace_id, purpose, blob, now()),
            )
        return sid

    def get(self, secret_id: str | None, *, workspace_id: str | None) -> Any:
        if not secret_id:
            raise NotFound("secret not found")
        row = self.db.one("SELECT * FROM secrets WHERE id = ?", (secret_id,))
        if row is None or row["workspace_id"] != workspace_id:
            raise NotFound("secret not found")
        plain = self.box.open(row["ciphertext"], self._aad(secret_id, row["purpose"], workspace_id))
        return json.loads(plain)

    def delete(self, secret_id: str | None) -> None:
        if secret_id:
            self.db.execute("DELETE FROM secrets WHERE id = ?", (secret_id,))


# --- passwords -----------------------------------------------------------------------------

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        # constant-ish work so that unknown users take as long as known ones
        hashlib.scrypt(b"x", salt=b"0" * 16, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=64 * 1024 * 1024)
        return False
    _, n, r, p, salt, digest = stored.split("$")
    candidate = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                               maxmem=64 * 1024 * 1024, dklen=32)
    return hmac.compare_digest(candidate, base64.b64decode(digest))


def check_password_policy(password: str) -> None:
    problems = []
    if len(password) < 12:
        problems.append("at least 12 characters")
    if password.lower() == password or password.upper() == password:
        problems.append("upper- and lower-case letters")
    if not any(ch.isdigit() for ch in password):
        problems.append("a digit")
    if problems:
        raise Invalid("Password needs " + ", ".join(problems) + ".", code="weak_password")


# --- tokens --------------------------------------------------------------------------------


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def hmac_sha256_hex(key: str, body: bytes) -> str:
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


# --- outbound URL policy (SSRF guard) --------------------------------------------------------


def check_outbound_url(url: str, *, allow_private: bool, allow_http: bool = False) -> str:
    """Validate a user-supplied URL before the engine connects to it (MCP servers, custom LLM
    endpoints). In server mode private, loopback and link-local addresses are refused so a user
    cannot make the Team Server reach internal services."""
    parsed = urlparse(url)
    if parsed.scheme not in ({"https", "http"} if (allow_http or allow_private) else {"https"}):
        raise Invalid("URL must use https", code="bad_url")
    if not parsed.hostname:
        raise Invalid("URL has no host", code="bad_url")
    if parsed.username or parsed.password:
        raise Invalid("Put credentials in the secret fields, not in the URL", code="bad_url")
    if allow_private:
        return url
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise Invalid(f"Cannot resolve {parsed.hostname}", code="bad_url") from exc
    from .egress import _ip_internal

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if _ip_internal(str(addr)) or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
            raise Invalid("URLs that point to private or local networks are not allowed on the Team Server",
                          code="bad_url")
    return url
