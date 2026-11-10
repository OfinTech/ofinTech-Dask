# A probability that has a source

*Engine `desk_engine/decide/` (`calibration.py`); tests `engine/tests/test_calibration.py`.*

A scorecard gives a proposal a number — 950 out of 1000. That number is a sum of weights somebody chose. It is
honest about *why* a proposal looks good, and it says nothing about how often proposals that look this good turn
out to be right. The second question has one honest source: what people decided. Among the proposals that scored
about 950, what share did a person accept? That share is the probability, and the proposals it was counted over
are its source.

This package computes that and nothing else. It is a measurement, not a model: isotonic regression in plain
Python, with no library, no parameter to tune and no random start.

## What it promises, and where each promise is kept

| Promise | Where |
|---|---|
| No probability without a sample behind it. Below 30 verdicts, or with fewer than 5 of either outcome, there is no curve at all. | `fit` returns `None`; `MIN_SAMPLES`, `MIN_EACH`. Forty acceptances and no rejection would fit a flat line at one, and a flat line at one is not a measurement. |
| A figure outside what was measured says so. | `Curve.probability(score)` returns the figure **and** a status: `calibrated` inside the range of scores the verdicts cover, `extrapolated` outside it, where the value of the nearest end is given and never anything beyond it. |
| Scoring higher never makes a proposal less likely. | Pool-adjacent-violators: a dip in a small sample is pooled with its neighbours, not believed. That the probability does not fall as the score rises is the one thing a scorecard may claim about itself; nothing else is assumed about the shape. |
| The same verdicts give the same curve. | `Curve.version` is a digest of the knots and the sample. The order the verdicts arrive in and the time of fitting are not part of it, so a recorded probability can be recomputed later and checked. |
| A figure travels with what it rests on. | `Curve.summary()` carries the number of verdicts, how many of each outcome, the range of scores they cover, the fit's Brier score and expected calibration error (both in-sample), and the curve itself. |

## Using it

```python
from desk_engine.decide import fit

# (score out of 1000, 1 if a person accepted the proposal, 0 if they rejected it)
curve = fit(points, scorer="my-scorecard", fitted_at=time.time())
if curve is None:
    ...                                   # not enough verdicts yet: show the score, and no probability
else:
    p, status = curve.probability(940)    # e.g. (0.88, "calibrated")
    curve.summary()                       # what a page needs to show beside it
```

`reliability(curve, points)` gives the table a person reads to judge the curve: ten bands of score, and for each
how many verdicts fell in it, what the curve says for them and what share was in fact accepted. A band with no
verdicts shows no observed share — which is the point of the table: it shows *where* the verdicts are, and where
there are none.

### A worked example

The verdicts in the tests are invented so that the shares are round: ten proposals at each of six scores, with 2,
4, 6, 8, 9 and 10 of them accepted (`WELL_BEHAVED` in `engine/tests/test_calibration.py`; the figures below are
that file's assertions and the output of `fit` and `reliability` on it).

| Score | Verdicts | Accepted | The curve says | Status |
|---|---|---|---|---|
| 500 | none | — | 0.20 | extrapolated |
| 600 | 10 | 2 | 0.20 | calibrated |
| 650 | none | — | 0.30 (between 600 and 700) | calibrated |
| 900 | 10 | 8 | 0.80 | calibrated |
| 990 | 10 | 10 | 1.00 | calibrated |
| 1000 | none | — | 1.00 | extrapolated |

Sixty verdicts, 39 accepted and 21 rejected, covering scores 600 to 990.

## What is not here

The package has no database, no network and no clock: it computes, and whoever owns the verdicts fits the curve
and keeps it. This repository does not yet contain a scorecard that produces such scores or a record of the
verdicts given on them, so no page of the product shows a figure from this package today.
