# OfinTech — brand

## The line

> **ذكاء مالي، ولكل رقم مصدره.**
> **Financial intelligence, sourced.**

A descriptor, not a slogan: it says what the company does and the one thing that separates it, in a single breath.
It belongs **with the wordmark** — under it in a footer, beside it on a deck cover, below it in an e-mail
signature. It is not a headline and it does not open a page on its own.

Where it is already in use: the footer of every page on the site, the panel on the sign-in and sign-up pages, and
the sign-off of the 90-second film.

### Why not a benefit line

Lines like *“smarter finances, better decisions”* fit every accounting product, every analytics tool and every
bank, so they carry nothing. Worse, a growth or outcome promise is one the product cannot keep — and a finance
lead who disbelieves the first sentence reads the rest of the page with his guard up. The page earns its
credibility by claiming only what the product does: answers from your own books, every figure with its source,
and limits that are built in rather than configured. Spend that credibility on nothing else.

Never add a compliance, certification or accuracy claim to any brand material. The product holds none, and
`site/docs.html` says so plainly.

## The headline

The site's own headline is sharper than the descriptor and does a different job:

> **الرقم الذي توقّع تحته. ومن أين جاء.**
> **The figure you sign your name under. And where it came from.**

It names a moment a finance lead knows by heart, and it names the differentiator. Keep it for the hero of a page
or the close of a film; keep the descriptor for everywhere the wordmark appears quietly.

## Assets

| File | Use |
|---|---|
| `ofintech-wordmark-light.svg` | on the navy field, or any dark surface |
| `ofintech-wordmark-dark.svg` | on paper and white surfaces |
| `ofintech-icon.svg` | the mark on its own: favicons, app icons, avatars |
| `ofintech-icon-512.png` | where SVG is not accepted |

Clear space around the wordmark: at least the height of the "O". Never set the wordmark in a colour other than
white or the navy, never on a photograph, never stretched.

## Palette and type

The site and the application share one palette and one pair of typefaces; the tokens live in `ui/src/styles.css`
and `site/styles.css`.

| | |
|---|---|
| Deep navy `#14243A` | the brand field: heroes, the footer, the film |
| Primary blue `#2563EB` | the interactive colour, and the only accent |
| Paper `#F8FAFC` / white `#FFFFFF` | surfaces |
| Ink `#172033` / `#64748B` | text and secondary text |
| Green `#16A34A` | reserved for *sourced* and *verified*, nothing else |
| Red `#B91C1C` | reserved for a negative figure or a refusal |

Type: **Inter** for Latin and for every figure (tabular, always), **IBM Plex Sans Arabic** for Arabic. Figures are
the subject matter — set them in tabular numerals, never let one wrap mid-number, and give a negative figure its
own direction so the minus sits in front of it in Arabic.

## The film

`film/` holds the 90-second piece and how it is made.
