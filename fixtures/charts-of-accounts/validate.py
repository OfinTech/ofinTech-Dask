#!/usr/bin/env python3
"""Show what the product makes of a chart of accounts, before you open a pull request.

    python fixtures/charts-of-accounts/validate.py
    python fixtures/charts-of-accounts/validate.py fixtures/charts-of-accounts/fr/sme

For every account it prints the class the class word resolves to and the role that is suggested, and marks each
disagreement with the `Expected role` column. A disagreement is not something to paper over by changing the
expectation: it is either a gap to record in `expected.json` with a reason, or a bug to fix in the word lists.
See the README beside this script.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "engine"))


def engine():
    from desk_engine.analysis import roles
    from desk_engine.costcentres.templates import _cls
    return roles, _cls


def rows(folder: Path) -> list[dict[str, str]]:
    with (folder / "accounts.csv").open(encoding="utf-8-sig", newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def report(folder: Path) -> int:
    """Print one chart, and return the number of disagreements that are not recorded as gaps."""
    roles, _cls = engine()
    note = folder / "expected.json"
    meta = json.loads(note.read_text(encoding="utf-8")) if note.exists() else {}
    gaps = meta.get("gaps") or {}
    class_gaps = {str(g.get("written")) for g in gaps.get("classes") or []}
    role_gaps = {str(g.get("code")) for g in gaps.get("roles") or []}

    print(f"\n{folder.relative_to(ROOT) if ROOT in folder.parents else folder}")
    if meta.get("invented") is not True:
        print('  ! expected.json must say "invented": true')
    unrecorded = 0
    for row in rows(folder):
        code, name, written, expected = row["Code"], row["Account"], row["Account class"], row["Expected role"]
        cls = _cls(written)
        if cls is None:
            mark = "gap" if written in class_gaps else "NEW"
            unrecorded += mark == "NEW"
            print(f"  [{mark}] {code:<6} {name:<34} class “{written}” is not recognised (expected {expected})")
            continue
        got = roles.suggest(cls, name)
        if got == expected:
            print(f"   ok   {code:<6} {name:<34} {cls:<14} {got}")
        else:
            mark = "gap" if code in role_gaps else "NEW"
            unrecorded += mark == "NEW"
            print(f"  [{mark}] {code:<6} {name:<34} {cls:<14} {got}  (you expect {expected})")
    return unrecorded


def main() -> int:
    folders = [Path(a).resolve() for a in sys.argv[1:]] or sorted(p.parent for p in HERE.rglob("expected.json"))
    if not folders:
        print("No charts found under fixtures/charts-of-accounts.")
        return 0
    unrecorded = sum(report(f) for f in folders)
    print(f"\n{len(folders)} chart(s) read.", end=" ")
    if unrecorded:
        print(f"{unrecorded} disagreement(s) are not recorded in expected.json yet.")
        print("Each one is a gap to record with a reason, or a bug to fix in the word lists -- not an "
              "expectation to change.")
        return 1
    print("Every account agrees with the fixture, gaps included.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
