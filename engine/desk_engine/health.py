"""Readiness: the few things a probe may be told without being told anything it should not know.

``/health`` answers whether the process is alive. ``/ready`` answers whether it can do its work, which is a
different question and the one that matters. A server whose disk is full, whose schema is an older version
than the code expects, or whose background scheduler stopped ticking answers ``/health`` perfectly while
getting everything else wrong -- and the first person to notice is the customer.

A check returns a status and a **reason code**. Never a path, a host name, a free-byte count, a version or any
other value: ``/ready`` is unauthenticated, because a container probe has no credentials, so everything it
says is public. An operator still needs the numbers, so they go to the log, once, when a check changes state.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .db import MIGRATIONS

if TYPE_CHECKING:
    from .app import Engine

OK, WARN, FAIL = "ok", "warn", "fail"

# A tick is three seconds, so a minute of silence is twenty missed ones: long enough that a slow tick is not an
# alarm, short enough that a wedged `await` is caught before the queue behind it matters.
STALL_SECONDS = 60.0

# How long one evaluation serves. The route is unauthenticated and the checks do real work -- a write
# transaction, and for S3 a request to the bucket -- so without this, anyone who can reach the port can make
# the engine hammer its own object store by holding down a reload. A probe every second then costs an
# evaluation every two, which is still far faster than any of these failures appear.
CACHE_SECONDS = 2.0
DISK_CRITICAL_MB, DISK_LOW_MB = 64, 512
DISK_CRITICAL_RATIO, DISK_LOW_RATIO = 0.02, 0.10

# Every reason a check may give. Closed on purpose: a reason code is published to anyone who can reach the
# port, so adding one is a decision about what to disclose, not a detail of the code that raises it.
REASONS = frozenset({
    "migrations_behind", "migrations_ahead",
    "database_readonly", "database_locked", "database_error",
    "disk_low", "disk_critical", "disk_unknown",
    "storage_unreachable",
    "scheduler_stalled",
})


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    reason: str | None = None   # a code from REASONS, never a value
    detail: str = ""            # numbers and messages for the log only; never in the response


def check_migrations(engine: Engine) -> Check:
    """The schema the database carries against the schema this build expects.

    Behind means a deploy applied code before its migrations, or that a start-up failed halfway. Ahead is the
    worse one and the quieter one: the file was written by a newer build, so this process is about to write
    rows that the newer schema will not read back the way it wrote them. Neither is visible from outside, and
    neither makes a single request fail until data is already wrong.
    """
    expected = len(MIGRATIONS)
    try:
        found = engine.db.conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        return Check("migrations", FAIL, "database_error", str(exc))
    if found < expected:
        return Check("migrations", FAIL, "migrations_behind", f"schema at {found}, build expects {expected}")
    if found > expected:
        return Check("migrations", FAIL, "migrations_ahead", f"schema at {found}, build expects {expected}")
    return Check("migrations", OK, detail=f"schema {found}")


def check_database(engine: Engine) -> Check:
    """Takes a write transaction and rolls it back. Reading proves nothing: a database on a full disk, on a
    read-only mount, or in a container whose volume was remounted still answers every SELECT."""
    try:
        with engine.db.tx():
            pass
    except sqlite3.OperationalError as exc:
        text = str(exc).lower()
        if "readonly" in text or "read-only" in text:
            return Check("database", FAIL, "database_readonly", str(exc))
        if "disk" in text or "full" in text:
            return Check("database", FAIL, "disk_critical", str(exc))
        if "locked" in text or "busy" in text:
            return Check("database", WARN, "database_locked", str(exc))
        return Check("database", FAIL, "database_error", str(exc))
    except sqlite3.Error as exc:
        return Check("database", FAIL, "database_error", str(exc))
    return Check("database", OK)


def check_disk(engine: Engine) -> Check:
    """Free space where the database and the files live. SQLite does not fail early on a filling disk: it
    works until the write that does not fit, and that write is somebody's document."""
    try:
        usage = shutil.disk_usage(engine.settings.data_dir)
    except OSError as exc:
        return Check("disk", WARN, "disk_unknown", str(exc))
    free_mb = usage.free / 1048576
    ratio = usage.free / usage.total if usage.total else 0.0
    detail = f"{free_mb:.0f} MB free of {usage.total / 1048576:.0f} MB ({ratio:.1%})"
    if free_mb < DISK_CRITICAL_MB or ratio < DISK_CRITICAL_RATIO:
        return Check("disk", FAIL, "disk_critical", detail)
    if free_mb < DISK_LOW_MB or ratio < DISK_LOW_RATIO:
        return Check("disk", WARN, "disk_low", detail)
    return Check("disk", OK, detail=detail)


async def check_storage(engine: Engine) -> Check:
    """Whether the object store answers. Local storage checks the folder; S3 checks the bucket, which is the
    case worth having -- a bucket policy changed after deployment fails no request until a file is written."""
    try:
        info = await engine.storage.check()
    except Exception as exc:  # noqa: BLE001 - a backend defines its own failures (HTTP, OS, auth);
        # whichever one it raises, the answer to "can this engine store a file" is no
        return Check("storage", FAIL, "storage_unreachable", f"{type(exc).__name__}: {exc}")
    return Check("storage", OK, detail=str(info))


def check_scheduler(engine: Engine, monotonic: float) -> Check:
    """How long since the background loop last came round. It carries webhook delivery, workflow ticks, Drive
    syncs and the WAL checkpoint, and it is wrapped in a try/except that keeps it alive through any exception
    -- so the way it dies is not an exception but an `await` that never returns, which nothing else notices."""
    beat = getattr(engine, "scheduler_beat", None)
    if beat is None:
        return Check("scheduler", OK, detail="not started")
    age = monotonic - beat
    if age > STALL_SECONDS:
        return Check("scheduler", FAIL, "scheduler_stalled", f"{age:.0f}s since the last tick")
    return Check("scheduler", OK, detail=f"{age:.0f}s since the last tick")


async def readiness(engine: Engine, monotonic: float) -> tuple[str, list[Check]]:
    """Every check, and the overall state: the worst of them.

    A warning answers 200. An orchestrator restarts a container that answers anything else, and restarting a
    server because its disk is at 85% replaces a problem that can be fixed with one that cannot.
    """
    checks = [
        check_migrations(engine),
        check_database(engine),
        check_disk(engine),
        await check_storage(engine),
        check_scheduler(engine, monotonic),
    ]
    if any(c.status == FAIL for c in checks):
        return "unready", checks
    if any(c.status == WARN for c in checks):
        return "degraded", checks
    return "ready", checks


def response_body(status: str, checks: list[Check]) -> dict[str, Any]:
    """What goes over the wire: the overall state and a code per check. No values, no paths, no versions."""
    return {"status": status,
            "checks": {c.name: ({"status": c.status, "reason": c.reason} if c.reason else {"status": c.status})
                       for c in checks}}


def log_line(status: str, checks: list[Check]) -> str:
    """What goes to the log when the state changes: the same checks with the numbers attached."""
    parts = [f"{c.name}={c.status}" + (f"({c.reason}: {c.detail})" if c.reason else (f"({c.detail})" if c.detail else ""))
             for c in checks]
    return f"readiness {status}; " + " ".join(parts)
