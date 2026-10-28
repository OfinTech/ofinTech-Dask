"""Cost centres as rules, not as a ledger field.

A cost centre is a set of *include* conditions (any may match) and *exclude* conditions (none may match). Each
condition combines, with AND, any of: a ledger tag (class, tracking category, analytic account, department), account
codes or names, parties, words in the description, and a date range. A cost centre therefore works on a ledger that
has no cost-centre field at all, and tells apart the cost that was tagged in the ledger from the cost a rule picked up
(the *untagged share*).

Matching is plain string comparison after normalising case, spacing and Arabic letter forms; nothing here is decided
by a model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

_AR = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ـ": None})
_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"^(?:(?P<dim>[^:]{1,60}):\s*)?(?P<value>.+?)(?:\s*\((?P<pct>\d+(?:\.\d+)?)%\))?$")


def norm(text: Any) -> str:
    """Lower case, single spaces, no diacritics or tatweel, one form of alef, teh marbuta and yeh."""
    s = unicodedata.normalize("NFKD", str(text or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return _SPACE.sub(" ", s.translate(_AR)).strip()


def parse_tags(text: str | None) -> list[tuple[str, str, float]]:
    """``Project: Riyadh | Department: Site (60%)`` → [(project, riyadh, 1.0), (department, site, 0.6)]."""
    out = []
    for part in str(text or "").split("|"):
        part = part.strip()
        if not part:
            continue
        m = _TAG.match(part)
        if not m:
            continue
        pct = float(m.group("pct")) / 100 if m.group("pct") else 1.0
        out.append((norm(m.group("dim") or ""), norm(m.group("value")), pct))
    return out


def account_matches(item: str, code: str | None, name: str | None) -> bool:
    """An account item matches by code prefix (``61`` matches 6100 and 6150) or by the account's full name."""
    item = str(item).strip()
    if not item:
        return False
    if code and str(code).strip().lower().startswith(item.lower()):
        return True
    return norm(item) == norm(name) or (bool(code) and norm(item) == norm(f"{code} {name}"))


class Condition(BaseModel):
    tag: str | None = Field(default=None, max_length=200,
                            description="a ledger tag, 'Project: Riyadh tower' or just 'Riyadh tower'")
    accounts: list[str] = Field(default_factory=list, max_length=200, description="account codes (prefixes) or names")
    parties: list[str] = Field(default_factory=list, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=50, description="words in the line description")
    date_from: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("accounts", "parties", "keywords")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return [s.strip()[:200] for s in v if s and s.strip()]

    @model_validator(mode="after")
    def _something(self) -> Condition:
        if not ((self.tag or "").strip() or self.accounts or self.parties or self.keywords):
            raise ValueError("each condition needs a tag, accounts, parties or keywords")
        return self

    def describe(self) -> str:
        bits = []
        if self.tag:
            bits.append(f"tag “{self.tag}”")
        if self.accounts:
            bits.append("account " + " or ".join(self.accounts[:6]) + ("…" if len(self.accounts) > 6 else ""))
        if self.parties:
            bits.append("party " + " or ".join(self.parties[:6]) + ("…" if len(self.parties) > 6 else ""))
        if self.keywords:
            bits.append("description contains " + " or ".join(f"“{k}”" for k in self.keywords[:6]))
        if self.date_from or self.date_to:
            bits.append(f"dated {self.date_from or '…'} to {self.date_to or '…'}")
        return " and ".join(bits)


class Rules(BaseModel):
    include: list[Condition] = Field(min_length=1, max_length=50)
    exclude: list[Condition] = Field(default_factory=list, max_length=50)


@dataclass
class Compiled:
    """A condition prepared for fast matching."""
    tag_dim: str | None
    tag_value: str | None
    accounts: list[str]
    parties: set[str]
    keywords: list[str]
    date_from: str | None
    date_to: str | None

    @classmethod
    def of(cls, c: Condition) -> Compiled:
        dim = value = None
        if c.tag and c.tag.strip():
            parsed = parse_tags(c.tag)
            if parsed:
                dim, value = parsed[0][0] or None, parsed[0][1]
        return cls(dim, value, c.accounts, {norm(p) for p in c.parties}, [norm(k) for k in c.keywords],
                   c.date_from, c.date_to)

    def match(self, line: dict[str, Any]) -> float:
        """The share of the line this condition takes (0 = no match; a split tag gives its percentage)."""
        d = line["date"]
        if (self.date_from and d < self.date_from) or (self.date_to and d > self.date_to):
            return 0.0
        if self.accounts and not any(account_matches(a, line["account_code"], line["account"]) for a in self.accounts):
            return 0.0
        if self.parties and line["_party"] not in self.parties:
            return 0.0
        if self.keywords and not any(k in line["_desc"] for k in self.keywords):
            return 0.0
        if self.tag_value is not None:
            share = 0.0
            for dim, value, pct in line["_tags"]:
                if value == self.tag_value and (self.tag_dim is None or dim == self.tag_dim):
                    share = max(share, pct)
            return share
        return 1.0


class Matcher:
    def __init__(self, rules: Rules | dict[str, Any]) -> None:
        r = rules if isinstance(rules, Rules) else Rules.model_validate(rules)
        self.rules = r
        self.include = [(i, Compiled.of(c)) for i, c in enumerate(r.include)]
        self.exclude = [Compiled.of(c) for c in r.exclude]

    def match(self, line: dict[str, Any]) -> tuple[float, int | None, bool]:
        """(share of the line, index of the include condition that took it, whether that condition is a tag)."""
        best, which = 0.0, None
        for i, c in self.include:
            share = c.match(line)
            if share > best:
                best, which = share, i
                if share >= 1.0:
                    break
        if not best or any(x.match(line) for x in self.exclude):
            return 0.0, None, False
        return best, which, self.include[which][1].tag_value is not None if which is not None else False


def prepare(line: dict[str, Any]) -> dict[str, Any]:
    """Adds the normalised fields matching uses (once per line)."""
    line["_party"] = norm(line.get("party"))
    line["_desc"] = norm(line.get("description"))
    line["_tags"] = parse_tags(line.get("tags"))
    return line
