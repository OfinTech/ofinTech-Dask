"""Test helpers: engine factory, a scripted fake LLM provider and a router for mocked HTTP APIs."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2 as httpx

from desk_engine.app import Engine, create_app
from desk_engine.config import Settings

LOCAL_TOKEN = "t" * 40


def settings(tmp: Path, mode: str = "desktop", **kw: Any) -> Settings:
    base = dict(
        mode=mode, data_dir=tmp, host="127.0.0.1", port=0,
        public_url=kw.pop("public_url", "http://localhost:47821" if mode == "desktop" else "https://desk.example.com"),
        site_url=kw.pop("site_url", ""),
        master_key=secrets.token_bytes(32), local_token=LOCAL_TOKEN, open_signup=False,
        allow_stdio_mcp=mode == "desktop", allow_private_network_urls=mode == "desktop", ui_dir=None,
    )
    base.update(kw)
    return Settings(**base)


class Router:
    """Mock transport: dispatches by (method, url prefix) to handler functions; records requests."""

    def __init__(self) -> None:
        self.handlers: list[tuple[str, str, Callable[[httpx.Request], httpx.Response]]] = []
        self.requests: list[httpx.Request] = []

    def add(self, method: str, prefix: str, handler: Callable[[httpx.Request], httpx.Response] | dict[str, Any]) -> None:
        if isinstance(handler, dict):
            payload = handler
            handler = lambda req: httpx.Response(200, json=payload)  # noqa: E731
        self.handlers.append((method, prefix, handler))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for method, prefix, handler in reversed(self.handlers):
            if request.method == method and url.startswith(prefix):
                return handler(request)
        return httpx.Response(404, json={"error": f"no mock for {request.method} {url}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def find(self, prefix: str, method: str | None = None) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).startswith(prefix) and (method is None or r.method == method)]


def sse(chunks: list[dict[str, Any]]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


class FakeLLM:
    """An OpenAI-compatible endpoint that replays a script. Each script item is either a string
    (final answer) or a list of (tool_name, arguments) calls."""

    BASE = "https://llm.test/v1"

    def __init__(self, router: Router, script: list[Any] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []
        router.add("POST", f"{self.BASE}/chat/completions", self.handle)
        router.add("GET", f"{self.BASE}/models", {"data": [{"id": "fake-model"}, {"id": "fake-large"}]})

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        item = self.script.pop(0) if self.script else "Done."
        if isinstance(item, str):
            words = item.split(" ")
            chunks = [{"choices": [{"delta": {"content": w + (" " if i < len(words) - 1 else "")}}]}
                      for i, w in enumerate(words)]
            chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        else:
            chunks = []
            for i, (name, args) in enumerate(item):
                chunks.append({"choices": [{"delta": {"tool_calls": [
                    {"index": i, "id": f"call_{len(self.calls)}_{i}", "function": {"name": name, "arguments": ""}}]}}]})
                text = json.dumps(args)
                half = len(text) // 2
                for part in (text[:half], text[half:]):
                    chunks.append({"choices": [{"delta": {"tool_calls": [{"index": i, "function": {"arguments": part}}]}}]})
            chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
        chunks.append({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
        return sse(chunks)


class Harness:
    """Engine + TestClient + helpers for the desktop mode."""

    def __init__(self, mode: str = "desktop", script: list[Any] | None = None, router: Router | None = None,
                 **settings_kw: Any) -> None:
        from starlette.testclient import TestClient

        self.tmp = tempfile.TemporaryDirectory()
        self.router = router or Router()
        self.llm = FakeLLM(self.router, script)
        self.settings = settings(Path(self.tmp.name), mode, **settings_kw)
        self.engine = Engine(self.settings, http_transport=self.router.transport())
        self.app = create_app(self.settings, engine=self.engine)
        base = "http://localhost" if mode == "desktop" else "https://desk.example.com"
        self.client = TestClient(self.app, base_url=base)
        self.client.__enter__()
        if mode == "desktop":
            self.client.headers["authorization"] = f"Bearer {LOCAL_TOKEN}"
        self.ws: str | None = None

    def settle(self, timeout: float = 60) -> None:
        """Waits for background document processing; a stall becomes a failure that names the stuck tasks."""
        import asyncio

        knowledge = self.engine.knowledge

        async def run() -> None:
            try:
                await asyncio.wait_for(knowledge.wait_idle(), timeout)
            except TimeoutError:
                states = [f"{t.get_name()}: done={t.done()} coro={t.get_coro()!r}" for t in knowledge.tasks]
                raise AssertionError(f"document processing did not finish within {timeout}s: {states}") from None

        self.client.portal.call(run)

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.engine.db.close()
        self.tmp.cleanup()

    def api(self, method: str, path: str, expect: int | None = 200, **kw: Any) -> Any:
        resp = self.client.request(method, path, **kw)
        if expect is not None and resp.status_code != expect:
            raise AssertionError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
        if resp.status_code == 204 or not resp.content:
            return None
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text

    def setup_workspace(self, with_model: bool = True) -> str:
        ws = self.api("POST", "/api/workspaces", json={"name": "Acme Ltd", "base_currency": "GBP", "country": "GB",
                                                       "fiscal_year_start_month": 4, "accounting_framework": "IFRS",
                                                       "timezone": "Europe/London"})
        self.ws = ws["id"]
        if with_model:
            self.api("POST", f"/api/w/{self.ws}/providers", json={"kind": "openai_compatible", "label": "Fake",
                                                                  "base_url": FakeLLM.BASE, "api_key": "sk-test"})
        return self.ws

    def wait_run(self, thread_id: str, timeout: float = 10) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.api("GET", f"/api/w/{self.ws}/threads/{thread_id}")
            run = data["run"]
            if run and run["status"] in {"done", "error", "waiting_approval"}:
                return data
            time.sleep(0.05)
        raise AssertionError("agent run did not finish")

    def wait_workflow(self, run_id: str, statuses: set[str] | None = None, timeout: float = 10) -> dict[str, Any]:
        statuses = statuses or {"done", "failed", "stopped", "rejected", "waiting_approval"}
        deadline = time.time() + timeout
        while time.time() < deadline:
            run = self.api("GET", f"/api/w/{self.ws}/workflow-runs/{run_id}")
            if run["status"] in statuses:
                return run
            time.sleep(0.05)
        raise AssertionError(f"workflow run did not reach {statuses}")


def env_without_desk() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("DESK_")}
