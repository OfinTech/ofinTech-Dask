# Design system

OfinTech Desk should look and feel like an intelligent financial command centre, not a traditional accounting
application: simple for executives, powerful for CFOs, precise for accountants, transparent for accounting managers.
One unified application, one identity, in Arabic and English (Spanish and French follow the English layout).

Everything below is implemented in `ui/src/styles.css`; the website (`landing/site/index.html`) carries the same
tokens in its own stylesheet.

## Colour

Colours live in three layers. Components use only the third; a palette change is made in the first.

**1. Brand primitives (`--ofin-*`)** — the official OfinTech palette.

| Token | Value | Role |
|---|---|---|
| `--ofin-navy` | `#14243A` | Deep Navy — brand foundation: navigation rail, brand tile, dark bands, toasts |
| `--ofin-blue` | `#2563EB` | Primary Blue — the one interactive colour: buttons, links, selected state, series 1 |
| `--ofin-blue-2` | `#3B82F6` | Secondary Blue — focus ring, forecast values, interactive colour in dark mode |
| `--ofin-blue-light` | `#EFF6FF` | Light Blue — selected and tinted surfaces |
| `--ofin-white` | `#FFFFFF` | |
| `--ofin-bg` | `#F8FAFC` | main background |
| `--ofin-surface` | `#FFFFFF` | panels, cards, tables |
| `--ofin-text` | `#172033` | primary text and actual figures |
| `--ofin-text-2` | `#64748B` | secondary text, labels, budget figures |
| `--ofin-border` | `#E2E8F0` | borders and rules |
| `--ofin-muted` | `#F1F5F9` | muted background, zebra rows |
| `--ofin-success` | `#16A34A` | on target, matched, approved |
| `--ofin-warning` | `#D97706` | watch, pending, unverified |
| `--ofin-error` | `#DC2626` | act, failed, rejected, negative |
| `--ofin-info` | `#0284C7` | information, AI estimates |

No other colour is introduced anywhere. Hover shades, tertiary text and soft backgrounds are derived from these
with `color-mix()`, never hard-coded.

**2. Semantic tokens (`--color-*`)** — what a colour is for: `--color-bg`, `--color-surface`,
`--color-surface-muted`, `--color-surface-selected`, `--color-text`, `--color-text-secondary`,
`--color-text-tertiary`, `--color-border`, `--color-border-strong`, `--color-brand`, `--color-on-brand`,
`--color-primary`, `--color-primary-hover`, `--color-primary-soft`, `--color-on-primary`, `--color-focus`,
`--color-success`, `--color-warning`, `--color-error`, `--color-info` (each with a `-soft` background),
`--color-scrim`, the rail tokens (`--rail-bg`, `--rail-text`, `--rail-active` …), chart series `--series-1…8`, and
the provenance colours `--prov-actual`, `--prov-forecast`, `--prov-budget`, `--prov-estimate`, `--prov-unverified`.

**3. Component aliases** — the short names the components were written with (`--paper`, `--panel`, `--sunk`,
`--ink`, `--ink-2`, `--ink-3`, `--rule`, `--rule-strong`, `--band`, `--blue`, `--blue-ink`, `--blue-wash`, `--red`,
`--amber`, `--green` and their `-wash` backgrounds, `--focus`, `--shadow`). They point at the semantic tokens.

**Dark mode** (system preference or Settings) keeps the same primitives on navy surfaces: the background is navy
darkened, panels are navy, the interactive colour lifts to Secondary Blue and the semantic colours lighten for
contrast. The rail is navy in both modes.

**Charts** use the palette only: series 1 Primary Blue, 2 Deep Navy, 3 Information, 4 Success, 5 Warning,
6 Secondary text, 7 a light blue tint, 8 Error. Adjacent series differ in hue or lightness.

## Typography

| | Font | Notes |
|---|---|---|
| English, Spanish, French | **Inter** (variable, 100–900, optical sizing) | `ui/src/fonts/InterVariable.woff2`, SIL OFL 1.1 |
| Arabic | **IBM Plex Sans Arabic** (Regular, Medium, SemiBold, Bold) | `ui/src/fonts/IBMPlexSansArabic-*.woff`, SIL OFL 1.1 |

Both are bundled, so the app works offline and under the strict CSP; nothing is fetched from a font service.

