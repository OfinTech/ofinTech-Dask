"""Skill text reaches the assistant's instructions only when an admin has approved it (audit findings AI-01, AI-02).

Every case reads the system messages actually sent to the (scripted) model, so a pass means the text was not in the
instructions, not merely that an API answered 4xx. Roles are the engine's real ones: owner, admin, editor, viewer.
"""

from __future__ import annotations

import json
import socket
import unittest
from unittest import mock

from starlette.testclient import TestClient

from .helpers import FakeLLM, Harness

PW = "Correct-Horse-9"
ORIGIN = {"origin": "https://desk.example.com"}


class SkillAuthorization(unittest.TestCase):
    def setUp(self) -> None:
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        for target in ("desk_engine.security.socket.getaddrinfo", "desk_engine.egress.socket.getaddrinfo"):
            patcher = mock.patch(target, return_value=public)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.h = Harness("server", script=[])
        self.addCleanup(self.h.close)
        self.owner = self.h.client
        self.assertEqual(self.owner.post("/api/auth/signup", json={"email": "o@a.test", "name": "Olive", "password": PW}).status_code, 200)
        self.ws = self.owner.post("/api/workspaces", json={"name": "Alpha", "base_currency": "USD"}, headers=ORIGIN).json()["id"]
        self.h.ws = self.ws
        self.owner.post(f"/api/w/{self.ws}/providers", json={"kind": "openai_compatible", "label": "Fake",
                                                            "base_url": FakeLLM.BASE, "api_key": "sk"}, headers=ORIGIN)
        self.c: dict[str, TestClient] = {"owner": self.owner}
        self.uid: dict[str, str] = {"owner": self.owner.get("/api/session").json()["user"]["id"]}
        for role in ("admin", "editor", "viewer"):
            self.c[role], self.uid[role] = self._member(f"{role}@a.test", role)

    def _member(self, email: str, role: str) -> tuple[TestClient, str]:
        inv = self.owner.post(f"/api/w/{self.ws}/invites", json={"email": email, "role": role}, headers=ORIGIN).json()
        c = TestClient(self.h.app, base_url="https://desk.example.com")
        c.portal = self.h.client.portal
        r = c.post("/api/auth/signup", json={"email": email, "name": role, "password": PW,
                                             "invite_token": inv["link"].rsplit("/", 1)[1]})
        self.assertEqual(r.status_code, 200, r.text)
        return c, c.get("/api/session").json()["user"]["id"]

    # -- helpers --------------------------------------------------------------------------------------------------
    def skill(self, who: str, name: str, body: str, expect: int = 200) -> dict:
        r = self.c[who].post(f"/api/w/{self.ws}/skills", json={"name": name, "description": "A guide for the close",
                                                               "body": body, "tags": []}, headers=ORIGIN)
        self.assertEqual(r.status_code, expect, r.text)
        return r.json()

    def enabled(self, name: str) -> bool:
        rows = self.owner.get(f"/api/w/{self.ws}/skills").json()
        rows = rows["skills"] if isinstance(rows, dict) else rows
        return next(s["enabled"] for s in rows if s["name"] == name)

    def thread(self, who: str = "viewer", skills: list[str] | None = None, expect: int = 200) -> str | None:
        r = self.c[who].post(f"/api/w/{self.ws}/threads", json={"skills": skills or []}, headers=ORIGIN)
        self.assertEqual(r.status_code, expect, r.text)
        return r.json()["id"] if expect == 200 else None

    def force_pins(self, tid: str, names: list[str]) -> None:
        """A pin that got past the API in the past (a stale or cached reference): written straight to the row."""
        self.h.engine.db.execute("UPDATE threads SET skills = ? WHERE id = ?", (json.dumps(names), tid))

    def system_text(self, tid: str, who: str = "viewer") -> str:
        self.h.llm.script = ["ok"]
        before = len(self.h.llm.calls)
        r = self.c[who].post(f"/api/w/{self.ws}/threads/{tid}/messages", json={"text": "Help with the close"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.h.wait_run(tid)
        self.assertGreater(len(self.h.llm.calls), before)
        return "\n".join(m["content"] for m in self.h.llm.calls[-1]["messages"] if m["role"] == "system")

    # -- the eight scenarios ----------------------------------------------------------------------------------------
    def test_1_viewer_pins_a_disabled_skill(self) -> None:
        s = self.skill("admin", "old-guide", "DISABLED-TEXT-1")
        self.assertEqual(self.c["admin"].put(f"/api/w/{self.ws}/skills/{s['id']}/enabled", json={"enabled": False},
                                             headers=ORIGIN).status_code, 200)
        self.thread("viewer", ["old-guide"], expect=422)
        tid = self.thread("viewer")
        self.force_pins(tid, ["old-guide"])
        self.assertNotIn("DISABLED-TEXT-1", self.system_text(tid))
        self.assertTrue(self.h.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'skill.pin_refused'"))

    def test_2_viewer_pins_an_unapproved_skill(self) -> None:
        s = self.skill("editor", "rogue-guide", "UNAPPROVED-TEXT-2")
        self.assertFalse(s["enabled"])
        self.thread("viewer", ["rogue-guide"], expect=422)
        tid = self.thread("viewer")
        self.force_pins(tid, ["rogue-guide"])
        self.assertNotIn("UNAPPROVED-TEXT-2", self.system_text(tid))

    def test_3_a_skill_of_another_workspace(self) -> None:
        other = self.owner.post("/api/workspaces", json={"name": "Beta", "base_currency": "USD"}, headers=ORIGIN).json()["id"]
        self.assertEqual(self.owner.post(f"/api/w/{other}/skills", json={"name": "beta-guide", "description": "A guide here",
                                                                         "body": "OTHER-WS-TEXT-3", "tags": []},
                                         headers=ORIGIN).status_code, 200)
        self.thread("viewer", ["beta-guide"], expect=422)
        tid = self.thread("viewer")
        self.force_pins(tid, ["beta-guide"])
        self.assertNotIn("OTHER-WS-TEXT-3", self.system_text(tid))

    def test_4_admin_disables_a_skill_after_it_was_pinned(self) -> None:
        s = self.skill("admin", "close-guide", "PINNED-THEN-DISABLED-4")
        tid = self.thread("viewer", ["close-guide"])
        self.assertIn("PINNED-THEN-DISABLED-4", self.system_text(tid))          # allowed while enabled
        self.c["admin"].put(f"/api/w/{self.ws}/skills/{s['id']}/enabled", json={"enabled": False}, headers=ORIGIN)
        self.assertNotIn("PINNED-THEN-DISABLED-4", self.system_text(tid))       # gone on the very next turn

    def test_5_skill_deleted_after_being_pinned(self) -> None:
        s = self.skill("admin", "temp-guide", "DELETED-TEXT-5")
        tid = self.thread("viewer", ["temp-guide"])
        r = self.c["admin"].delete(f"/api/w/{self.ws}/skills/{s['id']}", headers=ORIGIN)
        self.assertIn(r.status_code, (200, 204), r.text)
        text = self.system_text(tid)
        self.assertNotIn("DELETED-TEXT-5", text)

    def test_6_a_stale_reference_after_a_rename(self) -> None:
        s = self.skill("admin", "first-name", "RENAMED-TEXT-6")
        tid = self.thread("viewer", ["first-name"])
        r = self.c["admin"].put(f"/api/w/{self.ws}/skills/{s['id']}", json={"name": "second-name", "description": "A guide here",
                                                                          "body": "RENAMED-TEXT-6", "tags": []}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("RENAMED-TEXT-6", self.system_text(tid))              # the old name resolves to nothing

    def test_7_manipulated_skill_identifiers(self) -> None:
        s = self.skill("editor", "id-guide", "ID-TEXT-7")
        disabled_builtin = "month-end-close"
        self.c["admin"].put(f"/api/w/{self.ws}/skills/builtin:{disabled_builtin}/enabled", json={"enabled": False}, headers=ORIGIN)
        for name in (s["id"], f"builtin:{disabled_builtin}", disabled_builtin, "../id-guide", "ID-GUIDE", "id-guide "):
            self.thread("viewer", [name], expect=422)
        tid = self.thread("viewer")
        self.force_pins(tid, [s["id"], disabled_builtin, "id-guide"])
        text = self.system_text(tid)
        self.assertNotIn("ID-TEXT-7", text)
        self.assertNotIn(f"Pinned skill '{disabled_builtin}'", text)
        # the tools the model can call are held to the same rule
        from desk_engine.agent.tools import ToolContext, registry
        ctx = ToolContext(self.h.engine, self.h.engine.db.one("SELECT * FROM workspaces WHERE id = ?", (self.ws,)), self.uid["viewer"])
        for tool, args in (("load_skill", {"name": "id-guide"}), ("read_skill_file", {"skill": "id-guide", "file": "x.md"}),
                           ("load_skill", {"name": disabled_builtin}), ("read_skill_file", {"skill": disabled_builtin, "file": "x"})):
            out = self.h.client.portal.call(registry.get_static(tool).run, ctx, args)
            self.assertIn("not enabled", out.text, (tool, args, out.text))

    def test_8_permissions_change_during_a_session(self) -> None:
        s = self.skill("admin", "role-guide", "ADMIN-APPROVED-8")
        self.assertTrue(s["enabled"])
        # the admin is demoted to editor while signed in, then edits the skill
        r = self.owner.patch(f"/api/w/{self.ws}/members/{self.uid['admin']}", json={"role": "editor"}, headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c["admin"].put(f"/api/w/{self.ws}/skills/{s['id']}", json={"name": "role-guide", "description": "A guide here",
                                                                          "body": "EDITED-AFTER-DEMOTION-8", "tags": []},
                                headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(self.enabled("role-guide"))
        tid = self.thread("viewer")
        self.force_pins(tid, ["role-guide"])
        self.assertNotIn("EDITED-AFTER-DEMOTION-8", self.system_text(tid))
        # and can no longer switch it back on
        self.assertEqual(self.c["admin"].put(f"/api/w/{self.ws}/skills/{s['id']}/enabled", json={"enabled": True},
                                             headers=ORIGIN).status_code, 403)

    # -- AI-02 and the permission matrix -----------------------------------------------------------------------------
    def test_editor_edit_of_an_enabled_skill_returns_it_to_review(self) -> None:
        s = self.skill("owner", "close-guide", "Follow the checklist.")
        self.assertTrue(s["enabled"])
        r = self.c["editor"].put(f"/api/w/{self.ws}/skills/{s['id']}", json={"name": "close-guide", "description": "A guide here",
                                                                           "body": "EDITOR-TEXT", "tags": [], "enabled": True},
                                 headers=ORIGIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["enabled"])                       # a client-sent "enabled" is not trusted
        self.assertTrue(self.h.engine.db.one("SELECT 1 FROM audit_log WHERE action = 'skill.awaiting_approval'"))
        tid = self.thread("viewer")
        self.force_pins(tid, ["close-guide"])
        self.assertNotIn("EDITOR-TEXT", self.system_text(tid))
        self.assertEqual(self.c["admin"].put(f"/api/w/{self.ws}/skills/{s['id']}/enabled", json={"enabled": True},
                                             headers=ORIGIN).status_code, 200)
        tid2 = self.thread("viewer", ["close-guide"])
        self.assertIn("EDITOR-TEXT", self.system_text(tid2))        # once an admin approves the new text

    def test_permission_matrix(self) -> None:
        create = {"viewer": 403, "editor": 200, "admin": 200, "owner": 200}
        enabled_after_create = {"editor": False, "admin": True, "owner": True}
        for role, code in create.items():
            r = self.c[role].post(f"/api/w/{self.ws}/skills", json={"name": f"by-{role}", "description": "A guide here",
                                                                    "body": "x", "tags": [], "enabled": True}, headers=ORIGIN)
            self.assertEqual(r.status_code, code, (role, r.text))
            if code == 200:
                self.assertEqual(r.json()["enabled"], enabled_after_create[role], role)
        base = self.skill("owner", "matrix-guide", "v1")
        for role, code, stays_enabled in (("viewer", 403, True), ("admin", 200, True), ("owner", 200, True),
                                          ("editor", 200, False)):
            r = self.c[role].put(f"/api/w/{self.ws}/skills/{base['id']}", json={"name": "matrix-guide", "description": "A guide here",
                                                                              "body": f"v-{role}", "tags": []}, headers=ORIGIN)
            self.assertEqual(r.status_code, code, (role, r.text))
            self.assertEqual(self.enabled("matrix-guide"), stays_enabled, role)
        # an admin's edit never switches on a skill that is off
        r = self.c["admin"].put(f"/api/w/{self.ws}/skills/{base['id']}", json={"name": "matrix-guide", "description": "A guide here",
                                                                              "body": "v-admin-2", "tags": []}, headers=ORIGIN)
        self.assertFalse(r.json()["enabled"])
        for role, code in (("viewer", 403), ("editor", 403), ("admin", 200), ("owner", 200)):
            r = self.c[role].put(f"/api/w/{self.ws}/skills/{base['id']}/enabled", json={"enabled": True}, headers=ORIGIN)
            self.assertEqual(r.status_code, code, (role, r.text))


if __name__ == "__main__":
    unittest.main()
