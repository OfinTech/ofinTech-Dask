"""Demo data for OfinTech Desk: Harbor Line Logistics Ltd (fictional), 1 Sep 2025 – 27 Oct 2026, in USD.

Writes Zoho Books import files (CSV, in import order) and the same transactions as an OfinTech Desk general ledger
(Excel), plus a project budget workbook. Deterministic (fixed seed)."""

from __future__ import annotations

import calendar
import csv
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "HarborLine-demo"
rnd = random.Random(20261028)
START, END = date(2025, 9, 1), date(2026, 10, 27)
BANK = "Operating Account"

# ---- chart of accounts (code, name, Zoho type, Desk class, description) ------------------------------------------------
ACCOUNTS = [
    ("1000", BANK, "Bank", "Bank", "Main business current account (create it under Banking)"),
    ("1100", "Accounts Receivable", "Accounts Receivable", "Receivable", "System account"),
    ("1500", "Vehicles", "Fixed Asset", "Asset", "Trucks and vans at cost"),
    ("1510", "Accumulated Depreciation - Vehicles", "Fixed Asset", "Asset", "Contra asset"),
    ("1600", "Warehouse Racking", "Fixed Asset", "Asset", "Racking installed at the Eastport warehouse"),
    ("1900", "Suspense", "Other Current Asset", "Asset", "Payments waiting to be allocated"),
    ("2000", "Accounts Payable", "Accounts Payable", "Payable", "System account"),
    ("2500", "Bank Loan - Long Term", "Long Term Liability", "Liability", "Five-year fleet loan"),
    ("3000", "Owner's Equity", "Equity", "Equity", "System account"),
    ("4000", "Freight Revenue - FTL", "Income", "Income", "Full-truckload trips"),
    ("4010", "Freight Revenue - LTL", "Income", "Income", "Less-than-truckload pallets"),
    ("4100", "Warehousing Revenue", "Income", "Income", "Pallet storage"),
    ("4200", "Handling & Brokerage Revenue", "Income", "Income", "Handling and customs brokerage fees"),
    ("5000", "Fuel", "Cost Of Goods Sold", "Cost of sales", "Diesel and fuel cards"),
    ("5100", "Subcontracted Haulage", "Cost Of Goods Sold", "Cost of sales", "Third-party carriers"),
    ("5200", "Fleet Maintenance", "Cost Of Goods Sold", "Cost of sales", "Repairs, tyres and servicing"),
    ("6000", "Staff Salaries", "Expense", "Expense", "Drivers, warehouse and office payroll"),
    ("6100", "Warehouse Rent", "Expense", "Expense", "Rent of the Harbor Road warehouse"),
    ("6200", "Utilities", "Expense", "Expense", "Power, water and telecoms"),
    ("6300", "Software Subscriptions", "Expense", "Expense", "Fleet tracking and office software"),
    ("6400", "Professional Fees", "Expense", "Expense", "Consultants, audit and legal"),
    ("6500", "Depreciation - Vehicles", "Expense", "Expense", "Monthly depreciation of the fleet"),
    ("7000", "Loan Interest", "Other Expense", "Other expense", "Interest on the fleet loan"),
]
ACC = {a[1]: a for a in ACCOUNTS}

