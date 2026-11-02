"""One entry point, so the order of operations is visible in the commands themselves.

    ofinbench init                      make the corpus root and mint the de-identification key
    ofinbench new bank-statement bs-001 scaffold a case folder with a truth template to fill in
    ofinbench extract <file.pdf>        pull the text out of a PDF so it can be anonymised
    ofinbench deid --case bs-001 --in <dir>   de-identify into the case, then prove nothing leaked
    ofinbench verify --case bs-001 --in <dir> re-run that proof on its own
    ofinbench validate                  check every case's arithmetic before it is used to grade anything
    ofinbench items                     build the question set
    ofinbench run --client mod:fn --label base
    ofinbench compare base candidate    baseline against candidate, per category
    ofinbench status                    what the corpus covers, against what a real benchmark needs

The order is not a suggestion. `validate` before `items` because a truth file that does not foot marks correct
answers wrong, and `deid` before anything because the alternative is client data in a benchmark.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import anonymize, extract, paths, pseudonym, score
from . import items as items_mod
from . import truth as truth_mod

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "templates"
# what an honest benchmark needs, from the shape of the question set rather than from a rule of thumb: enough
# documents that one odd layout cannot move the score, spread over enough banks and formats to be representative.
TARGETS = {"bank-statement": 30, "monthly-close": 6}


def _root(args, *, create: bool = False) -> pathlib.Path:
    return paths.data_root(getattr(args, "root", None), create=create)


def cmd_init(args) -> int:
    root = _root(args, create=True)
    pseudonym.load_key(root, create=True)
    print(f"corpus root   {root}")
    print(f"key           {root / 'keys' / pseudonym.KEY_FILENAME}  (0600, never copy it beside the output)")
    print(f"cases         {root / 'cases'}")
    print("\nThis root is outside any git checkout, which is the only reason client documents may sit in it.")
    return 0


def cmd_new(args) -> int:
    root = _root(args)
    case = paths.case_dir(root, args.kind, args.case_id)
    if case.exists() and not args.force:
        print(f"{case} already exists; pass --force to overwrite the truth template", file=sys.stderr)
        return 1
    (case / "source").mkdir(parents=True, exist_ok=True)
    template = json.loads((TEMPLATES / f"{args.kind}.truth.json").read_text(encoding="utf-8"))
    template["case_id"] = args.case_id
    (case / "truth.json").write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    if not (case / "names.txt").exists():
        (case / "names.txt").write_text(
            "# One name per line: account holders, beneficiaries, suppliers, customers, staff.\n"
            "# The shaped identifiers (IBAN, VAT, ID, phone, email) are found automatically; names are not.\n"
            "# Anything left off this list stays in the output, and `deid` cannot warn you about it.\n",
            encoding="utf-8")
    print(f"case          {case}")
    print(f"truth         {case / 'truth.json'}   (example figures: replace every one)")
    print(f"names         {case / 'names.txt'}    (fill this in before running deid)")
    return 0


def cmd_extract(args) -> int:
    src = pathlib.Path(args.file).expanduser().resolve()
    out = pathlib.Path(args.out).expanduser() if args.out else src.parent / f"{src.stem}.extracted"
    report = extract.extract_pdf(src, out)
    print(f"{report['pages']} pages, {report['characters']} characters -> {report['combined']}")
    if report["warning"]:
        print(f"\nwarning: {report['warning']}", file=sys.stderr)
    return 0


def _names(case: pathlib.Path) -> list[str]:
    path = case / "names.txt"
    if not path.is_file():
        return []
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def cmd_deid(args) -> int:
    root = _root(args)
    case = paths.case_dir(root, args.kind, args.case_id)
    if not case.is_dir():
        print(f"no such case {case}; run `ofinbench new {args.kind} {args.case_id}` first", file=sys.stderr)
        return 1
    key = pseudonym.load_key(root, create=False)
    names = _names(case)
    if not names:
        print(f"warning: {case / 'names.txt'} is empty. Shaped identifiers will be replaced but every name in the\n"
              "         document will survive. Fill it in unless you are certain the document carries no names.",
              file=sys.stderr)

    source_dir = pathlib.Path(args.input).expanduser().resolve()
    if not source_dir.is_dir():
        print(f"--in must be a directory holding the original files; {source_dir} is not", file=sys.stderr)
        return 1
    dest_dir = case / "source"
    dest_dir.mkdir(parents=True, exist_ok=True)

    reports, failures = [], []
    for src in sorted(p for p in source_dir.rglob("*") if p.is_file() and not p.name.startswith(".")):
        dest = dest_dir / src.relative_to(source_dir)
        try:
            report = anonymize.anonymize_file(src, dest, key, names=names)
        except anonymize.AnonymizeError as exc:
            failures.append(str(exc))
            continue
        check = anonymize.verify_file(dest, original=src, names=names)
        report["verified"] = check["clean"]
        report["leaks"] = check["leaks"]
        report["review"] = check["review"]
        reports.append(report)
        if not check["clean"]:
            dest.unlink(missing_ok=True)   # never leave a file that failed its own leak test
            failures.append(f"{src.name}: anonymised output still carried {len(check['leaks'])} value(s) from the "
                            f"original, so it was deleted. Check names.txt and re-run.")

    (case / "deid-report.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    for report in reports:
        marks = ", ".join(f"{k} {v}" for k, v in sorted(report["counts"].items()) if k != "unclassified")
        print(f"{'ok  ' if report['verified'] else 'FAIL'} {report['file']:40} {marks or 'nothing found'}")
        for value in report["review"]:
            print(f"     review: long digit run left in place: {value}")
    for message in failures:
        print(f"\n{message}", file=sys.stderr)
    if failures:
        return 1
    print(f"\n{len(reports)} file(s) de-identified into {dest_dir} and each proved clean against its original.")
    print("Record the client's consent (or the basis for not needing it) in truth.json meta.consent before use.")
    return 0


def cmd_verify(args) -> int:
    root = _root(args)
    case = paths.case_dir(root, args.kind, args.case_id)
    names = _names(case)
    original_dir = pathlib.Path(args.input).expanduser().resolve()
    bad = 0
    for dest in sorted(p for p in (case / "source").rglob("*") if p.is_file()):
        src = original_dir / dest.relative_to(case / "source")
        if not src.is_file():
            print(f"skip {dest.name}: no original at {src}", file=sys.stderr)
            continue
        check = anonymize.verify_file(dest, original=src, names=names)
        print(f"{'ok  ' if check['clean'] else 'LEAK'} {dest.name}")
        for leak in check["leaks"]:
            print(f"     {leak['kind']}: {leak['value']}")
        bad += 0 if check["clean"] else 1
    return 1 if bad else 0


def cmd_validate(args) -> int:
    root = _root(args)
    found = paths.iter_cases(root, args.kind)
    if not found:
        print("no cases with a truth.json yet", file=sys.stderr)
        return 1
    bad = 0
    for kind, case in found:
        data = truth_mod.load(case / "truth.json")
        problems = truth_mod.validate(kind, data)
        consent = (data.get("meta") or {}).get("consent")
        if not consent or consent == "UNRECORDED":
            problems.append("meta.consent is not recorded: write the client's consent, or the basis for not needing it")
        if not (data.get("meta") or {}).get("deidentified"):
            problems.append("meta.deidentified is not true: set it only after `deid` has passed on this case")
        print(f"{'ok  ' if not problems else 'FAIL'} {kind}/{case.name}")
        for problem in problems:
            print(f"     {problem}")
        bad += 1 if problems else 0
    print(f"\n{len(found) - bad}/{len(found)} case(s) usable")
    return 1 if bad else 0


def cmd_items(args) -> int:
    root = _root(args)
    built: list[dict] = []
    for kind, case in paths.iter_cases(root, args.kind):
        data = truth_mod.load(case / "truth.json")
        if truth_mod.validate(kind, data):
            print(f"skip {case.name}: fails validation; run `ofinbench validate`", file=sys.stderr)
            continue
        built.extend(items_mod.build(kind, data))
    out = root / "items" / "items.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(built, ensure_ascii=False, indent=2), encoding="utf-8")
    from collections import Counter
    counts = Counter(i["category"] for i in built)
    print(f"{len(built)} items -> {out}")
    for category, n in sorted(counts.items()):
        print(f"  {category:12} {n}")
    return 0 if built else 1


def _documents(root: pathlib.Path) -> dict[str, str]:
    """Each case's de-identified text, as the model will be given it."""
    out: dict[str, str] = {}
    for _, case in paths.iter_cases(root):
        parts = []
        for path in sorted((case / "source").rglob("*")):
            if path.is_file() and path.suffix.lower() in anonymize.TEXT_SUFFIXES:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        out[truth_mod.load(case / "truth.json")["case_id"]] = "\n\n".join(parts)
    return out


