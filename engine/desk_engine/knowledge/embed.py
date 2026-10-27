"""Embeddings through the workspace's model providers (OpenAI-compatible ``/embeddings``: OpenAI,
Azure OpenAI, Gemini, Mistral, Together, OpenRouter, Ollama, LM Studio, vLLM, ...).

Embeddings are optional: without them the knowledge base still works with full-text (BM25) search;
with them, search becomes hybrid (keyword + semantic)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx2 as httpx
import numpy as np

from ..errors import NotConfigured, UpstreamError
from ..llm.catalog import get_kind
from ..llm.openai_compat import upstream_error
from ..llm.registry import provider_row

if TYPE_CHECKING:
    from ..app import Engine

BATCH = 64
NO_EMBEDDINGS = {"anthropic", "deepseek", "groq", "xai"}


class Embedder:
    def __init__(self, engine: Engine, ws_id: str, provider_id: str, model: str) -> None:
        row = provider_row(engine, ws_id, provider_id)
        kind = get_kind(row["kind"])
        if row["kind"] in NO_EMBEDDINGS:
            raise NotConfigured(f"{kind.label} does not offer an embeddings API. Choose another provider for "
                                "document search, or use keyword search only.", code="no_embeddings")
        self.label = row["label"]
        self.base_url = row["base_url"].rstrip("/")
        self.model = model
        self.auth_header = kind.auth_header
        self.api_key = None
        if row["secret_id"]:
            self.api_key = engine.secrets.get(row["secret_id"], workspace_id=ws_id).get("api_key")
        self.transport = engine.http_transport

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        if self.auth_header == "api-key":
            return {"api-key": self.api_key}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            for i in range(0, len(texts), BATCH):
                batch = [t[:8000] for t in texts[i:i + BATCH]]
                resp = await client.post(f"{self.base_url}/embeddings", headers=self._headers(),
                                         json={"model": self.model, "input": batch})
                if resp.status_code >= 400:
                    raise upstream_error(self.label, resp.status_code, resp.text)
                data: Any = resp.json().get("data", [])
                if len(data) != len(batch):
                    raise UpstreamError(f"{self.label} returned {len(data)} embeddings for {len(batch)} texts.")
                for item in sorted(data, key=lambda d: d.get("index", 0)):
                    out.append(item["embedding"])
        arr = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


def workspace_embedder(engine: Engine, workspace: dict[str, Any]) -> Embedder | None:
    if not workspace.get("embedding_provider_id") or not workspace.get("embedding_model"):
        return None
    return Embedder(engine, workspace["id"], workspace["embedding_provider_id"], workspace["embedding_model"])
