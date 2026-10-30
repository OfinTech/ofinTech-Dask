"""Keeping data inside: payments the assistant may never touch, and connections that may never leave.

Two levels, both enforced in code rather than by the model's instructions:

* A **sealed workspace** (``workspaces.egress_policy = 'sealed'``) hides and refuses every tool that sends data to
  an outside service (e-mail, chat apps, Google Drive, MCP servers, outgoing webhooks) and accepts only a language
  model on the local network.
* A **sealed server** (``DESK_SEALED=1``) seals every workspace and wraps the engine's HTTP client so that no
  connection to an address outside the private network can be opened, whatever code asks for it. Hosts a sealed
  server may still reach (an internal accounting system, a licensed open-banking gateway) are listed in
  ``DESK_EGRESS_ALLOW``. The network around the server should enforce the same (deploy/sealed.compose.yml).

Payments: OfinTech Desk reads banking and accounting data; it never initiates, approves, schedules or changes a
payment, a transfer or a beneficiary. Tools that would are refused when they are registered (built-in tools) or
listed (MCP tools), and again when they are called.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx2 as httpx

from .errors import Forbidden

if TYPE_CHECKING:
    from .agent.tools import Tool
    from .app import Engine

# ---- payments ------------------------------------------------------------------------------------------------------
_PAY_NAME = re.compile(
    r"(^|[_\-.\s])(pay|pays|payment|payments|payout|payouts|transfer|transfers|wire|remit|remittance|disburse|"
    r"disbursement|refund|refunds|beneficiary|beneficiaries|payee|payees|sepa|swift|sadad|sarie|ach|pis|"
    r"standing[_\-.\s]?order|direct[_\-.\s]?debit)([_\-.\s]|$)", re.I)
_PAY_TEXT = re.compile(
    r"\b(initiat\w* (a |the )?(payment|transfer)|create (a |the )?(payment|transfer|payout|beneficiary)|"
    r"send (money|funds|a payment)|make (a )?payment|transfer (money|funds)|approve (a |the )?payment|"
    r"schedule (a |the )?payment|add (a |the )?(beneficiary|payee)|payment initiation|wire (money|funds))\b", re.I)


def is_payment_tool(name: str, description: str = "", *, acts: bool = True) -> bool:
    """A tool that moves money or changes who receives it. ``acts`` is False for tools known to only read or compute
    (a loan schedule's "payment" column is not a payment)."""
    if not acts:
        return False
    return bool(_PAY_NAME.search(name) or _PAY_TEXT.search(description or ""))


# ---- what leaves the workspace -------------------------------------------------------------------------------------
# built-in tools that send workspace data to a service outside the organization
EGRESS_TOOLS = frozenset({
    "gmail_send", "gmail_create_draft", "gmail_search", "gmail_read", "gmail_import_attachment", "gmail_save_to_documents",
    "drive_add_document", "drive_find_spreadsheets", "drive_import_spreadsheet", "message_colleague",
})
SEALED_MESSAGE = ("This workspace keeps its data inside the organization, so tools that send data to outside services "
                  "are switched off. Nothing was sent.")


def server_sealed(engine: Engine) -> bool:
    return bool(getattr(engine.settings, "sealed", False))


def workspace_sealed(engine: Engine, workspace: dict[str, Any] | str | None) -> bool:
    if server_sealed(engine):
        return True
    if workspace is None:
        return False
    if isinstance(workspace, str):
        row = engine.db.one("SELECT egress_policy FROM workspaces WHERE id = ?", (workspace,))
        return bool(row and row["egress_policy"] == "sealed")
    policy = workspace.get("egress_policy")
    if policy is None and workspace.get("id"):
        return workspace_sealed(engine, workspace["id"])
    return policy == "sealed"


def tool_sends_out(tool: Tool) -> bool:
    return tool.name in EGRESS_TOOLS or tool.category == "mcp"


# ---- hosts ---------------------------------------------------------------------------------------------------------
_LOCAL_NAMES = ("localhost", "host.docker.internal")
_LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".localdomain", ".home.arpa")


def _ip_internal(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def host_internal(host: str | None, allow: frozenset[str] = frozenset(), *, resolve: bool = False) -> bool:
    """True when ``host`` is on the organization's own network: loopback and private addresses, single-label names
    (Docker services such as ``ollama``), local suffixes, or a host listed in DESK_EGRESS_ALLOW. Names are not resolved
    by default: a DNS lookup of a public name would itself leave the network."""
    if not host:
        return False
    h = host.strip("[]").lower().rstrip(".")
    if h in allow or any(h.endswith("." + a) for a in allow):
        return True
    if h in _LOCAL_NAMES or h.endswith(_LOCAL_SUFFIXES) or ("." not in h and ":" not in h):
        return True
    if _ip_internal(h):
        return True
    if not resolve:
        return False
    try:
        infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(infos) and all(_ip_internal(i[4][0]) for i in infos)


def url_internal(url: str, allow: frozenset[str] = frozenset()) -> bool:
    return host_internal(urlparse(url).hostname, allow)


def allowed_hosts(engine: Engine) -> frozenset[str]:
    return frozenset(getattr(engine.settings, "egress_allow", ()) or ())


def check_model_endpoint(engine: Engine, workspace: dict[str, Any] | str, base_url: str) -> None:
    """A sealed workspace may only talk to a language model on its own network."""
    if workspace_sealed(engine, workspace) and not url_internal(base_url or "", allowed_hosts(engine)):
        raise Forbidden("This workspace keeps its data inside the organization: choose a model that runs on your own "
                        "network (for example Ollama or vLLM), not a cloud model.", code="egress_blocked")


class EgressBlocked(httpx.ConnectError):
    pass


class SealedTransport(httpx.AsyncBaseTransport):
    """Wraps the engine's HTTP transport on a sealed server: requests to hosts outside the private network fail
    before any connection is opened."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None, allow: frozenset[str] = frozenset()) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.allow = allow
        self.blocked: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if not host_internal(host, self.allow):
            self.blocked.append(host)
            raise EgressBlocked(f"Blocked: this server keeps data inside the organization and may not connect to {host}.",
                                request=request)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


# ---- bank identifiers the model never needs in full ----------------------------------------------------------------
_IBAN = re.compile(r"\b([A-Z]{2}\d{2})((?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?)\b")
_CARD = re.compile(r"(?<![\d.,])([3-6]\d{3})(?:([ -]?)(\d{4})\2(\d{4})\2(\d{1,4})|(\d{11,12}))(?![\d]|[.,]\d)")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def mask_bank_identifiers(text: str) -> str:
    """IBANs keep their country, check digits and last four characters; card numbers keep their last four. Reports and
    reconciliations still work on the last four; a full number never reaches a language model or its logs."""
    if not text:
        return text

    def iban(m: re.Match[str]) -> str:
        body = m.group(2).replace(" ", "")
        if not 11 <= len(body) <= 30 or not any(c.isdigit() for c in body):
            return m.group(0)
        return f"{m.group(1)} **** {body[-4:]}"

    def card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if not 13 <= len(digits) <= 19 or not _luhn(digits):
            return m.group(0)
        return f"**** **** **** {digits[-4:]}"

    return _CARD.sub(card, _IBAN.sub(iban, text))
