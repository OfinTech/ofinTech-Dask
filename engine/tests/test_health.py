"""Liveness, readiness, and the rule that an unauthenticated probe is told nothing it should not know.

The checks exist because every one of these failures answers `/health` perfectly: a container on a full disk,
a deploy whose migrations did not run, a bucket whose policy changed, a background loop stuck on an await.
None of them fails a request until somebody's work is already lost.
"""

import json
import sqlite3
import time
import unittest
from types import SimpleNamespace

from desk_engine import health
from desk_engine.config import VERSION
from desk_engine.db import MIGRATIONS
from tests.helpers import Harness


class Liveness(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()

    def tearDown(self) -> None:
        self.h.close()

    def test_health_still_answers_what_it_always_did(self) -> None:
        """Existing probes point at /health; readiness is a new route, not a change to this one."""
        body = self.h.api("GET", "/health")
        self.assertEqual(body, {"status": "ok", "version": VERSION, "mode": "desktop"})

    def test_health_does_not_do_the_work_of_ready(self) -> None:
        """It takes no lock and reads nothing, so it keeps answering when the engine underneath cannot work."""
        self.h.engine.db.conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) - 1}")
        self.assertEqual(self.h.api("GET", "/health")["status"], "ok")
        self.assertEqual(self.h.api("GET", "/ready", expect=503)["status"], "unready")


class Readiness(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()

    def tearDown(self) -> None:
        self.h.close()

    def ready(self, expect: int = 200) -> dict:
        return self.h.api("GET", "/ready", expect=expect)

    def test_a_working_engine_is_ready(self) -> None:
        body = self.ready()
        self.assertEqual(body["status"], "ready")
        self.assertEqual({c["status"] for c in body["checks"].values()}, {"ok"})
        self.assertEqual(set(body["checks"]), {"migrations", "database", "disk", "storage", "scheduler"})

    def test_a_schema_behind_the_build_is_unready(self) -> None:
        """A deploy that applied code before its migrations. Every request still succeeds, writing rows into
        a schema that is not the one the code believes it has."""
        self.h.engine.db.conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) - 1}")
        body = self.ready(503)
        self.assertEqual(body["status"], "unready")
        self.assertEqual(body["checks"]["migrations"], {"status": "fail", "reason": "migrations_behind"})

    def test_a_schema_ahead_of_the_build_is_unready(self) -> None:
        """The quieter half: the file was written by a newer build, so a rollback is about to write rows the
        newer schema will not read back the way it wrote them."""
        self.h.engine.db.conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
        self.assertEqual(self.ready(503)["checks"]["migrations"]["reason"], "migrations_ahead")

    def test_a_stalled_scheduler_is_unready(self) -> None:
        self.h.engine.scheduler_beat = time.monotonic() - health.STALL_SECONDS - 1
        self.assertEqual(self.ready(503)["checks"]["scheduler"]["reason"], "scheduler_stalled")

    def test_a_scheduler_that_ticked_recently_is_fine(self) -> None:
        self.h.engine.scheduler_beat = time.monotonic()
        self.assertEqual(self.ready()["checks"]["scheduler"], {"status": "ok"})

    def test_one_evaluation_serves_a_short_window(self) -> None:
        """The route is unauthenticated and its checks do real work -- a write transaction, and for S3 a
        request to the bucket. Without a window, holding down a reload makes the engine hammer its own object
        store on a stranger's behalf."""
        calls = []

        async def counted() -> dict:
            calls.append(1)
            return {"ok": True}

        self.h.engine.storage.check = counted
        for _ in range(5):
            self.ready()
        self.assertEqual(len(calls), 1)

    def test_storage_that_does_not_answer_is_unready(self) -> None:
        async def refuse() -> dict:
            raise OSError("bucket policy denies ListBucket")

        self.h.engine.storage.check = refuse
        self.assertEqual(self.ready(503)["checks"]["storage"]["reason"], "storage_unreachable")

    def patch_disk(self, free_mb: float, total_mb: float = 102400) -> None:
        free, total = free_mb * 1048576, total_mb * 1048576
        original = health.shutil.disk_usage
        self.addCleanup(lambda: setattr(health.shutil, "disk_usage", original))
        health.shutil.disk_usage = lambda _p: SimpleNamespace(total=total, used=total - free, free=free)

    def test_a_full_disk_fails_and_a_low_one_only_warns(self) -> None:
        self.patch_disk(free_mb=8)
        self.assertEqual(health.check_disk(self.h.engine).reason, "disk_critical")
        self.patch_disk(free_mb=5120)                                  # 5 GB, but only 5% of the volume
        self.assertEqual(health.check_disk(self.h.engine).reason, "disk_low")
        self.patch_disk(free_mb=51200)
        self.assertEqual(health.check_disk(self.h.engine).status, health.OK)

    def test_degraded_still_answers_200(self) -> None:
        """On purpose: an orchestrator restarts a container that answers anything else, and restarting a
        server because its disk is at 95% replaces a problem that can be fixed with one that cannot."""
        self.patch_disk(free_mb=5120)
        body = self.ready()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["checks"]["disk"], {"status": "warn", "reason": "disk_low"})


