"""Builds a chat model from a workspace's stored provider configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db import loads
from ..errors import NotConfigured, NotFound
from .anthropic import AnthropicModel
from .base import ChatModel
from .catalog import get_kind
from .openai_compat import OpenAICompatible

if TYPE_CHECKING:
    from ..app import Engine


def provider_row(engine: Engine, ws_id: str, provider_id: str) -> dict[str, Any]:
    row = engine.db.one("SELECT * FROM llm_providers WHERE id = ? AND workspace_id = ?", (provider_id, ws_id))
    if row is None:
        raise NotFound("Model provider not found.")
    return row


def resolve_choice(engine: Engine, workspace: dict[str, Any], provider_id: str | None,
                   model: str | None) -> tuple[str, str]:
    """Explicit choice, else the workspace default, else the first configured provider."""
    pid = provider_id or workspace.get("default_provider_id")
    mdl = model or (workspace.get("default_model") if not provider_id or provider_id == workspace.get(
        "default_provider_id") else None)
    if not pid:
        row = engine.db.one("SELECT id, models FROM llm_providers WHERE workspace_id = ? ORDER BY created_at LIMIT 1",
                            (workspace["id"],))
        if row is None:
            raise NotConfigured("No language model is set up yet. Add one under Settings → Models.",
                                code="no_model")
        pid = row["id"]
    if not mdl:
        row = provider_row(engine, workspace["id"], pid)
        models = loads(row["models"], [])
        if not models:
            raise NotConfigured("Choose a model for this provider under Settings → Models.", code="no_model")
        mdl = models[0]
    return pid, mdl


def build_model(engine: Engine, ws_id: str, provider_id: str, model: str) -> ChatModel:
    from ..egress import MaskedModel
    return MaskedModel(_build_model(engine, ws_id, provider_id, model))   # type: ignore[return-value]


def _build_model(engine: Engine, ws_id: str, provider_id: str, model: str) -> ChatModel:
    row = provider_row(engine, ws_id, provider_id)
    from ..egress import check_model_endpoint
    check_model_endpoint(engine, ws_id, row["base_url"])
    kind = get_kind(row["kind"])
    api_key = None
    if row["secret_id"]:
        api_key = engine.secrets.get(row["secret_id"], workspace_id=ws_id).get("api_key")
    if kind.needs_key and not api_key:
        raise NotConfigured(f"{row['label']} has no API key.", code="no_api_key")
    options = loads(row["options"], {})
    transport = engine.http_transport
    if kind.protocol == "anthropic":
        return AnthropicModel(label=row["label"], base_url=row["base_url"], api_key=api_key, model=model,
                              transport=transport)
    return OpenAICompatible(
        label=row["label"],
        base_url=row["base_url"],
        api_key=api_key,
        model=model,
        auth_header=kind.auth_header,
        stream_usage=options.get("stream_usage", kind.kind not in {"ollama", "lmstudio", "openai_compatible", "mistral"}),
        transport=transport,
        extra_headers={"X-Title": "OfinTech Desk"} if kind.kind == "openrouter" else None,
    )
