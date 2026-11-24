"""How long the cash lasts at the rate it has been leaving, and what a change to that rate does to the date.

The cash_runway KPI already divides the bank balance by the average burn of three months. A founder's question is
one step further: in which month does the cash run out, and what happens to that month if we hire, cut a cost or
raise money. The ledger holds everything the answer needs, so it is worked out here, in code, on the same measures
the KPI uses (``cash`` and ``burn``), and the page and the assistant both report it.

It is arithmetic at the current rate, not a forecast: it assumes the months ahead look like the months averaged, and
says so wherever it is shown. Nothing here changes data, proposes an entry or carries a score.
"""

from __future__ import annotations

import math
from typing import Any

from ..errors import Invalid
from .ledger import Ledger

AVERAGE_MONTHS = 3          # the window the cash_runway KPI averages over
MAX_CHANGES = 12
# A change to the monthly rate, by its effect on burn: a new cost raises it, a new income lowers it.
MONTHLY = {"cost_added": 1.0, "cost_removed": -1.0, "income_added": -1.0, "income_removed": 1.0}
# A single amount, by its effect on the cash held today: money raised or received once, or paid out once.
ONE_OFF = {"cash_in": 1.0, "cash_out": -1.0}


def add_months(month: str, n: int) -> str:
    """The month ``n`` months after ``month`` (YYYY-MM)."""
    total = int(month[:4]) * 12 + int(month[5:7]) - 1 + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def position(cash: float, burn: float, month: str) -> dict[str, Any]:
    """Cash and a monthly burn read as months of cash and the month it runs out in. Not burning: neither exists.

    The month is the one during which the balance reaches zero: 2.4 months after the end of October is in January;
    exactly 2.0 months is the end of December."""
    if burn <= 0.005:
        return {"cash": round(cash, 2), "burn": round(burn, 2), "burning": False, "months_left": None, "runs_out": None}
    months = max(cash, 0.0) / burn
    return {"cash": round(cash, 2), "burn": round(burn, 2), "burning": True, "months_left": round(months, 2),
            "runs_out": add_months(month, math.ceil(months - 1e-9))}


def cash_runway(led: Ledger, month: str, span: int = AVERAGE_MONTHS,
                changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Cash at the end of ``month``, the average burn of the months up to it, how long the cash lasts at that rate
    and the month it runs out in; and the same again with ``changes`` applied, when there are any."""
    i = led.months.index(month)
    span = max(1, min(12, int(span or AVERAGE_MONTHS)))
    window = led.months[max(0, i - span + 1): i + 1]
    cash = led.value("cash", month) or 0.0
    burn = sum(led.value("burn", m) or 0.0 for m in window) / len(window)
    out: dict[str, Any] = {
        "month": month, "window": window, "asked_months": span, **position(cash, burn, month),
        "by_month": [{"month": m, "burn": round(led.value("burn", m) or 0.0, 2),
                      "cash": round(led.value("cash", m) or 0.0, 2)} for m in led.months[max(0, i - 5): i + 1]],
        "scenario": None}
    if not changes:
        return out
    if len(changes) > MAX_CHANGES:
        raise Invalid(f"At most {MAX_CHANGES} changes at a time.", code="too_many_changes")
    new_cash, new_burn, applied = cash, burn, []
    for c in changes:
        kind = str(c.get("kind") or "")
        try:
            amount = float(c.get("amount"))
        except (TypeError, ValueError):
            raise Invalid("Each change needs an amount.", code="bad_change") from None
        if kind not in MONTHLY and kind not in ONE_OFF:
            raise Invalid("A change is cost_added, cost_removed, income_added or income_removed (an amount a month), "
                          "or cash_in or cash_out (one amount, once).", code="bad_change")
        if amount < 0 or not math.isfinite(amount):
            raise Invalid("A change's amount is positive; its kind says which way it goes.", code="bad_change")
        if kind in MONTHLY:
            new_burn += MONTHLY[kind] * amount
        else:
            new_cash += ONE_OFF[kind] * amount
        applied.append({"kind": kind, "amount": round(amount, 2), "label": str(c.get("label") or "")[:80]})
    after = position(new_cash, new_burn, month)
    moved = (None if after["months_left"] is None or out["months_left"] is None
             else round(after["months_left"] - out["months_left"], 2))
    out["scenario"] = {**after, "changes": applied, "months_change": moved}
    return out
