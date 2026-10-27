## What and why

## How it was tested

- [ ] `python -m unittest discover -s tests -t .` (engine)
- [ ] `npx tsc --noEmit` and `npm run i18n:check` (ui)
- [ ] Browser smoke test, if the UI changed
- [ ] Docs and CHANGELOG updated

## Checks

- [ ] No secrets, real data or real financial statements
- [ ] Model output is not rendered as HTML or executed; anything that leaves the app needs an approval
- [ ] New figures shown to people are traceable to a source or marked as unsourced
- [ ] No compliance or certification claims
