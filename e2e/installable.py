"""The app has to be installable from the browser, because that is how most people will get a Desk window.

A finance lead on a managed laptop often cannot run a downloaded installer, and an unsigned one asks them to step
past Gatekeeper or SmartScreen -- a bad first thing to ask of someone evaluating a financial product. Installing
from the browser avoids all of it: the same signed-by-HTTPS page they already trust becomes an icon with a window
of its own.

Installability is not one switch; it is a handful of small things that each fail quietly. The manifest has to be
linked, served as a manifest rather than as JSON or plain text, parse, carry an icon at 192 and at 512, and carry
a maskable one so a platform that crops icons does not cut through the mark. The Content-Security-Policy has to
allow fetching it at all -- `manifest-src` falls back to `default-src`, which is how it works here, and a later
tightening of one without the other would switch installing off with nothing in any log to say so.

This serves the built UI through the real engine and checks all of it over HTTP, headers included.

    cd ui && node build.mjs && python e2e/installable.py
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "ui" / "dist"
TOKEN = "e2e" + "k" * 40
PORT = 47835
BASE = f"http://127.0.0.1:{PORT}"
REQUIRED_SIZES = {"192x192", "512x512"}


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=20) as response:
        return response.status, dict(response.headers), response.read()


def main() -> int:
    if not (DIST / "index.html").is_file():
        print(f"no build at {DIST}; run `node ui/build.mjs` first", file=sys.stderr)
        return 2

    import tempfile

    data = pathlib.Path(tempfile.mkdtemp())
    env = {**os.environ, "DESK_DEV": "1", "DESK_DATA_DIR": str(data), "DESK_UI_DIR": str(DIST),
           "DESK_LOCAL_TOKEN": TOKEN, "DESK_PORT": str(PORT)}
    engine = subprocess.Popen([sys.executable, "-m", "desk_engine"], cwd=ROOT / "engine", env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    problems: list[str] = []
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if "DESK_READY" in engine.stdout.readline().decode():
                break
        else:
            print("the engine did not start", file=sys.stderr)
            return 2

        status, headers, body = get("/")
        page = body.decode("utf-8", "replace")
        if 'rel="manifest"' not in page:
            problems.append("index.html does not link a manifest, so nothing can be installed")
        for needed in ('apple-touch-icon', 'name="theme-color"'):
            if needed not in page:
                problems.append(f"index.html is missing {needed}")

        csp = headers.get("content-security-policy", "")
        if "manifest-src" in csp and "'self'" not in csp.split("manifest-src")[1].split(";")[0]:
            problems.append("the Content-Security-Policy forbids fetching the manifest")
        if "default-src 'self'" not in csp and "manifest-src" not in csp:
            problems.append("neither default-src nor manifest-src allows the manifest")

        status, headers, body = get("/manifest.webmanifest")
        print(f"  manifest: {status}  {headers.get('content-type')}")
        if status != 200:
            problems.append(f"the manifest is not served ({status})")
            raise SystemExit(report(problems))
        if "manifest+json" not in (headers.get("content-type") or ""):
            problems.append(f"the manifest is served as {headers.get('content-type')!r}, not application/manifest+json")

        try:
            manifest = json.loads(body)
        except json.JSONDecodeError as exc:
            problems.append(f"the manifest is not valid JSON ({exc})")
            raise SystemExit(report(problems)) from exc

        for field in ("name", "start_url", "display", "icons"):
            if not manifest.get(field):
                problems.append(f"the manifest has no {field}")
        if manifest.get("display") not in {"standalone", "fullscreen", "minimal-ui"}:
            problems.append(f"display is {manifest.get('display')!r}: the app would open in a browser tab")

        declared = {i.get("sizes") for i in manifest.get("icons", [])}
        for size in REQUIRED_SIZES:
            if size not in declared:
                problems.append(f"no icon declared at {size}")
        if not any("maskable" in (i.get("purpose") or "") for i in manifest.get("icons", [])):
            problems.append("no maskable icon: a platform that crops icons would cut through the mark")

        from PIL import Image

        for icon in manifest.get("icons", []):
            src = icon["src"].lstrip(".")
            status, headers, raw = get(src)
            if status != 200:
                problems.append(f"{src} is declared but not served ({status})")
                continue
            path = data / pathlib.Path(src).name
            path.write_bytes(raw)
            width, height = Image.open(path).size
            if f"{width}x{height}" != icon["sizes"]:
                problems.append(f"{src} is {width}x{height} but the manifest says {icon['sizes']}")
            if "maskable" in (icon.get("purpose") or "") and Image.open(path).convert("RGBA").getpixel((2, 2))[3] < 255:
                problems.append(f"{src} is maskable but its corner is transparent: the crop would show a gap")
            print(f"  {src}  {width}x{height}  {icon.get('purpose', 'any')}")

        status, _, _ = get("/icons/apple-touch-icon.png")
        if status != 200:
            problems.append("the apple-touch-icon is missing, so iOS would use a screenshot of the page")
    finally:
        engine.terminate()

    return report(problems)


def report(problems: list[str]) -> int:
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s): the app would not offer to install.", file=sys.stderr)
        return 1
    print("\nOK: the manifest is linked, served as a manifest, and every icon it declares is there at its size.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
