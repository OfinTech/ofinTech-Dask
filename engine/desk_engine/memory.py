"""Memory: facts, preferences and policies the assistant may reuse across conversations.

Transparency rules:
* Nothing is saved without a person's approval. The assistant can only *propose* a memory. The proposal is shown in
  the conversation word for word, and a person saves or discards it. A workspace memory needs the editor role; a
  personal memory needs its own user.
* Every memory records who proposed it, who approved it, when, and the conversation it came from. It is audited and
  can be edited, pinned, archived or deleted on the Memory page.
* When an answer uses memories they are listed under it ("Used memory"), and their use is counted.
* Secrets are refused: passwords, API keys and tokens, card numbers, IBANs and full bank account numbers.

Retrieval is cheap and works across the four interface languages: the user's latest messages and every saved memory
are normalised (case, accents, Arabic letter forms), common words are dropped and words are reduced to a light stem
(Arabic articles and suffixes, English, Spanish and French plurals and endings), so "المستحقات" finds "المستحقة" and
"accrual entries" finds "Accruals are posted…". Rare words weigh more than common ones. The best few, plus the
pinned ones, are added to the prompt.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

from .db import new_id, now
from .errors import Forbidden, Invalid, NotFound

if TYPE_CHECKING:
    from .app import Engine

CATEGORIES = ["fact", "preference", "policy", "mapping", "contact", "deadline"]
MAX_CHARS = 500
MAX_IN_PROMPT = 8
MAX_PINNED = 6

_SECRET_PATTERNS = [
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"), "an API key"),
    (re.compile(r"\b(?:phpat|odk|ghp|gho|xox[abpr])_[A-Za-z0-9_\-]{10,}"), "an access token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an access key"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "a token"),
    (re.compile(r"(?i)\b(?:password|passwd|pwd|passcode|pin|otp|كلمة\s*(?:ال)?(?:سر|مرور)|contraseña|mot\s+de\s+passe)\b\s*[:=]?\s*\S+"),
     "a password"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b"), "an IBAN"),
    (re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
]


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def secret_in(text: str) -> str | None:
    """Names the kind of secret found in ``text``, or None."""
    for pattern, what in _SECRET_PATTERNS:
        if pattern.search(text):
            return what
    for m in re.finditer(r"(?:\d[ -]?){13,19}", text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return "a card number"
    if re.search(r"(?i)\b(?:account|acct|a/c|حساب|cuenta|compte)\b[^\n]{0,20}\b\d{9,18}\b", text):
        return "a bank account number"
    return None


def clean_content(text: str) -> str:
    content = " ".join(str(text or "").split())
    if not content:
        raise Invalid("A memory cannot be empty.")
    if len(content) > MAX_CHARS:
        raise Invalid(f"Keep a memory under {MAX_CHARS} characters; save one fact per memory.")
    what = secret_in(content)
    if what:
        raise Invalid(f"This looks like {what}. Secrets are never stored in memory; keep them in the connection "
                      "settings, where they are encrypted.", code="memory_secret")
    return content


def _fts(text: str) -> str | None:
    from .knowledge.base import fts_query

    return fts_query(text)


STOP_WORDS = set("""a an and are as at be by for from has have how i in is it of on or our that the this to was were what
when where which who why will with does do did can could should would about into than then there their these those your
you we me my us whats its any all also just much many please tell show give know remember
ما ماذا متى كيف كم هل من في على الى عن مع هذا هذه ذلك تلك هو هي نحن انا انت لنا لدينا عندنا كان يكون التي الذي
ايش وش شو اي او ثم لكن قد لقد كل بعض
el la los las un una de del y o en con por para que como cual cuando donde es son nuestro nuestra
le les des du et ou au aux avec pour par que qui quoi comment quand est sont notre nos""".split())
_AR_PREFIXES = ("وبال", "وال", "بال", "كال", "فال", "لل", "ال", "و", "ف", "ب", "ل", "ك")
_AR_VERB = ("سي", "ست", "سن", "ي", "ت", "ن", "ا")
_AR_SUFFIXES = ("ات", "ون", "ين", "ان", "ية", "يه", "ها", "هم", "نا", "كم", "ه", "ي")
_LATIN_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "es", "ed", "s")


def stem(token: str) -> str:
    """A light stem: enough for short memories to meet questions written differently."""
    if re.search(r"[\u0600-\u06ff]", token):
        for p in _AR_PREFIXES:
            if token.startswith(p) and len(token) - len(p) >= 3:
                token = token[len(p):]
                break
        for sfx in _AR_SUFFIXES:
            if token.endswith(sfx) and len(token) - len(sfx) >= 3:
                token = token[: -len(sfx)]
                break
        for p in _AR_VERB:
            if token.startswith(p) and len(token) - len(p) >= 3:
                token = token[len(p):]
                break
        return token
    if token.isdigit():
        return token
    for sfx in _LATIN_SUFFIXES:
        if token.endswith(sfx) and len(token) - len(sfx) >= 3:
            return token[: -len(sfx)] + ("y" if sfx == "ies" else "")
    return token


def stems(text: str) -> list[str]:
    from .costcentres.rules import norm

    out = []
    for t in re.findall(r"\w+", norm(text)):
        if t in STOP_WORDS or (len(t) < 2 and not t.isdigit()):
            continue
        out.append(stem(t))
    return out


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long_.startswith(short)


def rank(memories: list[dict[str, Any]], queries: list[tuple[str, float]]) -> list[tuple[float, dict[str, Any]]]:
    """Memories scored against weighted query texts: each matching word counts by how rare it is among memories."""
    docs = [(m, set(stems(f"{m['content']} {m.get('category') or ''}"))) for m in memories]
    if not docs:
        return []
    df: dict[str, int] = {}
    for _, words in docs:
        for w in words:
            df[w] = df.get(w, 0) + 1
    n = len(docs)
    scored = []
    for m, words in docs:
        score = 0.0
        for text, weight in queries:
            for q in set(stems(text)):
                hit = q if q in words else next((w for w in words if _same(q, w)), None)
                if hit:
                    score += weight * math.log(1 + n / df.get(hit, 1))
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    return scored


class Memory:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # -- reading ------------------------------------------------------------------------------------------
    def visible_sql(self) -> str:
        return "(m.scope = 'workspace' OR m.user_id = ?)"

    def list(self, ws_id: str, user_id: str, status: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT m.*, cu.name AS created_by_name, au.name AS approved_by_name, t.title AS thread_title FROM memories m "
               "LEFT JOIN users cu ON cu.id = m.created_by LEFT JOIN users au ON au.id = m.approved_by "
               "LEFT JOIN threads t ON t.id = m.thread_id "
               f"WHERE m.workspace_id = ? AND {self.visible_sql()}")
        params: list[Any] = [ws_id, user_id]
        if status:
            sql += " AND m.status = ?"
            params.append(status)
        if not q or not q.strip():
            return self.engine.db.all(sql + " ORDER BY m.pinned DESC, m.updated_at DESC LIMIT 500", params)
        from .costcentres.rules import norm

        rows = self.engine.db.all(sql + " ORDER BY m.updated_at DESC LIMIT 5000", params)
        needle = norm(q).strip()
        ranked = {m["id"]: sc for sc, m in rank(rows, [(q, 1.0)])}
        hits = [r for r in rows if r["id"] in ranked or needle in norm(r["content"])]
        hits.sort(key=lambda r: (-ranked.get(r["id"], 0.0) - (5.0 if needle in norm(r["content"]) else 0.0)))
        return hits[:500]

    def get(self, ws_id: str, user_id: str, mem_id: str) -> dict[str, Any]:
        row = self.engine.db.one(f"SELECT m.* FROM memories m WHERE m.id = ? AND m.workspace_id = ? AND {self.visible_sql()}",
                                 (mem_id, ws_id, user_id))
        if row is None:
            raise NotFound("Memory not found.")
        return row

    def relevant(self, ws_id: str, user_id: str, text: str | list[str], limit: int = MAX_IN_PROMPT) -> list[dict[str, Any]]:
        """Pinned memories (up to MAX_PINNED) and the memories that best match the conversation's latest user
        messages. ``text`` is the latest message, or the latest messages newest first (older ones weigh less, so a
        short follow-up such as "and last month?" still finds what the conversation is about)."""
        rows = self.engine.db.all(
            f"SELECT m.* FROM memories m WHERE m.workspace_id = ? AND m.status = 'active' AND {self.visible_sql()} "
            "ORDER BY m.updated_at DESC LIMIT 5000", (ws_id, user_id))
        out = [m for m in rows if m["pinned"]][:MAX_PINNED]
        texts = [text] if isinstance(text, str) else list(text)
        queries = [(t, 1.0 if i == 0 else 0.4) for i, t in enumerate(texts[:3]) if t]
        seen = {m["id"] for m in out}
        for _, m in rank([r for r in rows if r["id"] not in seen], queries):
            if len(out) >= limit + len(seen):
                break
            out.append(m)
        return out

    def prompt_block(self, memories: list[dict[str, Any]]) -> str | None:
        if not memories:
            return None
        lines = [f"[{m['id'][-6:]}] ({m['scope']}, {m['category']}) {m['content']}" for m in memories]
        return ("Memory saved by this workspace's people (approved, may be out of date). Use it when relevant and "
                "say so, e.g. \"per saved memory\". When the user says one of these is no longer true, propose the "
                "corrected fact with propose_memory and its id in `replaces`:\n" + "\n".join(lines))

    def mark_used(self, ids: list[str]) -> None:
        if ids:
            marks = ", ".join("?" for _ in ids)
            self.engine.db.execute(f"UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE id IN ({marks})",
                                   [now(), *ids])

    # -- writing ------------------------------------------------------------------------------------------
    def find_ref(self, ws_id: str, user_id: str, ref: str | None) -> dict[str, Any] | None:
        """A memory by id or by the short id shown to the assistant ([abc123])."""
        ref = (ref or "").strip().strip("[]")
        if not ref or not re.fullmatch(r"[A-Za-z0-9_]{4,40}", ref):
            return None
        return self.engine.db.one(
            f"SELECT m.* FROM memories m WHERE m.workspace_id = ? AND m.status = 'active' AND {self.visible_sql()} AND "
            "(m.id = ? OR m.id LIKE ?) ORDER BY m.updated_at DESC", (ws_id, user_id, ref, f"%{ref}"))

    def propose(self, ws_id: str, user_id: str, content: str, *, scope: str, category: str, reason: str | None,
                thread_id: str | None, message_id: str | None, replaces: str | None = None) -> dict[str, Any]:
        content = clean_content(content)
        old = self.find_ref(ws_id, user_id, replaces)
        if scope not in {"workspace", "personal"}:
            scope = "workspace"
        if category not in CATEGORIES:
            category = "fact"
        dup = self.engine.db.one("SELECT * FROM memories WHERE workspace_id = ? AND status IN ('active','proposed') AND "
                                 "lower(content) = lower(?) AND (scope = 'workspace' OR user_id = ?)",
                                 (ws_id, content, user_id))
        if dup:
            return dup
        mid = new_id("mem_")
        ts = now()
        self.engine.db.insert("memories", {
            "id": mid, "workspace_id": ws_id, "user_id": user_id if scope == "personal" else None, "scope": scope,
            "category": category, "content": content, "status": "proposed", "source_type": "chat",
            "thread_id": thread_id, "message_id": message_id, "reason": (reason or "")[:300] or None,
            "replaces_id": old["id"] if old else None, "created_by": user_id, "created_at": ts, "updated_at": ts})
        self.engine.audit(ws_id, user_id, "memory.propose", mid, {"scope": scope, "category": category,
                                                                  "replaces": old["id"] if old else None})
        return self.engine.db.one("SELECT * FROM memories WHERE id = ?", (mid,)) or {}

    def add(self, ws_id: str, user_id: str, role: str, content: str, *, scope: str, category: str,
            pinned: bool = False) -> dict[str, Any]:
        from .web import role_at_least

        content = clean_content(content)
        if scope == "workspace" and not role_at_least(role, "editor"):
            raise Forbidden("Saving workspace memory needs the editor role; save it as personal memory instead.")
        mid = new_id("mem_")
        ts = now()
        self.engine.db.insert("memories", {
            "id": mid, "workspace_id": ws_id, "user_id": user_id if scope == "personal" else None,
            "scope": "personal" if scope == "personal" else "workspace",
            "category": category if category in CATEGORIES else "fact", "content": content, "status": "active",
            "pinned": int(pinned), "source_type": "manual", "created_by": user_id, "approved_by": user_id,
            "created_at": ts, "updated_at": ts})
        self.engine.audit(ws_id, user_id, "memory.add", mid, {"scope": scope})
        return self.get(ws_id, user_id, mid)

    def decide(self, ws_id: str, user_id: str, role: str, mem_id: str, *, approve: bool,
               content: str | None = None) -> dict[str, Any]:
        from .web import role_at_least

        m = self.get(ws_id, user_id, mem_id)
        if m["status"] != "proposed":
            raise Invalid("This memory was already decided.")
        if m["scope"] == "workspace" and not role_at_least(role, "editor"):
            raise Forbidden("Saving workspace memory needs the editor role.")
        values: dict[str, Any] = {"status": "active" if approve else "discarded", "approved_by": user_id if approve else None,
                                  "updated_at": now()}
        if approve and content is not None:
            values["content"] = clean_content(content)
        self.engine.db.update("memories", mem_id, values, workspace_id=ws_id)
        self.engine.audit(ws_id, user_id, "memory.save" if approve else "memory.discard", mem_id)
        if approve and m.get("replaces_id"):
            old = self.engine.db.one("SELECT * FROM memories WHERE id = ? AND workspace_id = ? AND status = 'active'",
                                     (m["replaces_id"], ws_id))
            if old and (old["scope"] == "personal" or role_at_least(role, "editor")):
                self.engine.db.update("memories", old["id"], {"status": "archived", "pinned": 0, "updated_at": now()},
                                      workspace_id=ws_id)
                if old["pinned"]:
                    self.engine.db.update("memories", mem_id, {"pinned": 1}, workspace_id=ws_id)
                self.engine.audit(ws_id, user_id, "memory.edit", old["id"], {"status": "archived", "replaced_by": mem_id})
        return self.get(ws_id, user_id, mem_id)

    def edit(self, ws_id: str, user_id: str, role: str, mem_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        from .web import role_at_least

        m = self.get(ws_id, user_id, mem_id)
        if m["scope"] == "workspace" and not role_at_least(role, "editor"):
            raise Forbidden("Changing workspace memory needs the editor role.")
        values: dict[str, Any] = {"updated_at": now()}
        if "content" in changes and changes["content"] is not None:
            values["content"] = clean_content(changes["content"])
        if changes.get("category") in CATEGORIES:
            values["category"] = changes["category"]
        if changes.get("pinned") is not None:
            values["pinned"] = int(bool(changes["pinned"]))
        if changes.get("status") in {"active", "archived"}:
            values["status"] = changes["status"]
        self.engine.db.update("memories", mem_id, values, workspace_id=ws_id)
        self.engine.audit(ws_id, user_id, "memory.edit", mem_id, {k: v for k, v in values.items() if k != "updated_at"})
        return self.get(ws_id, user_id, mem_id)

    def delete(self, ws_id: str, user_id: str, role: str, mem_id: str) -> None:
        from .web import role_at_least

        m = self.get(ws_id, user_id, mem_id)
        if m["scope"] == "workspace" and not role_at_least(role, "editor") and m["created_by"] != user_id:
            raise Forbidden("Deleting workspace memory needs the editor role.")
        self.engine.db.execute("DELETE FROM memories WHERE id = ? AND workspace_id = ?", (mem_id, ws_id))
        self.engine.audit(ws_id, user_id, "memory.delete", mem_id, {"content": m["content"][:80]})
