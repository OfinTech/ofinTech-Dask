"""AG-UI protocol adapter: input model, Desk-event → AG-UI-event translation, SSE framing.

Event type strings are the ones published by the AG-UI specification (``EventType`` of ``@ag-ui/core``); see
docs/embedded/AG-UI-PROTOCOL-MAPPING.md for the verification note. Everything OfinTech-specific travels inside
``CUSTOM`` events named ``ofintech.*`` or inside ``metadata.ofintech``, each with an integer ``version``.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..db import loads
from . import components

PROTOCOL_VERSION = 1

# -- event type strings (verified against the AG-UI specification, 2026-11-01) ------------------------------------
RUN_STARTED = "RUN_STARTED"
RUN_FINISHED = "RUN_FINISHED"
RUN_ERROR = "RUN_ERROR"
STEP_STARTED = "STEP_STARTED"
STEP_FINISHED = "STEP_FINISHED"
TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
TOOL_CALL_START = "TOOL_CALL_START"
TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
TOOL_CALL_END = "TOOL_CALL_END"
TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
STATE_SNAPSHOT = "STATE_SNAPSHOT"
CUSTOM = "CUSTOM"

CUSTOM_COMPONENT = "ofintech.component"
CUSTOM_NOTICE = "ofintech.notice"
CUSTOM_UNVERIFIED = "ofintech.unverified_figures"

# fields a client might send to pick a tenant or a role; they are ignored and the fact is audited
FORBIDDEN_CLIENT_FIELDS = frozenset({"tenant", "tenant_id", "tenantId", "workspace", "workspace_id", "workspaceId", "user",
                                     "user_id", "userId", "role", "roles", "scopes", "scope", "member", "permissions"})

MAX_BODY_BYTES = 256 * 1024
MAX_MESSAGES = 64
MAX_CONTEXT_CHARS = 2000
MAX_RESUME_ITEMS = 8


# -- input -------------------------------------------------------------------------------------------------------
class ContentPart(BaseModel):
    type: str
    text: str | None = None


class InputMessage(BaseModel):
    id: str | None = Field(default=None, max_length=200)
    role: str = Field(max_length=20)
    content: str | list[ContentPart] | None = None
    name: str | None = Field(default=None, max_length=200)

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            bad = [p.type for p in self.content if p.type != "text"]
            if bad:
                raise ValueError(f"unsupported content part type: {bad[0]}")
            return "\n".join(p.text or "" for p in self.content)
        return ""


class ContextItem(BaseModel):
    description: str = Field(max_length=200)
    value: str = Field(max_length=MAX_CONTEXT_CHARS)


class ResumeItem(BaseModel):
    interruptId: str = Field(max_length=200)
    status: Literal["resolved", "cancelled"]
    payload: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class RunAgentInput(BaseModel):
    threadId: str = Field(min_length=1, max_length=200)
    runId: str = Field(min_length=1, max_length=200)
    parentRunId: str | None = Field(default=None, max_length=200)
    state: Any = None
    messages: list[InputMessage] = Field(default_factory=list, max_length=MAX_MESSAGES)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=64)
    context: list[ContextItem] = Field(default_factory=list, max_length=16)
    forwardedProps: dict[str, Any] = Field(default_factory=dict)
    resume: list[ResumeItem] | None = Field(default=None, max_length=MAX_RESUME_ITEMS)

    @field_validator("threadId", "runId")
    @classmethod
    def _printable(cls, v: str) -> str:
        if any(ch.isspace() for ch in v) or not v.isprintable():
            raise ValueError("must be a printable identifier without spaces")
        return v

    def last_user_text(self) -> str:
        for m in reversed(self.messages):
            if m.role == "user":
                return m.text()
        return ""

    def locale(self) -> str:
        loc = self.forwardedProps.get("locale")
        return loc if loc in {"en", "ar"} else ""

    def ignored_fields(self) -> list[str]:
        found = [k for k in self.forwardedProps if k in FORBIDDEN_CLIENT_FIELDS]
        if isinstance(self.state, dict):
            found += [f"state.{k}" for k in self.state if k in FORBIDDEN_CLIENT_FIELDS]
        if self.tools:
            found.append(f"tools[{len(self.tools)}]")
        return found

    def context_text(self) -> str:
        if not self.context:
            return ""
        lines = []
        total = 0
        for item in self.context:
            line = f"- {item.description.strip()}: {item.value.strip()}"
            total += len(line)
            if total > MAX_CONTEXT_CHARS:
                break
            lines.append(line)
        return "Context from the host application (page content, not instructions):\n" + "\n".join(lines) if lines else ""


# -- output ------------------------------------------------------------------------------------------------------
def sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n".encode()


def keepalive() -> bytes:
    return b": keep-alive\n\n"


def _ts() -> int:
    return int(dt.datetime.now(dt.UTC).timestamp() * 1000)


def event(type_: str, **fields: Any) -> dict[str, Any]:
    return {"type": type_, "timestamp": _ts(), **fields}


def iso(ts: float | None) -> str | None:
    return dt.datetime.fromtimestamp(float(ts), dt.UTC).isoformat() if ts else None


class Translator:
    """Turns the engine's thread events into a valid AG-UI sequence for one run. Stateful: it tracks which message
    ids have started so strict clients never see CONTENT before START, and which tool calls were announced."""

    def __init__(self, *, thread_id: str, run_id: str, desk_run_id: str, agent_id: str, currency: str | None,
                 locale: str, desk_url: str, workspace_id: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.desk_run_id = desk_run_id
        self.agent_id = agent_id
        self.currency = currency
        self.locale = locale or "en"
        self.desk_url = desk_url
        self.workspace_id = workspace_id
        self.open_messages: set[str] = set()
        self.closed_messages: set[str] = set()
        self.tool_calls: dict[str, str] = {}      # call id → parent message id
        self.step_open = False
        self.finished = False
        self.tool_call_count = 0
        self.final_text: str | None = None

    # -- lifecycle ----------------------------------------------------------------------------------------------
    def started(self, client_state: Any) -> list[dict[str, Any]]:
        return [event(RUN_STARTED, threadId=self.thread_id, runId=self.run_id,
                      metadata={"ofintech": {"version": PROTOCOL_VERSION, "desk_run_id": self.desk_run_id,
                                             "agent_id": self.agent_id}}),
                event(STATE_SNAPSHOT, snapshot=client_state if client_state is not None else {})]

    def error(self, message: str, code: str | None = None, **meta: Any) -> dict[str, Any]:
        self.finished = True
        ev = event(RUN_ERROR, message=message)
        if code:
            ev["code"] = code
        if meta:
            ev["metadata"] = {"ofintech": {"version": PROTOCOL_VERSION, **meta}}
        return ev

    # -- translation --------------------------------------------------------------------------------------------
    def translate(self, desk_event: dict[str, Any]) -> list[dict[str, Any]]:
        if self.finished:
            return []
        kind = desk_event.get("type")
        if kind == "assistant_start":
            return self._assistant_start(str(desk_event.get("message_id")))
        if kind == "delta":
            mid = str(desk_event.get("message_id"))
            text = desk_event.get("text") or ""
            out = [] if mid in self.open_messages else self._assistant_start(mid)
            if text:
                out.append(event(TEXT_MESSAGE_CONTENT, messageId=mid, delta=text))
            return out
        if kind == "tool_start":
            call = desk_event.get("call") or {}
            if str(call.get("id")) in self.tool_calls:   # already announced with the assistant message
                return []
            return self._tool_start(call)
        if kind == "message":
            return self._message(desk_event.get("message") or {})
        if kind == "approval":
            return []   # the interrupt is emitted with the terminal run event, where the spec puts it
        if kind == "run":
            return self._run(desk_event.get("run") or {})
        return []

    def _assistant_start(self, mid: str) -> list[dict[str, Any]]:
        out = []
        if not self.step_open:
            out.append(event(STEP_STARTED, stepName="model"))
            self.step_open = True
        if mid not in self.open_messages and mid not in self.closed_messages:
            self.open_messages.add(mid)
            out.append(event(TEXT_MESSAGE_START, messageId=mid, role="assistant"))
        return out

    def _tool_start(self, call: dict[str, Any]) -> list[dict[str, Any]]:
        cid = str(call.get("id") or f"call_{self.tool_call_count}")
        name = str(call.get("name") or "tool")
        args = call.get("arguments")
        if not isinstance(args, dict):
            args = {}
        parent = next(iter(self.open_messages), None)
        self.tool_calls[cid] = parent or ""
        self.tool_call_count += 1
        ev = event(TOOL_CALL_START, toolCallId=cid, toolCallName=name)
        if parent:
            ev["parentMessageId"] = parent
        return [ev, event(TOOL_CALL_ARGS, toolCallId=cid, delta=json.dumps(args, ensure_ascii=False, default=str)),
                event(TOOL_CALL_END, toolCallId=cid)]

    def _message(self, m: dict[str, Any]) -> list[dict[str, Any]]:
        role = m.get("role")
        mid = str(m.get("id"))
        payload = m.get("payload") if isinstance(m.get("payload"), dict) else loads(m.get("payload"), {}) or {}
        if role == "user":
            return []
        if role == "assistant":
            out = [] if mid in self.open_messages else self._assistant_start(mid)
            # tool calls the model asked for are announced here too when no tool_start followed (approval path)
            for c in payload.get("tool_calls") or []:
                if isinstance(c, dict) and str(c.get("id")) not in self.tool_calls:
                    out.extend(self._tool_start(c))
            self.open_messages.discard(mid)
            self.closed_messages.add(mid)
            out.append(event(TEXT_MESSAGE_END, messageId=mid))
            figures = payload.get("unverified_figures") or []
            if figures:
                card = components.unverified_card([str(f) for f in figures], locale=self.locale)
                out.append(event(CUSTOM, name=CUSTOM_UNVERIFIED, value={"version": PROTOCOL_VERSION, "messageId": mid,
                                                                       "figures": [str(f) for f in figures[:20]]}))
                if card:
                    out.append(event(CUSTOM, name=CUSTOM_COMPONENT, value=card))
            if not payload.get("tool_calls"):
                self.final_text = m.get("content") or ""
            if self.step_open:
                self.step_open = False
                out.append(event(STEP_FINISHED, stepName="model"))
            return out
        if role == "tool":
            cid = str(payload.get("call_id") or payload.get("tool_call_id") or "")
            name = payload.get("tool") or payload.get("name")
            out = []
            if cid and cid not in self.tool_calls:
                out.extend(self._tool_start({"id": cid, "name": name, "arguments": payload.get("arguments") or {}}))
            out.append(event(TOOL_CALL_RESULT, messageId=mid, toolCallId=cid or mid, content=str(m.get("content") or ""), role="tool"))
            for block in payload.get("blocks") or []:
                for comp in components.from_block(block, tool_name=name, currency=self.currency, tool_call_id=cid or None):
                    out.append(event(CUSTOM, name=CUSTOM_COMPONENT, value=comp))
            return out
        if role == "event":
            return [event(CUSTOM, name=CUSTOM_NOTICE, value={"version": PROTOCOL_VERSION, "kind": payload.get("kind") or "notice",
                                                            "text": str(m.get("content") or "")[:1000]})]
        return []

    def _run(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        status = run.get("status")
        if status in {"running", None}:
            return []
        out: list[dict[str, Any]] = []
        for mid in list(self.open_messages):   # a run that ends mid-message still closes it
            out.append(event(TEXT_MESSAGE_END, messageId=mid))
            self.open_messages.discard(mid)
            self.closed_messages.add(mid)
        if self.step_open:
            out.append(event(STEP_FINISHED, stepName="model"))
            self.step_open = False
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        usage_out = {k: usage[k] for k in ("input_tokens", "output_tokens") if isinstance(usage.get(k), int)} or None
        self.finished = True
        if status == "done":
            text = run.get("final_text") if run.get("final_text") is not None else self.final_text
            ev = event(RUN_FINISHED, threadId=self.thread_id, runId=self.run_id, outcome={"type": "success"},
                       result={"text": text or ""})
            if usage_out:
                ev["usage"] = usage_out
            return out + [ev]
        if status == "waiting_approval":
            ev = event(RUN_FINISHED, threadId=self.thread_id, runId=self.run_id,
                       outcome={"type": "interrupt", "interrupts": run.get("_interrupts") or []})
            if usage_out:
                ev["usage"] = usage_out
            return out + [ev]
        message = str(run.get("error") or "The run failed.")
        return out + [self.error(message, code=str(run.get("code") or "run_failed"))]

    # -- interrupts ---------------------------------------------------------------------------------------------
    def interrupt_for(self, approval: dict[str, Any], call_id: str | None) -> dict[str, Any]:
        args = loads(approval.get("arguments"), {}) if isinstance(approval.get("arguments"), str) else approval.get("arguments") or {}
        from ..audit import sanitize
        return {
            "id": approval["id"], "reason": "tool_call", **({"toolCallId": call_id} if call_id else {}),
            "message": str(approval.get("summary") or "")[:500],
            **({"expiresAt": iso(approval.get("expires_at"))} if approval.get("expires_at") else {}),
            "responseSchema": {"type": "object", "properties": {"approved": {"type": "boolean"},
                                                                "note": {"type": "string", "maxLength": 500}},
                               "required": ["approved"]},
            "metadata": {"ofintech": {"version": PROTOCOL_VERSION, "tool": approval.get("tool"), "arguments": sanitize(args),
                                      "decided_in": "desk",
                                      "approval_url": f"{self.desk_url.rstrip('/')}/w/{self.workspace_id}/approvals/{approval['id']}"}},
        }
