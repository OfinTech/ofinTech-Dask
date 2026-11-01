"""One embedded run from request to the end of its event stream."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..db import loads, now
from ..errors import AppError, Conflict, Forbidden, Invalid, NotFound
from . import agui
from .agents import EmbeddedAgent, get_agent
from .auth import EmbeddedAuth, EmbeddedPrincipal
from .metering import Entitlements, LimitEntitlements, Metering
from .model import Integrations

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.embedded")

KEEPALIVE_SECONDS = 15
RUN_TIMEOUT_SECONDS = 15 * 60   # a stream is closed after this long whatever the run is doing
STREAM_IDLE_LIMIT = 10 * 60     # no event at all for this long → the run is considered stuck


class Embedded:
    """``engine.embedded``: the services behind the embedded routes."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.integrations = Integrations(engine)
        self.auth = EmbeddedAuth(engine, self.integrations)
        self.metering = Metering(engine)
        self.entitlements: Entitlements = LimitEntitlements(self.metering)

    # -- helpers -------------------------------------------------------------------------------------------------
    def agent_for(self, principal: EmbeddedPrincipal, agent_id: str) -> EmbeddedAgent:
        agent = get_agent(agent_id)
        if agent is None:
            raise NotFound("Unknown agent.", code="agent_not_found")
        if agent.capability not in principal.capabilities:
            self.engine.audit(principal.workspace["id"], principal.user["id"], "embedded.denied", principal.integration["id"],
                              {"reason": "capability_missing", "agent": agent_id, "capability": agent.capability},
                              resource_type="embedded", decision="denied", result="refused")
            raise Forbidden(f"This integration does not have the {agent.capability} capability.", code="capability_missing")
        return agent

    def desk_url(self) -> str:
        return self.engine.settings.public_url or ""

    # -- a run -----------------------------------------------------------------------------------------------------
    async def run(self, principal: EmbeddedPrincipal, agent: EmbeddedAgent, inp: agui.RunAgentInput,
                  request_id: str | None) -> AsyncIterator[bytes]:
        """Starts the turn and streams AG-UI events until the run ends. Errors before the run starts are raised as
        ``AppError`` (plain JSON responses); errors after are ``RUN_ERROR`` frames."""
        engine = self.engine
        ws = principal.workspace
        integration = principal.integration
        limits = principal.limits
        started = time.monotonic()

        # 1. per-run rate limits and entitlements
        if self.auth.limiter.limited(f"runs:sess:{principal.session['id']}", int(limits["runs_per_minute_session"])):
            raise AppError("Too many runs on this session; slow down.", code="rate_limited", status=429)
        if self.auth.limiter.limited(f"runs:int:{integration['id']}", int(limits["runs_per_minute"])):
            raise AppError("Too many runs for this integration; slow down.", code="rate_limited", status=429)
        decision = self.entitlements.check_run(principal, agent.id)
        if not decision.allowed:
            engine.audit(ws["id"], principal.user["id"], "embedded.denied", integration["id"],
                         {"reason": decision.code, "limit": decision.limit, "used": decision.used, "agent": agent.id},
                         resource_type="embedded", decision="denied", result="refused")
            raise Forbidden(decision.message or "Not entitled.", code=decision.code)

        # 2. input
        try:
            text = inp.last_user_text().strip()
        except ValueError as exc:
            raise Invalid(str(exc), code="unsupported_content") from None
        if not text and not inp.resume:
            raise Invalid("The last user message is empty.", code="empty_message")
        if len(text) > int(limits["max_input_chars"]):
            raise Invalid(f"The message is longer than {limits['max_input_chars']} characters.", code="message_too_long")
        ignored = inp.ignored_fields()
        if ignored:
            engine.audit(ws["id"], principal.user["id"], "embedded.input.ignored_fields", integration["id"],
                         {"fields": ignored[:20], "agent": agent.id}, resource_type="embedded", decision="ignored", result="ok")
        context_text = inp.context_text()
        turn_text = text + ("\n\n" + context_text if context_text else "")

        # 3. the Desk thread behind the partner thread
        thread = self.integrations.thread_for(integration, principal.session, inp.threadId)

        # 4. open interrupts on this thread (approvals of a waiting run)
        waiting = engine.db.one("SELECT * FROM agent_runs WHERE thread_id = ? AND status = 'waiting_approval'", (thread["id"],))
        pre_error: dict[str, Any] | None = None
        translator_seed = agui.Translator(thread_id=inp.threadId, run_id=inp.runId, desk_run_id="", agent_id=agent.id,
                                          currency=ws.get("base_currency"), locale=inp.locale() or principal.session["locale"],
                                          desk_url=self.desk_url(), workspace_id=ws["id"])
        if waiting is not None:
            pre_error = await self._handle_resume(principal, thread, waiting, inp, translator_seed)
            if pre_error is None and inp.resume and not text:
                # a resume with nothing new to say: report the state of the waiting run and stop
                async def only_state() -> AsyncIterator[bytes]:
                    for ev in translator_seed.started(inp.state):
                        yield agui.sse(ev)
                    run_now = engine.agent.run_out(waiting["id"])
                    run_now["_interrupts"] = self._interrupts(waiting, translator_seed)
                    for ev in translator_seed.translate({"type": "run", "run": run_now}):
                        yield agui.sse(ev)
                return only_state()
        elif inp.resume:
            pre_error = translator_seed.error("There is no open interrupt on this thread.", code="interrupt_unknown")

        if pre_error is not None:
            async def failed() -> AsyncIterator[bytes]:
                for ev in translator_seed.started(inp.state):
                    yield agui.sse(ev)
                yield agui.sse(pre_error)
            self.metering.record(principal, run_id=None, agent_id=agent.id, status="refused",
                                 duration_ms=int((time.monotonic() - started) * 1000))
            return failed()

        # 5. subscribe before starting so the replay buffer is not needed for the first events
        hub = engine.events
        queue = hub.subscribe(thread["id"])
        try:
            desk_run_id = engine.agent.start_turn(
                ws, principal.user["id"], thread["id"], turn_text, source="embedded",
                state_extra={"tool_scope": agent.scope.to_state(), "extra_instructions": agent.instructions,
                             "embedded": {"integration_id": integration["id"], "agent_id": agent.id,
                                          "session_id": principal.session["id"], "client_run_id": inp.runId[:200]}})
        except Conflict:
            hub.unsubscribe(thread["id"], queue)
            raise Conflict("The assistant is still working on the previous message in this conversation.", code="thread_busy")
        except Exception:
            hub.unsubscribe(thread["id"], queue)
            raise
        engine.audit(ws["id"], principal.user["id"], "embedded.run.start", desk_run_id,
                     {"integration_id": integration["id"], "agent": agent.id, "thread_id": thread["id"],
                      "client_thread_id": inp.threadId[:200], "client_run_id": inp.runId[:200],
                      "subject_hash": principal.session["subject_hash"][:16], "chars": len(text), "context_items": len(inp.context)},
                     resource_type="embedded", decision="allowed", result="started", request_id=request_id)
        translator = agui.Translator(thread_id=inp.threadId, run_id=inp.runId, desk_run_id=desk_run_id, agent_id=agent.id,
                                     currency=ws.get("base_currency"), locale=inp.locale() or principal.session["locale"],
                                     desk_url=self.desk_url(), workspace_id=ws["id"])
        return self._stream(principal, agent, thread, desk_run_id, translator, queue, inp.state, started, request_id)

    async def _stream(self, principal: EmbeddedPrincipal, agent: EmbeddedAgent, thread: dict[str, Any], desk_run_id: str,
                      translator: agui.Translator, queue: asyncio.Queue[Any], client_state: Any, started: float,
                      request_id: str | None) -> AsyncIterator[bytes]:
        engine = self.engine
        hub = engine.events
        status = "error"
        try:
            for ev in translator.started(client_state):
                yield agui.sse(ev)
            deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
            last_event = time.monotonic()
            while not translator.finished:
                if time.monotonic() > deadline or time.monotonic() - last_event > STREAM_IDLE_LIMIT:
                    self._cancel(thread["id"], "The run took too long and was stopped.")
                    yield agui.sse(translator.error("The run took too long and was stopped.", code="run_timeout"))
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                except TimeoutError:
                    if queue not in hub._subs.get(thread["id"], ()):   # dropped as a slow consumer
                        yield agui.sse(translator.error("The event stream fell behind and was closed; re-attach to continue.",
                                                        code="stream_dropped"))
                        break
                    yield agui.keepalive()
                    continue
                last_event = time.monotonic()
                desk_event = item if isinstance(item, dict) else {}
                if desk_event.get("type") == "run":
                    run = dict(desk_event.get("run") or {})
                    if run.get("id") and run.get("id") != desk_run_id:
                        continue   # an older run's terminal event replayed from the buffer
                    if run.get("status") == "waiting_approval":
                        row = engine.db.one("SELECT * FROM agent_runs WHERE id = ?", (desk_run_id,))
                        run["_interrupts"] = self._interrupts(row, translator) if row else []
                    desk_event = {"type": "run", "run": run}
                for ev in translator.translate(desk_event):
                    yield agui.sse(ev)
            status = "done" if translator.finished and translator.final_text is not None else self._run_status(desk_run_id)
        except asyncio.CancelledError:
            # the client went away: stop the work it asked for, as the Desk UI's stop button would
            self._cancel(thread["id"], "The embedded client disconnected.")
            status = "cancelled"
            raise
        finally:
            hub.unsubscribe(thread["id"], queue)
            row = engine.db.one("SELECT usage, status FROM agent_runs WHERE id = ?", (desk_run_id,))
            usage = loads(row["usage"], {}) if row else {}
            if row and status not in {"cancelled"}:
                status = row["status"]
            self.metering.record(principal, run_id=desk_run_id, agent_id=agent.id, status=status,
                                 input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0),
                                 tool_calls=translator.tool_call_count, duration_ms=int((time.monotonic() - started) * 1000))
            engine.audit(principal.workspace["id"], principal.user["id"], "embedded.run.finish", desk_run_id,
                         {"integration_id": principal.integration["id"], "agent": agent.id, "status": status,
                          "tool_calls": translator.tool_call_count, "duration_ms": int((time.monotonic() - started) * 1000)},
                         resource_type="embedded", decision="allowed", result=status, request_id=request_id)

    async def attach(self, principal: EmbeddedPrincipal, agent_id: str, run_id: str, *, client_thread_id: str,
                     client_run_id: str) -> AsyncIterator[bytes]:
        """Re-attach to a run: a finished run is replayed from the database, a live one from the hub's buffer."""
        engine = self.engine
        run = self.integrations.owns_run(principal.integration, principal.session, run_id)
        if run is None:
            raise NotFound("Run not found.")
        agent = self.agent_for(principal, agent_id)
        ws = principal.workspace
        translator = agui.Translator(thread_id=client_thread_id, run_id=client_run_id, desk_run_id=run_id, agent_id=agent.id,
                                     currency=ws.get("base_currency"), locale=principal.session["locale"],
                                     desk_url=self.desk_url(), workspace_id=ws["id"])
        hub = engine.events
        if run["status"] in {"running", "waiting_approval"} and run["thread_id"] in hub._replay:
            queue = hub.subscribe(run["thread_id"])
            return self._stream(principal, agent, {"id": run["thread_id"]}, run_id, translator, queue, None, time.monotonic(), None)

        async def replay() -> AsyncIterator[bytes]:
            for ev in translator.started(None):
                yield agui.sse(ev)
            rows = engine.db.all("SELECT * FROM messages WHERE thread_id = ? AND created_at >= ? ORDER BY created_at",
                                 (run["thread_id"], run["created_at"]))
            for m in rows:
                payload = loads(m["payload"], {})
                if payload.get("run_id") != run_id and m["role"] != "user":
                    continue
                if m["role"] == "assistant":
                    for ev in translator.translate({"type": "assistant_start", "message_id": m["id"]}):
                        yield agui.sse(ev)
                    if m["content"]:
                        yield agui.sse(agui.event(agui.TEXT_MESSAGE_CONTENT, messageId=m["id"], delta=m["content"]))
                m["payload"] = payload
                for ev in translator.translate({"type": "message", "message": m}):
                    yield agui.sse(ev)
            out = engine.agent.run_out(run_id)
            if out["status"] == "waiting_approval":
                out["_interrupts"] = self._interrupts(run, translator)
            for ev in translator.translate({"type": "run", "run": out}):
                yield agui.sse(ev)
        return replay()

    def _run_status(self, run_id: str) -> str:
        row = self.engine.db.one("SELECT status FROM agent_runs WHERE id = ?", (run_id,))
        return row["status"] if row else "error"

    def _cancel(self, thread_id: str, reason: str) -> None:
        engine = self.engine
        task = engine.agent.tasks.get(thread_id)
        if task and not task.done():
            task.cancel()
        engine.db.execute("UPDATE agent_runs SET status = 'error', error = ?, updated_at = ? WHERE thread_id = ? "
                          "AND status IN ('running','waiting_approval')", (reason, now(), thread_id))
        engine.db.execute("UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND source = 'agent' AND "
                          "source_id IN (SELECT id FROM agent_runs WHERE thread_id = ?)", (thread_id,))
        engine.events.publish(thread_id, {"type": "run", "run": {"status": "error", "error": reason}})

    def cancel_run(self, principal: EmbeddedPrincipal, run_id: str) -> None:
        run = self.integrations.owns_run(principal.integration, principal.session, run_id)
        if run is None:
            raise NotFound("Run not found.")
        self._cancel(run["thread_id"], "Stopped by the embedded client.")
        self.engine.audit(principal.workspace["id"], principal.user["id"], "embedded.run.cancel", run_id,
                          {"integration_id": principal.integration["id"]}, resource_type="embedded", decision="allowed", result="cancelled")

    # -- approvals / interrupts ---------------------------------------------------------------------------------
    def _interrupts(self, run: dict[str, Any], translator: agui.Translator) -> list[dict[str, Any]]:
        state = loads(run["state"], {})
        out = []
        for item in state.get("pending") or []:
            appr = self.engine.db.one("SELECT * FROM approvals WHERE id = ? AND status = 'pending'", (item.get("approval_id"),))
            if appr:
                out.append(translator.interrupt_for(appr, (item.get("call") or {}).get("id")))
        return out

    async def _handle_resume(self, principal: EmbeddedPrincipal, thread: dict[str, Any], waiting: dict[str, Any],
                             inp: agui.RunAgentInput, translator: agui.Translator) -> dict[str, Any] | None:
        """Applies ``resume`` to the waiting run through the existing approvals engine. Returns a RUN_ERROR event
        when the specification says the resume is invalid, or when the engine refuses the decision."""
        state = loads(waiting["state"], {})
        open_ids = {item.get("approval_id") for item in state.get("pending") or []}
        if not inp.resume:
            return translator.error("This conversation is waiting for a decision; send resume items for the open interrupts.",
                                    code="resume_required", interrupts=sorted(i for i in open_ids if i))
        seen: set[str] = set()
        for item in inp.resume:
            if item.interruptId not in open_ids:
                return translator.error(f"Unknown interrupt {item.interruptId}.", code="interrupt_unknown")
            seen.add(item.interruptId)
            appr = self.engine.db.one("SELECT * FROM approvals WHERE id = ?", (item.interruptId,))
            if appr is None or appr["status"] != "pending" or (appr["expires_at"] and appr["expires_at"] < now()):
                return translator.error("This interrupt has expired or was already decided.", code="interrupt_expired")
            if item.status == "cancelled":
                continue
            payload = item.payload or {}
            if not isinstance(payload.get("approved"), bool):
                return translator.error("resume.payload.approved must be a boolean.", code="resume_invalid")
            note = payload.get("note")
            if note is not None and (not isinstance(note, str) or len(note) > 500):
                return translator.error("resume.payload.note must be a string of at most 500 characters.", code="resume_invalid")
            # the decision goes through the engine as the service user (a viewer). Deciding needs the editor role, so
            # the engine refuses and audits approval.denied — approvals are decided inside Desk, by design.
            try:
                from ..agent.tools import ToolContext
                role = ToolContext(self.engine, principal.workspace, principal.user["id"]).role
                await self.engine.agent.decide(principal.workspace, item.interruptId, principal.user["id"], role,
                                               payload["approved"], note)
            except Forbidden:
                return translator.error("Approvals are decided inside OfinTech Desk by a workspace editor.",
                                        code="approval_requires_desk_editor",
                                        approval_url=f"{self.desk_url().rstrip('/')}/w/{principal.workspace['id']}/approvals/{item.interruptId}")
            except AppError as exc:
                return translator.error(exc.message, code=exc.code)
        if open_ids - seen:
            return translator.error("Every open interrupt needs a resume item.", code="resume_required",
                                    interrupts=sorted(i for i in open_ids - seen if i))
        if any(item.status == "cancelled" for item in inp.resume):
            self._cancel(thread["id"], "Cancelled by the embedded client.")
        return None
