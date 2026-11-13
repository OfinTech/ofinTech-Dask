"""Request size of the 60-message window filled with maximum-size messages (AI-11). Run from engine/."""
import json, sys, time
sys.path.insert(0, ".")
from tests.helpers import Harness
from desk_engine.db import new_id
h = Harness(); ws = h.setup_workspace()
tid = h.api("POST", f"/api/w/{ws}/threads", json={})["id"]
ts = time.time() - 10000
for i in range(29):
    for n, (role, c) in enumerate((("user", ("Our fiscal year ends on 31 March. " if i == 0 else "") + "u" * 20000),
                                   ("assistant", f"Answer {i}. " + "a" * 8000))):
        h.engine.db.insert("messages", {"id": new_id("m_"), "thread_id": tid, "role": role, "content": c[:20000],
                                        "payload": "{}", "created_at": ts + 2 * i + n})
h.llm.script = ["Noted."]
h.api("POST", f"/api/w/{ws}/threads/{tid}/messages", json={"text": "And now?"}); h.wait_run(tid, 20)
msgs = h.llm.calls[-1]["messages"]
total = sum(len(m.get("content") or "") for m in msgs)
print(json.dumps({"request_chars": total, "approx_tokens": total // 4, "messages": len(msgs),
                  "fiscal_fact_sent": any("fiscal year ends" in (m.get("content") or "") for m in msgs)}))
h.close()
