"""Three closed questions about a ledger, answered from its own lines: what was posted on an account, what an
entry holds, and which lines and accounts moved a month's profit.

Why it exists. On 24 Nov 2026 two models were asked, on an invented company, what caused its one loss-making
month, what zakat had been paid, and the amount of a credit note before VAT. For the first, each compared the month
with an average it had worked out itself (a different one on each run) and named no account. For the second, one
said the books held nothing while the entry was there. For the third, one gave the total with the VAT in it. The
ledger holds each answer whole, so they are read here, in code, with the baseline said in words, and the model
reports them. Nothing here changes data, scores anything or learns from what is asked.
"""

from __future__ import annotations

import re
from collections import defaultdict
from statistics import mean
from typing import Any

from ..costcentres.rules import norm
from ..errors import Invalid
from .ledger import Ledger
from .overview import PL_LINES
from .roles import BALANCE_ROLES, INCOME_ROLES, PL_ROLES, WORDS, account_key

MAX_LINES = 60
MAX_ENTRIES = 25
AVERAGE_MONTHS = 6

# An account that holds a tax collected for, or recovered from, the authority on each sale and purchase. Found by
# name: the account classes do not tell it from any other liability.
TAX_WORDS = ("vat", "value added tax", "input tax", "output tax", "sales tax", "gst", "القيمة المضافة",
             "ضريبة المدخلات", "ضريبة المخرجات", "ضريبة المبيعات", "iva", "tva", "taxe sur la valeur ajoutee")
# Words that mean the same thing in the languages a ledger and a question may be written in: asked for "zakat" or
# "credit notes" in English, an account or an entry written in Arabic is still found. The account roles' own word
# lists come first; the rest are things a question names that no role does.
CONCEPTS: list[tuple[str, ...]] = [tuple(v) for v in WORDS.values()] + [
    TAX_WORDS,
    ("credit note", "credit notes", "credit memo", "sales return", "sales returns", "returns inwards",
     "اشعار دائن", "اشعارات دائنة", "مردودات", "مرتجعات", "مرتجع", "nota de credito", "notas de credito",
     "devolucion", "devoluciones", "avoir", "avoirs", "retour", "retours"),
    ("rent", "lease", "ايجار", "الايجار", "alquiler", "arrendamiento", "loyer", "loyers"),
    ("bank", "cash", "بنك", "البنك", "نقد", "النقدية", "الصندوق", "banco", "caja", "banque", "caisse"),
    ("receivable", "receivables", "debtors", "customers", "الذمم المدينة", "مدينون", "العملاء", "clientes",
     "deudores", "clients", "creances"),
    ("payable", "payables", "creditors", "suppliers", "vendors", "الذمم الدائنة", "دائنون", "الموردين", "الموردون",
     "proveedores", "acreedores", "fournisseurs", "dettes fournisseurs"),
    ("sales", "revenue", "income", "turnover", "مبيعات", "المبيعات", "ايرادات", "الايرادات", "ventas", "ingresos",
     "ventes", "chiffre d'affaires", "produits"),
    ("marketing", "advertising", "تسويق", "التسويق", "دعاية", "اعلان", "publicidad", "publicite"),
    ("subcontractor", "subcontractors", "مقاولو الباطن", "مقاولي الباطن", "subcontratistas", "sous-traitants",
     "sous-traitance"),
]
_SEPARATORS = re.compile(r"[-_/\\.،,:;()]+")


def plain(text: Any) -> str:
    """For comparing only: lower case, one form of each letter, punctuation between words read as a space, and
    an Arabic word without its article (المبيعات and مبيعات are one word to a person asking)."""
    return " ".join(w[2:] if w.startswith("ال") and len(w) > 4 else w for w in _SEPARATORS.sub(" ", norm(text)).split())


_CONCEPTS = [tuple(plain(w) for w in group) for group in CONCEPTS]


