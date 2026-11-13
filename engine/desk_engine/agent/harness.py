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
import os
import random
import re
import time
from typing import TYPE_CHECKING, Any

from ..db import dumps, loads, new_id, now
from ..errors import AppError, Conflict, NotFound
from ..llm.base import ChatMessage, ToolCall, Usage
from ..llm.registry import build_model, resolve_choice
from ..skills.loader import enabled_index, get_enabled_skill
from .context import available_groups, choose_tools, compact, select_groups
from .prompts import system_parts, workspace_context
from .tools import Tool, ToolContext, ToolOutput

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.agent")

MAX_STEPS = 16
HISTORY_MESSAGES = 60
EARLIER_USER_MESSAGES = 20      # user messages older than the window, carried as one note
EARLIER_USER_CHARS = 300        # of each
EARLIER_NOTE_CHARS = 3000       # of the note in all
REPEAT_LIMIT = 2                # identical read-tool calls served from the run's own earlier result after this many

# Model calls that failed for a reason that may pass are tried again, a bounded number of times, and only while
# nothing of the answer has been shown yet (a partial answer is never replayed). Tools are never retried here.
TRANSIENT_MODEL_ERRORS = {"llm_rate_limited", "llm_unavailable", "llm_timeout", "llm_network", "llm_incomplete",
                          "llm_malformed"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def model_retries() -> int:
    return max(0, min(5, int(_env_float("DESK_LLM_RETRIES", 2))))


def retry_delay(attempt: int, retry_after: float | None) -> float:
    """Exponential backoff with full jitter, or the provider's Retry-After, never above the cap."""
    cap = _env_float("DESK_LLM_RETRY_MAX_WAIT_S", 20.0)
    if retry_after is not None:
        return min(retry_after, cap)
    base = _env_float("DESK_LLM_RETRY_BASE_S", 1.0)
    return min(cap, random.uniform(0, base * (2 ** attempt)))  # noqa: S311 -- backoff jitter, not a secret


# How a run ended; stored in the run's state as ``outcome`` (the status column keeps its existing values)
OUTCOME_FOR_CODE = {"llm_timeout": "timed_out", "too_many_steps": "failed", "answer_incomplete": "incomplete",
                    "answer_empty": "failed"}


# --- figure check -----------------------------------------------------------------------------
# The answer check lives in figures.py (provenance-aware: tool, derived, memory, user, unsupported). The number
# reader below is kept unchanged for the workbook and report evidence in desk_engine/files.

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
    """Figures in ``answer`` that match nothing in ``evidence`` (all of it treated as tool output). Kept for callers
    that have one undivided evidence list; the agent loop uses ``figures.report`` with the sources kept apart."""
    from .figures import report
    return report(answer, evidence, [], [])["unverified_figures"]


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
    # what the user said before the window still matters (a fiscal year end, a unit, a constraint): their own earlier
    # messages are carried in one short note instead of being dropped; tool results and answers are not
    if rows and len(rows) >= HISTORY_MESSAGES:
        older = engine.db.all("SELECT content FROM messages WHERE thread_id = ? AND role = 'user' AND created_at < ? "
                              "ORDER BY created_at DESC LIMIT ?", (thread_id, rows[0]["created_at"], EARLIER_USER_MESSAGES))
        if older:
            lines, used = [], 0
            for r in reversed(older):
                text = " ".join((r["content"] or "").split())[:EARLIER_USER_CHARS]
                if used + len(text) > EARLIER_NOTE_CHARS:
                    break
                lines.append(f"- {text}")
                used += len(text)
            if lines:
                msgs.insert(0, ChatMessage("user", "[Earlier in this conversation: the user's own messages that no longer "
                                                   "fit, oldest first. They are what the user said, not new requests.]\n"
                                           + "\n".join(lines)))
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
                   attachments: list[dict[str, Any]] | None = None,
                   state_extra: dict[str, Any] | None = None) -> str:
        """``state_extra`` adds keys to the run's persisted state (``tool_scope``, ``extra_instructions``); it never
        overrides ``step``, ``parent`` or ``source``."""
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
                       dumps({**(state_extra or {}), "step": 0, "parent": parent, "source": source}), ts, ts))
            c.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (ts, thread_id))
        self.engine.events.start_run(thread_id)
        self.engine.events.publish(thread_id, {"type": "message", "message": self._message_out(msg_id)})
        self._spawn(thread_id, run_id)
        return run_id

    def _spawn(self, thread_id: str, run_id: str) -> None:
        task = asyncio.get_running_loop().create_task(self._run_safely(run_id))
        self.tasks[thread_id] = task

        def release(done: asyncio.Task[None], tid: str = thread_id) -> None:
            # a finished run's task is dropped, so the registry holds running conversations only
            if self.tasks.get(tid) is done:
                del self.tasks[tid]
        task.add_done_callback(release)

    async def wait(self, thread_id: str) -> None:
        task = self.tasks.get(thread_id)
        if task:
            await task

    async def decide(self, workspace: dict[str, Any], approval_id: str, user_id: str, role: str,
                     approve: bool, note: str | None = None) -> dict[str, Any]:
        """A person decides (see ``desk_engine.approvals``); a run waiting on it resumes once nothing is pending."""
        appr = self.engine.approvals.decide(workspace, approval_id, user_id, role, approve, note)
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
        return self.engine.approvals.out(approval_id)

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
            self._fail(run, exc.message, code=exc.code)
        except asyncio.CancelledError:
            self._set_outcome(run_id, "cancelled")
            raise
        except Exception as exc:  # pragma: no cover - logged
            log.exception("agent run %s failed", run_id)
            self._fail(run, f"Unexpected error: {type(exc).__name__}", code="internal")
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
        r["outcome"] = state.get("outcome") or {"done": "completed", "error": "failed"}.get(r["status"])
        r["error_code"] = state.get("error_code")
        return r

    def _set_outcome(self, run_id: str, outcome: str, code: str | None = None) -> None:
        row = self.engine.db.one("SELECT state FROM agent_runs WHERE id = ?", (run_id,))
        if row is None:
            return
        state = loads(row["state"], {})
        state["outcome"] = outcome
        if code:
            state["error_code"] = code
        self.engine.db.execute("UPDATE agent_runs SET state = ? WHERE id = ?", (dumps(state), run_id))

    def _fail(self, run: dict[str, Any], message: str, code: str | None = None) -> None:
        ts = now()
        msg_id = new_id("m_")
        outcome = OUTCOME_FOR_CODE.get(code or "", "failed")
        with self.engine.db.tx() as c:
            c.execute("UPDATE agent_runs SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
                      (message, ts, run["id"]))
            c.execute("INSERT INTO messages (id, thread_id, role, content, payload, created_at) VALUES (?,?,?,?,?,?)",
                      (msg_id, run["thread_id"], "event", message,
                       dumps({"kind": "error", "run_id": run["id"], "code": code, "outcome": outcome}), ts))
        self._set_outcome(run["id"], outcome, code)
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
        if state.get("tool_scope"):
            # a run started through the embedded gateway is limited to the read-only tool set of its agent; the
            # scope is part of the persisted run state, so it holds across approval pauses and restarts too
            from ..embedded.scope import ToolScope
            tools = ToolScope.from_state(state["tool_scope"]).apply(tools)
        usage = Usage(**loads(run["usage"], {})) if run["usage"] and run["usage"] != "{}" else Usage()

        # 1. finish pending tool calls decided by approvers
        await self._resolve_pending(ctx, run, state, tools)

        provider_id, model_name = resolve_choice(engine, workspace, thread["provider_id"], thread["model"])
        model = build_model(engine, workspace["id"], provider_id, model_name)
        skills = enabled_index(engine, workspace["id"])
        pinned = []
        for name in loads(thread["skills"], []):
            # deny by default: only a skill that is enabled in this workspace right now reaches the instructions; a
            # disabled, unapproved, deleted or unknown one is left out and the refusal is recorded
            skill = get_enabled_skill(engine, workspace["id"], str(name))
            if skill is None:
                engine.audit(workspace["id"], run["user_id"], "skill.pin_refused", str(name)[:120],
                             {"thread_id": thread["id"]}, resource_type="skill", decision="denied", result="refused",
                             request_id=run["id"])
                continue
            pinned.append(skill)
        from ..data.store import list_datasets

        connections = engine.db.all("SELECT id, provider, label FROM connections WHERE workspace_id = ? AND "
                                    "status = 'connected'", (workspace["id"],))
        datasets = list_datasets(engine, workspace["id"])
        system, dynamic = system_parts(workspace, skills=skills, pinned=pinned, connections=connections,
                                     datasets=datasets,
                                     extra=state.get("extra_instructions"), knowledge=self._knowledge_note(workspace["id"]),
                                     language=engine.db.scalar("SELECT language FROM users WHERE id = ?", (run["user_id"],)))
        context = workspace_context(connections=connections, datasets=datasets, collections=[
            r["name"] for r in engine.db.all("SELECT name FROM doc_collections WHERE workspace_id = ? ORDER BY name",
                                             (workspace["id"],))])
        recent = [r["content"] or "" for r in engine.db.all(
            "SELECT content FROM messages WHERE thread_id = ? AND role = 'user' ORDER BY created_at DESC LIMIT 3",
            (thread["id"],))]
        memories = engine.memory.relevant(workspace["id"], run["user_id"], recent)
        mem_block = engine.memory.prompt_block(memories)
        if mem_block:
            dynamic += "\n\n" + mem_block

        step = int(state.get("step", 0))
        seen: dict[tuple[str, str], int] = {}          # identical read calls in this run segment
        last_output: dict[tuple[str, str], ToolOutput] = {}
        while step < MAX_STEPS:
            step += 1
            past = history(engine, thread["id"])
            groups = select_groups(past, tools, has_datasets=bool(datasets), extra=state.get("extra_instructions") or "")
            chosen = choose_tools(tools, groups)
            specs = [t.spec() for t in chosen]
            more = available_groups(tools, groups)
            note = ("\n\nMore tool groups can be loaded with more_tools: " + ", ".join(more) + ".") if more else ""
            messages = [ChatMessage("system", system), ChatMessage("system", dynamic + note),
                        *([ChatMessage("user", context)] if context else []), *compact(past)]
            msg_id = new_id("m_")
            result, timing = await self._call_model(model, messages, specs, thread["id"], msg_id, model_name)
            usage.add(result.usage)
            engine.record_usage(workspace["id"], provider_id, model_name, result.usage, "chat")
            payload: dict[str, Any] = {"run_id": run["id"], "model": model_name, "provider_id": provider_id,
                                       "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                                      for c in result.tool_calls],
                                       "timing": timing, "finish": result.finish_reason}
            if not result.tool_calls and not result.text.strip():
                raise AppError("The model returned an empty answer. Nothing was stored as an answer; try again.",
                               code="answer_empty")
            if not result.tool_calls and result.finish_reason in ("length", "content_filter"):
                # the text is kept for reference, marked incomplete, and the run does not end as a finished answer
                payload.update(incomplete=True)
                engine.db.insert("messages", {"id": msg_id, "thread_id": thread["id"], "role": "assistant",
                                              "author_id": None, "content": result.text, "payload": dumps(payload),
                                              "created_at": now()})
                engine.events.publish(thread["id"], {"type": "message", "message": self._message_out(msg_id)})
                why = ("was cut off by the model's output limit" if result.finish_reason == "length"
                       else "was stopped by the provider's content filter")
                raise AppError(f"The answer {why} before it was finished. It is kept above, marked incomplete; do not "
                               "rely on it. Ask for a shorter answer or split the question.", code="answer_incomplete")
            if not result.tool_calls:
                from .figures import report
                payload.update(report(result.text, [m.content for m in past if m.role == "tool"],
                                      [m.content for m in past if m.role == "user"], [m["content"] for m in memories]))
                if memories:
                    payload["memories"] = [{"id": m["id"], "content": m["content"], "scope": m["scope"]} for m in memories]
                    engine.memory.mark_used([m["id"] for m in memories])
            ts = now()
            engine.db.insert("messages", {"id": msg_id, "thread_id": thread["id"], "role": "assistant",
                                          "author_id": None, "content": result.text, "payload": dumps(payload),
                                          "created_at": ts})
            engine.events.publish(thread["id"], {"type": "message", "message": self._message_out(msg_id)})

            if not result.tool_calls:
                state.update(step=step, final_text=result.text, pending=[], outcome="completed")
                engine.db.execute("UPDATE agent_runs SET status = 'done', state = ?, usage = ?, updated_at = ? "
                                  "WHERE id = ?", (dumps(state), dumps(usage.__dict__), now(), run["id"]))
                engine.db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now(), thread["id"]))
                return

            pending: list[dict[str, Any]] = []
            for call in result.tool_calls:
                tool = tools.get(call.name)
                if tool is not None and not tool.needs_approval():
                    key = (call.name, dumps(call.arguments))
                    seen[key] = seen.get(key, 0) + 1
                    if seen[key] > REPEAT_LIMIT and key in last_output:
                        # the same read with the same arguments again in this run: answered from its own result,
                        # without running the tool again (bounded loops, no extra load); a write is never cached
                        prev = last_output[key]
                        self._tool_message(ctx, call, ToolOutput(
                            f"Same call with the same arguments as before in this turn (call {seen[key]}); the result is "
                            "unchanged and repeated below. Use it, or answer, instead of calling it again.\n\n" + prev.text,
                            prev.blocks, prev.data), "ok")
                        continue
                if tool is None:
                    engine.audit(ctx.ws_id, ctx.user_id, "tool.denied", call.name[:120], {"reason": "unknown_or_not_offered"},
                                 resource_type="tool", decision="denied", result="refused", request_id=ctx.run_id)
                    self._tool_message(ctx, call, ToolOutput(f"Unknown tool '{call.name}'."), "error")
                    continue
                if tool.needs_approval():
                    approval_id = self._request_approval(ctx, tool, call)
                    pending.append({"call": {"id": call.id, "name": call.name, "arguments": call.arguments},
                                    "approval_id": approval_id})
                    continue
                out = await self._execute(ctx, tool, call)
                if not tool.needs_approval():
                    last_output[(call.name, dumps(call.arguments))] = out
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

    async def _call_model(self, model: Any, messages: list[ChatMessage], specs: list[Any], thread_id: str, msg_id: str,
                          model_name: str) -> tuple[Any, dict[str, Any]]:
        """One model call, streamed to the thread. A transient failure before any text was shown is retried with
        backoff (``model_retries()`` times at most); after text was shown, or for a permanent error, it is raised.
        Returns the result and its timing (milliseconds to the first event and to the end, and attempts)."""
        attempts = 0
        started = time.perf_counter()
        while True:
            attempts += 1
            shown = False
            first: float | None = None
            result = None
            self.engine.events.publish(thread_id, {"type": "assistant_start", "message_id": msg_id, "model": model_name})
            try:
                async for ev in model.stream(messages, specs):
                    if first is None:
                        first = time.perf_counter()
                    if ev.type == "text":
                        shown = True
                        self.engine.events.publish(thread_id, {"type": "delta", "message_id": msg_id, "text": ev.delta})
                    else:
                        result = ev.result
            except AppError as exc:
                if exc.code in TRANSIENT_MODEL_ERRORS and not shown and attempts <= model_retries():
                    wait = retry_delay(attempts - 1, (exc.details or {}).get("retry_after")
                                       if isinstance(exc.details, dict) else None)
                    log.warning("model call failed (%s), retry %d in %.1fs", exc.code, attempts, wait)
                    await asyncio.sleep(wait)
                    continue
                raise
            if result is None:
                raise AppError("The model's response ended without a result.", code="llm_incomplete")
            end = time.perf_counter()
            return result, {"ttfb_ms": round(((first or end) - started) * 1000, 1),
                            "total_ms": round((end - started) * 1000, 1), "attempts": attempts}

    def _knowledge_note(self, ws_id: str) -> str | None:
        n = self.engine.db.scalar("SELECT COUNT(*) FROM documents WHERE workspace_id = ? AND status = 'indexed'", (ws_id,))
        if not n:
            return None
        colls = self.engine.db.scalar("SELECT COUNT(*) FROM doc_collections WHERE workspace_id = ?", (ws_id,))
        note = f"Knowledge base: {n} indexed documents"
        if colls:
            note += f" in {colls} collections (named in the workspace context message)"
        return note + ". Use search_documents for anything written in them."

    async def _resolve_pending(self, ctx: ToolContext, run: dict[str, Any], state: dict[str, Any],
                               tools: dict[str, Tool]) -> None:
        pending = state.get("pending") or []
        if not pending:
            return
        for item in pending:
            call = ToolCall(item["call"]["id"], item["call"]["name"], item["call"]["arguments"])
            appr = self.engine.db.one("SELECT status FROM approvals WHERE id = ?", (item["approval_id"],))
            status = appr["status"] if appr else "rejected"
            if status == "approved":
                tool = tools.get(call.name)
                if tool is None:
                    self._tool_message(ctx, call, ToolOutput("The tool is no longer available."), "error")
                    continue
                # the approval is taken atomically for this execution: once, unexpired, for this workspace, tool,
                # requester and exactly these arguments, by a requester who still holds the role
                refused = self.engine.approvals.claim(approval_id=item["approval_id"], workspace_id=ctx.ws_id,
                                                      tool=call.name, requested_by=ctx.user_id,
                                                      arguments=call.arguments, executor=ctx.user_id)
                if refused:
                    self._tool_message(ctx, call, ToolOutput(f"Refused: the approval for this action is {refused} "
                                                             "and cannot be used. Ask the user again if needed."),
                                       "rejected", approval_id=item["approval_id"])
                    continue
                approver_ctx = ToolContext(ctx.engine, ctx.workspace, ctx.user_id, ctx.thread_id, ctx.run_id, ctx.source)
                output = await self._execute(approver_ctx, tool, call, approval_id=item["approval_id"])
                self.engine.approvals.finish(item["approval_id"], ok=not output.text.startswith("Error:"), text=output.text)
            elif status == "expired":
                self._tool_message(ctx, call, ToolOutput("The approval for this action expired before anyone decided. "
                                                         "Ask the user again if it is still wanted."), "rejected",
                                   approval_id=item["approval_id"])
            else:
                self._tool_message(ctx, call, ToolOutput("The user rejected this action. Do not retry it unless "
                                                         "they ask; offer an alternative."), "rejected",
                                   approval_id=item["approval_id"])
        state["pending"] = []
        self.engine.db.execute("UPDATE agent_runs SET state = ? WHERE id = ?", (dumps(state), run["id"]))

    def _request_approval(self, ctx: ToolContext, tool: Tool, call: ToolCall) -> str:
        approval_id = self.engine.approvals.request(workspace_id=ctx.ws_id, source="agent", source_id=ctx.run_id or "",
                                                    tool=call.name, arguments=call.arguments,
                                                    summary=tool.describe_call(call.arguments), requested_by=ctx.user_id,
                                                    request_id=ctx.run_id)
        self.engine.events.publish(ctx.thread_id or "", {"type": "approval", "approval": self.approval_out(approval_id)})
        self.engine.notify_role(ctx.ws_id, "editor", "approval", f"Approval needed: {tool.describe_call(call.arguments)}",
                                f"/inbox?approval={approval_id}", exclude=None)
        return approval_id

    async def _execute(self, ctx: ToolContext, tool: Tool, call: ToolCall, approval_id: str | None = None) -> ToolOutput:
        self.engine.events.publish(ctx.thread_id or "", {"type": "tool_start", "call": {"id": call.id, "name": call.name,
                                                                                        "arguments": call.arguments}})
        output = await tool.run(ctx, call.arguments)
        refused = output.text.startswith("Refused:")
        status = "error" if output.text.startswith("Error:") else ("refused" if refused else "ok")
        self.engine.audit(ctx.ws_id, ctx.user_id, "tool.denied" if refused else "tool.run", call.name,
                          {"source": ctx.source, "effect": "write" if tool.needs_approval() else "read",
                           "min_role": tool.min_role, "error": output.text[:200] if status != "ok" else None},
                          resource_type="tool", decision="denied" if refused else ("approved" if approval_id else "allowed"),
                          approval_id=approval_id, result=status, request_id=ctx.run_id)
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

