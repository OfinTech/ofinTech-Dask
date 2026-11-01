"""A mock of the parts of Stripe's REST API the billing service uses, mounted on the test Router so the real
``StripeClient`` code path (form encoding, idempotency keys, error handling) runs against it. It also builds signed
webhook events the way Stripe would deliver them.

No network, no real Stripe account: this is the sandbox mode the tests and local development run in."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qsl

import httpx2 as httpx

from desk_engine.platform.stripe import API, sign

WEBHOOK_SECRET = "whsec_test_0123456789abcdef0123456789abcdef"
SECRET_KEY = "sk_test_51MockStripeKeyForTests000000000000"


def _unflatten(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """``line_items[0][price]=x`` → {"line_items": [{"price": "x"}]} (lists appear as dicts keyed by index strings)."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        parts = key.replace("]", "").split("[")
        cur: Any = out
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            if last:
                cur[part] = value
            else:
                cur = cur.setdefault(part, {})
    return out


def _as_list(d: Any) -> list[Any]:
    if isinstance(d, dict):
        return [d[k] for k in sorted(d, key=lambda x: int(x) if x.isdigit() else 0)]
    return list(d or [])


class MockStripe:
    def __init__(self, router: Any) -> None:
        self.customers: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.checkouts: dict[str, dict[str, Any]] = {}
        self.products: dict[str, dict[str, Any]] = {}
        self.prices: dict[str, dict[str, Any]] = {}
        self.coupons: dict[str, dict[str, Any]] = {}
        self.promotion_codes: dict[str, dict[str, Any]] = {}
        self.invoices: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_next: int | None = None
        self.n = 0
        router.add("POST", API, self.handle)
        router.add("GET", API, self.handle)
        router.add("DELETE", API, self.handle)

    def _id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_mock{self.n:05d}"

    # --- dispatch ---------------------------------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = str(request.url).replace(API, "").split("?")[0]
        self.calls.append((request.method, path))
        if request.headers.get("authorization") != f"Bearer {SECRET_KEY}":
            return httpx.Response(401, json={"error": {"type": "invalid_request_error", "message": "Invalid API Key provided"}})
        if self.fail_next:
            status, self.fail_next = self.fail_next, None
            return httpx.Response(status, json={"error": {"type": "api_error", "message": "mock failure"}})
        key = request.headers.get("idempotency-key")
        if key and key in self.idempotency:
            return httpx.Response(200, json=self.idempotency[key])
        params = _unflatten(parse_qsl(request.content.decode())) if request.method == "POST" else dict(request.url.params)
        try:
            body = self._route(request.method, path, params)
        except KeyError as exc:
            return httpx.Response(404, json={"error": {"type": "invalid_request_error", "message": f"No such object: {exc}"}})
        except ValueError as exc:
            return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": str(exc)}})
        if key:
            self.idempotency[key] = body
        return httpx.Response(200, json=body)

    def _route(self, method: str, path: str, p: dict[str, Any]) -> dict[str, Any]:
        parts = [x for x in path.split("/") if x]
        if parts == ["balance"]:
            return {"object": "balance", "available": [{"amount": 0, "currency": "usd"}]}
        if parts == ["customers"] and method == "POST":
            cid = self._id("cus")
            self.customers[cid] = {"id": cid, "object": "customer", "email": p.get("email"), "name": p.get("name"), "metadata": p.get("metadata", {})}
            return self.customers[cid]
        if parts == ["products"] and method == "POST":
            pid = self._id("prod")
            self.products[pid] = {"id": pid, "object": "product", "name": p["name"], "metadata": p.get("metadata", {})}
            return self.products[pid]
        if parts == ["prices"] and method == "POST":
            prid = self._id("price")
            self.prices[prid] = {"id": prid, "object": "price", "product": p["product"], "unit_amount": int(p["unit_amount"]),
                                 "currency": p["currency"], "recurring": {"interval": p["recurring"]["interval"]}, "metadata": p.get("metadata", {})}
            return self.prices[prid]
        if parts == ["checkout", "sessions"] and method == "POST":
            if p.get("discounts") and p.get("allow_promotion_codes"):
                raise ValueError("You may only specify one of these parameters: allow_promotion_codes, discounts.")
            sid = self._id("cs")
            price = _as_list(p["line_items"])[0]["price"]
            if price not in self.prices:
                raise KeyError(price)
            session = {"id": sid, "object": "checkout.session", "mode": p["mode"], "customer": p["customer"], "url": f"https://checkout.stripe.test/{sid}",
                       "client_reference_id": p.get("client_reference_id"), "metadata": p.get("metadata", {}), "price": price,
                       "subscription_data": p.get("subscription_data", {}), "discounts": _as_list(p.get("discounts")), "status": "open"}
            self.checkouts[sid] = session
            return session
        if parts == ["billing_portal", "sessions"] and method == "POST":
            return {"id": self._id("bps"), "object": "billing_portal.session", "url": f"https://billing.stripe.test/{p['customer']}", "return_url": p.get("return_url")}
        if parts[0] == "subscriptions" and len(parts) == 2:
            sub = self.subscriptions[parts[1]]
            if method == "GET":
                return sub
            if method == "DELETE":
                sub["status"] = "canceled"
                sub["canceled_at"] = int(time.time())
                return sub
            if "cancel_at_period_end" in p:
                sub["cancel_at_period_end"] = p["cancel_at_period_end"] == "true"
            if "items" in p:
                for item in _as_list(p["items"]):
                    for it in sub["items"]["data"]:
                        if it["id"] == item["id"]:
                            it["price"] = self.prices[item["price"]]
            return sub
        if parts == ["invoices"] and method == "GET":
            return {"object": "list", "data": [i for i in self.invoices.values() if i["customer"] == p.get("customer")]}
        if parts == ["coupons"] and method == "POST":
            cid = self._id("coupon")
            if "percent_off" not in p and "amount_off" not in p:
                raise ValueError("You must pass either percent_off or amount_off.")
            self.coupons[cid] = {"id": cid, "object": "coupon", "percent_off": float(p["percent_off"]) if "percent_off" in p else None,
                                 "amount_off": int(p["amount_off"]) if "amount_off" in p else None, "currency": p.get("currency"),
                                 "duration": p["duration"], "duration_in_months": int(p["duration_in_months"]) if "duration_in_months" in p else None,
                                 "metadata": p.get("metadata", {}), "name": p.get("name"), "valid": True}
            return self.coupons[cid]
        if parts[0] == "coupons" and len(parts) == 2 and method == "DELETE":
            return {"id": parts[1], "object": "coupon", "deleted": True} | ({} if self.coupons.pop(parts[1], None) is None else {})
        if parts == ["promotion_codes"] and method == "POST":
            if any(pc["code"].upper() == p["code"].upper() and pc["active"] for pc in self.promotion_codes.values()):
                raise ValueError("An active promotion code with `code: " + p["code"] + "` already exists.")
            pid = self._id("promo")
            self.promotion_codes[pid] = {"id": pid, "object": "promotion_code", "code": p["code"], "coupon": self.coupons[p["coupon"]], "active": True,
                                         "restrictions": p.get("restrictions", {}), "max_redemptions": p.get("max_redemptions")}
            return self.promotion_codes[pid]
        if parts[0] == "promotion_codes" and len(parts) == 2 and method == "POST":
            pc = self.promotion_codes[parts[1]]
            if "active" in p:
                pc["active"] = p["active"] == "true"
            return pc
        raise KeyError(path)

    # --- the customer "pays": what Stripe would then send -------------------------------------------------------
    def complete_checkout(self, session_id: str, *, trial_days: int = 0, status: str = "active") -> dict[str, Any]:
        """Creates the subscription a completed Checkout produces and returns it (the webhook events are built from it)."""
        cs = self.checkouts[session_id]
        price = self.prices[cs["price"]]
        sub_id = self._id("sub")
        t = int(time.time())
        period = 365 * 86400 if price["recurring"]["interval"] == "year" else 30 * 86400
        discount = None
        if cs["discounts"]:
            promo = self.promotion_codes[cs["discounts"][0]["promotion_code"]]
            discount = {"object": "discount", "coupon": promo["coupon"], "promotion_code": promo["id"]}
        sub = {"id": sub_id, "object": "subscription", "customer": cs["customer"], "status": "trialing" if trial_days else status,
               "items": {"object": "list", "data": [{"id": self._id("si"), "object": "subscription_item", "price": price}]},
               "metadata": dict((cs.get("subscription_data") or {}).get("metadata") or cs["metadata"]),
               "current_period_start": t, "current_period_end": t + period, "cancel_at_period_end": False, "canceled_at": None,
               "trial_end": t + trial_days * 86400 if trial_days else None, "discount": discount, "created": t}
        self.subscriptions[sub_id] = sub
        cs["status"] = "complete"
        cs["subscription"] = sub_id
        return sub

    def invoice_for(self, sub: dict[str, Any], *, paid: bool, amount: int | None = None) -> dict[str, Any]:
        iid = self._id("in")
        price = sub["items"]["data"][0]["price"]
        amt = amount if amount is not None else price["unit_amount"]
        inv = {"id": iid, "object": "invoice", "customer": sub["customer"], "subscription": sub["id"], "status": "paid" if paid else "open",
               "amount_due": amt, "amount_paid": amt if paid else 0, "currency": price["currency"], "hosted_invoice_url": f"https://invoice.stripe.test/{iid}",
               "created": int(time.time()), "status_transitions": {"paid_at": int(time.time()) if paid else None}}
        self.invoices[iid] = inv
        return inv

    # --- webhook deliveries -----------------------------------------------------------------------------------------------
    def event(self, kind: str, obj: dict[str, Any], *, created: int | None = None, event_id: str | None = None) -> tuple[bytes, str]:
        """(payload, Stripe-Signature header) for one event."""
        eid = event_id or self._id("evt")
        body = json.dumps({"id": eid, "object": "event", "type": kind, "created": created or int(time.time()), "data": {"object": obj},
                           "livemode": False}).encode()
        return body, sign(WEBHOOK_SECRET, body)
