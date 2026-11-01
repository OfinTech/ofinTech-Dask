"""Embedded gateway: usage rows per run, daily limits, the entitlement hook (threat T11)."""

from __future__ import annotations

import unittest

from desk_engine.embedded.metering import Decision
from tests.embedded_helpers import issue_session, last, make_org, new_harness, run_input, stream


class Metering(unittest.TestCase):
    def setUp(self) -> None:
        self.h = new_harness(script=[[("calc_growth", {"values": [1, 2]})], "Doubled."] * 10)
        self.org = make_org(self.h, email="o@acme.test", limits={"runs_per_day": 2, "runs_per_minute": 100, "runs_per_minute_session": 100})

    def tearDown(self) -> None:
        self.h.close()

    def test_usage_rows_and_summary(self) -> None:
        stream(self.org, run_input("a", run="r1"))
        issue_session(self.org, "user-2")
        stream(self.org, run_input("b", run="r2"))
        rows = self.h.engine.db.all("SELECT * FROM embedded_usage ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["status"] for r in rows}, {"done"})
        self.assertEqual(rows[0]["tool_calls"], 1)
        self.assertGreater(rows[0]["input_tokens"], 0)
        self.assertNotEqual(rows[0]["subject_hash"], rows[1]["subject_hash"])
        self.assertTrue(all(r["agent_id"] == "finance-copilot" for r in rows))
        summary = self.org.admin_api("GET", f"/api/w/{self.org.ws}/embedded/integrations/{self.org.integration['id']}/usage?days=7")
        self.assertEqual(summary["total"]["runs"], 2)
        self.assertEqual(summary["today"]["runs"], 2)
        self.assertEqual(summary["rows"][0]["subjects"], 2)
        # usage is per workspace: another workspace's owner cannot read it
        other = make_org(self.h, email="o@globex.test", name="Globex", origins=["https://g.test"], integration=False)
        r = self.h.client.get(f"/api/w/{other.ws}/embedded/integrations/{self.org.integration['id']}/usage", headers=other.owner())
        self.assertEqual(r.status_code, 404)

    def test_daily_run_limit_enforced(self) -> None:
        stream(self.org, run_input("a", run="r1"))
        stream(self.org, run_input("b", run="r2"))
        with self.assertRaises(AssertionError) as ctx:
            stream(self.org, run_input("c", run="r3"))
        self.assertIn("entitlement_exceeded", str(ctx.exception))
        denied = self.h.engine.db.all("SELECT detail FROM audit_log WHERE action = 'embedded.denied'")
        self.assertTrue(any("entitlement_exceeded" in d["detail"] for d in denied))
        # raising the limit through the admin API takes effect at once
        self.org.admin_api("PATCH", f"/api/w/{self.org.ws}/embedded/integrations/{self.org.integration['id']}", json={"limits": {"runs_per_day": 5}})
        self.assertEqual(last(stream(self.org, run_input("c", run="r3")))["type"], "RUN_FINISHED")
        # the input cap cannot be raised above the gateway's ceiling
        row = self.org.admin_api("PATCH", f"/api/w/{self.org.ws}/embedded/integrations/{self.org.integration['id']}",
                                 json={"limits": {"max_input_chars": 100000}})
        self.assertEqual(row["limits"]["max_input_chars"], 8000)
        r = self.h.client.patch(f"/api/w/{self.org.ws}/embedded/integrations/{self.org.integration['id']}", headers=self.org.owner(),
                                json={"limits": {"billing_tier": 3}})
        self.assertEqual(r.status_code, 422)

    def test_entitlement_hook_is_consulted(self) -> None:
        calls = []

        class Plan:
            def check_run(self, principal, agent_id):
                calls.append((principal.integration["id"], agent_id))
                return Decision(False, "plan_required", "Upgrade the plan to use this agent.")

        self.h.engine.embedded.entitlements = Plan()
        with self.assertRaises(AssertionError) as ctx:
            stream(self.org, run_input("a", run="r1"))
        self.assertIn("plan_required", str(ctx.exception))
        self.assertEqual(calls, [(self.org.integration["id"], "finance-copilot")])
        self.assertEqual(self.h.engine.db.scalar("SELECT COUNT(*) FROM agent_runs"), 0)   # nothing started


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
