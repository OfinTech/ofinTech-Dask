"""Usage metering per integration and the entitlement hook.

Metering writes one ``embedded_usage`` row per run. ``Entitlements`` is the interface a billing or plan system
would implement later; the default implementation enforces the per-integration limits stored on the integration
row (runs and tokens per day). There is no billing here and no claim about one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from ..db import now
from .auth import EmbeddedPrincipal

if TYPE_CHECKING:
    from ..app import Engine


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str = "ok"
    message: str = ""
    limit: int | None = None
    used: int | None = None


class Entitlements(Protocol):
    def check_run(self, principal: EmbeddedPrincipal, agent_id: str) -> Decision: ...


class Metering:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def day_start(self) -> float:
        import datetime as dt
        today = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return today.timestamp()

    def today(self, integration_id: str) -> dict[str, int]:
        row = self.engine.db.one(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens, "
            "COALESCE(SUM(tool_calls), 0) AS tool_calls FROM embedded_usage WHERE integration_id = ? AND created_at >= ?",
            (integration_id, self.day_start()))
        return {"runs": int(row["runs"]), "tokens": int(row["tokens"]), "tool_calls": int(row["tool_calls"])}

    def record(self, principal: EmbeddedPrincipal, *, run_id: str | None, agent_id: str, status: str,
               input_tokens: int = 0, output_tokens: int = 0, tool_calls: int = 0, duration_ms: int = 0) -> None:
        self.engine.db.insert("embedded_usage", {
            "integration_id": principal.integration["id"], "session_id": principal.session["id"], "run_id": run_id,
            "subject_hash": principal.session["subject_hash"], "agent_id": agent_id, "status": status,
            "input_tokens": int(input_tokens), "output_tokens": int(output_tokens), "tool_calls": int(tool_calls),
            "duration_ms": int(duration_ms), "created_at": now()})

    def summary(self, integration_id: str, *, days: int = 30) -> dict[str, Any]:
        since = now() - days * 86400
        rows = self.engine.db.all(
            "SELECT date(created_at, 'unixepoch') AS day, agent_id, status, COUNT(*) AS runs, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens, "
            "COALESCE(SUM(tool_calls), 0) AS tool_calls, COUNT(DISTINCT subject_hash) AS subjects "
            "FROM embedded_usage WHERE integration_id = ? AND created_at >= ? GROUP BY day, agent_id, status ORDER BY day",
            (integration_id, since))
        total = {"runs": sum(r["runs"] for r in rows), "input_tokens": sum(r["input_tokens"] for r in rows),
                 "output_tokens": sum(r["output_tokens"] for r in rows), "tool_calls": sum(r["tool_calls"] for r in rows)}
        return {"days": days, "rows": rows, "total": total, "today": self.today(integration_id)}


class LimitEntitlements:
    """Default entitlements: the integration's own limits. A plan system would replace this object on
    ``engine.embedded.entitlements``; the gateway only calls ``check_run``."""

    def __init__(self, metering: Metering) -> None:
        self.metering = metering

    def check_run(self, principal: EmbeddedPrincipal, agent_id: str) -> Decision:
        limits = principal.limits
        used = self.metering.today(principal.integration["id"])
        if used["runs"] >= limits["runs_per_day"]:
            return Decision(False, "entitlement_exceeded", "The integration reached its daily run limit.",
                            limit=limits["runs_per_day"], used=used["runs"])
        if used["tokens"] >= limits["tokens_per_day"]:
            return Decision(False, "entitlement_exceeded", "The integration reached its daily token limit.",
                            limit=limits["tokens_per_day"], used=used["tokens"])
        return Decision(True)
