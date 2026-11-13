"""E3 — the agent loop under failure: model errors, broken streams, bad tool calls, failing and slow tools, loops
and long conversations.

Every scenario drives the real agent loop through the HTTP API with a scripted OpenAI-compatible model on a mocked
transport, so each one shows what the engine does when the model or a tool misbehaves in that exact way.

    python3 reports/ai-agent-audit/evals/e3_reliability.py
"""
from __future__ import annotations

import json
import time

import httpx2 as httpx

from common import FakeLLM, Harness, save, sse

URL = f"{FakeLLM.BASE}/chat/completions"
results: list[dict] = []


def turn(h: Harness, text: str, thread: str | None = None, timeout: float = 30) -> dict:
    if thread is None:
        thread = h.api("POST", f"/api/w/{h.ws}/threads", json={})["id"]
    h.api("POST", f"/api/w/{h.ws}/threads/{thread}/messages", json={"text": text})
    data = h.wait_run(thread, timeout)
    data["thread_id"] = thread
    return data


def final_text(data: dict) -> str:
    msgs = [m for m in data["messages"] if m["role"] == "assistant"]
    return msgs[-1]["content"] if msgs else ""


def events(data: dict) -> list[str]:
    return [m["content"] for m in data["messages"] if m["role"] == "event"]


def scenario(name: str, expectation: str):
    def wrap(fn):
        def run():
            h = Harness()
            h.setup_workspace()
            t0 = time.perf_counter()
            try:
                ok, observed = fn(h)
            except Exception as exc:  # the harness itself must never hide a failure
                ok, observed = False, f"scenario raised {type(exc).__name__}: {exc}"
            finally:
                h.close()
            results.append({"scenario": name, "expected": expectation, "pass": bool(ok), "observed": observed,
                            "seconds": round(time.perf_counter() - t0, 2)})
            print(("PASS " if ok else "FAIL ") + name + " — " + str(observed)[:220])
        return run
    return wrap


def text_chunks(text: str, finish: str | None = "stop") -> list[dict]:
    words = text.split(" ")
    out = [{"choices": [{"delta": {"content": w + (" " if i < len(words) - 1 else "")}}]} for i, w in enumerate(words)]
    if finish:
        out.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return out


# --- model failures -------------------------------------------------------------------------------
@scenario("model_429_rate_limited", "a transient 429 is retried (with backoff) or the run fails with a clear, honest message")
def s429(h):
    calls = []
    h.router.add("POST", URL, lambda r: calls.append(1) or httpx.Response(429, json={"error": {"message": "rate limited"}},
                                                                          headers={"retry-after": "1"}))
    d = turn(h, "What is our cash position?")
    return d["run"]["status"] == "error" and len(calls) >= 1, {"status": d["run"]["status"], "model_calls": len(calls),
                                                                 "retried": len(calls) > 1, "message": d["run"]["error"]}


@scenario("model_500_then_ok", "one transient 500 is retried and the turn still completes")
def s500(h):
    calls = []

    def handler(r):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(500, text="upstream exploded")
        return sse(text_chunks("All good."))
    h.router.add("POST", URL, handler)
    d = turn(h, "hello")
    return d["run"]["status"] == "done", {"status": d["run"]["status"], "model_calls": len(calls), "error": d["run"]["error"]}


@scenario("model_timeout", "a model timeout ends the run with a clear message (no hang, no partial answer)")
def stimeout(h):
    def handler(r):
        raise httpx.ReadTimeout("read timed out", request=r)
    h.router.add("POST", URL, handler)
    d = turn(h, "hello")
    return d["run"]["status"] == "error" and "Unexpected error" not in (d["run"]["error"] or ""), {
        "status": d["run"]["status"], "message": d["run"]["error"]}


