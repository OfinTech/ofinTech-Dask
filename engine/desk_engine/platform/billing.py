"""Billing: Stripe Checkout and Customer Portal, idempotent webhook processing, reconciliation, invoices.

Stripe is the authority for payment and subscription state. Nothing a browser sends changes a subscription here:
Checkout only *starts* a payment, and the local row is written when Stripe's signed webhook (or an explicit
reconciliation against Stripe's API) says so. Every webhook event is stored by its id before it is processed
(duplicates and replays are answered 200 and ignored), processed in its own transaction, retried on failure and
parked as dead-letter after ``email.max_attempts``-style limits, and an event older than the last one applied to the
same subscription is ignored (out-of-order delivery).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..db import new_id, now
from ..errors import Conflict, Forbidden, Invalid, NotFound
from . import plans as plans_mod
from .settings import get_secret, get_setting, secret_status
from .stripe import StripeClient, parse_event, verify_signature

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.billing")

MAX_EVENT_ATTEMPTS = 5
HANDLED = {"checkout.session.completed", "customer.subscription.created", "customer.subscription.updated",
           "customer.subscription.deleted", "invoice.paid", "invoice.payment_succeeded", "invoice.payment_failed",
           "charge.refunded", "customer.subscription.trial_will_end"}
STATUS_MAP = {"trialing": "trialing", "active": "active", "past_due": "past_due", "canceled": "canceled",
              "unpaid": "unpaid", "incomplete": "incomplete", "incomplete_expired": "canceled", "paused": "past_due"}


def enabled(engine: Engine) -> bool:
    return bool(get_setting(engine, "stripe.enabled")) and secret_status(engine, "stripe.secret_key")["configured"]


def client(engine: Engine) -> StripeClient:
    if not get_setting(engine, "stripe.enabled"):
        raise Invalid("Billing is not switched on for this installation.", code="billing_off")
    key = get_secret(engine, "stripe.secret_key")
    if not key:
        raise Invalid("Stripe is not configured: the owner has not saved a secret key.", code="billing_unconfigured")
    mode = get_setting(engine, "stripe.mode")
    c = StripeClient(key, transport=engine.http_transport)
    if mode == "live" and not c.live:
        raise Invalid("Stripe is set to live mode but the saved key is a test key.", code="stripe_mode_mismatch")
    if mode == "test" and c.live:
        raise Invalid("Stripe is set to test mode but the saved key is a live key. Switch the mode or the key.",
                      code="stripe_mode_mismatch")
    return c


def status(engine: Engine) -> dict[str, Any]:
    failed = engine.db.scalar("SELECT COUNT(*) FROM stripe_events WHERE status IN ('failed','dead_letter')") or 0
    last = engine.db.one("SELECT id, type, received_at, status FROM stripe_events ORDER BY received_at DESC LIMIT 1")
    return {"enabled": bool(get_setting(engine, "stripe.enabled")), "mode": get_setting(engine, "stripe.mode"),
            "publishable_key": get_setting(engine, "stripe.publishable_key"),
            "secret_key": secret_status(engine, "stripe.secret_key"),
            "webhook_secret": secret_status(engine, "stripe.webhook_secret"),
            "currencies": get_setting(engine, "stripe.currencies"), "portal_enabled": get_setting(engine, "stripe.portal_enabled"),
            "tax_enabled": get_setting(engine, "stripe.tax_enabled"), "grace_days": get_setting(engine, "billing.grace_days"),
            "allow_promotion_codes": get_setting(engine, "billing.allow_promotion_codes"),
            "webhook_url": f"{engine.settings.public_url.rstrip('/')}/api/billing/webhook",
            "events_total": engine.db.scalar("SELECT COUNT(*) FROM stripe_events") or 0, "events_failed": failed,
            "last_event": last, "ready": enabled(engine) and secret_status(engine, "stripe.webhook_secret")["configured"]}


async def health(engine: Engine) -> dict[str, Any]:
    try:
        c = client(engine)
        bal = await c.ping()
        return {"ok": True, "live": c.live, "currencies": sorted({b.get("currency", "").upper() for b in bal.get("available", [])})}
    except Exception as exc:  # noqa: BLE001 - the dashboard shows the reason
        return {"ok": False, "error": getattr(exc, "message", str(exc))}


# --- customers and checkout ----------------------------------------------------------------------------------------

async def ensure_customer(engine: Engine, user: dict[str, Any]) -> str:
    row = engine.db.one("SELECT stripe_customer_id FROM billing_customers WHERE user_id = ?", (user["id"],))
    if row:
        return row["stripe_customer_id"]
    c = client(engine)
    cust = await c.create_customer(email=user["email"], name=user["name"], user_id=user["id"])
    with engine.db.tx() as tx:
        tx.execute("INSERT OR IGNORE INTO billing_customers (user_id, stripe_customer_id, created_at) VALUES (?,?,?)",
                   (user["id"], cust["id"], now()))
    return engine.db.scalar("SELECT stripe_customer_id FROM billing_customers WHERE user_id = ?", (user["id"],))


def _price_for(plan: dict[str, Any], interval: str) -> str:
    price = plan.get("stripe_price_monthly") if interval == "month" else plan.get("stripe_price_annual")
    if not price:
        raise Invalid("This plan has no Stripe price for that billing interval yet. The owner syncs it under Billing.",
                      code="plan_not_synced")
    return price


async def start_checkout(engine: Engine, user: dict[str, Any], *, plan_id: str, interval: str,
                         coupon_code: str | None = None) -> dict[str, Any]:
    from . import coupons as coupons_mod

    if not engine.settings.hosted:
        raise Forbidden("Subscriptions apply to the hosted edition only.", code="not_hosted")
    plan = plans_mod.get_plan(engine, plan_id)
    if plan["status"] != "active" or plan["contact_sales"]:
        raise Invalid("This plan cannot be bought online." + (" Contact sales." if plan["contact_sales"] else ""),
                      code="contact_sales" if plan["contact_sales"] else "plan_unavailable")
    if interval not in ("month", "year") or (plan["billing_interval"] != "both" and plan["billing_interval"] != interval):
        raise Invalid("This plan is not offered with that billing interval.")
    if plan["monthly_price"] == 0 and plan["annual_price"] == 0:
        raise Invalid("A free plan needs no checkout.", code="free_plan")
    live = plans_mod.current_subscription(engine, user["id"])
    if live and live["source"] == "stripe":
        raise Conflict("You already have a subscription. Change it from the billing portal.", code="already_subscribed")
    c = client(engine)
    customer = await ensure_customer(engine, user)
    promo = None
    if coupon_code:
        coupon = coupons_mod.validate(engine, code=coupon_code, user_id=user["id"], plan_id=plan["id"],
                                      amount=plan["monthly_price"] if interval == "month" else plan["annual_price"])
        if not coupon.get("stripe_promotion_code_id"):
            raise Invalid("This coupon is not synced with Stripe yet.", code="coupon_not_synced")
        promo = coupon["stripe_promotion_code_id"]
    base = engine.settings.public_url.rstrip("/")
    session = await c.create_checkout_session(
        customer=customer, price=_price_for(plan, interval), user_id=user["id"], plan_id=plan["id"],
        success_url=f"{base}/#/settings/plan?checkout=success", cancel_url=f"{base}/#/pricing?checkout=cancelled",
        trial_days=int(plan.get("trial_days") or 0), promotion_code=promo,
        allow_promotion_codes=bool(get_setting(engine, "billing.allow_promotion_codes")),
        tax=bool(get_setting(engine, "stripe.tax_enabled")))
    engine.audit(None, user["id"], "billing.checkout.started", session.get("id"), {"plan": plan["code"], "interval": interval,
                 "coupon": coupon_code}, resource_type="checkout", decision="allowed", result="ok")
    return {"url": session.get("url"), "id": session.get("id")}


async def portal(engine: Engine, user: dict[str, Any]) -> dict[str, Any]:
    if not get_setting(engine, "stripe.portal_enabled"):
        raise Forbidden("The billing portal is switched off.", code="portal_off")
    c = client(engine)
    customer = await ensure_customer(engine, user)
    base = engine.settings.public_url.rstrip("/")
    session = await c.create_portal_session(customer=customer, return_url=f"{base}/#/settings/plan")
    return {"url": session.get("url")}


async def cancel_from_user(engine: Engine, user: dict[str, Any]) -> dict[str, Any]:
    sub = plans_mod.current_subscription(engine, user["id"])
    if sub is None:
        raise NotFound("No subscription to cancel.")
    if sub["source"] != "stripe":
        return plans_mod.cancel(engine, sub["id"], by=user["id"], at_period_end=True)
    c = client(engine)
    stripe_sub = await c.cancel_subscription(sub["stripe_subscription_id"], at_period_end=True)
    _upsert_from_stripe(engine, stripe_sub, event_created=now())
    engine.audit(None, user["id"], "subscription.cancel", sub["id"], {"at_period_end": True}, resource_type="subscription",
                 decision="allowed", result="ok")
    return engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (sub["id"],))


async def change_plan_from_user(engine: Engine, user: dict[str, Any], *, plan_id: str, interval: str) -> dict[str, Any]:
    sub = plans_mod.current_subscription(engine, user["id"])
    if sub is None or sub["source"] != "stripe":
        raise NotFound("No Stripe subscription to change; start a checkout instead.", code="no_subscription")
    plan = plans_mod.get_plan(engine, plan_id)
    price = _price_for(plan, interval)
    c = client(engine)
    current = await c.retrieve_subscription(sub["stripe_subscription_id"])
    items = (current.get("items") or {}).get("data") or []
    if not items:
        raise Invalid("The Stripe subscription has no items to change.")
    old = plans_mod.get_plan(engine, sub["plan_id"])
    upgrade = plan["monthly_price"] > old["monthly_price"]
    updated = await c.change_price(sub["stripe_subscription_id"], items[0]["id"], price,
                                   proration="create_prorations" if upgrade else "none")
    updated.setdefault("metadata", {})["plan_id"] = plan["id"]
    _upsert_from_stripe(engine, updated, event_created=now())
    engine.audit(None, user["id"], "subscription.upgrade" if upgrade else "subscription.downgrade", sub["id"],
                 {"from": old["code"], "to": plan["code"]}, resource_type="subscription", decision="allowed", result="ok")
    return engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (sub["id"],))


async def sync_plan(engine: Engine, plan_id: str, *, by: str) -> dict[str, Any]:
    """Creates the Stripe product and prices for a plan that has none (owner action)."""
    plan = plans_mod.get_plan(engine, plan_id)
    c = client(engine)
    changes: dict[str, Any] = {}
    product = plan.get("stripe_product_id")
    if not product:
        product = (await c.create_product(name=plan["name"], plan_id=plan["id"]))["id"]
        changes["stripe_product_id"] = product
    if plan["monthly_price"] and not plan.get("stripe_price_monthly") and plan["billing_interval"] in ("month", "both"):
        changes["stripe_price_monthly"] = (await c.create_price(product=product, amount=plan["monthly_price"],
                                                                currency=plan["currency"], interval="month", plan_id=plan["id"]))["id"]
    if plan["annual_price"] and not plan.get("stripe_price_annual") and plan["billing_interval"] in ("year", "both"):
        changes["stripe_price_annual"] = (await c.create_price(product=product, amount=plan["annual_price"],
                                                               currency=plan["currency"], interval="year", plan_id=plan["id"]))["id"]
    if changes:
        plans_mod.update_plan(engine, plan["id"], changes, by=by)
    return plans_mod.get_plan(engine, plan["id"])


# --- webhooks --------------------------------------------------------------------------------------------------------

def receive_webhook(engine: Engine, payload: bytes, signature: str) -> dict[str, Any]:
    """Verifies, stores and processes one webhook delivery. Raises Forbidden for a bad signature; a duplicate is
    acknowledged without processing (Stripe retries until it sees a 2xx, so this is what makes retries safe)."""
    secret = get_secret(engine, "stripe.webhook_secret")
    if not secret:
        raise Forbidden("Webhooks are not configured.", code="webhook_unconfigured")
    if not signature or not verify_signature(secret, payload, signature):
        engine.audit(None, None, "billing.webhook.rejected", None, {"reason": "bad_signature"}, resource_type="webhook",
                     decision="denied", result="refused")
        raise Forbidden("Bad webhook signature.", code="bad_signature")
    try:
        event = parse_event(payload)
    except ValueError:
        raise Invalid("Not a Stripe event.") from None
    with engine.db.tx() as c:
        inserted = c.execute("INSERT OR IGNORE INTO stripe_events (id, type, created, received_at, status, payload) VALUES "
                             "(?,?,?,?,?,?)", (event["id"], event["type"], event.get("created"), now(), "received",
                                                payload.decode("utf-8", "replace")[:200_000])).rowcount
    if not inserted:
        return {"received": True, "duplicate": True}
    outcome = process_event(engine, event)
    return {"received": True, "duplicate": False, **outcome}


def process_event(engine: Engine, event: dict[str, Any]) -> dict[str, Any]:
    eid = event["id"]
    try:
        result = _dispatch(engine, event)
    except Exception as exc:  # noqa: BLE001 - recorded and retried by the scheduler
        log.warning("stripe event %s (%s) failed: %s", eid, event["type"], exc)
        attempts = (engine.db.scalar("SELECT attempts FROM stripe_events WHERE id = ?", (eid,)) or 0) + 1
        status = "dead_letter" if attempts >= MAX_EVENT_ATTEMPTS else "failed"
        engine.db.update("stripe_events", eid, {"status": status, "attempts": attempts, "error": str(exc)[:500]})
        if status == "dead_letter":
            from .notifications import owner_alert
            owner_alert(engine, "billing_event_failed", f"A Stripe event could not be processed: {event['type']} ({eid})",
                        category="billing", link="#/owner/billing")
        return {"processed": False, "status": status}
    engine.db.update("stripe_events", eid, {"status": "processed" if result != "ignored" else "ignored", "processed_at": now(),
                                            "attempts": (engine.db.scalar("SELECT attempts FROM stripe_events WHERE id = ?", (eid,)) or 0) + 1})
    return {"processed": result != "ignored", "status": result}


def retry_failed(engine: Engine) -> int:
    n = 0
    for row in engine.db.all("SELECT id, payload FROM stripe_events WHERE status = 'failed' ORDER BY received_at LIMIT 20"):
        try:
            event = parse_event(row["payload"].encode())
        except ValueError:
            engine.db.update("stripe_events", row["id"], {"status": "dead_letter", "error": "unparseable"})
            continue
        process_event(engine, event)
        n += 1
    return n


def _dispatch(engine: Engine, event: dict[str, Any]) -> str:
    kind = event["type"]
    obj = (event.get("data") or {}).get("object") or {}
    created = float(event.get("created") or now())
    if kind not in HANDLED:
        return "ignored"
    if kind == "checkout.session.completed":
        if obj.get("mode") != "subscription":
            return "ignored"
        user_id = (obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id")
        if user_id and obj.get("customer"):
            with engine.db.tx() as c:
                c.execute("INSERT OR IGNORE INTO billing_customers (user_id, stripe_customer_id, created_at) VALUES (?,?,?)",
                          (user_id, obj["customer"], now()))
        # the subscription itself arrives in customer.subscription.created; when it is embedded, apply it now
        sub = obj.get("subscription")
        if isinstance(sub, dict):
            _upsert_from_stripe(engine, sub, event_created=created, checkout=obj)
        engine.audit(None, user_id, "billing.checkout.completed", obj.get("id"), {"customer": obj.get("customer")},
                     resource_type="checkout", decision="allowed", result="ok")
        return "checkout"
    if kind.startswith("customer.subscription."):
        if kind == "customer.subscription.trial_will_end":
            _trial_ending(engine, obj)
            return "notified"
        return _upsert_from_stripe(engine, obj, event_created=created)
    if kind in ("invoice.paid", "invoice.payment_succeeded", "invoice.payment_failed"):
        return _invoice(engine, obj, failed=kind == "invoice.payment_failed", event_created=created)
    if kind == "charge.refunded":
        inv = obj.get("invoice")
        if inv:
            engine.db.execute("UPDATE invoices SET amount_refunded = ?, status = CASE WHEN ? >= amount_paid THEN 'refunded' ELSE status END, "
                              "updated_at = ? WHERE id = ?", (int(obj.get("amount_refunded") or 0), int(obj.get("amount_refunded") or 0), now(), inv))
        return "refund"
    return "ignored"


def _user_for(engine: Engine, stripe_sub: dict[str, Any], checkout: dict[str, Any] | None = None) -> str | None:
    meta = stripe_sub.get("metadata") or {}
    uid = meta.get("user_id") or (checkout or {}).get("client_reference_id")
    if uid and engine.db.one("SELECT 1 FROM users WHERE id = ?", (uid,)):
        return uid
    cust = stripe_sub.get("customer")
    cust_id = cust.get("id") if isinstance(cust, dict) else cust
    row = engine.db.one("SELECT user_id FROM billing_customers WHERE stripe_customer_id = ?", (cust_id,)) if cust_id else None
    return row["user_id"] if row else None


def _plan_for(engine: Engine, stripe_sub: dict[str, Any]) -> dict[str, Any] | None:
    meta = stripe_sub.get("metadata") or {}
    if meta.get("plan_id"):
        try:
            return plans_mod.get_plan(engine, meta["plan_id"])
        except NotFound:
            pass
    items = (stripe_sub.get("items") or {}).get("data") or []
    for item in items:
        price = item.get("price") or {}
        pid = price.get("id") if isinstance(price, dict) else price
        if pid:
            row = engine.db.one("SELECT id FROM plans WHERE stripe_price_monthly = ? OR stripe_price_annual = ?", (pid, pid))
            if row:
                return plans_mod.get_plan(engine, row["id"])
    return None


def _interval(stripe_sub: dict[str, Any]) -> str | None:
    items = (stripe_sub.get("items") or {}).get("data") or []
    for item in items:
        rec = ((item.get("price") or {}).get("recurring") or {}) if isinstance(item.get("price"), dict) else {}
        if rec.get("interval"):
            return rec["interval"]
    return None


def _upsert_from_stripe(engine: Engine, stripe_sub: dict[str, Any], *, event_created: float,
                        checkout: dict[str, Any] | None = None) -> str:
    sid = stripe_sub.get("id")
    if not sid:
        return "ignored"
    existing = engine.db.one("SELECT * FROM subscriptions WHERE stripe_subscription_id = ?", (sid,))
    if existing and existing.get("last_event_at") and event_created < existing["last_event_at"]:
        return "ignored"   # an older event delivered late: the newer state already applied wins
    user_id = _user_for(engine, stripe_sub, checkout)
    plan = _plan_for(engine, stripe_sub)
    if user_id is None or plan is None:
        raise RuntimeError(f"cannot map subscription {sid} to a user and a plan")
    status = STATUS_MAP.get(stripe_sub.get("status") or "", "incomplete")
    if status == "incomplete":
        return "ignored"   # a checkout that never completed never provisions anything
    cust = stripe_sub.get("customer")
    cust_id = cust.get("id") if isinstance(cust, dict) else cust
    ts = now()
    previous_status = existing["status"] if existing else None
    previous_plan = existing["plan_id"] if existing else None
    values = {"user_id": user_id, "plan_id": plan["id"], "status": status, "interval": _interval(stripe_sub) or "month",
              "source": "stripe", "stripe_customer_id": cust_id,
              "current_period_start": stripe_sub.get("current_period_start"), "current_period_end": stripe_sub.get("current_period_end"),
              "cancel_at_period_end": 1 if stripe_sub.get("cancel_at_period_end") else 0,
              "canceled_at": stripe_sub.get("canceled_at") if status == "canceled" else None,
              "trial_end": stripe_sub.get("trial_end"), "last_event_at": event_created, "updated_at": ts}
    if status == "past_due":
        values["grace_until"] = (existing or {}).get("grace_until") or ts + int(get_setting(engine, "billing.grace_days")) * 86400
    else:
        values["grace_until"] = None
    with engine.db.tx() as c:
        if existing:
            sets = ", ".join(f"{k} = ?" for k in values)
            c.execute(f"UPDATE subscriptions SET {sets} WHERE id = ?", [*values.values(), existing["id"]])
            local_id = existing["id"]
        else:
            # one Stripe subscription per account: an owner-assigned one gives way to the paid one
            c.execute("UPDATE subscriptions SET status = 'canceled', canceled_at = ?, updated_at = ? WHERE user_id = ? AND "
                      "source != 'stripe' AND status IN ('trialing','active','past_due')", (ts, ts, user_id))
            local_id = new_id("sub_")
            cols = {"id": local_id, "stripe_subscription_id": sid, "created_at": ts, **values}
            c.execute(f"INSERT INTO subscriptions ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", list(cols.values()))
    if not existing:
        _record_coupon(engine, stripe_sub, user_id=user_id, local_id=local_id, plan_id=plan["id"], checkout=checkout)
    engine.audit(None, user_id, "subscription.synced", local_id, {"status": status, "plan": plan["code"], "stripe": sid},
                 resource_type="subscription", decision="allowed", result="ok")
    plans_mod.enforce_workspace_limit(engine, user_id)
    _lifecycle_events(engine, user_id, plan, status, previous_status, previous_plan)
    return "synced"


def _record_coupon(engine: Engine, stripe_sub: dict[str, Any], *, user_id: str, local_id: str, plan_id: str,
                   checkout: dict[str, Any] | None) -> None:
    """A discount on a new subscription is one redemption of our coupon (matched by the metadata Stripe carries)."""
    from . import coupons as coupons_mod

    disc = stripe_sub.get("discount") or {}
    coupon_obj = disc.get("coupon") if isinstance(disc, dict) else None
    stripe_coupon = coupon_obj.get("id") if isinstance(coupon_obj, dict) else coupon_obj
    local = None
    if isinstance(coupon_obj, dict) and (coupon_obj.get("metadata") or {}).get("coupon_id"):
        local = engine.db.one("SELECT id FROM coupons WHERE id = ?", (coupon_obj["metadata"]["coupon_id"],))
    if local is None and stripe_coupon:
        local = engine.db.one("SELECT id FROM coupons WHERE stripe_coupon_id = ?", (stripe_coupon,))
    if local is None:
        return
    amount = int(((checkout or {}).get("total_details") or {}).get("amount_discount") or 0)
    if not amount:   # the discount on the first period, from our own coupon terms and the plan's price
        c = coupons_mod.get(engine, local["id"])
        plan = plans_mod.get_plan(engine, plan_id)
        price = plan["annual_price"] if _interval(stripe_sub) == "year" else plan["monthly_price"]
        amount = int(round(price * (c["percent_off"] or 0) / 100)) if c["percent_off"] else min(price, int(c["amount_off"] or 0))
    coupons_mod.record_redemption(engine, coupon_id=local["id"], user_id=user_id, subscription_id=local_id, plan_id=plan_id,
                                  amount_discounted=amount)
    engine.db.update("subscriptions", local_id, {"coupon_id": local["id"]})


def _lifecycle_events(engine: Engine, user_id: str, plan: dict[str, Any], status: str, previous_status: str | None,
                      previous_plan: str | None) -> None:
    from .notifications import owner_alert, user_event
    user = engine.db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        return
    first_paid = previous_status is None and status in ("active", "trialing") and (plan["monthly_price"] or plan["annual_price"])
    if first_paid:
        owner_alert(engine, "first_paid_subscription", f"{user['email']} subscribed to {plan['name']}", category="billing",
                    link="#/owner/subscriptions")
        user_event(engine, user, "subscription_confirmation", {"plan": plan["name"]}, category="billing")
    elif previous_plan and previous_plan != plan["id"]:
        old = plans_mod.get_plan(engine, previous_plan)
        up = plan["monthly_price"] > old["monthly_price"]
        owner_alert(engine, "subscription_upgrade" if up else "subscription_downgrade",
                    f"{user['email']} moved from {old['name']} to {plan['name']}", category="billing", link="#/owner/subscriptions")
        user_event(engine, user, "subscription_confirmation", {"plan": plan["name"]}, category="billing")
    if status == "canceled" and previous_status not in (None, "canceled"):
        owner_alert(engine, "subscription_cancellation", f"{user['email']} cancelled {plan['name']}", category="billing",
                    link="#/owner/subscriptions")
        user_event(engine, user, "subscription_cancellation", {"plan": plan["name"]}, category="billing")


def _trial_ending(engine: Engine, stripe_sub: dict[str, Any]) -> None:
    from .notifications import user_event
    row = engine.db.one("SELECT user_id, plan_id FROM subscriptions WHERE stripe_subscription_id = ?", (stripe_sub.get("id"),))
    if row:
        user = engine.db.one("SELECT * FROM users WHERE id = ?", (row["user_id"],))
        if user:
            user_event(engine, user, "trial_expiration", {"plan": plans_mod.get_plan(engine, row["plan_id"])["name"]},
                       category="billing", dedupe=f"trial-end:{stripe_sub.get('id')}")


def _invoice(engine: Engine, inv: dict[str, Any], *, failed: bool, event_created: float) -> str:
    from .notifications import owner_alert, user_event
    iid = inv.get("id")
    if not iid:
        return "ignored"
    cust = inv.get("customer")
    cust_id = cust.get("id") if isinstance(cust, dict) else cust
    sub = inv.get("subscription")
    sub_id = sub.get("id") if isinstance(sub, dict) else sub
    user = engine.db.one("SELECT user_id FROM billing_customers WHERE stripe_customer_id = ?", (cust_id,)) if cust_id else None
    user_id = user["user_id"] if user else None
    values = {"user_id": user_id, "stripe_customer_id": cust_id, "stripe_subscription_id": sub_id,
              "status": "payment_failed" if failed else (inv.get("status") or "paid"), "amount_due": int(inv.get("amount_due") or 0),
              "amount_paid": int(inv.get("amount_paid") or 0), "currency": (inv.get("currency") or "").upper(),
              "hosted_invoice_url": inv.get("hosted_invoice_url"), "created": inv.get("created"),
              "paid_at": None if failed else (inv.get("status_transitions") or {}).get("paid_at") or event_created,
              "updated_at": now()}
    with engine.db.tx() as c:
        if c.execute("SELECT 1 FROM invoices WHERE id = ?", (iid,)).fetchone():
            sets = ", ".join(f"{k} = ?" for k in values)
            c.execute(f"UPDATE invoices SET {sets} WHERE id = ?", [*values.values(), iid])
        else:
            cols = {"id": iid, **values}
            c.execute(f"INSERT INTO invoices ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", list(cols.values()))
    if sub_id:
        local = engine.db.one("SELECT * FROM subscriptions WHERE stripe_subscription_id = ?", (sub_id,))
        if local and failed and local["status"] in ("active", "trialing"):
            engine.db.update("subscriptions", local["id"], {"status": "past_due", "grace_until": now() + int(get_setting(engine, "billing.grace_days")) * 86400,
                                                            "updated_at": now()})
        elif local and not failed and local["status"] == "past_due":
            engine.db.update("subscriptions", local["id"], {"status": "active", "grace_until": None, "updated_at": now()})
            plans_mod.enforce_workspace_limit(engine, local["user_id"])
    engine.audit(None, user_id, "billing.invoice.failed" if failed else "billing.invoice.paid", iid,
                 {"amount": values["amount_due"] if failed else values["amount_paid"], "currency": values["currency"]},
                 resource_type="invoice", decision="allowed", result="ok")
    if user_id:
        u = engine.db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if u:
            amount = f"{(values['amount_due'] if failed else values['amount_paid']) / 100:.2f} {values['currency']}"
            user_event(engine, u, "payment_failure" if failed else "payment_success", {"amount": amount, "invoice_url": values["hosted_invoice_url"] or ""},
                       category="billing", dedupe=f"invoice:{iid}:{'failed' if failed else 'paid'}")
    if failed:
        owner_alert(engine, "payment_failed", f"A payment failed for {u['email'] if user_id and u else cust_id}", category="billing",
                    link="#/owner/billing")
    return "invoice"


async def reconcile(engine: Engine, subscription_id: str, *, by: str) -> dict[str, Any]:
    """Pulls the subscription from Stripe and applies it, whatever the webhooks delivered."""
    sub = engine.db.one("SELECT * FROM subscriptions WHERE id = ? OR stripe_subscription_id = ?", (subscription_id, subscription_id))
    if sub is None or not sub.get("stripe_subscription_id"):
        raise NotFound("No Stripe subscription with that id.")
    c = client(engine)
    stripe_sub = await c.retrieve_subscription(sub["stripe_subscription_id"])
    stripe_sub.setdefault("metadata", {}).setdefault("plan_id", sub["plan_id"])
    stripe_sub["metadata"].setdefault("user_id", sub["user_id"])
    outcome = _upsert_from_stripe(engine, stripe_sub, event_created=now())
    engine.audit(None, by, "billing.reconciled", sub["id"], {"outcome": outcome}, resource_type="subscription", decision="allowed",
                 result="ok")
    return engine.db.one("SELECT * FROM subscriptions WHERE id = ?", (sub["id"],))


# --- reporting --------------------------------------------------------------------------------------------------------

def mrr(engine: Engine) -> dict[str, Any]:
    """Monthly recurring revenue in minor units per currency, from active and past-due (still within grace) paid
    subscriptions; annual plans count a twelfth. Trials are not revenue."""
    out: dict[str, int] = {}
    rows = engine.db.all("SELECT s.interval, s.status, s.grace_until, p.monthly_price, p.annual_price, p.currency FROM subscriptions s "
                         "JOIN plans p ON p.id = s.plan_id WHERE s.status IN ('active','past_due')")
    for r in rows:
        if r["status"] == "past_due" and r["grace_until"] and r["grace_until"] < now():
            continue
        amount = r["annual_price"] // 12 if r["interval"] == "year" else r["monthly_price"]
        out[r["currency"]] = out.get(r["currency"], 0) + amount
    return {"by_currency": out, "arr_by_currency": {k: v * 12 for k, v in out.items()}}


def list_events(engine: Engine, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT id, type, created, received_at, processed_at, status, attempts, error FROM stripe_events"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    return engine.db.all(sql + " ORDER BY received_at DESC LIMIT ?", [*params, limit])


def list_invoices(engine: Engine, *, user_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    sql = "SELECT i.*, u.email FROM invoices i LEFT JOIN users u ON u.id = i.user_id"
    params: list[Any] = []
    if user_id:
        sql += " WHERE i.user_id = ?"
        params.append(user_id)
    return engine.db.all(sql + " ORDER BY i.created DESC LIMIT ?", [*params, limit])

