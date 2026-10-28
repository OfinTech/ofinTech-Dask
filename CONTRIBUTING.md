# Contributing to OfinTech Desk

Thank you for helping. This project handles other people's financial data, so changes are judged first on whether
they keep that data safe and every figure traceable, and then on everything else.

## Ground rules

- **No secrets or real data** in commits, issues or test fixtures: no API keys, tokens, passwords, certificates,
  production data or real financial statements. Test data is invented. `server/.env.example` is the only settings file
  in the repository.
- **No vendor lock-in.** Model providers are configured, never hard-coded; new features must work with any provider
  the app supports, including local models.
- **The model is untrusted.** Never render HTML or run code it returns, never give it SQL, a shell or unrestricted
  database access, and route anything that leaves the app (e-mail, messages, external tools) through an approval.
- **No compliance claims** (ZATCA, e-invoicing, PDPL, SAMA, SOC 2 or others) and no pretending to connect to a bank or
  accounting system without a working, tested integration.
- **Figures must stay traceable.** A feature that puts numbers in front of a person shows where each one came from,
  or marks it as unsourced.

## Questions and discussion

Join the community on [Discord](https://discord.gg/hbzN6udBM) for questions, ideas and help with setting up or connecting a ledger. Open a
GitHub issue for bugs and feature requests, so they can be tracked.

- Do not post API keys, passwords, tokens, customer names or real financial figures on Discord. Use invented data in
  screenshots and examples.
- Report security problems privately as described in [SECURITY.md](SECURITY.md), not on Discord or in an issue.

## Development setup

```bash
pip install -r engine/requirements.txt
(cd ui && npm install && node build.mjs)
cd engine && DESK_DEV=1 DESK_UI_DIR=../ui/dist DESK_LOCAL_TOKEN=$(python -c "print('x'*40)") python -m desk_engine
```

Optional: `tesseract` with `eng ara spa fra` data for the OCR tests, LibreOffice for the formula recalculation test,
Python Playwright with Chromium for the browser smoke test.

## Before you open a pull request

```bash
cd engine && python -m unittest discover -s tests -t .      # engine tests
cd ui && npx tsc --noEmit && npm run i18n:check             # UI types and translations
cd desktop && npx tsc --noEmit -p tsconfig.json             # desktop shell
python e2e/ui_smoke.py                                      # browser smoke test (after building the UI)
```

- Add tests for new behaviour; tests use mocked HTTP (`tests/helpers.py`), never live services.
- New database changes go in a **new** migration at the end of `MIGRATIONS` in `engine/desk_engine/db.py`; never edit
  a migration that has been released.
- New interface text is written in English inside `tr("…")` (or `N("…")` in tables) and translated in
  `ui/src/i18n/{ar,es,fr}.ts`. If you cannot translate it, leave it out of the dictionaries: English is shown until
  someone adds it, and `npm run i18n:check` lists what is missing.
- Write interface text in plain words for finance people: what a thing does, not how it is built.
- Update the relevant page in `docs/` and add a line to `CHANGELOG.md` under *Unreleased*.

## Commits and reviews

Keep pull requests focused. Describe what changed, why, and how you tested it. A maintainer reviews every change; a
change that touches authentication, permissions, encryption, approvals or the agent's tools needs a second reviewer.

By contributing you agree that your contribution is licensed under the Apache License 2.0 (section 5 of the
licence). Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
