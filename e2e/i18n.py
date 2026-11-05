"""What an interface string renders as in another language, read from the dictionary the app ships.

A browser check that waits for translated words has to know those words, and the obvious way to know them --
typing them into the check -- makes a copy that goes stale without a sound. The translation is revised, the
page is still right, and the check fails looking for a sentence nobody writes any more. That has now happened
three times in one translation pass: "على المسار", "المطابقة والإقفال", and the Arabic session warning.

So the checks name the English string, which is what `ui/src/i18n/*.ts` is keyed by, and ask here for the
words the page will actually show.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = re.compile(r'^\s*("(?:[^"\\]|\\.)*")\s*:\s*("(?:[^"\\]|\\.)*")\s*,?\s*$', re.M)
PLACEHOLDER = re.compile(r"\{\d+\}")
EDGES = "،؛.,:—- "   # Arabic comma, Arabic semicolon, and the Latin ones


def dictionary(lang: str = "ar") -> dict[str, str]:
    src = (ROOT / "ui" / "src" / "i18n" / f"{lang}.ts").read_text(encoding="utf-8")
    return {json.loads(m.group(1)): json.loads(m.group(2)) for m in ENTRY.finditer(src)}


def translated(key: str, lang: str = "ar") -> str:
    """A fragment of the translation of `key` that a `text=` selector can match.

    Placeholders are filled in before the text reaches the page, so what comes back is the longest run of the
    translation that contains none of them, trimmed of the punctuation that would otherwise hang off its end.

    A key that is not in the file raises rather than returning something that will time out thirty seconds
    later: the check needs a new key, and the message says so.
    """
    table = dictionary(lang)
    if key not in table:
        raise SystemExit(f"e2e: ui/src/i18n/{lang}.ts has no entry for {key!r}; "
                         "the English string changed, so this check needs its new key")
    return max((part.strip().strip(EDGES) for part in PLACEHOLDER.split(table[key])), key=len)
