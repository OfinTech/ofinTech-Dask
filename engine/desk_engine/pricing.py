"""What a model call cost, from rates the workspace enters itself.

The product records every token it spends but has never been able to say what they cost, because a token price is
not a property of the software: each workspace brings its own provider and its own contract, prices change, and a
price bundled with a release would be stale the week after. So nothing is priced until someone enters a rate, and
usage with no rate behind it is reported as unpriced rather than quietly counted as free. A cost figure that
silently omits a third of the traffic is worse than no cost figure at all, in a product whose whole claim is that
a number can be traced to where it came from.

Three rules decide what a call cost.

**The rate in force at the time.** A rate carries the date it takes effect, and usage is costed against whatever
was in force when it happened. Adding next month's price does not re-cost last month. Correcting a rate that was
typed wrongly does, which is the difference between a new price and a correction, and it is the difference an
accountant already has in mind.

**The more specific rate wins.** A rate may name a provider or leave it open for any provider serving that model
name. Where both could apply, the one naming the provider does, whichever was entered later: naming the provider
is the narrower statement, so it is the deliberate one.

**Cached tokens are not input tokens.** ``usage.input_tokens`` counts every prompt token including the cached
ones, as ``Usage`` says, so the tokens billed at the full input rate are what is left after the cache reads and
writes are taken out. Costing the column as it stands would bill the cached part twice, at the dearer rate.
"""

from __future__ import annotations

import itertools
from typing import Any

PER = 1_000_000.0
RATE_FIELDS = ("input_per_m", "output_per_m", "cache_read_per_m", "cache_write_per_m")


def matches(rate: dict[str, Any], provider_id: str | None, model: str) -> bool:
    """A rate applies to this call if the model is the same and it either names this provider or names none."""
    if rate["model"] != model:
        return False
    return rate["provider_id"] is None or rate["provider_id"] == provider_id


def in_force(rates: list[dict[str, Any]], provider_id: str | None, model: str, at: float) -> dict[str, Any] | None:
    """The rate that applied to this provider and model at ``at``: most specific first, then most recent."""
    best: tuple[tuple[int, float], dict[str, Any]] | None = None
    for rate in rates:
        if not matches(rate, provider_id, model) or rate["effective_from"] > at:
            continue
        key = (0 if rate["provider_id"] is None else 1, float(rate["effective_from"]))
        if best is None or key > best[0]:
            best = (key, rate)
    return best[1] if best else None


def periods(rates: list[dict[str, Any]], provider_id: str | None, model: str,
            since: float, until: float) -> list[tuple[float, float, dict[str, Any] | None]]:
    """Split ``[since, until)`` where the rate changes, as (from, to, rate or None).

    Splitting rather than costing the window against one rate is what makes a price change mid-month come out
    right. A `None` rate marks a stretch of usage nobody has priced, which the caller reports rather than drops.
    """
    boundaries = {since}
    for rate in rates:
        if matches(rate, provider_id, model) and since < rate["effective_from"] < until:
            boundaries.add(float(rate["effective_from"]))
    ordered = [*sorted(boundaries), until]
    return [(a, b, in_force(rates, provider_id, model, a)) for a, b in itertools.pairwise(ordered) if b > a]


def cost_of(tokens: dict[str, Any], rate: dict[str, Any]) -> float:
    """What these tokens cost at this rate, in the rate's own currency.

    The cached tokens are taken out of the input count before it is billed at the input rate; they are billed at
    their own rates, which is why a provider's cache is worth having in the first place.
    """
    cache_read = int(tokens.get("cache_read") or 0)
    cache_write = int(tokens.get("cache_write") or 0)
    fresh_input = max(0, int(tokens.get("input_tokens") or 0) - cache_read - cache_write)
    return (
        fresh_input * float(rate["input_per_m"])
        + int(tokens.get("output_tokens") or 0) * float(rate["output_per_m"])
        + cache_read * float(rate["cache_read_per_m"])
        + cache_write * float(rate["cache_write_per_m"])
    ) / PER


def rates_for(db: Any, ws_id: str) -> list[dict[str, Any]]:
    return db.all("SELECT * FROM model_prices WHERE workspace_id = ? ORDER BY effective_from", (ws_id,))


def _sums(db: Any, ws_id: str, provider_id: str | None, model: str, start: float, end: float) -> dict[str, Any]:
    return db.one(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
        "COALESCE(SUM(output_tokens), 0) AS output_tokens, COALESCE(SUM(cache_read), 0) AS cache_read, "
        "COALESCE(SUM(cache_write), 0) AS cache_write FROM usage "
        "WHERE workspace_id = ? AND provider_id IS ? AND model = ? AND created_at >= ? AND created_at < ?",
        (ws_id, provider_id, model, start, end))


def priced_usage(db: Any, ws_id: str, since: float, until: float) -> dict[str, Any]:
    """Usage in the window, by provider and model, with what it cost.

    Returns one row per provider and model carrying its tokens, its cost per currency, and how much of it no rate
    covered. Costs are kept in the currency each rate names and are never converted: an exchange rate this product
    invented would be a figure with no source behind it, which is the one thing it must not produce.
    """
    rates = rates_for(db, ws_id)
    pairs = db.all("SELECT DISTINCT provider_id, model FROM usage WHERE workspace_id = ? "
                   "AND created_at >= ? AND created_at < ? ORDER BY model", (ws_id, since, until))
    labels = {p["id"]: p["label"] for p in db.all("SELECT id, label FROM llm_providers WHERE workspace_id = ?", (ws_id,))}

    rows: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    unpriced = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    for pair in pairs:
        provider_id, model = pair["provider_id"], pair["model"]
        row: dict[str, Any] = {"provider_id": provider_id, "provider": labels.get(provider_id), "model": model,
                               "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0,
                               "cost": {}, "unpriced_calls": 0}
        for start, end, rate in periods(rates, provider_id, model, since, until):
            sums = _sums(db, ws_id, provider_id, model, start, end)
            if not sums["calls"]:
                continue
            for field in ("calls", "input_tokens", "output_tokens", "cache_read", "cache_write"):
                row[field] += int(sums[field])
            if rate is None:
                row["unpriced_calls"] += int(sums["calls"])
                unpriced["calls"] += int(sums["calls"])
                unpriced["input_tokens"] += int(sums["input_tokens"])
                unpriced["output_tokens"] += int(sums["output_tokens"])
                continue
            currency = rate["currency"]
            amount = cost_of(sums, rate)
            row["cost"][currency] = round(row["cost"].get(currency, 0.0) + amount, 6)
            totals[currency] = round(totals.get(currency, 0.0) + amount, 6)
        if row["calls"]:
            rows.append(row)

    rows.sort(key=lambda r: r["calls"], reverse=True)
    return {"by_model": rows, "totals": totals, "unpriced": unpriced,
            "priced": bool(rates), "currencies": sorted(totals)}
