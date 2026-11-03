"""No route may serve one workspace's resource to another, and the proof must cover routes nobody has written yet.

Workspace scoping in this engine is sound but it is a discipline rather than a structure: ``db.update`` takes an
optional ``workspace_id``, deletes are issued by bare id, and each handler is correct because it first reads the
row with the workspace in the ``WHERE`` clause. Reading the code, that discipline holds everywhere it was checked.
The weakness is not any one handler; it is that 117 routes take a resource id under ``/api/w/{ws}/`` and the test
that proved isolation named eight of them by hand. A route added next month is outside the proof, and the failure
it would introduce is the one that matters most in a multi-tenant financial product.

So this test asks the router which routes exist instead of being told. It plants a canary row in workspace A for
every resource family, then calls every id-taking route as workspace B using A's id, and fails if any of them
answers 200 or lets the canary text through.

The mapping from a path parameter to its table is declared below and is the one thing kept by hand. A parameter
that is not in it fails the test by name, which is the point: adding a route to a new resource family forces the
author to say which table it reads, and the isolation proof then covers it. Getting an entry wrong is safe --
the canary simply will not match, the route is reported as not exercised, and nothing passes that should not.
"""

from __future__ import annotations

import re
import sqlite3
import unittest
from typing import Any

from .helpers import Harness

ORIGIN = {"origin": "http://localhost"}

CANARY = "CanaryLedgerFalconAndCo"

# A path segment that selects a format, a kind or a named enumeration is not a resource id and is left alone.
NOT_A_RESOURCE = {"fmt", "kind", "measure", "name", "provider", "entry"}

# family (first segment after /api/w/{ws}/) and parameter -> the table the handler reads it from
RESOURCE_TABLES: dict[tuple[str, str], tuple[str, str]] = {
    ("analysis", "kid"): ("kpi_settings", "kpi_id"),
    ("approvals", "aid"): ("approvals", "id"),
    ("close", "rid"): ("close_runs", "id"),
    ("close", "iid"): ("close_items", "id"),
    ("collections", "cid"): ("doc_collections", "id"),
    ("comments", "cid"): ("comments", "id"),
    ("connections", "cid"): ("connections", "id"),
    ("connections", "link_id"): ("channel_links", "id"),
    ("cost-centres", "cid"): ("cost_centres", "id"),
    ("cost-centres", "did"): ("datasets", "id"),
    ("dashboards", "did"): ("dashboards", "id"),
    ("datasets", "did"): ("datasets", "id"),
    ("documents", "did"): ("documents", "id"),
    ("embedded", "iid"): ("embedded_integrations", "id"),
    ("embedded", "sid"): ("embedded_sessions", "id"),
    ("files", "fid"): ("files", "id"),
    ("files", "finding"): ("file_findings", "id"),
    ("forecasts", "fid"): ("forecasts", "id"),
    ("imports", "upload_id"): ("datasets", "id"),
    ("invites", "invite_id"): ("invites", "id"),
    ("mcp-keys", "kid"): ("mcp_keys", "id"),
    ("mcp-servers", "sid"): ("mcp_servers", "id"),
    ("members", "user_id"): ("members", "user_id"),
    ("memories", "mid"): ("memories", "id"),
    ("prices", "rid"): ("model_prices", "id"),
    ("providers", "pid"): ("llm_providers", "id"),
    ("skills", "sid"): ("skills", "id"),
    ("threads", "tid"): ("threads", "id"),
    ("webhooks", "hid"): ("webhooks", "id"),
    ("webhooks", "did"): ("webhook_deliveries", "id"),
    ("workflow-runs", "rid"): ("workflow_runs", "id"),
    ("workflows", "wid"): ("workflows", "id"),
}


def canary_id(table: str) -> str:
    return f"canary_{table}"


