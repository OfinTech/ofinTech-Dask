"""Discount coupons: created here, mirrored to Stripe as a coupon plus a promotion code, validated here before a
checkout and recorded on redemption.

Stripe's constraints are respected rather than papered over: a coupon's discount, duration and currency cannot be
changed after creation (Stripe's coupons are immutable in those fields), so here too only the metadata — name, active
state, expiry, plan restrictions, per-customer limit — can be edited; a different discount is a new coupon. Stripe
applies one promotion code per subscription, so stacking is refused. A fixed-amount coupon needs a currency and
applies only to plans in that currency.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, new_id, now
from ..errors import Conflict, Invalid, NotFound

if TYPE_CHECKING:
    from ..app import Engine

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,39}$")


def _row(c: dict[str, Any]) -> dict[str, Any]:
    c = dict(c)
    c["plan_ids"] = loads(c.get("plan_ids"), [])
    c["active"] = bool(c.get("active"))
    c["first_time_only"] = bool(c.get("first_time_only"))
    return c


def list_coupons(engine: Engine) -> list[dict[str, Any]]:
    return [_row(c) for c in engine.db.all("SELECT * FROM coupons ORDER BY created_at DESC")]


def get(engine: Engine, coupon_id: str) -> dict[str, Any]:
    c = engine.db.one("SELECT * FROM coupons WHERE id = ? OR code = ?", (coupon_id, coupon_id.upper()))
    if c is None:
        raise NotFound("Coupon not found.")
    return _row(c)


def create(engine: Engine, values: dict[str, Any], *, by: str) -> dict[str, Any]:
    code = str(values.get("code") or "").strip().upper()
    if not CODE_RE.match(code):
        raise Invalid("A code is 3 to 40 characters: letters, digits, '-' or '_'.")
    percent, amount = values.get("percent_off"), values.get("amount_off")
    if (percent is None) == (amount is None):
        raise Invalid("Give either a percentage or a fixed amount, not both.")
    if percent is not None and not (isinstance(percent, (int, float)) and 0 < percent <= 100):
        raise Invalid("The percentage is between 0 and 100.")
    currency = (values.get("currency") or "").upper() or None
    if amount is not None:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise Invalid("The amount is a whole number of minor units (cents).")
        if not currency or not re.fullmatch(r"[A-Z]{3}", currency):
            raise Invalid("A fixed amount needs a currency.")
    duration = values.get("duration") or "once"
    if duration not in ("once", "repeating", "forever"):
        raise Invalid("Duration is once, repeating or forever.")
    months = values.get("duration_months")
    if duration == "repeating" and not (isinstance(months, int) and 1 <= months <= 36):
        raise Invalid("A repeating coupon lasts 1 to 36 months.")
    for k in ("max_redemptions", "per_customer_limit", "min_amount"):
        v = values.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            raise Invalid(f"{k} is a whole number.")
    plan_ids = values.get("plan_ids") or []
    if not isinstance(plan_ids, list) or any(not isinstance(p, str) for p in plan_ids):
        raise Invalid("plan_ids is a list of plan ids.")
    for pid in plan_ids:
        if engine.db.one("SELECT 1 FROM plans WHERE id = ?", (pid,)) is None:
            raise Invalid(f"Unknown plan {pid}.")
    expires_at = values.get("expires_at")
    if expires_at is not None and (not isinstance(expires_at, (int, float)) or expires_at < now()):
        raise Invalid("The expiry must be in the future.")
    if engine.db.one("SELECT 1 FROM coupons WHERE code = ?", (code,)):
        raise Conflict("A coupon with this code exists.")
    ts = now()
    row = {"id": new_id("cpn_"), "code": code, "percent_off": float(percent) if percent is not None else None,
           "amount_off": amount, "currency": currency, "duration": duration,
           "duration_months": months if duration == "repeating" else None, "expires_at": expires_at,
           "max_redemptions": values.get("max_redemptions"), "per_customer_limit": values.get("per_customer_limit") or 1,
           "plan_ids": dumps(plan_ids), "first_time_only": 1 if values.get("first_time_only") else 0,
           "min_amount": values.get("min_amount"), "active": 1, "created_by": by, "created_at": ts, "updated_at": ts}
    engine.db.insert("coupons", row)
    engine.audit(None, by, "coupon.create", row["id"], {"code": code, "percent_off": row["percent_off"], "amount_off": amount,
                 "duration": duration}, resource_type="coupon", decision="allowed", result="ok")
    return get(engine, row["id"])


EDITABLE = {"active", "expires_at", "plan_ids", "per_customer_limit", "first_time_only", "max_redemptions"}


def update(engine: Engine, coupon_id: str, values: dict[str, Any], *, by: str) -> dict[str, Any]:
    coupon = get(engine, coupon_id)
    bad = set(values) - EDITABLE
    if bad:
        raise Invalid(f"These fields cannot change after creation (Stripe keeps them fixed): {', '.join(sorted(bad))}.",
                      code="immutable")
    clean: dict[str, Any] = {}
    if "active" in values:
        if not isinstance(values["active"], bool):
            raise Invalid("active is true or false.")
        clean["active"] = int(values["active"])
    if "expires_at" in values:
        v = values["expires_at"]
        if v is not None and (not isinstance(v, (int, float))):
            raise Invalid("expires_at is a timestamp.")
        clean["expires_at"] = v
    if "plan_ids" in values:
        pids = values["plan_ids"] or []
        if not isinstance(pids, list):
            raise Invalid("plan_ids is a list.")
        for pid in pids:
            if engine.db.one("SELECT 1 FROM plans WHERE id = ?", (pid,)) is None:
                raise Invalid(f"Unknown plan {pid}.")
        clean["plan_ids"] = dumps(pids)
    for k in ("per_customer_limit", "max_redemptions"):
        if k in values:
            v = values[k]
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
                raise Invalid(f"{k} is a whole number.")
            clean[k] = v
    if "first_time_only" in values:
        clean["first_time_only"] = 1 if values["first_time_only"] else 0
    clean["updated_at"] = now()
    engine.db.update("coupons", coupon["id"], clean)
    engine.audit(None, by, "coupon.update", coupon["id"], values, resource_type="coupon", decision="allowed", result="ok")
    return get(engine, coupon["id"])


def validate(engine: Engine, *, code: str, user_id: str, plan_id: str, amount: int) -> dict[str, Any]:
    """Server-side check before a checkout. Raises Invalid with a specific code when the coupon cannot be used."""
    c = engine.db.one("SELECT * FROM coupons WHERE code = ?", ((code or "").strip().upper(),))
    if c is None:
        raise Invalid("This coupon code is not valid.", code="coupon_invalid")
    c = _row(c)
    if not c["active"]:
        raise Invalid("This coupon is no longer active.", code="coupon_inactive")
    if c["expires_at"] and c["expires_at"] < now():
        raise Invalid("This coupon has expired.", code="coupon_expired")
    if c["max_redemptions"] and c["redemptions"] >= c["max_redemptions"]:
        raise Invalid("This coupon has been used up.", code="coupon_exhausted")
    if c["plan_ids"] and plan_id not in c["plan_ids"]:
        raise Invalid("This coupon does not apply to that plan.", code="coupon_plan")
    plan = engine.db.one("SELECT currency FROM plans WHERE id = ?", (plan_id,))
    if c["amount_off"] and plan and c["currency"] and plan["currency"] != c["currency"]:
        raise Invalid("This coupon is for a different currency.", code="coupon_currency")
    if c["min_amount"] and amount < c["min_amount"]:
        raise Invalid("The purchase is below this coupon's minimum.", code="coupon_minimum")
    mine = engine.db.scalar("SELECT COUNT(*) FROM coupon_redemptions WHERE coupon_id = ? AND user_id = ?", (c["id"], user_id)) or 0
    if c["per_customer_limit"] and mine >= c["per_customer_limit"]:
        raise Invalid("You have already used this coupon.", code="coupon_used")
    if c["first_time_only"] and engine.db.one("SELECT 1 FROM subscriptions WHERE user_id = ? AND source = 'stripe'", (user_id,)):
        raise Invalid("This coupon is for first-time customers.", code="coupon_first_time")
    return c


def record_redemption(engine: Engine, *, coupon_id: str, user_id: str, subscription_id: str | None, plan_id: str | None,
                      amount_discounted: int) -> None:
    with engine.db.tx() as c:
        c.execute("INSERT INTO coupon_redemptions (id, coupon_id, user_id, subscription_id, plan_id, amount_discounted, created_at) "
                  "VALUES (?,?,?,?,?,?,?)", (new_id("red_"), coupon_id, user_id, subscription_id, plan_id, amount_discounted, now()))
        c.execute("UPDATE coupons SET redemptions = redemptions + 1, updated_at = ? WHERE id = ?", (now(), coupon_id))
    engine.audit(None, user_id, "coupon.redeemed", coupon_id, {"subscription": subscription_id, "amount": amount_discounted},
                 resource_type="coupon", decision="allowed", result="ok")


def redemptions(engine: Engine, coupon_id: str) -> list[dict[str, Any]]:
    return engine.db.all("SELECT r.*, u.email FROM coupon_redemptions r LEFT JOIN users u ON u.id = r.user_id WHERE r.coupon_id = ? "
                         "ORDER BY r.created_at DESC", (coupon_id,))


def performance(engine: Engine) -> list[dict[str, Any]]:
    return engine.db.all("SELECT c.id, c.code, c.redemptions, COALESCE(SUM(r.amount_discounted), 0) AS discounted, "
                         "COUNT(DISTINCT r.user_id) AS customers FROM coupons c LEFT JOIN coupon_redemptions r ON r.coupon_id = c.id "
                         "GROUP BY c.id ORDER BY c.redemptions DESC")


async def sync_to_stripe(engine: Engine, coupon_id: str, *, by: str) -> dict[str, Any]:
    from .billing import client

    coupon = get(engine, coupon_id)
    c = client(engine)
    changes: dict[str, Any] = {}
    if not coupon.get("stripe_coupon_id"):
        sc = await c.create_coupon(coupon_id=coupon["id"], percent_off=coupon["percent_off"], amount_off=coupon["amount_off"],
                                   currency=coupon["currency"], duration=coupon["duration"],
                                   duration_in_months=coupon["duration_months"], max_redemptions=coupon["max_redemptions"],
                                   redeem_by=int(coupon["expires_at"]) if coupon["expires_at"] else None, name=coupon["code"])
        changes["stripe_coupon_id"] = sc["id"]
    if not coupon.get("stripe_promotion_code_id"):
        plan_currency = None
        if coupon["plan_ids"]:
            plan_currency = engine.db.scalar("SELECT currency FROM plans WHERE id = ?", (coupon["plan_ids"][0],))
        pc = await c.create_promotion_code(coupon=changes.get("stripe_coupon_id") or coupon["stripe_coupon_id"], code=coupon["code"],
                                           first_time_only=coupon["first_time_only"], min_amount=coupon["min_amount"],
                                           currency=coupon["currency"] or plan_currency, max_redemptions=coupon["max_redemptions"],
                                           expires_at=int(coupon["expires_at"]) if coupon["expires_at"] else None)
        changes["stripe_promotion_code_id"] = pc["id"]
    if changes:
        changes["updated_at"] = now()
        engine.db.update("coupons", coupon["id"], changes)
        engine.audit(None, by, "coupon.synced", coupon["id"], {k: v for k, v in changes.items() if k != "updated_at"},
                     resource_type="coupon", decision="allowed", result="ok")
    return get(engine, coupon["id"])


async def set_active(engine: Engine, coupon_id: str, active: bool, *, by: str) -> dict[str, Any]:
    from .billing import client, enabled

    coupon = update(engine, coupon_id, {"active": active}, by=by)
    if coupon.get("stripe_promotion_code_id") and enabled(engine):
        await client(engine).set_promotion_code_active(coupon["stripe_promotion_code_id"], active)
    return get(engine, coupon["id"])
