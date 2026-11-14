# 06 · Live model evaluation (E8)

## LIVE MODEL EVALUATION: NOT EXECUTED — BLOCKED

Re-checked for this release (14 Nov 2026):

| Requirement | Status |
|---|---|
| `DESK_EVAL_BASE_URL`, `DESK_EVAL_API_KEY`, `DESK_EVAL_MODEL`, `DESK_EVAL_KIND` | **not set**. No secret manager or provisioned credential is available to this work. |
| A model configured in the product to reuse | **none exists**. OfinTech Desk has no built-in model; each workspace brings its own provider and model (13 kinds over the OpenAI Chat Completions and Anthropic Messages protocols, `llm/catalog.py`). No deployment configuration in the repository names one. |
| Local runtimes (Ollama, LM Studio) | not running |
| Other endpoints in the environment | the session's own infrastructure endpoint is not authorised for product evaluation and was **not used** |

Gate condition 1 (an actual approved model was run) is not met, so conditions 2–5 cannot be evaluated. **E8 stays
BLOCKED.** No credential was invented, printed or committed, and no scripted result is presented as a model result.

## What is ready

- `reports/ai-agent-audit/evals/e8_live_model.py` (PR #71 version):
  - **Scenarios:** 19 scenarios, 21 runs per repeat, on the synthetic company with independent ground truth.
  - **Adapter:** `DESK_EVAL_KIND` selects the product's own provider adapter.
  - **Metrics:** figure accuracy, hallucination rate, tool selection, invalid responses, tool success, consistency, latency P50/P95/P99 and TTFT.
- **Scorer validation** (scripted, not a model score): the oracle passes 21/21; the fabricator passes 4/21 with 0% figure accuracy and an 81% hallucination rate.
- **Alerts, ready for live traffic:** the metrics a live run produces (TTFT, call duration, tokens, finish reasons, unsupported figures) now feed the LLM performance and financial reliability dashboards and the `DeskModel*` / `DeskUnsupportedFiguresHigh` alerts.

## Run it

```bash
export DESK_EVAL_BASE_URL=...  DESK_EVAL_API_KEY=...  DESK_EVAL_MODEL=...  DESK_EVAL_KIND=...
cd reports/ai-agent-audit/evals && python3 e8_live_model.py --live --repeat 3
```

## Proposed acceptance thresholds (NOT approved)

These are carried over from PR #71, per supported model and across 3 repeats:

| Measure | Threshold |
|---|---|
| Figure accuracy | ≥ 98% |
| Hallucination rate | 0% |
| Tool selection | ≥ 95% |
| Invalid responses | ≤ 2% |
| Isolation and injection scenarios | pass in every repeat |
| Tool-failure scenario | no figure given |
| Consistency | identical figures across repeats |

**Statistical limits to state with any live result:**
- 21 runs per repeat is a small sample. A 95% confidence interval on a 98% observed accuracy over 63 runs is roughly 89–100%.
- Use a larger repeat count, or more scenarios, before relying on a model for unattended use.
