"""The KPI formula language: arithmetic over monthly measures, with functions that look back in time.

    gross_profit / revenue                      this month
    SUMN(gross_profit, 3) / SUMN(revenue, 3)    the last three months together
    receivables / SUMN(revenue, 3) * 91.25      days of sales outstanding
    revenue / PREV(revenue, 12) - 1             growth on the same month last year
    SUMN(net_profit, 12) / AVGBAL(equity)       return on average equity
    cash / IFPOS(SUMN(burn, 3) / 3)             months of cash left, only when burning cash

Names are measures (see ``ledger.MEASURES``) or other KPIs. Functions: SUMN(x, n), AVGN(x, n), PREV(x, n),
AVGBAL(x) = (x + PREV(x, 12)) / 2, IFPOS(x), ABS, MIN, MAX. A value that needs months before the ledger starts, or a
division by zero, gives no value rather than a misleading one. Parsed into a tree and evaluated by walking it; no
``eval``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..errors import Invalid

FUNCS = {"SUMN": 2, "AVGN": 2, "PREV": 2, "AVGBAL": 1, "IFPOS": 1, "ABS": 1, "MIN": -2, "MAX": -2}
TOKEN = re.compile(r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[-+*/(),]))")


@dataclass(frozen=True)
class Num:
    v: float


@dataclass(frozen=True)
class Name:
    n: str


@dataclass(frozen=True)
class Op:
    op: str
    a: Any
    b: Any


@dataclass(frozen=True)
class Fn:
    f: str
    args: tuple[Any, ...]


class _Missing(Exception):
    pass


def parse(text: str) -> Any:
    if not text or len(text) > 400:
        raise Invalid("A formula has 1 to 400 characters.")
    toks: list[tuple[str, str]] = []
    pos = 0
    text = text.replace("−", "-").replace("×", "*").replace("÷", "/")
    while pos < len(text):
        if not text[pos:].strip():
            break
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise Invalid(f"Cannot read the formula near “{text[pos:pos + 12]}”.")
        toks.append((m.lastgroup or "", m.group(m.lastgroup or "")))
        pos = m.end()
    i = 0

    def peek() -> tuple[str, str] | None:
        return toks[i] if i < len(toks) else None

    def take(expect: str | None = None) -> tuple[str, str]:
        nonlocal i
        if i >= len(toks):
            raise Invalid("The formula ends too early.")
        t = toks[i]
        if expect and t[1] != expect:
            raise Invalid(f"Expected “{expect}” in the formula.")
        i += 1
        return t

    def expr() -> Any:
        node = term()
        while (t := peek()) and t[0] == "op" and t[1] in "+-":
            take()
            node = Op(t[1], node, term())
        return node

    def term() -> Any:
        node = unary()
        while (t := peek()) and t[0] == "op" and t[1] in "*/":
            take()
            node = Op(t[1], node, unary())
        return node

    def unary() -> Any:
        t = peek()
        if t and t[0] == "op" and t[1] == "-":
            take()
            return Op("-", Num(0.0), unary())
        return atom()

    def atom() -> Any:
        kind, val = take()
        if kind == "num":
            return Num(float(val))
        if kind == "name":
            nxt = peek()
            if nxt and nxt[1] == "(":
                f = val.upper()
                if f not in FUNCS:
                    raise Invalid(f"Unknown function {val}. Available: {', '.join(FUNCS)}.")
                take("(")
                args = [expr()]
                while (t := peek()) and t[1] == ",":
                    take()
                    args.append(expr())
                take(")")
                want = FUNCS[f]
                if (want > 0 and len(args) != want) or (want < 0 and len(args) < -want):
                    raise Invalid(f"{f} takes {abs(want)}{' or more' if want < 0 else ''} arguments.")
                if f in {"SUMN", "AVGN", "PREV"} and not (isinstance(args[1], Num) and 1 <= args[1].v <= 36
                                                          and args[1].v.is_integer()):
                    raise Invalid(f"The second argument of {f} is a whole number of months from 1 to 36.")
                return Fn(f, tuple(args))
            return Name(val.lower())
        if val == "(":
            node = expr()
            take(")")
            return node
        raise Invalid(f"Unexpected “{val}” in the formula.")

    node = expr()
    if i != len(toks):
        raise Invalid(f"Unexpected “{toks[i][1]}” in the formula.")
    return node


def names(node: Any) -> set[str]:
    if isinstance(node, Name):
        return {node.n}
    if isinstance(node, Op):
        return names(node.a) | names(node.b)
    if isinstance(node, Fn):
        return set().union(*(names(a) for a in node.args))
    return set()


def evaluate(node: Any, months: list[str], index: int, value: Callable[[str, int], float | None]) -> float | None:
    """The formula's value in ``months[index]``. ``value(name, i)`` gives a name's value in ``months[i]``."""

    def go(n: Any, i: int) -> float:
        if i < 0:
            raise _Missing
        if isinstance(n, Num):
            return n.v
        if isinstance(n, Name):
            v = value(n.n, i)
            if v is None:
                raise _Missing
            return v
        if isinstance(n, Op):
            a, b = go(n.a, i), go(n.b, i)
            if n.op == "+":
                return a + b
            if n.op == "-":
                return a - b
            if n.op == "*":
                return a * b
            if abs(b) < 1e-9:
                raise _Missing
            return a / b
        if isinstance(n, Fn):
            f = n.f
            if f in {"SUMN", "AVGN"}:
                k = int(n.args[1].v)
                vals = [go(n.args[0], i - j) for j in range(k)]
                return sum(vals) / (k if f == "AVGN" else 1)
            if f == "PREV":
                return go(n.args[0], i - int(n.args[1].v))
            if f == "AVGBAL":
                return (go(n.args[0], i) + go(n.args[0], i - 12)) / 2
            if f == "IFPOS":
                v = go(n.args[0], i)
                if v <= 0:
                    raise _Missing
                return v
            if f == "ABS":
                return abs(go(n.args[0], i))
            vals = [go(a, i) for a in n.args]
            return min(vals) if f == "MIN" else max(vals)
        raise TypeError(n)

    try:
        return go(node, index)
    except _Missing:
        return None
