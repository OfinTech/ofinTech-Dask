"""HTTP API. Every module defines a ``router``; public (unauthenticated) endpoints are the OAuth
callback and the messaging webhooks, which authenticate by state value or signature instead."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..app import Engine


def all_routes(engine: Engine) -> list[Any]:
    from . import (analysis, approvals, auth, close, comments, connections, costcentres, dashboards, datasets, documents, files, mcp,
                   memory, overview, owner, platform, providers, skills, threads, webhooks, workflows, workspaces)

    routes: list[Any] = []
    for module in (auth, workspaces, providers, threads, approvals, skills, datasets, documents, files, dashboards,
                   connections, mcp, workflows, comments, memory, costcentres, analysis, overview, webhooks, close, owner, platform):
        module.router.bind(engine)
        routes.extend(module.router.routes)
    from ..embedded import routes as embedded_routes

    embedded_routes.admin.bind(engine)
    routes.extend(embedded_routes.admin.routes)
    routes.extend(embedded_routes.public_routes(engine))
    return routes


def public_routes() -> list[Any]:
    from .public import routes

    return routes
