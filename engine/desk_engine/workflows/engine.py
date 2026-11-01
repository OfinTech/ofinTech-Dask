"""Durable workflows: a trigger and an ordered list of steps; every run is stored, pauses at
approvals and resumes where it stopped (also after a restart)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..agent.tools import ToolContext
from ..db import dumps, loads, new_id, now
from ..errors import AppError, Invalid, NotFound
from .cron import Cron
from .template import render

if TYPE_CHECKING:
    from ..app import Engine

log = logging.getLogger("desk.workflows")


# --- definition ----------------------------------------------------------------------------------


class ManualTrigger(BaseModel):
    type: Literal["manual"] = "manual"


class ScheduleTrigger(BaseModel):
    type: Literal["schedule"]
    cron: str
    timezone: str | None = None

    @field_validator("cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        Cron(v)
        return v


class EmailTrigger(BaseModel):
    type: Literal["email"]
    query: str = Field(min_length=1, max_length=500, description="Gmail search, e.g. 'from:bank.com has:attachment'")
    connection_id: str | None = None


class ChannelTrigger(BaseModel):
    type: Literal["channel_command"]
    keyword: str = Field(pattern=r"^/?[a-zA-Z][a-zA-Z0-9_]{1,30}$")


class WebhookTrigger(BaseModel):
    """Started by another system: POST /api/hooks/workflows/{id} with the workflow's secret."""
    type: Literal["webhook"]


Trigger = Annotated[ManualTrigger | ScheduleTrigger | EmailTrigger | ChannelTrigger | WebhookTrigger,
                    Field(discriminator="type")]


class StepBase(BaseModel):
    """Drafts may be saved with empty fields; ``problems()`` lists what must be filled in before the workflow can
    be switched on or run."""
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    name: str = Field(default="", max_length=120)
    retries: int = Field(default=0, ge=0, le=5, description="tries again after a failure (tool, file, notify steps)")
    retry_delay: float = Field(default=30, ge=0, le=3600, description="seconds before the first retry; doubles each time")
    on_error: Literal["fail", "continue"] = "fail"


class AgentStep(StepBase):
    type: Literal["agent"]
    prompt: str = Field(default="", max_length=8000)
    skills: list[str] = Field(default_factory=list)
    provider_id: str | None = None
    model: str | None = None


class ToolStep(StepBase):
    type: Literal["tool"]
    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)


class ApprovalStep(StepBase):
    type: Literal["approval"]
    message: str = Field(default="", max_length=4000)


class ConditionStep(StepBase):
    type: Literal["condition"]
    left: str = ""
    op: Literal["gt", "gte", "lt", "lte", "eq", "ne", "contains", "empty", "not_empty"]
    right: str = ""
    on_false: Literal["stop", "skip_next"] = "stop"


class NotifyStep(StepBase):
    type: Literal["notify"]
    channels: list[Literal["in_app", "email", "telegram", "whatsapp"]] = Field(default_factory=lambda: ["in_app"])
    recipients: Literal["me", "admins", "editors", "everyone"] = "me"
    message: str = Field(default="", max_length=4000)


class FileStep(StepBase):
    """Refresh an evidence-linked file with the latest data, or apply edits to the user's own Excel/Word file."""
    type: Literal["file"]
    file_id: str = ""
    action: Literal["refresh", "edit"] = "refresh"
    edits: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    note: str = Field(default="", max_length=300)


Step = Annotated[AgentStep | ToolStep | ApprovalStep | ConditionStep | NotifyStep | FileStep,
                 Field(discriminator="type")]


