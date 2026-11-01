"""A small Stripe client over the REST API (form-encoded, idempotency keys, no SDK) and webhook signature checks.

Only the endpoints the billing service needs are wrapped. The client takes the engine's HTTP transport, so tests run
it against a mock Stripe (``tests/stripe_mock.py``) through exactly the same code path, and a sealed server's egress
guard applies to it like to every other outbound call. Card numbers never pass through here: Checkout and the
Customer Portal are Stripe-hosted pages.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

from ..db import new_id
from ..errors import UpstreamError

API = "https://api.stripe.com/v1"
SIGNATURE_TOLERANCE = 300   # seconds, Stripe's own recommendation


def _flatten(prefix: str, value: Any, out: list[tuple[str, str]]) -> None:
    """Stripe's bracket encoding: ``line_items[0][price]=…``."""
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]" if prefix else str(k), v, out)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif isinstance(value, bool):
        out.append((prefix, "true" if value else "false"))
    elif value is not None:
        out.append((prefix, str(value)))


def encode(params: dict[str, Any]) -> str:
    out: list[tuple[str, str]] = []
    _flatten("", params, out)
    return urlencode(out)


class StripeClient:
    def __init__(self, secret_key: str, *, transport: Any = None, timeout: float = 20) -> None:
        self.secret_key = secret_key
        self.transport = transport
        self.timeout = timeout

    @property
    def live(self) -> bool:
        return self.secret_key.startswith("sk_live_") or self.secret_key.startswith("rk_live_")

    async def _call(self, method: str, path: str, params: dict[str, Any] | None = None, *, idempotency_key: str | None = None) -> dict[str, Any]:
        import httpx2 as httpx

        headers = {"Authorization": f"Bearer {self.secret_key}", "Stripe-Version": "2024-06-20"}
        if idempotency_key and method == "POST":
            headers["Idempotency-Key"] = idempotency_key
        body = encode(params or {}) if method == "POST" else None
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        url = f"{API}{path}" + (f"?{encode(params)}" if method == "GET" and params else "")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.request(method, url, headers=headers, content=body)
        except Exception as exc:
            raise UpstreamError(f"Stripe could not be reached: {type(exc).__name__}.", code="stripe_unreachable",
                                status=502) from exc
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
            raise UpstreamError(f"Stripe refused the request: {err.get('message') or resp.status_code}",
                                code="stripe_error", status=502, details={"type": err.get("type"), "code": err.get("code")})
        return data

    # --- customers, checkout, portal -----------------------------------------------------------------------------
    async def create_customer(self, *, email: str, name: str, user_id: str) -> dict[str, Any]:
        return await self._call("POST", "/customers", {"email": email, "name": name, "metadata": {"user_id": user_id}},
                                idempotency_key=f"cust-{user_id}")

    async def create_checkout_session(self, *, customer: str, price: str, success_url: str, cancel_url: str,
                                      user_id: str, plan_id: str, trial_days: int = 0, promotion_code: str | None = None,
                                      allow_promotion_codes: bool = True, tax: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {
            "mode": "subscription", "customer": customer, "line_items": [{"price": price, "quantity": 1}],
            "success_url": success_url, "cancel_url": cancel_url, "client_reference_id": user_id,
            "subscription_data": {"metadata": {"user_id": user_id, "plan_id": plan_id}},
            "metadata": {"user_id": user_id, "plan_id": plan_id},
        }
        if trial_days:
            params["subscription_data"]["trial_period_days"] = trial_days
        if promotion_code:
            params["discounts"] = [{"promotion_code": promotion_code}]   # Stripe: discounts and allow_promotion_codes are exclusive
        elif allow_promotion_codes:
            params["allow_promotion_codes"] = True
        if tax:
            params["automatic_tax"] = {"enabled": True}
        return await self._call("POST", "/checkout/sessions", params, idempotency_key=new_id("co-"))

    async def create_portal_session(self, *, customer: str, return_url: str) -> dict[str, Any]:
        return await self._call("POST", "/billing_portal/sessions", {"customer": customer, "return_url": return_url})

    # --- subscriptions -------------------------------------------------------------------------------------------
    async def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/subscriptions/{subscription_id}")

    async def cancel_subscription(self, subscription_id: str, *, at_period_end: bool = True) -> dict[str, Any]:
        if at_period_end:
            return await self._call("POST", f"/subscriptions/{subscription_id}", {"cancel_at_period_end": True})
        return await self._call("DELETE", f"/subscriptions/{subscription_id}")

    async def resume_subscription(self, subscription_id: str) -> dict[str, Any]:
        return await self._call("POST", f"/subscriptions/{subscription_id}", {"cancel_at_period_end": False})

    async def change_price(self, subscription_id: str, item_id: str, price: str, *, proration: str = "create_prorations") -> dict[str, Any]:
        return await self._call("POST", f"/subscriptions/{subscription_id}",
                                {"items": [{"id": item_id, "price": price}], "proration_behavior": proration})

    async def list_invoices(self, customer: str, limit: int = 24) -> dict[str, Any]:
        return await self._call("GET", "/invoices", {"customer": customer, "limit": limit})

    # --- catalogue -----------------------------------------------------------------------------------------------
    async def create_product(self, *, name: str, plan_id: str) -> dict[str, Any]:
        return await self._call("POST", "/products", {"name": name, "metadata": {"plan_id": plan_id}}, idempotency_key=f"prod-{plan_id}")

    async def create_price(self, *, product: str, amount: int, currency: str, interval: str, plan_id: str) -> dict[str, Any]:
        return await self._call("POST", "/prices", {"product": product, "unit_amount": amount, "currency": currency.lower(),
                                                    "recurring": {"interval": interval}, "metadata": {"plan_id": plan_id}},
                                idempotency_key=f"price-{plan_id}-{interval}-{amount}-{currency}")

    # --- coupons -------------------------------------------------------------------------------------------------
    async def create_coupon(self, *, coupon_id: str, percent_off: float | None, amount_off: int | None, currency: str | None,
                            duration: str, duration_in_months: int | None, max_redemptions: int | None,
                            redeem_by: int | None, name: str) -> dict[str, Any]:
        params: dict[str, Any] = {"duration": duration, "name": name, "metadata": {"coupon_id": coupon_id}}
        if percent_off is not None:
            params["percent_off"] = percent_off
        else:
            params["amount_off"] = amount_off
            params["currency"] = (currency or "usd").lower()
        if duration == "repeating":
            params["duration_in_months"] = duration_in_months
        if max_redemptions:
            params["max_redemptions"] = max_redemptions
        if redeem_by:
            params["redeem_by"] = redeem_by
        return await self._call("POST", "/coupons", params, idempotency_key=f"coupon-{coupon_id}")

    async def create_promotion_code(self, *, coupon: str, code: str, first_time_only: bool, min_amount: int | None,
                                    currency: str | None, max_redemptions: int | None, expires_at: int | None) -> dict[str, Any]:
        params: dict[str, Any] = {"coupon": coupon, "code": code, "restrictions": {"first_time_transaction": first_time_only}}
        if min_amount:
            params["restrictions"]["minimum_amount"] = min_amount
            params["restrictions"]["minimum_amount_currency"] = (currency or "usd").lower()
        if max_redemptions:
            params["max_redemptions"] = max_redemptions
        if expires_at:
            params["expires_at"] = expires_at
        return await self._call("POST", "/promotion_codes", params, idempotency_key=f"promo-{code}")

    async def set_promotion_code_active(self, promotion_code_id: str, active: bool) -> dict[str, Any]:
        return await self._call("POST", f"/promotion_codes/{promotion_code_id}", {"active": active})

    async def delete_coupon(self, coupon_id: str) -> dict[str, Any]:
        return await self._call("DELETE", f"/coupons/{coupon_id}")

    async def ping(self) -> dict[str, Any]:
        """A harmless authenticated read, for the dashboard's health check."""
        return await self._call("GET", "/balance")


# --- webhooks --------------------------------------------------------------------------------------------------------

def sign(secret: str, payload: bytes, timestamp: int | None = None) -> str:
    """Builds a ``Stripe-Signature`` header (tests and the mock use it)."""
    t = timestamp if timestamp is not None else int(time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify_signature(secret: str, payload: bytes, header: str, *, tolerance: int = SIGNATURE_TOLERANCE,
                     at: float | None = None) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        t = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs((at if at is not None else time.time()) - t) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    candidates = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    return any(hmac.compare_digest(expected, c) for c in candidates)


def parse_event(payload: bytes) -> dict[str, Any]:
    try:
        event = json.loads(payload)
    except ValueError as exc:
        raise ValueError("not JSON") from exc
    if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
        raise ValueError("not a Stripe event")
    return event
