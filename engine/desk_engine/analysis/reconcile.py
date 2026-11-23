"""Bank reconciliation: one month of a bank statement against the ledger's bank and cash lines.

What it does is what a person does with two printouts and a pen: ticks off every movement that appears on both,
and lists what is left on each side. A movement is ticked when the direction and the amount agree to the cent, on
the same date first, then within a few days, then on the amount alone within the month (and those last ones are
listed, because they are the ones worth a second look). It reads; it posts nothing, proposes no entry, carries no
score and learns nothing from what the accountant then decides.

Why it exists. On 23 and 24 Nov 2026 two models were asked to compare a statement's closing balance with the books.
With only a dataset query to work with, one took the highest balance on the statement for the closing balance,
and one ran sixteen queries and stopped without an answer. The question is closed (the two files hold the whole
answer), so it is answered here, in code, and the model reports it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from ..errors import Invalid

WINDOW_DAYS = 3
MAX_ROWS = 50_000

# Column names as banks and spreadsheets write them (English, Arabic, Spanish, French). On a bank statement a
# credit is money received and a debit is money paid: the bank's side of the account, the reverse of the ledger's.
NAMES: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "value date", "posting date", "التاريخ", "تاريخ", "تاريخ العملية", "fecha",
             "fecha valor", "date d'opération", "date de valeur"),
    "money_in": ("money in", "paid in", "in", "credit", "credits", "deposit", "deposits", "receipts", "received",
                 "دائن", "إيداع", "ايداع", "إيداعات", "وارد", "الوارد", "مقبوضات", "entrada", "entradas", "abono",
                 "abonos", "haber", "ingresos", "crédit", "crédits", "encaissement", "encaissements"),
    "money_out": ("money out", "paid out", "out", "debit", "debits", "withdrawal", "withdrawals", "payments", "paid",
                  "مدين", "سحب", "سحوبات", "صادر", "الصادر", "مدفوعات", "salida", "salidas", "cargo", "cargos", "debe",
                  "pagos", "débit", "débits", "décaissement", "décaissements"),
    "amount": ("amount", "المبلغ", "مبلغ", "importe", "monto", "montant"),
    "balance": ("balance", "running balance", "closing balance", "الرصيد", "رصيد", "saldo", "solde"),
    "description": ("description", "details", "narrative", "particulars", "memo", "reference", "transaction",
                    "البيان", "الوصف", "التفاصيل", "الشرح", "concepto", "descripción", "descripcion", "detalle",
                    "libellé", "libelle", "désignation"),
}
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y")


def _norm(name: Any) -> str:
    return " ".join(str(name or "").replace("_", " ").strip().lower().split())


def detect_columns(columns: list[dict[str, Any]], given: dict[str, Any] | None = None) -> dict[str, int]:
    """Which column of the statement is the date, the money in and out (or one signed amount), the balance and the
    description: the names a caller gave, else the usual names. Raises Invalid, naming the columns it found, when
    it cannot tell where the date or the amounts are."""
    names = [_norm(c.get("name")) for c in columns]
    found: dict[str, int] = {}
    for role, asked in (given or {}).items():
        if role not in NAMES or not asked:
            continue
        if _norm(asked) not in names:
            raise Invalid(f"The statement has no column “{asked}”. Its columns are: "
                          + ", ".join(str(c.get("name")) for c in columns) + ".", code="no_such_column")
        found[role] = names.index(_norm(asked))
    for role, usual in NAMES.items():
        if role in found:
            continue
        hit = next((i for i, n in enumerate(names) if n in usual and i not in found.values()), None)
        if hit is None and role == "date":
            hit = next((i for i, c in enumerate(columns) if c.get("type") == "date"), None)
        if hit is not None:
            found[role] = hit
    if "description" not in found:
        text = next((i for i, c in enumerate(columns) if c.get("type") == "text" and i not in found.values()), None)
        if text is not None:
            found["description"] = text
    if "date" not in found or not ({"money_in", "money_out"} & set(found) or "amount" in found):
        raise Invalid("This does not read as a bank statement: it needs a date column and either money in and money "
                      "out columns or one amount column. Its columns are: "
                      + ", ".join(str(c.get("name")) for c in columns)
                      + ". Name them with columns (date, money_in, money_out, amount, balance, description).",
                      code="not_a_statement")
    return found


def _day(v: Any) -> str | None:
    text = str(v or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text):
        return text[:10]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    text = str(v).strip().replace(",", "").replace(" ", "").replace(" ", "")
    neg = text.startswith("(") and text.endswith(")")
    try:
        n = float(text.strip("()"))
    except ValueError:
        return None
    return -n if neg else n


def statement_lines(rows: list[list[Any]], cols: dict[str, int]) -> tuple[list[dict[str, Any]], int]:
    """The statement's rows as movements and balances, and how many rows had no readable date."""
    out: list[dict[str, Any]] = []
    unread = 0
    for n, row in enumerate(rows, start=1):
        day = _day(row[cols["date"]])
        if day is None:
            unread += 1
            continue
        money_in = money_out = 0.0
        if "amount" in cols and not ({"money_in", "money_out"} & set(cols)):
            amount = _num(row[cols["amount"]]) or 0.0
            money_in, money_out = (amount, 0.0) if amount >= 0 else (0.0, -amount)
        else:
            money_in = abs(_num(row[cols["money_in"]]) or 0.0) if "money_in" in cols else 0.0
            money_out = abs(_num(row[cols["money_out"]]) or 0.0) if "money_out" in cols else 0.0
        out.append({"row": n, "date": day, "in": round(money_in, 2), "out": round(money_out, 2),
                    "balance": _num(row[cols["balance"]]) if "balance" in cols else None,
                    "text": str(row[cols["description"]] or "").strip() if "description" in cols else ""})
    return out, unread


