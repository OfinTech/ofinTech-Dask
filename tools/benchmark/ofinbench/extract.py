"""Getting the text out of a PDF so it can be anonymised, since the PDF itself never is.

This is deliberately plain: pypdf's text layer, one file per page plus a combined file. A statement exported from
online banking has a real text layer and comes out clean. A scan has none, and this will produce almost nothing --
which is the correct outcome, because a scan needs OCR, and running OCR here would hide from the operator that the
text they are about to sign off as ground truth was guessed by a recogniser rather than read.
"""

from __future__ import annotations

import pathlib


def extract_pdf(src: pathlib.Path, out_dir: pathlib.Path) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(str(src))
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        pages.append(text)
        (out_dir / f"{src.stem}.p{i:03}.txt").write_text(text, encoding="utf-8")
    combined = out_dir / f"{src.stem}.txt"
    combined.write_text("\n\n".join(pages), encoding="utf-8")
    characters = sum(len(p.strip()) for p in pages)
    return {
        "pages": len(pages),
        "characters": characters,
        "combined": str(combined),
        "warning": (
            "almost no text came out: this is very likely a scan. Run it through OCR yourself, read the result, and "
            "correct it before using it as ground truth -- do not sign off text a recogniser guessed."
        ) if characters < 40 * len(pages) else "",
    }
