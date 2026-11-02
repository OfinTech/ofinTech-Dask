"""Money figures must never leave the box that holds them.

A KPI card, a dashboard widget and the ledger totals each show one figure at a large type size inside a cell whose
width the layout decides. Before this check existed, a figure in the billions painted over the cell border and into
the card beside it. The rule the stylesheet now follows is: the figure never wraps mid-number, never gets clipped,
and shrinks with its own cell (container queries) down to a floor; below that the layout gives it a wider cell
instead. This renders the three patterns straight from ui/src/styles.css at several widths, in English and in
Arabic (right to left), and fails if any figure is wider than the box around it.

    python e2e/figures_fit.py [out-dir]        # writes figures-fit.png next to the other e2e evidence
"""

from __future__ import annotations

import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = (ROOT / "ui" / "src" / "styles.css").read_text(encoding="utf-8")

# the widest figures a Gulf ledger realistically shows: eight and ten significant digits with a currency code
BIG, HUGE = "SAR 12,345,678.90", "SAR 1,234,567,890.12"
WIDTHS = (1440, 1280, 1024, 900, 760, 560, 420, 380)

PAGE = f"""<!doctype html><html lang="LANG" dir="DIR"><head><meta charset="utf-8"><style>{CSS}</style></head>
<body class="app"><div style="padding:16px">
<div class="kpis">
  <div class="kpi"><div class="label">Revenue</div><div class="value">{BIG}</div></div>
  <div class="kpi"><div class="label">Net profit</div><div class="value">{HUGE}</div></div>
  <div class="kpi"><div class="label">Ending cash</div><div class="value">{BIG}</div></div>
  <div class="kpi"><div class="label">Receivables</div><div class="value">{HUGE}</div></div>
</div>
<div class="dash-grid" style="margin-top:16px">
  <div class="widget"><h3>Ending cash</h3><div class="big">{HUGE}</div></div>
  <div class="widget"><h3>Revenue</h3><div class="big">{BIG}</div></div>
  <div class="widget"><h3>Expenses</h3><div class="big">{BIG}</div></div>
  <div class="widget w2"><h3>Gross margin</h3><div class="big">{BIG}</div></div>
</div>
<dl class="tally" style="margin-top:16px">
  <div><dt>Debit</dt><dd>{HUGE}</dd></div>
  <div><dt>Credit</dt><dd>{HUGE}</dd></div>
  <div><dt>Difference</dt><dd>0.00</dd></div>
</dl>
</div></body></html>"""

MEASURE = """() => {
  const bad = [];
  for (const sel of [".kpi .value", ".widget .big", ".tally dd"]) {
    document.querySelectorAll(sel).forEach((el, i) => {
      const box = el.closest(".kpi, .widget, .tally div");
      const wider = el.scrollWidth > el.clientWidth + 1;
      const spills = el.getBoundingClientRect().width > box.clientWidth + 1;
      if (wider || spills) bad.push(`${sel}[${i}] "${el.textContent}" needs ${el.scrollWidth}px, has ${el.clientWidth}px`);
    });
  }
  return bad;
}"""


def main(out_dir: str | None) -> int:
    out = pathlib.Path(out_dir or (ROOT / "e2e-output"))
    out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for width in WIDTHS:
            for lang, direction in (("en", "ltr"), ("ar", "rtl")):
                page.set_viewport_size({"width": width, "height": 900})
                page.set_content(PAGE.replace("LANG", lang).replace("DIR", direction))
                bad = page.evaluate(MEASURE)
                print(f"{width:>5}px {lang}: {'ok' if not bad else 'OVERFLOW'}")
                failures += [f"{width}px {lang}: {b}" for b in bad]
        page.set_viewport_size({"width": 1000, "height": 760})
        page.set_content(PAGE.replace("LANG", "en").replace("DIR", "ltr"))
        page.screenshot(path=str(out / "figures-fit.png"), full_page=True)
        browser.close()
    if failures:
        print("\nFigures leaving their box:")
        for f in failures:
            print("  " + f)
        return 1
    print(f"\nEvery figure fits at {len(WIDTHS)} widths in both directions. Screenshot: {out / 'figures-fit.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
