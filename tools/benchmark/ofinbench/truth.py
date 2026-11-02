"""The ground truth a case carries, and the accounting that has to hold before it can be used to grade anything.

A document alone cannot score a model: something has to say what the right answer is. That is this file. The
validation is deliberately arithmetic rather than schematic -- a truth file whose fields are all present but whose
opening balance plus credits does not reach its closing balance will mark correct answers wrong, and a benchmark
with a wrong key is worse than no benchmark. So every case is checked the way a reviewer would check a working
paper: the statement must foot, the trial balance must balance, the reported figures must follow from the roles the
accountant assigned, and every date must sit inside the period it claims.

Sign convention, stated once because every later number depends on it: in `lines`, `debit` is money leaving the
account and `credit` is money arriving, both written positive. In `trial_balance`, `debit` and `credit` are the
account's own balance columns, so a revenue account carries its figure in `credit`.
"""

from __future__ import annotations

import datetime
import json
import pathlib

ROLES = ("revenue", "cogs", "opex", "cash", "ar", "ap", "equity", "other")
LANGUAGES = ("ar", "en", "mixed")
FORMATS = ("pdf-text", "pdf-scan", "xlsx", "csv", "image")
TOLERANCE = 0.02  # two halalas, so a rounded cent in the source is not reported as a break


class TruthError(Exception):
    """The truth file is missing a field, or its figures do not agree with each other."""


