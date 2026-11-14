# 11 · Correction: the open-source edition ships no model (14 Nov 2026)

## What was wrong

Reports 01, 02 and 08 of this folder, and the earlier readiness and release-gate reports, treated the live-model
evaluation (E8) as a **release gate of this repository**, and counted it as **BLOCKED** for lack of "the
authorised OfinTech Desk model".

## The fact

The owner corrected this on 14 Nov 2026: **OfinTech Desk open source does not provide any model.** It agrees with
the open/paid split decided on 11 Nov 2026 (project notes, "open-core split"):
- the public repository carries "no learned weights, no model";
- the trained Ofin model (SystemOne) is a paid part, "never opened";
- each deployment of the open-source Desk brings its own provider and model: 13 provider kinds over the OpenAI and Anthropic protocols, including self-hosted runtimes.

## What changes

| Item | Before | Now |
|---|---|---|
| Live E8 gate (08) | BLOCKED | **NOT APPLICABLE** to the open-source release |
| E8's role | the release's accuracy test | the **operator's acceptance test** for the model they configure; for the paid edition, the test of SystemOne in that edition's own process |
| Accuracy category (09) | not scored, "E8 blocked" | not scored, "no model ships" (weight still excluded; overall 83 unchanged) |
| SLOs 3, 3b, 5 (04) | release SLOs waiting for E8 | **per-deployment** SLOs, set by each operator with their model |
| Remaining blockers to GO | E8, monitoring and alert delivery, resource limits at runtime, SLO approval | **monitoring and alert delivery, resource limits at runtime, SLO approval** |

## What does not change

- The decision is still **NO-GO**, for the three remaining blockers.
- The answers a user gets are only as good as the model they configure. **The release notes must say so** and point operators to E8 (`reports/ai-agent-audit/evals/e8_live_model.py`) and its proposed thresholds (02).
- The engine-side guarantees hold whatever the model:
  - approvals, roles, and the payment and sealed guards;
  - tenant isolation;
  - the figure check marking unsupported numbers;
  - completion checks that never store a cut answer as complete.

  They were tested against a scripted model that does whatever an attacker wants (05 of the readiness reports).
