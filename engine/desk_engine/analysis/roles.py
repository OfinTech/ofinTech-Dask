"""Account roles: what each ledger account is for, so ratios can be computed on any chart of accounts.

Every account gets a suggested role from its account class and words in its name (English, Arabic, Spanish, French).
People confirm or change the suggestion; only confirmed choices are stored. Nothing here is decided by a model.
"""

from __future__ import annotations

from typing import Any

from ..costcentres.rules import norm

BALANCE_ROLES = ["cash", "receivables", "inventory", "other_current_assets", "fixed_assets", "other_non_current_assets",
                 "payables", "other_current_liabilities", "short_term_debt", "long_term_debt",
                 "other_non_current_liabilities", "equity"]
PL_ROLES = ["revenue", "other_income", "cogs", "payroll", "opex", "depreciation", "interest", "tax", "other_expense"]
ROLES = BALANCE_ROLES + PL_ROLES
ASSET_ROLES = {"cash", "receivables", "inventory", "other_current_assets", "fixed_assets", "other_non_current_assets"}
LIABILITY_ROLES = {"payables", "other_current_liabilities", "short_term_debt", "long_term_debt",
                   "other_non_current_liabilities"}
INCOME_ROLES = {"revenue", "other_income"}

WORDS: dict[str, list[str]] = {
    "inventory": ["inventory", "inventories", "stock", "merchandise", "raw material", "finished goods", "work in progress",
                  "مخزون", "المخزون", "بضاعة", "مواد خام", "inventario", "existencias", "mercaderia", "stocks",
                  "marchandises", "matieres premieres"],
    "fixed_assets": ["equipment", "machinery", "vehicle", "vehicles", "building", "buildings", "land", "furniture",
                     "fixtures", "property", "plant", "leasehold", "fixed asset", "fixed assets", "accumulated depreciation",
                     "racking", "shelving", "fit-out", "fitout", "leasehold improvements", "trucks", "fleet", "رفوف", "تجهيزات",
                     "computer hardware", "معدات", "الات", "سيارات", "مركبات", "مباني", "اراضي", "اثاث", "اصول ثابتة",
                     "مجمع الاهلاك", "مجمع الاستهلاك", "مجمع اهلاك", "مجمع استهلاك", "maquinaria", "vehiculo", "vehiculos", "edificio", "terreno",
                     "mobiliario", "inmovilizado", "amortizacion acumulada", "materiel", "vehicule", "batiment",
                     "mobilier", "immobilisation", "immobilisations", "amortissements cumules"],
    "other_non_current_assets": ["goodwill", "intangible", "long-term investment", "long term investment",
                                 "شهرة", "الشهرة", "اصول غير ملموسة", "الاصول غير الملموسة", "استثمارات طويلة", "fondo de comercio", "intangible",
                                 "ecart d'acquisition", "incorporel"],
    "debt": ["loan", "loans", "borrowing", "borrowings", "overdraft", "credit facility", "mortgage", "notes payable",
             "bond", "bonds", "lease liability", "lease liabilities", "قرض", "قروض", "تمويل بنكي", "سحب على المكشوف",
             "مرابحة", "prestamo", "prestamos", "credito bancario", "descubierto", "hipoteca", "emprunt", "emprunts", "decouvert",
             "pret", "prets"],
    "long_term": ["long-term", "long term", "non-current", "non current", "طويل", "طويلة الاجل", "غير متداول",
                  "largo plazo", "no corriente", "long terme", "non courant"],
    "other_non_current_liabilities": ["deferred tax", "employee benefit", "end of service", "pension", "provision for",
                                      "مكافاة نهاية الخدمة", "ضريبة مؤجلة", "مخصص", "impuesto diferido", "provision",
                                      "impot differe", "indemnites de fin"],
    "payroll": ["salary", "salaries", "wage", "wages", "payroll", "staff cost", "staff costs", "employee", "employees",
                "personnel", "social security", "social insurance", "gosi", "pension contribution", "رواتب", "الرواتب", "اجور", "الاجور", "مرتبات",
                "تامينات اجتماعية", "التامينات الاجتماعية", "التامينات", "sueldos", "salarios", "nomina", "seguridad social", "salaires",
                "remuneration", "charges sociales", "personnel"],
    "depreciation": ["depreciation", "amortisation", "amortization", "اهلاك", "الاهلاك", "استهلاك", "الاستهلاك",
                     "depreciacion", "amortizacion", "amortissement", "amortissements", "dotation aux amortissements"],
    "interest": ["interest", "finance cost", "finance costs", "financing cost", "فوائد", "الفوائد", "تكاليف التمويل",
                 "اعباء تمويلية", "intereses", "gastos financieros", "interets", "charges financieres"],
    "tax": ["income tax", "corporate tax", "corporation tax", "tax on profit", "zakat", "ضريبة الدخل", "الزكاة", "زكاة",
            "impuesto sobre beneficios", "impuesto sobre sociedades", "impot sur les societes", "impot sur le resultat"],
}
_NORM = {k: [norm(w) for w in v] for k, v in WORDS.items()}


