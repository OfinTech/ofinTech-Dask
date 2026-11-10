"""From a score to a probability that has a source.

A scorecard gives a proposal a number. The number says why the proposal looks good; it does not say how often
proposals that look this good turn out to be right. This package answers that from the only place it can be
answered: what people decided. A probability here is the share of proposals that scored like this one and that a
person then accepted, and it carries the count it was measured over.

It is a measurement, not a model: isotonic regression in plain Python, with no library, no parameter to tune and
no random start, so the same verdicts always give the same curve and the same version. Three things are refused
rather than promised:

* below the minimum sample, or without both outcomes, there is no curve at all;
* outside the range of scores the verdicts cover, a figure is marked ``extrapolated``;
* a probability is never reported apart from the sample it rests on.

The package has no database, no network and no clock. It computes; whoever owns the verdicts fits the curve and
keeps it. See docs/MEASURED_PROBABILITY.md.
"""

from __future__ import annotations

from .calibration import (
    CALIBRATED,
    EXTRAPOLATED,
    MIN_EACH,
    MIN_SAMPLES,
    SCORE_MAX,
    UNCALIBRATED,
    Curve,
    Knot,
    fit,
    reliability,
)

__all__ = ["CALIBRATED", "EXTRAPOLATED", "MIN_EACH", "MIN_SAMPLES", "SCORE_MAX", "UNCALIBRATED", "Curve", "Knot",
           "fit", "reliability"]
