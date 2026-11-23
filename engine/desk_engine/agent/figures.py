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

A calculator's result counts as a tool result only when every number that went into it has a source of its own (a
result of another tool, the user's messages, an approved memory, an earlier sound calculation) or is a plain
constant of arithmetic (12 months, 100 per cent, 365 days). A rate or a base the model supplied itself makes the
result ``unsupported``: the sum is right and what it was a sum of came from nowhere.
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

# Where a figure may start. Not inside a Latin word, an id, a date or a time ("INV-1104", "Q3", "v1.2") -- and
# not in the middle of another figure. Two things this used to get wrong, both seen on 23 Nov 2026 in Arabic answers:
#  * Arabic writes "and" and "by" as one letter joined to the next word, so "و79,605.55" is the figure 79,605.55
#    with "and" in front of it. The old test was "not after any letter", which refused to start there...
#  * ...and then started three characters later, after the comma, and reported the unknown figure "605.55".
#    A correct answer was marked "check before relying on" for a number nobody wrote. Never start after "digit,".
_NUM = re.compile(
    r"(?<![A-Za-z0-9_.\-/:])(?<!\d,)"
    r"(?P<neg>[-−(])?"
    r"(?P<cur>[$€£¥]|USD|EUR|GBP|SAR|AED|QAR|KWD|BHD|OMR|EGP)?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"(?P<suf>\s?(?:%|٪|percent\b|per cent\b|pct\b|bn\b|billion\b|million\b|m\b|k\b|x(?![\w])|×))?"
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


def parse(text: str, *, evidence: bool = False) -> list[Figure]:
    """The figures in a text. In an answer, a bare number under 100 is a list number or a step and is left alone;
    in evidence it is kept as kind ``small``, so that "4 items" in an answer can be traced to "4 found" in a tool
    result (it was reported as unknown on 24 Nov 2026). A ``small`` figure supports counts only, never an amount,
    a percentage or a ratio."""
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
        if suf in ("%", "٪", "percent", "per cent", "pct"):
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
            if not evidence:
                continue                               # small counts, list numbers, steps: no unit, nothing to check
            kind = "small"
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
        return ((e.kind in ("amount", "small") or (e.kind == "count" and e.unit == a.unit))
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


# --- what went into a calculation ---------------------------------------------------------------------------------
# Numbers that arithmetic itself supplies: small whole numbers (a count of months, a list of three), and the
# constants of percentages, years and thousands. Anything else a calculator is given must have been read somewhere.
CONSTANTS = {24.0, 28.0, 29.0, 30.0, 31.0, 52.0, 60.0, 90.0, 91.25, 100.0, 180.0, 360.0, 365.0, 366.0, 1000.0, 1_000_000.0}


# The calculators that take any figures at all ("figures you already have, copied exactly"). The financial
# calculators (NPV, loan schedules, depreciation) take parameters a person chooses, and are not held to this.
PLAIN_CALCULATORS = {"calc_arithmetic", "calc_what_if"}


def is_calculator(name: str) -> bool:
    return name in PLAIN_CALCULATORS


def _numbers(args: Any) -> list[float]:
    """Every number among a tool call's arguments (labels and other text are not read: a year in a label is not an
    input)."""
    if isinstance(args, bool):
        return []
    if isinstance(args, (int, float)):
        return [float(args)]
    if isinstance(args, dict):
        return [x for v in args.values() for x in _numbers(v)]
    if isinstance(args, (list, tuple)):
        return [x for v in args for x in _numbers(v)]
    return []


def _half(v: float) -> float:
    """Half of the last digit the number shows: 148083.31 -> 0.005, 0.561 -> 0.0005, 38000 -> 0.5."""
    text = repr(float(v))
    if "e" in text or "E" in text:
        return abs(v) * 1e-9
    decimals = len(text.split(".")[1]) if "." in text and not text.endswith(".0") else 0
    return 0.5 * 10 ** -decimals


def _sourced(v: float, evidence: list[Figure]) -> bool:
    if abs(v) in CONSTANTS or (float(v).is_integer() and abs(v) <= 12):
        return True
    half = _half(v)
    for e in evidence:
        tol = max(half, e.half_unit) + 1e-9 * max(1.0, abs(e.value))
        if abs(abs(v) - abs(e.value)) <= tol:
            return True
        if e.kind == "percent" and abs(abs(v) * 100 - abs(e.value)) <= e.half_unit + 1e-9:
            return True                                # 0.561 for "56.1%"
    return False


def sound_calculations(calcs: list[tuple[Any, str]], evidence_texts: list[str], *,
                       since: int = 0) -> tuple[list[str], list[str]]:
    """The results of the calculator calls whose inputs all have a source, and the inputs that have none (as text,
    in call order). A sound result becomes evidence for the calls after it: a total, then a share of that total.

    ``since`` is the index of the first call that belongs to the answer being checked. Every call is judged, so
    that an unsound one from an earlier turn stays out of the evidence, but only the later ones are reported:
    on 24 Nov 2026 two numbers from the second answer of a conversation were shown under its third, fourth,
    fifth and sixth, whose own calculations were sound."""
    evidence = [f for t in evidence_texts for f in parse(t, evidence=True)]
    sound: list[str] = []
    orphans: list[str] = []
    for i, (args, result) in enumerate(calcs):
        missing = [v for v in _numbers(args) if not _sourced(v, evidence)]
        if missing:
            if i >= since:
                orphans += [f"{v:,.10g}" for v in missing]
            continue
        sound.append(result)
        evidence += parse(result, evidence=True)
    return sound, list(dict.fromkeys(orphans))


def classify(answer: str, tool_texts: list[str], user_texts: list[str], memory_texts: list[str]) -> list[Figure]:
    tools = [f for t in tool_texts for f in parse(t, evidence=True)]
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


def report(answer: str, tool_texts: list[str], user_texts: list[str], memory_texts: list[str], *,
           calcs: list[tuple[Any, str]] | None = None, calcs_since: int = 0) -> dict[str, Any]:
    """What the app stores with an answer: unverified figures, the user's own figures, and every figure's source.

    ``calcs`` are the calculator calls of the conversation as (arguments, result), in order, kept apart from
    ``tool_texts``: only those whose inputs have a source count as evidence. ``calcs_since`` is how many of them
    were made before the user's latest message; unsourced inputs are reported for the ones after it."""
    orphans: list[str] = []
    if calcs:
        sound, orphans = sound_calculations(calcs, [*tool_texts, *user_texts, *memory_texts], since=calcs_since)
        tool_texts = [*tool_texts, *sound]
    figs = classify(answer, tool_texts, user_texts, memory_texts)

    def uniq(xs: list[str]) -> list[str]:
        return list(dict.fromkeys(xs))[:20]
    return {
        "unverified_figures": uniq([f.raw for f in figs if f.provenance == "unsupported"]),
        "user_figures": uniq([f.raw for f in figs if f.provenance == "user"]),
        "figure_provenance": [{"figure": f.raw, "kind": f.kind, "source": f.provenance, **({"via": f.via} if f.via else {})}
                              for f in figs][:60],
        # numbers given to a calculator that were read nowhere; its result is then no evidence for the answer
        **({"unsourced_inputs": orphans[:20]} if orphans else {}),
    }
