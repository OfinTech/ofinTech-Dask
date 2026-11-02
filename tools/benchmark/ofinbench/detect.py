"""Finding the identifiers in a Gulf financial document, in Arabic or English.

Shaped identifiers are found by their shape and are reliable: an IBAN passes mod-97, a ZATCA VAT number is fifteen
digits starting and ending with 3, a Saudi mobile has a known prefix. Those are handled here and need no help.

Names are not like that. There is no offline name recogniser worth trusting on a mixed Arabic and English statement,
and a tool that guessed would quietly leave some names in the output while mangling account descriptions. So names
and counterparties come from a list the accountant supplies per case, and `scan` reports every long digit run it
could not classify so a human decides. The guarantee is not "the tool found everything"; it is "`verify` fails if
anything on the list or of a known shape is still there."
"""

from __future__ import annotations

import dataclasses
import re

from .numbers import D, fold_digits
from .pseudonym import iban_valid


@dataclasses.dataclass(frozen=True)
class Finding:
    kind: str
    value: str
    start: int
    end: int


# the shapes, most specific first: an IBAN must win over the digit runs inside it
IBAN = re.compile(rf"\b[A-Z]{{2}}{D}{{2}}(?:[  ]?[A-Z0-9٠-٩۰-۹]{{4}}){{3,7}}[A-Z0-9٠-٩۰-۹]{{0,4}}\b")
VAT = re.compile(rf"\b3{D}{{13}}3\b")
PHONE = re.compile(rf"(?:\+|00)?966[ \-]?5{D}{{8}}\b|\b0?5{D}{{8}}\b")
GOV_ID = re.compile(rf"\b[1247]{D}{{9}}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
ACCOUNT_CONTEXT = re.compile(
    rf"(?:(?:رقم[  ]+)?الحساب|حساب[  ]+رقم|حـ/|A/?C|Acct\.?|Account(?:[  ]+(?:No\.?|Number|#))?)"
    rf"[  :#\-ـ]*({D}(?:[  \-]?{D}){{5,23}})",
    re.IGNORECASE,
)
# anything long enough to be an identifier that none of the above claimed: reported, never silently replaced
UNCLASSIFIED = re.compile(rf"\b{D}{{9,}}\b")


def _overlaps(found: list[Finding], start: int, end: int) -> bool:
    return any(start < f.end and f.start < end for f in found)


def scan(text: str, *, names: list[str] | None = None) -> list[Finding]:
    """Every identifier in `text`, non-overlapping, in document order.

    Shapes are tried most specific first, and a later pattern never claims characters an earlier one already took.
    Supplied names are matched case-insensitively and whole-word, longest first so "Oasis Trading Co." is replaced
    as one counterparty rather than leaving "Trading Co." behind.
    """
    found: list[Finding] = []

    for name in sorted(names or [], key=len, reverse=True):
        clean = name.strip()
        if len(clean) < 3:
            continue
        kind = "org" if _looks_like_org(clean) else "name"
        for match in re.finditer(rf"(?<!\w){re.escape(clean)}(?!\w)", text, re.IGNORECASE):
            if not _overlaps(found, match.start(), match.end()):
                found.append(Finding(kind, match.group(0), match.start(), match.end()))

    for match in IBAN.finditer(text):
        compact = "".join(fold_digits(match.group(0)).split())
        if iban_valid(compact) and not _overlaps(found, match.start(), match.end()):
            found.append(Finding("iban", match.group(0), match.start(), match.end()))

    for kind, pattern in (("vat", VAT), ("phone", PHONE), ("gov_id", GOV_ID), ("email", EMAIL)):
        for match in pattern.finditer(text):
            if not _overlaps(found, match.start(), match.end()):
                found.append(Finding(kind, match.group(0), match.start(), match.end()))

    for match in ACCOUNT_CONTEXT.finditer(text):
        start, end = match.span(1)
        if not _overlaps(found, start, end):
            found.append(Finding("account", match.group(1), start, end))

    for match in UNCLASSIFIED.finditer(text):
        if not _overlaps(found, match.start(), match.end()):
            found.append(Finding("unclassified", match.group(0), match.start(), match.end()))

    return sorted(found, key=lambda f: f.start)


_ORG_WORDS = (
    "co", "co.", "company", "llc", "ltd", "est", "est.", "establishment", "corp", "corporation", "holding", "group",
    "trading", "industries", "contracting", "logistics", "supplies", "bank", "شركة", "مؤسسة", "مصنع", "للتجارة",
    "القابضة", "مجموعة", "بنك", "مصرف", "للمقاولات", "للتوريدات", "للخدمات",
)


def _looks_like_org(value: str) -> bool:
    """A counterparty rather than a person, so the stand-in is a company name and the document still reads correctly."""
    words = re.split(r"[\s ]+", value.casefold())
    return any(w.strip(".,") in _ORG_WORDS or w.startswith("ل") and len(w) > 4 for w in words)
