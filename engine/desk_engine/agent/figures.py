"""Provenance-aware check of the figures in an answer (audit finding AI-05).

Every figure the assistant states is classified by where it can be traced to:

* ``tool``     -- it matches a figure in a tool result (ledger, dataset, calculator, document): verified;
* ``derived``  -- it is the sum, difference, ratio or change of two *verified figures stated in the same answer*,
                  recomputed here exactly: verified;
* ``memory``   -- it matches a memory a person approved: shown as consulted memory;
* ``user``     -- it appears only in the user's own messages: the user's figure, *not* checked against the books;
* ``unsupported`` -- none of the above: the model produced it without evidence.

Matching is deterministic and unit-aware, and its tolerance comes from the figures' own precision rather than from
a percentage: "45,000.00" must agree with its source to the cent, "1.2m" to the nearest 50,000, "45.2%" to 0.05 of a
point. A percentage is compared with percentages (or with a plain fraction, 0.452), never with a ratio ("0.45×"), a
count of days or an amount. Counts below 100 are checked when they carry a unit (days, invoices, months, ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

COUNT_UNITS = ("days", "day", "months", "month", "weeks", "week", "years", "year", "invoices", "invoice", "bills",
               "bill", "customers", "customer", "suppliers", "supplier", "vendors", "vendor", "transactions",
               "transaction", "entries", "entry", "lines", "line", "accounts", "account", "orders", "order",
               "employees", "employee", "items", "item", "payments", "payment", "times", "periods", "period")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

_NUM = re.compile(
    r"(?<![\w.\-/:])"                                   # not inside a word, a date, an id or a time
    r"(?P<neg>[-−(])?"
    r"(?P<cur>[$€£¥]|USD|EUR|GBP|SAR|AED|QAR|KWD|BHD|OMR|EGP)?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"(?P<suf>\s?(?:%|percent\b|per cent\b|pct\b|bn\b|billion\b|million\b|m\b|k\b|x(?![\w])|×))?"
    r"(?P<unit>\s+(?:" + "|".join(COUNT_UNITS) + r")\b)?"
    r"(?![\w/]|\.\d|-\d)",
    re.I)
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}:\d{2}\b")
_CITE = re.compile(r"\[\d+\]")
_LIST = re.compile(r"(?m)^\s*\d+[.)]\s")


@dataclass
class Figure:
    raw: str
    value: float          # in natural units: 45.2% -> 45.2 (percent points); 1.2m -> 1,200,000
    kind: str             # percent | ratio | count | amount
    half_unit: float      # half of the last digit shown: the rounding the figure can carry
    start: int = 0
    unit: str = ""        # for counts: the unit word, singular ("day", "invoice")
    provenance: str = ""
    via: dict[str, Any] = field(default_factory=dict)


def _singular(unit: str) -> str:
    if unit in ("entries",):
        return "entry"
    return unit[:-1] if unit.endswith("s") and unit not in ("times",) else unit


def parse(text: str) -> list[Figure]:
    text = text or ""
    masked = _DATE.sub(lambda m: " " * len(m.group(0)), text)
    masked = _CITE.sub(lambda m: " " * len(m.group(0)), masked)
    masked = _LIST.sub(lambda m: " " * len(m.group(0)), masked)
    out: list[Figure] = []
    for m in _NUM.finditer(masked):
        whole = m.group("num").replace(",", "")
        dec = m.group("dec") or ""
        suf = (m.group("suf") or "").strip().lower()
        unit = (m.group("unit") or "").strip().lower()
        decimals = len(dec) - 1 if dec else 0
        try:
            value = float(whole + dec)
        except ValueError:
            continue
        scale = {"k": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}.get(suf, 1.0)
        if suf in ("%", "percent", "per cent", "pct"):
            kind = "percent"
        elif suf in ("x", "×"):
            kind = "ratio"
        elif unit:
            kind = "count"
        else:
            kind = "amount"
        after = masked[m.end():m.end() + 12].lower().lstrip()
        before = masked[max(0, m.start() - 12):m.start()].lower()
        # dates written in words ("31 October", "October 31", "Q3 2026" is masked by the year rule below)
        if kind == "amount" and (after[:3] in MONTHS or any(before.rstrip().endswith(mo) or before.rstrip().endswith(mo + ".")
                                                          for mo in MONTHS)) and value <= 31 and not dec:
            continue
        if kind == "amount" and not dec and "," not in m.group("num") and scale == 1.0 and 1900 <= value <= 2100:
            continue                                   # a year
        if kind == "amount" and not dec and scale == 1.0 and value < 100 and not m.group("cur"):
            continue                                   # small counts, list numbers, steps: no unit, nothing to check
        sign = -1.0 if m.group("neg") else 1.0
        out.append(Figure(raw=m.group(0).strip(), value=sign * value * scale, kind=kind,
                          half_unit=0.5 * (10 ** -decimals) * scale, start=m.start(),
                          unit=_singular(unit)))
    return out


def _close(a: Figure, e: Figure, e_value: float, e_half: float) -> bool:
    tol = max(a.half_unit, e_half) + 1e-9 * max(1.0, abs(e_value))
    return abs(abs(a.value) - abs(e_value)) <= tol


def matches(a: Figure, e: Figure) -> bool:
    """Same figure, allowing for the rounding either side shows; units must agree."""
    if a.kind == "percent":
        if e.kind == "percent":
            return _close(a, e, e.value, e.half_unit)
        if e.kind == "amount" and abs(e.value) <= 10 and e.half_unit <= 0.005:
            return _close(a, e, e.value * 100, e.half_unit * 100)      # a plain fraction such as 0.4523
        return False
    if a.kind == "ratio":
        return e.kind in ("ratio", "amount") and _close(a, e, e.value, e.half_unit)
    if a.kind == "count":
        # 5 days is not 5 customers: a count matches a plain number or a count of the same unit
        return ((e.kind == "amount" or (e.kind == "count" and e.unit == a.unit))
                and _close(a, e, e.value, e.half_unit))
    # an amount: amounts, counts and ratios written as plain numbers; never a percentage
    return e.kind in ("amount", "count", "ratio") and _close(a, e, e.value, e.half_unit)


def _derived(a: Figure, verified: list[Figure]) -> dict[str, Any] | None:
    """Is ``a`` the exact result of one operation on two verified figures stated in the same answer?"""
    pool = [f for f in verified if f is not a and f.kind in ("amount", "count")][:40]
    for x, y in combinations(pool, 2):
        for p, q in ((x, y), (y, x)):
            cands: list[tuple[str, float, float]] = []
            if a.kind in ("amount", "count"):
                cands += [("sum", p.value + q.value, p.half_unit + q.half_unit),
                          ("difference", p.value - q.value, p.half_unit + q.half_unit)]
            if a.kind == "percent" and q.value:
                cands += [("share", p.value / q.value * 100, 0.0), ("change", (p.value - q.value) / abs(q.value) * 100, 0.0)]
            if a.kind == "ratio" and q.value:
                cands.append(("ratio", p.value / q.value, 0.0))
            for op, val, half in cands:
                if abs(abs(a.value) - abs(val)) <= a.half_unit + half + 1e-9 * max(1.0, abs(val)):
                    return {"op": op, "of": [p.raw, q.raw]}
    return None


def classify(answer: str, tool_texts: list[str], user_texts: list[str], memory_texts: list[str]) -> list[Figure]:
    tools = [f for t in tool_texts for f in parse(t)]
    users = [f for t in user_texts for f in parse(t)]
    memories = [f for t in memory_texts for f in parse(t)]
    figs = parse(answer)
    for f in figs:
        if any(matches(f, e) for e in tools):
            f.provenance = "tool"
        elif any(matches(f, e) for e in memories):
            f.provenance = "memory"
    verified = [f for f in figs if f.provenance == "tool"]
    # derived figures may build on each other (a total, then a share of that total): a few passes, each recomputed
    for _ in range(3):
        grew = False
        for f in figs:
            if f.provenance:
                continue
            via = _derived(f, verified)
            if via:
                f.provenance, f.via = "derived", via
                verified.append(f)
                grew = True
        if not grew:
            break
    for f in figs:
        if f.provenance:
            continue
        if any(matches(f, e) for e in users):
            f.provenance = "user"
        else:
            f.provenance = "unsupported"
    return figs


def report(answer: str, tool_texts: list[str], user_texts: list[str], memory_texts: list[str]) -> dict[str, Any]:
    """What the app stores with an answer: unverified figures, the user's own figures, and every figure's source."""
    figs = classify(answer, tool_texts, user_texts, memory_texts)

    def uniq(xs: list[str]) -> list[str]:
        return list(dict.fromkeys(xs))[:20]
    return {
        "unverified_figures": uniq([f.raw for f in figs if f.provenance == "unsupported"]),
        "user_figures": uniq([f.raw for f in figs if f.provenance == "user"]),
        "figure_provenance": [{"figure": f.raw, "kind": f.kind, "source": f.provenance, **({"via": f.via} if f.via else {})}
                              for f in figs][:60],
    }
