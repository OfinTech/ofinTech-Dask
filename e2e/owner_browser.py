"""The Owner Control Center in a real browser against a real hosted Team Server.

    python e2e/owner_browser.py [out-dir]

Starts one engine process in hosted mode (`DESK_HOSTED=true`, `DESK_PLATFORM_OWNERS` set) with the built UI and
drives Chromium through Playwright: the owner signs up and reaches the dashboard, an ordinary account cannot; every
section renders; a plan is edited; Stripe settings are saved and the secret never comes back; a coupon is created;
an announcement is sent and received; e-mail settings and templates work; the Free plan's workspace limit shows in
the app; the layout holds at phone width; Arabic renders right-to-left. Each check records expected, actual, a
screenshot and the outcome; the summary is written to `<out-dir>/owner/evidence.json`.

Needs Python Playwright with Chromium and a built UI in ui/dist (CI builds it).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PW = "Correct-Horse-Battery-9"
OWNER = "owner@platform.test"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Engine:
    def __init__(self) -> None:
        self.port = free_port()
        self.tmp = tempfile.mkdtemp(prefix="desk-owner-")
        self.base = f"http://127.0.0.1:{self.port}"
        env = {**os.environ, "DESK_MODE": "server", "DESK_DATA_DIR": self.tmp, "DESK_PORT": str(self.port), "DESK_HOST": "127.0.0.1",
               "DESK_PUBLIC_URL": self.base, "DESK_UI_DIR": str(ROOT / "ui" / "dist"), "DESK_LOG_LEVEL": "warning",
               "DESK_MASTER_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(), "DESK_ALLOW_HTTP": "true",
               "DESK_ALLOW_PRIVATE_URLS": "1", "DESK_HOSTED": "true", "DESK_PLATFORM_OWNERS": OWNER, "DESK_OPEN_SIGNUP": "true"}
        env.pop("DESK_DEV", None)
        self.proc = subprocess.Popen([sys.executable, "-m", "desk_engine"], cwd=ROOT / "engine", env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            line = self.proc.stdout.readline() if self.proc.stdout else ""
            if "DESK_READY" in line:
                return
            if self.proc.poll() is not None:
                raise SystemExit("engine exited:\n" + line + (self.proc.stdout.read() if self.proc.stdout else ""))
        raise SystemExit("engine did not start")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class Evidence:
    def __init__(self, out: Path) -> None:
        self.out = out
        (out / "owner").mkdir(parents=True, exist_ok=True)
        self.rows: list[dict[str, Any]] = []

    def record(self, test_id: str, expected: str, actual: str, ok: bool, page: Page | None = None, shot: str | None = None) -> None:
        path = None
        if page is not None and shot:
            path = f"owner/{shot}.png"
            page.screenshot(path=str(self.out / path))
        self.rows.append({"id": test_id, "expected": expected, "actual": actual, "result": "PASS" if ok else "FAIL", "screenshot": path})
        print(f"{test_id}: {'PASS' if ok else 'FAIL'} — {actual}", flush=True)


def signup(page: Page, base: str, email: str, name: str) -> None:
    page.goto(base + "/#/")
    page.wait_for_selector("input[type=email]", timeout=30_000)       # the sign-in / first-account form
    if page.locator("button:has-text('Create an account')").count():   # not the first account: switch to sign-up
        page.click("button:has-text('Create an account')")
    page.wait_for_selector("input[autocomplete=name]", timeout=10_000)
    page.fill("input[autocomplete=name]", name)
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", PW)
    page.click("button:has-text('Create account')")
    page.wait_for_selector("text=Company or workspace name", timeout=30_000)   # the onboarding page: signed in, no workspace yet


def main(out_dir: str) -> int:
    out = Path(out_dir)
    ev = Evidence(out)
    engine = Engine()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-GB")
            page = ctx.new_page()
            # 1. the owner signs up (listed in DESK_PLATFORM_OWNERS) and the Owner Center link appears
            console: list[str] = []
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            signup(page, engine.base, OWNER, "Olivia Owner")
            page.goto(engine.base + "/#/owner")
            page.reload()
            try:
                page.wait_for_selector("aside.rail >> text=Platform owner", timeout=30_000)
            except Exception:
                page.screenshot(path=str(out / "owner" / "failure.png"))
                print("PAGE TEXT:", page.inner_text("body")[:1500], flush=True)
                print("SESSION:", page.evaluate("fetch('/api/session').then(r => r.text())")[:800], flush=True)
                print("CONSOLE:", "\n".join(console[-20:]), flush=True)
                raise
            page.wait_for_selector("text=Total registered users", timeout=30_000)
            ev.record("O-1", "owner reaches #/owner and sees the KPI overview", f"overview rendered, KPI tiles present: {page.locator('.kpi-card').count()}",
                      page.locator(".kpi-card").count() >= 10, page, "o1-overview")
            # 2. every section renders without an error box
            sections = ["users", "workspaces", "subscriptions", "plans", "billing", "coupons", "leads", "analytics", "notifications", "email",
                        "security", "integrations", "storage", "audit", "settings"]
            failed = []
            for s in sections:
                page.goto(engine.base + f"/#/owner/{s}")
                page.wait_for_timeout(600)
                page.wait_for_selector("h1", timeout=15_000)
                if page.locator(".notice.error").count() or page.locator("text=Something went wrong").count():
                    failed.append(s)
                page.screenshot(path=str(out / "owner" / f"section-{s}.png"))
            ev.record("O-2", "all 16 sections render without an error", f"errors in: {failed or 'none'}", not failed)
            # 3. the plan editor: open Pro, change the workspace limit, save; the API reflects it
            page.goto(engine.base + "/#/owner/plans")
            page.wait_for_selector("text=Plans & Features")
            page.locator(".kpi-card", has_text="Pro").first.click()
            page.wait_for_selector("text=Edit plan")
            limit = page.locator("label:has-text('Workspace limit') input")
            limit.fill("3")
            page.click("button:has-text('Save')")
            page.wait_for_timeout(800)
            pro = page.evaluate("fetch('/api/owner/plans').then(r => r.json()).then(j => j.items.find(p => p.code === 'pro').max_workspaces)")
            ev.record("O-3", "Pro plan workspace limit saved from the editor", f"API reports max_workspaces={pro}", pro == 3, page, "o3-plans")
            # 4. Stripe settings: save a test key; the page shows only the last four characters afterwards
            page.goto(engine.base + "/#/owner/billing")
            page.wait_for_selector("text=Stripe configuration")
            page.locator("label:has-text('Secret key (write-only)') input").fill("sk_test_51Browser000000000000000ABCD")
            page.locator("label:has-text('Webhook signing secret') input").fill("whsec_browser0000000000000000WXYZ")
            page.locator("label:has-text('Billing enabled') input, label:has-text('Billing enabled')").first.click()
            page.click("button:has-text('Save')")
            page.wait_for_timeout(1000)
            body = page.content()
            ev.record("O-4", "secret key saved; only its last four characters are shown", f"'…ABCD' shown: {'ABCD' in body}; full key absent: {'sk_test_51Browser' not in body}",
                      "ABCD" in body and "sk_test_51Browser" not in body, page, "o4-billing")
            # 5. a coupon is created from the dialog
            page.goto(engine.base + "/#/owner/coupons")
            page.click("button:has-text('New coupon')")
            page.locator("label:has-text('Coupon code') input").fill("BROWSER10")
            page.click("button:has-text('Create')")
            page.wait_for_selector("text=BROWSER10", timeout=15_000)
            ev.record("O-5", "coupon created and listed", "BROWSER10 appears in the list", True, page, "o5-coupons")
            # 6. e-mail settings + a template edit
            page.goto(engine.base + "/#/owner/email")
            page.wait_for_selector("text=SMTP host")
            page.locator("label:has-text('SMTP host') input").fill("smtp.example.test")
            page.locator("label:has-text('Sender e-mail') input").fill("desk@example.test")
            page.locator("label:has-text('Password (write-only)') input").fill("super-secret-smtp")
            page.locator("section:has-text('SMTP') button:has-text('Save')").first.click()
            page.wait_for_timeout(800)
            body = page.content()
            ev.record("O-6", "SMTP settings saved; the password is never shown again", f"password absent from page: {'super-secret-smtp' not in body}; host shown: {'smtp.example.test' in body}",
                      "super-secret-smtp" not in body and "smtp.example.test" in body, page, "o6-email")
            # 7. an announcement to everyone is sent and the owner (also a user) receives it in-app
            page.goto(engine.base + "/#/owner/notifications")
            page.click("button:has-text('New announcement')")
            page.locator("label:has-text('Title') input").fill("Welcome aboard")
            page.locator("label:has-text('Message') textarea").fill("Thanks for joining the pilot.")
            page.locator(".modal button:has-text('Send now')").click()
            page.wait_for_timeout(1000)
            unread = page.evaluate("fetch('/api/me/notifications').then(r => r.json()).then(j => j.items.filter(n => n.title === 'Welcome aboard').length)")
            ev.record("O-7", "announcement sent and delivered in-app", f"notifications titled 'Welcome aboard': {unread}", unread == 1, page, "o7-announcement")
            # 8. an ordinary account cannot reach the owner area; a second workspace is refused on Free
            ctx2 = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-GB")
            p2 = ctx2.new_page()
            signup(p2, engine.base, "alice@acme.test", "Alice")
            p2.goto(engine.base + "/#/owner")
            p2.wait_for_timeout(800)
            denied = p2.locator("text=This area is for platform administrators").count() > 0
            status = p2.evaluate("fetch('/api/owner/overview').then(r => r.status)")
            ev.record("O-8", "an ordinary account is refused the owner area (page and API)", f"page refuses: {denied}; API status {status}", denied and status == 403, p2, "o8-denied")
            codes = p2.evaluate("""async () => { const mk = (n) => fetch('/api/workspaces', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({name: n})}).then(r => r.status);
                return [await mk('One'), await mk('Two')]; }""")
            ev.record("O-9", "Free plan: first workspace created, second refused with 403", f"statuses {codes}", codes == [200, 403])
            # 10. the user directory shows the new account
            page.goto(engine.base + "/#/owner/users")
            page.wait_for_selector("text=alice@acme.test", timeout=15_000)
            ev.record("O-10", "new user appears in the directory", "alice@acme.test listed", True, page, "o10-users")
            # 11. Arabic, right-to-left
            page.evaluate("localStorage.setItem('desk.lang', 'ar')")
            page.goto(engine.base + "/#/owner")
            page.reload()
            page.wait_for_selector("aside.rail >> text=مالك المنصة", timeout=15_000)
            rtl = page.evaluate("document.documentElement.dir")
            ev.record("O-11", "Arabic interface renders right-to-left", f"dir={rtl}, heading in Arabic", rtl == "rtl", page, "o11-arabic")
            page.evaluate("localStorage.setItem('desk.lang', 'en')")
            # 12. phone width: no horizontal overflow on the overview
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(engine.base + "/#/owner")
            page.reload()
            page.wait_for_selector("text=Total registered users", timeout=15_000)
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            ev.record("O-12", "the layout fits a 390px viewport", f"horizontal overflow {overflow}px", overflow <= 2, page, "o12-mobile")
            ctx2.close()
            browser.close()
    finally:
        engine.stop()
    passed = sum(1 for r in ev.rows if r["result"] == "PASS")
    (out / "owner" / "evidence.json").write_text(json.dumps({"passed": passed, "failed": len(ev.rows) - passed, "rows": ev.rows}, indent=2))
    print(f"owner dashboard browser tests: {passed} passed, {len(ev.rows) - passed} failed")
    return 0 if passed == len(ev.rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "e2e-output"))
