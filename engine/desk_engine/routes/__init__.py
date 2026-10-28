"""HTTP API. Every module defines a ``router``; public (unauthenticated) endpoints are the OAuth
callback and the messaging webhooks, which authenticate by state value or signature instead."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..app import Engine


def all_routes(engine: Engine) -> list[Any]:
    from . import (analysis, approvals, auth, comments, connections, costcentres, dashboards, datasets, documents, files, mcp,
                   memory, providers, skills, threads, workflows, workspaces)

    routes: list[Any] = []
    for module in (auth, workspaces, providers, threads, approvals, skills, datasets, documents, files, dashboards,
                   connections, mcp, workflows, comments, memory, costcentres, analysis):
        module.router.bind(engine)
        routes.extend(module.router.routes)
    return routes


def public_routes() -> list[Any]:
    from .public import routes

    return routes
