"""A small, safe formula language for computed columns, totals, KPIs and figures quoted in reports.

    variance            = actual - budget
    variance_pct        = (actual - budget) / budget
    gross margin        = [pl:Gross profit:amount] / [pl:Revenue:amount]
    same, dotted form   = pl.total.amount

Names are column keys of the same row. ``[table:row:column]`` (or ``table.row.column`` when the row is a single
word such as ``total``) points at one cell of any table; the row is ``total`` or the text in the table's first
column. Functions: ABS, ROUND, MIN, MAX, SUM, AVERAGE. Operators: + - * / and parentheses.

Each expression is parsed once and then either compiled to an Excel formula (cell references are supplied by the
caller) or evaluated in Python with the same blank-as-zero and divide-by-zero rules Excel applies, so the values
shown in the app equal what Excel calculates. No Python ``eval`` is involved.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..errors import Invalid

FUNCS = {"ABS": (1, 1), "ROUND": (1, 2), "MIN": (1, 30), "MAX": (1, 30), "SUM": (1, 60), "AVERAGE": (1, 60)}
TOKEN = re.compile(r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?) |
    (?P<ref>\[[^\[\]]{1,200}\]) |
    (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+){0,2}) |
    (?P<op>[-+*/(),])
)""", re.X)


@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class Col:
    key: str


@dataclass(frozen=True)
class Ref:
    table: str
    row: str
    col: str


@dataclass(frozen=True)
class Neg:
    arg: Any


@dataclass(frozen=True)
class Bin:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[Any, ...]


@dataclass(frozen=True)
class Span:
    """SUM over a run of cells in the same row (row totals of pivoted tables)."""
    keys: tuple[str, ...]


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    text = text.replace("−", "-").replace("×", "*").replace("÷", "/")
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise Invalid(f"Cannot read the formula near “{text[pos:pos + 12]}”.")
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


MAX_DEPTH = 32  # brackets nested deeper than this are refused before parsing, so the parser's recursion is bounded


def check_depth(text: str, limit: int = MAX_DEPTH) -> None:
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            if depth > limit:
                raise Invalid(f"A formula may not nest brackets more than {limit} deep.")
        elif ch == ")":
            depth -= 1


def parse(text: str) -> Any:
    if not text or len(text) > 600:
        raise Invalid("A formula must be between 1 and 600 characters.")
    check_depth(text)
    toks = _tokens(text)
    i = 0

    def peek() -> tuple[str, str] | None:
        return toks[i] if i < len(toks) else None

    def take(expected: str | None = None) -> tuple[str, str]:
        nonlocal i
        if i >= len(toks):
            raise Invalid(f"The formula “{text}” ends too early.")
        tok = toks[i]
        if expected is not None and tok[1] != expected:
            raise Invalid(f"Expected “{expected}” in “{text}”.")
        i += 1
        return tok

    def expr() -> Any:
        node = term()
        while (t := peek()) and t[1] in "+-" and t[0] == "op":
            take()
            node = Bin(t[1], node, term())
        return node

    def term() -> Any:
        node = factor()
        while (t := peek()) and t[1] in "*/" and t[0] == "op":
            take()
            node = Bin(t[1], node, factor())
        return node

    def factor() -> Any:
        t = peek()
        if t and t[0] == "op" and t[1] in "+-":
            take()
            inner = factor()
            return Neg(inner) if t[1] == "-" else inner
        return atom()

    def atom() -> Any:
        kind, val = take()
        if kind == "num":
            return Num(float(val))
        if kind == "ref":
            parts = [p.strip() for p in val[1:-1].split(":")]
            if len(parts) != 3 or not all(parts):
                raise Invalid(f"Write cell references as [table:row:column], e.g. [pl:Revenue:amount]; got {val}.")
            return Ref(parts[0], parts[1], parts[2])
        if kind == "name":
            nxt = peek()
            if nxt and nxt[1] == "(":
                fn = val.upper()
                if fn not in FUNCS:
                    raise Invalid(f"Unknown function {val}. Available: {', '.join(FUNCS)}.")
                take("(")
                args = [expr()]
                while (t := peek()) and t[1] == ",":
                    take()
                    args.append(expr())
                take(")")
                lo, hi = FUNCS[fn]
                if not lo <= len(args) <= hi:
                    raise Invalid(f"{fn} takes {lo}–{hi} arguments.")
                return Call(fn, tuple(args))
            if "." in val:
                parts = val.split(".")
                if len(parts) != 3:
                    raise Invalid(f"Write cell references as table.row.column or [table:row:column]; got {val}.")
                return Ref(*parts)
            return Col(val.lower())
        if val == "(":
            node = expr()
            take(")")
            return node
        raise Invalid(f"Unexpected “{val}” in “{text}”.")

    node = expr()
    if i != len(toks):
        raise Invalid(f"Unexpected “{toks[i][1]}” in “{text}”.")
    return node


