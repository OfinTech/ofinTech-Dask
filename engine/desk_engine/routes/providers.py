"""Language model providers (bring your own key) and the workspace default model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..db import dumps, loads, new_id, now
from ..errors import Invalid, NotFound
from ..llm.base import ChatMessage, complete
from ..llm.catalog import CATALOG, get_kind
from ..llm.registry import build_model, provider_row
from ..security import check_outbound_url
from ..web import Ctx, Router

router = Router()


def _out(row: dict[str, Any]) -> dict[str, Any]:
    kind = CATALOG.get(row["kind"])
    return {"id": row["id"], "kind": row["kind"], "label": row["label"], "base_url": row["base_url"],
            "has_key": bool(row["secret_id"]), "models": loads(row["models"], []), "options": loads(row["options"], {}),
            "kind_label": kind.label if kind else row["kind"], "created_at": row["created_at"]}


@router.get("/api/llm/catalog")
async def catalog(ctx: Ctx) -> list[dict[str, Any]]:
    out = []
    for k in CATALOG.values():
        d = k.to_dict()
        d["available"] = not (k.local and not ctx.engine.settings.allow_private_network_urls)
        out.append(d)
    return out


@router.get("/api/w/{ws}/providers", role="viewer")
async def list_providers(ctx: Ctx) -> dict[str, Any]:
    rows = ctx.engine.db.all("SELECT * FROM llm_providers WHERE workspace_id = ? ORDER BY created_at", (ctx.ws_id,))
    return {"providers": [_out(r) for r in rows], "default_provider_id": ctx.workspace["default_provider_id"],
            "default_model": ctx.workspace["default_model"],
            "utility_provider_id": ctx.workspace.get("utility_provider_id"),
            "utility_model": ctx.workspace.get("utility_model")}


class ProviderIn(BaseModel):
    kind: str
    label: str | None = Field(default=None, max_length=80)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=2000)
    models: list[str] = Field(default_factory=list, max_length=200)


def _check_url(ctx: Ctx, kind_key: str, url: str) -> str:
    kind = get_kind(kind_key)
    url = url.strip().rstrip("/")
    if not url:
        raise Invalid("Enter the provider's base URL.")
    if kind.editable_url or url != kind.base_url:
        check_outbound_url(url, allow_private=ctx.engine.settings.allow_private_network_urls,
                           allow_http=ctx.engine.settings.allow_private_network_urls)
    if "YOUR-RESOURCE" in url:
        raise Invalid("Replace YOUR-RESOURCE with your Azure OpenAI resource name.")
    return url


async def _fetch_models(ctx: Ctx, provider_id: str) -> list[str]:
    row = provider_row(ctx.engine, ctx.ws_id, provider_id)
    model = build_model(ctx.engine, ctx.ws_id, provider_id, "probe")
    names = await model.list_models()
    current = loads(row["models"], [])
    merged = current + [n for n in names if n not in current] if current else names
    ctx.engine.db.update("llm_providers", provider_id, {"models": dumps(merged[:500])}, workspace_id=ctx.ws_id)
    return merged


@router.post("/api/w/{ws}/providers", role="admin", body=ProviderIn, fresh=True)
async def add_provider(ctx: Ctx) -> dict[str, Any]:
    b: ProviderIn = ctx.body
    kind = get_kind(b.kind)
    if kind.local and not ctx.engine.settings.allow_private_network_urls:
        raise Invalid(f"{kind.label} runs on your own computer; add it in the desktop app.")
    url = _check_url(ctx, b.kind, b.base_url or kind.base_url)
    from ..egress import check_model_endpoint
    check_model_endpoint(ctx.engine, ctx.workspace, url)   # a sealed workspace accepts only a model on its own network
    if kind.needs_key and not (b.api_key or "").strip():
        raise Invalid(f"{kind.label} needs an API key.", code="no_api_key")
    pid = new_id("llm_")
    secret_id = None
    if b.api_key and b.api_key.strip():
        secret_id = ctx.engine.secrets.put("llm", {"api_key": b.api_key.strip()}, workspace_id=ctx.ws_id,
                                           secret_id=f"sec_{pid}")
    models = [m.strip() for m in b.models if m.strip()]
    ctx.engine.db.insert("llm_providers", {"id": pid, "workspace_id": ctx.ws_id, "kind": b.kind,
                                           "label": (b.label or kind.label).strip(), "base_url": url,
                                           "secret_id": secret_id, "models": dumps(models), "options": "{}",
                                           "created_at": now()})
    warning = None
    try:
        models = await _fetch_models(ctx, pid)
    except Exception as exc:  # the provider may not list models; the user can type them
        warning = f"Saved, but the model list could not be loaded: {getattr(exc, 'message', str(exc))}"
    if not ctx.workspace["default_provider_id"] and models:
        ctx.engine.db.update("workspaces", ctx.ws_id, {"default_provider_id": pid, "default_model": models[0]})
    ctx.audit("provider.add", pid, {"kind": b.kind})
    return _out(provider_row(ctx.engine, ctx.ws_id, pid)) | {"warning": warning}


class ProviderPatch(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    base_url: str | None = None
    api_key: str | None = None
    models: list[str] | None = None


@router.patch("/api/w/{ws}/providers/{pid}", role="admin", body=ProviderPatch)
async def patch_provider(ctx: Ctx) -> dict[str, Any]:
    pid = ctx.request.path_params["pid"]
    row = provider_row(ctx.engine, ctx.ws_id, pid)
    b: ProviderPatch = ctx.body
    values: dict[str, Any] = {}
    if b.label:
        values["label"] = b.label.strip()
    if b.base_url:
        values["base_url"] = _check_url(ctx, row["kind"], b.base_url)
    if b.models is not None:
        values["models"] = dumps([m.strip() for m in b.models if m.strip()][:500])
    if b.api_key:
        values["secret_id"] = ctx.engine.secrets.put("llm", {"api_key": b.api_key.strip()}, workspace_id=ctx.ws_id,
                                                     secret_id=row["secret_id"] or f"sec_{pid}")
    if values:
        ctx.engine.db.update("llm_providers", pid, values, workspace_id=ctx.ws_id)
        ctx.audit("provider.update", pid, {k: ("***" if k == "secret_id" else v) for k, v in values.items()})
    return _out(provider_row(ctx.engine, ctx.ws_id, pid))


@router.delete("/api/w/{ws}/providers/{pid}", role="admin", fresh=True)
async def delete_provider(ctx: Ctx) -> None:
    pid = ctx.request.path_params["pid"]
    row = provider_row(ctx.engine, ctx.ws_id, pid)
    ctx.engine.secrets.delete(row["secret_id"])
    ctx.engine.db.execute("DELETE FROM llm_providers WHERE id = ? AND workspace_id = ?", (pid, ctx.ws_id))
    if ctx.workspace["default_provider_id"] == pid:
        ctx.engine.db.update("workspaces", ctx.ws_id, {"default_provider_id": None, "default_model": None})
    ctx.engine.db.execute("UPDATE threads SET provider_id = NULL, model = NULL WHERE workspace_id = ? AND provider_id = ?",
                          (ctx.ws_id, pid))
    ctx.audit("provider.delete", pid)


@router.post("/api/w/{ws}/providers/{pid}/models/refresh", role="admin")
async def refresh_models(ctx: Ctx) -> dict[str, Any]:
    return {"models": await _fetch_models(ctx, ctx.request.path_params["pid"])}


class TestIn(BaseModel):
    model: str


@router.post("/api/w/{ws}/providers/{pid}/test", role="admin", body=TestIn)
async def test_provider(ctx: Ctx) -> dict[str, Any]:
    pid = ctx.request.path_params["pid"]
    model = build_model(ctx.engine, ctx.ws_id, pid, ctx.body.model)
    result = await complete(model, [ChatMessage("user", "Reply with the single word OK.")], max_tokens=20)
    ctx.engine.record_usage(ctx.ws_id, pid, ctx.body.model, result.usage, "test")
    return {"ok": True, "reply": result.text[:200], "usage": result.usage.__dict__}


class DefaultIn(BaseModel):
    provider_id: str
    model: str = Field(min_length=1, max_length=200)


@router.put("/api/w/{ws}/default-model", role="admin", body=DefaultIn)
async def set_default(ctx: Ctx) -> dict[str, Any]:
    b: DefaultIn = ctx.body
    if not ctx.engine.db.one("SELECT 1 FROM llm_providers WHERE id = ? AND workspace_id = ?", (b.provider_id, ctx.ws_id)):
        raise NotFound("Provider not found.")
    ctx.engine.db.update("workspaces", ctx.ws_id, {"default_provider_id": b.provider_id, "default_model": b.model})
    ctx.audit("provider.default", b.provider_id, {"model": b.model})
    return {"default_provider_id": b.provider_id, "default_model": b.model}


class UtilityIn(BaseModel):
    provider_id: str | None = None
    model: str | None = Field(default=None, max_length=200)


@router.put("/api/w/{ws}/utility-model", role="admin", body=UtilityIn)
async def set_utility(ctx: Ctx) -> dict[str, Any]:
    """A smaller, cheaper model for short jobs (search answers). Empty: use the default model."""
    b: UtilityIn = ctx.body
    if b.provider_id and not ctx.engine.db.one("SELECT 1 FROM llm_providers WHERE id = ? AND workspace_id = ?",
                                               (b.provider_id, ctx.ws_id)):
        raise NotFound("Provider not found.")
    values = {"utility_provider_id": b.provider_id or None, "utility_model": (b.model or None) if b.provider_id else None}
    ctx.engine.db.update("workspaces", ctx.ws_id, values)
    ctx.audit("provider.utility", b.provider_id or "-", {"model": b.model})
    return values
