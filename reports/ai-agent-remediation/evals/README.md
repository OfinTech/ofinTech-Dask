# Remediation evaluations

| File | What it does |
|---|---|
| `e10_figure_provenance.py` | Adversarial evaluation of the figure check: 24,000 synthetic answers in 16 classes (seed 1113), old check vs new, with precision, recall and per-class rates. |
| `baseline_figure_check.py` | The old figure check, verbatim from `bd1e952`, used as E10's "before". |

E1–E9 are the audit's scripts in `reports/ai-agent-audit/evals/`; five corrections to them are described in
`../REMEDIATION_SUMMARY.md` (none changes what a pass means, and each was re-run on the old code).

```bash
cd reports/ai-agent-audit/evals
python3 e1_calculators.py; python3 e2_ledger_grounding.py; python3 e3_reliability.py
python3 e5_tenant_isolation.py; python3 e6_benchmarks.py; python3 e7_instruction_surfaces.py
python3 e8_live_model.py --dry-run; python3 e9_memory_growth.py
cd ../../ai-agent-remediation/evals && python3 e10_figure_provenance.py
```

Results: `../results/before/` (on `main` `00d93f8`; `*.as_audited.json` from the scripts as audited) and
`../results/after/` (this branch).