def _has(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


def _related(asked: str) -> list[tuple[str, ...]]:
    """The concepts a question names, each with its words in every language, the more specific first (the order
    of CONCEPTS): "VAT payable" is the tax account before it is a payable."""
    return [group for group in _CONCEPTS if any(_has(asked, w) for w in group)]


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def chart(led: Ledger) -> dict[str, dict[str, Any]]:
    """The ledger's accounts: key -> code, name, class and role."""
    out: dict[str, dict[str, Any]] = {}
    for ln in led.lines:
        key = account_key(ln["account_code"], ln["account"])
        if key not in out:
            out[key] = {"key": key, "code": ln["account_code"] or "", "name": ln["account"],
                        "label": led.names.get(key, ln["account"]), "class": ln["account_class"], "role": led.role_of(ln)}
    return out


def match_accounts(accounts: dict[str, dict[str, Any]], asked: str) -> list[str]:
    """The accounts a question means: by code, then by its name or part of it, then by all its words, then by a
    word for the same thing in another language. Empty when nothing fits."""
    raw = str(asked or "").strip()
    q = plain(raw)
    if not q:
        return []
    codes = [k for k, a in accounts.items() if a["code"] and a["code"].lower() == raw.lower()]
    if codes:
        return codes
    first = raw.split()[0].lower()
    lead = [k for k, a in accounts.items() if a["code"] and a["code"].lower() == first and len(raw.split()) > 1]
    if lead:
        return lead
    names = {k: plain(a["name"]) for k, a in accounts.items()}
    # whole words, never part of one: "rent" is not in "current account"; the start of a word is, last ("haul")
    for rule in (lambda n: n == q, lambda n: _has(n, q), lambda n: all(_has(n, w) for w in q.split()),
                 lambda n: all(len(w) > 2 and any(part.startswith(w) for part in n.split()) for w in q.split())):
        hit = [k for k, n in names.items() if rule(n)]
        if hit:
            return hit
    for group in _related(q):
        hit = [k for k, n in names.items() if any(_has(n, w) for w in group)]
        if hit:
            return hit
    return []


def _entries(led: Ledger) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ln in led.lines:
        out[ln["entry"]].append(ln)
    return out


_TAX = tuple(plain(w) for w in TAX_WORDS)


def _is_tax(name: str) -> bool:
    n = plain(name)
    return any(_has(n, w) for w in _TAX)


def entry_amounts(led: Ledger, lines: list[dict[str, Any]]) -> dict[str, float] | None:
    """An invoice, bill or credit note read as amount before tax, tax and total. Only for an entry with a line on
    a sales-tax account, a line on a customer, supplier or bank account, and something else between them (the
    sale, the purchase, the stock, the expense) on the same side as the tax: a tax payment or a journal that
    moves tax alone has no amount before tax and gets none."""
    tax = [ln for ln in lines if _is_tax(ln["account"])]
    settled = [ln for ln in lines if ln not in tax and led.role_of(ln) in ("receivables", "payables", "cash")]
    goods = [ln for ln in lines if ln not in tax and ln not in settled]
    if not (tax and goods and settled):
        return None
    net, vat, total = (sum(ln["debit"] - ln["credit"] for ln in group) for group in (goods, tax, settled))
    if net * vat <= 0 or abs(net + vat + total) > 0.005:
        return None
    return {"before_tax": round(abs(net), 2), "tax": round(abs(vat), 2), "total": round(abs(total), 2)}


def _others(lines: list[dict[str, Any]], this_key: str, names: dict[str, str]) -> list[dict[str, Any]]:
    """The other accounts of an entry, one row each."""
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for ln in lines:
        key = account_key(ln["account_code"], ln["account"])
        if key != this_key:
            sums[key][0] += ln["debit"]
            sums[key][1] += ln["credit"]
    return [{"account": names.get(k, k), "debit": round(d, 2), "credit": round(c, 2)}
            for k, (d, c) in sorted(sums.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))]