def cmd_run(args) -> int:
    root = _root(args)
    item_file = root / "items" / "items.json"
    if not item_file.is_file():
        print("no items yet; run `ofinbench items`", file=sys.stderr)
        return 1
    built = json.loads(item_file.read_text(encoding="utf-8"))
    client = score.load_client(args.client)
    documents = _documents(root)
    missing = sorted({i["case"] for i in built} - {c for c, text in documents.items() if text.strip()})
    if missing:
        print(f"warning: no readable source text for {', '.join(missing)}; those answers will be ungrounded",
              file=sys.stderr)
    results = score.run(built, documents, client)
    summary = score.summarize(results)
    score.write_run(root / "runs" / f"{args.label}.json", args.label, results, summary)

    overall = summary["overall"]
    print(f"\n{args.label}: {overall['score']}  ({overall['passed']} passed, {overall['failed']} failed, "
          f"{overall['review']} to review, of {overall['n']})")
    for title, bucket in (("by category", "by_category"), ("by language", "by_language")):
        print(f"\n{title}")
        for key, row in summary[bucket].items():
            print(f"  {key:12} {row['score']!s:>6}   n={row['n']:<4} review={row['review']}")
    if overall["review"]:
        print(f"\n{overall['review']} answer(s) need a human read; they are not counted in the score.")
        for result in results:
            if result.outcome == score.REVIEW:
                print(f"  {result.item}: {result.note}")
    print(f"\nrun written to {root / 'runs' / (args.label + '.json')}")
    return 0


