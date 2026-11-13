"""E10 — adversarial evaluation of the figure check: provenance-aware (new) against the audited check (old).

Evidence is the real tool output the agent receives for the synthetic E2 company (seed 7031): a monthly
query_dataset table, the KPI scorecard and a calculator result. Answers are generated from those outputs with a
fixed seed, in classes whose correct verdict is known by construction:

  must pass (valid)            copied amounts; amounts rounded or in k/m; copied percentages; copied ratios;
                               copied day counts; a difference, a share or a change of two figures the answer states
  must be the user's           a figure that appears only in the user's message
  must be flagged (unsupported) fabricated amounts; amounts mis-copied by 0.01%-0.45%; fabricated percentages;
                               a percentage that equals a ratio x 100 or a day count (cross-unit); wrong day counts;
                               a figure the user asserted (must not count as verified); wrong arithmetic on stated
                               figures; a result whose operands the answer does not state

A fabricated value is drawn so that it differs from every evidence figure of a compatible unit by more than the
rounding either side shows -- the only sense in which a number can be "not in the evidence".

    python3 reports/ai-agent-remediation/evals/e10_figure_provenance.py
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ai-agent-audit" / "evals"))
sys.path.insert(0, str(HERE))

from common import Harness, run_tool, tool_ctx  # noqa: E402
from e2_ledger_grounding import build  # noqa: E402

import baseline_figure_check as old  # noqa: E402
from desk_engine.agent import figures  # noqa: E402

SEED = 1113
N = 1500
rng = random.Random(SEED)
RESULTS = HERE.parent / "results"


def tool_outputs() -> list[str]:
    lines, _ = build()
    h = Harness()
    ws = h.setup_workspace(with_model=False)
    conn = h.api("POST", f"/api/w/{ws}/connections/custom", json={"label": "ERP", "mode": "push"})
    h.client.post("/api/ingest/v1/accounting/journal-lines", json={"lines": lines},
                  headers={"authorization": f"Bearer {conn['ingest_key']}"})
    ctx = tool_ctx(h, ws)
    ds = h.api("GET", f"/api/w/{ws}/datasets")
    ds = ds["datasets"] if isinstance(ds, dict) else ds
    q = run_tool(h, ctx, "query_dataset", {"dataset": ds[0]["name"], "group_by": [{"column": "date", "grain": "month"}],
                                           "measures": [{"column": "debit", "agg": "sum"}]}).text
    k = run_tool(h, ctx, "financial_kpis", {"month": "2026-10"}).text
    c = run_tool(h, ctx, "calc_loan_schedule", {"principal": 250000, "annual_rate": 0.065, "periods": 60}).text
    h.close()
    return [q, k, c]


def values(texts: list[str]) -> dict[str, list[float]]:
    """The evidence figures by unit, read independently of the code under test (simple, explicit patterns)."""
    joined = "\n".join(texts)
    return {
        "amount": [float(x.replace(",", "")) for x in re.findall(r"(?<![\w.%-])(\d{1,3}(?:,\d{3})+\.\d{2})(?![\d%×])", joined)],
        "percent": [float(x) for x in re.findall(r"(-?\d+\.\d)%", joined)],
        "ratio": [float(x) for x in re.findall(r"(\d+\.\d{2})×", joined)],
        "days": [float(x.replace(",", "")) for x in re.findall(r"(\d[\d,]*) days", joined)],
    }


def far(pool: list[float], lo: float, hi: float, gap: float, rel: bool = False, nd: int = 2) -> float:
    while True:
        x = round(rng.uniform(lo, hi), nd)
        if all(abs(x - v) > (abs(v) * gap if rel else gap) for v in pool):
            return x


def main() -> None:
    tools = tool_outputs()
    ev = values(tools)
    amounts, pcts, ratios, days = ev["amount"], ev["percent"], ev["ratio"], ev["days"]
    all_numbers = amounts + pcts + ratios + days
    cases: list[tuple[str, str, str, list[str]]] = []   # (class, expected, answer, user messages)

    def add(cls, expected, answer, users=()):
        cases.append((cls, expected, answer, list(users)))

    for _ in range(N):
        a, b = rng.sample(amounts, 2)
        p = rng.choice(pcts)
        add("valid: amount copied", "pass", f"Debits in the month were {a:,.2f}.")
        add("valid: amount rounded / k / m", "pass", rng.choice([f"About {a:,.0f}.", f"About {a / 1000:,.1f}k.",
                                                                f"About {a / 1e6:,.2f}m."]))
        add("valid: percentage copied", "pass", f"The margin was {p:.1f}%.")
        if ratios:
            add("valid: ratio copied", "pass", f"The current ratio is {rng.choice(ratios):.2f}x.")
        if days:
            add("valid: day count copied", "pass", f"DSO is {rng.choice(days):,.0f} days.")
        add("valid: derived from stated figures", "pass",
            rng.choice([f"{a:,.2f} against {b:,.2f}, a difference of {a - b:,.2f}.",
                        f"{a:,.2f} against {b:,.2f}, a change of {(a - b) / abs(b) * 100:.1f}%.",
                        f"{a:,.2f} and {b:,.2f} make {a + b:,.2f}, of which the first is {a / (a + b) * 100:.1f}%."]))
        u = far(all_numbers, 1000, 900000, 0.05, rel=True)
        add("user's own figure", "user", f"Using your budget of {u:,.2f}, the debits are {a:,.2f}.",
            [f"Our budget for the month is {u:,.2f}."])
        add("unsupported: fabricated amount", "flag", f"Debits were {far(all_numbers, 1000, 900000, 0.05, rel=True):,.2f}.")
        add("unsupported: amount mis-copied by 0.01%-0.45%", "flag",
            f"Debits were {round(a * (1 + rng.choice([-1, 1]) * rng.uniform(0.0001, 0.0045)), 2):,.2f}.")
        add("unsupported: fabricated percentage", "flag",
            f"The margin was {far(pcts + [r * 100 for r in ratios] + days, 0, 100, 0.15, nd=1):.1f}%.")
        if ratios:
            r = rng.choice(ratios)
            add("unsupported: cross-unit percentage (ratio x 100)", "flag", f"The margin was {r * 100 + rng.choice([-0.2, 0.2]):.1f}%."
                if any(abs(r * 100 - q) <= 0.15 for q in pcts) else f"The margin was {r * 100:.1f}%.")
        if days:
            d = rng.choice(days)
            if all(abs(d - q) > 0.05 for q in pcts):
                add("unsupported: cross-unit percentage (day count)", "flag", f"The margin was {d:.1f}%.")
        add("unsupported: wrong day count", "flag",
            f"DSO is {int(far(all_numbers + [x * 100 for x in ratios], 5, 99, 0.6, nd=0))} days.")
        w = far(all_numbers, 1000, 900000, 0.05, rel=True)
        add("unsupported: user-asserted figure repeated", "user-not-verified", f"As you said, revenue was {w:,.2f}.",
            [f"I think revenue was {w:,.2f}."])
        add("unsupported: wrong arithmetic on stated figures", "flag",
            f"{a:,.2f} against {b:,.2f}, a difference of {(a - b) * rng.choice([1.01, 0.99]) + rng.choice([-7.13, 7.13]):,.2f}.")
        add("unsupported: result of figures the answer does not state", "flag",
            f"Total debits for the two months were {a + b:,.2f}." if all(abs(a + b - v) > 0.01 for v in amounts) else
            f"Debits were {far(all_numbers, 1000, 900000, 0.05, rel=True):,.2f}.")

    def verdict_new(answer: str, users: list[str]) -> str:
        r = figures.report(answer, tools, users, [])
        if r["unverified_figures"]:
            return "flag"
        if r["user_figures"]:
            return "user"
        return "pass"

    def verdict_old(answer: str, users: list[str]) -> str:
        return "flag" if old.unverified_figures(answer, tools + users) else "pass"

    stats: dict[str, dict] = {}
    for cls, expected, answer, users in cases:
        s = stats.setdefault(cls, {"expected": expected, "n": 0, "new_ok": 0, "old_ok": 0, "new_examples": []})
        s["n"] += 1
        vn, vo = verdict_new(answer, users), verdict_old(answer, users)
        good_new = vn == expected or (expected == "user-not-verified" and vn in ("user", "flag"))
        good_old = vo == ("pass" if expected == "pass" else "flag") if expected != "user" else vo == "pass"
        s["new_ok"] += good_new
        s["old_ok"] += good_old
        if not good_new and len(s["new_examples"]) < 3:
            s["new_examples"].append({"answer": answer, "got": vn, "report": figures.report(answer, tools, users, [])})

    def confusion(which: str) -> dict:
        tp = fn = fp = tn = 0
        for cls, s in stats.items():
            bad = s["expected"] in ("flag", "user-not-verified")
            ok = s[f"{which}_ok"]
            if bad:
                tp += ok
                fn += s["n"] - ok
            elif s["expected"] == "pass":
                tn += ok
                fp += s["n"] - ok
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        return {"true_positives": tp, "false_negatives": fn, "false_positives": fp, "true_negatives": tn,
                "precision": None if prec is None else round(prec, 4), "recall": None if rec is None else round(rec, 4),
                "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else None,
                "false_negative_rate": round(fn / (fn + tp), 4) if fn + tp else None}

    out = {"seed": SEED, "answers": len(cases), "evidence_figures": {k: len(v) for k, v in ev.items()},
           "by_class": {k: {"expected": v["expected"], "n": v["n"], "new_correct_rate": round(v["new_ok"] / v["n"], 4),
                            "old_correct_rate": round(v["old_ok"] / v["n"], 4), "new_failures": v["new_examples"]}
                        for k, v in stats.items()},
           "new": confusion("new"), "old": confusion("old")}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "e10_figure_provenance.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"E10: {len(cases)} answers; evidence figures {out['evidence_figures']}")
    print(f"{'class':58s} {'expected':18s} {'new':>7s} {'old':>7s}")
    for k, v in out["by_class"].items():
        print(f"{k:58s} {v['expected']:18s} {v['new_correct_rate']:7.1%} {v['old_correct_rate']:7.1%}")
    for w in ("new", "old"):
        print(w, json.dumps(out[w]))


if __name__ == "__main__":
    main()
