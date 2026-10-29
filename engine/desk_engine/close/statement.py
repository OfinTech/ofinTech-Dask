"""Bank statements for the close: read a statement file and match it to the ledger's bank lines.

Matching is by amount (to the cent) and date (within five days, nearest first). What is left is the reconciliation:

* in the ledger, not yet on the statement: cheques and transfers not cleared, deposits in transit;
* on the statement, not in the ledger: bank charges, interest, receipts nobody recorded.

With the statement's closing balance:  statement + ledger-only items = ledger balance + statement-only items.
Whatever does not agree is shown as the unexplained difference.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..costcentres.rules import norm
from ..errors import Invalid
from .compute import r2

DATE_WORDS = ["date", "value date", "posting date", "transaction date", "booking date", "تاريخ", "التاريخ",
              "تاريخ القيد", "تاريخ العملية", "fecha", "fecha valor", "date operation", "date valeur", "date de valeur"]
DESC_WORDS = ["description", "details", "narrative", "narration", "memo", "reference", "particulars", "transaction",
              "البيان", "الوصف", "التفاصيل", "المرجع", "concepto", "descripcion", "detalle", "libelle", "libelle operation"]
AMOUNT_WORDS = ["amount", "net amount", "المبلغ", "القيمة", "importe", "montant"]
OUT_WORDS = ["debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "payments", "مدين", "سحب",
             "مسحوبات", "cargo", "cargos", "retiros", "debit euros"]
IN_WORDS = ["credit", "credits", "deposit", "deposits", "money in", "paid in", "receipts", "دائن", "ايداع", "ايداعات",
            "abono", "abonos", "ingresos", "credit euros"]
BALANCE_WORDS = ["balance", "running balance", "closing balance", "الرصيد", "saldo", "solde"]

FEE_WORDS = [norm(w) for w in ("fee", "fees", "charge", "charges", "commission", "service charge", "bank charge",
                               "رسوم", "عمولة", "عمولات", "comision", "comisiones", "frais", "commission")]
INTEREST_WORDS = [norm(w) for w in ("interest", "فوائد", "فائدة", "intereses", "interets")]
BANK_CHARGE_ACCOUNT = [norm(w) for w in ("bank charge", "bank charges", "bank fee", "bank fees", "رسوم بنكية",
                                         "عمولات بنكية", "مصاريف بنكية", "comisiones bancarias", "gastos bancarios",
                                         "frais bancaires")]
INTEREST_INCOME_ACCOUNT = [norm(w) for w in ("interest income", "interest received", "ايرادات فوائد", "ايراد فوائد",
                                             "ingresos por intereses", "produits financiers", "interets recus")]
MATCH_DAYS = 5


def _col(header: list[str], words: list[str]) -> int | None:
    names = [norm(h) for h in header]
    for i, n in enumerate(names):          # exact header first
        if n in words:
            return i
    for i, n in enumerate(names):
        if n and any(n.startswith(w) or f" {w}" in f" {n}" for w in words):
            return i
    return None


def read_statement(data: bytes, filename: str) -> dict[str, Any]:
    """Statement lines (date, description, amount with money in positive) and the closing balance if the file has a
    balance column. Raises Invalid naming what is missing."""
    from ..data.importer import parse_date, parse_number, read_workbook

    words = {"date": [norm(w) for w in DATE_WORDS], "desc": [norm(w) for w in DESC_WORDS],
             "amount": [norm(w) for w in AMOUNT_WORDS], "out": [norm(w) for w in OUT_WORDS],
             "in": [norm(w) for w in IN_WORDS], "balance": [norm(w) for w in BALANCE_WORDS]}
    for table in read_workbook(data, filename):
        for hi, row in enumerate(table.raw[:30]):
            header = [str(v or "").strip() for v in row]
            c_date = _col(header, words["date"])
            if c_date is None:
                continue
            c_amt, c_out, c_in = _col(header, words["amount"]), _col(header, words["out"]), _col(header, words["in"])
            if c_amt is None and (c_out is None or c_in is None):
                continue
            c_desc, c_bal = _col(header, words["desc"]), _col(header, words["balance"])
            lines: list[dict[str, Any]] = []
            for n, raw in enumerate(table.raw[hi + 1:], start=hi + 2):
                raw = list(raw) + [None] * len(header)
                d = parse_date(raw[c_date]) if raw[c_date] not in (None, "") else None
                if not isinstance(d, date):
                    continue
                if c_amt is not None:
                    amount = parse_number(raw[c_amt])
                else:
                    out_v, in_v = parse_number(raw[c_out]) or 0.0, parse_number(raw[c_in]) or 0.0
                    amount = (in_v or 0.0) - abs(out_v or 0.0) if (out_v or in_v) else None
                if amount is None or abs(amount) < 0.005:
                    continue
                bal = parse_number(raw[c_bal]) if c_bal is not None else None
                lines.append({"id": f"s{n}", "row": n, "date": d.isoformat(), "amount": r2(amount),
                              "description": str(raw[c_desc]).strip()[:200] if c_desc is not None and raw[c_desc] not in (None, "") else "",
                              "balance": r2(bal) if bal is not None else None})
            if not lines:
                continue
            closing = None
            if c_bal is not None and any(x["balance"] is not None for x in lines):
                ascending = lines[0]["date"] <= lines[-1]["date"]
                ordered = lines if ascending else list(reversed(lines))
                last = [x for x in ordered if x["balance"] is not None][-1]
                closing = {"date": last["date"], "amount": last["balance"]}
            return {"sheet": table.name, "header_row": hi + 1, "lines": lines, "closing": closing,
                    "first_date": min(x["date"] for x in lines), "last_date": max(x["date"] for x in lines)}
    raise Invalid("No bank statement table was found: the file needs a date column and either an amount column or "
                  "money-in and money-out columns, under a header row.", code="not_a_statement")


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def reconcile(statement: dict[str, Any], ledger_lines: list[dict[str, Any]], *, month: str, month_end: str,
              account: dict[str, Any], book_balance: float, closing_balance: float | None) -> dict[str, Any]:
    """Match the month's statement lines to the month's ledger lines on the bank account and reconcile."""
    stmt = [x for x in statement["lines"] if x["date"][:7] == month]
    book = [{"id": f"l{ln['row']}", "date": ln["date"], "amount": r2(ln["debit"] - ln["credit"]), "entry": ln["entry"],
             "description": ln.get("description") or "", "party": ln.get("party")}
            for ln in ledger_lines if ln["date"][:7] == month and abs(ln["debit"] - ln["credit"]) > 0.005]
    free = {b["id"]: b for b in book}
    matched = []
    for s in sorted(stmt, key=lambda x: x["date"]):
        cands = [b for b in free.values() if abs(b["amount"] - s["amount"]) < 0.005 and _days(b["date"], s["date"]) <= MATCH_DAYS]
        if not cands:
            continue
        best = min(cands, key=lambda b: (_days(b["date"], s["date"]), b["date"]))
        del free[best["id"]]
        matched.append({"statement": s, "ledger": best})
    matched_ids = {m["statement"]["id"] for m in matched}
    stmt_only = [s for s in stmt if s["id"] not in matched_ids]
    ledger_only = sorted(free.values(), key=lambda b: b["date"])
    for b in ledger_only:
        b["age_days"] = _days(b["date"], month_end)
    closing = closing_balance
    if closing is None and statement.get("closing") and statement["closing"]["date"][:7] == month:
        closing = statement["closing"]["amount"]
    out: dict[str, Any] = {
        "account": account, "statement_lines": len(stmt), "ledger_lines": len(book), "matched": len(matched),
        "matched_pairs": matched[:500], "statement_only": stmt_only, "ledger_only": ledger_only,
        "statement_in": r2(sum(s["amount"] for s in stmt if s["amount"] > 0)),
        "statement_out": r2(-sum(s["amount"] for s in stmt if s["amount"] < 0)),
        "book_balance": r2(book_balance), "closing_balance": r2(closing) if closing is not None else None,
        "ledger_only_total": r2(sum(b["amount"] for b in ledger_only)),
        "statement_only_total": r2(sum(s["amount"] for s in stmt_only)),
    }
    if closing is not None:
        adjusted_bank = closing + out["ledger_only_total"]
        adjusted_book = book_balance + out["statement_only_total"]
        out["adjusted_bank"] = r2(adjusted_bank)
        out["adjusted_book"] = r2(adjusted_book)
        out["difference"] = r2(adjusted_bank - adjusted_book)
    else:
        out["difference"] = None
    return out


def statement_proposals(rec: dict[str, Any], accts: Any, month_end: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Entries for bank charges and interest that are on the statement only, and review items for the rest."""
    account = rec["account"]
    bank_line = {"account_code": account["code"], "account": account["name"], "account_class": "Bank"}
    fee_acc = accts.find(BANK_CHARGE_ACCOUNT, classes={"Expense", "Other expense"})
    int_acc = accts.find(INTEREST_INCOME_ACCOUNT, classes={"Income", "Other income"})
    entries, review = [], []
    for s in rec["statement_only"]:
        text = f" {norm(s['description'])} "
        amt = abs(s["amount"])
        evidence = [{"date": s["date"], "entry": f"statement row {s['row']}", "description": s["description"],
                     "debit": 0.0, "credit": 0.0, "amount": s["amount"]}]
        if s["amount"] < 0 and any(w in text for w in FEE_WORDS):
            acc = fee_acc or {"code": None, "name": "Bank charges", "class": "Expense"}
            entries.append({"key": f"bank_fee:{s['date']}:{s['amount']}:{norm(s['description'])[:40]}",
                            "kind": "bank_fee", "amount": r2(amt), "date": s["date"], "description": s["description"],
                            "title": f"Bank charge {s['date']}: {s['description'] or 'no description'}",
                            "reason": f"On the bank statement ({s['date']}, {s['amount']:,.2f}) and not in the ledger.",
                            "method": "The statement line, charged to bank charges.",
                            "lines": [{"account_code": acc["code"], "account": acc["name"], "account_class": acc["class"],
                                       "debit": r2(amt), "credit": 0.0}, {**bank_line, "debit": 0.0, "credit": r2(amt)}],
                            "warning": None if fee_acc else "“Bank charges” is not in this ledger's chart of accounts; "
                                                            "choose the account you use before importing the journal.",
                            "evidence": evidence})
        elif s["amount"] > 0 and any(w in text for w in INTEREST_WORDS):
            acc = int_acc or {"code": None, "name": "Interest income", "class": "Other income"}
            entries.append({"key": f"bank_interest:{s['date']}:{s['amount']}:{norm(s['description'])[:40]}",
                            "kind": "bank_interest", "amount": r2(amt), "date": s["date"], "description": s["description"],
                            "title": f"Interest received {s['date']}: {s['description'] or 'no description'}",
                            "reason": f"On the bank statement ({s['date']}, {s['amount']:,.2f}) and not in the ledger.",
                            "method": "The statement line, credited to interest income.",
                            "lines": [{**bank_line, "debit": r2(amt), "credit": 0.0},
                                      {"account_code": acc["code"], "account": acc["name"], "account_class": acc["class"],
                                       "debit": 0.0, "credit": r2(amt)}],
                            "warning": None if int_acc else "“Interest income” is not in this ledger's chart of "
                                                            "accounts; choose the account you use before importing.",
                            "evidence": evidence})
        else:
            review.append({"key": f"bank_unknown:{s['date']}:{s['amount']}:{norm(s['description'])[:40]}",
                           "kind": "bank_unknown", "amount": s["amount"], "date": s["date"], "description": s["description"],
                           "title": f"On the statement, not in the ledger: {s['date']} {s['description'] or ''} "
                                    f"{s['amount']:,.2f}".replace("  ", " "),
                           "reason": ("Money in that nobody recorded: find the invoice it pays, or record it."
                                      if s["amount"] > 0 else
                                      "Money out that nobody recorded: find the bill or the transfer, or record it."),
                           "evidence": evidence})
    return entries, review
