"""E4 — how well the "unverified figures" check catches numbers that are not in the evidence.

After every final answer the engine lists figures that appear in no tool result or user message
(agent/harness.py, unverified_figures). This is the product's main defence against a model that invents or
mis-copies a financial figure, so its detection rate is measured here on generated answers whose correct and
fabricated figures are known.

Classes:
  copied      figures copied exactly from the evidence            -> should NOT be flagged (false-positive rate)
  reformatted the same figures as %, rounded to 0 dp, or in k/m    -> should NOT be flagged
  fabricated  figures at least 5% away from every evidence figure  -> SHOULD be flagged (detection rate)
  near_miss   wrong by 0.1%-0.45% (a mis-copied digit far right)    -> SHOULD be flagged
  small_count wrong counts/days/percent-points below 100           -> SHOULD be flagged
  user_echo   a wrong figure the user asserted, repeated            -> SHOULD be flagged (the user is not a source)

    python3 reports/ai-agent-audit/evals/e4_figure_check.py
"""
from __future__ import annotations

import random

from common import save

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "engine"))
from desk_engine.agent.harness import unverified_figures  # noqa: E402

SEED = 99
rng = random.Random(SEED)
N = 2000


def fmt(v: float) -> str:
    return f"{v:,.2f}"


def evidence() -> tuple[list[str], list[float]]:
    vals = [round(rng.uniform(1_000, 5_000_000), 2) for _ in range(rng.randint(3, 12))]
    text = "Account | Amount\n" + "\n".join(f"Line {i} | {fmt(v)}" for i, v in enumerate(vals))
    return [text], vals


def far_from(vals: list[float], lo: float = 1_000, hi: float = 5_000_000) -> float:
    while True:
        x = round(rng.uniform(lo, hi), 2)
        if all(abs(x - v) / v > 0.05 and abs(x - v * 100) / (v * 100) > 0.05 and abs(x - v / 100) / max(v / 100, 1e-9) > 0.05
               for v in vals):
            return x


def main() -> None:
    stats = {k: {"n": 0, "flagged": 0} for k in ("copied", "reformatted", "fabricated", "near_miss", "small_count", "user_echo")}
    examples: dict[str, list] = {k: [] for k in stats}
    for _ in range(N):
        ev, vals = evidence()
        v = rng.choice(vals)
        cases = {
            "copied": f"Revenue was {fmt(v)} this month.",
            "reformatted": rng.choice([f"Revenue was {v:,.0f}.", f"Revenue was {v / 1000:,.1f}k.",
                                       f"Revenue was {v / 1e6:,.2f}m." if v > 1e6 else f"Revenue was {v:,.0f}."]),
            "fabricated": f"Revenue was {fmt(far_from(vals))}.",
            "near_miss": f"Revenue was {fmt(round(v * (1 + rng.choice([-1, 1]) * rng.uniform(0.001, 0.0045)), 2))}.",
            "small_count": f"DSO is {rng.randint(20, 99)} days and {rng.randint(2, 40)} invoices are overdue.",
        }
        wrong = far_from(vals)
        cases["user_echo"] = (f"As you said, revenue was {fmt(wrong)}.", [*ev, f"I think revenue was {fmt(wrong)}"])
        for cls, item in cases.items():
            answer, evid = item if isinstance(item, tuple) else (item, ev)
            flagged = unverified_figures(answer, evid)
            # the small_count class: the evidence holds no such counts, so each one is unsupported
            stats[cls]["n"] += 1
            stats[cls]["flagged"] += bool(flagged)
            if len(examples[cls]) < 3:
                examples[cls].append({"answer": answer, "flagged": flagged})
    out = {}
    for cls, s in stats.items():
        rate = s["flagged"] / s["n"]
        should_flag = cls not in ("copied", "reformatted")
        out[cls] = {"n": s["n"], "flagged": s["flagged"], "rate": round(rate, 4),
                    "metric": "detection rate" if should_flag else "false-positive rate"}
        print(f"  {cls:12s} n={s['n']}  flagged={s['flagged']}  {out[cls]['metric']}: {rate:.1%}")
    # realistic evidence: the KPI scorecard text the agent actually receives for the E2 company
    from common import Harness, run_tool, tool_ctx
    from e2_ledger_grounding import build
    lines, _ = build()
    h = Harness()
    ws = h.setup_workspace(with_model=False)
    conn = h.api("POST", f"/api/w/{ws}/connections/custom", json={"label": "ERP", "mode": "push"})
    h.client.post("/api/ingest/v1/accounting/journal-lines", json={"lines": lines},
                  headers={"authorization": f"Bearer {conn['ingest_key']}"})
    card = run_tool(h, tool_ctx(h, ws), "financial_kpis", {"month": "2026-10"}).text
    h.close()
    import re as _re
    shown = [float(x.replace(",", "")) for x in _re.findall(r"-?\d[\d,]*\.?\d*", card)]
    real = {"n": 0, "flagged": 0}
    pcts = {"n": 0, "flagged": 0}
    for _ in range(N):
        # a fabricated margin or rate between 0% and 100%, at least 1 point away from every percentage shown
        while True:
            x = round(rng.uniform(0, 100), 1)
            if all(abs(x - s) >= 1 for s in shown):
                break
        pcts["n"] += 1
        pcts["flagged"] += bool(unverified_figures(f"The margin was {x}%.", [card]))
        # a fabricated amount between 1,000 and 1,000,000, at least 5% away from every figure shown
        while True:
            y = round(rng.uniform(1_000, 1_000_000), 2)
            if all(abs(y - s) / max(abs(s), 1) > 0.05 for s in shown if s):
                break
        real["n"] += 1
        real["flagged"] += bool(unverified_figures(f"Working capital was {y:,.2f}.", [card]))
    out["fabricated_pct_vs_scorecard"] = {**pcts, "rate": round(pcts["flagged"] / pcts["n"], 4), "metric": "detection rate",
                                          "evidence_numbers": len(shown)}
    out["fabricated_amount_vs_scorecard"] = {**real, "rate": round(real["flagged"] / real["n"], 4), "metric": "detection rate"}
    for k in ("fabricated_pct_vs_scorecard", "fabricated_amount_vs_scorecard"):
        print(f"  {k:30s} n={out[k]['n']} flagged={out[k]['flagged']} detection rate: {out[k]['rate']:.1%}")
    save("e4_figure_check", {"seed": SEED, "answers_per_class": N, "classes": out, "examples": examples})


if __name__ == "__main__":
    main()