def _gap(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _cents(v: float) -> int:
    return round(v * 100)


def reconcile(statement: list[dict[str, Any]], book: list[dict[str, Any]], *, statement_opening: float | None,
              statement_closing: float | None, book_opening: float | None, book_closing: float | None,
              window: int = WINDOW_DAYS) -> dict[str, Any]:
    """Ticks off the movements both sides have and returns what is left, with the arithmetic of the difference.

    Each movement has ``date``, ``in`` and ``out`` (one of them zero) and whatever identifies it. Signs follow the
    account holder: ``in`` raises the balance on both sides."""
    def moves(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(x, _dir="in" if x["in"] else "out", _c=_cents(x["in"] or x["out"]))
                for x in items if _cents(x["in"]) or _cents(x["out"])]
    s_moves, b_moves = moves(statement), moves(book)
    open_book: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for b in b_moves:
        open_book[(b["_dir"], b["_c"])].append(b)
    matched: list[dict[str, Any]] = []
    left = list(s_moves)
    for how, limit in (("same date", 0), (f"within {window} days", window), ("amount only", None)):
        still = []
        for s in left:
            pool = open_book.get((s["_dir"], s["_c"])) or []
            near = [b for b in pool if limit is None or _gap(s["date"], b["date"]) <= limit]
            if not near:
                still.append(s)
                continue
            b = min(near, key=lambda x: _gap(s["date"], x["date"]))
            pool.remove(b)
            matched.append({"statement": s, "book": b, "how": how, "days": _gap(s["date"], b["date"])})
        left = still
    book_only = sorted((b for pool in open_book.values() for b in pool), key=lambda x: (x["date"], str(x.get("entry"))))
    bank_only = left

    def total(items: list[dict[str, Any]], key: str) -> float:
        return round(sum(x[key] for x in items), 2)
    # statement less ledger: a payment the bank has not seen leaves the statement higher, a deposit it has not seen
    # leaves it lower; a receipt only the bank has makes it higher, a charge only the bank has makes it lower
    explained = round(total(book_only, "out") - total(book_only, "in") + total(bank_only, "in") - total(bank_only, "out"), 2)
    difference = (None if statement_closing is None or book_closing is None
                  else round(statement_closing - book_closing, 2))
    opening_gap = (None if statement_opening is None or book_opening is None
                   else round(statement_opening - book_opening, 2))
    unexplained = None if difference is None else round(difference - explained - (opening_gap or 0.0), 2)

    def clean(x: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in x.items() if not k.startswith("_")}
    by_how: dict[str, int] = defaultdict(int)
    for m in matched:
        by_how[m["how"]] += 1
    return {"statement_opening": statement_opening, "statement_closing": statement_closing,
            "book_opening": book_opening, "book_closing": book_closing, "difference": difference,
            "opening_difference": opening_gap, "explained": explained, "unexplained": unexplained,
            "statement_movements": len(s_moves), "book_movements": len(b_moves), "matched": len(matched),
            "matched_by": dict(by_how), "window_days": window,
            "amount_only": [{"statement": clean(m["statement"]), "book": clean(m["book"]), "days": m["days"]}
                            for m in matched if m["how"] == "amount only"],
            "book_only": [clean(b) for b in book_only], "bank_only": [clean(s) for s in bank_only]}


def statement_balances(lines: list[dict[str, Any]], month: str) -> tuple[float | None, float | None, str | None]:
    """The statement's balance before the month's first movement and after its last, and the date of the last
    row: read from the balance column, in the order of the rows (the highest balance is not the closing one)."""
    rows = [x for x in lines if x["date"][:7] == month and x["balance"] is not None]
    if not rows:
        return None, None, None
    # a statement printed newest first: within one day its rows run backwards too
    newest_first = lines[0]["date"] > lines[-1]["date"]
    rows.sort(key=lambda x: (x["date"], -x["row"] if newest_first else x["row"]))
    first, last = rows[0], rows[-1]
    return round(first["balance"] - first["in"] + first["out"], 2), round(last["balance"], 2), last["date"]
