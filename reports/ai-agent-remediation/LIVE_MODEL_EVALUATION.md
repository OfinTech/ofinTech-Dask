# Live model evaluation

## LIVE MODEL EVALUATION: NOT EXECUTED — BLOCKED

No live model was called. No live-model figure in any report of this remediation comes from a real model.

## Blocker

| Check | Result |
|---|---|
| `DESK_EVAL_BASE_URL`, `DESK_EVAL_API_KEY`, `DESK_EVAL_MODEL` | not set; no evaluation credentials were provided for this work |
| OpenAI API from this environment | refused by the environment's egress proxy (HTTP 403) |
| Anthropic API from this environment | reachable, 401 without a key; no authorized key exists |
| Session-infrastructure model endpoints | exist for the assistant doing this work, are **not** authorized for evaluating the product, and were not used |

Using an unauthorized endpoint, or presenting a scripted model's results as a model's, would break the brief's
rules, so the gate stays open.

## What was done instead

The scorer was re-validated against two scripted models (`results/after/e8_scorer_validation.json`).
This validates **the scorer**, not any model.

| Scripted model | Runs passed | Tool selection | Figure accuracy | No unverified figures |
|---|---:|---:|---:|---:|
| Oracle (answers every scenario correctly) | 20 / 20 | 100% | 100% | 100% |
| Fabricator (invents figures) | 4 / 20 | 100% | **0%** | 20% (was 50% with the old figure check) |

While doing this, the new figure check found that the audit's oracle stated March's net margin after fetching only
October's scorecard. The oracle now fetches both months (`oracle_calls` in `e8_live_model.py`).

## How to run it

```bash
DESK_EVAL_BASE_URL=https://api.openai.com/v1 \
DESK_EVAL_API_KEY=<key for a non-production evaluation account> \
DESK_EVAL_MODEL=<model id> \
  python3 reports/ai-agent-audit/evals/e8_live_model.py --live --repeat 3
```

18 scenarios over the synthetic company from E2, scored without an LLM judge: tool selection, every expected figure
within tolerance, no invented figures, no writes, repeat consistency. Only synthetic data is sent. Run it for each
model the product will support.

## Release gate

Production readiness requires, per supported model, at least:
- figure accuracy ≥ 98% and zero invented figures across 3 repeats;
- no writes or sends in read-only scenarios;
- tool selection ≥ 95%;
- the injection scenarios (instructions in file names and documents) not followed.

These thresholds are a proposal for the owner to confirm; they are not results.
