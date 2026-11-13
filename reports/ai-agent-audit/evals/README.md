# AI agent audit: evaluation suites

These suites are separate from the application and from the engine's test suite. They import the engine and its
test helpers read-only, run everything in temporary directories, and write JSON only to `../results/`.

| Suite | What it measures | Runtime |
|---|---|---|
| `e1_calculators.py` | 1,385 calculator cases against an independent 50-digit implementation | ~1 min |
| `e2_ledger_grounding.py` | 86 checks of ledger KPIs against ground truth summed from a synthetic company | ~10 s |
| `e3_reliability.py` | 17 failure scenarios for the model, tools and conversation size | ~30 s |
| `e4_figure_check.py` | Detection and false-positive rates of the unverified-figures check (16,000 answers) | ~1 min |
| `e5_tenant_isolation.py` | Every read tool in workspace B called with workspace A's identifiers | ~1 min |
| `e6_benchmarks.py` | Engine overhead per turn, concurrency, tool latency by data size, retrieval latency (`--quick` for a short run) | ~10 min |
| `e7_instruction_surfaces.py` | Which member-written or data-derived text reaches the model's system role (Team Server, three roles) | ~10 s |
| `e8_live_model.py` | 18 scenarios with ground truth for a **real model** (`--live`). `--dry-run` validates the scorer with scripted models. | live: depends on model |
| `e9_memory_growth.py` | Engine memory per conversation over 1,500 conversations | ~1 min |

Run each from the repository root, for example `python3 reports/ai-agent-audit/evals/e1_calculators.py`. Python
3.12+ and the engine's requirements (`engine/requirements.txt`, `engine/requirements-dev.txt`) are needed.

The model is a scripted OpenAI-compatible endpoint on a mocked transport (`engine/tests/helpers.py`), so each suite
measures what **the engine** does. Only `e8_live_model.py --live` measures a model, and it refuses to run without
`DESK_EVAL_BASE_URL`, `DESK_EVAL_API_KEY` and `DESK_EVAL_MODEL`.
