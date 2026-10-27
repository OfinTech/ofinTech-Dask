"""The agent loop: model call → tool calls → results → model call, until the model answers.

* Read tools run immediately. Write tools (and MCP tools whose policy is "ask") pause the run and
  create approval requests; the run resumes when every pending request is decided.
* Every event is published on the thread channel and stored as messages, so the conversation can be
  rebuilt for the next turn and shown to everyone in the workspace.
* After the final answer the figure check lists numbers that do not appear in any tool result or
  user message, so reviewers know what to verify.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, new_id, now
from ..errors import AppError, Conflict, Forbidden, NotFound
from ..llm.base import ChatMessage, ToolCall, Usage
from ..llm.registry import build_model, resolve_choice
from ..skills.loader import enabled_index, get_skill
from .context import available_groups, choose_tools, compact, select_groups
from .prompts import system_parts
from .tools import Tool, ToolContext, ToolOutput

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.agent")

MAX_STEPS = 16
HISTORY_MESSAGES = 60


# --- figure check -----------------------------------------------------------------------------

_NUM_RE = re.compile(r"(?<![\w.])[-−(]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:%|k|m|bn|million|billion)?(?![\w])",
                     re.I)


def _numbers(text: str) -> list[tuple[str, float]]:
    out = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(0).strip()
        s = raw.lower().replace("−", "-").replace("(", "-").replace(",", "")
        mult = 1.0
        for suffix, factor in (("billion", 1e9), ("million", 1e6), ("bn", 1e9), ("%", 0.01), ("k", 1e3), ("m", 1e6)):
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                mult = factor
                break
        try:
            out.append((raw, float(s) * mult))
        except ValueError:
            continue
    return out


def unverified_figures(answer: str, evidence: list[str]) -> list[str]:
    known: list[float] = []
    for text in evidence:
        for _, v in _numbers(text):
            known.append(v)
    flagged = []
    for raw, v in _numbers(answer):
        a = abs(v)
        if a < 100 and "." not in raw and "%" not in raw:
            continue  # small counts, list numbers, days
        if 1900 <= a <= 2100 and "." not in raw and "," not in raw:
            continue  # years
        ok = False
        for k in known:
            for cand in (k, k * 100, k / 100):
                tol = max(abs(cand) * 0.005, 0.011)
                if abs(abs(cand) - a) <= tol:
                    ok = True
                    break
            if ok:
                break
        if not ok and raw not in flagged:
            flagged.append(raw)
    return flagged[:20]


# --- conversation <-> storage -----------------------------------------------------------------


def history(engine: Engine, thread_id: str) -> list[ChatMessage]:
    rows = engine.db.all(
        "SELECT * FROM (SELECT * FROM messages WHERE thread_id = ? AND role IN ('user','assistant','tool') "
        "ORDER BY created_at DESC LIMIT ?) ORDER BY created_at", (thread_id, HISTORY_MESSAGES))
    msgs: list[ChatMessage] = []
    for r in rows:
        payload = loads(r["payload"], {})
        if r["role"] == "user":
            msgs.append(ChatMessage("user", r["content"]))
        elif r["role"] == "assistant":
            calls = [ToolCall(c["id"], c["name"], c.get("arguments") or {}) for c in payload.get("tool_calls", [])]
            msgs.append(ChatMessage("assistant", r["content"], calls))
        else:
            msgs.append(ChatMessage("tool", r["content"], tool_call_id=payload.get("tool_call_id"),
                                    name=payload.get("name")))
    # drop leading tool results whose assistant call fell outside the window
    while msgs and msgs[0].role == "tool":
        msgs.pop(0)
    # every tool call must have a result (the model APIs require it)
    answered = {m.tool_call_id for m in msgs if m.role == "tool"}
    fixed: list[ChatMessage] = []
    for m in msgs:
        fixed.append(m)
        if m.role == "assistant":
            for c in m.tool_calls:
                if c.id not in answered:
                    fixed.append(ChatMessage("tool", "No result (the call was interrupted).", tool_call_id=c.id,
                                             name=c.name))
    return fixed


ATTACHED_MARK = "\n\n[Attached files]\n"


def attachment_note(docs: list[dict[str, Any]]) -> str:
    """The lines the model reads about files attached to a message (the app shows them as chips instead)."""
    lines = []
    for d in docs:
        if d["status"] == "indexed":
            what = (f"{d['pages']} pages, " if d.get("pages") else "") + "readable"
        elif d["status"] == "remote":
            what = "scanned or not readable here; searchable through PipesHub (OCR)"
        elif d["status"] == "processing":
            what = "still being processed"
        else:
            what = f"could not be read: {d.get('error') or 'unknown error'}"
        lines.append(f"- “{d['name']}” (document id {d['id']}; {what})")
    editable = any(str(d["name"]).lower().endswith((".xlsx", ".docx")) for d in docs)
    return (ATTACHED_MARK + "\n".join(lines) + "\nUse read_document for a whole file, or search_documents with "
            "document_ids to find passages; cite them as usual."
            + ("\nTo fill in or change an attached .xlsx/.docx itself, call open_file_for_editing, then inspect_file "
               "and edit_file." if editable else ""))


class Agent:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.tasks: dict[str, asyncio.Task[None]] = {}

    # -- public API ----------------------------------------------------------------------------
    def thread_busy(self, thread_id: str) -> bool:
        task = self.tasks.get(thread_id)
        if task and not task.done():
            return True
        return self.engine.db.one(
            "SELECT 1 FROM agent_runs WHERE thread_id = ? AND status IN ('running','waiting_approval')", (thread_id,)
        ) is not None

    def start_turn(self, workspace: dict[str, Any], user_id: str, thread_id: str, text: str, *,
                   parent: dict[str, Any] | None = None, source: str = "chat",
                   attachments: list[dict[str, Any]] | None = None) -> str:
        if self.thread_busy(thread_id):
            raise Conflict("The assistant is still working on this conversation or waiting for an approval.",
                           code="thread_busy")
        ts = now()
        msg_id = new_id("m_")
        run_id = new_id("run_")
        with self.engine.db.tx() as c:
            c.execute("INSERT INTO messages (id, thread_id, role, author_id, content, payload, created_at) "
                      "VALUES (?,?,?,?,?,?,?)", (msg_id, thread_id, "user", user_id, text,
                                                 dumps({"source": source, **({"attachments": attachments}
                                                                             if attachments else {})}), ts))
            c.execute("INSERT INTO agent_runs (id, workspace_id, thread_id, user_id, status, state, created_at, "
                      "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      (run_id, workspace["id"], thread_id, user_id, "running",
                       dumps({"step": 0, "parent": parent, "source": source}), ts, ts))
            c.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (ts, thread_id))
        self.engine.events.start_run(thread_id)
        self.engine.events.publish(thread_id, {"type": "message", "message": self._message_out(msg_id)})
        self._spawn(thread_id, run_id)
        return run_id

    def _spawn(self, thread_id: str, run_id: str) -> None:
        task = asyncio.get_running_loop().create_task(self._run_safely(run_id))
        self.tasks[thread_id] = task

    async def wait(self, thread_id: str) -> None:
        task = self.tasks.get(thread_id)
        if task:
            await task

    async def decide(self, workspace: dict[str, Any], approval_id: str, user_id: str, role: str,
                     approve: bool, note: str | None = None) -> dict[str, Any]:
        from ..web import role_at_least

        appr = self.engine.db.one("SELECT * FROM approvals WHERE id = ? AND workspace_id = ?",
                                  (approval_id, workspace["id"]))
        if appr is None:
            raise NotFound("Approval not found.")
        if appr["status"] != "pending":
            raise Conflict("This request was already decided.")
        if not role_at_least(role, "editor"):
            raise Forbidden("Approving actions needs the editor role or higher.")
        status = "approved" if approve else "rejected"
        self.engine.db.execute(
            "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, result = ? WHERE id = ? AND status = 'pending'",
            (status, user_id, now(), dumps({"note": note}) if note else None, approval_id))
        self.engine.audit(workspace["id"], user_id, f"approval.{status}", approval_id,
                          {"tool": appr["tool"], "source": appr["source"]})
        if appr["source"] == "agent":
            run = self.engine.db.one("SELECT * FROM agent_runs WHERE id = ?", (appr["source_id"],))
            if run:
                pending = self.engine.db.scalar(
                    "SELECT COUNT(*) FROM approvals WHERE source = 'agent' AND source_id = ? AND status = 'pending'",
                    (run["id"],))
                self.engine.events.publish(run["thread_id"], {"type": "approval", "approval": self.approval_out(approval_id)})
                if pending == 0 and run["status"] == "waiting_approval":
                    self.engine.db.execute("UPDATE agent_runs SET status = 'running', updated_at = ? WHERE id = ?",
                                           (now(), run["id"]))
                    self.engine.events.start_run(run["thread_id"])
                    self._spawn(run["thread_id"], run["id"])
        elif appr["source"] == "workflow":
            await self.engine.workflows.on_approval(appr, approve)
        return self.approval_out(approval_id)

    def approval_out(self, approval_id: str) -> dict[str, Any]:
        a = self.engine.db.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        assert a is not None
        a["arguments"] = loads(a["arguments"], {})
        a["result"] = loads(a["result"], None)
        return a

    def _message_out(self, msg_id: str) -> dict[str, Any]:
        m = self.engine.db.one("SELECT * FROM messages WHERE id = ?", (msg_id,))
        assert m is not None
        m["payload"] = loads(m["payload"], {})
        return m

    # -- the loop ------------------------------------------------------------------------------
    async def _run_safely(self, run_id: str) -> None:
        run = self.engine.db.one("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        if run is None:
            return
        thread_id = run["thread_id"]
        try:
            await self._run(run)
        except AppError as exc:
            self._fail(run, exc.message)
        except Exception as exc:  # pragma: no cover - logged
            log.exception("agent run %s failed", run_id)
            self._fail(run, f"Unexpected error: {type(exc).__name__}")
        finally:
            self.engine.events.publish(thread_id, {"type": "run", "run": self.run_out(run_id)})
            self.engine.events.end_run(thread_id)
            final = self.engine.db.one("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
            if final:
                await self.engine.on_agent_run_update(final)

    def run_out(self, run_id: str) -> dict[str, Any]:
        r = self.engine.db.one("SELECT id, thread_id, status, error, usage, created_at, updated_at, state "
                               "FROM agent_runs WHERE id = ?", (run_id,))
        assert r is not None
        r["usage"] = loads(r["usage"], {})
        state = loads(r.pop("state"), {})
        r["final_text"] = state.get("final_text")
        return r

    def _fail(self, run: dict[str, Any], message: str) -> None:
        ts = now()
        msg_id = new_id("m_")
        with self.engine.db.tx() as c:
            c.execute("UPDATE agent_runs SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
                      (message, ts, run["id"]))
            c.execute("INSERT INTO messages (id, thread_id, role, content, payload, created_at) VALUES (?,?,?,?,?,?)",
                      (msg_id, run["thread_id"], "event", message, dumps({"kind": "error", "run_id": run["id"]}), ts))
        self.engine.events.publish(run["thread_id"], {"type": "message", "message": self._message_out(msg_id)})

    async def _run(self, run: dict[str, Any]) -> None:
        engine = self.engine
        thread = engine.db.one("SELECT * FROM threads WHERE id = ?", (run["thread_id"],))
        workspace = engine.db.one("SELECT * FROM workspaces WHERE id = ?", (run["workspace_id"],))
        if thread is None or workspace is None:
            raise NotFound("Conversation not found.")
        state = loads(run["state"], {})
        ctx = ToolContext(engine, workspace, run["user_id"], thread["id"], run["id"], state.get("source") or "chat")
        tools = await engine.tools.for_context(ctx)
        usage = Usage(**loads(run["usage"], {})) if run["usage"] and run["usage"] != "{}" else Usage()

        # 1. finish pending tool calls decided by approvers
        await self._resolve_pending(ctx, run, state, tools)

        provider_id, model_name = resolve_choice(engine, workspace, thread["provider_id"], thread["model"])
        model = build_model(engine, workspace["id"], provider_id, model_name)
        skills = enabled_index(engine, workspace["id"])
        pinned = []
        for name in loads(thread["skills"], []):
            try:
                pinned.append(get_skill(engine, workspace["id"], name))
            except AppError:
                continue
        from ..data.store import list_datasets

        connections = engine.db.all("SELECT id, provider, label FROM connections WHERE workspace_id = ? AND "
                                    "status = 'connected'", (workspace["id"],))
        datasets = list_datasets(engine, workspace["id"])
        system, dynamic = system_parts(workspace, skills=skills, pinned=pinned, connections=connections,
                                     datasets=datasets,
                                     extra=state.get("extra_instructions"), knowledge=self._knowledge_note(workspace["id"]),
                                     language=engine.db.scalar("SELECT language FROM users WHERE id = ?", (run["user_id"],)))
        last_user = engine.db.one("SELECT content FROM messages WHERE thread_id = ? AND role = 'user' ORDER BY created_at "
                                  "DESC LIMIT 1", (thread["id"],))
        memories = engine.memory.relevant(workspace["id"], run["user_id"], (last_user or {}).get("content") or "")
        mem_block = engine.memory.prompt_block(memories)
        if mem_block:
            dynamic += "\n\n" + mem_block

        step = int(state.get("step", 0))
        while step < MAX_STEPS:
            step += 1
            past = history(engine, thread["id"])
            groups = select_groups(past, tools, has_datasets=bool(datasets), extra=state.get("extra_instructions") or "")
            chosen = choose_tools(tools, groups)
            specs = [t.spec() for t in chosen]
            more = available_groups(tools, groups)
            note = ("\n\nMore tool groups can be loaded with more_tools: " + ", ".join(more) + ".") if more else ""
            messages = [ChatMessage("system", system), ChatMessage("system", dynamic + note), *compact(past)]
            msg_id = new_id("m_")
            engine.events.publish(thread["id"], {"type": "assistant_start", "message_id": msg_id, "model": model_name})
            result = None
            async for ev in model.stream(messages, specs):
                if ev.type == "text":
                    engine.events.publish(thread["id"], {"type": "delta", "message_id": msg_id, "text": ev.delta})
                else:
                    result = ev.result
            assert result is not None
            usage.add(result.usage)
            engine.record_usage(workspace["id"], provider_id, model_name, result.usage, "chat")
            payload: dict[str, Any] = {"run_id": run["id"], "model": model_name, "provider_id": provider_id,
                                       "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                                      for c in result.tool_calls]}
            if not result.tool_calls:
                evidence = [m.content for m in past if m.role in ("tool", "user")] + [m["content"] for m in memories]
                payload["unverified_figures"] = unverified_figures(result.text, evidence)
                if memories:
                    payload["memories"] = [{"id": m["id"], "content": m["content"], "scope": m["scope"]} for m in memories]
                    engine.memory.mark_used([m["id"] for m in memories])
            ts = now()
            engine.db.insert("messages", {"id": msg_id, "thread_id": thread["id"], "role": "assistant",
                                          "author_id": None, "content": result.text, "payload": dumps(payload),
                                          "created_at": ts})
            engine.events.publish(thread["id"], {"type": "message", "message": self._message_out(msg_id)})

            if not result.tool_calls:
                state.update(step=step, final_text=result.text, pending=[])
                engine.db.execute("UPDATE agent_runs SET status = 'done', state = ?, usage = ?, updated_at = ? "
                                  "WHERE id = ?", (dumps(state), dumps(usage.__dict__), now(), run["id"]))
                engine.db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now(), thread["id"]))
                return

            pending: list[dict[str, Any]] = []
            for call in result.tool_calls:
                tool = tools.get(call.name)
                if tool is None:
                    self._tool_message(ctx, call, ToolOutput(f"Unknown tool '{call.name}'."), "error")
                    continue
                if tool.needs_approval():
                    approval_id = self._request_approval(ctx, tool, call)
                    pending.append({"call": {"id": call.id, "name": call.name, "arguments": call.arguments},
                                    "approval_id": approval_id})
                    continue
                await self._execute(ctx, tool, call)
            if pending:
                state.update(step=step, pending=pending)
                engine.db.execute("UPDATE agent_runs SET status = 'waiting_approval', state = ?, usage = ?, "
                                  "updated_at = ? WHERE id = ?", (dumps(state), dumps(usage.__dict__), now(), run["id"]))
                return
            state["step"] = step
            engine.db.execute("UPDATE agent_runs SET state = ?, usage = ?, updated_at = ? WHERE id = ?",
                              (dumps(state), dumps(usage.__dict__), now(), run["id"]))

        raise AppError(f"Stopped after {MAX_STEPS} steps without a final answer. Try a narrower request.",
                       code="too_many_steps")

    def _knowledge_note(self, ws_id: str) -> str | None:
        n = self.engine.db.scalar("SELECT COUNT(*) FROM documents WHERE workspace_id = ? AND status = 'indexed'", (ws_id,))
        pipeshub = self.engine.db.one("SELECT 1 FROM connections WHERE workspace_id = ? AND provider = 'pipeshub' AND "
                                  "status = 'connected'", (ws_id,))
        if not n and not pipeshub:
            return None
        colls = [r["name"] for r in self.engine.db.all("SELECT name FROM doc_collections WHERE workspace_id = ? "
                                                       "ORDER BY name", (ws_id,))]
        note = f"Knowledge base: {n} indexed documents"
        if colls:
            note += " in collections: " + ", ".join(colls[:30])
        if pipeshub:
            note += "; PipesHub is connected and searched too"
        return note + ". Use search_documents for anything written in them."

    async def _resolve_pending(self, ctx: ToolContext, run: dict[str, Any], state: dict[str, Any],
                               tools: dict[str, Tool]) -> None:
        pending = state.get("pending") or []
        if not pending:
            return
        for item in pending:
            call = ToolCall(item["call"]["id"], item["call"]["name"], item["call"]["arguments"])
            appr = self.engine.db.one("SELECT status, decided_by FROM approvals WHERE id = ?", (item["approval_id"],))
            status = appr["status"] if appr else "rejected"
            if status == "approved":
                tool = tools.get(call.name)
                if tool is None:
                    self._tool_message(ctx, call, ToolOutput("The tool is no longer available."), "error")
                    continue
                approver_ctx = ToolContext(ctx.engine, ctx.workspace, ctx.user_id, ctx.thread_id, ctx.run_id, ctx.source)
                output = await self._execute(approver_ctx, tool, call, approval_id=item["approval_id"])
                self.engine.db.execute("UPDATE approvals SET result = ? WHERE id = ?",
                                       (dumps({"text": output.text[:2000]}), item["approval_id"]))
            else:
                self._tool_message(ctx, call, ToolOutput("The user rejected this action. Do not retry it unless "
                                                         "they ask; offer an alternative."), "rejected",
                                   approval_id=item["approval_id"])
        state["pending"] = []
        self.engine.db.execute("UPDATE agent_runs SET state = ? WHERE id = ?", (dumps(state), run["id"]))

    def _request_approval(self, ctx: ToolContext, tool: Tool, call: ToolCall) -> str:
        approval_id = new_id("ap_")
        self.engine.db.insert("approvals", {
            "id": approval_id, "workspace_id": ctx.ws_id, "source": "agent", "source_id": ctx.run_id,
            "tool": call.name, "arguments": dumps(call.arguments), "summary": tool.describe_call(call.arguments),
            "status": "pending", "requested_by": ctx.user_id, "created_at": now()})
        self.engine.events.publish(ctx.thread_id or "", {"type": "approval", "approval": self.approval_out(approval_id)})
        self.engine.notify_role(ctx.ws_id, "editor", "approval", f"Approval needed: {tool.describe_call(call.arguments)}",
                                f"/inbox?approval={approval_id}", exclude=None)
        return approval_id

    async def _execute(self, ctx: ToolContext, tool: Tool, call: ToolCall, approval_id: str | None = None) -> ToolOutput:
        self.engine.events.publish(ctx.thread_id or "", {"type": "tool_start", "call": {"id": call.id, "name": call.name,
                                                                                        "arguments": call.arguments}})
        output = await tool.run(ctx, call.arguments)
        status = "error" if output.text.startswith("Error:") else "ok"
        if tool.effect == "write" or tool.policy is not None:
            self.engine.audit(ctx.ws_id, ctx.user_id, "tool.run", call.name,
                              {"approval_id": approval_id, "status": status, "source": ctx.source})
        self._tool_message(ctx, call, output, status, approval_id=approval_id)
        return output

    def _tool_message(self, ctx: ToolContext, call: ToolCall, output: ToolOutput, status: str,
                      approval_id: str | None = None) -> None:
        msg_id = new_id("m_")
        payload = {"tool_call_id": call.id, "name": call.name, "arguments": call.arguments, "blocks": output.blocks,
                   "status": status, "approval_id": approval_id, "run_id": ctx.run_id}
        self.engine.db.insert("messages", {"id": msg_id, "thread_id": ctx.thread_id, "role": "tool", "author_id": None,
                                           "content": output.for_model(), "payload": dumps(payload, ),
                                           "created_at": now()})
        self.engine.events.publish(ctx.thread_id or "", {"type": "message", "message": self._message_out(msg_id)})

