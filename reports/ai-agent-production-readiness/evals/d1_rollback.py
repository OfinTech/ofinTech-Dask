"""D1 — rollback: data written by this branch is served by the previous release (and the reverse).

    phase write:  python3 d1_rollback.py write <dir>   (run from engine/ of the version that writes)
    phase read:   python3 d1_rollback.py read <dir>    (run from engine/ of the version that reads)

Uses the test harness's settings in <dir> (synthetic data, scripted model). No schema migration is added by the
branch, so the check is that every conversation, run and tool message reads back and a new turn works.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from starlette.testclient import TestClient  # noqa: E402

from desk_engine.app import Engine, create_app  # noqa: E402
from tests.helpers import LOCAL_TOKEN, FakeLLM, Router, settings  # noqa: E402


def boot(d: Path, script):
    router = Router()
    llm = FakeLLM(router, script)
    keyfile = d / "test-master.key"           # the same key across restarts, as a real install keeps it
    if not keyfile.exists():
        import secrets
        keyfile.write_bytes(secrets.token_bytes(32))
    s = settings(d, "desktop", master_key=keyfile.read_bytes())
    engine = Engine(s, http_transport=router.transport())
    c = TestClient(create_app(s, engine=engine), base_url="http://localhost")
    c.__enter__()
    c.headers["authorization"] = f"Bearer {LOCAL_TOKEN}"
    return engine, c, llm


def turn(c, ws, text):
    import time
    tid = c.post(f"/api/w/{ws}/threads", json={}).json()["id"]
    c.post(f"/api/w/{ws}/threads/{tid}/messages", json={"text": text})
    for _ in range(400):
        d = c.get(f"/api/w/{ws}/threads/{tid}").json()
        if d["run"] and d["run"]["status"] in ("done", "error"):
            return d
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def main() -> None:
    phase, d = sys.argv[1], Path(sys.argv[2])
    engine, c, llm = boot(d, [[("calc_growth", {"values": [100, 120]})], "Growth was 20.00%.",
                             [("calc_growth", {"values": [100, 110]})], "Growth was 10.00%."])
    if phase == "write":
        ws = c.post("/api/workspaces", json={"name": "Rollback Ltd", "base_currency": "GBP"}).json()["id"]
        c.post(f"/api/w/{ws}/providers", json={"kind": "openai_compatible", "label": "Fake", "base_url": FakeLLM.BASE,
                                               "api_key": "sk-test"})
        d1 = turn(c, ws, "growth?")
        out = {"phase": "write", "status": d1["run"]["status"], "messages": len(d1["messages"])}
    else:
        r = c.get("/api/session")
        assert r.status_code == 200, (r.status_code, r.text[:300])
        ws = r.json()["workspaces"][0]["id"]
        threads = c.get(f"/api/w/{ws}/threads").json()
        threads = threads["threads"] if isinstance(threads, dict) else threads
        old = [c.get(f"/api/w/{ws}/threads/{t['id']}") for t in threads]
        d2 = turn(c, ws, "growth again?")
        out = {"phase": "read", "old_threads_read": sum(r.status_code == 200 for r in old), "old_threads": len(old),
               "old_messages": sum(len(r.json()["messages"]) for r in old), "new_turn": d2["run"]["status"]}
    c.__exit__(None, None, None)
    engine.db.close()
    print(json.dumps(out))


if __name__ == "__main__":
    main()
