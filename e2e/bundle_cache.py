"""A UI fix has to be able to reach a browser that has already loaded the app.

The engine sends `cache-control: public, max-age=31536000, immutable` for a hashed asset name. A browser holding an
immutable response does not revalidate it, so the only thing that lets a new build through is a new URL. Before this
check existed, the build wrote dist/assets/app.css at a fixed name and index.html pointed at it, which meant every
returning browser kept the stylesheet it had cached -- for a year, past any redeploy, image rebuild or restart.

This reads a built ui/dist and fails if the bundle index.html points at could be pinned at an unchanging URL:

    node ui/build.mjs && python e2e/bundle_cache.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "ui" / "dist"
REFERENCE = re.compile(r"""(?:href|src)=["'](\./assets/[^"']+)["']""")
HASHED = re.compile(r"-[0-9A-Z]{8,}\.[0-9a-zA-Z]+$")


def main() -> int:
    index = DIST / "index.html"
    if not index.is_file():
        print(f"no build at {DIST}; run `node ui/build.mjs` first", file=sys.stderr)
        return 2

    sys.path.insert(0, str(ROOT / "engine"))
    from desk_engine.app import _is_hashed_asset

    html = index.read_text(encoding="utf-8")
    references = REFERENCE.findall(html)
    problems: list[str] = []

    for ext in ("css", "js"):
        if not any(ref.endswith(f".{ext}") for ref in references):
            problems.append(f"index.html references no .{ext} bundle at all")

    for ref in references:
        rel = ref.removeprefix("./")
        if not (DIST / rel).is_file():
            problems.append(f"{ref} is referenced but was not built")
            continue
        if not HASHED.search(rel):
            problems.append(f"{ref} has no content hash in its name, so a new build reuses its URL")
            continue
        if not _is_hashed_asset(rel):
            problems.append(f"{ref} is hashed but the engine would still serve it no-cache, losing the caching")

    # and the other side of the rule: a plain name must never be served as immutable
    for plain in ("assets/app.css", "assets/app.js", "assets/style.css"):
        if _is_hashed_asset(plain):
            problems.append(f"the engine would pin {plain} for a year although its URL never changes")

    for ref in references:
        print(f"ok   {ref}")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s): a UI change would not reach a browser that already loaded the app.",
              file=sys.stderr)
        return 1
    print(f"\n{len(references)} referenced asset(s), each content-hashed and each served immutable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