CUSTOMERS = [  # name, terms (days), share of business, how late they pay (days after due, min, max), city, country
    ("Meridian Retail Group", 45, 0.30, (0, 12), "Chicago", "USA"),
    ("Alder & Finch Foods", 30, 0.14, (-5, 8), "Toronto", "Canada"),
    ("Coastal Pharma Supplies", 30, 0.12, (0, 6), "Rotterdam", "Netherlands"),
    ("Northgate Electronics", 30, 0.11, (5, 20), "Manchester", "United Kingdom"),
    ("Bluepeak Furniture", 30, 0.10, (0, 15), "Dubai", "United Arab Emirates"),
    ("Solstice Beverages", 30, 0.09, (-3, 10), "Madrid", "Spain"),
    ("Kestrel Auto Parts", 30, 0.08, (40, 70), "Monterrey", "Mexico"),
    ("Lumen Home Goods", 30, 0.06, (10, 25), "Lyon", "France"),
]
VENDORS = [  # name, terms, account, what they bill, city, country
    ("Atlas Fuel Cards", 15, "Fuel", "Fuel card statement", "Houston", "USA"),
    ("Swiftway Haulage", 30, "Subcontracted Haulage", "Subcontracted trips", "Newark", "USA"),
    ("Ironclad Fleet Services", 30, "Fleet Maintenance", "Fleet servicing and repairs", "Newark", "USA"),
    ("Harbor Road Properties", 0, "Warehouse Rent", "Warehouse rent", "Newark", "USA"),
    ("Brightspark Utilities", 20, "Utilities", "Power, water and telecoms", "Newark", "USA"),
    ("Cloudnine Software", 30, "Software Subscriptions", "Fleet tracking and office software", "Dublin", "Ireland"),
    ("Eastport Fitout Contractors", 30, "Professional Fees", "Eastport warehouse fit-out", "Newark", "USA"),
    ("Rackline Storage Systems", 45, "Warehouse Racking", "Pallet racking", "Hamburg", "Germany"),
    ("Kingsley & Rowe Advisors", 30, "Professional Fees", "Accounting and advisory", "Boston", "USA"),
]
ITEMS = [  # name, account, unit, base rate, description
    ("FTL Trip", "Freight Revenue - FTL", "trip", 1850.0, "Full-truckload trip"),
    ("LTL Pallet", "Freight Revenue - LTL", "pallet", 95.0, "Less-than-truckload, per pallet"),
    ("Pallet Storage", "Warehousing Revenue", "pallet-month", 18.0, "Pallet storage, per pallet per month"),
    ("Handling Fee", "Handling & Brokerage Revenue", "job", 140.0, "Inbound and outbound handling"),
    ("Customs Brokerage", "Handling & Brokerage Revenue", "entry", 220.0, "Customs entry and clearance"),
]
SEASON = {11: 1.10, 12: 1.22, 1: 0.86, 2: 0.88, 3: 0.95, 4: 0.98, 5: 1.00, 6: 1.02, 7: 1.00, 8: 1.03, 9: 1.07, 10: 1.12}


