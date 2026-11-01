"""Plans, entitlements and their server-side enforcement; Stripe billing through the real client against a mock
Stripe, with the webhook properties that matter: signature, deduplication, replay, out-of-order delivery, failed
payments, reconciliation and dead-letter handling."""

from __future__ import annotations

import json
import threading
import time

from desk_engine.platform import plans
from desk_engine.platform.stripe import sign

from .helpers import Harness
from .owner_base import ORIGIN, OwnerBase
from .stripe_mock import WEBHOOK_SECRET


class Limits(OwnerBase):
    def test_free_plan_allows_one_hosted_workspace_and_pro_two(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        self.assertEqual(alice.get("/api/session").json()["entitlements"]["plan_code"], "free")
        self.assertEqual(alice.post("/api/workspaces", json={"name": "One"}, headers=ORIGIN).status_code, 200)
        r = alice.post("/api/workspaces", json={"name": "Two"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["error"], "workspace_limit")
        self.assertEqual(r.json()["details"], {"limit": 1, "owned": 1, "plan": "free"})
        # the owner puts the account on Pro (an enterprise-style assignment, no Stripe involved)
        r = self.owner.post(f"/api/owner/users/{alice.user_id}/subscription", json={"plan_id": "pro", "note": "pilot"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(alice.get("/api/session").json()["entitlements"]["plan_code"], "pro")
        self.assertEqual(alice.post("/api/workspaces", json={"name": "Two"}, headers=ORIGIN).status_code, 200)
        self.assertEqual(alice.post("/api/workspaces", json={"name": "Three"}, headers=ORIGIN).json()["details"]["limit"], 2)
        # being invited to other people's workspaces never counts against the limit
        bob = self.invite_user("bob@acme.test", "Bob")
        ws_b = bob.post("/api/workspaces", json={"name": "Bob Ltd"}, headers=ORIGIN).json()["id"]
        bob.post(f"/api/w/{ws_b}/invites", json={"email": "alice@acme.test", "role": "editor"}, headers=ORIGIN)
        self.assertEqual(len(alice.get("/api/session").json()["workspaces"]), 3)
        self.assertEqual(alice.post("/api/workspaces", json={"name": "Three"}, headers=ORIGIN).status_code, 403)

    def test_limit_holds_under_concurrent_requests(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        results: list[int] = []

        def create(i: int) -> None:
            c = self.client()
            c.cookies = alice.cookies
            results.append(c.post("/api/workspaces", json={"name": f"Race {i}"}, headers=ORIGIN).status_code)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200, 403, 403, 403, 403, 403])
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM members WHERE user_id = ? AND role = 'owner'", (alice.user_id,)), 1)

    def test_downgrade_makes_extra_workspaces_read_only_never_deletes(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        self.owner.post(f"/api/owner/users/{alice.user_id}/subscription", json={"plan_id": "pro"}, headers=ORIGIN)
        one = alice.post("/api/workspaces", json={"name": "One"}, headers=ORIGIN).json()["id"]
        two = alice.post("/api/workspaces", json={"name": "Two"}, headers=ORIGIN).json()["id"]
        alice.post(f"/api/w/{two}/threads", json={"title": "Budget"}, headers=ORIGIN)
        sub = self.engine.db.one("SELECT id FROM subscriptions WHERE user_id = ? AND status = 'active'", (alice.user_id,))
        r = self.owner.post(f"/api/owner/subscriptions/{sub['id']}/cancel?now=1", headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(alice.get("/api/session").json()["entitlements"]["plan_code"], "free")
        self.assertEqual(len(alice.get("/api/session").json()["workspaces"]), 2)             # both still there
        self.assertEqual(alice.get(f"/api/w/{two}/threads").status_code, 200)                   # readable
        r = alice.post(f"/api/w/{two}/threads", json={"title": "x"}, headers=ORIGIN)             # not writable
        self.assertEqual(r.json()["error"], "workspace_read_only")
        self.assertEqual(r.json()["details"]["reason"], "over_plan_limit")
        self.assertEqual(alice.post(f"/api/w/{one}/threads", json={"title": "x"}, headers=ORIGIN).status_code, 200)   # the oldest stays writable
        # upgrading again restores it
        self.owner.post(f"/api/owner/users/{alice.user_id}/subscription", json={"plan_id": "pro"}, headers=ORIGIN)
        self.assertEqual(alice.post(f"/api/w/{two}/threads", json={"title": "y"}, headers=ORIGIN).status_code, 200)

    def test_storage_quota_member_limit_and_feature_entitlements(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        ws = alice.post("/api/workspaces", json={"name": "One"}, headers=ORIGIN).json()["id"]
        # a tiny quota through an override: the second upload does not fit
        r = self.owner.put(f"/api/owner/users/{alice.user_id}/entitlements", json={"overrides": {"storage_quota_mb": 1}, "note": "test"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["storage_quota_mb"], 1)
        big = b"x" * (700 * 1024)
        r = alice.post(f"/api/w/{ws}/documents", files=[("files", ("a.txt", big))], headers=ORIGIN)
        self.assertEqual(len(r.json()["added"]), 1, r.text)
        r = alice.post(f"/api/w/{ws}/documents", files=[("files", ("b.txt", big + b"y"))], headers=ORIGIN)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["error"], "storage_quota")
        self.assertTrue(self.owner.get(f"/api/owner/workspaces/{ws}").json()["storage"]["total"] >= 700 * 1024)
        self.h.ws = ws
        self.h.settle()
        # member limit (free: 5 incl. pending invitations)
        for i in range(4):
            self.assertEqual(alice.post(f"/api/w/{ws}/invites", json={"email": f"m{i}@acme.test"}, headers=ORIGIN).status_code, 200)
        r = alice.post(f"/api/w/{ws}/invites", json={"email": "m9@acme.test"}, headers=ORIGIN)
        self.assertEqual(r.json()["error"], "member_limit")
        # features not in the Free plan are refused on the server, whatever the page shows
        r = alice.post(f"/api/w/{ws}/mcp-keys", json={"name": "k"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(r.json()["error"], "feature_not_in_plan")
        self.owner.put(f"/api/owner/users/{alice.user_id}/entitlements", json={"overrides": {"features": {"api_access": True}}}, headers=ORIGIN)
        self.assertEqual(alice.post(f"/api/w/{ws}/mcp-keys", json={"name": "k"}, headers=ORIGIN).status_code, 200)
        # a global feature flag switches a feature off for everyone, and can never touch a security control
        self.owner.put("/api/owner/settings", json={"values": {"features.flags": {"api_access": False}}}, headers=ORIGIN)
        r = alice.post(f"/api/w/{ws}/mcp-keys", json={"name": "k2"}, headers=ORIGIN)
        self.assertEqual(r.json()["error"], "feature_disabled")
        self.assertFalse(alice.get("/api/session").json()["platform"]["features.flags"]["api_access"])

    def test_self_hosted_edition_has_no_limits(self) -> None:
        h = Harness("server", script=[])
        try:
            c = h.client
            c.post("/api/auth/signup", json={"email": "ops@selfhost.test", "name": "Ops", "password": "Correct-Horse-Battery-9"})
            for i in range(3):
                self.assertEqual(c.post("/api/workspaces", json={"name": f"W{i}"}, headers=ORIGIN).status_code, 200)
            s = c.get("/api/session").json()
            self.assertFalse(s["hosted"])
            self.assertEqual(s["entitlements"]["plan_code"], "self_hosted")
            self.assertEqual(s["entitlements"]["max_workspaces"], 0)
            # the owner area exists for the operator, billing is simply off
            self.assertEqual(c.get("/api/owner/overview").status_code, 200)
            self.assertFalse(c.get("/api/plans").json()["billing"])
        finally:
            h.close()

    def test_plan_builder_validation_and_visibility(self) -> None:
        r = self.owner.post("/api/owner/plans", json={"code": "Team Plan", "name": "Team"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 422)
        r = self.owner.post("/api/owner/plans", json={"code": "team", "name": "Team", "monthly_price": 4900, "max_workspaces": 5,
                                                      "features": {"api_access": True, "bogus": True}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 422)
        r = self.owner.post("/api/owner/plans", json={"code": "team", "name": "Team", "monthly_price": 4900, "annual_price": 49000,
                                                      "max_workspaces": 5, "storage_quota_mb": 51200, "features": {"api_access": True}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["id"]
        self.assertEqual(r.json()["status"], "draft")
        public = self.client().get("/api/plans").json()
        self.assertEqual([p["code"] for p in public["plans"]], ["free", "pro", "enterprise"])   # a draft is not for sale
        self.owner.patch(f"/api/owner/plans/{pid}", json={"status": "active"}, headers=ORIGIN)
        self.assertIn("team", [p["code"] for p in self.client().get("/api/plans").json()["plans"]])
        self.owner.patch(f"/api/owner/plans/{pid}", json={"visibility": "hidden"}, headers=ORIGIN)
        self.assertNotIn("team", [p["code"] for p in self.client().get("/api/plans").json()["plans"]])
        self.assertEqual(self.owner.post("/api/owner/plans", json={"code": "team", "name": "Dup"}, headers=ORIGIN).status_code, 409)
        # Enterprise capabilities carry their real state; nothing unbuilt is "available"
        self.assertEqual(next(c["status"] for c in public["enterprise"] if c["key"] == "sso"), "planned")
        self.assertTrue(self.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'plan.create' AND target = ?", (pid,)))


class Stripe(OwnerBase):
    def setUp(self) -> None:
        super().setUp()
        self.configure_stripe()
        r = self.owner.post(f"/api/owner/plans/{self.plan_id('pro')}/sync-stripe", headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.pro = r.json()
        self.assertTrue(self.pro["stripe_price_monthly"].startswith("price_"))
        self.assertTrue(self.pro["stripe_price_annual"].startswith("price_"))
        self.alice = self.invite_user("alice@acme.test", "Alice")

    def pay(self, interval: str = "month", coupon: str | None = None, trial: bool = False) -> dict:
        """Checkout through the API, "payment" on the mock, the webhooks Stripe would send."""
        body = {"plan_id": "pro", "interval": interval}
        if coupon:
            body["coupon"] = coupon
        r = self.alice.post("/api/billing/checkout", json=body, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["url"].startswith("https://checkout.stripe.test/"))
        session_id = r.json()["id"]
        sub = self.stripe.complete_checkout(session_id, trial_days=14 if trial else 0)
        cs = self.stripe.checkouts[session_id]
        self.assertEqual(self.webhook("checkout.session.completed", {**cs, "subscription": sub["id"], "total_details": {"amount_discount": 290 if coupon else 0}}).status_code, 200)
        self.assertEqual(self.webhook("customer.subscription.created", sub).status_code, 200)
        inv = self.stripe.invoice_for(sub, paid=True)
        self.assertEqual(self.webhook("invoice.paid", inv).status_code, 200)
        return sub

    def test_checkout_provisions_pro_from_the_webhook_not_the_browser(self) -> None:
        self.assertEqual(self.alice.post("/api/workspaces", json={"name": "One"}, headers=ORIGIN).status_code, 200)
        self.assertEqual(self.alice.post("/api/workspaces", json={"name": "Two"}, headers=ORIGIN).status_code, 403)
        r = self.alice.post("/api/billing/checkout", json={"plan_id": "pro", "interval": "month"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        # starting a checkout changes nothing: Stripe has not said the customer paid
        self.assertEqual(self.alice.get("/api/me/plan").json()["plan"]["code"], "free")
        self.assertEqual(self.alice.post("/api/workspaces", json={"name": "Two"}, headers=ORIGIN).status_code, 403)
        sub = self.pay()
        me = self.alice.get("/api/me/plan").json()
        self.assertEqual(me["plan"]["code"], "pro")
        self.assertEqual(me["subscription"]["status"], "active")
        self.assertEqual(me["subscription"]["stripe_subscription_id"], sub["id"])
        self.assertEqual(len(me["invoices"]), 1)
        self.assertEqual(me["invoices"][0]["amount_paid"], 2900)
        self.assertEqual(self.alice.post("/api/workspaces", json={"name": "Two"}, headers=ORIGIN).status_code, 200)
        # the owner sees revenue and the ledger of events
        subs = self.owner.get("/api/owner/subscriptions").json()
        self.assertEqual(subs["mrr"]["by_currency"], {"USD": 2900})
        self.assertEqual(subs["items"][0]["email"], "alice@acme.test")
        k = self.owner.get("/api/owner/analytics/kpis?period=7d").json()
        self.assertEqual(k["active_subscriptions"], 1)
        self.assertEqual(k["paid_workspaces"], 2)
        self.assertEqual(k["mrr"], {"USD": 2900})
        self.assertEqual(k["arr"], {"USD": 34800})
        # a second checkout is refused while subscribed; the portal link is Stripe's
        self.assertEqual(self.alice.post("/api/billing/checkout", json={"plan_id": "pro"}, headers=ORIGIN).status_code, 409)
        self.assertTrue(self.alice.post("/api/billing/portal", headers=ORIGIN).json()["url"].startswith("https://billing.stripe.test/"))
        # the customer e-mails went through the queue
        self.deliver_mail()
        subjects = [m["Subject"] for m in self.mails()]
        self.assertTrue(any("Pro" in s for s in subjects), subjects)
        self.assertTrue(any("Payment received" in s for s in subjects), subjects)

    def test_webhook_signature_duplicates_replay_and_order(self) -> None:
        sub = self.pay()
        # bad signature, missing signature, and a stale timestamp (replay) are refused and audited
        body, _ = self.stripe.event("customer.subscription.updated", sub)
        self.assertEqual(self.client().post("/api/billing/webhook", content=body, headers={"stripe-signature": "t=1,v1=00"}).status_code, 403)
        self.assertEqual(self.client().post("/api/billing/webhook", content=body).status_code, 403)
        old = sign(WEBHOOK_SECRET, body, timestamp=int(time.time()) - 3600)
        self.assertEqual(self.client().post("/api/billing/webhook", content=body, headers={"stripe-signature": old}).status_code, 403)
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'billing.webhook.rejected'"), 3)
        # the same event delivered twice is processed once
        cancelled = {**sub, "status": "canceled", "canceled_at": int(time.time())}
        body, sig = self.stripe.event("customer.subscription.deleted", cancelled, event_id="evt_dup_1")
        r1 = self.client().post("/api/billing/webhook", content=body, headers={"stripe-signature": sig}).json()
        r2 = self.client().post("/api/billing/webhook", content=body, headers={"stripe-signature": sig}).json()
        self.assertEqual((r1["duplicate"], r2["duplicate"]), (False, True))
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM stripe_events WHERE id = 'evt_dup_1'"), 1)
        self.assertEqual(self.alice.get("/api/me/plan").json()["plan"]["code"], "free")
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'subscription.synced'"), 2)
        # an *older* event arriving late does not resurrect the subscription
        stale_active = {**sub, "status": "active"}
        r = self.webhook("customer.subscription.updated", stale_active, created=int(time.time()) - 600)
        self.assertEqual(r.json()["status"], "ignored")
        self.assertEqual(self.alice.get("/api/me/plan").json()["plan"]["code"], "free")
        # an unknown event type is acknowledged and ignored; a malformed body is refused
        self.assertEqual(self.webhook("product.created", {"id": "prod_x"}).json()["status"], "ignored")
        junk = b"not json"
        self.assertEqual(self.client().post("/api/billing/webhook", content=junk, headers={"stripe-signature": sign(WEBHOOK_SECRET, junk)}).status_code, 422)

    def test_failed_payment_grace_recovery_and_reconciliation(self) -> None:
        sub = self.pay()
        self.alice.post("/api/workspaces", json={"name": "One"}, headers=ORIGIN)
        self.alice.post("/api/workspaces", json={"name": "Two"}, headers=ORIGIN)
        failed = self.stripe.invoice_for(sub, paid=False)
        self.assertEqual(self.webhook("invoice.payment_failed", failed).status_code, 200)
        me = self.alice.get("/api/me/plan").json()
        self.assertEqual(me["subscription"]["status"], "past_due")
        self.assertEqual(me["plan"]["code"], "pro")                     # the grace period keeps the plan
        self.assertTrue(me["subscription"]["grace_until"] > time.time())
        self.assertEqual(self.owner.get("/api/owner/analytics/kpis?period=7d").json()["failed_payments"]["value"], 1)
        self.deliver_mail()
        self.assertTrue(any("Payment failed" in m["Subject"] for m in self.mails()))
        self.assertTrue(self.engine.db.one("SELECT 1 FROM user_notifications WHERE user_id = ? AND category = 'billing'", (self.owner_id,)))
        # grace runs out: Free again, the extra workspace read-only, nothing deleted
        self.engine.db.execute("UPDATE subscriptions SET grace_until = ? WHERE stripe_subscription_id = ?", (time.time() - 1, sub["id"]))
        plans.tick(self.engine)
        self.assertEqual(self.alice.get("/api/me/plan").json()["plan"]["code"], "free")
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM workspace_state WHERE status = 'read_only'"), 1)
        # the customer pays: the paid invoice restores the plan
        paid = self.stripe.invoice_for(sub, paid=True)
        self.assertEqual(self.webhook("invoice.paid", paid).status_code, 200)
        self.assertEqual(self.alice.get("/api/me/plan").json()["subscription"]["status"], "active")
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM workspace_state WHERE status = 'read_only'"), 0)
        # reconciliation pulls the truth from Stripe whatever the webhooks said
        self.stripe.subscriptions[sub["id"]]["cancel_at_period_end"] = True
        local = self.engine.db.one("SELECT id FROM subscriptions WHERE stripe_subscription_id = ?", (sub["id"],))
        r = self.owner.post(f"/api/owner/subscriptions/{local['id']}/reconcile", headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["cancel_at_period_end"], 1)
        # the customer cancels (through our API → Stripe → local), at period end
        r = self.alice.post("/api/billing/cancel", headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.stripe.subscriptions[sub["id"]]["cancel_at_period_end"], True)

    def test_unmappable_event_fails_safely_and_can_be_retried(self) -> None:
        orphan = {"id": "sub_orphan", "object": "subscription", "customer": "cus_unknown", "status": "active", "metadata": {},
                  "items": {"data": [{"id": "si_x", "price": {"id": "price_unknown", "recurring": {"interval": "month"}}}]},
                  "current_period_start": 1, "current_period_end": 2}
        r = self.webhook("customer.subscription.created", orphan)
        self.assertEqual(r.status_code, 200)                 # acknowledged so Stripe stops retrying; parked here
        self.assertFalse(r.json()["processed"])
        row = self.engine.db.one("SELECT status, attempts, error FROM stripe_events ORDER BY received_at DESC LIMIT 1")
        self.assertEqual(row["status"], "failed")
        self.assertIn("cannot map", row["error"])
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM subscriptions"), 0)
        # retries by the scheduler, then dead-letter with an owner alert
        from desk_engine.platform import billing
        for _ in range(6):
            billing.retry_failed(self.engine)
        row = self.engine.db.one("SELECT status FROM stripe_events ORDER BY received_at DESC LIMIT 1")
        self.assertEqual(row["status"], "dead_letter")
        self.assertTrue(self.engine.db.one("SELECT 1 FROM user_notifications WHERE title LIKE 'A Stripe event could not be processed%'"))
        self.assertEqual(self.owner.get("/api/owner/billing").json()["events_failed"], 1)

    def test_trial_annual_interval_and_mode_mismatch(self) -> None:
        sub = self.pay(interval="year", trial=True)
        me = self.alice.get("/api/me/plan").json()
        self.assertEqual(me["subscription"]["status"], "trialing")
        self.assertEqual(me["subscription"]["interval"], "year")
        self.assertEqual(self.owner.get("/api/owner/analytics/kpis").json()["trial_subscriptions"], 1)
        self.assertEqual(self.owner.get("/api/owner/subscriptions").json()["mrr"]["by_currency"], {})   # a trial is not revenue
        self.assertEqual(self.webhook("customer.subscription.trial_will_end", sub).status_code, 200)
        self.assertTrue(self.engine.db.one("SELECT 1 FROM email_jobs WHERE template_key = 'trial_expiration'"))
        # a live-mode setting with a test key is refused before any call
        self.owner.put("/api/owner/billing", json={"mode": "live"}, headers=ORIGIN)
        r = self.alice.post("/api/billing/portal", headers=ORIGIN)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"], "stripe_mode_mismatch")
        self.owner.put("/api/owner/billing", json={"mode": "test"}, headers=ORIGIN)
        self.assertTrue(self.owner.post("/api/owner/billing/test", headers=ORIGIN).json()["ok"])

    def test_billing_off_or_self_hosted_refuses_checkout(self) -> None:
        self.owner.put("/api/owner/billing", json={"enabled": False}, headers=ORIGIN)
        r = self.alice.post("/api/billing/checkout", json={"plan_id": "pro"}, headers=ORIGIN)
        self.assertEqual(r.json()["error"], "billing_off")
        self.assertFalse(self.client().get("/api/plans").json()["billing"])
        # stored card data never exists here: the mock saw no card fields
        self.assertFalse(any("card" in json.dumps(c) for c in self.stripe.checkouts.values()))