class WhatAProbeIsTold(unittest.TestCase):
    """The route is unauthenticated, because a container probe has no credentials. Everything it answers is
    therefore public, and has to be judged that way."""

    def setUp(self) -> None:
        self.h = Harness()

    def tearDown(self) -> None:
        self.h.close()

    def test_the_body_carries_no_path_version_or_measurement(self) -> None:
        self.h.engine.db.conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) - 1}")
        self.h.engine.scheduler_beat = time.monotonic() - 9999
        raw = json.dumps(self.h.api("GET", "/ready", expect=503))

        for leaked in (str(self.h.settings.data_dir), str(self.h.settings.db_path), VERSION, "MB", "free",
                       "schema", "sqlite", str(len(MIGRATIONS))):
            self.assertNotIn(leaked.lower(), raw.lower(), leaked)

    def test_every_reason_a_check_can_give_is_declared(self) -> None:
        """A reason code is published to anyone who can reach the port, so the set is closed on purpose and
        adding one is a decision about disclosure rather than a detail of the code that raises it."""
        engine = self.h.engine
        engine.db.conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) - 1}")
        engine.scheduler_beat = time.monotonic() - 9999
        gathered = {health.check_migrations(engine).reason, health.check_disk(engine).reason,
                    health.check_scheduler(engine, time.monotonic()).reason,
                    health.Check("database", health.FAIL, "database_readonly").reason}
        self.assertTrue({r for r in gathered if r} <= health.REASONS)

    def test_the_log_keeps_what_the_response_drops(self) -> None:
        """An operator needs the numbers; a probe does not get them. They are written once per change of
        state so a probe every second does not fill the log with the same line."""
        engine = self.h.engine
        engine.db.conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) - 1}")
        line = health.log_line(*self.h.client.portal.call(lambda: health.readiness(engine, time.monotonic())))

        self.assertIn("migrations_behind", line)
        self.assertIn(f"build expects {len(MIGRATIONS)}", line)   # the number the response refuses to carry
        self.assertIn("MB free", line)


class DatabaseWritability(unittest.TestCase):
    """A read proves nothing: a database on a full disk, on a read-only mount, or in a container whose volume
    was remounted answers every SELECT it is given."""

    def setUp(self) -> None:
        self.h = Harness()

    def tearDown(self) -> None:
        self.h.close()

    def mapping(self, message: str) -> health.Check:
        import contextlib

        @contextlib.contextmanager
        def refuse():
            raise sqlite3.OperationalError(message)
            yield

        self.h.engine.db.tx = refuse
        return health.check_database(self.h.engine)

    def test_sqlite_messages_map_to_declared_reasons(self) -> None:
        for message, status, reason in [
            ("attempt to write a readonly database", health.FAIL, "database_readonly"),
            ("disk I/O error: database or disk is full", health.FAIL, "disk_critical"),
            ("database is locked", health.WARN, "database_locked"),
            ("no such table: nonsense", health.FAIL, "database_error"),
        ]:
            with self.subTest(message=message):
                check = self.mapping(message)
                self.assertEqual((check.status, check.reason), (status, reason))

    def test_a_writable_database_passes(self) -> None:
        self.assertEqual(health.check_database(self.h.engine), health.Check("database", health.OK))