def months() -> list[date]:
    out, d = [], START
    while d <= END:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def last_day(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def biz(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


GL: list[dict] = []          # OfinTech Desk general-ledger lines
INVOICES: list[dict] = []    # Zoho invoice rows (one per line)
BILLS: list[dict] = []
CPAY: list[dict] = []
VPAY: list[dict] = []
JOURNALS: list[dict] = []
DOCS: dict[str, dict] = {}   # number -> {total, paid, due, party, kind}


def gl(day: date, entry: str, typ: str, account: str, dr: float, cr: float, desc: str, party: str | None) -> None:
    code, name, _, cls, _ = ACC[account]
    GL.append({"Date": day, "Entry": entry, "Type": typ, "Account code": code, "Account": name, "Account class": cls,
               "Debit": r2(dr), "Credit": r2(cr), "Description": desc, "Party": party, "Cost centre tags": None})


def journal(day: date, ref: str, notes: str, legs: list[tuple[str, float, float, str, str | None]]) -> None:
    assert abs(sum(d for _, d, _, _, _ in legs) - sum(c for _, _, c, _, _ in legs)) < 0.005, ref
    for account, dr, cr, desc, party in legs:
        JOURNALS.append({"Journal Date": day.isoformat(), "Journal Number": ref, "Reference Number": ref,
                         "Notes": notes, "Status": "Published", "Currency": "USD", "Account": account,
                         "Description": desc, "Contact Name": party or "", "Debit": f"{dr:.2f}" if dr else "",
                         "Credit": f"{cr:.2f}" if cr else ""})
        gl(day, ref, "Journal", account, dr, cr, desc, party)


# ---- opening position on 1 Nov 2025 ---------------------------------------------------------------------------------
journal(START, "JV-OPEN", "Opening balances", [
    (BANK, 520000, 0, "Opening bank balance", None),
    ("Vehicles", 420000, 0, "Fleet at cost: 6 trucks, 4 vans", None),
    ("Accumulated Depreciation - Vehicles", 0, 84000, "Depreciation to 31 Aug 2025", None),
    ("Bank Loan - Long Term", 0, 240000, "Fleet loan outstanding", None),
    ("Owner's Equity", 0, 616000, "Owner's capital and retained earnings", None),
])

# ---- sales invoices -------------------------------------------------------------------------------------------------
inv_no = 1000
for mi, m in enumerate(months()):
    growth = 1 + 0.012 * mi
    target = 168000 * SEASON[m.month] * growth  # revenue for the month
    for cname, terms, share, late, *_ in CUSTOMERS:
        budget = target * share
        n_inv = 2 if share >= 0.1 else 1
        for k in range(n_inv):
            inv_no += 1
            number = f"INV-{inv_no}"
            day = biz(m + timedelta(days=rnd.randint(2, 12) + k * 13))
            if day > END:
                continue
            due = day + timedelta(days=terms)
            part = budget / n_inv
            lines = []
            trips = max(1, round(part * 0.55 / 1850 * rnd.uniform(0.85, 1.15)))
            lines.append(("FTL Trip", trips, round(1850 * rnd.uniform(0.97, 1.05), 2)))
            pallets = max(4, round(part * 0.2 / 95 * rnd.uniform(0.8, 1.2)))
            lines.append(("LTL Pallet", pallets, 95.0))
            if cname in ("Meridian Retail Group", "Bluepeak Furniture", "Lumen Home Goods", "Northgate Electronics"):
                stored = max(20, round(part * 0.18 / 18 * rnd.uniform(0.9, 1.1)))
                lines.append(("Pallet Storage", stored, 18.0))
            lines.append(("Handling Fee", rnd.randint(2, 6), 140.0))
            if cname in ("Coastal Pharma Supplies", "Northgate Electronics", "Bluepeak Furniture", "Kestrel Auto Parts"):
                lines.append(("Customs Brokerage", rnd.randint(1, 4), 220.0))
            total = 0.0
            for item, qty, rate in lines:
                it = next(i for i in ITEMS if i[0] == item)
                amount = r2(qty * rate)
                total += amount
                INVOICES.append({"Invoice Date": day.isoformat(), "Invoice Number": number, "Invoice Status": "Sent",
                                 "Customer Name": cname, "Due Date": due.isoformat(), "Payment Terms": terms,
                                 "Payment Terms Label": f"Net {terms}", "Currency Code": "USD", "Item Name": item,
                                 "Item Desc": f"{it[4]} - {m.strftime('%b %Y')}", "Quantity": qty, "Item Price": f"{rate:.2f}",
                                 "Account": it[1], "Usage unit": it[2], "Notes": "Thank you for your business."})
            total = r2(total)
            gl(day, number, "Invoice", "Accounts Receivable", total, 0, f"Invoice {number}", cname)
            by_acc: dict[str, float] = defaultdict(float)
            for item, qty, rate in lines:
                by_acc[next(i for i in ITEMS if i[0] == item)[1]] += r2(qty * rate)
            for acc, amt in by_acc.items():
                gl(day, number, "Invoice", acc, 0, amt, f"Invoice {number}", cname)
            DOCS[number] = {"kind": "invoice", "party": cname, "date": day, "due": due, "total": total, "paid": 0.0}
            # customer payment
            if cname == "Lumen Home Goods" and m.month == 4:
                continue  # disputed invoice, never paid
            pay_day = biz(due + timedelta(days=rnd.randint(*late)))
            if pay_day > END:
                continue
            paid = total
            if cname == "Kestrel Auto Parts" and rnd.random() < 0.35:
                paid = r2(total * 0.5)  # pays half first
            CPAY.append({"Date": pay_day.isoformat(), "Payment Number": f"PMT-{len(CPAY) + 1:04d}", "Customer Name": cname,
                         "Invoice Number": number, "Amount": f"{paid:.2f}", "Amount Applied to Invoice": f"{paid:.2f}",
                         "Payment Mode": "Bank Transfer", "Deposit To": BANK, "Reference Number": f"BT{rnd.randint(100000, 999999)}",
                         "Currency Code": "USD"})
            gl(pay_day, f"PMT-{len(CPAY):04d}", "Customer payment", BANK, paid, 0, f"Payment for {number}", cname)
            gl(pay_day, f"PMT-{len(CPAY):04d}", "Customer payment", "Accounts Receivable", 0, paid, f"Payment for {number}", cname)
            DOCS[number]["paid"] += paid

# ---- supplier bills -------------------------------------------------------------------------------------------------
bill_no = 0


def bill(vendor: str, day: date, lines: list[tuple[str, float, str]], *, pay_after: int | None = None,
         number: str | None = None) -> None:
    global bill_no
    v = next(x for x in VENDORS if x[0] == vendor)
    bill_no += 1
    number = number or f"{''.join(w[0] for w in vendor.split())[:3].upper()}-{day:%y%m}-{bill_no:03d}"
    due = day + timedelta(days=v[1])
    total = 0.0
    for account, amount, desc in lines:
        amount = r2(amount)
        total += amount
        BILLS.append({"Bill Date": day.isoformat(), "Bill Number": number, "Bill Status": "Open", "Vendor Name": vendor,
                      "Due Date": due.isoformat(), "Payment Terms": v[1], "Currency Code": "USD", "Account": account,
                      "Description": desc, "Quantity": 1, "Rate": f"{amount:.2f}", "Item Total": f"{amount:.2f}"})
        gl(day, number, "Bill", account, amount, 0, desc, vendor)
    total = r2(total)
    gl(day, number, "Bill", "Accounts Payable", 0, total, f"Bill {number}", vendor)
    DOCS[number] = {"kind": "bill", "party": vendor, "date": day, "due": due, "total": total, "paid": 0.0}
    delay = pay_after if pay_after is not None else v[1] + rnd.randint(-3, 6)
    pay_day = biz(day + timedelta(days=max(delay, 0)))
    if pay_day <= END:
        ref = f"VP-{len(VPAY) + 1:04d}"
        VPAY.append({"Date": pay_day.isoformat(), "Payment Number": ref, "Vendor Name": vendor, "Bill Number": number,
                     "Amount": f"{total:.2f}", "Amount Applied to Bill": f"{total:.2f}", "Payment Mode": "Bank Transfer",
                     "Paid Through": BANK, "Reference Number": ref, "Currency Code": "USD"})
        gl(pay_day, ref, "Vendor payment", "Accounts Payable", total, 0, f"Payment of {number}", vendor)
        gl(pay_day, ref, "Vendor payment", BANK, 0, total, f"Payment of {number}", vendor)
        DOCS[number]["paid"] = total


for mi, m in enumerate(months()):
    activity = SEASON[m.month] * (1 + 0.012 * mi)
    bill("Atlas Fuel Cards", biz(last_day(m) - timedelta(days=2)) if last_day(m) <= END else END,
         [("Fuel", 36500 * activity * rnd.uniform(0.95, 1.08), f"Fuel card statement {m:%b %Y}")])
    bill("Swiftway Haulage", biz(m + timedelta(days=20)),
         [("Subcontracted Haulage", 21000 * activity * rnd.uniform(0.8, 1.25), f"Subcontracted trips {m:%b %Y}")])
    bill("Ironclad Fleet Services", biz(m + timedelta(days=rnd.randint(5, 22))),
         [("Fleet Maintenance", rnd.uniform(4800, 9500), "Fleet servicing and repairs")])
    bill("Harbor Road Properties", m, [("Warehouse Rent", 14500, f"Warehouse rent {m:%b %Y}")], pay_after=2)
    bill("Brightspark Utilities", biz(m + timedelta(days=9)),
         [("Utilities", 2100 * rnd.uniform(0.9, 1.2) * (1.15 if m.month in (12, 1, 2, 7, 8) else 1), "Power, water and telecoms")])
    bill("Cloudnine Software", m, [("Software Subscriptions", 1450 if mi < 6 else 1690, f"Fleet tracking and office software {m:%b %Y}")])
    if m.month in (12, 3, 6, 9):
        bill("Kingsley & Rowe Advisors", biz(m + timedelta(days=14)), [("Professional Fees", 4200, "Quarterly accounting and advisory")])

# Eastport warehouse fit-out project (EWF): budget 160,000, runs Feb–Dec 2026
for day, vendor, lines in [
    (date(2026, 2, 16), "Eastport Fitout Contractors", [("Professional Fees", 6800, "EWF - design and permits")]),
    (date(2026, 3, 18), "Eastport Fitout Contractors", [("Professional Fees", 9400, "EWF - site preparation")]),
    (date(2026, 4, 20), "Rackline Storage Systems", [("Warehouse Racking", 38500, "EWF - pallet racking, phase 1")]),
    (date(2026, 5, 19), "Eastport Fitout Contractors", [("Professional Fees", 11200, "EWF - electrical and lighting")]),
    (date(2026, 6, 22), "Rackline Storage Systems", [("Warehouse Racking", 29800, "EWF - pallet racking, phase 2")]),
    (date(2026, 7, 21), "Eastport Fitout Contractors", [("Professional Fees", 8600, "EWF - fire safety works")]),
    (date(2026, 8, 18), "Eastport Fitout Contractors", [("Professional Fees", 7300, "Dock levellers installation")]),  # no project code
    (date(2026, 9, 22), "Rackline Storage Systems", [("Warehouse Racking", 22600, "EWF - mezzanine racking")]),
    (date(2026, 10, 20), "Eastport Fitout Contractors", [("Professional Fees", 19900, "EWF - floor marking, signage and dock office")]),
]:
    bill(vendor, biz(day), lines)

# ---- monthly journals: payroll, depreciation, loan ------------------------------------------------------------------
loan = 240000.0
for mi, m in enumerate(months()):
    end = min(last_day(m), END)
    payroll = 52000 * (1 + 0.004 * mi) + (6000 if m.month == 12 else 0)
    journal(biz(end - timedelta(days=3)), f"JV-PAY-{m:%y%m}", f"Payroll {m:%B %Y}", [
        ("Staff Salaries", r2(payroll), 0, f"Payroll {m:%B %Y}", None),
        (BANK, 0, r2(payroll), f"Payroll {m:%B %Y}", None)])
    if last_day(m) <= END:
        journal(last_day(m), f"JV-DEP-{m:%y%m}", f"Depreciation {m:%B %Y}", [
            ("Depreciation - Vehicles", 7000, 0, "Fleet depreciation (5 years straight line)", None),
            ("Accumulated Depreciation - Vehicles", 0, 7000, "Fleet depreciation", None)])
    interest = r2(loan * 0.065 / 12)
    journal(biz(m + timedelta(days=24)), f"JV-LOAN-{m:%y%m}", f"Fleet loan instalment {m:%B %Y}", [
        ("Bank Loan - Long Term", 5000, 0, "Loan principal", "First Harbor Bank"),
        ("Loan Interest", interest, 0, "Loan interest 6.5%", "First Harbor Bank"),
        (BANK, 0, r2(5000 + interest), "Loan instalment", "First Harbor Bank")])
    loan -= 5000

# a payment to the fit-out contractor that nobody allocated yet
journal(date(2026, 9, 29), "JV-SUSP-01", "Unallocated payment", [
    ("Suspense", 4800, 0, "Eastport Fitout Contractors advance", "Eastport Fitout Contractors"),
    (BANK, 0, 4800, "Eastport Fitout Contractors advance", "Eastport Fitout Contractors")])
# a new van bought in June, paid from the bank
journal(date(2026, 6, 10), "JV-CAPEX-01", "New delivery van", [
    ("Vehicles", 48000, 0, "Delivery van, VIN ending 4471", "Metro Commercial Vehicles"),
    (BANK, 0, 48000, "Delivery van purchase", "Metro Commercial Vehicles")])
# from July the new van adds depreciation
for m in months():
    if date(2026, 7, 1) <= m and last_day(m) <= END:
        journal(last_day(m), f"JV-DEP2-{m:%y%m}", "Depreciation - new van", [
            ("Depreciation - Vehicles", 800, 0, "New van depreciation (5 years)", None),
            ("Accumulated Depreciation - Vehicles", 0, 800, "New van depreciation", None)])


# ---- write files ---------------------------------------------------------------------------------------------------
def write_csv(name: str, rows: list[dict]) -> None:
    path = OUT / "zoho-books" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_statement(month: str = "2026-10") -> None:
    """The bank's statement for the operating account for one month, for the month-end close: most lines clear on the
    same day or a day later, the last payment has not cleared yet, and the bank adds a fee and a customer transfer
    that nobody recorded."""
    from datetime import timedelta

    def day(x: dict) -> date:
        d = x["Date"]
        return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])

    bank = [x for x in GL if x["Account"] == "Operating Account"]
    opening = round(sum((x["Debit"] or 0) - (x["Credit"] or 0) for x in bank if day(x).isoformat()[:7] < month), 2)
    rows = sorted((x for x in bank if day(x).isoformat()[:7] == month), key=lambda x: (day(x), x["Entry"]))
    outstanding = max((x for x in rows if (x["Credit"] or 0) > 0), key=lambda x: (day(x), x["Entry"]))
    lines = []
    for i, x in enumerate(rows):
        if x is outstanding:
            continue
        amount = round((x["Debit"] or 0) - (x["Credit"] or 0), 2)
        cleared = day(x) + timedelta(days=1 if amount < 0 and i % 3 == 1 else 0)
        lines.append((cleared, (x["Party"] or x["Description"] or "").upper()[:60], amount))
    lines.append((date(2026, 10, 29), f"TRANSFER FROM {CUSTOMERS[0][0].upper()} REF REMITTANCE", 3150.00))
    lines.append((date(2026, 10, 31), "MONTHLY ACCOUNT MAINTENANCE FEE", -45.00))
    lines.sort(key=lambda x: x[0])
    path = OUT / "ofintech-desk" / f"HarborLine_Bank_Statement_{month}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Description", "Money out", "Money in", "Balance"])
        w.writerow([f"{month}-01", "Opening balance", "", "", f"{opening:.2f}"])
        bal = opening
        for d, text, amount in lines:
            bal = round(bal + amount, 2)
            w.writerow([d.isoformat(), text, f"{-amount:.2f}" if amount < 0 else "", f"{amount:.2f}" if amount > 0 else "",
                        f"{bal:.2f}"])