def _bank_share(led: Ledger, lines: list[dict[str, Any]], ln: dict[str, Any]) -> tuple[float, float]:
    """How much of one line was paid from, and received into, the bank in its own entry. An entry that pays several
    lines from one bank movement splits it by their size."""
    cash = [x for x in lines if led.role_of(x) == "cash"]
    rest = [x for x in lines if led.role_of(x) != "cash"]
    out_ = sum(x["credit"] - x["debit"] for x in cash)
    paid = received = 0.0
    if out_ > 0.005 and ln["debit"] > 0:
        debits = sum(x["debit"] for x in rest)
        paid = ln["debit"] * min(1.0, out_ / debits) if debits else 0.0
    if out_ < -0.005 and ln["credit"] > 0:
        credits = sum(x["credit"] for x in rest)
        received = ln["credit"] * min(1.0, -out_ / credits) if credits else 0.0
    return paid, received


def account_activity(led: Ledger, key: str, first: str, last: str, party: str | None = None) -> dict[str, Any]:
    """One account between two months: where it stood, every line with the rest of its entry, and where it ended."""
    accounts = chart(led)
    acct = accounts[key]
    entries = _entries(led)
    want = plain(party) if party else ""
    rows, months = [], {m: [0.0, 0.0] for m in led.months if first <= m <= last}
    paid = received = 0.0
    is_cash = acct["role"] == "cash"
    for ln in sorted(led.lines, key=lambda x: (x["date"], x.get("row") or 0)):
        if account_key(ln["account_code"], ln["account"]) != key or not first <= ln["date"][:7] <= last:
            continue
        if want and want not in plain(ln.get("party")):
            continue
        whole = entries[ln["entry"]]
        p, r = (0.0, 0.0) if is_cash else _bank_share(led, whole, ln)
        paid, received = paid + p, received + r
        months[ln["date"][:7]][0] += ln["debit"]
        months[ln["date"][:7]][1] += ln["credit"]
        rows.append({"date": ln["date"], "entry": ln["entry"], "type": ln.get("type") or "", "party": ln.get("party") or "",
                     "description": ln.get("description") or "", "debit": round(ln["debit"], 2),
                     "credit": round(ln["credit"], 2), "with": _others(whole, key, led.names),
                     "amounts": entry_amounts(led, whole), "paid_from_bank": round(p, 2),
                     "received_into_bank": round(r, 2)})
    opening = closing = None
    if acct["role"] in BALANCE_ROLES and key in led.balances and not want:
        i = led.months.index(first)
        opening = led.balances[key][led.months[i - 1]] if i > 0 else led.opening.get(key, 0.0)
        closing = led.balances[key][last]
    debit, credit = sum(x["debit"] for x in rows), sum(x["credit"] for x in rows)
    return {"account": acct, "first": first, "last": last, "party": party, "lines": rows, "count": len(rows),
            "debit": _r(debit), "credit": _r(credit), "net": _r(debit - credit), "opening": _r(opening),
            "closing": _r(closing), "paid_from_bank": None if is_cash else _r(paid),
            "received_into_bank": None if is_cash else _r(received),
            "by_month": [{"month": m, "debit": _r(d), "credit": _r(c),
                          "closing": _r(led.balances[key][m]) if closing is not None else None}
                         for m, (d, c) in months.items()]}


