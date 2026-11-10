"""From a score to a probability, by measurement.

A scorecard says 950 out of 1000. That number is a sum of weights someone chose; it is honest about *why* a
proposal looks good, and it says nothing about how often proposals that look this good turn out to be right. The
curve here answers the second question from the only source that can: the verdicts people in this workspace have
already given. Among proposals that scored about 950, what share did a person accept? That share is the
probability, and the proposals it was counted over are its source.

The fit is isotonic regression by pool-adjacent-violators: the probability may not go *down* as the score goes
up, which is the one thing a scorecard is entitled to claim about itself, and nothing else is assumed about the
shape. It needs no library, it has no parameters to tune, and the same verdicts always produce the same curve,
which is what makes a recorded probability recomputable later.

What the curve refuses to say:

* anything, below ``MIN_SAMPLES`` verdicts or ``MIN_EACH`` of either kind -- a curve fitted to twelve accepts and
  no rejects is a flat line at one, and a flat line at one is not a measurement;
* a *calibrated* figure outside the range of scores the verdicts actually cover -- the value at the nearest end
  is returned and marked ``extrapolated``, because a score nobody has ruled on is a score the curve knows nothing
  about, and the top of a scorecard's range is usually where a person is asked least and verdicts are rarest.

Everything here is a pure function of its arguments.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any

MIN_SAMPLES = 30          # verdicts before a curve is offered at all
MIN_EACH = 5              # of each outcome: a curve needs to have seen both answers to say anything about either
SCORE_MAX = 1000          # the scorecards' scale; a curve maps [0, SCORE_MAX] to [0, 1]
RELIABILITY_BINS = 10

CALIBRATED = "calibrated"
EXTRAPOLATED = "extrapolated"
UNCALIBRATED = "uncalibrated"


@dataclasses.dataclass(frozen=True)
class Knot:
    """One pooled block of the isotonic fit: the mean score of its verdicts, the share accepted, and how many."""
    score: float
    probability: float
    n: int


@dataclasses.dataclass(frozen=True)
class Curve:
    scorer: str
    knots: tuple[Knot, ...]
    n: int
    accepted: int
    lo: int                   # lowest score among the verdicts: below it the curve is extrapolating
    hi: int                   # highest
    brier: float              # mean squared error of the fitted probabilities against the verdicts (in-sample)
    ece: float                # expected calibration error over RELIABILITY_BINS (in-sample)
    version: str              # a digest of the knots and the sample: the same verdicts give the same version
    fitted_at: float

    # -- reading -------------------------------------------------------------------------------------------------
    def probability(self, score: float) -> tuple[float, str]:
        """The measured probability for a score, and whether the score sits inside the verdicts' range.

        Between knots the value is interpolated on the score, which keeps it monotone because the knots are; at
        and beyond the ends it is the end knot's value, marked extrapolated.
        """
        s = float(score)
        if s < self.lo or s > self.hi:
            return (self.knots[0].probability if s < self.lo else self.knots[-1].probability), EXTRAPOLATED
        if len(self.knots) == 1:
            return self.knots[0].probability, CALIBRATED
        for a, b in zip(self.knots, self.knots[1:]):
            if s <= b.score:
                if s <= a.score or b.score == a.score:
                    return a.probability, CALIBRATED
                t = (s - a.score) / (b.score - a.score)
                return a.probability + t * (b.probability - a.probability), CALIBRATED
        return self.knots[-1].probability, CALIBRATED

    def to_json(self) -> dict[str, Any]:
        return {"scorer": self.scorer, "knots": [dataclasses.asdict(k) for k in self.knots], "n": self.n,
                "accepted": self.accepted, "lo": self.lo, "hi": self.hi, "brier": self.brier, "ece": self.ece,
                "version": self.version, "fitted_at": self.fitted_at}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Curve:
        return cls(scorer=str(data["scorer"]),
                   knots=tuple(Knot(float(k["score"]), float(k["probability"]), int(k["n"])) for k in data["knots"]),
                   n=int(data["n"]), accepted=int(data["accepted"]), lo=int(data["lo"]), hi=int(data["hi"]),
                   brier=float(data["brier"]), ece=float(data["ece"]), version=str(data["version"]),
                   fitted_at=float(data["fitted_at"]))

    def summary(self) -> dict[str, Any]:
        """What a panel shows: the sample, the range it covers, how well the curve fits it, and the curve itself."""
        return {"scorer": self.scorer, "version": self.version, "n": self.n, "accepted": self.accepted,
                "rejected": self.n - self.accepted, "lo": self.lo, "hi": self.hi, "brier": round(self.brier, 4),
                "ece": round(self.ece, 4), "fitted_at": self.fitted_at,
                "knots": [{"score": round(k.score), "probability": round(k.probability, 3), "n": k.n}
                          for k in self.knots]}


def _version(scorer: str, knots: Sequence[Knot], n: int, lo: int, hi: int) -> str:
    body = json.dumps({"scorer": scorer, "knots": [dataclasses.asdict(k) for k in knots], "n": n, "lo": lo,
                       "hi": hi}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def _pav(points: Sequence[tuple[int, int]]) -> list[Knot]:
    """Pool-adjacent-violators on verdicts sorted by score. Equal scores are pooled first, then any block whose
    share is lower than the block before it is merged into it until the sequence does not decrease."""
    by_score: dict[int, list[int]] = {}
    for s, y in points:
        by_score.setdefault(int(s), []).append(1 if y else 0)
    blocks: list[list[float]] = []      # [sum of scores, sum of outcomes, count]
    for s in sorted(by_score):
        ys = by_score[s]
        blocks.append([float(s) * len(ys), float(sum(ys)), float(len(ys))])
        while len(blocks) >= 2 and blocks[-2][1] / blocks[-2][2] > blocks[-1][1] / blocks[-1][2]:
            last = blocks.pop()
            blocks[-1] = [blocks[-1][0] + last[0], blocks[-1][1] + last[1], blocks[-1][2] + last[2]]
    return [Knot(score=b[0] / b[2], probability=b[1] / b[2], n=int(b[2])) for b in blocks]


def fit(points: Iterable[tuple[int, int]], *, scorer: str, fitted_at: float,
        min_samples: int = MIN_SAMPLES, min_each: int = MIN_EACH) -> Curve | None:
    """The curve these verdicts support, or None when they do not support one.

    ``points`` are (score, outcome) pairs, outcome 1 for accepted and 0 for rejected. Scores outside
    [0, SCORE_MAX] are clamped rather than refused: a scorer that overshoots is a scorer's bug, and the verdict
    on its proposal is still a verdict.
    """
    data = [(max(0, min(SCORE_MAX, int(s))), 1 if y else 0) for s, y in points]
    n = len(data)
    accepted = sum(y for _, y in data)
    if n < min_samples or accepted < min_each or n - accepted < min_each:
        return None
    knots = _pav(data)
    lo, hi = min(s for s, _ in data), max(s for s, _ in data)
    draft = Curve(scorer=scorer, knots=tuple(knots), n=n, accepted=accepted, lo=lo, hi=hi, brier=0.0, ece=0.0,
                  version="", fitted_at=fitted_at)
    fitted = [draft.probability(s)[0] for s, _ in data]
    brier = sum((p - y) ** 2 for p, (_, y) in zip(fitted, data)) / n
    ece = _ece(fitted, [y for _, y in data])
    return dataclasses.replace(draft, brier=brier, ece=ece, version=_version(scorer, knots, n, lo, hi))


def _ece(predicted: Sequence[float], observed: Sequence[int], bins: int = RELIABILITY_BINS) -> float:
    """Expected calibration error: for each bin of predicted probability, how far the observed share is from the
    predicted mean, weighted by the bin's size. Zero is a curve that says what it means."""
    total = len(predicted)
    if not total:
        return 0.0
    buckets: dict[int, list[tuple[float, int]]] = {}
    for p, y in zip(predicted, observed):
        buckets.setdefault(min(bins - 1, int(p * bins)), []).append((p, y))
    err = 0.0
    for rows in buckets.values():
        mean_p = sum(p for p, _ in rows) / len(rows)
        share = sum(y for _, y in rows) / len(rows)
        err += abs(mean_p - share) * len(rows) / total
    return err


