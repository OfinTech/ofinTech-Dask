"""The figure check exactly as audited (commit bd1e952, engine/desk_engine/agent/harness.py lines 36-83), kept
here only to measure the old behaviour on the same answers as the new one."""
import re

# --- figure check -----------------------------------------------------------------------------

_NUM_RE = re.compile(r"(?<![\w.])[-−(]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:%|k|m|bn|million|billion)?(?![\w])",
                     re.I)


def _numbers(text: str) -> list[tuple[str, float]]:
    out = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(0).strip()
        s = raw.lower().replace("−", "-").replace("(", "-").replace(",", "")
        mult = 1.0
        for suffix, factor in (("billion", 1e9), ("million", 1e6), ("bn", 1e9), ("%", 0.01), ("k", 1e3), ("m", 1e6)):
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                mult = factor
                break
        try:
            out.append((raw, float(s) * mult))
        except ValueError:
            continue
    return out


def unverified_figures(answer: str, evidence: list[str]) -> list[str]:
    known: list[float] = []
    for text in evidence:
        for _, v in _numbers(text):
            known.append(v)
    flagged = []
    for raw, v in _numbers(answer):
        a = abs(v)
        if a < 100 and "." not in raw and "%" not in raw:
            continue  # small counts, list numbers, days
        if 1900 <= a <= 2100 and "." not in raw and "," not in raw:
            continue  # years
        ok = False
        for k in known:
            for cand in (k, k * 100, k / 100):
                tol = max(abs(cand) * 0.005, 0.011)
                if abs(abs(cand) - a) <= tol:
                    ok = True
                    break
            if ok:
                break
        if not ok and raw not in flagged:
            flagged.append(raw)
    return flagged[:20]