def find_entries(led: Ledger, first: str, last: str, entry: str | None = None,
                 search: str | None = None) -> dict[str, Any]:
    """Whole entries: one by its number, or every entry between two months whose type, descriptions, parties or
    accounts hold the words asked for (or a word for the same thing in another language)."""
    if not (entry or search):
        raise Invalid("Give an entry number or words to search for.", code="nothing_asked")
    entries = _entries(led)
    found: list[tuple[str, list[dict[str, Any]]]] = []
    if entry:
        want = str(entry).strip().lower()
        found = [(e, ls) for e, ls in entries.items() if e.lower() == want]
        if not found:
            found = [(e, ls) for e, ls in entries.items() if want in e.lower()]
    else:
        q = plain(search)
        blobs = {e: plain(" ".join(str(x) for ln in ls for x in (
            ln["entry"], ln.get("type"), ln.get("description"), ln.get("party"), ln["account"], ln["account_code"]) if x))
            for e, ls in entries.items() if first <= min(ln["date"] for ln in ls)[:7] <= last}
        # the words as asked first; a word for the same thing in another language only when those find nothing
        rules = [lambda b: _has(b, q) or all(_has(b, w) for w in q.split())]
        rules += [lambda b, g=group: any(_has(b, w) for w in g) for group in _related(q)]
        for rule in rules:
            found = [(e, entries[e]) for e, b in blobs.items() if rule(b)]
            if found:
                break
    found.sort(key=lambda kv: (min(ln["date"] for ln in kv[1]), kv[0]))
    out = []
    for e, ls in found:
        parties = [ln["party"] for ln in ls if ln.get("party")]
        out.append({"entry": e, "date": min(ln["date"] for ln in ls),
                    "type": next((ln["type"] for ln in ls if ln.get("type")), ""),
                    "party": max(set(parties), key=parties.count) if parties else "",
                    "lines": [{"account": led.names.get(account_key(ln["account_code"], ln["account"]), ln["account"]),
                               "debit": round(ln["debit"], 2), "credit": round(ln["credit"], 2),
                               "description": ln.get("description") or "", "party": ln.get("party") or ""} for ln in ls],
                    "amounts": entry_amounts(led, ls)})
    types = sorted({ln["type"] for ln in led.lines if ln.get("type")})
    return {"entries": out[:MAX_ENTRIES], "count": len(out), "first": first, "last": last, "entry": entry,
            "search": search, "types": types}


def profit_drivers(led: Ledger, month: str, against: str = "average", span: int = AVERAGE_MONTHS) -> dict[str, Any]:
    """A month's profit and loss against a baseline, line by line and account by account, with each one's effect
    on net profit. The baseline is the average of the months before it (six unless asked otherwise), the month
    before it, or the same month a year earlier. The effects of the lines add up to the change in net profit."""
    months = led.months
    i = months.index(month)
    if against == "previous_month":
        base = months[i - 1:i] if i > 0 else []
    elif against == "last_year":
        same = f"{int(month[:4]) - 1:04d}{month[4:]}"
        base = [same] if same in months else []
    elif against == "average":
        base = months[max(0, i - max(1, min(12, span))):i]
    else:
        raise Invalid("against is average, previous_month or last_year.", code="bad_baseline")
    if not base:
        what = {"previous_month": "no month before it", "last_year": "not the same month a year earlier",
                "average": "no month before it"}[against]
        raise Invalid(f"The ledger starts in {months[0]}: it has {what} to compare {month} with.", code="no_baseline")

    def over_base(f: Any) -> float:
        return mean(f(m) for m in base)
    lines = []
    for name, kind, _ in PL_LINES:
        value, usual = led.value(name, month) or 0.0, over_base(lambda m, n=name: led.value(n, m) or 0.0)
        change = value - usual
        lines.append({"measure": name, "kind": kind, "value": _r(value), "baseline": _r(usual), "change": _r(change),
                      "effect": _r(-change if kind == "cost" else change) if kind in ("income", "cost") else None})
    accounts = []
    for role in PL_ROLES:
        per_month = led.by_account.get(role, {})
        for key in {k for m in [month, *base] for k in per_month.get(m, {})}:
            value = per_month.get(month, {}).get(key, 0.0)
            usual = over_base(lambda m, k=key, p=per_month: p.get(m, {}).get(k, 0.0))
            change = value - usual
            if abs(change) < 0.005:
                continue
            accounts.append({"measure": role, "key": key, "account": led.names.get(key, key), "value": _r(value),
                             "baseline": _r(usual), "change": _r(change),
                             "effect": _r(change if role in INCOME_ROLES else -change)})
    accounts.sort(key=lambda a: -abs(a["effect"] or 0))
    year = months[-12:]
    return {"month": month, "against": against, "baseline_months": base, "lines": lines, "accounts": accounts[:12],
            "accounts_count": len(accounts),
            "net_profit": next(ln for ln in lines if ln["measure"] == "net_profit"),
            "year": [year[0], year[-1]],
            "loss_months": [{"month": m, "net_profit": _r(led.value("net_profit", m) or 0.0)} for m in year
                            if (led.value("net_profit", m) or 0.0) < -0.005]}
