"""Five-field cron expressions (minute hour day-of-month month day-of-week) evaluated in an IANA
time zone. Supports ``*``, lists, ranges, steps and month/day names. Day-of-month and day-of-week
follow the usual rule: when both are restricted, either may match."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..errors import Invalid

_MONTHS = {n: i for i, n in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], start=1)}
_DAYS = {n: i for i, n in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def _value(token: str, index: int) -> int:
    t = token.lower()
    if index == 3 and t in _MONTHS:
        return _MONTHS[t]
    if index == 4 and t in _DAYS:
        return _DAYS[t]
    if not t.isdigit():
        raise Invalid(f"'{token}' is not valid in a cron expression.", code="bad_cron")
    return int(t)


def _field(spec: str, index: int) -> set[int]:
    lo, hi = _RANGES[index]
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) == 0:
                raise Invalid("Cron step must be a positive number.", code="bad_cron")
            step = int(step_s)
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _value(a, index), _value(b, index)
        else:
            start = _value(part, index)
            end = hi if step > 1 else start
        if not (lo <= start <= hi and lo <= end <= hi) or start > end:
            raise Invalid(f"Cron value out of range in '{spec}'.", code="bad_cron")
        out.update(range(start, end + 1, step))
    if index == 4 and 7 in out:
        out.discard(7)
        out.add(0)
    return out


class Cron:
    def __init__(self, expr: str) -> None:
        parts = expr.split()
        if len(parts) != 5:
            raise Invalid("A schedule needs five fields: minute hour day month weekday (e.g. '0 8 * * MON').",
                          code="bad_cron")
        self.expr = expr
        self.minutes, self.hours, self.days, self.months, self.weekdays = (_field(p, i) for i, p in enumerate(parts))
        self.dom_any = parts[2] == "*"
        self.dow_any = parts[4] == "*"

    def _day_ok(self, dt: datetime) -> bool:
        dom = dt.day in self.days
        dow = ((dt.weekday() + 1) % 7) in self.weekdays
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow
        if self.dow_any:
            return dom
        return dom or dow

    def next_after(self, after: datetime, tz: str = "UTC") -> datetime:
        zone = ZoneInfo(tz)
        t = after.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60 // 15):  # bounded search
            if t.month not in self.months:
                t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
                continue
            if not self._day_ok(t):
                t = t.replace(hour=0, minute=0) + timedelta(days=1)
                continue
            if t.hour not in self.hours:
                t = t.replace(minute=0) + timedelta(hours=1)
                continue
            if t.minute not in self.minutes:
                t += timedelta(minutes=1)
                continue
            return t
        raise Invalid("This schedule never runs.", code="bad_cron")


def describe(expr: str) -> str:
    presets = {"0 8 * * *": "Every day at 08:00", "0 8 * * 1": "Every Monday at 08:00",
               "0 8 * * MON": "Every Monday at 08:00", "0 8 1 * *": "On the 1st of every month at 08:00",
               "0 * * * *": "Every hour"}
    return presets.get(expr, f"Cron: {expr}")
