"""Memory: proposals need approval, secrets are refused, memories reach later conversations and are shown."""

from __future__ import annotations

import unittest

from desk_engine.memory import secret_in

from .helpers import Harness


class Secrets(unittest.TestCase):
    def test_detects_secrets(self) -> None:
        for text in ("The QuickBooks password: Ledger2026!", "use key sk-proj-abcdefghijklmnop1234",
                     "token phpat_abcdefghijk123", "IBAN SA44 2000 0001 2345 6789 1234", "card 4111 1111 1111 1111",
                     "Our main account no 123456789012 at Gulf Bank", "كلمة المرور 12345"):
            self.assertIsNotNone(secret_in(text), text)
        for text in ("Materiality threshold is 50,000 SAR", "Rent goes to 6100 Rent expense",
                     "Month-end close finishes on working day 5", "Invoice 4471 total 18,400.00"):
            self.assertIsNone(secret_in(text), text)


class MemoryFlow(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()

    def tearDown(self) -> None:
        self.h.close()

    def chat(self, text: str) -> dict:
        h, ws = self.h, self.ws
        t = h.api("POST", f"/api/w/{ws}/threads", json={})
        h.api("POST", f"/api/w/{ws}/threads/{t['id']}/messages", json={"text": text})
        return h.wait_run(t["id"])

    def test_propose_approve_use(self) -> None:
        h, ws = self.h, self.ws
        h.llm.script = [[("propose_memory", {"content": "Materiality threshold for variance commentary is 50,000 GBP.",
                                             "category": "policy", "reason": "user stated it"})],
                        "Noted; I suggested saving that threshold."]
        data = self.chat("Remember: our materiality threshold is 50,000 GBP")
        tool = next(m for m in data["messages"] if m["role"] == "tool")
        mem = tool["payload"]["blocks"][0]["memory"]
        self.assertEqual(mem["status"], "proposed")
        # not used until a person saves it
        h.llm.script = ["ok"]
        self.chat("What is our materiality threshold?")
        self.assertNotIn("50,000 GBP", h.llm.calls[-1]["messages"][0]["content"])
        saved = h.api("POST", f"/api/w/{ws}/memories/{mem['id']}/decide", json={"approve": True})
        self.assertEqual(saved["status"], "active")
        h.llm.script = ["Per saved memory, the threshold is 50,000 GBP."]
        data = self.chat("What is our materiality threshold for commentary?")
        system = h.llm.calls[-1]["messages"][0]["content"]
        self.assertIn("Materiality threshold for variance commentary is 50,000 GBP.", system)
        answer = data["messages"][-1]
        self.assertEqual([m["id"] for m in answer["payload"]["memories"]], [mem["id"]])
        self.assertEqual(answer["payload"]["unverified_figures"], [])  # the figure comes from memory
        listed = h.api("GET", f"/api/w/{ws}/memories")["memories"][0]
        self.assertEqual(listed["use_count"], 1)
        self.assertEqual(listed["thread_title"], "Remember: our materiality threshold is 50,000 GBP")
        self.assertEqual(listed["created_by_name"], listed["approved_by_name"])
        actions = [a["action"] for a in h.api("GET", f"/api/w/{ws}/audit")]
        self.assertIn("memory.propose", actions)
        self.assertIn("memory.save", actions)
        # unrelated questions do not pull it in; pinning always includes it
        h.llm.script = ["ok", "ok"]
        self.chat("Draft an e-mail to the auditors")
        self.assertNotIn("50,000 GBP", h.llm.calls[-1]["messages"][0]["content"])
        h.api("PATCH", f"/api/w/{ws}/memories/{mem['id']}", json={"pinned": True})
        self.chat("Draft an e-mail to the auditors")
        self.assertIn("50,000 GBP", h.llm.calls[-1]["messages"][0]["content"])
        # archived memory is no longer used; other workspaces never see it
        h.api("PATCH", f"/api/w/{ws}/memories/{mem['id']}", json={"status": "archived"})
        other = h.api("POST", "/api/workspaces", json={"name": "Other"})["id"]
        self.assertEqual(h.api("GET", f"/api/w/{other}/memories")["memories"], [])
        h.api("DELETE", f"/api/w/{ws}/memories/{mem['id']}", expect=None)
        self.assertEqual(h.api("GET", f"/api/w/{ws}/memories")["memories"], [])

    def test_secrets_and_manual_entries(self) -> None:
        h, ws = self.h, self.ws
        h.llm.script = [[("propose_memory", {"content": "The bank portal password: Gulf-2026-secret"})], "Understood."]
        data = self.chat("remember the bank password Gulf-2026-secret")
        tool = next(m for m in data["messages"] if m["role"] == "tool")
        self.assertIn("Secrets are never stored", tool["content"])
        self.assertEqual(h.api("GET", f"/api/w/{ws}/memories")["memories"], [])
        r = h.api("POST", f"/api/w/{ws}/memories", json={"content": "Card 4111 1111 1111 1111 for travel"}, expect=422)
        self.assertEqual(r["error"], "memory_secret")
        m = h.api("POST", f"/api/w/{ws}/memories", json={"content": "Rent is booked to 6100 Rent expense.",
                                                         "category": "mapping"})
        self.assertEqual((m["status"], m["source_type"]), ("active", "manual"))
        found = h.api("GET", f"/api/w/{ws}/memories", params={"q": "rent account"})["memories"]
        self.assertEqual([x["id"] for x in found], [m["id"]])

    def test_retrieval_in_other_words_follow_ups_and_replacing(self) -> None:
        h, ws = self.h, self.ws
        uid = h.engine.db.scalar("SELECT id FROM users LIMIT 1")
        ids = {}
        for c in ("الحسابات المستحقة تُرحَّل في اليوم الثالث من كل شهر", "Accruals are posted by working day 3.",
                  "Materiality threshold for the audit is 5,000.", "El cierre mensual termina el quinto día hábil",
                  "Rent account 6100 maps to Premises costs"):
            ids[c] = h.api("POST", f"/api/w/{ws}/memories", json={"content": c})["id"]

        def found(text: str | list[str]) -> list[str]:
            return [m["content"] for m in h.engine.memory.relevant(ws, uid, text)]

        # other forms of the same words: Arabic articles and endings, plurals, other languages
        self.assertEqual(found("متى نرحل المستحقات؟")[:1], ["الحسابات المستحقة تُرحَّل في اليوم الثالث من كل شهر"])
        self.assertEqual(found("When do we post accrual entries?")[:1], ["Accruals are posted by working day 3."])
        self.assertEqual(found("¿Cuándo termina el cierre?")[:1], ["El cierre mensual termina el quinto día hábil"])
        self.assertEqual(found("hello there"), [])
        # a short follow-up keeps the conversation's subject
        self.assertEqual(found(["and for the group?", "What is our materiality threshold?"])[:1],
                         ["Materiality threshold for the audit is 5,000."])
        # the Memory page finds words as they are being typed
        listed = h.api("GET", f"/api/w/{ws}/memories?status=active&q=accru")["memories"]
        self.assertEqual([m["content"] for m in listed], ["Accruals are posted by working day 3."])
        self.assertEqual(len(h.api("GET", f"/api/w/{ws}/memories?status=active&q=مستحق")["memories"]), 1)
        # the assistant corrects an outdated memory: saving the new one archives the old one and keeps it pinned
        old = ids["Materiality threshold for the audit is 5,000."]
        h.api("PATCH", f"/api/w/{ws}/memories/{old}", json={"pinned": True})
        h.llm.script = [[("propose_memory", {"content": "Materiality threshold for the audit is 7,500.",
                                             "category": "policy", "replaces": old[-6:]})], "Suggested the new threshold."]
        data = self.chat("The audit materiality is now 7,500, not 5,000")
        card = next(m for m in data["messages"] if m["role"] == "tool")["payload"]["blocks"][0]["memory"]
        self.assertEqual(card["replaces"], "Materiality threshold for the audit is 5,000.")
        h.api("POST", f"/api/w/{ws}/memories/{card['id']}/decide", json={"approve": True})
        statuses = {m["content"]: (m["status"], m["pinned"]) for m in h.api("GET", f"/api/w/{ws}/memories")["memories"]}
        self.assertEqual(statuses["Materiality threshold for the audit is 5,000."], ("archived", 0))
        self.assertEqual(statuses["Materiality threshold for the audit is 7,500."], ("active", 1))
        self.assertNotIn("5,000", " ".join(found("materiality")))


if __name__ == "__main__":
    unittest.main()
