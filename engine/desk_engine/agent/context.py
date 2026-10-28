"""Keeping each model call small: which tools to send, and how much of the past to resend.

Tool schemas are the largest part of a request (about 8,000 tokens for all built-in tools), so a call carries only
the tool groups the request needs: a core set, groups whose words appear in the conversation, groups already used in
the thread, and any the model asks for with ``more_tools``. Tools that are not sent can still run if the model calls
them by name, so selection never blocks work.

Older tool results are shortened in the resent history (the full results stay stored and still count as evidence for
the figure check); the most recent turns are always sent in full.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

from ..llm.base import ChatMessage

if TYPE_CHECKING:
    from .tools import Tool

# groups sent with every call
CORE = {"finance", "knowledge", "memory", "skills", "meta", "mcp"}

# words (English, Arabic, Spanish, French) that bring in a group
TRIGGERS: dict[str, list[str]] = {
    "files": ["workbook", "report", "excel", "xlsx", "docx", "word", "spreadsheet", "sheet", "schedule", "pack", "memo",
              "template", "file", "deck", "commentary", "reconciliation",
              "مصنف", "تقرير", "اكسل", "إكسل", "ملف", "جدول", "وورد", "مذكرة", "قالب", "تسوية",
              "libro", "informe", "hoja", "archivo", "plantilla", "conciliación", "memoria",
              "classeur", "rapport", "tableur", "fichier", "modèle", "rapprochement", "note"],
    "google": ["email", "e-mail", "mail", "gmail", "inbox", "drive", "google", "draft", "send", "attachment", "sheets",
               "بريد", "ايميل", "إيميل", "جيميل", "درايف", "مسودة", "أرسل", "ارسل", "مرفق",
               "correo", "borrador", "enviar", "adjunto",
               "courriel", "brouillon", "envoyer", "pièce jointe"],
    "data": ["dataset", "data", "query", "trend", "chart", "dashboard", "import", "rows", "group", "total", "average",
             "month", "quarter", "year", "budget", "actual", "variance", "revenue", "expense", "cost", "sales",
             "بيانات", "مخطط", "لوحة", "شهر", "ربع", "سنة", "موازنة", "إيراد", "ايراد", "مصروف", "تكلفة", "مبيعات", "انحراف",
             "datos", "gráfico", "panel", "mes", "trimestre", "año", "presupuesto", "ingreso", "gasto", "ventas",
             "données", "graphique", "tableau de bord", "mois", "trimestre", "année", "budget", "recette", "dépense",
             "ventes", "écart"],
    "accounting": ["invoice", "bill", "ledger", "p&l", "profit", "loss", "balance sheet", "receivable", "payable",
                   "quickbooks", "xero", "zoho", "odoo", "sync", "overdue", "aging", "customer", "supplier", "vendor",
                   "فاتورة", "فواتير", "دفتر", "ربح", "خسارة", "ميزانية", "مدين", "دائن", "متأخر", "عميل", "مورد", "قائمة الدخل",
                   "factura", "beneficio", "pérdida", "balance", "cobro", "pago", "vencid", "cliente", "proveedor",
                   "facture", "bénéfice", "perte", "bilan", "créance", "dette", "échu", "fournisseur"],
    "analysis": ["kpi", "ratio", "margin", "liquidity", "dso", "dpo", "working capital", "leverage", "forecast", "runway",
                 "cash flow", "profitability", "scorecard", "performance", "ebitda", "roe", "debt",
                 "مؤشر", "مؤشرات", "نسبة", "هامش", "سيولة", "توقع", "توقعات", "تنبؤ", "التدفق النقدي", "ربحية", "أداء",
                 "اداء", "مديونية", "رأس المال العامل",
                 "indicador", "ratio", "margen", "liquidez", "previsión", "prevision", "pronóstico", "flujo de caja",
                 "rentabilidad", "indicateur", "marge", "liquidité", "prévision", "trésorerie", "rentabilité"],
    "costcentres": ["cost centre", "cost center", "cost-centre", "project", "budget", "burn", "run out", "runs out",
                    "مركز تكلفة", "مركز التكلفة", "مراكز التكلفة", "مشروع", "المشروع", "موازنة", "الموازنة", "تنفد",
                    "centro de coste", "centro de costo", "proyecto", "presupuesto",
                    "centre de coût", "centre de cout", "projet", "budget"],
    "workflows": ["workflow", "automate", "automation", "routine", "every week", "every month", "run the",
                  "سير العمل", "أتمتة", "اتمتة", "كل أسبوع", "كل شهر",
                  "flujo", "automatiza", "cada semana", "cada mes",
                  "workflow", "automatis", "chaque semaine", "chaque mois"],
    "collaboration": ["colleague", "tell ", "notify", "message ", "@", "team",
                      "زميل", "أبلغ", "ابلغ", "أخبر", "الفريق",
                      "compañero", "avisa", "equipo", "collègue", "prévenir", "équipe"],
}

GROUP_HELP = {
    "files": "create evidence-linked Excel workbooks and Word reports; fill in or edit the user's own .xlsx/.docx",
    "google": "Gmail search, read, drafts and sending; Google Drive and Sheets import",
    "data": "imported datasets: list, describe, query; dashboards",
    "accounting": "accounting system reports, invoices and bills, sync",
    "analysis": "the KPI scorecard, one KPI explained, and forecasts set by the finance lead",
    "costcentres": "cost centres and projects: cost, cash paid, budget lines and when the budget runs out",
    "workflows": "list and run workflows",
    "collaboration": "message colleagues in the workspace",
}

KEEP_FULL_TURNS = 2       # the latest user turns are resent in full
OLD_TOOL_CHARS = 600      # older tool results are cut to this many characters
MAX_TOOL_CHARS = 16000    # any single tool result in the request


def enabled() -> bool:
    return os.environ.get("DESK_TOOL_SELECTION", "on").lower() not in {"off", "0", "false"}


def _text_of(messages: list[ChatMessage], turns: int = 3) -> str:
    """The latest few user messages (what the request is about)."""
    users = [m.content for m in messages if m.role == "user"][-turns:]
    return "\n".join(users).lower()


def select_groups(messages: list[ChatMessage], tools: dict[str, Tool], *, has_datasets: bool,
                  extra: str = "") -> set[str]:
    groups = set(CORE)
    text = _text_of(messages) + "\n" + extra.lower()
    for group, words in TRIGGERS.items():
        if any(w in text for w in words):
            groups.add(group)
    if has_datasets:
        groups.add("data")
    if "[attached files]" in text and re.search(r"\.(xlsx|docx)\b", text):
        groups.add("files")
    # groups already used in this thread, and groups the model asked for
    for m in messages:
        if m.role == "assistant":
            for c in m.tool_calls:
                t = tools.get(c.name)
                if t is not None:
                    groups.add(t.category)
                if c.name == "more_tools":
                    groups.update(str(g) for g in (c.arguments.get("groups") or []))
    return groups


def choose_tools(tools: dict[str, Tool], groups: set[str]) -> list[Tool]:
    if not enabled():
        return sorted(tools.values(), key=lambda t: (t.category, t.name))
    chosen = [t for t in tools.values() if t.category in groups]
    return sorted(chosen, key=lambda t: (t.category, t.name))  # stable order keeps the prompt cache valid


def available_groups(tools: dict[str, Tool], groups: set[str]) -> dict[str, str]:
    present = {t.category for t in tools.values()}
    return {g: h for g, h in GROUP_HELP.items() if g in present and g not in groups}


def compact(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Shorten old tool results; keep the last turns whole. Message pairing (call → result) is preserved."""
    user_idx = [i for i, m in enumerate(messages) if m.role == "user"]
    cut = user_idx[-KEEP_FULL_TURNS] if len(user_idx) >= KEEP_FULL_TURNS else 0
    out = []
    for i, m in enumerate(messages):
        if m.role == "tool":
            limit = OLD_TOOL_CHARS if i < cut else MAX_TOOL_CHARS
            if len(m.content) > limit:
                m = ChatMessage("tool", m.content[:limit].rstrip() + f"\n… [shortened from {len(m.content):,} "
                                "characters; call the tool again for the full result]", tool_call_id=m.tool_call_id,
                                name=m.name)
        out.append(m)
    return out


def size(messages: list[ChatMessage], tools: list[Any]) -> int:
    """Characters sent in one request (a rough proxy for tokens, about 4 characters each in English)."""
    import json

    n = sum(len(m.content) + sum(len(json.dumps(c.arguments)) for c in m.tool_calls) for m in messages)
    return n + sum(len(t.description) + len(json.dumps(t.parameters)) for t in tools)
