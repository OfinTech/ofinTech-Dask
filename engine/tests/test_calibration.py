"""The measured probability, as pure functions.

What is proved here, because each of these is a way a probability stops having a source:

* the curve is a measurement of the verdicts given to it, monotone in the score, and the same verdicts always
  give the same curve and the same version;
* below the minimum sample, or without both outcomes, there is no curve at all -- not a confident one;
* outside the range the verdicts cover, a figure is marked extrapolated rather than offered as measured;
* what a page is given to show carries the sample the figure rests on.

Every verdict below is invented: (score, accepted, rejected) triples written by hand so the shares are round.
"""

from __future__ import annotations

import unittest

from desk_engine.decide import calibration

T = 1_700_000_000.0


def verdicts(spec: list[tuple[int, int, int]]) -> list[tuple[int, int]]:
    """(score, accepted, rejected) triples to (score, outcome) points."""
    out: list[tuple[int, int]] = []
    for score, ok, no in spec:
        out += [(score, 1)] * ok + [(score, 0)] * no
    return out


WELL_BEHAVED = verdicts([(600, 2, 8), (700, 4, 6), (800, 6, 4), (900, 8, 2), (950, 9, 1), (990, 10, 0)])


class CurveTest(unittest.TestCase):
    def test_is_a_measurement_of_the_verdicts(self):
        curve = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T)
        self.assertIsNotNone(curve)
        self.assertEqual(curve.n, 60)
        self.assertEqual(curve.accepted, 39)
        self.assertAlmostEqual(curve.probability(600)[0], 0.2, places=3)
        self.assertAlmostEqual(curve.probability(900)[0], 0.8, places=3)
        self.assertAlmostEqual(curve.probability(990)[0], 1.0, places=3)

    def test_monotone_in_the_score(self):
        curve = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T)
        last = -1.0
        for s in range(600, 991, 5):
            p, _ = curve.probability(s)
            self.assertGreaterEqual(p, last - 1e-12)
            last = p

    def test_violators_are_pooled_not_believed(self):
        """A dip at 800 among rising scores is noise in a small sample; the fit pools it with its neighbours
        rather than reporting that scoring higher made acceptance less likely."""
        dip = verdicts([(700, 5, 5), (800, 2, 8), (900, 8, 2)])
        curve = calibration.fit(dip, scorer="t", fitted_at=T)
        self.assertLessEqual(curve.probability(700)[0], curve.probability(800)[0] + 1e-12)
        self.assertLessEqual(curve.probability(800)[0], curve.probability(900)[0] + 1e-12)

    def test_same_verdicts_same_curve_same_version(self):
        a = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T)
        b = calibration.fit(list(reversed(WELL_BEHAVED)), scorer="t", fitted_at=T + 5)
        self.assertEqual(a.version, b.version)
        self.assertEqual([k.probability for k in a.knots], [k.probability for k in b.knots])
        self.assertNotEqual(a.fitted_at, b.fitted_at)      # when it was fitted is not part of what it says

    def test_different_verdicts_different_version(self):
        a = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T)
        b = calibration.fit(WELL_BEHAVED + [(950, 0)], scorer="t", fitted_at=T)
        self.assertNotEqual(a.version, b.version)

    def test_below_the_minimum_sample_there_is_no_curve(self):
        self.assertIsNone(calibration.fit(WELL_BEHAVED[:29], scorer="t", fitted_at=T))

    def test_without_both_outcomes_there_is_no_curve(self):
        """Forty accepts and no rejects would fit a flat line at one. That is not a measurement of anything."""
        self.assertIsNone(calibration.fit([(900, 1)] * 40, scorer="t", fitted_at=T))
        self.assertIsNone(calibration.fit([(900, 1)] * 36 + [(500, 0)] * 4, scorer="t", fitted_at=T))

    def test_outside_the_verdicts_range_is_extrapolated(self):
        curve = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T)
        self.assertEqual(curve.probability(1000)[1], calibration.EXTRAPOLATED)
        self.assertEqual(curve.probability(100)[1], calibration.EXTRAPOLATED)
        self.assertEqual(curve.probability(750)[1], calibration.CALIBRATED)
        # and the extrapolated value is the nearest end, never beyond it
        self.assertAlmostEqual(curve.probability(1000)[0], curve.probability(990)[0], places=6)

    def test_survives_a_round_trip(self):
        curve = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T)
        back = calibration.Curve.from_json(curve.to_json())
        self.assertEqual(back, curve)

    def test_fit_quality_is_reported(self):
        curve = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T)
        self.assertGreaterEqual(curve.brier, 0.0)
        self.assertLess(curve.brier, 0.25)              # better than always saying 0.5
        self.assertGreaterEqual(curve.ece, 0.0)
        self.assertLess(curve.ece, 0.05)                # in-sample, so it should be close to zero

    def test_reliability_table_shows_where_the_verdicts_are(self):
        curve = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T)
        rows = calibration.reliability(curve, WELL_BEHAVED)
        self.assertEqual(len(rows), calibration.RELIABILITY_BINS)
        self.assertEqual(sum(r["n"] for r in rows), 60)
        self.assertIsNone(rows[0]["observed"])          # no verdicts scored 0-99
        self.assertIsNotNone(rows[9]["observed"])

    def test_reliability_compares_like_with_like(self):
        """Ten verdicts that all scored 600, two of them accepted: the curve says 0.2 there and the table says so.
        Read at the middle of the band (650) the same curve says 0.3, and a fit with no error looks a tenth out."""
        curve = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T)
        rows = {r["from"]: r for r in calibration.reliability(curve, WELL_BEHAVED)}
        for band in (600, 700, 800, 900):
            self.assertAlmostEqual(rows[band]["predicted"], rows[band]["observed"], places=3)
        self.assertAlmostEqual(rows[600]["predicted"], 0.2, places=3)
        self.assertAlmostEqual(curve.probability(650)[0], 0.3, places=3)

    def test_scores_off_the_scale_are_clamped_not_refused(self):
        curve = calibration.fit(WELL_BEHAVED + [(1200, 1)] * 5 + [(-50, 0)] * 5, scorer="t", fitted_at=T)
        self.assertEqual(curve.hi, 1000)
        self.assertEqual(curve.lo, 0)



    def test_what_a_page_shows_carries_the_sample(self):
        """A probability is never handed over apart from what it was counted on: how many verdicts, how many of
        each outcome, and the range of scores they cover."""
        s = calibration.fit(WELL_BEHAVED, scorer="t", fitted_at=T).summary()
        self.assertEqual((s["n"], s["accepted"], s["rejected"]), (60, 39, 21))
        self.assertEqual((s["lo"], s["hi"]), (600, 990))
        self.assertEqual(sum(k["n"] for k in s["knots"]), 60)


if __name__ == "__main__":
    unittest.main()
