"""Shutting down while worker threads are still reading must not close their connections under them (a closed
SQLite connection used by another thread crashes the process). Run in a subprocess, so a crash fails the test
instead of killing the test run."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

SCRIPT = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, ".")
    from unittest import mock
    from desk_engine.db import Database
    from tests.helpers import Harness

    orig_all = Database.all
    def slow_all(self, sql, params=()):
        # time spent inside SQLite itself (a big table), where closing the connection from another thread crashes
        self.conn.execute("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c LIMIT 200000) "
                          "SELECT count(*) FROM c").fetchone()
        return orig_all(self, sql, params)

    for i in range(25):
        h = Harness()
        ws = h.setup_workspace()
        t = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
        h.llm.script = [[("calc_growth", {"values": [1, 2]})], "Done."]
        with mock.patch.object(Database, "all", slow_all):
            h.api("POST", f"/api/w/{ws}/threads/{t}/messages", json={"text": "g"})
            time.sleep(0.01 * (i % 7))          # shut down at different points of the run
            h.close()
    print("survived")
''')


class ShutdownWhileThreadsRead(unittest.TestCase):
    def test_no_crash(self) -> None:
        r = subprocess.run([sys.executable, "-X", "faulthandler", "-c", SCRIPT], cwd=Path(__file__).parents[1],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, (r.stdout[-500:], r.stderr[-3000:]))
        self.assertIn("survived", r.stdout)


if __name__ == "__main__":
    unittest.main()
