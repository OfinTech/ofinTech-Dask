"""Cost centres: storage, the general-ledger datasets they read, reports and budget import."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..connectors.accounting import GL_COLUMNS
from ..data.store import get_dataset, list_datasets, table_name
from ..db import dumps, loads, new_id, now
from ..errors import Invalid, NotFound
from .analysis import Analysis
from .budget import match_accounts, parse_docx, parse_xlsx
from .rules import Matcher, Rules, prepare

if TYPE_CHECKING:
    from ..app import Engine

GL_NAMES = [c["name"].lower() for c in GL_COLUMNS]
FIELDS = ["date", "entry", "type", "account_code", "account", "account_class", "debit", "credit", "description",
          "party", "tags"]
EDITABLE = ("name", "code", "description", "status", "dataset_id", "start_date", "end_date", "include_assets",
            "budget_amount", "burn_months")


def _s(v: Any) -> str | None:
    """Text from a cell: an imported spreadsheet stores codes such as 5200 as numbers."""
    if v is None or v == "":
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def is_ledger(ds: dict[str, Any]) -> bool:
    """A general-ledger dataset: synced from a connection, pushed, or imported from a file with the same columns."""
    names = [str(c["name"]).strip().lower() for c in ds["columns"]]
    return names[:len(GL_NAMES)] == GL_NAMES


def bad_rules(exc: ValidationError) -> Invalid:
    first = exc.errors(include_url=False)[:3]
    return Invalid("The rules are not valid: " + "; ".join(
        ".".join(str(x) for x in e["loc"]) + f": {e['msg']}" for e in first), code="bad_rules")


class CostCentres:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._cache: dict[tuple[Any, ...], tuple[dict[str, Any], Analysis]] = {}

    # -- ledgers ---------------------------------------------------------------------------------------------------
    def ledgers(self, ws_id: str) -> list[dict[str, Any]]:
        return [{"id": d["id"], "name": d["name"], "row_count": d["row_count"], "refreshed_at": d["refreshed_at"],
                 "source": d["source"]} for d in list_datasets(self.engine, ws_id) if is_ledger(d)]

    def _ledger(self, ws_id: str, dataset_id: str | None) -> dict[str, Any]:
        if not dataset_id:
            raise Invalid("Choose the general ledger this cost centre reads.", code="no_ledger")
        ds = get_dataset(self.engine, ws_id, dataset_id)
        if not is_ledger(ds):
            raise Invalid("That dataset is not a general ledger. Sync an accounting connection, push journal lines, "
                          "or import a file with the general-ledger columns (see docs/COST_CENTRES.md).",
                          code="not_a_ledger")
        return ds

    def lines(self, ws_id: str, dataset_id: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        ds = self._ledger(ws_id, dataset_id)
        keys = ", ".join(f"c{i}" for i in range(len(FIELDS)))
        rows = self.engine.db.conn.execute(f"SELECT _row, {keys} FROM {table_name(ds['id'])} ORDER BY _row").fetchall()
        out = []
        for r in rows:
            ln = dict(zip(FIELDS, r[1:], strict=False))
            ln["row"] = r[0]
            ln["date"] = str(ln["date"] or "")[:10]
            ln["entry"] = _s(ln["entry"]) or f"row {r[0]}"
            ln["account_code"] = _s(ln["account_code"])
            ln["account"] = _s(ln["account"]) or ""
            ln["account_class"] = _s(ln["account_class"]) or ""
            for k in ("type", "description", "party", "tags"):
                ln[k] = _s(ln[k])
            ln["debit"] = float(ln["debit"] or 0)
            ln["credit"] = float(ln["credit"] or 0)
            out.append(prepare(ln))
        return ds, out

    def ledger_values(self, ws_id: str, dataset_id: str) -> dict[str, Any]:
        """What the rule editor offers: accounts, tags and parties found in the ledger."""
        ds, lines = self.lines(ws_id, dataset_id)
        accounts: dict[tuple[str | None, str], str] = {}
        tags: Counter[str] = Counter()
        parties: Counter[str] = Counter()
        for ln in lines:
            accounts[(ln["account_code"], ln["account"])] = ln["account_class"]
            for part in str(ln.get("tags") or "").split("|"):
                part = part.strip()
                if part:
                    tags[part.rsplit(" (", 1)[0] if part.endswith("%)") else part] += 1
            if ln.get("party"):
                parties[str(ln["party"])] += 1
        dates = [ln["date"] for ln in lines if ln["date"]]
        return {"dataset": {"id": ds["id"], "name": ds["name"], "row_count": ds["row_count"]},
                "accounts": [{"code": c, "name": n, "class": k} for (c, n), k in
                             sorted(accounts.items(), key=lambda kv: (kv[0][0] or "", kv[0][1]))],
                "tags": [t for t, _ in tags.most_common(300)], "parties": [p for p, _ in parties.most_common(300)],
                "first_date": min(dates) if dates else None, "last_date": max(dates) if dates else None}

    def import_ledger_file(self, ws_id: str, user_id: str, name: str, data: bytes, *, dataset_id: str | None = None,
                           dataset_name: str | None = None) -> dict[str, Any]:
        """A general ledger uploaded as Excel or CSV becomes (or replaces) a ledger dataset."""
        from ..data.store import create_dataset, replace_rows
        from .templates import read_ledger_file

        parsed = read_ledger_file(data, name)
        columns = [dict(c) for c in GL_COLUMNS]
        if dataset_id:
            ds = self._ledger(ws_id, dataset_id)
            ds = replace_rows(self.engine, ws_id, ds["id"], columns, parsed["rows"])
            self.engine.db.execute("UPDATE datasets SET source_ref = ? WHERE id = ?",
                                   (dumps({"filename": name[:200], "sheet": parsed["sheet"], "kind": "general_ledger"}),
                                    ds["id"]))
            action = "dataset.replace"
        else:
            label = (dataset_name or f"{name.rsplit('.', 1)[0]} · General ledger").strip()[:120]
            ds = create_dataset(self.engine, ws_id, name=label, source="csv" if name.lower().endswith(".csv") else "excel",
                                columns=columns, rows=parsed["rows"],
                                source_ref={"filename": name[:200], "sheet": parsed["sheet"], "kind": "general_ledger"},
                                created_by=user_id)
            action = "dataset.import"
        self.engine.audit(ws_id, user_id, action, ds["id"], {"filename": name[:200], "rows": ds["row_count"],
                                                              "kind": "general_ledger"})
        parsed.pop("rows")
        return parsed | {"dataset": {"id": ds["id"], "name": ds["name"], "row_count": ds["row_count"]}}

    # -- cost centres ----------------------------------------------------------------------------------------------
    def _row(self, ws_id: str, cc_id: str) -> dict[str, Any]:
        row = self.engine.db.one("SELECT * FROM cost_centres WHERE workspace_id = ? AND (id = ? OR lower(name) = "
                                 "lower(?) OR lower(code) = lower(?))", (ws_id, cc_id, cc_id, cc_id))
        if row is None:
            raise NotFound("Cost centre not found.")
        return row

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["rules"] = loads(row["rules"], {})
        out["include_assets"] = bool(row["include_assets"])
        return out

    def budget_lines(self, cc_id: str) -> list[dict[str, Any]]:
        rows = self.engine.db.all("SELECT * FROM cost_centre_budget_lines WHERE cost_centre_id = ? ORDER BY position",
                                  (cc_id,))
        return [r | {"accounts": loads(r["accounts"], []), "source": loads(r["source"], {})} for r in rows]

    def list(self, ws_id: str) -> list[dict[str, Any]]:
        rows = self.engine.db.all("SELECT * FROM cost_centres WHERE workspace_id = ? ORDER BY status, lower(name)",
                                  (ws_id,))
        return [self._public(r) for r in rows]

    def get(self, ws_id: str, cc_id: str) -> dict[str, Any]:
        row = self._row(ws_id, cc_id)
        versions = self.engine.db.all(
            "SELECT v.version, v.rules, v.note, v.created_at, u.name AS changed_by_name FROM cost_centre_rule_versions v "
            "LEFT JOIN users u ON u.id = v.changed_by WHERE v.cost_centre_id = ? ORDER BY v.version DESC", (row["id"],))
        return self._public(row) | {
            "budget_lines": self.budget_lines(row["id"]),
            "versions": [v | {"rules": loads(v["rules"], {})} for v in versions]}

    def _check(self, ws_id: str, values: dict[str, Any]) -> None:
        if values.get("dataset_id"):
            self._ledger(ws_id, values["dataset_id"])
        s, e = values.get("start_date"), values.get("end_date")
        if s and e and s > e:
            raise Invalid("The start date is after the end date.")

    def create(self, ws_id: str, user_id: str, values: dict[str, Any]) -> dict[str, Any]:
        try:
            rules = Rules.model_validate(values.pop("rules"))
        except ValidationError as exc:
            raise bad_rules(exc) from exc
        self._check(ws_id, values)
        if self.engine.db.one("SELECT id FROM cost_centres WHERE workspace_id = ? AND lower(name) = lower(?)",
                              (ws_id, values["name"])):
            raise Invalid("A cost centre with that name already exists.", code="duplicate")
        cid, ts = new_id("cc_"), now()
        r = dumps(rules.model_dump(exclude_defaults=True))
        with self.engine.db.tx() as c:
            cols = {"id": cid, "workspace_id": ws_id, "rules": r, "rules_version": 1, "created_by": user_id,
                    "created_at": ts, "updated_at": ts, **{k: values.get(k) for k in EDITABLE if k in values}}
            cols["include_assets"] = int(bool(cols.get("include_assets")))
            c.execute(f"INSERT INTO cost_centres ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                      list(cols.values()))
            c.execute("INSERT INTO cost_centre_rule_versions (cost_centre_id, version, rules, note, changed_by, "
                      "created_at) VALUES (?,?,?,?,?,?)", (cid, 1, r, "created", user_id, ts))
        self.engine.audit(ws_id, user_id, "cost_centre.create", cid, {"name": values["name"]})
        return self.get(ws_id, cid)

    def update(self, ws_id: str, user_id: str, cc_id: str, values: dict[str, Any]) -> dict[str, Any]:
        row = self._row(ws_id, cc_id)
        note = values.pop("note", None)
        sets = {k: values[k] for k in EDITABLE if k in values}
        if "include_assets" in sets:
            sets["include_assets"] = int(bool(sets["include_assets"]))
        if "name" in sets and self.engine.db.one(
                "SELECT id FROM cost_centres WHERE workspace_id = ? AND lower(name) = lower(?) AND id != ?",
                (ws_id, sets["name"], row["id"])):
            raise Invalid("A cost centre with that name already exists.", code="duplicate")
        self._check(ws_id, {"dataset_id": sets.get("dataset_id"),
                            "start_date": sets.get("start_date", row["start_date"]),
                            "end_date": sets.get("end_date", row["end_date"])})
        ts = now()
        with self.engine.db.tx() as c:
            if values.get("rules") is not None:
                try:
                    rules = dumps(Rules.model_validate(values["rules"]).model_dump(exclude_defaults=True))
                except ValidationError as exc:
                    raise bad_rules(exc) from exc
                if rules != row["rules"]:
                    version = row["rules_version"] + 1
                    sets |= {"rules": rules, "rules_version": version}
                    c.execute("INSERT INTO cost_centre_rule_versions (cost_centre_id, version, rules, note, changed_by, "
                              "created_at) VALUES (?,?,?,?,?,?)", (row["id"], version, rules, note, user_id, ts))
            if sets:
                sets["updated_at"] = ts
                c.execute(f"UPDATE cost_centres SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                          [*sets.values(), row["id"]])
        self.engine.audit(ws_id, user_id, "cost_centre.update", row["id"], {"fields": sorted(sets)})
        return self.get(ws_id, row["id"])

    def delete(self, ws_id: str, user_id: str, cc_id: str) -> None:
        row = self._row(ws_id, cc_id)
        self.engine.db.execute("DELETE FROM cost_centres WHERE id = ?", (row["id"],))
        self.engine.audit(ws_id, user_id, "cost_centre.delete", row["id"], {"name": row["name"]})

    # -- analysis --------------------------------------------------------------------------------------------------
    def _analysis(self, ws_id: str, cc_id: str, as_of: str | None = None) -> tuple[dict[str, Any], Analysis]:
        row = self._row(ws_id, cc_id)
        cc = self._public(row)
        ds = self._ledger(ws_id, cc["dataset_id"])
        others_rows = [r for r in self.engine.db.all("SELECT id, name, rules, include_assets, dataset_id, updated_at "
                                                     "FROM cost_centres WHERE workspace_id = ? AND id != ?",
                                                     (ws_id, row["id"])) if r["dataset_id"] == cc["dataset_id"]]
        key = (row["id"], row["updated_at"], ds["refreshed_at"], as_of,
               tuple((r["id"], r["updated_at"]) for r in others_rows))
        if key in self._cache:
            return self._cache[key]
        _, lines = self.lines(ws_id, cc["dataset_id"])
        last = max((ln["date"] for ln in lines if ln["date"]), default=None)
        if as_of:
            try:
                when = date.fromisoformat(as_of)
            except ValueError as exc:
                raise Invalid("as_of must be a date such as 2026-09-30.") from exc
        else:
            when = min(date.today(), date.fromisoformat(last)) if last else date.today()
        others = [(r["name"], Matcher(loads(r["rules"], {})), bool(r["include_assets"])) for r in others_rows]
        a = Analysis(lines, cc, as_of=when, budget_lines=self.budget_lines(row["id"]), others=others)
        meta = {"cost_centre": cc, "ledger": {"id": ds["id"], "name": ds["name"], "refreshed_at": ds["refreshed_at"],
                                              "last_date": last, "lines": len(lines)}}
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = (meta, a)
        return meta, a

    async def report(self, ws_id: str, cc_id: str, as_of: str | None = None) -> dict[str, Any]:
        meta, a = await asyncio.to_thread(self._analysis, ws_id, cc_id, as_of)
        rep = await asyncio.to_thread(a.report)
        return meta | rep | {"rules": [c.describe() for c in a.matcher.rules.include],
                             "excludes": [c.describe() for c in a.matcher.rules.exclude],
                             "rules_version": meta["cost_centre"]["rules_version"]}

    async def summaries(self, ws_id: str) -> list[dict[str, Any]]:
        out = []
        for cc in self.list(ws_id):
            item = {k: cc[k] for k in ("id", "name", "code", "status", "start_date", "end_date", "dataset_id",
                                       "rules_version", "budget_amount")}
            try:
                rep = await self.report(ws_id, cc["id"])
                t, ro = rep["totals"], rep["runout"]
                item |= {"cost": t["cost"], "budget": t["budget"], "used_pct": t["used_pct"],
                         "untagged_share": t["untagged_share"], "cash_paid": rep["cash"]["cash_paid"],
                         "unpaid": rep["cash"]["unpaid"], "run_out_date": ro["run_out_date"],
                         "exceeded_on": ro["exceeded_on"], "runout_status": ro["status"], "as_of": rep["as_of"]}
            except (Invalid, NotFound) as exc:
                item["error"] = exc.message
            out.append(item)
        return out

    async def matched(self, ws_id: str, cc_id: str, kind: str = "matched", as_of: str | None = None,
                      offset: int = 0, limit: int = 200) -> dict[str, Any]:
        _, a = await asyncio.to_thread(self._analysis, ws_id, cc_id, as_of)
        if kind == "matched":
            rows = await asyncio.to_thread(a.matched_lines)
        elif kind == "possibly_missing":
            rows = (await asyncio.to_thread(a.possibly_missing, 2000))["lines"]
        elif kind == "unassigned_bank":
            rows = (await asyncio.to_thread(a.unassigned_bank, 2000))["lines"]
        else:
            raise Invalid("kind must be matched, possibly_missing or unassigned_bank.")
        return {"kind": kind, "total": len(rows), "lines": rows[offset:offset + min(limit, 1000)]}

    async def preview(self, ws_id: str, dataset_id: str, rules: dict[str, Any], *, include_assets: bool = False,
                      start_date: str | None = None) -> dict[str, Any]:
        """What a set of rules would pick up, before saving."""
        try:
            Rules.model_validate(rules)
        except ValidationError as exc:
            raise bad_rules(exc) from exc
        _, lines = await asyncio.to_thread(self.lines, ws_id, dataset_id)
        last = max((ln["date"] for ln in lines if ln["date"]), default=date.today().isoformat())
        cc = {"name": "", "rules": rules, "include_assets": include_assets, "start_date": start_date}
        a = await asyncio.to_thread(Analysis, lines, cc, as_of=date.fromisoformat(last))
        t = a.totals()
        return {"matched_lines": t["matched_lines"], "cost": t["cost"], "revenue": t["revenue"],
                "untagged_share": t["untagged_share"], "by_rule": t["by_rule"]}

    # -- budget ----------------------------------------------------------------------------------------------------
    async def import_budget(self, ws_id: str, user_id: str, cc_id: str, file_id: str,
                            sheet: str | None = None) -> dict[str, Any]:
        row = self._row(ws_id, cc_id)
        files = self.engine.files
        v = files._upload(ws_id, file_id)
        fmt = loads(v["model"], {}).get("format")
        data, _, _ = await files.download(ws_id, file_id, fmt, v["version"])
        parsed = await asyncio.to_thread(parse_xlsx, data, sheet) if fmt == "xlsx" else \
            await asyncio.to_thread(parse_docx, data)
        accounts: list[tuple[str | None, str]] = []
        if row["dataset_id"]:
            values = await asyncio.to_thread(self.ledger_values, ws_id, row["dataset_id"])
            accounts = [(a["code"], a["name"]) for a in values["accounts"]]
        lines = match_accounts(parsed["lines"], accounts)
        name = v["file"]["name"]
        stored = [{"label": ln["label"], "amount": ln["amount"], "accounts": ln["accounts"],
                   "source": {"file_id": file_id, "file_name": name, "version": v["version"], "cell": ln["cell"],
                              "code": ln.get("code"), "matched_by": ln.get("matched_by")}} for ln in lines]
        self._store_budget(row["id"], stored)
        self.engine.db.update("cost_centres", row["id"], {"budget_file_id": file_id, "budget_file_version": v["version"],
                                                          "updated_at": now()})
        self.engine.audit(ws_id, user_id, "cost_centre.budget_import", row["id"],
                          {"file_id": file_id, "version": v["version"], "lines": len(stored)})
        parsed.pop("lines")
        return parsed | {"lines": self.budget_lines(row["id"]),
                         "mapped": sum(1 for s in stored if s["accounts"]), "file_name": name}

    def _store_budget(self, cc_id: str, lines: list[dict[str, Any]]) -> None:
        with self.engine.db.tx() as c:
            c.execute("DELETE FROM cost_centre_budget_lines WHERE cost_centre_id = ?", (cc_id,))
            for i, ln in enumerate(lines):
                c.execute("INSERT INTO cost_centre_budget_lines (id, cost_centre_id, position, label, amount, accounts, "
                          "source) VALUES (?,?,?,?,?,?,?)", (new_id("bl_"), cc_id, i, ln["label"][:200],
                                                             round(float(ln["amount"]), 2),
                                                             dumps(ln.get("accounts") or []),
                                                             dumps(ln.get("source") or {})))

    def set_budget_lines(self, ws_id: str, user_id: str, cc_id: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
        """Replaces the budget lines (edits of labels, amounts and account matches). A line that keeps its id keeps
        the file cell it came from unless its amount changed."""
        row = self._row(ws_id, cc_id)
        old = {b["id"]: b for b in self.budget_lines(row["id"])}
        stored = []
        for ln in lines:
            prev = old.get(ln.get("id") or "")
            source = dict(prev["source"]) if prev else {"entered_by": user_id}
            if prev and abs(float(prev["amount"]) - float(ln["amount"])) > 0.004:
                source = {"entered_by": user_id, "was": {"amount": prev["amount"], **prev["source"]}}
            stored.append({"label": ln["label"], "amount": ln["amount"], "accounts": ln.get("accounts") or [],
                           "source": source})
        self._store_budget(row["id"], stored)
        self.engine.db.update("cost_centres", row["id"], {"updated_at": now()})
        self.engine.audit(ws_id, user_id, "cost_centre.budget_edit", row["id"], {"lines": len(stored)})
        return self.get(ws_id, row["id"])