def main() -> dict:
    write_csv("01_chart_of_accounts.csv", [
        {"Account Name": n, "Account Code": c, "Account Type": t, "Description": d, "Currency": "USD"}
        for c, n, t, _, d in ACCOUNTS if t not in ("Bank", "Accounts Receivable", "Accounts Payable") and n != "Owner's Equity"])
    write_csv("02_customers.csv", [
        {"Display Name": n, "Company Name": n, "Contact Type": "customer", "Currency Code": "USD",
         "Payment Terms": t, "Payment Terms Label": f"Net {t}", "Billing City": city, "Billing Country": country,
         "EmailID": f"accounts@{n.lower().replace(' & ', '-').replace(' ', '')[:18]}.example"}
        for n, t, _, _, city, country in CUSTOMERS])
    write_csv("03_vendors.csv", [
        {"Display Name": n, "Company Name": n, "Contact Type": "vendor", "Currency Code": "USD", "Payment Terms": t,
         "Payment Terms Label": "Due on receipt" if t == 0 else f"Net {t}", "Billing City": city, "Billing Country": country,
         "EmailID": f"billing@{n.lower().replace(' & ', '-').replace(' ', '')[:18]}.example"}
        for n, t, _, _, city, country in VENDORS] + [
        {"Display Name": n, "Company Name": n, "Contact Type": "vendor", "Currency Code": "USD", "Payment Terms": 0,
         "Payment Terms Label": "Due on receipt", "Billing City": "Newark", "Billing Country": "USA",
         "EmailID": f"info@{n.lower().replace(' ', '')[:18]}.example"} for n in ("First Harbor Bank", "Metro Commercial Vehicles")])
    write_csv("04_items.csv", [
        {"Item Name": n, "Item Type": "Service", "Rate": f"{r:.2f}", "Account": a, "Usage unit": u, "Description": d}
        for n, a, u, r, d in ITEMS])
    write_csv("05_invoices.csv", INVOICES)
    write_csv("06_customer_payments.csv", CPAY)
    write_csv("07_bills.csv", BILLS)
    write_csv("08_vendor_payments.csv", VPAY)
    write_csv("09_manual_journals.csv", JOURNALS)

    from openpyxl import Workbook
    from openpyxl.styles import Font
    GL.sort(key=lambda x: (x["Date"], x["Entry"]))
    wb = Workbook()
    ws = wb.active
    ws.title = "General ledger"
    cols = list(GL[0])
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    for x in GL:
        ws.append([x[c] for c in cols])
    for col, w in zip("ABCDEFGHIJK", (12, 16, 18, 13, 34, 14, 13, 13, 44, 30, 18), strict=True):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    (OUT / "ofintech-desk").mkdir(parents=True, exist_ok=True)
    wb.save(OUT / "ofintech-desk" / "HarborLine_General_Ledger.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws["A1"] = "Eastport Warehouse Fit-out (EWF) - approved budget"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = "Approved by the board on 2 February 2026. Amounts in USD."
    ws.append([])
    ws.append(["Code", "Item", "Budget (USD)", "Notes"])
    for c in ws[4]:
        c.font = Font(bold=True)
    rows = [("6400", "Design, permits and contractors", 58000, "Eastport Fitout Contractors"),
            ("1600", "Pallet racking", 86000, "Rackline Storage Systems"),
            (None, "Contingency", 16000, "Not to be used without CFO approval")]
    for r in rows:
        ws.append(list(r))
    ws.append([None, "Total", 160000, None])
    ws.cell(row=ws.max_row, column=2).font = Font(bold=True)
    for col, w in zip("ABCD", (8, 34, 15, 40), strict=True):
        ws.column_dimensions[col].width = w
    wb.save(OUT / "ofintech-desk" / "EWF_Project_Budget.xlsx")

    write_statement()

    dr = sum(x["Debit"] for x in GL)
    cr = sum(x["Credit"] for x in GL)
    return {"gl_lines": len(GL), "debits": round(dr, 2), "credits": round(cr, 2),
            "invoices": len(DOCS) and sum(1 for d in DOCS.values() if d["kind"] == "invoice"),
            "bills": sum(1 for d in DOCS.values() if d["kind"] == "bill"), "journals": len({j["Journal Number"] for j in JOURNALS}),
            "customer_payments": len(CPAY), "vendor_payments": len(VPAY)}


if __name__ == "__main__":
    print(main())
