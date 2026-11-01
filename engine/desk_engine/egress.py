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


_PAY_EXTRA = re.compile(
    r"(^|_)(send_money|move_money|release_funds|transfer_funds|funds_transfer|bank_details|bank_account_details|iban|"
    r"vendor_bank|supplier_bank|payee_bank|bill_pay|paybill|pay_bill|payment_order|payment_run|sadad|mada|"
    r"paiement|paiements|virement|virements|pago|pagos|transferencia|transferencias|zahlung|ueberweisung)(_|$)")


def _words(name: str) -> str:
    """createPayment, Create-Payment and create.payment all become create_payment."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def is_payment_tool(name: str, description: str = "", *, acts: bool = True) -> bool:
    """A tool that moves money or changes who receives it. ``acts`` is False for built-in tools known to only read or
    compute (a loan schedule's "payment" column is not a payment); tools from MCP servers always count as acting."""
    if not acts:
        return False
    words = _words(name)
    return bool(_PAY_NAME.search(words) or _PAY_EXTRA.search(words) or _PAY_TEXT.search(description or "")
                or re.search(r"\b(bank details|beneficiary details|release (the )?funds|move money|send money|"
                             r"payment order|change (the )?iban|update (the )?iban)\b", description or "", re.I))


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
    ws_id = workspace if isinstance(workspace, str) else workspace.get("id")
    if ws_id:   # read now, not from a copy taken when a run started: sealing applies to runs already in progress
        row = engine.db.one("SELECT egress_policy FROM workspaces WHERE id = ?", (ws_id,))
        return bool(row and row["egress_policy"] == "sealed")
    return isinstance(workspace, dict) and workspace.get("egress_policy") == "sealed"


def tool_sends_out(tool: Tool) -> bool:
    return tool.name in EGRESS_TOOLS or tool.category == "mcp"


# ---- hosts ---------------------------------------------------------------------------------------------------------
_LOCAL_NAMES = ("localhost", "host.docker.internal")
_LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".localdomain", ".home.arpa")


# 100.64.0.0/10 is shared address space (carrier-grade NAT, overlay networks such as Tailscale, and some cloud
# metadata services); Python counts it as neither private nor reserved, so it is named here.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

_EXTERNAL_V6 = [ipaddress.ip_network(n) for n in ("2002::/16", "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32")]