def reliability(curve: Curve, points: Iterable[tuple[int, int]], bins: int = RELIABILITY_BINS) -> list[dict[str, Any]]:
    """The reliability table for a panel: per band of score, what the curve says and what the verdicts said.

    The curve was fitted on these verdicts, so agreement is expected; the table is there so a person can see
    *where* the verdicts are, and where there are none. In a band that has verdicts, what the curve says is its
    mean over those verdicts' own scores -- the figure the observed share is fairly compared with. Read at the
    middle of the band instead, ten verdicts that all scored 600 are set against the curve's value at 650, and a
    curve that fits them exactly looks a tenth out. A band with no verdicts has nothing to average over; the
    curve is read at its middle and nothing is shown as observed.
    """
    width = SCORE_MAX / bins
    rows: list[dict[str, Any]] = []
    data = [(max(0, min(SCORE_MAX, int(s))), 1 if y else 0) for s, y in points]
    for i in range(bins):
        lo, hi = int(i * width), int((i + 1) * width) - (0 if i == bins - 1 else 1)
        here = [(s, y) for s, y in data if lo <= s <= hi]
        predicted = (sum(curve.probability(s)[0] for s, _ in here) / len(here) if here
                     else curve.probability((lo + hi) / 2)[0])
        rows.append({"from": lo, "to": hi, "n": len(here), "predicted": round(predicted, 3),
                     "observed": round(sum(y for _, y in here) / len(here), 3) if here else None})
    return rows
