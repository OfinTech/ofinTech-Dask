"""Reference adapter for the OfinTech Desk Open Accounting Connector (pull mode).

It serves the five endpoints from a small in-memory ledger so you can see the format end to end. Replace the
functions in the "your system" section with queries against your accounting system or ERP (SQL, its API, an
export file) and keep the response shapes.

    ADAPTER_KEY=change-me python adapter.py           # listens on 127.0.0.1:8787
    curl -H "Authorization: Bearer change-me" "http://127.0.0.1:8787/v1/profit-and-loss?start=2026-01-01&end=2026-03-31"

Standard library only (Python 3.10+). Put it behind HTTPS (a reverse proxy) before OfinTech Desk connects to it
over the internet; the Team Server refuses plain-HTTP and private addresses for connectors.
"""

from __future__ import annotations

import hmac
import json
import os
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

PAGE_SIZE = 500

# --- your system: replace these with real queries ------------------------------------------------------------

LEDGER = [  # (date, account, section, amount) — posted entries summarised per month
    ("2026-01-31", "4000 Sales", "Income", 51000.0), ("2026-02-28", "4000 Sales", "Income", 52000.0),
    ("2026-03-31", "4000 Sales", "Income", 53000.0), ("2026-01-31", "5000 Cost of goods sold", "Cost of sales", 20400.0),
    ("2026-02-28", "5000 Cost of goods sold", "Cost of sales", 20800.0),
    ("2026-03-31", "5000 Cost of goods sold", "Cost of sales", 21200.0), ("2026-01-31", "6100 Rent", "Expenses", 4500.0),
    ("2026-02-28", "6100 Rent", "Expenses", 4500.0), ("2026-03-31", "6100 Rent", "Expenses", 4500.0),
]
BALANCES = [("Assets", "Current assets", "1000 Bank", 88200.0), ("Assets", "Receivables", "1100 Accounts receivable", 30500.0),
            ("Liabilities", "Payables", "2000 Accounts payable", 12700.0), ("Equity", "Equity", "3000 Share capital", 50000.0),
            ("Equity", "Current year earnings", "Profit to date", 56000.0)]
INVOICES = [{"number": f"INV-{n:04d}", "party": party, "issue_date": issued, "due_date": due, "currency": "USD",
             "total": total, "balance": bal, "status": "Open" if bal else "Paid"}
            for n, (party, issued, due, total, bal) in enumerate([
                ("Northwind Retail", "2026-02-10", "2026-03-11", 12000.0, 12000.0),
                ("Contoso Hotels", "2026-03-02", "2026-04-01", 18500.0, 18500.0),
                ("Fabrikam Ltd", "2026-01-15", "2026-02-14", 9000.0, 0.0)], start=1)]
BILLS = [{"number": "BILL-0091", "party": "City Properties", "issue_date": "2026-03-01", "due_date": "2026-03-31",
          "currency": "USD", "total": 4500.0, "balance": 4500.0, "status": "Open"}]


def company() -> dict[str, Any]:
    return {"name": "Example Trading Co", "currency": "USD", "country": "US", "fiscal_year_start_month": 1}


def profit_and_loss(start: date, end: date) -> list[dict[str, Any]]:
    out: dict[tuple[str, str, str], float] = {}
    for d, account, section, amount in LEDGER:
        if start.isoformat() <= d <= end.isoformat():
            key = (d[:7] + "-01", section, account)
            out[key] = out.get(key, 0.0) + amount
    return [{"period": p, "section": s, "account": a, "amount": round(v, 2)} for (p, s, a), v in sorted(out.items())]


def balance_sheet(as_of: date) -> list[dict[str, Any]]:
    return [{"as_of": as_of.isoformat(), "section": s, "group": g, "account": a, "amount": v} for s, g, a, v in BALANCES]


def documents(kind: str, status: str) -> list[dict[str, Any]]:
    docs = INVOICES if kind == "invoices" else BILLS
    return [d for d in docs if status == "all" or d["balance"]]


# --- the protocol (keep as is) ----------------------------------------------------------------------------------

def handle(path: str, query: dict[str, str], authorization: str, key: str) -> tuple[int, Any]:
    """Pure request → response function, so it can be tested without a network."""
    given = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    if not key or not hmac.compare_digest(given.encode(), key.encode()):
        return 401, {"error": "unauthorized", "message": "Missing or wrong key."}
    try:
        if path == "/v1/company":
            return 200, company()
        if path == "/v1/profit-and-loss":
            return 200, {"lines": profit_and_loss(date.fromisoformat(query["start"]), date.fromisoformat(query["end"]))}
        if path == "/v1/balance-sheet":
            return 200, {"lines": balance_sheet(date.fromisoformat(query["as_of"]))}
        if path in ("/v1/invoices", "/v1/bills"):
            docs = documents(path.rsplit("/", 1)[1], query.get("status", "open"))
            start = int(query.get("cursor") or 0)
            page = docs[start:start + PAGE_SIZE]
            nxt = str(start + PAGE_SIZE) if start + PAGE_SIZE < len(docs) else None
            return 200, {"documents": page, "next_cursor": nxt}
    except (KeyError, ValueError) as exc:
        return 400, {"error": "bad_request", "message": f"Missing or invalid parameter: {exc}"}
    return 404, {"error": "not_found", "message": "Unknown path."}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        status, body = handle(parts.path, query, self.headers.get("Authorization", ""), os.environ.get("ADAPTER_KEY", ""))
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    if not os.environ.get("ADAPTER_KEY"):
        raise SystemExit("Set ADAPTER_KEY to a long random value first.")
    host, port = os.environ.get("ADAPTER_HOST", "127.0.0.1"), int(os.environ.get("ADAPTER_PORT", "8787"))
    print(f"Open Accounting Connector adapter on http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