def _ip(addr: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Any IP literal, including the forms the C resolver accepts (134744072, 0x8080808, 010.0.0.1, 8.8.2056)."""
    try:
        return ipaddress.ip_address(addr)
    except ValueError:
        pass
    if re.fullmatch(r"[0-9a-fx.]+", addr) and any(ch.isdigit() for ch in addr):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(addr))
        except OSError:
            return None
    return None


def _ip_internal(addr: str) -> bool:
    ip = _ip(addr)
    if ip is None:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return _ip_internal(str(ip.ipv4_mapped))
        if any(ip in n for n in _EXTERNAL_V6):   # 6to4, NAT64 and Teredo carry traffic to public IPv4 hosts
            return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip in _CGNAT


def host_internal(host: str | None, allow: frozenset[str] = frozenset(), *, resolve: bool = False) -> bool:
    """True when ``host`` is on the organization's own network: loopback and private addresses, single-label names
    (Docker services such as ``ollama``), local suffixes, or a host listed in DESK_EGRESS_ALLOW. Names are not resolved
    by default: a DNS lookup of a public name would itself leave the network."""
    if not host:
        return False
    h = host.strip("[]").lower().rstrip(".")
    if h in allow or any(h.endswith("." + a) for a in allow):
        return True
    if _ip(h) is not None:          # an address in any notation is judged as an address, never as a name
        return _ip_internal(h)
    if h in _LOCAL_NAMES or h.endswith(_LOCAL_SUFFIXES) or ("." not in h and ":" not in h):
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


class PrivateNetworkGuard(httpx.AsyncBaseTransport):
    """On a Team Server that does not allow private-network URLs, refuses every request whose host is, or resolves
    to, an address on the private network, at the moment the request is made.

    ``security.check_outbound_url`` makes the same judgement when a URL is saved, but a name can change what it
    resolves to between then and the connection (DNS rebinding), a redirect can point somewhere else, and some
    URLs are only ever checked once. Judging the host again here, for every request including redirects, closes that
    window. Hosts the operator lists in DESK_EGRESS_ALLOW, and the configured S3 endpoint, are exempt because they are
    the operator's own infrastructure. A name that does not resolve is let through: nothing can be reached at it,
    and the connection fails on its own with the ordinary error.
    """

    CACHE_SECONDS = 60

    def __init__(self, inner: httpx.AsyncBaseTransport | None, allow: frozenset[str] = frozenset()) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.allow = frozenset(h.lower() for h in allow)
        self.blocked: list[str] = []
        self._cache: dict[str, tuple[float, bool]] = {}

    def _exempt(self, host: str) -> bool:
        return host in self.allow or any(host.endswith("." + a) for a in self.allow)

    async def _internal(self, host: str) -> bool:
        import asyncio
        import time

        if _ip(host) is not None:
            return _ip_internal(host)
        hit = self._cache.get(host)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None, proto=socket.IPPROTO_TCP)
        except OSError:
            return False
        internal = any(_ip_internal(i[4][0]) for i in infos)
        self._cache[host] = (time.monotonic() + self.CACHE_SECONDS, internal)
        return internal

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").strip("[]").lower().rstrip(".")
        local_name = host in _LOCAL_NAMES or host.endswith(_LOCAL_SUFFIXES) or ("." not in host and ":" not in host)
        if not self._exempt(host) and (local_name or await self._internal(host)):
            self.blocked.append(host)
            raise EgressBlocked(f"Blocked: the Team Server does not connect to private or local network addresses ({host}).",
                                request=request)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


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
_SEP = r"[ \u00a0\-.]?"
_IBAN = re.compile(rf"(?<![A-Za-z0-9])([A-Za-z]{{2}}\d{{2}})((?:{_SEP}[A-Za-z0-9]{{4}}){{2,7}}(?:{_SEP}\d{{1,4}})?)", re.I)
_CARD = re.compile(r"(?<![\d.,])[2-6]\d{3}(?:[ \u00a0\-.]?\d){9,15}(?![\d]|[.,]\d\d?(?!\d))")


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def mask_messages(messages: list[Any]) -> list[Any]:
    """Every message on its way to a language model: user text, tool results, documents, memory and workflow steps."""
    from dataclasses import replace
    return [replace(m, content=mask_bank_identifiers(m.content)) if isinstance(getattr(m, "content", None), str) else m
            for m in messages]


class MaskedModel:
    """Wraps a chat model so that nothing reaches it with a full IBAN or card number, whichever code built the
    messages."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def stream(self, messages: list[Any], tools: list[Any], **kw: Any) -> Any:
        async for event in self.inner.stream(mask_messages(messages), tools, **kw):
            yield event

    async def list_models(self) -> list[str]:
        return await self.inner.list_models()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def mask_bank_identifiers(text: str) -> str:
    """IBANs keep their country, check digits and last four characters; card numbers keep their last four. Reports and
    reconciliations still work on the last four; a full number never reaches a language model or its logs."""
    if not text:
        return text
    if any("\u0660" <= ch <= "\u0669" or "\u06f0" <= ch <= "\u06f9" for ch in text):
        text = text.translate(_ARABIC_DIGITS)   # a card written in Arabic-Indic digits is still a card

    def iban(m: re.Match[str]) -> str:
        body = re.sub(r"[ \u00a0\-.]", "", m.group(2))
        if not 11 <= len(body) <= 30 or sum(c.isdigit() for c in body) < 8:
            return m.group(0)
        return f"{m.group(1).upper()} **** {body[-4:]}"

    def card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        seps = set(re.sub(r"\d", "", m.group(0)))
        if len(seps) > 1 or not 13 <= len(digits) <= 19 or not _luhn(digits):   # one kind of separator, like a card
            return m.group(0)
        return f"**** **** **** {digits[-4:]}"

    return _CARD.sub(card, _IBAN.sub(iban, text))
