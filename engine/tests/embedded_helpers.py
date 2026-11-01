"""Helpers for the embedded gateway suites: an organisation with an owner, a workspace, a fake model, an integration
and a session, plus a reader for AG-UI event streams."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from tests.helpers import FakeLLM, Harness

PW = "Str0ngPassw0rd!"
ORIGIN = "https://erp.partner.test"


def new_harness(script: list[Any] | None = None, **kw: Any) -> Harness:
    # server mode (sessions, step-up auth, origin checks); private URLs allowed so the fake model host resolves
    kw.setdefault("open_signup", True)   # several organisations sign up in one test
    return Harness(mode="server", script=script, allow_private_network_urls=True, **kw)


@dataclass
class Org:
    h: Harness
    ws: str
    owner_token: str
    integration: dict[str, Any] = field(default_factory=dict)
    key: str = ""
    session_token: str = ""
    session_id: str = ""

    def owner(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.owner_token}"}

    def page(self, origin: str = ORIGIN, token: str | None = None) -> dict[str, str]:
        return {"authorization": f"Bearer {token or self.session_token}", "origin": origin}

    def admin_api(self, method: str, path: str, expect: int | None = 200, **kw: Any) -> Any:
        kw.setdefault("headers", {}).update(self.owner())
        return self.h.api(method, path, expect, **kw)


def signup(h: Harness, email: str, name: str = "Owner") -> str:
    r = h.client.post("/api/auth/signup", json={"email": email, "name": name, "password": PW})
    assert r.status_code == 200, r.text
    h.client.cookies.clear()   # the suites speak bearer, not cookie (so no CSRF origin header is needed)
    return r.json()["token"]


def make_org(h: Harness, *, email: str, name: str = "Acme", origins: list[str] | None = None,
             capabilities: list[str] | None = None, with_model: bool = True, integration: bool = True,
             session: bool = True, limits: dict[str, int] | None = None) -> Org:
    token = signup(h, email)
    auth = {"authorization": f"Bearer {token}"}
    ws = h.api("POST", "/api/workspaces", headers=auth, json={"name": name, "base_currency": "SAR", "country": "SA",
                                                                 "fiscal_year_start_month": 1, "accounting_framework": "IFRS",
                                                                 "timezone": "Asia/Riyadh"})["id"]
    if with_model:
        h.api("POST", f"/api/w/{ws}/providers", headers=auth, json={"kind": "openai_compatible", "label": "Fake",
                                                                     "base_url": FakeLLM.BASE, "api_key": "sk-test"})
    org = Org(h, ws, token)
    if integration:
        body: dict[str, Any] = {"name": f"{name} ERP", "allowed_origins": origins or [ORIGIN],
                                "capabilities": capabilities or ["ask_finance", "financial_intelligence", "reconciliation_read"]}
        if limits:
            body["limits"] = limits
        org.integration = h.api("POST", f"/api/w/{ws}/embedded/integrations", headers=auth, json=body)
        org.key = org.integration["key"]
        if session:
            issue_session(org, "user-1")
    return org


def issue_session(org: Org, subject: str, *, locale: str = "en", ttl: int | None = None, expect: int = 200) -> dict[str, Any]:
    body: dict[str, Any] = {"subject": subject, "locale": locale}
    if ttl is not None:
        body["ttl_seconds"] = ttl
    r = org.h.client.post("/api/embedded/sessions", headers={"authorization": f"Bearer {org.key}"}, json=body)
    assert r.status_code == expect, f"{r.status_code}: {r.text[:300]}"
    data = r.json()
    if expect == 200:
        org.session_token = data["token"]
        org.session_id = data["session_id"]
    return data


def run_input(text: str, *, thread: str = "thread-1", run: str = "run-1", **extra: Any) -> dict[str, Any]:
    return {"threadId": thread, "runId": run, "messages": [{"id": "m1", "role": "user", "content": text}], **extra}


def stream(org: Org, body: dict[str, Any], *, agent: str = "finance-copilot", headers: dict[str, str] | None = None,
           expect: int = 200) -> list[dict[str, Any]]:
    """POSTs a run and returns the decoded AG-UI events (empty when the response was not a stream)."""
    events: list[dict[str, Any]] = []
    with org.h.client.stream("POST", f"/api/embedded/agents/{agent}/run", headers=headers or org.page(), json=body) as resp:
        if resp.status_code != expect:
            resp.read()
            raise AssertionError(f"run -> {resp.status_code}: {resp.text[:400]}")
        if resp.status_code != 200:
            resp.read()
            return events
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] + (":" + e["name"] if e["type"] == "CUSTOM" else "") for e in events]


def last(events: list[dict[str, Any]]) -> dict[str, Any]:
    return events[-1]


def audit_actions(h: Harness, prefix: str = "embedded") -> list[dict[str, Any]]:
    return h.engine.db.all("SELECT action, decision, result, detail, workspace_id, user_id FROM audit_log WHERE action LIKE ? "
                           "ORDER BY created_at, rowid", (prefix + "%",))


def assert_valid_sequence(events: list[dict[str, Any]]) -> None:
    """The structural rules of AG-UI that a strict client enforces."""
    assert events, "no events"
    assert events[0]["type"] == "RUN_STARTED", types(events)
    assert events[-1]["type"] in {"RUN_FINISHED", "RUN_ERROR"}, types(events)
    assert sum(1 for e in events if e["type"] in {"RUN_FINISHED", "RUN_ERROR"}) == 1
    open_msgs: set[str] = set()
    started_calls: set[str] = set()
    for e in events:
        t = e["type"]
        if t == "TEXT_MESSAGE_START":
            assert e["messageId"] not in open_msgs
            open_msgs.add(e["messageId"])
        elif t == "TEXT_MESSAGE_CONTENT":
            assert e["messageId"] in open_msgs, "content before start"
            assert e["delta"], "empty delta"
        elif t == "TEXT_MESSAGE_END":
            assert e["messageId"] in open_msgs, "end before start"
            open_msgs.discard(e["messageId"])
        elif t == "TOOL_CALL_START":
            assert e["toolCallId"] not in started_calls, "tool call announced twice"
            started_calls.add(e["toolCallId"])
        elif t in {"TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT"}:
            assert e["toolCallId"] in started_calls, f"{t} before start"
        elif t == "CUSTOM":
            assert e["name"].startswith("ofintech.")
            assert e["value"]["version"] == 1
    assert not open_msgs, f"messages left open: {open_msgs}"
