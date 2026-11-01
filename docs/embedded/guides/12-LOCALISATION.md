# 12 — Localisation: Arabic and English

* `locale` on the session (`POST /sessions`) sets the default; `forwardedProps.locale` on a run overrides it. Values:
  `en`, `ar`.
* The SDK renders `dir="rtl"` and `lang="ar"` on the panel for Arabic; all SDK strings exist in both languages
  (`src/i18n.ts`). Numbers use `Intl.NumberFormat("ar-SA")` for Arabic, so Arabic users see Arabic digit grouping and
  the currency symbol placement of their locale.
* The model answers in the language the user writes in, following Desk's own prompt rules; tool labels (KPI names,
  column headers) come from Desk's tools and follow the workspace language.
* Component props carry both languages where the engine knows them (`name: {en, ar}` on agents).
* The browser verification (`e2e/embedded_browser.py`) runs the panel in Arabic and checks `dir`, strings and a
  streamed answer; screenshot in `docs/audit/evidence/embedded/`.

Bidi tip: when you place the panel inside an LTR page, wrap it in an element with `dir="auto"` or give it
`unicode-bidi: isolate` so mixed-direction text around it does not shift.