def _has(name: str, key: str) -> bool:
    n = f" {norm(name)} "
    return any(f" {w} " in n or (len(w) > 6 and w in n) for w in _NORM[key])


def account_key(code: str | None, name: str) -> str:
    return f"{code or ''}|{name}"


def suggest(account_class: str, name: str) -> str:
    """The suggested role for an account, from its class and words in its name."""
    c = account_class
    if c == "Bank":
        return "cash"
    if c == "Receivable":
        return "receivables"
    if c == "Payable":
        return "payables"
    if c == "Equity":
        return "equity"
    if c == "Income":
        return "revenue"
    if c == "Other income":
        return "other_income"
    if c == "Cost of sales":
        return "cogs"
    if c in ("Expense", "Other expense"):
        for role in ("depreciation", "interest", "tax", "payroll"):
            if _has(name, role):
                return role
        return "opex" if c == "Expense" else "other_expense"
    if c == "Asset":
        for role in ("inventory", "fixed_assets", "other_non_current_assets"):
            if _has(name, role):
                return role
        return "other_current_assets"
    if c == "Liability":
        if _has(name, "debt"):
            return "long_term_debt" if _has(name, "long_term") else "short_term_debt"
        if _has(name, "other_non_current_liabilities") or _has(name, "long_term"):
            return "other_non_current_liabilities"
        return "other_current_liabilities"
    return "other_current_assets"


def role_fits(role: str, account_class: str) -> bool:
    """A balance-sheet account cannot take a P&L role and the other way round (it would break the arithmetic)."""
    pl = account_class in {"Income", "Other income", "Cost of sales", "Expense", "Other expense"}
    return (role in PL_ROLES) == pl


def accounts_with_roles(lines: list[dict[str, Any]], chosen: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every account in the ledger with its suggested and chosen role."""
    seen: dict[str, dict[str, Any]] = {}
    for ln in lines:
        key = account_key(ln["account_code"], ln["account"])
        a = seen.get(key)
        if a is None:
            a = seen[key] = {"key": key, "code": ln["account_code"], "name": ln["account"],
                             "class": ln["account_class"], "lines": 0, "debit": 0.0, "credit": 0.0}
        a["lines"] += 1
        a["debit"] += ln["debit"]
        a["credit"] += ln["credit"]
    out = []
    for key, a in sorted(seen.items(), key=lambda kv: (kv[1]["code"] or "", kv[1]["name"])):
        s = suggest(a["class"], a["name"])
        c = chosen.get(key)
        role = c["role"] if c and role_fits(c["role"], a["class"]) else s
        out.append(a | {"suggested": s, "role": role, "confirmed": bool(c), "confirmed_by": (c or {}).get("by"),
                        "debit": round(a["debit"], 2), "credit": round(a["credit"], 2)})
    return out