def problems(definition: "WorkflowDef") -> list[dict[str, str]]:
    """What still has to be filled in, as {field, message} items the app shows next to the steps."""
    out: list[dict[str, str]] = []
    for i, s in enumerate(definition.steps):
        label = f"Step {i + 1}" + (f" “{s.name.strip()}”" if s.name.strip() else "")
        field = f"steps.{i}"
        if not s.name.strip():
            out.append({"field": f"{field}.name", "message": f"{label}: give the step a name."})
        if isinstance(s, AgentStep) and not s.prompt.strip():
            out.append({"field": f"{field}.prompt", "message": f"{label}: write what the assistant should do."})
        elif isinstance(s, ToolStep) and not s.tool.strip():
            out.append({"field": f"{field}.tool", "message": f"{label}: choose a tool."})
        elif isinstance(s, (ApprovalStep, NotifyStep)) and not s.message.strip():
            what = "what the approver is asked to check" if isinstance(s, ApprovalStep) else "the message to send"
            out.append({"field": f"{field}.message", "message": f"{label}: write {what}."})
        elif isinstance(s, FileStep) and not s.file_id.strip():
            out.append({"field": f"{field}.file_id", "message": f"{label}: choose the file."})
        elif isinstance(s, FileStep) and s.action == "edit" and not s.edits:
            out.append({"field": f"{field}.edits", "message": f"{label}: add the edits to make."})
        elif isinstance(s, ConditionStep) and not s.left.strip():
            out.append({"field": f"{field}.left", "message": f"{label}: say which value to check, e.g. "
                                                               "{{steps.step1.data.total}}."})
    return out


def require_complete(definition: "WorkflowDef", action: str) -> None:
    issues = problems(definition)
    if issues:
        raise Invalid(f"Finish the workflow before you {action} it: " + " ".join(i["message"] for i in issues[:5]),
                      code="workflow_incomplete", details=issues)


