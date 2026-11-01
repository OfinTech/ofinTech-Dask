"""Encrypted database backups.

A backup is a consistent online copy of the SQLite database (the backup API, so the engine keeps running), sealed
with AES-256-GCM under a **backup key** that is not the master key: an operator can hand backup files and the
backup key to one place (an off-site store, a disaster-recovery team) without handing over the key that opens the
stored credentials, and the master key without the backup key opens no backup.

File format (``.desk-backup``)::

    magic "DSKBK1" · 1 byte version · 12-byte nonce · AES-256-GCM ciphertext of the SQLite file
    associated data: the 8-byte little-endian length of the plaintext

Restoring authenticates the whole file before a byte is written, checks that what came out is a SQLite database
that passes ``PRAGMA integrity_check``, and refuses to overwrite an existing file unless asked.
"""

from __future__ import annotations

import base64
import os
import sqlite3
import struct
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"DSKBK1"
VERSION = b"\x01"
SUFFIX = ".desk-backup"


class BackupError(RuntimeError):
    pass


def decode_key(raw: str) -> bytes:
    raw = raw.strip()
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError) as exc:
        raise BackupError("The backup key must be base64 (32 random bytes; make one with `python -m desk_engine keygen`).") from exc
    if len(key) != 32:
        raise BackupError("The backup key must decode to exactly 32 bytes.")
    return key


def read_key_file(path: str | Path) -> bytes:
    p = Path(path)
    try:
        return decode_key(p.read_text())
    except OSError as exc:
        raise BackupError(f"Cannot read the backup key file {p}.") from exc


def snapshot(db_path: Path) -> bytes:
    """A consistent copy of the live database, as bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "snapshot.sqlite3"
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(copy)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()
        return copy.read_bytes()


def seal(plaintext: bytes, key: bytes) -> bytes:
    nonce = os.urandom(12)
    aad = struct.pack("<Q", len(plaintext))
    return MAGIC + VERSION + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def open_sealed(blob: bytes, key: bytes) -> bytes:
    head = len(MAGIC) + 1 + 12
    if not blob.startswith(MAGIC) or len(blob) < head + 16:
        raise BackupError("This is not an OfinTech Desk encrypted backup.")
    if blob[len(MAGIC):len(MAGIC) + 1] != VERSION:
        raise BackupError("This backup was written by a newer version of the engine.")
    nonce = blob[len(MAGIC) + 1:head]
    body = blob[head:]
    plaintext_len = len(body) - 16
    try:
        return AESGCM(key).decrypt(nonce, body, struct.pack("<Q", plaintext_len))
    except Exception as exc:
        raise BackupError("The backup could not be opened: wrong backup key, or the file was altered.") from exc


def verify_sqlite(data: bytes) -> dict[str, int]:
    """Opens the bytes as a database and runs the integrity check; returns a few counts for the operator."""
    if not data.startswith(b"SQLite format 3\x00"):
        raise BackupError("The decrypted backup is not a SQLite database.")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "check.sqlite3"
        p.write_bytes(data)
        conn = sqlite3.connect(p)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise BackupError(f"The backup's integrity check failed: {ok}")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            counts = {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                      for name in ("workspaces", "users", "datasets", "documents", "messages")
                      if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()}
        finally:
            conn.close()
    return {"schema_version": version, **counts}


def write_backup(db_path: Path, target: Path, key: bytes) -> dict[str, int]:
    data = snapshot(db_path)
    info = verify_sqlite(data)
    sealed = seal(data, key)
    tmp = target.with_name(target.name + f".tmp{os.getpid()}")
    tmp.write_bytes(sealed)
    tmp.chmod(0o600)
    os.replace(tmp, target)
    info["bytes"] = len(sealed)
    return info


def restore_backup(source: Path, target: Path, key: bytes, *, overwrite: bool = False) -> dict[str, int]:
    if target.exists() and not overwrite:
        raise BackupError(f"{target} exists; pass --overwrite to replace it (stop the engine first).")
    data = open_sealed(source.read_bytes(), key)
    info = verify_sqlite(data)
    tmp = target.with_name(target.name + f".tmp{os.getpid()}")
    tmp.write_bytes(data)
    tmp.chmod(0o600)
    os.replace(tmp, target)
    for side in ("-wal", "-shm"):   # a restored database starts clean; a stale journal from the old file must not apply
        p = Path(str(target) + side)
        if p.exists():
            p.unlink()
    return info


def inspect_backup(source: Path, key: bytes) -> dict[str, int]:
    """What a backup holds, without restoring it (a restore rehearsal)."""
    return verify_sqlite(open_sealed(source.read_bytes(), key))