Scale (`--t-*`): 12 (xs: table headers, badges, supporting text), 13 (sm: labels, table body), 14 (md: body),
16 (lg: card titles, h3), 20 (xl: section headings, h2), 26 (2xl: page titles, KPI values), 34 (3xl: hero figures),
44 (4xl: the one number on an executive screen). Weights: body 400, labels and buttons 520–560, headings 600–680.

**Figures** always use tabular numerals (`font-feature-settings: "tnum"` on `body`, `font-variant-numeric` on
`.num`, `.kpi .value`, `.kpi-value`, `.tally dd`, `.fig`). In Arabic, figures keep Inter's digits
(`--font-figures`), stay left-to-right and isolated (`unicode-bidi: isolate`), so currency symbols, percentages,
dates and account numbers read correctly inside right-to-left text. Letter-spacing is never applied to Arabic.

## Layout and surfaces

- The shell is a 228 px navy rail and a white-on-`--color-bg` main area; pages are `max-width: 1240px` with
  24/32/48 px padding.
- Panels and widgets: `--color-surface`, 1 px `--color-border`, radius `--radius-lg` (10 px); controls use
  `--radius` (6 px). Shadows are subtle (`--shadow`) and used only for floating surfaces.
- Tables (`.ledger`): 12 px uppercase-free headers on the surface colour with a strong bottom rule, zebra rows on
  the muted surface, figures right-aligned in tabular numerals, totals in a `tfoot` with a strong top rule.
- Spacing scale `--s1…--s7`: 4, 8, 12, 16, 24, 32, 48 px.
- Avoid gradients, decorative animation, crowded dashboards and nested cards. Motion is limited to the typing
  caret and the spinner, both switched off under `prefers-reduced-motion`.

## Value provenance

Every figure that is not a booked actual says what it is (`.prov` badge):

| Class | Meaning | Colour |
|---|---|---|
| `.prov.actual` | booked in the ledger | text on muted |
| `.prov.forecast` | approved forecast or plan | Secondary Blue on Light Blue |
| `.prov.budget` | budget | secondary text, outlined |
| `.prov.estimate` | AI-generated estimate | Information on its soft background |
| `.prov.unverified` | not matched to a source | Warning on its soft background |

An estimate is never presented as an actual result.

## Role-based experiences

The product is one application with four connected experience levels. The level is **derived from the member's
permission role** — it never grants access; the server enforces permissions independently of what the interface
shows.

| Level | Role | Default landing | What it shows |
|---|---|---|---|
| Executive | viewer | Executive overview | financial health, revenue, expenses, net profit, cash, cash flow, risks, AI insights, pending decisions — in plain language, every KPI with a one-line explanation |
| CFO / Finance manager | admin | Finance analysis | statements, profitability, cash-flow forecast, budget versus actual, ratios, working capital, receivables and payables, scenarios, trends, anomalies |
| Accountant | editor | Accounting operations | journal lines, ledger, bank reconciliation and the month-end close, imports, documents, validation, evidence, audit trail |
| Accounting manager | editor with approval duties | Accounting oversight | reconciliation status, unmatched transactions, pending approvals, exceptions, team activity, missing documents, close progress, outstanding tasks |

Anyone can switch between the views their role permits (Executive overview → Finance analysis → Accounting
operations); the switch only changes what is shown.

**Drill-down** runs from any executive KPI to the records behind it — net profit → profitability → expense breakdown
→ the transactions of one category → one transaction with its document, source and audit history — keeping the
reporting period, the filters and a breadcrumb trail, with a way back at every step, and with permissions checked
at every level.

## Acceptance tests

- **CEO:** someone with no accounting background understands the company's financial health within 30 seconds.
- **CFO:** profitability, cash flow, risks and performance can be investigated without unnecessary navigation.
- **Accountant:** detailed workflows complete without losing any professional tool.
- **Accounting manager:** team activity, reconciliation exceptions, approvals and outstanding work are visible.
- **Drill-down:** from an executive KPI to its transactions, context preserved, permissions respected.
- **Bilingual:** all four experiences work in Arabic (RTL) and English (LTR).

## Brand assets

The OFINTECH logo (`docs/brand`, drawn in `ui/src/components/ui.tsx`) is never modified or distorted: the brand
tile is the monogram on Primary Blue, the wordmark takes the current text colour.
