"""F1 — the executive dashboard and the agent's KPI tool give the same figures, for every month of the demo ledger.

The dashboard (GET /overview) and the agent's financial_kpis tool are two routes to one evaluator; this checks they
agree to the last digit, KPI by KPI and month by month, on the synthetic HarborLine demo company. Run from engine/:

    python3 ../reports/ai-agent-production-readiness/evals/f1_dashboard_vs_agent.py [out.json]
"""
from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, ".")
from desk_engine.agent.tools import ToolContext, registry  # noqa: E402
from tests.helpers import Harness  # noqa: E402
from tests.test_overview import GL  # noqa: E402


def main() -> None:
    h = Harness()
    ws = h.setup_workspace()
    h.api("POST", f"/api/w/{ws}/cost-centres/ledger/upload", files={"file": ("GL.xlsx", GL.read_bytes())})
    months = h.api("GET", f"/api/w/{ws}/overview")["months"]
    ctx = ToolContext(h.engine, h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (ws,)),
                      h.engine.db.scalar("SELECT id FROM users WHERE is_local = 1"))
    tool = registry.get_static("financial_kpis")
    compared, mismatches = 0, []
    for m in months:
        ov = h.api("GET", f"/api/w/{ws}/overview?month={m}")
        out = asyncio.run(tool.run(ctx, {"month": m}))
        agent = {k["id"]: k["value"] for k in out.data["kpis"]}       # what the agent's tool returned
        for kid, v in ov["kpis"].items():
            if kid in agent:
                compared += 1
                if v["value"] != agent[kid]:
                    mismatches.append({"month": m, "kpi": kid, "dashboard": v["value"], "agent": agent[kid]})
    h.close()
    res = {"months": len(months), "values_compared": compared, "mismatches": len(mismatches),
           "examples": mismatches[:10]}
    print(json.dumps(res))
    if len(sys.argv) > 1:
        json.dump(res, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
