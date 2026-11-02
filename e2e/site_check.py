"""The public website, checked the way a visitor meets it.

Serves site/ over a local port and drives Chromium through it: four pages, both languages, five widths. It fails if
anything spills sideways, if a string stays Arabic after switching to English, if a form posts anywhere but the
engine, if one of the engine's error codes does not reach the page it belongs to with a readable message, or if the
console reports an error.

    python e2e/site_check.py [site-dir]
"""
import pathlib
import sys, http.server, socketserver, threading, functools
from playwright.sync_api import sync_playwright

root = sys.argv[1] if len(sys.argv) > 1 else str(pathlib.Path(__file__).resolve().parents[1] / "site")
PAGES = ["index.html", "docs.html", "sign-in.html", "sign-up.html"]
WIDTHS = [380, 480, 768, 1024, 1440]
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
class Q(socketserver.TCPServer): allow_reuse_address = True
srv = Q(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
fails = []
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

    for page in PAGES:
        for lang in ("ar", "en"):
            for w in WIDTHS:
                pg.set_viewport_size({"width": w, "height": 900})
                pg.goto(f"{base}/{page}?lang={lang}", wait_until="networkidle")
                over = pg.evaluate("() => Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)")
                if over: fails.append(f"{page} {lang} @{w}px overflows by {over}px")
            d = pg.evaluate("() => [document.documentElement.lang, document.documentElement.dir]")
            want = ["en", "ltr"] if lang == "en" else ["ar", "rtl"]
            if d != want: fails.append(f"{page} {lang}: html is {d}, expected {want}")
        # nothing untranslated left behind when switching to English
        pg.goto(f"{base}/{page}?lang=en", wait_until="networkidle")
        left = pg.evaluate("""() => [...document.querySelectorAll('[data-en]')]
            .filter(el => el.innerHTML.trim() !== el.getAttribute('data-en').trim()).map(el => el.tagName).slice(0,5)""")
        if left: fails.append(f"{page}: {len(left)} node(s) did not switch to English: {left}")

    # the forms post to the engine, and nothing posts to the site itself
    pg.goto(f"{base}/sign-in.html", wait_until="networkidle")
    act = pg.eval_on_selector("form", "f => f.action")
    if "/auth/form/sign-in" not in act or "127.0.0.1:" + str(port) in act:
        fails.append(f"sign-in form posts to {act}")
    pg.goto(f"{base}/sign-up.html", wait_until="networkidle")
    act = pg.eval_on_selector("form", "f => f.action")
    if "/auth/form/sign-up" not in act: fails.append(f"sign-up form posts to {act}")
    g = pg.eval_on_selector("a[data-engine-path]", "a => a.href")
    if "/auth/form/google" not in g: fails.append(f"google link points at {g}")

    # an error code from the engine lands on the root and must be shown on the page it belongs to
    for code, page in (("signin-error", "sign-in"), ("signup-exists", "sign-up"), ("signup-password", "sign-up")):
        pg.goto(f"{base}/index.html#{code}", wait_until="networkidle")
        pg.wait_for_timeout(300)
        if page not in pg.url: fails.append(f"#{code} stayed on {pg.url}")
        shown = pg.evaluate("() => { const a = document.getElementById('alert'); return a && a.classList.contains('show') ? a.textContent.trim() : ''; }")
        if not shown: fails.append(f"#{code} showed no message")
        else: print(f"  #{code} -> {page}: {shown[:60]}")

    if errs: fails.append("console/page errors: " + "; ".join(errs[:5]))
    b.close()
srv.shutdown()
print()
if fails:
    print("FAILURES:")
    for f in fails: print("  " + f)
    sys.exit(1)
print(f"OK: {len(PAGES)} pages x 2 languages x {len(WIDTHS)} widths, forms wired, error codes routed, no console errors.")