class WorkflowDef(BaseModel):
    trigger: Trigger = Field(default_factory=ManualTrigger)
    steps: list[Step] = Field(min_length=1, max_length=30)

    @field_validator("steps")
    @classmethod
    def _unique(cls, steps: list[Any]) -> list[Any]:
        ids = [s.id for s in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        return steps


def compare(left: Any, op: str, right: Any) -> bool:
    def num(x: Any) -> float | None:
        try:
            return float(str(x).replace(",", ""))
        except (TypeError, ValueError):
            return None

    if op == "empty":
        return left in (None, "", [], {})
    if op == "not_empty":
        return left not in (None, "", [], {})
    if op == "contains":
        return str(right).lower() in str(left).lower()
    ln, rn = num(left), num(right)
    if op in {"gt", "gte", "lt", "lte"}:
        if ln is None or rn is None:
            return False
        return {"gt": ln > rn, "gte": ln >= rn, "lt": ln < rn, "lte": ln <= rn}[op]
    same = (ln == rn) if (ln is not None and rn is not None) else str(left).strip() == str(right).strip()
    return same if op == "eq" else not same


# --- runtime --------------------------------------------------------------------------------------


class Workflows:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def get(self, ws_id: str, wf_id: str) -> dict[str, Any]:
        row = self.engine.db.one("SELECT * FROM workflows WHERE id = ? AND workspace_id = ?", (wf_id, ws_id))
        if row is None:
            raise NotFound("Workflow not found.")
        row["definition"] = loads(row["definition"], {})
        row["webhook"] = bool(row.pop("webhook_hash", None))
        return row

    def next_run(self, definition: dict[str, Any], workspace: dict[str, Any]) -> float | None:
        trig = definition.get("trigger", {})
        if trig.get("type") != "schedule":
            return None
        tz = trig.get("timezone") or workspace.get("timezone") or "UTC"
        return Cron(trig["cron"]).next_after(datetime.now(UTC), tz).timestamp()

    def save(self, workspace: dict[str, Any], user_id: str, name: str, description: str, definition: WorkflowDef,
             enabled: bool, wf_id: str | None = None) -> dict[str, Any]:
        self._validate_tools(definition)
        if enabled:
            require_complete(definition, "switch on")
        d = definition.model_dump()
        nxt = self.next_run(d, workspace) if enabled else None
        ts = now()
        if wf_id:
            n = self.engine.db.update("workflows", wf_id, {"name": name, "description": description, "definition": dumps(d),
                                                           "enabled": int(enabled), "next_run_at": nxt, "updated_at": ts},
                                      workspace_id=workspace["id"])
            if not n:
                raise NotFound("Workflow not found.")
        else:
            wf_id = new_id("wf_")
            self.engine.db.insert("workflows", {"id": wf_id, "workspace_id": workspace["id"], "name": name,
                                                "description": description, "definition": dumps(d),
                                                "enabled": int(enabled), "next_run_at": nxt, "created_by": user_id,
                                                "created_at": ts, "updated_at": ts})
        return self.get(workspace["id"], wf_id)

    def _validate_tools(self, definition: WorkflowDef) -> None:
        static = {t.name for t in self.engine.tools.static_tools()}
        for s in definition.steps:
            if isinstance(s, ToolStep) and s.tool and s.tool not in static and not s.tool.startswith("mcp_"):
                raise Invalid(f"Step '{s.name}': unknown tool '{s.tool}'.")

    # -- runs ------------------------------------------------------------------------------------
    def start(self, workspace: dict[str, Any], wf: dict[str, Any], trigger: dict[str, Any], started_by: str) -> str:
        definition = wf.get("definition")
        if isinstance(definition, dict):
            require_complete(WorkflowDef.model_validate(definition), "run")
        run_id = new_id("wr_")
        context = {"trigger": trigger, "steps": {}, "workspace": {"name": workspace["name"],
                                                                  "currency": workspace.get("base_currency")},
                   "today": datetime.now(UTC).date().isoformat(), "workflow": {"name": wf["name"]}}
        self.engine.db.insert("workflow_runs", {"id": run_id, "workflow_id": wf["id"], "workspace_id": workspace["id"],
                                                "trigger": dumps(trigger), "status": "running", "step_index": 0,
                                                "context": dumps(context), "log": "[]", "started_by": started_by,
                                                "started_at": now()})
        self.engine.audit(workspace["id"], started_by, "workflow.run", wf["id"], {"run_id": run_id,
                                                                                  "trigger": trigger.get("type")})
        self._spawn(run_id)
        return run_id

    def _may_act(self, ws_id: str, user_id: str) -> bool:
        """The creator is still an active member with at least the editor role (the role workflows need to be
        created); checked before every step, so a demotion or removal stops a run in flight."""
        row = self.engine.db.one("SELECT m.role FROM members m JOIN users u ON u.id = m.user_id WHERE m.workspace_id = ? "
                                 "AND m.user_id = ? AND u.disabled = 0", (ws_id, user_id))
        return row is not None and row["role"] != "viewer"

    def _spawn(self, run_id: str) -> None:
        self.tasks[run_id] = asyncio.get_running_loop().create_task(self._advance_safely(run_id))

    def run_out(self, ws_id: str, run_id: str) -> dict[str, Any]:
        r = self.engine.db.one("SELECT * FROM workflow_runs WHERE id = ? AND workspace_id = ?", (run_id, ws_id))
        if r is None:
            raise NotFound("Run not found.")
        for k in ("trigger", "context", "log"):
            r[k] = loads(r[k], {} if k != "log" else [])
        wf = self.engine.db.one("SELECT definition FROM workflows WHERE id = ?", (r["workflow_id"],))
        r["timeline"] = timeline(loads(wf["definition"], {}) if wf else {}, r)
        return r

    def _log(self, run: dict[str, Any], step: str, message: str, status: str = "info", ms: float | None = None,
             **extra: Any) -> None:
        log_items = loads(run["log"], [])
        item = {"at": now(), "step": step, "status": status, "message": message[:4000]} | extra
        if ms is not None:
            item["ms"] = round(ms)
        log_items.append(item)
        run["log"] = dumps(log_items)
        self.engine.db.execute("UPDATE workflow_runs SET log = ? WHERE id = ?", (run["log"], run["id"]))

    def _finish(self, run: dict[str, Any], status: str, error: str | None = None) -> None:
        self.engine.db.execute("UPDATE workflow_runs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                               (status, error, now(), run["id"]))
        wf = self.engine.db.one("SELECT * FROM workflows WHERE id = ?", (run["workflow_id"],))
        self.engine.audit(run["workspace_id"], run.get("started_by"), f"workflow.run.{status}", run["workflow_id"],
                          {"run_id": run["id"], "name": wf["name"] if wf else None, "error": error})
        if wf and status in {"failed"}:
            self.engine.notify(run["workspace_id"], wf["created_by"], "workflow",
                               f"Workflow '{wf['name']}' failed: {error}", f"/workflows/{wf['id']}?run={run['id']}")
        if status in {"done", "failed", "stopped", "rejected"}:
            asyncio.get_running_loop().create_task(self.engine.channels.on_workflow_finished(run["id"]))

    async def _advance_safely(self, run_id: str) -> None:
        try:
            await self._advance(run_id)
        except AppError as exc:
            run = self.engine.db.one("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
            if run:
                if not getattr(exc, "logged", False):
                    self._log(run, "-", exc.message, "error")
                self._finish(run, "failed", exc.message)
        except Exception as exc:  # pragma: no cover
            log.exception("workflow run %s failed", run_id)
            run = self.engine.db.one("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
            if run:
                self._finish(run, "failed", f"Unexpected error: {type(exc).__name__}")

    async def _advance(self, run_id: str) -> None:
        engine = self.engine
        run = engine.db.one("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
        if run is None or run["status"] != "running":
            return
        wf = engine.db.one("SELECT * FROM workflows WHERE id = ?", (run["workflow_id"],))
        workspace = engine.db.one("SELECT * FROM workspaces WHERE id = ?", (run["workspace_id"],))
        if wf is None or workspace is None:
            return
        definition = WorkflowDef.model_validate(loads(wf["definition"], {}))
        context = loads(run["context"], {})
        owner = wf["created_by"]
        # the run acts as the workflow's creator; a creator who left the workspace or whose account was disabled
        # can no longer act, so the run stops here rather than with that person's old rights
        if not self._may_act(workspace["id"], owner):
            self._log(run, "-", "The workflow's creator is no longer an active member of this workspace; the run stops.", "error")
            self._finish(run, "failed", "The workflow's creator is no longer an active member of this workspace.")
            return
        index = run["step_index"]
        while index < len(definition.steps):
            step = definition.steps[index]
            if step.type == "agent":
                thread_id = new_id("t_")
                ts = now()
                engine.db.insert("threads", {"id": thread_id, "workspace_id": workspace["id"],
                                             "title": f"{wf['name']} · {step.name}"[:120], "created_by": owner,
                                             "visibility": "workspace", "provider_id": step.provider_id,
                                             "model": step.model, "skills": dumps(step.skills),
                                             "channel": dumps({"type": "workflow", "run_id": run_id}),
                                             "created_at": ts, "updated_at": ts})
                engine.db.execute("UPDATE workflow_runs SET status = 'waiting', step_index = ? WHERE id = ?",
                                  (index, run_id))
                self._log(run, step.id, f"Agent started in conversation {thread_id}.")
                engine.agent.start_turn(workspace, owner, thread_id, str(render(step.prompt, context)),
                                        parent={"type": "workflow_run", "id": run_id, "step": index}, source="workflow")
                return
            if step.type == "approval":
                approval_id = engine.approvals.request(workspace_id=workspace["id"], source="workflow", source_id=run_id,
                                                       tool="workflow.approval", arguments={"step": index},
                                                       summary=f"{wf['name']}: {render(step.message, context)}",
                                                       requested_by=owner, request_id=run_id)
                engine.db.execute("UPDATE workflow_runs SET status = 'waiting_approval', step_index = ? WHERE id = ?",
                                  (index, run_id))
                engine.notify_role(workspace["id"], "editor", "approval", f"Approval needed: {wf['name']} – {step.name}",
                                   f"/inbox?approval={approval_id}")
                self._log(run, step.id, "Waiting for approval.")
                return
            if step.type == "tool":
                ctx = ToolContext(engine, workspace, owner, None, run_id, "workflow")
                tools = await engine.tools.for_context(ctx)
                tool = tools.get(step.tool)
                if tool is None:
                    raise Invalid(f"Tool '{step.tool}' is not available (is the connection still set up?).")
                args = render(step.arguments, context)
                if tool.needs_approval():
                    approval_id = engine.approvals.request(workspace_id=workspace["id"], source="workflow", source_id=run_id,
                                                           tool=step.tool, arguments=args, summary=tool.describe_call(args),
                                                           requested_by=owner, request_id=run_id)
                    context.setdefault("pending_args", {})[str(index)] = args
                    engine.db.execute("UPDATE workflow_runs SET status = 'waiting_approval', step_index = ?, "
                                      "context = ? WHERE id = ?", (index, dumps(context), run_id))
                    engine.notify_role(workspace["id"], "editor", "approval",
                                       f"Approval needed: {tool.describe_call(args)[:200]}", f"/inbox?approval={approval_id}")
                    self._log(run, step.id, f"Waiting for approval to run {step.tool}.")
                    return


                async def call_tool(tool: Any = tool, args: Any = args, ctx: ToolContext = ctx) -> dict[str, Any]:
                    out = await tool.run(ctx, args)
                    if out.text.startswith("Error:"):
                        raise Invalid(f"Step '{step.name}': {out.text}")
                    return {"output": out.text, "data": out.data}

                await self._attempt(run, step, context, call_tool)
            elif step.type == "file":
                await self._attempt(run, step, context, lambda: self._file_step(workspace, owner, step, context))
            elif step.type == "condition":
                left = render(step.left, context)
                right = render(step.right, context)
                ok = compare(left, step.op, right)
                self._log(run, step.id, f"{left!r} {step.op} {right!r} → {ok}", "ok")
                context["steps"][step.id] = {"output": ok}
                if not ok:
                    if step.on_false == "stop":
                        engine.db.execute("UPDATE workflow_runs SET context = ? WHERE id = ?", (dumps(context), run_id))
                        self._finish(run, "stopped", None)
                        return
                    index += 1
            elif step.type == "notify":
                message = str(render(step.message, context))

                async def send(message: str = message, step: Any = step) -> dict[str, Any]:
                    sent = await self._notify(workspace, wf, owner, step, message)
                    return {"output": message, "data": sent, "log": f"Notified: {', '.join(sent) or 'nobody'}"}

                await self._attempt(run, step, context, send)
            index += 1
            engine.db.execute("UPDATE workflow_runs SET step_index = ?, context = ? WHERE id = ?",
                              (index, dumps(context), run_id))
        self._finish(run, "done")

    async def _attempt(self, run: dict[str, Any], step: Any, context: dict[str, Any], fn: Any) -> None:
        """Run one step with its retry policy; record the output and how long it took."""
        delay = step.retry_delay
        for attempt in range(step.retries + 1):
            started = time.monotonic()
            try:
                result = await fn()
            except AppError as exc:
                ms = (time.monotonic() - started) * 1000
                if attempt < step.retries:
                    self._log(run, step.id, f"Attempt {attempt + 1} failed: {exc.message} Trying again in "
                                            f"{delay:g} s.", "retry", ms)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 3600)
                    continue
                if step.on_error == "continue":
                    context["steps"][step.id] = {"output": "", "error": exc.message}
                    self._log(run, step.id, f"Failed, continuing: {exc.message}", "error", ms)
                    return
                self._log(run, step.id, exc.message, "error", ms)
                exc.logged = True  # type: ignore[attr-defined]
                raise
            ms = (time.monotonic() - started) * 1000
            note = result.pop("log", None)
            context["steps"][step.id] = result
            self._log(run, step.id, (note or str(result.get("output") or ""))[:500], "ok", ms,
                      **({"attempt": attempt + 1} if attempt else {}))
            return

    async def _file_step(self, workspace: dict[str, Any], owner: str, step: FileStep,
                         context: dict[str, Any]) -> dict[str, Any]:
        files = self.engine.files
        file_id = str(render(step.file_id, context)).strip()
        if step.action == "refresh":
            out = await files.refresh(workspace, owner, file_id)
        else:
            out = await files.edit_upload(workspace, owner, file_id, render(step.edits, context), thread_id=None,
                                          note=str(render(step.note, context)) or None)
        f, s = out["file"], out["model"].get("summary", {})
        unverified = s.get("cells_unverified", s.get("unverified", 0))
        text = f"Saved “{f['name']}” version {out['version']}" + (f"; {unverified} figure(s) without a source"
                                                                  if unverified else "") + "."
        return {"output": text, "data": {"file_id": f["id"], "version": out["version"], "name": f["name"],
                                         "link": files.app_url(workspace["id"])(f"files/{f['id']}"),
                                         "unverified": unverified}}

    async def _notify(self, workspace: dict[str, Any], wf: dict[str, Any], owner: str, step: NotifyStep,
                      message: str) -> list[str]:
        """Notifications go only to members of the workspace (their app inbox, their own e-mail
        address via the connected Gmail, or their linked Telegram/WhatsApp chat)."""
        from ..web import ROLES

        engine = self.engine
        if step.recipients == "me":
            members = engine.db.all("SELECT u.* FROM users u WHERE u.id = ?", (owner,))
        else:
            min_role = {"admins": "admin", "editors": "editor", "everyone": "viewer"}[step.recipients]
            allowed = ROLES[ROLES.index(min_role):]
            marks = ",".join("?" for _ in allowed)
            members = engine.db.all(f"SELECT u.* FROM users u JOIN members m ON m.user_id = u.id WHERE m.workspace_id = ? "
                                    f"AND m.role IN ({marks}) AND u.disabled = 0", (workspace["id"], *allowed))
        sent: list[str] = []
        title = f"{wf['name']}: {message[:180]}"
        for u in members:
            if "in_app" in step.channels:
                engine.notify(workspace["id"], u["id"], "workflow", title, f"/workflows/{wf['id']}")
                sent.append(f"app:{u['email']}")
            if "email" in step.channels and not u["is_local"]:
                try:
                    from ..connectors.google import GoogleClient, build_raw_email
                    from ..connectors.oauth import get_connection

                    g = GoogleClient(engine, get_connection(engine, workspace["id"], None, "google"))
                    await g.send(build_raw_email(sender=None, to=u["email"], subject=f"[OfinTech Desk] {wf['name']}",
                                                 body=message))
                    sent.append(f"email:{u['email']}")
                except AppError as exc:
                    log.warning("workflow e-mail failed: %s", exc.message)
            for channel in ("telegram", "whatsapp"):
                if channel in step.channels:
                    n = await engine.channels.send_to_member(workspace["id"], u["id"], channel, message)
                    if n:
                        sent.append(f"{channel}:{u['email']}")
        return sent

    # -- callbacks -------------------------------------------------------------------------------
    async def on_agent_done(self, agent_run: dict[str, Any], parent: dict[str, Any]) -> None:
        run = self.engine.db.one("SELECT * FROM workflow_runs WHERE id = ?", (parent["id"],))
        if run is None:
            return
        wf = self.engine.db.one("SELECT * FROM workflows WHERE id = ?", (run["workflow_id"],))
        definition = WorkflowDef.model_validate(loads(wf["definition"], {})) if wf else None
        if definition is None:
            return
        step = definition.steps[parent["step"]]
        if agent_run["status"] == "waiting_approval":
            self.engine.db.execute("UPDATE workflow_runs SET status = 'waiting_approval' WHERE id = ?", (run["id"],))
            self._log(run, step.id, "The agent is waiting for an approval.")
            return
        if agent_run["status"] == "error":
            self._log(run, step.id, agent_run.get("error") or "Agent failed.", "error")
            if step.on_error == "continue":
                context = loads(run["context"], {})
                context["steps"][step.id] = {"output": "", "error": agent_run.get("error")}
                self.engine.db.execute("UPDATE workflow_runs SET status = 'running', step_index = ?, context = ? "
                                       "WHERE id = ?", (parent["step"] + 1, dumps(context), run["id"]))
                self._spawn(run["id"])
                return
            self._finish(run, "failed", agent_run.get("error"))
            return
        if agent_run["status"] != "done":
            return
        state = loads(agent_run["state"], {})
        context = loads(run["context"], {})
        context["steps"][step.id] = {"output": state.get("final_text", ""), "thread_id": agent_run["thread_id"]}
        self._log(run, step.id, (state.get("final_text") or "")[:500], "ok")
        self.engine.db.execute("UPDATE workflow_runs SET status = 'running', step_index = ?, context = ? WHERE id = ?",
                               (parent["step"] + 1, dumps(context), run["id"]))
        self._spawn(run["id"])

    async def on_approval(self, approval: dict[str, Any], approved: bool) -> None:
        run = self.engine.db.one("SELECT * FROM workflow_runs WHERE id = ?", (approval["source_id"],))
        if run is None or run["status"] != "waiting_approval":
            return
        index = int(loads(approval["arguments"], {}).get("step", run["step_index"])) \
            if approval["tool"] == "workflow.approval" else run["step_index"]
        wf = self.engine.db.one("SELECT * FROM workflows WHERE id = ?", (run["workflow_id"],))
        definition = WorkflowDef.model_validate(loads(wf["definition"], {}))
        step = definition.steps[index]
        if not approved:
            self._log(run, step.id, "Rejected.", "error")
            self._finish(run, "rejected", "An approver rejected this step.")
            return
        context = loads(run["context"], {})
        if approval["tool"] != "workflow.approval":
            workspace = self.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (run["workspace_id"],))
            if not self._may_act(run["workspace_id"], wf["created_by"]):
                self._finish(run, "failed", "The workflow's creator is no longer an active member of this workspace.")
                return
            ctx = ToolContext(self.engine, workspace, wf["created_by"], None, run["id"], "workflow")
            tools = await self.engine.tools.for_context(ctx)
            tool = tools.get(approval["tool"])
            if tool is None:
                self._finish(run, "failed", f"Tool {approval['tool']} is no longer available.")
                return
            args = loads(approval["arguments"], {})
            refused = self.engine.approvals.claim(approval_id=approval["id"], workspace_id=run["workspace_id"],
                                                  tool=approval["tool"], requested_by=wf["created_by"], arguments=args,
                                                  executor=approval.get("decided_by"))
            if refused:
                self._finish(run, "failed", f"The approval for {approval['tool']} is {refused} and cannot be used.")
                return
            out = await tool.run(ctx, args)
            self.engine.approvals.finish(approval["id"], ok=not out.text.startswith("Error:"), text=out.text)
            self.engine.audit(run["workspace_id"], approval.get("decided_by"), "tool.run", approval["tool"],
                              {"source": "workflow", "effect": "write"}, resource_type="tool", decision="approved",
                              approval_id=approval["id"], result="error" if out.text.startswith("Error:") else "ok",
                              request_id=run["id"])
            context["steps"][step.id] = {"output": out.text, "data": out.data}
            self._log(run, step.id, out.text[:500], "ok")
        else:
            refused = self.engine.approvals.claim(approval_id=approval["id"], workspace_id=run["workspace_id"],
                                                  tool="workflow.approval", requested_by=wf["created_by"],
                                                  arguments={"step": index}, executor=approval.get("decided_by"))
            if refused:
                self._finish(run, "failed", f"The approval for this step is {refused} and cannot be used.")
                return
            self.engine.approvals.finish(approval["id"], ok=True, text="approved")
            self._log(run, step.id, "Approved.", "ok")
        self.engine.db.execute("UPDATE workflow_runs SET status = 'running', step_index = ?, context = ? WHERE id = ?",
                               (index + 1, dumps(context), run["id"]))
        self._spawn(run["id"])

    async def resume_interrupted(self) -> None:
        """After a restart, continue runs that were executing plain steps."""
        for r in self.engine.db.all("SELECT id FROM workflow_runs WHERE status = 'running'"):
            self._spawn(r["id"])

    # -- triggers --------------------------------------------------------------------------------
    async def tick(self) -> None:
        ts = now()
        due = self.engine.db.all("SELECT * FROM workflows WHERE enabled = 1 AND next_run_at IS NOT NULL AND "
                                 "next_run_at <= ?", (ts,))
        for wf in due:
            workspace = self.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (wf["workspace_id"],))
            if workspace is None:
                continue
            definition = loads(wf["definition"], {})
            self.engine.db.execute("UPDATE workflows SET next_run_at = ? WHERE id = ?",
                                   (self.next_run(definition, workspace), wf["id"]))
            self.start(workspace, {**wf, "definition": definition}, {"type": "schedule", "at": ts}, wf["created_by"])

    async def poll_email(self) -> None:
        from ..connectors.google import GoogleClient
        from ..connectors.oauth import get_connection

        for wf in self.engine.db.all("SELECT * FROM workflows WHERE enabled = 1"):
            definition = loads(wf["definition"], {})
            trig = definition.get("trigger", {})
            if trig.get("type") != "email":
                continue
            key = f"wf_email_cursor:{wf['id']}"
            cursor = loads(self.engine.db.scalar("SELECT value FROM settings WHERE key = ?", (key,)), None)
            started = int(now())
            if cursor is None:
                self._set(key, started)  # first poll: only mail that arrives from now on
                continue
            workspace = self.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (wf["workspace_id"],))
            try:
                client = GoogleClient(self.engine, get_connection(self.engine, wf["workspace_id"],
                                                                  trig.get("connection_id"), "google"))
                messages = await client.search(f"({trig['query']}) after:{int(cursor)}", 10)
            except AppError as exc:
                log.warning("e-mail trigger for %s: %s", wf["id"], exc.message)
                continue
            self._set(key, started)
            for m in reversed(messages):
                payload = {"type": "email", "message_id": m["id"], "from": m["from"], "subject": m["subject"],
                           "snippet": m["snippet"], "attachments": m["attachments"],
                           "text": f"{m['subject']}\n{m['snippet']}"}
                self.start(workspace, {**wf, "definition": definition}, payload, wf["created_by"])

    def _set(self, key: str, value: Any) -> None:
        with self.engine.db.tx() as c:
            c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, dumps(value)))

    def match_channel_command(self, ws_id: str, text: str) -> dict[str, Any] | None:
        word = re.split(r"\s+", text.strip(), maxsplit=1)[0].lstrip("/").lower() if text.strip() else ""
        if not word:
            return None
        for wf in self.engine.db.all("SELECT * FROM workflows WHERE workspace_id = ? AND enabled = 1", (ws_id,)):
            trig = loads(wf["definition"], {}).get("trigger", {})
            if trig.get("type") == "channel_command" and trig["keyword"].lstrip("/").lower() == word:
                return wf
        return None


