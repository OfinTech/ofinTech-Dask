# Languages

The interface is available in **English, Arabic, Spanish and French**. Each person picks a language under
**Settings → Account → Language** (or on the sign-in page); signed-in people keep the choice on their account, so it
follows them to every device. Before sign-in the browser's language is used when it is one of these.

- **Arabic** switches the whole layout to right to left. Figures, codes, formulas and file contents keep their own
  direction, and every paragraph takes the direction of its first letter, so mixed Arabic and English text reads
  correctly.
- **Numbers and dates** follow the language's conventions, always with Western digits (as most finance teams'
  reports use): `1,234.56` in English, `1.234,56` in Spanish, `1 234,56` in French.
- **The assistant** answers in the language of your latest message, and uses your interface language when that is
  unclear. Account names, document titles and quoted text stay as they are.
- **Workflow templates** are shown in your language. Messages from the server (errors) are in English.

## How translation works

Interface text is written in English in the code, inside `tr("…")`, and looked up in a dictionary per language:
`ui/src/i18n/ar.ts`, `es.ts`, `fr.ts`. A missing entry shows English, so nothing breaks while a translation is
incomplete. Text with values uses placeholders: `tr("Delete {0} and all its versions?", { 0: file.name })`; labels
kept in tables are marked with `N("…")` and translated where they are shown.

```bash
cd ui
npm run i18n:check            # coverage per language, and the first missing strings
node scripts/i18n.mjs --keys  # every string, as JSON, to hand to a translator
```

## Adding a language

1. Copy `ui/src/i18n/fr.ts` to `ui/src/i18n/<code>.ts` and translate the values (keep `{0}`-style placeholders and
   `{{…}}` workflow variables unchanged).
2. Register it in `ui/src/lib/i18n.ts` (`LANGUAGES`, `DICTS` and the locale in `locale()`); set `dir: "rtl"` for a
   right-to-left language.
3. Allow the code in `ProfileIn.language` (`engine/desk_engine/routes/auth.py`), add its name to `LANGUAGE_NAMES`
   (`engine/desk_engine/agent/prompts.py`) and, optionally, template names in `engine/desk_engine/workflows/templates.py`.
4. Run `npm run i18n:check` and the browser smoke test.

The translations in this release were written for it and should be reviewed by finance people who are native
speakers before you roll the app out to them; corrections are welcome as pull requests.
