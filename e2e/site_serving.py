"""The site has to work under the rules it is actually served with, not only when opened from disk.

site_check.py opens the pages directly and checks what they say and how they lay out. It cannot see nginx.conf,
and nginx.conf is where a site of this kind breaks silently: the configuration this one inherited carried
`script-src 'none'`, which was correct while the site was plain HTML and would have switched off the language
switch and the sign-in forms the moment a script arrived, with no error anywhere but the browser console.

So this serves site/ under the directives read out of site/nginx.conf -- the real Content-Security-Policy, the
real redirects, the real try_files -- and drives it in a browser. It asserts the pages still work under that
policy, that the addresses written down when the site was a single page still arrive somewhere, and that the
forms still point at the engine.

    python e2e/site_serving.py
"""

from __future__ import annotations

import http.server
import pathlib
import re
import sys
import threading
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
CONF = (SITE / "nginx.conf").read_text(encoding="utf-8")
HEADERS = {name: (quoted or bare) for name, quoted, bare
           in re.findall(r'add_header\s+(\S+)\s+(?:"([^"]*)"|(\S+))\s+always;', CONF)}
REDIRECTS = dict(re.findall(r"location\s*=\s*(\S+)\s*\{\s*return\s+301\s+(\S+);", CONF))
PORT = 18099
BASE = f"http://127.0.0.1:{PORT}"


class Handler(http.server.SimpleHTTPRequestHandler):
    """nginx's `try_files $uri $uri.html $uri/`, its 301s, its headers and its 404 page."""

    def do_GET(self) -> None:  # noqa: N802 - the base class names it
        path = self.path.split("?")[0].split("#")[0]
        if path in REDIRECTS:
            self.send_response(301)
            self.send_header("Location", REDIRECTS[path])
            self.end_headers()
            return
        rel = path.lstrip("/") or "index.html"
        for candidate in (rel, f"{rel}.html", f"{rel.rstrip('/')}/index.html"):
            target = SITE / candidate
            if target.is_file():
                return self._send(200, target)
        self._send(404, SITE / "404.html")

    def _send(self, status: int, target: pathlib.Path) -> None:
        body = target.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", self.guess_type(str(target)))
        for name, value in HEADERS.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:                       # the browser dropped a request it no longer needs
            pass

    def log_message(self, *args: object) -> None:
        pass


def status_of(path: str) -> int:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object) -> None:
            return None

    try:
        return urllib.request.build_opener(NoRedirect).open(BASE + path).status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> int:
    for required in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options"):
        if required not in HEADERS:
            print(f"FAIL nginx.conf sends no {required}", file=sys.stderr)
            return 1

    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    problems: list[str] = []
    try:
        for path, want in (("/features", 301), ("/features/", 301), ("/docs", 200), ("/sign-in", 200),
                           ("/sign-up", 200), ("/", 200), ("/no-such-page", 404)):
            got = status_of(path)
            print(f"  {path:16} {got}")
            if got != want:
                problems.append(f"{path} returned {got}, expected {want}")

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for path, locale in (("/", "ar-SA"), ("/", "en-US"), ("/docs.html", "ar-SA"),
                                 ("/sign-in.html", "ar-SA"), ("/sign-up.html", "en-US"), ("/no-such-page", "ar-SA")):
                context = browser.new_context(locale=locale, viewport={"width": 1280, "height": 900})
                page = context.new_page()
                errors: list[str] = []
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                missing: list[str] = []
                page.on("response", lambda r: missing.append(r.url) if r.status >= 400 and
                        not r.url.endswith("/no-such-page") else None)
                page.goto(BASE + path, wait_until="networkidle")
                page.wait_for_timeout(250)

                ran = bool(page.evaluate("!!document.querySelector('[data-lang-toggle]')")) and \
                    page.evaluate("document.documentElement.dir") in ("rtl", "ltr")
                action = page.evaluate("document.querySelector('form[data-engine-path]')?.action || ''")
                print(f"  {path:16} [{locale}] dir={page.evaluate('document.documentElement.dir')} "
                      f"h1={page.locator('h1').first.inner_text()[:34]!r}")
                if not ran:
                    problems.append(f"{path} [{locale}]: site.js did not run — the policy in nginx.conf blocks it")
                if missing:
                    problems.append(f"{path} [{locale}]: did not load {missing[:2]}")
                if path.startswith("/sign-") and not action.startswith("https://desk.ofintech.ai"):
                    problems.append(f"{path}: the form posts to {action!r}, not to the engine")
                # the 404 page is itself served with a 404 status, which the browser always reports as a console
                # error; every other console error, and any sub-resource that failed, is a real fault
                real = [e for e in errors if not (path == "/no-such-page" and "status of 404" in e)]
                if real:
                    problems.append(f"{path} [{locale}]: console {real[:2]}")
                context.close()

            # the one-page site's sections are in people's bookmarks; a fragment never reaches the server
            for fragment, expected in (("#signin", "/sign-in.html"), ("#signup", "/sign-up.html")):
                context = browser.new_context(locale="ar-SA")
                page = context.new_page()
                page.goto(f"{BASE}/{fragment}", wait_until="networkidle")
                page.wait_for_timeout(400)
                landed = page.url.replace(BASE, "")
                print(f"  {fragment:16} -> {landed}")
                if not landed.startswith(expected):
                    problems.append(f"{fragment} landed on {landed}, expected {expected}")
                context.close()
            browser.close()
    finally:
        server.shutdown()

    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print("\nOK: the pages work under the policy in site/nginx.conf, old addresses still arrive, forms reach the engine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