def timeline(definition: dict[str, Any], run: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per step: what happened, when, how long it took and what it produced."""
    log_items = run["log"] if isinstance(run["log"], list) else loads(run["log"], [])
    outputs = (run["context"] if isinstance(run["context"], dict) else loads(run["context"], {})).get("steps", {})
    rows = []
    for i, step in enumerate(definition.get("steps", [])):
        entries = [e for e in log_items if e.get("step") == step.get("id")]
        status = "pending"
        if entries:
            last = entries[-1]["status"]
            status = {"ok": "done", "error": "failed", "retry": "running"}.get(last, "running")
            if last == "info" and run["status"] == "waiting_approval":
                status = "waiting"
        elif i < run.get("step_index", 0) and run["status"] in {"done", "running", "waiting", "waiting_approval"}:
            status = "skipped"
        if run["status"] in {"stopped", "failed", "rejected"} and status == "pending" and i > run.get("step_index", 0):
            status = "not run"
        started = entries[0]["at"] if entries else None
        finished = entries[-1]["at"] if entries and status in {"done", "failed"} else None
        ms = sum(e.get("ms", 0) for e in entries if "ms" in e) or (
            round((finished - started) * 1000) if started and finished and finished > started else None)
        out = outputs.get(step.get("id"), {})
        rows.append({"id": step.get("id"), "name": step.get("name") or step.get("id"), "type": step.get("type"),
                     "status": status, "started_at": started, "finished_at": finished, "ms": ms,
                     "attempts": sum(1 for e in entries if e["status"] in {"retry", "ok", "error"}) or None,
                     "output": str(out.get("output", ""))[:1500] if out else "", "error": out.get("error"),
                     "thread_id": out.get("thread_id"), "data": out.get("data") if isinstance(out.get("data"), dict) else None})
    return rows


def upcoming(cron: str, tz: str, count: int = 5) -> list[str]:
    """The next few times a schedule fires, as ISO timestamps in its time zone."""
    from zoneinfo import ZoneInfo

    c = Cron(cron)
    at = datetime.now(UTC)
    out = []
    for _ in range(count):
        at = c.next_after(at, tz)
        out.append(at.astimezone(ZoneInfo(tz)).isoformat(timespec="minutes"))
    return out
