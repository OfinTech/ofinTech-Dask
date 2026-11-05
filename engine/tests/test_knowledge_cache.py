"""Retrieval housekeeping: the embedded flag after a re-save, the bounded matrix cache, and the search log.

These are the parts that stay quiet when they go wrong. A workspace whose documents all carry vectors can
report that none of them do, a cache with no ceiling grows until the process dies, and a search log is the
easiest place to spill the contents of the thing being searched.
"""

import json
import logging
import unittest

import httpx2 as httpx
import numpy as np

from tests.helpers import FakeLLM, Harness
from tests.test_knowledge import CONTRACT, POLICY, fake_embedding

QUERY = "renews automatically notice"


class EmbeddedFlag(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()
        self.embed_calls = 0

        def embeddings(req: httpx.Request) -> httpx.Response:
            self.embed_calls += 1
            body = json.loads(req.content)
            return httpx.Response(200, json={"data": [{"index": i, "embedding": fake_embedding(t)}
                                                      for i, t in enumerate(body["input"])]})

        self.h.router.add("POST", f"{FakeLLM.BASE}/embeddings", embeddings)
        self.h.api("POST", f"/api/w/{self.ws}/documents",
                   files=[("files", (n, b)) for n, b in {"msa.pdf": CONTRACT, "policy.docx": POLICY}.items()])
        self.h.settle()
        self.provider = self.h.api("GET", f"/api/w/{self.ws}/providers")["providers"][0]["id"]

    def tearDown(self) -> None:
        self.h.close()

    def set_model(self, model: str | None) -> dict:
        body = {"provider_id": self.provider, "model": model} if model else {"provider_id": None, "model": None}
        out = self.h.api("PUT", f"/api/w/{self.ws}/knowledge/embeddings", json=body)
        self.h.settle()
        return out

    def stats(self) -> dict:
        return self.h.api("GET", f"/api/w/{self.ws}/knowledge/settings")["stats"]

    def test_resaving_the_same_model_leaves_every_document_embedded(self) -> None:
        """Saving the embedding settings re-queues the workspace. Saving them unchanged used to clear the flag
        for every document and then find nothing to embed, so the workspace reported nothing as embedded while
        semantic search went on working -- and on a large workspace it walked every document to get there."""
        first = self.set_model("fake-embed")
        self.assertEqual(first["documents_queued"], 2)
        self.assertEqual(self.stats()["embedded"], 2)
        calls = self.embed_calls

        again = self.set_model("fake-embed")

        self.assertEqual(again["documents_queued"], 0)             # nothing needed doing
        # The one extra provider call is the save validating the key and model, not a document being re-sent.
        self.assertEqual(self.embed_calls, calls + 1)
        self.assertEqual(self.stats()["embedded"], 2)              # the flag survived the re-save
        self.assertEqual(self.h.engine.db.scalar(
            "SELECT COUNT(*) FROM doc_chunks WHERE workspace_id = ? AND embedding IS NULL", (self.ws,)), 0)
        self.assertTrue(self.h.api("POST", f"/api/w/{self.ws}/documents/search", json={"query": QUERY}))

    def test_changing_the_model_requeues_and_re_embeds(self) -> None:
        self.set_model("fake-embed")
        calls = self.embed_calls

        changed = self.set_model("fake-embed-v2")

        self.assertEqual(changed["documents_queued"], 2)
        self.assertGreater(self.embed_calls, calls)
        self.assertEqual(self.stats()["embedded"], 2)
        models = {r["embedding_model"] for r in self.h.engine.db.all(
            "SELECT DISTINCT embedding_model FROM doc_chunks WHERE workspace_id = ?", (self.ws,))}
        self.assertEqual(models, {"fake-embed-v2"})

    def test_embedding_a_document_whose_chunks_are_already_current_restores_the_flag(self) -> None:
        """Covers the repair directly, for any caller that clears the flag without re-embedding."""
        self.set_model("fake-embed")
        doc_id = self.h.engine.db.scalar("SELECT id FROM documents WHERE workspace_id = ? LIMIT 1", (self.ws,))
        self.h.engine.db.execute("UPDATE documents SET embedded = 0 WHERE id = ?", (doc_id,))

        self.h.client.portal.call(self.h.engine.knowledge.embed_document, self.ws, doc_id)

        self.assertEqual(self.h.engine.db.scalar("SELECT embedded FROM documents WHERE id = ?", (doc_id,)), 1)

    def test_turning_embeddings_off_queues_nothing(self) -> None:
        self.set_model("fake-embed")
        self.assertEqual(self.set_model(None)["documents_queued"], 0)
        self.assertEqual(self.h.engine.knowledge.reembed_all(self.ws), 0)


class MatrixCache(unittest.TestCase):
    """The cache holds one float32 matrix per (workspace, model). Unbounded, that is how a server with many
    workspaces runs out of memory without any single workspace looking large."""

    def setUp(self) -> None:
        self.h = Harness(knowledge_cache_mb=1)
        self.k = self.h.engine.knowledge

    def tearDown(self) -> None:
        self.h.close()

    def plant(self, ws: str, model: str, rows: int) -> tuple[str, str]:
        key = (ws, model)
        self.k._matrix_cache[key] = ((rows, rows), np.zeros((rows, 64), dtype=np.float32), list(range(rows)))
        return key

    def test_evicts_until_inside_the_budget(self) -> None:
        a = self.plant("ws_a", "m", 2500)        # ~0.61 MB each, so two do not fit in 1 MB
        b = self.plant("ws_b", "m", 2500)

        self.k._evict(keep=b)

        self.assertEqual(list(self.k._matrix_cache), [b])
        self.assertLessEqual(self.k.cache_bytes(), 1048576)
        self.assertNotIn(a, self.k._matrix_cache)

    def test_evicts_least_recently_used_first(self) -> None:
        a = self.plant("ws_a", "m", 1800)
        b = self.plant("ws_b", "m", 1800)
        c = self.plant("ws_c", "m", 1800)        # three at ~0.44 MB: one of the three has to go
        self.k._matrix_cache.move_to_end(a)      # a was just used, so b is now the oldest

        self.k._evict(keep=c)

        self.assertNotIn(b, self.k._matrix_cache)
        self.assertIn(a, self.k._matrix_cache)
        self.assertIn(c, self.k._matrix_cache)

    def test_a_workspace_larger_than_the_budget_is_kept_and_reported(self) -> None:
        """Dropping the matrix this query needs would mean rebuilding it from SQLite on every search. It is
        kept, and the operator is told, because that is the signal the workspace has outgrown a full scan."""
        big = self.plant("ws_big", "m", 8000)    # ~1.95 MB against a 1 MB budget

        with self.assertLogs("desk.knowledge", level="WARNING") as caught:
            self.k._evict(keep=big)

        self.assertIn(big, self.k._matrix_cache)
        self.assertIn("over the", "\n".join(caught.output))

    def test_no_limit_when_the_budget_is_zero(self) -> None:
        h = Harness(knowledge_cache_mb=0)
        try:
            k = h.engine.knowledge
            k._matrix_cache[("ws_a", "m")] = ((1, 1), np.zeros((4000, 64), dtype=np.float32), [1])
            k._matrix_cache[("ws_b", "m")] = ((1, 1), np.zeros((4000, 64), dtype=np.float32), [1])
            k._evict(keep=("ws_b", "m"))
            self.assertEqual(len(k._matrix_cache), 2)
        finally:
            h.close()

    def test_forget_releases_one_workspace_only(self) -> None:
        self.plant("ws_a", "m", 10)
        self.plant("ws_a", "other-model", 10)
        kept = self.plant("ws_b", "m", 10)

        self.k.forget("ws_a")

        self.assertEqual(list(self.k._matrix_cache), [kept])


class SearchMeasurement(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.ws = self.h.setup_workspace()

        def embeddings(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            return httpx.Response(200, json={"data": [{"index": i, "embedding": fake_embedding(t)}
                                                      for i, t in enumerate(body["input"])]})

        self.h.router.add("POST", f"{FakeLLM.BASE}/embeddings", embeddings)
        self.h.api("POST", f"/api/w/{self.ws}/documents", files=[("files", ("msa.pdf", CONTRACT))])
        self.h.settle()
        self.h.api("PUT", f"/api/w/{self.ws}/knowledge/embeddings", json={
            "provider_id": self.h.api("GET", f"/api/w/{self.ws}/providers")["providers"][0]["id"],
            "model": "fake-embed"})
        self.h.settle()

    def tearDown(self) -> None:
        self.h.close()

    def search(self, query: str = QUERY) -> tuple[list, str]:
        with self.assertLogs("desk.knowledge", level="INFO") as caught:
            hits = self.h.api("POST", f"/api/w/{self.ws}/documents/search", json={"query": query})
        return hits, "\n".join(caught.output)

    def test_every_search_records_what_it_cost(self) -> None:
        hits, logged = self.search()

        self.assertTrue(hits)
        for field in ("chunks=", "cache=", "semantic=ok", "keyword_ms=", "semantic_ms=", "total_ms=",
                      "cache_mb=", "returned="):
            self.assertIn(field, logged, field)
        self.assertIn(self.ws, logged)

    def test_the_log_never_carries_the_query_or_the_documents(self) -> None:
        """A search line is written on every lookup, including the ones an agent makes over a customer's
        ledger. Counts and timings are safe to keep; what was asked and what came back are not."""
        secret = "northwind"                                     # a word that is in the document and the query
        hits, logged = self.search(f"{secret} termination notice")

        self.assertTrue(hits)
        for leaked in (secret, "termination", "msa.pdf", hits[0]["text"][:40]):
            self.assertNotIn(leaked.lower(), logged.lower(), leaked)

    def test_the_matrix_is_reused_and_rebuilt_when_the_chunks_change(self) -> None:
        self.search()
        _, second = self.search()
        self.assertIn("cache=hit", second)

        self.h.api("POST", f"/api/w/{self.ws}/documents", files=[("files", ("policy.docx", POLICY))])
        self.h.settle()

        _, after = self.search()
        self.assertIn("cache=build", after)                       # new chunks invalidate the cached matrix

    def test_deleting_the_workspace_releases_its_matrix(self) -> None:
        self.search()
        self.assertTrue(self.h.engine.knowledge.cache_bytes())

        self.h.api("DELETE", f"/api/w/{self.ws}", expect=None)

        self.assertEqual(self.h.engine.knowledge.cache_bytes(), 0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main()
