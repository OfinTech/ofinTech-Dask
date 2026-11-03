"""Token rates, and what the workspace's model calls cost.

Rates sit beside the providers rather than in a global table, because the provider does: a workspace brings its own
key and its own contract, so it is the one that knows what it pays. Nothing is priced until someone enters a rate,
and the usage view says how much of the period no rate covered instead of counting it as free.
"""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, Field

from .. import pricing
from ..db import new_id, now
from ..errors import Invalid, NotFound
from ..web import Ctx, Router

router = Router()
MAX_RATE = 10_000.0          # per million tokens: far above any published price, and a guard against a typo


def _out(row: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    return {"id": row["id"], "provider_id": row["provider_id"], "provider": labels.get(row["provider_id"]),
            "model": row["model"], "currency": row["currency"],
            **{f: float(row[f]) for f in pricing.RATE_FIELDS},
            "effective_from": row["effective_from"], "note": row["note"], "created_at": row["created_at"]}


def _labels(ctx: Ctx) -> dict[str, str]:
    return {p["id"]: p["label"] for p in
            ctx.engine.db.all("SELECT id, label FROM llm_providers WHERE workspace_id = ?", (ctx.ws_id,))}


class RateIn(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    provider_id: str | None = None
    input_per_m: float = Field(default=0, ge=0, le=MAX_RATE)
    output_per_m: float = Field(default=0, ge=0, le=MAX_RATE)
    cache_read_per_m: float = Field(default=0, ge=0, le=MAX_RATE)
    cache_write_per_m: float = Field(default=0, ge=0, le=MAX_RATE)
    effective_from: str | None = None          # YYYY-MM-DD; today when left out
    note: str | None = Field(default=None, max_length=200)


class RatePatch(BaseModel):
    """A correction to a rate already entered. It re-costs the usage that rate covers, which is the point of it:
    a price typed wrongly produced wrong figures, and fixing the price has to fix them."""

    currency: str | None = Field(default=None, min_length=3, max_length=3)
    input_per_m: float | None = Field(default=None, ge=0, le=MAX_RATE)
    output_per_m: float | None = Field(default=None, ge=0, le=MAX_RATE)
    cache_read_per_m: float | None = Field(default=None, ge=0, le=MAX_RATE)
    cache_write_per_m: float | None = Field(default=None, ge=0, le=MAX_RATE)
    effective_from: str | None = None
    note: str | None = Field(default=None, max_length=200)


def _day_start(value: str | None) -> float:
    """A rate takes effect at the start of the day it is given for, in UTC, so a period never begins mid-day."""
    if not value:
        return datetime.datetime.now(datetime.UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        day = datetime.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise Invalid("The date must be written as YYYY-MM-DD.", code="bad_date") from exc
    return datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.UTC).timestamp()


def _check_provider(ctx: Ctx, provider_id: str | None) -> None:
    if provider_id and not ctx.engine.db.one(
            "SELECT id FROM llm_providers WHERE id = ? AND workspace_id = ?", (provider_id, ctx.ws_id)):
        raise NotFound("No such provider in this workspace.")


@router.get("/api/w/{ws}/prices", role="admin")
async def list_prices(ctx: Ctx) -> dict[str, Any]:
    labels = _labels(ctx)
    rows = ctx.engine.db.all("SELECT * FROM model_prices WHERE workspace_id = ? "
                             "ORDER BY model, effective_from DESC", (ctx.ws_id,))
    seen = ctx.engine.db.all("SELECT DISTINCT model FROM usage WHERE workspace_id = ? AND model IS NOT NULL "
                             "ORDER BY model", (ctx.ws_id,))
    return {"prices": [_out(r, labels) for r in rows],
            "models_seen": [r["model"] for r in seen],
            "providers": [{"id": pid, "label": label} for pid, label in sorted(labels.items(), key=lambda p: p[1])]}


@router.post("/api/w/{ws}/prices", role="admin", body=RateIn)
async def add_price(ctx: Ctx) -> dict[str, Any]:
    b: RateIn = ctx.body
    _check_provider(ctx, b.provider_id)
    if not any(getattr(b, field) for field in pricing.RATE_FIELDS):
        raise Invalid("Enter at least one rate; a price of nothing for everything is not a price.", code="no_rate")
    rate_id = new_id("mp_")
    ctx.engine.db.insert("model_prices", {
        "id": rate_id, "workspace_id": ctx.ws_id, "provider_id": b.provider_id, "model": b.model.strip(),
        "currency": b.currency.strip().upper(),
        **{field: float(getattr(b, field)) for field in pricing.RATE_FIELDS},
        "effective_from": _day_start(b.effective_from), "note": (b.note or "").strip() or None, "created_at": now()})
    ctx.audit("price.add", rate_id, {"model": b.model.strip(), "currency": b.currency.strip().upper()})
    return _out(ctx.engine.db.one("SELECT * FROM model_prices WHERE id = ?", (rate_id,)), _labels(ctx))


@router.patch("/api/w/{ws}/prices/{rid}", role="admin", body=RatePatch)
async def patch_price(ctx: Ctx) -> dict[str, Any]:
    rid = ctx.request.path_params["rid"]
    row = ctx.engine.db.one("SELECT * FROM model_prices WHERE id = ? AND workspace_id = ?", (rid, ctx.ws_id))
    if not row:
        raise NotFound("No such rate.")
    b: RatePatch = ctx.body
    changes: dict[str, Any] = {}
    for field in pricing.RATE_FIELDS:
        if getattr(b, field) is not None:
            changes[field] = float(getattr(b, field))
    if b.currency is not None:
        changes["currency"] = b.currency.strip().upper()
    if b.effective_from is not None:
        changes["effective_from"] = _day_start(b.effective_from)
    if b.note is not None:
        changes["note"] = b.note.strip() or None
    if not changes:
        raise Invalid("Nothing to change.")
    ctx.engine.db.update("model_prices", rid, changes)
    # the previous figures are kept in the audit entry: a corrected rate changes what past usage is reported to
    # have cost, and the trail has to show what it was reported as before
    ctx.audit("price.change", rid, {"model": row["model"], "was": {f: row[f] for f in changes}, "now": changes})
    return _out(ctx.engine.db.one("SELECT * FROM model_prices WHERE id = ?", (rid,)), _labels(ctx))


@router.delete("/api/w/{ws}/prices/{rid}", role="admin", fresh=True)
async def delete_price(ctx: Ctx) -> dict[str, Any]:
    rid = ctx.request.path_params["rid"]
    row = ctx.engine.db.one("SELECT * FROM model_prices WHERE id = ? AND workspace_id = ?", (rid, ctx.ws_id))
    if not row:
        raise NotFound("No such rate.")
    ctx.engine.db.execute("DELETE FROM model_prices WHERE id = ?", (rid,))
    ctx.audit("price.remove", rid, {"model": row["model"], "currency": row["currency"],
                                    **{f: row[f] for f in pricing.RATE_FIELDS}})
    return {"ok": True}
