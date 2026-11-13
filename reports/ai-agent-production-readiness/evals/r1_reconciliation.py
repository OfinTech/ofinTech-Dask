"""R1 — the close's bank reconciliation (close/statement.py) against independently specified expected matches.

Synthetic statement and ledger lines only. Each case states the pairs a correct reconciliation must produce under the
product's documented rule (same amount to the cent, dates at most five days apart, nearest date first) and the
exceptions it must report. Run from engine/:

    python3 ../reports/ai-agent-production-readiness/evals/r1_reconciliation.py [out.json]
"""
from __future__ import annotations

import json
import random
import sys

sys.path.insert(0, ".")
from desk_engine.close.statement import reconcile, statement_proposals  # noqa: E402,F401


def run(stmt, ledger, closing=None, book=0.0):
    lines = [{"row": i, "date": d, "debit": max(a, 0), "credit": max(-a, 0), "entry": f"E{i}", "description": t}
             for i, d, a, t in ledger]
    st = {"lines": [{"id": f"s{i}", "date": d, "amount": a, "description": t} for i, d, a, t in stmt]}
    return reconcile(st, lines, month="2026-10", month_end="2026-10-31", account={"code": "1000"},
                     book_balance=book, closing_balance=closing)


CASES = [
    ("exact match", [(1, "2026-10-05", -1200.0, "Rent")], [(1, "2026-10-05", -1200.0, "Rent")],
     {"s1": "l1"}, [], []),
    ("date offset within 5 days", [(1, "2026-10-09", 850.0, "Receipt")], [(1, "2026-10-04", 850.0, "Invoice 77")],
     {"s1": "l1"}, [], []),
    ("date offset of 6 days is an exception", [(1, "2026-10-11", 850.0, "Receipt")],
     [(1, "2026-10-05", 850.0, "Invoice 78")], {}, ["s1"], ["l1"]),
    ("amount differs by one cent", [(1, "2026-10-07", -99.99, "Fee")], [(1, "2026-10-07", -100.0, "Fee")],
     {}, ["s1"], ["l1"]),
    ("duplicate posting in the ledger", [(1, "2026-10-12", -560.0, "Supplier A")],
     [(1, "2026-10-12", -560.0, "Supplier A"), (2, "2026-10-12", -560.0, "Supplier A (again)")],
     {"s1": "l1"}, [], ["l2"]),
    ("duplicate line on the statement", [(1, "2026-10-12", -560.0, "Supplier A"), (2, "2026-10-12", -560.0, "dup")],
     [(1, "2026-10-12", -560.0, "Supplier A")], {"s1": "l1"}, ["s2"], []),
    ("ambiguous: two candidates, nearest wins", [(1, "2026-10-10", -300.0, "Card")],
     [(1, "2026-10-08", -300.0, "Card A"), (2, "2026-10-11", -300.0, "Card B")], {"s1": "l2"}, [], ["l1"]),
    ("nearest-first must not cost a match", [(1, "2026-10-03", -100.0, "a"), (2, "2026-10-08", -100.0, "b")],
     [(1, "2026-10-01", -100.0, "a"), (2, "2026-10-04", -100.0, "b")], {"s1": "l1", "s2": "l2"}, [], []),
    ("bank charge only on the statement", [(1, "2026-10-31", -12.5, "Monthly fee")], [], {}, ["s1"], []),
    ("cheque not yet cleared", [], [(1, "2026-10-29", -2000.0, "Cheque 1042")], {}, [], ["l1"]),
    ("other month ignored", [(1, "2026-09-30", -50.0, "Sept")], [(1, "2026-10-01", -50.0, "Oct")], {}, [], ["l1"]),
]


def generated(n: int, seed: int = 20261114):
    """Random months: unique amounts so the expected pairs are known by construction, plus noise on both sides."""
    rnd = random.Random(seed)
    out = []
    for k in range(n):
        stmt, ledger, exp, s_only, l_only = [], [], {}, [], []
        amounts = rnd.sample(range(1000, 900000), 40)
        i = 0
        for a in amounts:
            i += 1
            amt = round(a / 100 * rnd.choice((1, -1)), 2)
            day = rnd.randint(6, 25)
            kind = rnd.random()
            if kind < 0.7:                              # cleared, within the window
                stmt.append((i, f"2026-10-{day + rnd.randint(0, 5):02d}", amt, "x"))
                ledger.append((i, f"2026-10-{day:02d}", amt, "x"))
                exp[f"s{i}"] = f"l{i}"
            elif kind < 0.8:                            # cleared too late to match
                stmt.append((i, f"2026-10-{min(31, day + 6):02d}", amt, "x"))
                ledger.append((i, f"2026-10-{day:02d}", amt, "x"))
                s_only.append(f"s{i}")
                l_only.append(f"l{i}")
            elif kind < 0.9:                            # statement only
                stmt.append((i, f"2026-10-{day:02d}", amt, "x"))
                s_only.append(f"s{i}")
            else:                                       # ledger only
                ledger.append((i, f"2026-10-{day:02d}", amt, "x"))
                l_only.append(f"l{i}")
        out.append((f"generated month {k}", stmt, ledger, exp, s_only, l_only))
    return out


def main() -> None:
    rows, pairs_total, pairs_right, exc_total, exc_right = [], 0, 0, 0, 0
    for name, stmt, ledger, exp, s_only, l_only in CASES + generated(200):
        r = run(stmt, ledger)
        got = {m["statement"]["id"]: m["ledger"]["id"] for m in r["matched_pairs"]}
        gs, gl = sorted(s["id"] for s in r["statement_only"]), sorted(b["id"] for b in r["ledger_only"])
        ok = got == exp and gs == sorted(s_only) and gl == sorted(l_only)
        pairs_total += len(exp)
        pairs_right += sum(got.get(k) == v for k, v in exp.items())
        exc_total += len(s_only) + len(l_only)
        exc_right += len(set(gs) & set(s_only)) + len(set(gl) & set(l_only))
        false_matches = sum(1 for k, v in got.items() if exp.get(k) != v)
        rows.append({"case": name, "pass": ok, "false_matches": false_matches,
                     **({} if ok else {"expected": exp, "got": got, "statement_only": gs, "ledger_only": gl})})
    # the reconciliation identity, with a closing balance
    r = run([(1, "2026-10-05", -1200.0, "Rent"), (2, "2026-10-31", -12.5, "Fee")],
            [(1, "2026-10-05", -1200.0, "Rent"), (2, "2026-10-29", -2000.0, "Cheque")], closing=8787.5, book=6800.0)
    identity = {"difference": r["difference"], "expected": 0.0, "pass": r["difference"] == 0.0}
    out = {"cases": len(rows), "passed": sum(x["pass"] for x in rows),
           "pair_recall": round(pairs_right / max(1, pairs_total), 4),
           "false_matches": sum(x["false_matches"] for x in rows),
           "exception_recall": round(exc_right / max(1, exc_total), 4),
           "reconciliation_identity": identity,
           "failures": [x for x in rows if not x["pass"]][:10]}
    print(json.dumps({k: v for k, v in out.items() if k != "failures"}))
    for f in out["failures"][:5]:
        print("FAIL", f["case"])
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
