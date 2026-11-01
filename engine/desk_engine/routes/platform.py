"""Platform routes for signed-in people and the public: plans and pricing, the person's own plan and billing,
Contact Sales, the analytics collector, the Stripe webhook, platform notifications and preferences.

Nothing here changes a subscription on the client's say-so: checkout and the portal are Stripe-hosted, and the
local state follows Stripe's signed webhooks (``platform/billing.py``)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, PlainTextResponse

from ..errors import Forbidden, Invalid
from ..platform import analytics, billing, coupons, notifications, plans, sales
from ..platform.settings import get_setting
from ..web import BodyTooLarge, Ctx, Router, read_body

router = Router()


def _public_plan(p: dict[str, Any]) -> dict[str, Any]:
    return {k: p[k] for k in ("id", "code", "name", "description", "monthly_price", "annual_price", "currency", "max_workspaces",
                              "storage_quota_mb", "max_members", "features", "trial_days", "billing_interval", "contact_sales",
                              "position") if k in p}


@router.get("/api/plans", auth=False)
async def public_plans(ctx: Ctx) -> dict[str, Any]:
    """The pricing page: public, active plans and what Enterprise really offers (each capability marked by its state)."""
    return {"hosted": ctx.engine.settings.hosted, "plans": [_public_plan(p) for p in plans.list_plans(ctx.engine, public_only=True)],
            "enterprise": plans.ENTERPRISE_CAPABILITIES, "billing": billing.enabled(ctx.engine) and ctx.engine.settings.hosted,
            "currencies": get_setting(ctx.engine, "stripe.currencies"), "features": plans.FEATURES}


@router.get("/api/me/plan")
async def my_plan(ctx: Ctx) -> dict[str, Any]:
    sub = plans.current_subscription(ctx.engine, ctx.user_id)
    plan = plans.plan_for(ctx.engine, ctx.user_id)
    return {"subscription": sub, "plan": _public_plan(plan) if plan else None, "entitlements": plans.entitlements_for(ctx.engine, ctx.user_id),
            "history": plans.subscription_history(ctx.engine, ctx.user_id)[:20],
            "invoices": billing.list_invoices(ctx.engine, user_id=ctx.user_id, limit=24),
            "ai_tokens_this_month": plans.ai_tokens_this_month(ctx.engine, ctx.user_id),
            "billing": billing.enabled(ctx.engine) and ctx.engine.settings.hosted,
            "portal": bool(get_setting(ctx.engine, "stripe.portal_enabled"))}


class CheckoutIn(BaseModel):
    plan_id: str = Field(max_length=60)
    interval: Literal["month", "year"] = "month"
    coupon: str | None = Field(default=None, max_length=40)


@router.post("/api/billing/checkout", body=CheckoutIn)
async def checkout(ctx: Ctx) -> dict[str, Any]:
    return await billing.start_checkout(ctx.engine, ctx.user, plan_id=ctx.body.plan_id, interval=ctx.body.interval,
                                        coupon_code=ctx.body.coupon)


@router.post("/api/billing/portal")
async def portal(ctx: Ctx) -> dict[str, Any]:
    return await billing.portal(ctx.engine, ctx.user)


@router.post("/api/billing/cancel", fresh=True)
async def cancel(ctx: Ctx) -> dict[str, Any]:
    return await billing.cancel_from_user(ctx.engine, ctx.user)


class ChangeIn(BaseModel):
    plan_id: str = Field(max_length=60)
    interval: Literal["month", "year"] = "month"


@router.post("/api/billing/change", body=ChangeIn, fresh=True)
async def change(ctx: Ctx) -> dict[str, Any]:
    return await billing.change_plan_from_user(ctx.engine, ctx.user, plan_id=ctx.body.plan_id, interval=ctx.body.interval)


class CouponCheckIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    plan_id: str = Field(max_length=60)
    interval: Literal["month", "year"] = "month"


@router.post("/api/billing/coupon-check", body=CouponCheckIn)
async def coupon_check(ctx: Ctx) -> dict[str, Any]:
    from .auth import _rate_limit

    _rate_limit(ctx, f"coupon:{ctx.user_id}", limit=20, window=600)
    plan = plans.get_plan(ctx.engine, ctx.body.plan_id)
    amount = plan["monthly_price"] if ctx.body.interval == "month" else plan["annual_price"]
    c = coupons.validate(ctx.engine, code=ctx.body.code, user_id=ctx.user_id, plan_id=plan["id"], amount=amount)
    discount = int(round(amount * c["percent_off"] / 100)) if c["percent_off"] else min(amount, c["amount_off"] or 0)
    return {"valid": True, "code": c["code"], "percent_off": c["percent_off"], "amount_off": c["amount_off"], "duration": c["duration"],
            "duration_months": c["duration_months"], "discount": discount, "total": max(0, amount - discount), "currency": plan["currency"]}


@router.post("/api/billing/webhook", auth=False)
async def stripe_webhook(ctx: Ctx) -> Any:
    """Stripe calls this. Authentication is the signature over the raw body; everything else is refused."""
    try:
        payload = await read_body(ctx.request, 1024 * 1024)
    except BodyTooLarge:
        raise Invalid("Payload too large.") from None
    result = billing.receive_webhook(ctx.engine, payload, ctx.request.headers.get("stripe-signature", ""))
    return JSONResponse(result, status_code=200)


# --- Contact Sales ----------------------------------------------------------------------------------------------------

class LeadIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    company: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, max_length=2)
    company_size: str | None = Field(default=None, max_length=20)
    users_estimate: int | None = Field(default=None, ge=0, le=1_000_000)
    workspaces_needed: int | None = Field(default=None, ge=0, le=1_000_000)
    storage_estimate: str | None = Field(default=None, max_length=60)
    features: list[str] = Field(default_factory=list, max_length=20)
    deployment: str | None = Field(default=None, max_length=20)
    message: str | None = Field(default=None, max_length=4000)
    lang: str | None = Field(default=None, max_length=5)
    website: str | None = Field(default=None, max_length=200)   # honeypot: real people leave it empty


@router.post("/api/sales/contact", auth=False, body=LeadIn)
async def contact_sales(ctx: Ctx) -> dict[str, Any]:
    from .auth import _rate_limit

    if ctx.body.website:
        return {"id": None, "status": "NEW"}   # a bot filled the hidden field: pretend, store nothing
    host = ctx.request.client.host if ctx.request.client else "-"
    _rate_limit(ctx, f"lead-ip:{host}", limit=5, window=3600)
    values = ctx.body.model_dump(exclude={"website"})
    source = "app" if ctx.user else "web"
    return sales.submit(ctx.engine, values, source=source)


# --- analytics collector --------------------------------------------------------------------------------------------------

class CollectIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    path: str | None = Field(default=None, max_length=200)
    referrer: str | None = Field(default=None, max_length=500)
    lang: str | None = Field(default=None, max_length=10)
    consent: bool = False
    props: dict[str, Any] | None = None


@router.post("/api/analytics/collect", auth=False, body=CollectIn)
async def collect(ctx: Ctx) -> Any:
    from .auth import _rate_limit

    host = ctx.request.client.host if ctx.request.client else "-"
    _rate_limit(ctx, f"collect-ip:{host}", limit=240, window=60)
    stored = analytics.collect_web(ctx.engine, name=ctx.body.name, path=ctx.body.path, referrer=ctx.body.referrer or ctx.request.headers.get("referer"),
                                   lang=ctx.body.lang, consent=ctx.body.consent, client_host=host,
                                   user_agent=ctx.request.headers.get("user-agent", ""),
                                   country_header=ctx.request.headers.get("cf-ipcountry") or ctx.request.headers.get("x-country"),
                                   props=ctx.body.props)
    return PlainTextResponse("", status_code=204 if stored else 202)


@router.get("/api/analytics/policy", auth=False)
async def analytics_policy(ctx: Ctx) -> dict[str, Any]:
    return {"enabled": bool(get_setting(ctx.engine, "analytics.enabled")), "require_consent": bool(get_setting(ctx.engine, "analytics.require_consent")),
            "collects": ["page views", "coarse device and browser class", "referrer host", "country (from the proxy, if enabled)", "language"],
            "never": ["IP addresses", "names or e-mail addresses", "financial data", "prompts or answers", "documents"]}


# --- platform notifications ---------------------------------------------------------------------------------------------

@router.get("/api/me/notifications")
async def my_notifications(ctx: Ctx) -> dict[str, Any]:
    items = notifications.list_for_user(ctx.engine, ctx.user_id, unread_only=ctx.request.query_params.get("unread") == "1")
    return {"items": items, "unread": sum(1 for i in items if not i["read_at"])}


class ReadIn(BaseModel):
    ids: list[str] | None = None


@router.post("/api/me/notifications/read", body=ReadIn)
async def read_notifications(ctx: Ctx) -> dict[str, Any]:
    return {"marked": notifications.mark_read(ctx.engine, ctx.user_id, ctx.body.ids)}


@router.get("/api/me/notification-preferences")
async def my_preferences(ctx: Ctx) -> dict[str, Any]:
    return {"categories": notifications.CATEGORIES, "mandatory_in_app": sorted(notifications.MANDATORY_IN_APP),
            "preferences": notifications.preferences(ctx.engine, ctx.user_id)}


class PrefsIn(BaseModel):
    preferences: dict[str, dict[str, bool]]


@router.put("/api/me/notification-preferences", body=PrefsIn)
async def set_my_preferences(ctx: Ctx) -> dict[str, Any]:
    return {"preferences": notifications.set_preferences(ctx.engine, ctx.user_id, ctx.body.preferences)}


@router.get("/api/me/entitlements")
async def my_entitlements(ctx: Ctx) -> dict[str, Any]:
    if ctx.engine.settings.is_desktop:
        raise Forbidden("The desktop app has no plans.")
    return plans.entitlements_for(ctx.engine, ctx.user_id)
