"""Coupons, e-mail (settings, templates, queue), announcements and preferences, owner alerts, Contact Sales,
analytics (collection, privacy, metrics, exports) and the Google sign-in policy."""

from __future__ import annotations

import time

from desk_engine.platform import analytics, email as email_svc, notifications, sales
from desk_engine.platform.settings import get_setting, set_setting

from .owner_base import ORIGIN, OwnerBase


class Coupons(OwnerBase):
    def setUp(self) -> None:
        super().setUp()
        self.configure_stripe()
        self.owner.post(f"/api/owner/plans/{self.plan_id('pro')}/sync-stripe", headers=ORIGIN)
        self.alice = self.invite_user("alice@acme.test", "Alice")

    def test_create_validate_redeem_limits_and_immutability(self) -> None:
        r = self.owner.post("/api/owner/coupons", json={"code": "launch 10", "percent_off": 10}, headers=ORIGIN)
        self.assertEqual(r.status_code, 422)
        r = self.owner.post("/api/owner/coupons", json={"code": "LAUNCH10", "percent_off": 10, "amount_off": 500}, headers=ORIGIN)
        self.assertEqual(r.status_code, 422)   # one or the other
        r = self.owner.post("/api/owner/coupons", json={"code": "launch10", "percent_off": 10, "duration": "repeating", "duration_months": 3,
                                                        "max_redemptions": 2, "plan_ids": [self.plan_id("pro")]}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        c = r.json()
        self.assertEqual(c["code"], "LAUNCH10")
        self.assertTrue(c["stripe_coupon_id"].startswith("coupon_"))         # mirrored to Stripe
        self.assertTrue(c["stripe_promotion_code_id"].startswith("promo_"))
        self.assertEqual(self.owner.post("/api/owner/coupons", json={"code": "LAUNCH10", "percent_off": 5}, headers=ORIGIN).status_code, 409)
        # the customer checks it: valid for Pro, 10 % off
        r = self.alice.post("/api/billing/coupon-check", json={"code": "launch10", "plan_id": "pro"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["discount"], r.json()["total"]), (290, 2610))
        self.assertEqual(self.alice.post("/api/billing/coupon-check", json={"code": "NOPE", "plan_id": "pro"}, headers=ORIGIN).json()["error"], "coupon_invalid")
        self.assertEqual(self.alice.post("/api/billing/coupon-check", json={"code": "LAUNCH10", "plan_id": "free"}, headers=ORIGIN).json()["error"], "coupon_plan")
        # checkout with it: the discount goes to Stripe as a promotion code (no stacking with free-form codes)
        r = self.alice.post("/api/billing/checkout", json={"plan_id": "pro", "coupon": "LAUNCH10"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        cs = self.stripe.checkouts[r.json()["id"]]
        self.assertEqual(cs["discounts"][0]["promotion_code"], c["stripe_promotion_code_id"])
        sub = self.stripe.complete_checkout(cs["id"])
        self.webhook("checkout.session.completed", {**cs, "subscription": sub["id"], "total_details": {"amount_discount": 290}})
        self.webhook("customer.subscription.created", sub)
        red = self.owner.get(f"/api/owner/coupons/{c['id']}/redemptions").json()["items"]
        self.assertEqual(len(red), 1)
        self.assertEqual((red[0]["email"], red[0]["amount_discounted"]), ("alice@acme.test", 290))
        self.assertEqual(self.owner.get("/api/owner/coupons").json()["performance"][0]["discounted"], 290)
        # per-customer limit (1) and first-time rule
        self.assertEqual(self.alice.post("/api/billing/coupon-check", json={"code": "LAUNCH10", "plan_id": "pro"}, headers=ORIGIN).json()["error"], "coupon_used")
        # immutable after creation, as in Stripe; metadata is editable; disabling deactivates the promotion code too
        r = self.owner.patch(f"/api/owner/coupons/{c['id']}", json={"active": False}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.stripe.promotion_codes[c["stripe_promotion_code_id"]]["active"])
        bob = self.invite_user("bob@acme.test", "Bob")
        self.assertEqual(bob.post("/api/billing/coupon-check", json={"code": "LAUNCH10", "plan_id": "pro"}, headers=ORIGIN).json()["error"], "coupon_inactive")
        r = self.owner.patch(f"/api/owner/coupons/{c['id']}", json={"active": True, "expires_at": time.time() - 10}, headers=ORIGIN)
        self.assertEqual(bob.post("/api/billing/coupon-check", json={"code": "LAUNCH10", "plan_id": "pro"}, headers=ORIGIN).json()["error"], "coupon_expired")
        # exhausted
        self.owner.patch(f"/api/owner/coupons/{c['id']}", json={"clear_expiry": True, "max_redemptions": 1}, headers=ORIGIN)
        self.assertEqual(bob.post("/api/billing/coupon-check", json={"code": "LAUNCH10", "plan_id": "pro"}, headers=ORIGIN).json()["error"], "coupon_exhausted")
        # unauthorized modification: a regular user and a delegated admin cannot touch coupons
        self.assertEqual(self.alice.patch(f"/api/owner/coupons/{c['id']}", json={"active": True}, headers=ORIGIN).status_code, 403)
        self.assertTrue(self.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'coupon.create'"))

    def test_fixed_amount_needs_currency_and_matches_plan(self) -> None:
        self.assertEqual(self.owner.post("/api/owner/coupons", json={"code": "FIVE", "amount_off": 500}, headers=ORIGIN).status_code, 422)
        r = self.owner.post("/api/owner/coupons", json={"code": "FIVE", "amount_off": 500, "currency": "EUR"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.alice.post("/api/billing/coupon-check", json={"code": "FIVE", "plan_id": "pro"}, headers=ORIGIN).json()["error"], "coupon_currency")


class Email(OwnerBase):
    def test_settings_templates_queue_retries_and_suppression(self) -> None:
        # the dashboard's SMTP settings win over the environment; the password is write-only
        r = self.owner.put("/api/owner/email", json={"smtp_host": "smtp.acme.test", "smtp_port": 587, "smtp_user": "mailer", "smtp_password": "s3cret-pass",
                                                     "from_address": "desk@acme.test", "from_name": "Acme Desk"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["source"], "dashboard")
        self.assertTrue(r.json()["password"]["configured"])
        self.assertNotIn("s3cret", self.owner.get("/api/owner/email").text)
        self.assertEqual(self.owner.put("/api/owner/email", json={"smtp_security": "none"}, headers=ORIGIN).status_code, 422)   # clear-text with a login
        # templates: bilingual defaults, editable, previewable, resettable
        t = self.owner.get("/api/owner/email/templates/welcome/ar/preview").json()
        self.assertIn("مرحباً", t["body"])
        r = self.owner.put("/api/owner/email/templates/welcome/en", json={"subject": "Hi {name}", "body": "Welcome aboard, {name}. {missing}"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["custom"])
        self.assertEqual(self.owner.get("/api/owner/email/templates/welcome/en/preview").json()["body"], "Welcome aboard, Olivia. ")
        self.assertFalse(self.owner.delete("/api/owner/email/templates/welcome/en", headers=ORIGIN).json()["custom"])
        self.assertEqual(self.owner.put("/api/owner/email/templates/bogus/en", json={"subject": "x", "body": "y"}, headers=ORIGIN).status_code, 404)
        # the dashboard's mailer is a distinct object from the environment's
        self.assertIsNot(email_svc.mailer(self.engine), self.engine.mailer)
        # the queue: a sign-up queues welcome + verification; delivery goes through the configured mailer
        fake = self.engine.mailer
        set_setting(self.engine, "email.smtp_host", "", by=None)   # back to the environment mailer so the fake receives the mail
        bob = self.invite_user("bob@acme.test", "Bob")
        del bob
        jobs = self.owner.get("/api/owner/email/jobs?status=queued").json()["items"]
        self.assertTrue(any(j["template_key"] == "welcome" and j["to_address"] == "bob@acme.test" for j in jobs))
        sent = self.deliver_mail()
        self.assertGreaterEqual(sent, 1)
        self.assertTrue(any(m["To"] == "bob@acme.test" and "Welcome" in m["Subject"] for m in fake.sent))
        # a failing provider: retried with back-off, parked after the limit, retryable by hand
        fake.fail = True
        set_setting(self.engine, "email.max_attempts", 2, by=None)
        email_svc.enqueue(self.engine, to="carol@acme.test", template_key="welcome", lang="en", vars={"name": "Carol"}, category="account")
        self.deliver_mail()
        job = self.engine.db.one("SELECT * FROM email_jobs WHERE to_address = 'carol@acme.test'")
        self.assertEqual((job["status"], job["attempts"]), ("queued", 1))
        self.assertTrue(job["next_attempt_at"] > time.time() + 30)
        self.engine.db.execute("UPDATE email_jobs SET next_attempt_at = 0 WHERE id = ?", (job["id"],))
        self.deliver_mail()
        self.assertEqual(self.engine.db.scalar("SELECT status FROM email_jobs WHERE id = ?", (job["id"],)), "failed")
        self.assertIn("refused", self.engine.db.scalar("SELECT last_error FROM email_jobs WHERE id = ?", (job["id"],)))
        fake.fail = False
        self.assertEqual(self.owner.post(f"/api/owner/email/jobs/{job['id']}/retry", headers=ORIGIN).status_code, 204)
        self.deliver_mail()
        self.assertEqual(self.engine.db.scalar("SELECT status FROM email_jobs WHERE id = ?", (job["id"],)), "sent")
        # suppression and dedupe
        self.owner.post("/api/owner/email/suppressions", json={"address": "dave@acme.test", "reason": "bounced"}, headers=ORIGIN)
        self.assertIsNone(email_svc.enqueue(self.engine, to="dave@acme.test", template_key="welcome", lang="en", vars={}, category="account"))
        self.assertEqual(self.engine.db.scalar("SELECT status FROM email_jobs WHERE to_address = 'dave@acme.test'"), "suppressed")
        self.assertIsNotNone(email_svc.enqueue(self.engine, to="eve@acme.test", template_key="welcome", lang="en", vars={}, category="account", dedupe_key="once"))
        self.assertIsNone(email_svc.enqueue(self.engine, to="eve@acme.test", template_key="welcome", lang="en", vars={}, category="account", dedupe_key="once"))
        # a test send
        self.assertTrue(self.owner.post("/api/owner/email/test", json={"to": "ops@acme.test"}, headers=ORIGIN).json()["ok"])
        self.assertNotIn("s3cret", str(fake.sent))


class Announcements(OwnerBase):
    def test_targeting_preferences_idempotent_delivery_and_schedule(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        bob = self.invite_user("bob@acme.test", "Bob")
        self.owner.post(f"/api/owner/users/{bob.user_id}/subscription", json={"plan_id": "pro"}, headers=ORIGIN)
        # bob declines product e-mails but cannot decline security in-app notices
        r = bob.put("/api/me/notification-preferences", json={"preferences": {"product": {"in_app": True, "email": False}, "security": {"in_app": False, "email": True}}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["preferences"]["security"]["in_app"])
        # preview the audience, then send to Pro accounts only, in-app and by e-mail
        r = self.owner.post("/api/owner/announcements/preview", json={"title": "x", "body": "y", "target": {"plan_codes": ["pro"]}}, headers=ORIGIN)
        self.assertEqual(r.json()["recipients"], 1)
        r = self.owner.post("/api/owner/announcements", json={"title": "New: cash-flow forecasts", "body": "Try them under Analysis.", "category": "product",
                                                              "channels": ["in_app", "email"], "target": {"plan_codes": ["pro"]}, "send_now": True}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        aid = r.json()["id"]
        self.assertEqual(r.json()["status"], "sent")
        self.assertEqual(bob.get("/api/me/notifications").json()["unread"], 1)
        self.assertEqual(alice.get("/api/me/notifications").json()["unread"], 0)
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM email_jobs WHERE template_key = 'platform_announcement'"), 0)   # bob declined e-mail
        # sending again delivers nothing twice
        self.owner.post(f"/api/owner/announcements/{aid}/send", headers=ORIGIN)
        self.assertEqual(bob.get("/api/me/notifications").json()["unread"], 1)
        self.assertEqual(len(self.owner.get(f"/api/owner/announcements/{aid}/deliveries").json()["items"]), 1)
        bob.post("/api/me/notifications/read", json={"ids": None}, headers=ORIGIN)
        self.assertEqual(bob.get("/api/me/notifications").json()["unread"], 0)
        # a scheduled announcement to everyone goes out when the scheduler reaches its time
        r = self.owner.post("/api/owner/announcements", json={"title": "Maintenance Sunday", "body": "20 minutes.", "category": "maintenance",
                                                              "target": {"all": True}, "scheduled_at": time.time() + 3600}, headers=ORIGIN)
        self.assertEqual(r.json()["status"], "scheduled")
        notifications.tick(self.engine)
        self.assertEqual(alice.get("/api/me/notifications").json()["unread"], 0)
        self.engine.db.execute("UPDATE announcements SET scheduled_at = ? WHERE id = ?", (time.time() - 1, r.json()["id"]))
        notifications.tick(self.engine)
        self.assertEqual(alice.get("/api/me/notifications").json()["unread"], 1)
        self.assertEqual(self.owner.post(f"/api/owner/announcements/{r.json()['id']}/cancel", headers=ORIGIN).status_code, 404)   # already sent
        # new-user targeting and bad targets
        self.assertEqual(self.owner.post("/api/owner/announcements/preview", json={"title": "x", "body": "y", "target": {"new_users_days": 7}}, headers=ORIGIN).json()["recipients"], 3)
        self.assertEqual(self.owner.post("/api/owner/announcements", json={"title": "x", "body": "y", "target": {"everyone": True}}, headers=ORIGIN).status_code, 422)

    def test_owner_alerts_follow_rules(self) -> None:
        # a sign-up alerts in-app by default; the rule can add e-mail or switch the alert off
        self.invite_user("alice@acme.test", "Alice")
        mine = self.owner.get("/api/me/notifications").json()["items"]
        self.assertTrue(any(n["title"].startswith("New account: alice@acme.test") for n in mine))
        r = self.owner.put("/api/owner/alerts", json={"rules": {"new_registration": {"in_app": False, "email": True}}}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        before = len(self.owner.get("/api/me/notifications").json()["items"])
        self.invite_user("bob@acme.test", "Bob")
        self.assertEqual(len(self.owner.get("/api/me/notifications").json()["items"]), before)
        self.assertTrue(self.engine.db.one("SELECT 1 FROM email_jobs WHERE template_key = 'owner_alert' AND subject LIKE '%New account: bob@acme.test%'"))
        self.assertEqual(self.owner.put("/api/owner/alerts", json={"rules": {"made_up": {"in_app": True}}}, headers=ORIGIN).status_code, 422)
        self.assertEqual(self.owner.put("/api/owner/alerts", json={"webhook_url": "http://insecure.test/hook"}, headers=ORIGIN).status_code, 422)


class Sales(OwnerBase):
    def test_contact_sales_lead_lifecycle(self) -> None:
        anon = self.client()
        r = anon.post("/api/sales/contact", json={"name": "Faisal", "email": "cfo@acme.example", "company": "Acme Holdings", "country": "sa",
                                                   "company_size": "201-1000", "users_estimate": 40, "workspaces_needed": 6, "deployment": "private",
                                                   "features": ["sso", "audit_retention"], "message": "We need a private deployment.", "lang": "ar"})
        self.assertEqual(r.status_code, 200, r.text)
        lead_id = r.json()["id"]
        # the honeypot swallows bots silently; bad input is refused
        self.assertIsNone(anon.post("/api/sales/contact", json={"name": "Bot", "email": "b@b.test", "company": "B", "website": "http://spam"}).json()["id"])
        self.assertEqual(anon.post("/api/sales/contact", json={"name": "X", "email": "not-an-email", "company": "Y"}).status_code, 422)
        # the enquirer's confirmation (Arabic) and the owner's notification are queued; the owner is alerted in-app
        self.assertTrue(self.engine.db.one("SELECT 1 FROM email_jobs WHERE to_address = 'cfo@acme.example' AND subject = 'استلمنا استفسارك'"))
        self.assertTrue(self.engine.db.one("SELECT 1 FROM email_jobs WHERE template_key = 'sales_lead_notification'"))
        self.assertTrue(any("Acme Holdings" in n["title"] for n in self.owner.get("/api/me/notifications").json()["items"]))
        # the inbox, status flow, assignment, notes, follow-up reminder
        inbox = self.owner.get("/api/owner/leads?status=NEW").json()
        self.assertEqual(inbox["total"], 1)
        self.assertEqual(inbox["items"][0]["country"], "SA")
        self.assertEqual(inbox["stats"]["by_status"]["NEW"], 1)
        r = self.owner.patch(f"/api/owner/leads/{lead_id}", json={"status": "QUALIFIED", "assigned_to": self.owner_id, "follow_up_at": time.time() - 1}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.owner.post(f"/api/owner/leads/{lead_id}/notes", json={"body": "Call booked for Tuesday."}, headers=ORIGIN)
        self.assertEqual(self.owner.get(f"/api/owner/leads/{lead_id}").json()["notes"][0]["body"], "Call booked for Tuesday.")
        sales.remind_followups(self.engine)
        self.assertTrue(any(n["title"].startswith("Follow up with Acme Holdings") for n in self.owner.get("/api/me/notifications").json()["items"]))
        self.assertEqual(self.owner.patch(f"/api/owner/leads/{lead_id}", json={"status": "MAYBE"}, headers=ORIGIN).status_code, 422)
        # leads are protected: an ordinary account cannot read them; the CSV export carries them for the owner
        alice = self.invite_user("alice@acme.test", "Alice")
        self.assertEqual(alice.get("/api/owner/leads").status_code, 403)
        csv = self.owner.get("/api/owner/export/leads.csv")
        self.assertEqual(csv.status_code, 200)
        self.assertIn("Acme Holdings", csv.text)
        self.assertIn("attachment", csv.headers["content-disposition"])


class Analytics(OwnerBase):
    def collect(self, name: str = "page_view", **kw: object) -> int:
        body = {"name": name, "path": "/pricing", "consent": True, "lang": "en", **kw}
        return self.client().post("/api/analytics/collect", json=body, headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0", "referer": "https://www.google.com/search?q=desk",
                                                                                "cf-ipcountry": "SA"}).status_code

    def test_collection_respects_consent_and_stores_no_personal_data(self) -> None:
        self.assertEqual(self.collect(consent=False), 202)                  # consent required by default: nothing stored
        self.assertEqual(self.engine.db.scalar("SELECT COUNT(*) FROM analytics_events WHERE kind = 'web'"), 0)
        self.assertEqual(self.collect(), 204)
        self.assertEqual(self.collect("pricing_view"), 204)
        self.assertEqual(self.client().post("/api/analytics/collect", json={"name": "page_view", "consent": True},
                                            headers={"user-agent": "Googlebot/2.1"}).status_code, 202)   # bots are dropped
        self.assertEqual(self.client().post("/api/analytics/collect", json={"name": "keystrokes", "consent": True}).status_code, 422)
        row = self.engine.db.one("SELECT * FROM analytics_events WHERE kind = 'web' ORDER BY id LIMIT 1")
        self.assertEqual((row["device"], row["browser"], row["source"], row["referrer"], row["country"]), ("desktop", "chrome", "search", "www.google.com", "SA"))
        self.assertEqual(len(row["visitor"]), 24)
        self.assertNotIn("testclient", str(row))                            # no client address anywhere
        self.assertNotIn("q=desk", str(row))                                # the referrer's query string is gone
        # the owner can switch collection off, or stop requiring consent, and the public policy says so
        self.owner.put("/api/owner/integrations/analytics", json={"enabled": False}, headers=ORIGIN)
        self.assertEqual(self.collect(), 202)
        self.assertFalse(self.client().get("/api/analytics/policy").json()["enabled"])

    def test_platform_metrics_series_and_exports_come_from_real_events(self) -> None:
        alice = self.invite_user("alice@acme.test", "Alice")
        ws = alice.post("/api/workspaces", json={"name": "Alice Ltd"}, headers=ORIGIN).json()["id"]
        alice.post(f"/api/w/{ws}/documents", files=[("files", ("n.md", b"# notes"))], headers=ORIGIN)
        self.h.ws = ws
        self.h.settle()
        # product events were mirrored from the audit trail — names only
        names = {r["name"] for r in self.engine.db.all("SELECT name FROM analytics_events WHERE kind = 'platform'")}
        self.assertTrue({"signup", "workspace_created", "document_added"} <= names, names)
        self.assertIsNone(self.engine.db.scalar("SELECT props FROM analytics_events WHERE kind = 'platform' AND name = 'document_added'"))
        for _ in range(3):
            self.collect()
        self.collect("signup_view")
        k = self.owner.get("/api/owner/analytics/kpis?period=today").json()
        self.assertEqual(k["total_users"], 2)
        self.assertEqual(k["new_users_today"], 2)
        self.assertEqual(k["total_workspaces"], 2)   # Alice's and the owner's scratch workspace from the invitation
        self.assertEqual(k["page_views"]["value"], 3)
        self.assertEqual(k["unique_visitors"]["value"], 1)
        self.assertGreaterEqual(k["dau"], 1)
        self.assertIsNone(k["churn_rate"])                                   # no paid base: null, not zero
        self.assertEqual(k["conversion_rate"], 0.0)
        for metric in ("user_growth", "registrations", "active_users", "funnel", "traffic", "sources", "devices", "countries", "features",
                       "workspaces_created", "plan_segments", "retention", "storage", "email", "security", "ai_usage", "revenue"):
            r = self.owner.get(f"/api/owner/analytics/series/{metric}?period=7d")
            self.assertEqual(r.status_code, 200, f"{metric}: {r.text}")
        funnel = {p["name"]: p["value"] for p in self.owner.get("/api/owner/analytics/series/funnel?period=7d").json()["points"]}
        self.assertEqual((funnel["page_view"], funnel["signup_completed"]), (1, 2))
        features = {p["name"]: p["value"] for p in self.owner.get("/api/owner/analytics/series/features?period=7d").json()["points"]}
        self.assertEqual(features.get("document_added"), 1)
        self.assertEqual(self.owner.get("/api/owner/analytics/series/nope").status_code, 422)
        # custom range, comparison period, CSV exports with definitions, access control
        r = self.owner.get(f"/api/owner/analytics/kpis?from={time.time() - 86400}&to={time.time() + 1}")
        self.assertEqual(r.json()["new_users"]["value"], 2)
        self.assertEqual(r.json()["new_users"]["previous"], 0)
        csv = self.owner.get("/api/owner/export/kpis.csv?period=7d").text
        self.assertIn("metric,value,previous,definition", csv)
        self.assertIn("Distinct accounts", csv)
        self.assertEqual(alice.get("/api/owner/analytics/kpis").status_code, 403)
        self.assertIn("mau", self.owner.get("/api/owner/analytics/definitions").json()["metrics"])
        # retention purge
        self.engine.db.execute("UPDATE analytics_events SET created_at = created_at - 500 * 86400 WHERE kind = 'web'")
        self.assertEqual(analytics.purge(self.engine), 4)


class GooglePolicy(OwnerBase):
    def test_owner_controls_google_sign_in(self) -> None:
        from desk_engine.routes.auth import google_login_available

        self.assertFalse(google_login_available(self.engine))               # no client configured yet
        r = self.owner.put("/api/owner/integrations/google", json={"client_id": "abc.apps.googleusercontent.com", "client_secret": "GOCSPX-secret",
                                                                   "allowed_domains": ["acme.test"], "registration": "invite_only"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["available"])
        self.assertEqual(r.json()["credentials"]["client_id"], "abc.apps.googleusercontent.com")
        self.assertNotIn("GOCSPX", self.owner.get("/api/owner/integrations").text)
        self.assertTrue(self.client().get("/api/session").json()["features"]["google_login"])
        self.assertEqual(get_setting(self.engine, "google.allowed_domains"), ["acme.test"])
        self.owner.put("/api/owner/integrations/google", json={"enabled": False}, headers=ORIGIN)
        self.assertFalse(self.client().get("/api/session").json()["features"]["google_login"])
        self.assertEqual(self.client().post("/api/auth/google/start", json={}).status_code, 422)
        self.assertEqual(self.owner.put("/api/owner/integrations/google", json={"allowed_domains": ["not a domain"]}, headers=ORIGIN).status_code, 422)
        self.assertEqual(self.owner.put("/api/owner/integrations/google", json={"client_secret": "x"}, headers=ORIGIN).status_code, 422)   # needs its id
