"""Agent and workflow tools over the month-end close. Preparing a close only reads the ledger and records proposals
in the app; nothing is posted to the accounting system, so it runs without an approval (also unattended at night)."""

from __future__ import annotations

from typing import Any

from ..errors import AppError
from .analysis_tools import _has_ledger
from .tools import ToolContext, ToolOutput, registry, schema


def _view(ctx: ToolContext, a: dict[str, Any]) -> dict[str, Any] | None:
    month = a.get("month")
    run = ctx.engine.close.find(ctx.ws_id, month) if month else None
    if run is None and not month:
        row = ctx.engine.db.one("SELECT id FROM close_runs WHERE workspace_id = ? ORDER BY month DESC, updated_at DESC",
                                (ctx.ws_id,))
        run = {"id": row["id"]} if row else None
    return ctx.engine.close.view(ctx.ws_id, run["id"]) if run else None


@registry.tool("month_end_close", "Where the month-end close stands: readiness, the checklist, proposed entries "
               "(missing accruals, depreciation, bank charges) with their status, large variances and their "
               "explanations, customers not invoiced and the bank reconciliation. Optional month (YYYY-MM); the latest "
               "close when omitted.", schema({"month": {"type": "string"}}), category="analysis", available=_has_ledger)
async def _status(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    try:
        view = _view(ctx, a)
    except AppError as exc:
        return ToolOutput(exc.message)
    if view is None:
        return ToolOutput("No month-end close has been prepared yet" + (f" for {a['month']}" if a.get("month") else "")
                          + ". It can be prepared on the Month-end close page or with prepare_month_end_close.")
    text = ctx.engine.close.summary_text(view)
    var = [i for i in view["items"] if i["kind"] == "variance" and i["status"] != "resolved"]
    if var:
        text += "\nVariances:\n" + "\n".join(f"- {i['data']['title']}. {i['data']['reason']}"
                                             + (f" Explanation: {i['note']}" if i["note"] else "") for i in var)
    b = view.get("bank")
    if b:
        text += (f"\nBank: {b['matched']} of {b['statement_lines']} statement lines matched; "
                 f"{len(b['ledger_only'])} ledger items not yet on the statement; "
                 + (f"unexplained difference {b['difference']:,.2f}." if b.get("difference") is not None
                    else "closing balance not given."))
    return ToolOutput(text + f"\nPage: /close/{view['id']}", [], {"id": view["id"], "month": view["month"],
                                                                  "readiness": view["readiness"]})


@registry.tool("prepare_month_end_close", "Prepare (or refresh) the month-end close from the general ledger: checks "
               "that entries balance, proposes accruals for regular costs missing this month and depreciation not "
               "charged, matches the bank statement if one was added, lists large variances and regular customers not "
               "invoiced, and scores readiness. Nothing is posted to the accounting system. Optional month (YYYY-MM, "
               "default: the last complete month) and ledger (dataset id).",
               schema({"month": {"type": "string"}, "ledger": {"type": "string"}}),
               category="analysis", available=_has_ledger, min_role="editor")
async def _prepare(ctx: ToolContext, a: dict[str, Any]) -> ToolOutput:
    import asyncio

    try:
        view = await asyncio.to_thread(ctx.engine.close.prepare, ctx.ws_id, ctx.user_id, month=a.get("month") or None,
                                       dataset_id=a.get("ledger") or None,
                                       via="workflow" if ctx.source == "workflow" else "assistant")
    except AppError as exc:
        return ToolOutput(exc.message)
    return ToolOutput(ctx.engine.close.summary_text(view) + f"\nPage: /close/{view['id']}", [],
                      {"id": view["id"], "month": view["month"], "readiness": view["readiness"],
                       "link": f"/close/{view['id']}"})