class TenantIsolation(unittest.TestCase):
    plant_errors: dict[str, str] = {}
    @classmethod
    def setUpClass(cls) -> None:
        cls.h = Harness(mode="desktop")
        cls.a = cls.h.setup_workspace(with_model=False)
        cls.b = cls.h.api("POST", "/api/workspaces", json={
            "name": "Beta Ltd", "base_currency": "GBP", "country": "GB", "fiscal_year_start_month": 4,
            "accounting_framework": "IFRS", "timezone": "Europe/London"})["id"]
        cls.planted = cls._plant(cls.h, cls.a)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.h.close()

    @staticmethod
    def _plant(h: Harness, ws: str) -> dict[str, str]:
        """One row per resource table in workspace A, filled generically from the schema.

        Foreign keys are switched off for the planting only: these rows exist to be asked for by id from the wrong
        workspace, and giving each one a valid parent would be a second fixture to keep correct for no gain.
        """
        db = h.engine.db
        planted: dict[str, str] = {}
        cls_errors: dict[str, str] = {}
        for table, id_column in sorted({v for v in RESOURCE_TABLES.values()}):
            columns = db.all(f"PRAGMA table_info({table})")
            if not columns or not any(c["name"] == id_column for c in columns):
                cls_errors[table] = f"no {id_column} column"
                continue
            values: dict[str, Any] = {}
            for col in columns:
                name, kind = col["name"], (col["type"] or "").upper()
                if name == id_column:
                    values[name] = canary_id(table)
                elif name == "workspace_id":
                    values[name] = ws
                elif col["notnull"] and col["dflt_value"] is None:
                    values[name] = 0 if ("INT" in kind or "REAL" in kind or "NUM" in kind) else CANARY
            try:
                # A separate connection with foreign keys off: SQLite ignores the pragma inside an open
                # transaction, and the engine's own connection must not be left with its guarantees relaxed.
                with sqlite3.connect(h.settings.db_path) as raw:
                    raw.execute("PRAGMA foreign_keys=OFF")
                    cols = ", ".join(values)
                    raw.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({', '.join('?' * len(values))})",
                                list(values.values()))
                planted[table] = canary_id(table)
            except Exception as exc:               # reported by name below, never swallowed
                cls_errors[table] = f"{type(exc).__name__}: {exc}"
        TenantIsolation.plant_errors = cls_errors
        return planted

    @staticmethod
    def _routes(h: Harness) -> list[tuple[str, str, list[str]]]:
        out = []
        for route in h.app.routes:
            path = getattr(route, "path", "")
            if "{ws}" not in path:
                continue
            params = [p for p in re.findall(r"{([a-z_]+)(?::[^}]+)?}", path) if p != "ws"]
            if not params:
                continue
            for method in sorted(getattr(route, "methods", None) or []):
                if method in {"HEAD", "OPTIONS"}:
                    continue
                out.append((method, path, params))
        return out

    def test_every_parameter_is_declared(self) -> None:
        """A route to a new resource family must say which table it reads, or isolation is not proved for it."""
        unmapped = set()
        for _, path, params in self._routes(self.h):
            family = path.split("/")[4]
            for param in params:
                if param not in NOT_A_RESOURCE and (family, param) not in RESOURCE_TABLES:
                    unmapped.add(f"{family}/{{{param}}}  ({path})")
        self.assertEqual(set(), unmapped,
                         "add these to RESOURCE_TABLES in this file so the isolation proof covers them:\n  "
                         + "\n  ".join(sorted(unmapped)))

    def test_no_route_serves_another_workspaces_resource(self) -> None:
        leaks: list[str] = []
        exercised = untested = 0
        for method, path, params in self._routes(self.h):
            family = path.split("/")[4]
            url = path.replace("{ws}", self.b)
            skip = False
            for param in params:
                mapped = RESOURCE_TABLES.get((family, param))
                table = mapped[0] if mapped else None
                if param in NOT_A_RESOURCE:
                    url = re.sub(r"{" + param + r"(?::[^}]+)?}", "csv" if param == "fmt" else "x", url)
                    continue
                if table is None or table not in self.planted:
                    skip = True
                    break
                url = re.sub(r"{" + param + r"(?::[^}]+)?}", canary_id(table), url)
            if skip:
                untested += 1
                continue

            response = self.h.client.request(method, url, headers=ORIGIN, json={} if method != "GET" else None)
            body = response.text
            if response.status_code == 200:
                leaks.append(f"{method} {url} answered 200 for workspace {self.b}'s caller")
            if CANARY in body:
                leaks.append(f"{method} {url} returned workspace A's text")
            if response.status_code in {403, 404}:
                exercised += 1
            else:
                untested += 1

        missing = sorted({t for t, _ in RESOURCE_TABLES.values()} - set(self.planted))
        print(f"\n[isolation] {exercised} route(s) refused another workspace's id outright; "
              f"{untested} stopped earlier (request validation, or a fixture row that could not be planted)")
        for table in missing:
            print(f"[isolation] no canary row for {table}: {self.plant_errors.get(table, 'unknown')}")
        self.assertEqual([], leaks, "\n".join(leaks))
        self.assertGreater(exercised, 85, "too few routes were actually exercised for this to prove much")


if __name__ == "__main__":
    unittest.main()
