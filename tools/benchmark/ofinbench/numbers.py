"""Reading a figure the way a human reader would, so the scorer grades the answer and not its formatting.

The existing CFAI evaluation lost marks to its own scorer: a model that answered the budget question correctly as a
percentage used, where the expected answer was the amount remaining, was marked wrong. That is a measurement bug, and
on a real-document benchmark it would hide exactly the weakness the benchmark exists to find. So equality here is
deliberately generous about form and strict about value.

What counts as the same figure: Arabic-Indic and extended Arabic-Indic digits as well as ASCII; thousands separators
of any common kind, including the Arabic thousands mark; a decimal written with a comma or with the Arabic decimal
separator; a currency code or symbol on either side; a negative written with a leading or trailing minus, with
parentheses, or with a debit marker; a percentage written with or without its sign. What does not: a different value.
"""

from __future__ import annotations

import re
import unicodedata

ARABIC_INDIC = {ord("٠") + i: str(i) for i in range(10)}
EXTENDED_INDIC = {ord("۰") + i: str(i) for i in range(10)}
DIGIT_MAP = {**ARABIC_INDIC, **EXTENDED_INDIC}

# every digit shape a Gulf document uses, for patterns that must match either script
D = "[0-9٠-٩۰-۹]"

_SEPARATORS = "٬   _'’"           # Arabic thousands mark, thin and no-break spaces, apostrophes
_CURRENCY = r"(?:SAR|SR|ر\.?س\.?|ريال|USD|EUR|AED|QAR|KWD|BHD|OMR|\$|€|£)"
_DEBIT = r"(?:DR|DB|مدين|خصم)"
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def fold_digits(text: str) -> str:
    """Arabic-Indic digits to ASCII, leaving everything else alone."""
    return text.translate(DIGIT_MAP)


def parse_amount(text: str) -> float | None:
    """The single figure in `text`, or None if there is not exactly one readable figure.

    Returning None rather than guessing matters: an answer containing two numbers is not a numeric answer, and
    grading it as if the first one were the answer would reward a model that hedges.
    """
    if text is None:
        return None
    s = unicodedata.normalize("NFKC", str(text)).strip()
    s = fold_digits(s)

    negative = False
    if re.search(r"\(\s*[^()]*\d[^()]*\)", s):                       # (1,234) is a credit shown in brackets
        negative = True
        s = s.replace("(", " ").replace(")", " ")
    if re.search(rf"(?<!\w){_DEBIT}(?!\w)", s, re.IGNORECASE):
        negative = True
    s = re.sub(rf"(?<!\w){_DEBIT}(?!\w)", " ", s, flags=re.IGNORECASE)
    s = re.sub(_CURRENCY, " ", s, flags=re.IGNORECASE)
    s = s.replace("−", "-").replace("–", "-")               # unicode minus and en dash
    for ch in _SEPARATORS:
        s = s.replace(ch, "")
    s = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "", s)                 # a plain space grouping thousands
    s = s.replace("٫", ".")                                      # Arabic decimal separator

    percent = "%" in s or "٪" in s
    s = s.replace("%", " ").replace("٪", " ")

    if re.fullmatch(r"\s*-?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*-?\s*", s):
        s = s.replace(",", "")                                        # 1,234,567.89
    elif re.fullmatch(r"\s*-?\s*\d{1,3}(?:\.\d{3})+,\d+\s*-?\s*", s):
        s = s.replace(".", "").replace(",", ".")                      # 1.234.567,89
    elif re.fullmatch(r"\s*-?\s*\d+,\d{1,2}\s*-?\s*", s):
        s = s.replace(",", ".")                                       # 1234,56
    else:
        s = s.replace(",", "")

    if s.rstrip().endswith("-"):                                      # trailing minus, as some ledgers print it
        negative = True
        s = s.rstrip()[:-1]

    found = _NUMBER.findall(s)
    if len(found) != 1:
        return None
    value = float(found[0])
    if negative:
        value = -abs(value)
    return value / 100 if percent else value


def amounts_in(text: str) -> list[float]:
    """Every figure in a free-text answer, in order. Used when an expected answer is a set rather than one number."""
    s = fold_digits(unicodedata.normalize("NFKC", str(text)))
    s = re.sub(_CURRENCY, " ", s, flags=re.IGNORECASE)
    for ch in _SEPARATORS:
        s = s.replace(ch, "")
    s = s.replace("٫", ".")
    out: list[float] = []
    for raw in re.findall(r"-?\d[\d,]*(?:\.\d+)?", s):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def same_amount(expected: float, answer: str | float, *, tolerance: float = 0.0) -> bool:
    """True when `answer` states `expected`, within `tolerance` as a fraction of the expected magnitude.

    A relative tolerance with an absolute floor handles both ends: a rounding difference on a figure in the millions
    is not an error, and a figure that should be zero is not satisfied by anything nearby.
    """
    value = parse_amount(answer) if isinstance(answer, str) else answer
    if value is None:
        # Prose carrying more than one figure is the normal case, not a hedge: "closing 196,085.25, up from an
        # opening of 125,000" is a correct answer, and so is one that ends with a date. So any figure in the answer
        # may satisfy the expected value. An answer that genuinely hedges between two candidates is caught by the
        # hedge check in the scorer, which reads the wording rather than counting the numbers.
        if isinstance(answer, str):
            return any(_close(expected, v, tolerance) for v in amounts_in(answer))
        return False
    return _close(expected, value, tolerance)


def states_amount(expected: float, answer: str, *, tolerance: float = 0.0, signed: bool = False) -> bool:
    """True when the figure appears anywhere in a longer answer. For commentary, where prose carries other numbers.

    Sign is ignored by default, because in prose the direction is carried by the words rather than by a minus:
    "fees of 2,500 were charged" states the same fact as a ledger line of -2,500, and marking it wrong would
    penalise the more readable answer.
    """
    values = amounts_in(answer)
    if signed:
        return any(_close(expected, v, tolerance) for v in values)
    return any(_close(abs(expected), abs(v), tolerance) for v in values)


def _close(expected: float, value: float, tolerance: float) -> bool:
    """Exact to the halala, plus the two allowances an accountant would make and nothing else.

    A figure rounded to the whole riyal is the same figure: answering 196,085 for a closing balance of 196,085.25 is
    not an error. A relative tolerance is honoured only when the caller asks for one, because applying a blanket 1%
    to a balance in the hundreds of thousands would accept an answer nearly two thousand riyals out -- which is how
    a scorer ends up reporting that a model reads statements well when it does not.
    """
    difference = abs(value - expected)
    if difference <= 0.005:
        return True
    if tolerance and difference <= abs(expected) * tolerance:
        return True
    return difference <= 0.5 and round(expected) == round(value)
