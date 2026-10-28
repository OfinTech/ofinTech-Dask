"""Fake OpenAI-compatible server for UI checks: answers by keyword, can call tools."""
import json
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

async def models(request):
    return JSONResponse({"data": [{"id": "demo-finance-1"}, {"id": "demo-finance-mini"}]})

def sse(chunks):
    async def gen():
        for c in chunks:
            yield f"data: {json.dumps(c)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

def text(t):
    words = t.split(" ")
    return [{"choices": [{"delta": {"content": w + " "}}]} for w in words] + [{"choices": [{"delta": {}, "finish_reason": "stop"}]}]

def call(name, args):
    return [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": name, "arguments": json.dumps(args)}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}]

WORKBOOK = {
    "title": "Q1 2026 management accounts",
    "sheets": [
        {"name": "Summary", "blocks": [
            {"type": "heading", "text": "Q1 2026 by account and month"},
            {"type": "table", "id": "pl", "title": "Management P&L",
             "query": {"dataset": "pl · Management P&L", "measures": [{"column": "Amount", "agg": "sum"}],
                       "group_by": [{"column": "Account"}, {"column": "Period", "grain": "month"}]},
             "pivot": {"column": "Period"}},
            {"type": "kpis", "items": [
                {"label": "Fuel as % of revenue", "value": "[pl:Fuel:row_total] / [pl:Freight revenue:row_total]",
                 "format": "percent"},
                {"label": "Q1 revenue", "value": "[pl:Freight revenue:row_total]", "format": "money"}]},
        ]},
        {"name": "Adjustments", "blocks": [
            {"type": "table", "id": "adj", "title": "Proposed accruals", "rows": [["Rent accrual", 14000], ["Audit fee", 7250.5]],
             "columns": [{"label": "Item"}, {"label": "Amount", "format": "money"},
                         {"label": "With VAT 15%", "formula": "ROUND(amount * 1.15, 2)", "format": "money"}]},
        ]},
    ],
}


def review(msgs):
    import re
    if msgs[-1]["role"] == "tool":
        return sse(text("One point needs attention before this goes out."))
    content = msgs[-1]["content"]
    sheet = re.search(r"SHEET “(.+?)”", content)
    row = re.search(r"row (\d+):", content)
    target = f"{sheet.group(1)}!D{row.group(1)}" if sheet and row else "file"
    return sse(call("add_finding", {"target": target, "severity": "medium",
                                    "title": "February fuel looks low against revenue",
                                    "detail": "Fuel fell while revenue was flat; check for a missing supplier invoice."}))


async def chat(request):
    body = await request.json()
    msgs = body["messages"]
    last = msgs[-1]
    if "independent reviewer" in msgs[0]["content"]:
        return review(msgs)
    wants_workbook = any(m["role"] == "user" and "workbook" in str(m.get("content", "")).lower() for m in msgs[-6:])
    if wants_workbook and last["role"] == "tool" and last["content"].startswith("- "):
        name = last["content"][2:].split(" (source")[0]
        return sse(call("query_dataset", {"dataset": name, "measures": [{"column": "Amount", "agg": "sum"}],
                                          "group_by": [{"column": "Account"}]}))
    if wants_workbook and last["role"] == "tool" and "Freight revenue" in last["content"] and "Saved workbook" not in last["content"]:
        import copy
        name = next(m for m in reversed(msgs) if m["role"] == "assistant" and m.get("tool_calls"))
        args = json.loads(name["tool_calls"][0]["function"]["arguments"])
        wb = copy.deepcopy(WORKBOOK)
        wb["sheets"][0]["blocks"][1]["query"]["dataset"] = args["dataset"]
        return sse(call("create_workbook", wb))
    if last["role"] == "tool" and "Saved workbook" in last["content"]:
        return sse(text("The workbook is ready. One accrual (the audit fee) has no source yet; please confirm it."))
    if last["role"] == "tool":
        content = last["content"]
        if "NPV" in content:
            return sse(text("Here is the evaluation.\n\n| Measure | Value |\n|---|---:|\n| NPV at 8% | 2,463.94 |\n| IRR | 9.70% |\n\nThe project clears an **8%** hurdle, so it adds value on these cash flows."))
        if content.startswith("[1]") and "Payments above" in content:
            return sse(text("The attached delegation of authority says payments above 25,000 need two approvers, the CFO and the finance manager [1]."))
        if content.startswith("[1]"):
            return sse(text("Under the travel policy, hotels may cost at most 180 per night [1], and expenses above 1,000 need the finance manager's approval [2]."))
        if "Sent to" in content or "rejected" in content:
            return sse(text("Done. " + content[:120]))
        return sse(text("Here is what I found:\n\n" + content[:600]))
    if last["role"] == "user" and "[Attached files]" in last["content"]:
        import re
        ids = re.findall(r"document id (doc_[a-z0-9]+)", last["content"])
        return sse(call("search_documents", {"query": "approval limit", "document_ids": ids}))
    q = last["content"].lower()
    if "policy" in q or "contract" in q:
        return sse(call("search_documents", {"query": last["content"]}))
    if "npv" in q or "irr" in q:
        return sse(call("calc_npv_irr", {"cashflows": [-50000, 12000, 15000, 18000, 16000], "discount_rate": 0.08}))
    if "remind" in q or "message" in q:
        return sse(call("message_colleague", {"member": "me@this-computer.local", "message": "Please post the March accruals by Friday."}))
    if "workbook" in q:
        return sse(call("list_datasets", {}))
    if "dataset" in q or "data" in q:
        return sse(call("list_datasets", {}))
    return sse(text("I can help with figures from your ledger, spreadsheets and connected systems. Try asking for an NPV or a variance."))

async def hook(request):
    """A webhook receiver: answers 200 when the delivery is signed, 401 otherwise (the smoke test checks both)."""
    body = await request.body()
    sig = request.headers.get("x-ofintech-signature-256", "")
    if not sig.startswith("sha256=") or not request.headers.get("x-ofintech-event"):
        return JSONResponse({"error": "unsigned"}, status_code=401)
    return JSONResponse({"ok": True, "event": request.headers["x-ofintech-event"], "bytes": len(body)})


app = Starlette(routes=[Route("/v1/models", models), Route("/v1/chat/completions", chat, methods=["POST"]),
                        Route("/hook", hook, methods=["POST"])])
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=47999, log_level="warning")