def walk(node: Any) -> list[Any]:
    out = [node]
    if isinstance(node, Neg):
        out += walk(node.arg)
    elif isinstance(node, Bin):
        out += walk(node.left) + walk(node.right)
    elif isinstance(node, Call):
        for a in node.args:
            out += walk(a)
    return out


def has_division(node: Any) -> bool:
    return any(isinstance(n, Bin) and n.op == "/" for n in walk(node))


# --- Excel -------------------------------------------------------------------------------------------------

def to_excel(node: Any, address: Callable[[Any], str]) -> str:
    """``address`` maps Col/Ref/Span nodes to A1 references (Span → a range)."""

    def go(n: Any, parent_prec: int = 0) -> str:
        if isinstance(n, Num):
            v = n.value
            return str(int(v)) if v == int(v) and abs(v) < 1e15 else repr(v)
        if isinstance(n, (Col, Ref)):
            return address(n)
        if isinstance(n, Span):
            return f"SUM({address(n)})"
        if isinstance(n, Neg):
            return f"-{go(n.arg, 3)}"
        if isinstance(n, Call):
            return f"{n.name}({','.join(go(a) for a in n.args)})"
        if isinstance(n, Bin):
            prec = 1 if n.op in "+-" else 2
            right_prec = prec + 1  # left-associative: a-(b-c) keeps its brackets
            s = f"{go(n.left, prec)}{n.op}{go(n.right, right_prec)}"
            return f"({s})" if prec < parent_prec else s
        raise TypeError(n)

    body = go(node)
    return f'=IFERROR({body},"")' if has_division(node) else f"={body}"


def to_text(node: Any, label: Callable[[Any], str]) -> str:
    """Readable form for notes and the reviewer: ``Actual − Budget``."""

    def go(n: Any, parent_prec: int = 0) -> str:
        if isinstance(n, Num):
            return f"{n.value:g}"
        if isinstance(n, (Col, Ref, Span)):
            return label(n)
        if isinstance(n, Neg):
            return f"−{go(n.arg, 3)}"
        if isinstance(n, Call):
            return f"{n.name}({', '.join(go(a) for a in n.args)})"
        if isinstance(n, Bin):
            prec = 1 if n.op in "+-" else 2
            op = {"-": " − ", "+": " + ", "*": " × ", "/": " ÷ "}[n.op]
            s = f"{go(n.left, prec)}{op}{go(n.right, prec + 1)}"
            return f"({s})" if prec < parent_prec else s
        raise TypeError(n)

    return go(node)


# --- evaluation (Excel semantics) --------------------------------------------------------------------------

class _Err(Exception):
    pass


def _num(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    raise _Err("text in arithmetic")


def evaluate(node: Any, value: Callable[[Any], Any]) -> float | None:
    """Returns the number Excel would show, or None where Excel shows an error (wrapped in IFERROR → blank)."""

    def go(n: Any) -> float:
        if isinstance(n, Num):
            return n.value
        if isinstance(n, (Col, Ref)):
            v = value(n)
            if v is _ERROR:
                raise _Err("referenced error")
            return _num(v)
        if isinstance(n, Span):
            vals = value(n)
            return sum(_num(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool))
        if isinstance(n, Neg):
            return -go(n.arg)
        if isinstance(n, Bin):
            a, b = go(n.left), go(n.right)
            if n.op == "+":
                return a + b
            if n.op == "-":
                return a - b
            if n.op == "*":
                return a * b
            if b == 0:
                raise _Err("#DIV/0!")
            return a / b
        if isinstance(n, Call):
            if n.name in {"SUM", "AVERAGE", "MIN", "MAX"}:
                vals = [go(a) for a in n.args]
                if n.name == "SUM":
                    return sum(vals)
                if n.name == "AVERAGE":
                    return sum(vals) / len(vals)
                return min(vals) if n.name == "MIN" else max(vals)
            if n.name == "ABS":
                return abs(go(n.args[0]))
            if n.name == "ROUND":
                digits = int(go(n.args[1])) if len(n.args) > 1 else 0
                if not -30 <= digits <= 30:
                    raise _Err("#NUM!")  # Excel shows an error for such a digit count; the cell stays blank here
                return excel_round(go(n.args[0]), digits)
        raise TypeError(n)

    try:
        result = go(node)
    except _Err:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


_ERROR = object()


def excel_round(x: float, digits: int) -> float:
    """Excel rounds half away from zero (Python rounds half to even), after reducing the binary value to 15
    significant digits, so ROUND(7250.5 * 1.15, 2) is 8338.08 as in Excel, not 8338.07."""
    from decimal import ROUND_HALF_UP, Decimal

    q = Decimal(1).scaleb(-digits)
    return float(Decimal(f"{x:.15g}").quantize(q, rounding=ROUND_HALF_UP))


def references(node: Any) -> list[Any]:
    return [n for n in walk(node) if isinstance(n, (Col, Ref, Span))]