def load(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TruthError(f"{path}: not valid JSON ({exc})") from exc


def _num(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TruthError(f"{where}: expected a number, found {value!r}")
    return float(value)


def _date(value, where: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError as exc:
        raise TruthError(f"{where}: expected a date as YYYY-MM-DD, found {value!r}") from exc


def _require(obj: dict, key: str, where: str):
    if key not in obj:
        raise TruthError(f"{where}: missing {key!r}")
    return obj[key]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOLERANCE, abs(b) * 1e-6)


def _meta(truth: dict, problems: list[str]) -> None:
    meta = truth.get("meta") or {}
    if meta.get("language") not in LANGUAGES:
        problems.append(f"meta.language must be one of {', '.join(LANGUAGES)}")
    if meta.get("source_format") not in FORMATS:
        problems.append(f"meta.source_format must be one of {', '.join(FORMATS)}")


def validate_bank_statement(truth: dict) -> list[str]:
    """Every way the statement fails to foot, as a list of plain sentences. Empty means the case is usable."""
    problems: list[str] = []
    _meta(truth, problems)
    try:
        period = _require(truth, "period", "truth")
        start, end = _date(_require(period, "start", "period"), "period.start"), _date(_require(period, "end", "period"), "period.end")
        if end < start:
            problems.append("period.end is before period.start")
        balances = _require(truth, "balances", "truth")
        opening = _num(_require(balances, "opening", "balances"), "balances.opening")
        closing = _num(_require(balances, "closing", "balances"), "balances.closing")
        totals = _require(truth, "totals", "truth")
        credits = _num(_require(totals, "credits", "totals"), "totals.credits")
        debits = _num(_require(totals, "debits", "totals"), "totals.debits")
        count = _require(totals, "transaction_count", "totals")
        lines = _require(truth, "lines", "truth")
        if not isinstance(lines, list) or not lines:
            raise TruthError("truth.lines must be a non-empty list")
        if not (truth.get("account") or {}).get("currency"):
            problems.append("account.currency is required: a figure without its currency cannot be graded")
    except TruthError as exc:
        return problems + [str(exc)]

    if not _close(opening + credits - debits, closing):
        problems.append(
            f"the statement does not foot: opening {opening:,.2f} + credits {credits:,.2f} "
            f"- debits {debits:,.2f} = {opening + credits - debits:,.2f}, but closing says {closing:,.2f}"
        )
    if count != len(lines):
        problems.append(f"totals.transaction_count says {count} but there are {len(lines)} lines")

    line_credits = line_debits = 0.0
    running = opening
    for i, line in enumerate(lines, 1):
        where = f"lines[{i}]"
        try:
            when = _date(_require(line, "date", where), f"{where}.date")
            debit = _num(line.get("debit", 0) or 0, f"{where}.debit")
            credit = _num(line.get("credit", 0) or 0, f"{where}.credit")
        except TruthError as exc:
            problems.append(str(exc))
            continue
        if not (start <= when <= end):
            problems.append(f"{where}.date {when} is outside the period {start}..{end}")
        if debit < 0 or credit < 0:
            problems.append(f"{where}: debit and credit are both written positive; found debit {debit}, credit {credit}")
        if debit and credit:
            problems.append(f"{where}: a line carries either a debit or a credit, not both")
        if not _require(line, "description", where):
            problems.append(f"{where}.description is empty")
        line_credits += credit
        line_debits += debit
        running += credit - debit
        if "balance" in line and line["balance"] is not None:
            stated = _num(line["balance"], f"{where}.balance")
            if not _close(running, stated):
                problems.append(f"{where}.balance says {stated:,.2f} but the running balance is {running:,.2f}")

    if not _close(line_credits, credits):
        problems.append(f"lines add up to {line_credits:,.2f} in credits but totals.credits says {credits:,.2f}")
    if not _close(line_debits, debits):
        problems.append(f"lines add up to {line_debits:,.2f} in debits but totals.debits says {debits:,.2f}")
    return problems


def role_totals(trial_balance: list[dict]) -> dict[str, float]:
    """Each role's figure, signed the way the statements read it: revenue positive, cost positive, cash positive."""
    out = {role: 0.0 for role in ROLES}
    for row in trial_balance:
        role = row.get("role", "other")
        debit = float(row.get("debit", 0) or 0)
        credit = float(row.get("credit", 0) or 0)
        out[role] = out.get(role, 0.0) + (credit - debit if role in {"revenue", "ap", "equity"} else debit - credit)
    return out


def validate_monthly_close(truth: dict) -> list[str]:
    """Every way the close contradicts itself. Empty means the case is usable."""
    problems: list[str] = []
    _meta(truth, problems)
    try:
        period = _require(truth, "period", "truth")
        start, end = _date(_require(period, "start", "period"), "period.start"), _date(_require(period, "end", "period"), "period.end")
        if end < start:
            problems.append("period.end is before period.start")
        trial = _require(truth, "trial_balance", "truth")
        reported = _require(truth, "reported", "truth")
        if not isinstance(trial, list) or not trial:
            raise TruthError("truth.trial_balance must be a non-empty list")
        if not truth.get("currency"):
            problems.append("currency is required")
    except TruthError as exc:
        return problems + [str(exc)]

    debit_sum = credit_sum = 0.0
    accounts: set[str] = set()
    for i, row in enumerate(trial, 1):
        where = f"trial_balance[{i}]"
        code = str(row.get("account", "")).strip()
        if not code:
            problems.append(f"{where}.account is empty")
        if code in accounts:
            problems.append(f"{where}: account {code} appears twice")
        accounts.add(code)
        if row.get("role") not in ROLES:
            problems.append(f"{where}.role must be one of {', '.join(ROLES)}; found {row.get('role')!r}")
        try:
            debit_sum += _num(row.get("debit", 0) or 0, f"{where}.debit")
            credit_sum += _num(row.get("credit", 0) or 0, f"{where}.credit")
        except TruthError as exc:
            problems.append(str(exc))

    if not _close(debit_sum, credit_sum):
        problems.append(f"the trial balance does not balance: debits {debit_sum:,.2f} against credits {credit_sum:,.2f}")

    roles = role_totals(trial)
    checks = (
        ("revenue", roles["revenue"]),
        ("cogs", roles["cogs"]),
        ("opex", roles["opex"]),
        ("cash", roles["cash"]),
        ("gross_profit", roles["revenue"] - roles["cogs"]),
        ("net_profit", roles["revenue"] - roles["cogs"] - roles["opex"]),
    )
    for field, derived in checks:
        if field in reported:
            try:
                stated = _num(reported[field], f"reported.{field}")
            except TruthError as exc:
                problems.append(str(exc))
                continue
            if not _close(derived, stated):
                problems.append(
                    f"reported.{field} says {stated:,.2f} but the trial balance roles give {derived:,.2f}; "
                    "either a role is assigned wrongly or the reported figure includes something outside the roles"
                )

    for i, row in enumerate(truth.get("budget") or [], 1):
        code = str(row.get("account", "")).strip()
        if code and code not in accounts:
            problems.append(f"budget[{i}]: account {code} is not in the trial balance")

    for i, row in enumerate(truth.get("receivables") or [], 1):
        where = f"receivables[{i}]"
        if not row.get("customer"):
            problems.append(f"{where}.customer is empty")
        try:
            amount = _num(_require(row, "amount", where), f"{where}.amount")
            due = _date(_require(row, "due", where), f"{where}.due")
        except TruthError as exc:
            problems.append(str(exc))
            continue
        if amount <= 0:
            problems.append(f"{where}.amount must be positive; an unpaid invoice is a positive receivable")
        if due > end:
            problems.append(f"{where}.due {due} is after the period end {end}: it is not yet due and cannot be overdue")
    return problems


def validate(kind: str, truth: dict) -> list[str]:
    if kind == "bank-statement":
        return validate_bank_statement(truth)
    if kind == "monthly-close":
        return validate_monthly_close(truth)
    raise TruthError(f"unknown case kind {kind!r}")
