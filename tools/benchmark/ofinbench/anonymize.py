"""Replacing the identifiers a document carries, and then proving none are left.

The two commands are `apply` and `verify`, and the second is the one that matters. Anonymisation that is only as
good as the tool's recall is not anonymisation; the accountant signing it off needs a check that fails loudly. So
`verify` re-scans the output with the same detector plus the supplied name list and exits non-zero on any hit,
and it is run on the output rather than trusted from the apply step.

Formats: text, CSV, TSV, Markdown, JSON and XLSX are rewritten in place in a copy. A PDF is extracted to text and
anonymised as text, and the PDF itself is never rewritten. Removing text from a PDF so that it cannot be recovered
is not something to do by default -- a redaction box drawn over a name leaves the name in the content stream -- and
a benchmark that silently shipped such a file would publish the client data it was built to protect.
"""

from __future__ import annotations

import csv
import io
import json
import pathlib

from . import pseudonym
from .detect import Finding, scan
from .numbers import fold_digits

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".jsonl"}
REPLACERS = {
    "iban": pseudonym.fake_iban,
    "account": pseudonym.fake_account,
    "gov_id": pseudonym.fake_gov_id,
    "vat": pseudonym.fake_vat,
    "phone": pseudonym.fake_phone,
    "email": pseudonym.fake_email,
    "name": pseudonym.fake_name,
    "org": pseudonym.fake_org,
}


class AnonymizeError(Exception):
    """The input cannot be anonymised safely in the format it arrived in."""


def replacement(key: bytes, finding: Finding) -> str:
    """The stand-in for one finding. Unclassified runs are left alone: the report asks a human about them."""
    if finding.kind == "unclassified":
        return finding.value
    make = REPLACERS[finding.kind]
    canonical = fold_digits(finding.value).strip()
    if finding.kind in {"iban", "account", "gov_id", "vat", "phone"}:
        canonical = "".join(ch for ch in canonical if ch.isalnum())
    return make(key, canonical)


def anonymize_text(text: str, key: bytes, *, names: list[str] | None = None) -> tuple[str, dict[str, int], list[str]]:
    """Rewrite `text`, returning the result, a count per kind, and the unclassified runs a human should look at."""
    findings = scan(text, names=names)
    counts: dict[str, int] = {}
    unclassified: list[str] = []
    out: list[str] = []
    cursor = 0
    for finding in findings:
        out.append(text[cursor:finding.start])
        if finding.kind == "unclassified":
            unclassified.append(finding.value)
            out.append(finding.value)
        else:
            out.append(replacement(key, finding))
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
        cursor = finding.end
    out.append(text[cursor:])
    return "".join(out), counts, sorted(set(unclassified))


def _merge(into: dict[str, int], more: dict[str, int]) -> None:
    for kind, n in more.items():
        into[kind] = into.get(kind, 0) + n


def anonymize_rows(rows: list[list[str]], key: bytes, *, names: list[str] | None = None):
    """Cell by cell, so a value spanning two columns is never joined into a false identifier."""
    counts: dict[str, int] = {}
    unclassified: list[str] = []
    done: list[list[str]] = []
    for row in rows:
        new_row = []
        for cell in row:
            text, c, u = anonymize_text("" if cell is None else str(cell), key, names=names)
            _merge(counts, c)
            unclassified.extend(u)
            new_row.append(text)
        done.append(new_row)
    return done, counts, sorted(set(unclassified))


