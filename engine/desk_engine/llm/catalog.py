"""Supported model providers. Users bring their own key; nothing here is bound to one vendor.

Model names are not hard-coded: after a provider is added, the app lists the models the provider
reports (``GET /models``), and the user can also type any model id.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProviderKind:
    kind: str
    label: str
    protocol: str  # "openai" (chat completions) or "anthropic" (messages)
    base_url: str
    needs_key: bool = True
    editable_url: bool = False
    local: bool = False
    auth_header: str = "bearer"  # "bearer" | "api-key" (Azure)
    docs: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


CATALOG: dict[str, ProviderKind] = {
    p.kind: p
    for p in [
        ProviderKind("openai", "OpenAI", "openai", "https://api.openai.com/v1",
                     docs="https://platform.openai.com/api-keys"),
        ProviderKind("anthropic", "Anthropic", "anthropic", "https://api.anthropic.com/v1",
                     docs="https://console.anthropic.com/settings/keys"),
        ProviderKind("gemini", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
                     docs="https://aistudio.google.com/apikey"),
        ProviderKind("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1",
                     docs="https://platform.deepseek.com/api_keys"),
        ProviderKind("mistral", "Mistral AI", "openai", "https://api.mistral.ai/v1",
                     docs="https://console.mistral.ai/api-keys"),
        ProviderKind("groq", "Groq", "openai", "https://api.groq.com/openai/v1",
                     docs="https://console.groq.com/keys"),
        ProviderKind("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
                     docs="https://openrouter.ai/keys"),
        ProviderKind("xai", "xAI", "openai", "https://api.x.ai/v1", docs="https://console.x.ai"),
        ProviderKind("together", "Together AI", "openai", "https://api.together.xyz/v1",
                     docs="https://api.together.ai/settings/api-keys"),
        ProviderKind("azure_openai", "Azure OpenAI", "openai", "https://YOUR-RESOURCE.openai.azure.com/openai/v1",
                     editable_url=True, auth_header="api-key",
                     docs="https://learn.microsoft.com/azure/ai-foundry/openai/"),
        ProviderKind("ollama", "Ollama (local)", "openai", "http://localhost:11434/v1", needs_key=False,
                     editable_url=True, local=True, docs="https://ollama.com"),
        ProviderKind("lmstudio", "LM Studio (local)", "openai", "http://localhost:1234/v1", needs_key=False,
                     editable_url=True, local=True, docs="https://lmstudio.ai"),
        ProviderKind("openai_compatible", "Other OpenAI-compatible", "openai", "", needs_key=False,
                     editable_url=True, docs="Any server that implements /v1/chat/completions (vLLM, LiteLLM, ...)"),
    ]
}


def get_kind(kind: str) -> ProviderKind:
    try:
        return CATALOG[kind]
    except KeyError as exc:
        from ..errors import Invalid

        raise Invalid(f"Unknown provider type: {kind}") from exc