@scenario("stream_cut_before_done", "a stream that ends without [DONE] or a finish_reason is not stored as a complete answer")
def scut(h):
    chunks = text_chunks("Revenue for October was 120,450.00 and the margin", finish=None)
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)  # no finish_reason, no [DONE]: the connection dropped
    h.router.add("POST", URL, lambda r: httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"}))
    d = turn(h, "How did October go?")
    txt = final_text(d)
    return d["run"]["status"] != "done", {"status": d["run"]["status"], "stored_answer": txt,
                                          "marked_incomplete": any("incomplete" in e.lower() or "cut" in e.lower() for e in events(d))}


@scenario("finish_reason_length", "an answer cut by the output-token limit is marked as incomplete")
def slength(h):
    h.router.add("POST", URL, lambda r: sse(text_chunks("The three largest variances are rent at", finish="length")))
    d = turn(h, "Explain the variances")
    msg = [m for m in d["messages"] if m["role"] == "assistant"][-1]
    flagged = "length" in json.dumps(msg["payload"]) or any("limit" in e.lower() for e in events(d))
    return flagged, {"status": d["run"]["status"], "stored_answer": msg["content"], "flagged": flagged}


@scenario("malformed_sse_chunk", "a chunk that is not valid JSON is reported, not silently dropped from the answer")
def smalformed(h):
    good = text_chunks("Net profit was 45,000.00 in Q3.")
    body = ("".join(f"data: {json.dumps(c)}\n\n" for c in good[:3]) + 'data: {"choices": [{"delta": {"content": "NOT 45"\n\n'
            + "".join(f"data: {json.dumps(c)}\n\n" for c in good[3:]) + "data: [DONE]\n\n")
    h.router.add("POST", URL, lambda r: httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"}))
    d = turn(h, "Q3 profit?")
    return d["run"]["status"] != "done" or any("malformed" in e.lower() for e in events(d)), {
        "status": d["run"]["status"], "stored_answer": final_text(d)}


@scenario("empty_model_reply", "an empty reply (no text, no tool call) is not stored as the answer")
def sempty(h):
    h.router.add("POST", URL, lambda r: sse([{"choices": [{"delta": {}, "finish_reason": "stop"}]}]))
    d = turn(h, "Summarise the month")
    return d["run"]["status"] != "done" or bool(final_text(d)), {"status": d["run"]["status"], "stored_answer": repr(final_text(d))}


# --- tool-call failures ---------------------------------------------------------------------------
@scenario("invalid_json_arguments", "invalid tool arguments go back to the model as an error and the run recovers")
def sbadjson(h):
    step = []

    def handler(r):
        step.append(1)
        if len(step) == 1:
            return sse([{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": "calc_growth", "arguments": '{"values": [100, 120'}}]}}]}, {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}])
        if len(step) == 2:
            seen = json.loads(r.content)["messages"][-1]["content"]
            step.append(seen)
            return sse(text_chunks("Fixed."))
        return sse(text_chunks("Done."))
    h.router.add("POST", URL, handler)
    d = turn(h, "growth of 100 to 120")
    return d["run"]["status"] == "done" and "not valid JSON" in str(step), {"status": d["run"]["status"],
                                                                             "tool_result_seen_by_model": step[-1] if len(step) > 2 else None}


@scenario("unknown_tool", "a call to a tool that does not exist is refused, audited and the run continues")
def sunknown(h):
    h.llm.script = [[("transfer_funds", {"amount": 1})], "I cannot do that."]
    d = turn(h, "move money")
    audited = h.engine.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'tool.denied'") if _has_table(h, "audit_log") else None
    return d["run"]["status"] == "done", {"status": d["run"]["status"], "audited_denials": audited}


@scenario("schema_violation", "arguments of the wrong type are refused before the tool runs")
def sschema(h):
    h.llm.script = [[("calc_loan_schedule", {"principal": "a lot", "annual_rate": 0.05, "periods": 12})], "ok"]
    d = turn(h, "loan")
    tool = [m for m in d["messages"] if m["role"] == "tool"][0]
    return "schema" in tool["content"], {"tool_result": tool["content"][:160]}


def _patch(name: str, handler):
    from desk_engine.agent.tools import registry
    t = registry.get_static(name)
    old = (t.handler, t.timeout)
    t.handler = handler
    return t, old


@scenario("tool_raises_unexpected_exception", "a tool that raises (e.g. RuntimeError, database locked) returns an error to the "
          "model; the turn is not lost")
def sraise(h):
    import sqlite3

    async def boom(ctx, a):
        raise sqlite3.OperationalError("database is locked")
    t, old = _patch("calc_growth", boom)
    try:
        h.llm.script = [[("calc_growth", {"values": [1, 2]})], "Recovered answer."]
        d = turn(h, "growth")
    finally:
        t.handler, t.timeout = old
    return d["run"]["status"] == "done", {"status": d["run"]["status"], "error": d["run"]["error"],
                                          "answer": final_text(d)}


@scenario("tool_timeout_async", "a slow (async) tool is stopped at its timeout and the run continues")
def stimeout_tool(h):
    import asyncio

    async def slow(ctx, a):
        await asyncio.sleep(5)
    t, old = _patch("calc_growth", slow)
    t.timeout = 0.5
    try:
        h.llm.script = [[("calc_growth", {"values": [1, 2]})], "ok"]
        t0 = time.perf_counter()
        d = turn(h, "growth")
        took = time.perf_counter() - t0
    finally:
        t.handler, t.timeout = old
    tool = [m for m in d["messages"] if m["role"] == "tool"][0]
    return d["run"]["status"] == "done" and "did not finish" in tool["content"] and took < 4, {
        "turn_seconds": round(took, 2), "tool_result": tool["content"]}


@scenario("tool_blocking_sync_work", "a tool doing blocking work cannot stall other conversations beyond its timeout")
def sblocking(h):
    async def blocking(ctx, a):
        time.sleep(2.0)   # e.g. a large synchronous SQLite query or file parse inside an async handler
        from desk_engine.agent.tools import ToolOutput
        return ToolOutput("done")
    t, old = _patch("calc_growth", blocking)
    t.timeout = 0.5
    try:
        h.llm.script = [[("calc_growth", {"values": [1, 2]})], "ok"]
        thread = h.api("POST", f"/api/w/{h.ws}/threads", json={})["id"]
        h.api("POST", f"/api/w/{h.ws}/threads/{thread}/messages", json={"text": "growth"})
        time.sleep(0.2)
        t0 = time.perf_counter()
        h.client.get("/health")   # another request served by the same event loop
        other = time.perf_counter() - t0
        d = h.wait_run(thread, 30)
    finally:
        t.handler, t.timeout = old
    tool = [m for m in d["messages"] if m["role"] == "tool"][0]
    return other < 0.5, {"other_request_seconds_while_tool_runs": round(other, 2), "tool_result": tool["content"][:80],
                         "timeout_enforced": "did not finish" in tool["content"]}


@scenario("runaway_loop", "a model that never stops calling tools is stopped after MAX_STEPS")
def sloop(h):
    h.llm.script = [[("calc_growth", {"values": [1, 2]})]] * 40
    d = turn(h, "loop")
    return d["run"]["status"] == "error" and len(h.llm.calls) == 16, {"model_calls": len(h.llm.calls), "error": d["run"]["error"]}


@scenario("repeated_identical_tool_calls", "the same tool call with the same arguments in one run is not executed again and again")
def srepeat(h):
    h.llm.script = [[("calc_growth", {"values": [1, 2]})]] * 6 + ["done"]
    d = turn(h, "growth")
    n = sum(1 for m in d["messages"] if m["role"] == "tool")
    return n <= 2, {"identical_executions": n}


# --- conversation size ----------------------------------------------------------------------------
@scenario("long_conversation_80_turns", "request size stays bounded and every request keeps call/result pairing valid")
def slong(h):
    big = "x" * 9000
    script = []
    for i in range(80):
        script += [[("calc_growth", {"values": [100 + i, 120 + i]})], f"Answer {i}. " + big[: 200]]
    h.llm.script = script
    thread = h.api("POST", f"/api/w/{h.ws}/threads", json={})["id"]
    sizes, counts, broken = [], [], 0
    for i in range(80):
        turn(h, f"Question {i}: " + big[:1500], thread)
    for body in h.llm.calls:
        msgs = body["messages"]
        sizes.append(len(json.dumps(msgs)) + len(json.dumps(body.get("tools", []))))
        counts.append(len(msgs))
        ids = set()
        for m in msgs:
            for c in m.get("tool_calls") or []:
                ids.add(c["id"])
            if m["role"] == "tool" and m.get("tool_call_id") not in ids:
                broken += 1
    return broken == 0 and max(sizes) < 200_000, {"requests": len(sizes), "first_request_chars": sizes[0],
                                                   "max_request_chars": max(sizes), "last_request_chars": sizes[-1],
                                                   "max_messages": max(counts), "orphan_tool_results": broken}


@scenario("early_fact_retained", "a fact the user stated early in a long conversation still reaches the model later "
          "(or is kept by summary/memory)")
def sretain(h):
    h.llm.script = ["Noted."] + [[("calc_growth", {"values": [1, 2]})], "ok"] * 30
    thread = h.api("POST", f"/api/w/{h.ws}/threads", json={})["id"]
    turn(h, "Remember for this conversation: our fiscal year ends on 30 June and we report in SAR thousands.", thread)
    for i in range(30):
        turn(h, f"Question {i}", thread)
    last = json.dumps(h.llm.calls[-1]["messages"])
    kept = "30 June" in last
    lost_at = next((i for i, b in enumerate(h.llm.calls) if "30 June" not in json.dumps(b["messages"])), None)
    return kept, {"fact_in_last_request": kept, "first_request_without_it": lost_at, "requests": len(h.llm.calls)}


def _has_table(h, name):
    return h.engine.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None


if __name__ == "__main__":
    for fn in (s429, s500, stimeout, scut, slength, smalformed, sempty, sbadjson, sunknown, sschema, sraise,
               stimeout_tool, sblocking, sloop, srepeat, slong, sretain):
        fn()
    passed = sum(r["pass"] for r in results)
    save("e3_reliability", {"scenarios": len(results), "passed": passed, "results": results})
    print(f"E3: {passed}/{len(results)} passed")
