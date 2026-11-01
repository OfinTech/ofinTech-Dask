# Skills

A skill is finance know-how the assistant follows: a checklist, a policy or a method. The format is the common
`SKILL.md` convention:

```markdown
---
name: expense-policy            # 2–64 lowercase letters, digits, hyphens
description: Our travel and expense limits; use for any question about expense approvals.   # when to use it
version: 1.0.0
tags: [policy]
---

# Expense policy
- Hotels: up to 180 per night ...
```

**How the assistant uses skills.**
- The system prompt lists each enabled skill's name and description.
- When a request matches, the model calls `load_skill` to read the full text (and `read_skill_file` for reference
  files). This keeps prompts short while skills grow.
- A conversation or workflow step can **pin** skills, so they are always loaded.

**Managing skills (Skills page).**
- Turn built-in skills on or off per workspace (admins).
- Write a new skill with a live preview, or duplicate a built-in one to make your own version. A skill's text
  becomes part of the assistant's instructions, so a skill written or imported by an editor stays off until an
  admin turns it on; a skill made by an admin is on at once. A custom skill cannot take a built-in's name.
- Import a `SKILL.md` or a `.zip` containing one `SKILL.md` plus reference files (`.md`, `.txt`, `.csv`, `.json`; at
  most 20 files and 1 MB). Scripts and binaries in an archive are ignored and reported. **Nothing in a skill is ever
  executed.**
- Export any skill as `SKILL.md`.

**Built-in skills.** month-end-close, bank-reconciliation, variance-analysis, cash-flow-forecast, ar-collections,
ap-review, financial-statement-analysis, management-report, budget-builder, revenue-recognition (IFRS 15 / ASC 606),
expense-audit, sales-tax-overview, invoice-data-extraction, kpi-dashboard-design, fx-revaluation. They live in
`engine/desk_engine/skills/builtin/` and avoid country-specific rates. Where rules differ by country, they tell the
assistant to confirm with official sources.