def cmd_compare(args) -> int:
    root = _root(args)
    try:
        baseline = json.loads((root / "runs" / f"{args.baseline}.json").read_text(encoding="utf-8"))["summary"]
        candidate = json.loads((root / "runs" / f"{args.candidate}.json").read_text(encoding="utf-8"))["summary"]
    except FileNotFoundError as exc:
        print(f"{exc!s}", file=sys.stderr)
        return 1
    print(f"{'':24} {args.baseline:>10} {args.candidate:>10} {'delta':>8}")
    for name, a, b, delta in score.compare(baseline, candidate):
        mark = "" if delta is None or delta >= 0 else "   <- worse than the baseline"
        print(f"{name:24} {a!s:>10} {b!s:>10} {delta!s:>8}{mark}")
    return 0


def cmd_status(args) -> int:
    root = _root(args)
    print(f"corpus root {root}\n")
    for kind in paths.KINDS:
        cases = paths.iter_cases(root, kind)
        spread: dict[str, set[str]] = {"bank": set(), "language": set(), "source_format": set(), "quality": set()}
        usable = 0
        for _, case in cases:
            data = truth_mod.load(case / "truth.json")
            meta = data.get("meta") or {}
            for field in list(spread):
                value = meta.get(field)
                if field == "bank" and not value:
                    value = meta.get("entity")          # a close is labelled by entity, a statement by bank
                if value:
                    spread[field].add(str(value))
            if not truth_mod.validate(kind, data) and meta.get("deidentified"):
                usable += 1
        target = TARGETS[kind]
        bar = "#" * min(40, round(40 * usable / target)) if target else ""
        print(f"{kind}: {usable} usable of {len(cases)} present, target {target}  {bar}")
        for field, values in sorted(spread.items()):
            print(f"    {field:14} {len(values):>2} distinct  {', '.join(sorted(values)) if values else '-'}")
        print()
    print("Spread matters more than the count: thirty statements from one bank in one format measure one layout.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ofinbench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", help=f"corpus root (default: ${paths.ENV_VAR}); must be outside any git repository")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the corpus root and mint the key").set_defaults(func=cmd_init)

    p = sub.add_parser("new", help="scaffold a case folder")
    p.add_argument("kind", choices=paths.KINDS)
    p.add_argument("case_id")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("extract", help="pull the text out of a PDF")
    p.add_argument("file")
    p.add_argument("--out")
    p.set_defaults(func=cmd_extract)

    for name, func, help_text in (("deid", cmd_deid, "de-identify originals into a case and prove it"),
                                  ("verify", cmd_verify, "re-prove a case against its originals")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--kind", choices=paths.KINDS, required=True)
        p.add_argument("--case-id", required=True)
        p.add_argument("--in", dest="input", required=True, help="directory holding the ORIGINAL files, outside the corpus")
        p.set_defaults(func=func)

    for name, func, help_text in (("validate", cmd_validate, "check every case's arithmetic"),
                                  ("items", cmd_items, "build the question set")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--kind", choices=paths.KINDS)
        p.set_defaults(func=func)

    p = sub.add_parser("run", help="score a model against the question set")
    p.add_argument("--client", required=True, help="module:function taking (question, document) and returning text")
    p.add_argument("--label", required=True, help="a name for this run, e.g. base or cfai-full")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("compare", help="baseline against candidate")
    p.add_argument("baseline")
    p.add_argument("candidate")
    p.set_defaults(func=cmd_compare)

    sub.add_parser("status", help="what the corpus covers").set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (paths.RootError, pseudonym.KeyError_, truth_mod.TruthError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
