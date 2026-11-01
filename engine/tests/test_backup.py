"""Encrypted backups: round trip, wrong key, tampering, theft, restore safety, and financial figures surviving a
restore unchanged. Synthetic data only."""

from __future__ import annotations

import base64
import os
import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path

from desk_engine import backup as bk

from .helpers import Harness, env_without_desk


class EncryptedBackups(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace(with_model=False)
        up = self.h.api("POST", f"/api/w/{self.ws}/imports", files={"file": ("gl.csv", b"account,debit\n4000,1250.75\n4010,99.25\n")})
        self.ds = self.h.api("POST", f"/api/w/{self.ws}/imports/{up['upload_id']}/commit",
                             json={"name": "GL", "sheet": None, "header_row": 0})
        self.h.api("POST", f"/api/w/{self.ws}/providers", json={"kind": "openai_compatible", "label": "Fake",
                                                               "base_url": "https://llm.test/v1", "api_key": "sk-SECRET-PROVIDER-KEY"})
        self.key = secrets.token_bytes(32)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.h.close()
        self.tmp.cleanup()

    def _sum(self, db: Path) -> float:
        conn = sqlite3.connect(db)
        try:
            table = conn.execute("SELECT id FROM datasets WHERE name = 'GL'").fetchone()[0]
            return conn.execute(f"SELECT SUM(c1) FROM ds_{table}").fetchone()[0]
        finally:
            conn.close()

    def test_round_trip_keeps_every_financial_figure(self) -> None:
        target = self.dir / "b1.desk-backup"
        info = bk.write_backup(self.h.settings.db_path, target, self.key)
        self.assertEqual(info["schema_version"], 18)
        self.assertEqual(info["workspaces"], 1)
        self.assertEqual(info["datasets"], 1)
        self.assertEqual(oct(target.stat().st_mode)[-3:], "600")
        restored = self.dir / "restored.sqlite3"
        bk.restore_backup(target, restored, self.key)
        self.assertEqual(self._sum(restored), 1350.0)
        self.assertEqual(self._sum(restored), self._sum(self.h.settings.db_path))
        # the restored database is byte-identical to a plain snapshot taken at the same moment
        self.assertEqual(restored.read_bytes(), bk.open_sealed(target.read_bytes(), self.key))
        # a stored credential restored under the same master key still opens
        from desk_engine.db import Database
        from desk_engine.security import SecretBox, SecretStore

        store = SecretStore(Database(restored), SecretBox(self.h.settings.master_key))
        sid = store.db.one("SELECT secret_id FROM llm_providers")["secret_id"]
        self.assertEqual(store.get(sid, workspace_id=self.ws)["api_key"], "sk-SECRET-PROVIDER-KEY")
        store.db.close()

    def test_theft_of_the_file_reveals_nothing(self) -> None:
        target = self.dir / "b2.desk-backup"
        bk.write_backup(self.h.settings.db_path, target, self.key)
        blob = target.read_bytes()
        self.assertTrue(blob.startswith(bk.MAGIC))
        for needle in (b"SQLite format 3", b"CREATE TABLE", b"workspaces", b"Acme Ltd", b"1250.75", b"4000",
                       b"sk-SECRET", b"llm_providers"):
            self.assertNotIn(needle, blob, needle)
        # the master key is not the backup key: holding the server's key opens no backup
        with self.assertRaises(bk.BackupError):
            bk.open_sealed(blob, self.h.settings.master_key)
        with self.assertRaises(bk.BackupError):
            bk.open_sealed(blob, secrets.token_bytes(32))

    def test_tampering_is_detected_before_anything_is_written(self) -> None:
        target = self.dir / "b3.desk-backup"
        bk.write_backup(self.h.settings.db_path, target, self.key)
        blob = bytearray(target.read_bytes())
        for pos in (len(bk.MAGIC) + 1 + 12 + 100, len(blob) - 1, len(blob) // 2):
            altered = bytearray(blob)
            altered[pos] ^= 0x01
            (self.dir / "bad.desk-backup").write_bytes(altered)
            out = self.dir / "never.sqlite3"
            with self.assertRaises(bk.BackupError):
                bk.restore_backup(self.dir / "bad.desk-backup", out, self.key)
            self.assertFalse(out.exists())
            self.assertEqual([p.name for p in self.dir.glob("never.sqlite3.tmp*")], [])
        # truncated and foreign files
        (self.dir / "short").write_bytes(blob[:20])
        with self.assertRaises(bk.BackupError):
            bk.inspect_backup(self.dir / "short", self.key)
        (self.dir / "plain.sqlite3").write_bytes(bk.snapshot(self.h.settings.db_path))
        with self.assertRaises(bk.BackupError):
            bk.inspect_backup(self.dir / "plain.sqlite3", self.key)

    def test_restore_refuses_to_overwrite_unless_asked_and_drops_stale_journals(self) -> None:
        target = self.dir / "b4.desk-backup"
        bk.write_backup(self.h.settings.db_path, target, self.key)
        existing = self.dir / "live.sqlite3"
        existing.write_bytes(b"not a database")
        Path(str(existing) + "-wal").write_bytes(b"stale")
        with self.assertRaises(bk.BackupError):
            bk.restore_backup(target, existing, self.key)
        self.assertEqual(existing.read_bytes(), b"not a database")
        bk.restore_backup(target, existing, self.key, overwrite=True)
        self.assertEqual(self._sum(existing), 1350.0)
        self.assertFalse(Path(str(existing) + "-wal").exists())

    def test_command_line(self) -> None:
        import subprocess
        import sys

        keyfile = self.dir / "backup.key"
        keyfile.write_text(base64.urlsafe_b64encode(self.key).decode())
        env = {**env_without_desk(), "DESK_MODE": "desktop", "DESK_DATA_DIR": str(self.h.settings.data_dir),
               "DESK_MASTER_KEY": base64.urlsafe_b64encode(self.h.settings.master_key).decode(),
               "DESK_LOCAL_TOKEN": "t" * 40, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        run = lambda *a: subprocess.run([sys.executable, "-m", "desk_engine", *a], capture_output=True, text=True, env=env)  # noqa: E731
        r = run("backup", str(self.dir / "cli"), "--key-file", str(keyfile))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("encrypted backup written", r.stdout)
        self.assertTrue((self.dir / "cli.desk-backup").exists())
        r = run("backup-check", str(self.dir / "cli.desk-backup"), "--key-file", str(keyfile))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("passes the integrity check", r.stdout)
        r = run("backup", str(self.dir / "nokey"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("backup key is required", r.stderr)
        self.assertFalse((self.dir / "nokey.desk-backup").exists())
        wrong = self.dir / "wrong.key"
        wrong.write_text(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        r = run("restore", str(self.dir / "cli.desk-backup"), "--key-file", str(wrong), "--to", str(self.dir / "r.sqlite3"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("wrong backup key", r.stderr)
        self.assertFalse((self.dir / "r.sqlite3").exists())
        r = run("restore", str(self.dir / "cli.desk-backup"), "--key-file", str(keyfile), "--to", str(self.dir / "r.sqlite3"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._sum(self.dir / "r.sqlite3"), 1350.0)


if __name__ == "__main__":
    unittest.main()