def anonymize_file(src: pathlib.Path, dest: pathlib.Path, key: bytes, *, names: list[str] | None = None) -> dict:
    """Anonymise one file into `dest`, returning a report. Raises for a format that cannot be done safely."""
    suffix = src.suffix.lower()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if suffix == ".pdf":
        raise AnonymizeError(
            f"{src.name}: a PDF is not rewritten in place. Extract it first -- `ofinbench extract {src}` writes the "
            "text and tables beside it -- then anonymise the extracted files. A redaction drawn over a PDF leaves "
            "the original text in the file."
        )

    if suffix in {".xlsx", ".xlsm"}:
        import openpyxl

        book = openpyxl.load_workbook(src)
        counts: dict[str, int] = {}
        unclassified: list[str] = []
        for sheet in book.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str):
                        text, c, u = anonymize_text(cell.value, key, names=names)
                        if text != cell.value:
                            cell.value = text
                        _merge(counts, c)
                        unclassified.extend(u)
        book.save(dest)
        return {"file": src.name, "counts": counts, "unclassified": sorted(set(unclassified))}

    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with src.open(newline="", encoding="utf-8-sig") as handle:
            rows = [list(r) for r in csv.reader(handle, delimiter=delimiter)]
        rows, counts, unclassified = anonymize_rows(rows, key, names=names)
        buffer = io.StringIO()
        csv.writer(buffer, delimiter=delimiter, lineterminator="\n").writerows(rows)
        dest.write_text(buffer.getvalue(), encoding="utf-8")
        return {"file": src.name, "counts": counts, "unclassified": unclassified}

    if suffix in TEXT_SUFFIXES or suffix == "":
        raw = src.read_text(encoding="utf-8")
        text, counts, unclassified = anonymize_text(raw, key, names=names)
        if suffix in {".json", ".jsonl"}:
            try:
                json.loads(text) if suffix == ".json" else [json.loads(l) for l in text.splitlines() if l.strip()]
            except json.JSONDecodeError as exc:
                raise AnonymizeError(f"{src.name}: anonymised output is no longer valid JSON ({exc})") from exc
        dest.write_text(text, encoding="utf-8")
        return {"file": src.name, "counts": counts, "unclassified": unclassified}

    raise AnonymizeError(f"{src.name}: unsupported format {suffix or '(no extension)'}. Convert it to CSV or text first.")


def _readable_text(path: pathlib.Path) -> str:
    """Everything a reader could get out of the file, as one string, for the leak test below."""
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        import openpyxl

        book = openpyxl.load_workbook(path, read_only=True)
        return "\n".join(
            str(cell.value) for sheet in book.worksheets for row in sheet.iter_rows() for cell in row
            if cell.value is not None
        )
    return path.read_text(encoding="utf-8", errors="replace")


def _canonical(value: str) -> str:
    folded = fold_digits(value).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def verify_file(output: pathlib.Path, *, original: pathlib.Path, names: list[str] | None = None) -> dict:
    """Prove the output carries nothing from the original, and report what still needs a human eye.

    The test is exact rather than heuristic: every identifier the detector found in the original is reduced to its
    alphanumeric core, and the output fails if any of those cores appears in it. This avoids the trap of trying to
    tell a tool-generated fake from a real value by its shape -- a fake Saudi mobile has the same prefix as a real
    one, so a shape test would either pass leaks or fail on its own output. Comparing against the source cannot be
    fooled either way.

    Supplied names are checked separately, because a name the detector could not shape-match must still be gone.
    Unclassified long digit runs are returned, not failed: they are usually references and invoice numbers, and the
    accountant decides which of them identify a client.
    """
    out_text = _readable_text(output)
    out_core = _canonical(out_text)
    src_text = _readable_text(original)

    leaked: list[dict] = []
    for finding in scan(src_text, names=names):
        if finding.kind == "unclassified":
            continue
        core = _canonical(finding.value)
        if len(core) < 6:
            continue
        if core in out_core:
            leaked.append({"kind": finding.kind, "value": finding.value})

    for name in names or []:
        clean = name.strip()
        if len(clean) >= 3 and _canonical(clean) and _canonical(clean) in out_core:
            leaked.append({"kind": "name-list", "value": clean})

    residual = sorted({f.value for f in scan(out_text) if f.kind == "unclassified"})
    seen: set[tuple[str, str]] = set()
    unique = [x for x in leaked if not ((x["kind"], x["value"]) in seen or seen.add((x["kind"], x["value"])))]
    return {"leaks": unique, "review": residual, "clean": not unique}
