#!/usr/bin/env python3
"""Check a bank-statement fixture before you open a pull request.

    python fixtures/validate.py                      # every fixture
    python fixtures/validate.py fixtures/statements/gb/my-bank

For each fixture it reads the statement with the same code the product uses and compares the result with
`expected.json`. If a fixture has no `expect` block yet, it prints what the engine read so you can paste it in
and check it against the file by eye -- the expectation has to be what the statement *says*, not what the code
happened to return, or the fixture locks a bug in place.

It also refuses a fixture that looks like real data. Invented data is the rule here: see fixtures/README.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

MONEY = 0.005          # a statement is read to the cent
# A card number must pass Luhn *and* start in a range the schemes actually issue from, at a length that range
# uses. Luhn alone is a check digit: about one in ten random numbers passes it, so an account-number column
# trips it within a few rows and the warning becomes noise people learn to ignore.
IIN = ((r"4", (13, 16, 19)), (r"5[1-5]", (16,)), (r"2[2-7]", (16,)), (r"3[47]", (15,)),
       (r"3(?:0[0-5]|[68])", (14,)), (r"6(?:011|5)", (16,)), (r"35", (16,)))


def luhn(digits: str) -> bool:
    total, even = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if even:
            n *= 2
            if n > 9:
                n -= 9
        total, even = total + n, not even
    return total % 10 == 0


def looks_like_a_card(text: str) -> str | None:
    for run in re.findall(r"\d(?:[ -]?\d){12,18}", text):
        digits = re.sub(r"\D", "", run)
        if not luhn(digits):
            continue
        if any(len(digits) in lengths and re.match(prefix, digits) for prefix, lengths in IIN):
            return run
    return None


def read(path: Path) -> dict:
    from desk_engine.close.statement import read_statement
    return read_statement(path.read_bytes(), path.name)


def check(folder: Path) -> list[str]:
    """The problems with one fixture, empty when it is good."""
    meta_path = folder / "expected.json"
    problems: list[str] = []
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:                        # unreadable JSON, whatever the reason, is one answer
        return [f"expected.json could not be read: {exc}"]

    if meta.get("invented") is not True:
        problems.append('expected.json must say "invented": true -- every figure, name and number is made up')
    statement = folder / str(meta.get("file") or "statement.csv")
    if not statement.exists():
        return problems + [f"{statement.name} is missing"]

    if statement.suffix.lower() in {".csv", ".txt"}:
        text = statement.read_text(encoding="utf-8", errors="replace")
        card = looks_like_a_card(text)
        if card:
            problems.append(f"{card} reads as a real card number -- change the digits")
        if re.search(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", text):
            problems.append("something in the file is shaped like an IBAN -- invent one or take it out")

    try:
        got = read(statement)
    except Exception as exc:                        # the refusal message is the whole point here
        return problems + [f"the statement was refused: {exc}"]

    total = round(sum(line["amount"] for line in got["lines"]), 2)
    actual = {"lines": len(got["lines"]), "first_date": got["first_date"], "last_date": got["last_date"],
              "total": total, "closing": got["closing"]}
    expect = meta.get("expect")
    if not expect:
        print(f"  {folder.relative_to(ROOT)}: no expectation yet. The engine reads it as:")
        print("  " + json.dumps(actual, ensure_ascii=False))
        print("  Check each of those against the file, then put them in expected.json under \"expect\".")
        return problems

    for key in ("lines", "first_date", "last_date"):
        if key in expect and expect[key] != actual[key]:
            problems.append(f"{key}: expected {expect[key]!r}, read {actual[key]!r}")
    if "total" in expect and abs(float(expect["total"]) - total) > MONEY:
        problems.append(f"total: expected {expect['total']}, read {total}")
    if "closing" in expect:
        want, have = expect["closing"], actual["closing"]
        if (want is None) != (have is None):
            problems.append(f"closing balance: expected {want!r}, read {have!r}")
        elif want and (want.get("date") != have["date"] or abs(float(want["amount"]) - have["amount"]) > MONEY):
            problems.append(f"closing balance: expected {want!r}, read {have!r}")
    return problems


def folders(args: list[str]) -> list[Path]:
    if args:
        return [Path(a).resolve() for a in args]
    return sorted(p.parent for p in (ROOT / "fixtures" / "statements").rglob("expected.json"))


def main() -> int:
    found = folders(sys.argv[1:])
    if not found:
        print("No fixtures found under fixtures/statements.")
        return 0
    bad = 0
    for folder in found:
        problems = check(folder)
        name = folder.relative_to(ROOT) if ROOT in folder.parents else folder
        if problems:
            bad += 1
            print(f"✗ {name}")
            for p in problems:
                print(f"    {p}")
        else:
            print(f"✓ {name}")
    print(f"\n{len(found) - bad} of {len(found)} fixtures read as expected.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
