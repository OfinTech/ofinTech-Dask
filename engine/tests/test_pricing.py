"""What a model call cost, and the three ways that figure goes wrong if nobody is careful.

A cost that is quietly too low is worse than no cost, because it is believed. Each class of test below guards one
way of producing one: billing cached tokens at the full input rate, costing a whole month against the rate that
happens to be current today, and counting usage nobody has priced as though it were free.
"""

from __future__ import annotations

import datetime
import unittest

from desk_engine import pricing
from desk_engine.db import new_id, now

from .helpers import Harness

DAY = 86400.0


def day(iso: str) -> float:
    return datetime.datetime.combine(datetime.date.fromisoformat(iso), datetime.time.min,
                                     tzinfo=datetime.UTC).timestamp()


def rate(model: str, *, provider_id: str | None = None, frm: str = "2026-01-01", currency: str = "USD",
         inp: float = 0, out: float = 0, cache_read: float = 0, cache_write: float = 0) -> dict:
    return {"id": new_id("mp_"), "provider_id": provider_id, "model": model, "currency": currency,
            "input_per_m": inp, "output_per_m": out, "cache_read_per_m": cache_read,
            "cache_write_per_m": cache_write, "effective_from": day(frm)}


class CostOfTokens(unittest.TestCase):
    def test_cached_tokens_are_taken_out_of_the_input_count(self) -> None:
        """``Usage`` counts cached tokens inside input_tokens, so billing that column whole charges them twice.

        A million prompt tokens of which 900,000 came from the cache cost the input rate on 100,000 and the cache
        rate on the rest. Billing the column whole comes to more than five times as much, and looks just as
        plausible on a dashboard.
        """
        tokens = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read": 900_000, "cache_write": 0}
        cost = pricing.cost_of(tokens, rate("m", inp=3.0, cache_read=0.30))
        self.assertAlmostEqual(0.1 * 3.0 + 0.9 * 0.30, cost, places=9)
        naive = 1.0 * 3.0                       # the whole input column at the input rate
        self.assertGreater(naive, cost * 5)

    def test_cache_writes_are_taken_out_too_and_billed_at_their_own_rate(self) -> None:
        tokens = {"input_tokens": 500_000, "output_tokens": 0, "cache_read": 100_000, "cache_write": 200_000}
        cost = pricing.cost_of(tokens, rate("m", inp=3.0, cache_read=0.30, cache_write=3.75))
        self.assertAlmostEqual(0.2 * 3.0 + 0.1 * 0.30 + 0.2 * 3.75, cost, places=9)

    def test_output_is_billed_on_top(self) -> None:
        tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read": 0, "cache_write": 0}
        self.assertAlmostEqual(3.0 + 15.0, pricing.cost_of(tokens, rate("m", inp=3.0, out=15.0)), places=9)

    def test_a_count_that_does_not_add_up_never_goes_negative(self) -> None:
        """A provider reporting more cached tokens than input tokens must not produce a negative bill."""
        tokens = {"input_tokens": 100, "output_tokens": 0, "cache_read": 900, "cache_write": 0}
        self.assertGreaterEqual(pricing.cost_of(tokens, rate("m", inp=3.0, cache_read=0.3)), 0)


class WhichRateApplies(unittest.TestCase):
    def test_the_rate_in_force_at_the_time_not_the_latest_one(self) -> None:
        rates = [rate("m", frm="2026-01-01", inp=3.0), rate("m", frm="2026-06-01", inp=6.0)]
        self.assertEqual(3.0, pricing.in_force(rates, None, "m", day("2026-03-01"))["input_per_m"])
        self.assertEqual(6.0, pricing.in_force(rates, None, "m", day("2026-07-01"))["input_per_m"])

    def test_usage_before_the_first_rate_has_none(self) -> None:
        rates = [rate("m", frm="2026-06-01", inp=6.0)]
        self.assertIsNone(pricing.in_force(rates, None, "m", day("2026-03-01")))

    def test_a_rate_naming_the_provider_beats_a_wider_one_entered_later(self) -> None:
        """Naming the provider is the narrower statement, so it is the deliberate one."""
        rates = [rate("m", provider_id="llm_a", frm="2026-01-01", inp=3.0),
                 rate("m", provider_id=None, frm="2026-06-01", inp=9.0)]
        self.assertEqual(3.0, pricing.in_force(rates, "llm_a", "m", day("2026-07-01"))["input_per_m"])
        self.assertEqual(9.0, pricing.in_force(rates, "llm_b", "m", day("2026-07-01"))["input_per_m"])

    def test_a_rate_for_another_model_never_applies(self) -> None:
        self.assertIsNone(pricing.in_force([rate("other", inp=3.0)], None, "m", day("2026-07-01")))

    def test_periods_split_the_window_where_the_rate_changes(self) -> None:
        rates = [rate("m", frm="2026-01-01", inp=3.0), rate("m", frm="2026-06-15", inp=6.0)]
        spans = pricing.periods(rates, None, "m", day("2026-06-01"), day("2026-07-01"))
        self.assertEqual(2, len(spans))
        self.assertEqual(3.0, spans[0][2]["input_per_m"])
        self.assertEqual(6.0, spans[1][2]["input_per_m"])
        self.assertEqual(day("2026-06-15"), spans[0][1])

    def test_an_unpriced_stretch_comes_back_as_a_period_with_no_rate(self) -> None:
        """It has to be a period of its own, or the usage inside it silently disappears from the total."""
        spans = pricing.periods([rate("m", frm="2026-06-15", inp=6.0)], None, "m",
                                day("2026-06-01"), day("2026-07-01"))
        self.assertIsNone(spans[0][2])
        self.assertIsNotNone(spans[1][2])


class OverRealUsage(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)
        self.db = self.h.engine.db

    def spend(self, *, model: str = "big", provider_id: str | None = None, at: float,
              inp: int = 0, out: int = 0, cache_read: int = 0, cache_write: int = 0) -> None:
        self.db.insert("usage", {"workspace_id": self.ws, "provider_id": provider_id, "model": model,
                                 "input_tokens": inp, "output_tokens": out, "cache_read": cache_read,
                                 "cache_write": cache_write, "source": "test", "created_at": at})

    def price(self, **kw) -> None:
        row = rate(kw.pop("model", "big"), **kw)
        self.db.insert("model_prices", {**row, "workspace_id": self.ws, "note": None, "created_at": now()})

    def report(self, days: int = 30) -> dict:
        until = now() + 1
        return pricing.priced_usage(self.db, self.ws, until - days * DAY, until)

    def test_nothing_is_priced_until_a_rate_exists(self) -> None:
        self.spend(at=now() - DAY, inp=1_000_000, out=1_000_000)
        report = self.report()
        self.assertFalse(report["priced"])
        self.assertEqual({}, report["totals"])
        self.assertEqual(1, report["unpriced"]["calls"], "unpriced usage must be reported, not counted as free")
        self.assertEqual(1, report["by_model"][0]["unpriced_calls"])

    def test_a_rate_prices_the_usage_it_covers(self) -> None:
        self.spend(at=now() - DAY, inp=1_000_000, out=500_000)
        self.price(frm="2020-01-01", inp=3.0, out=15.0)
        report = self.report()
        self.assertAlmostEqual(3.0 + 7.5, report["totals"]["USD"], places=6)
        self.assertEqual(0, report["unpriced"]["calls"])

    def test_a_price_change_inside_the_window_costs_each_side_at_its_own_rate(self) -> None:
        """Costing the window against one rate is the quiet error: it is wrong on both sides of the change."""
        today = datetime.datetime.now(datetime.UTC).date()
        changed = today - datetime.timedelta(days=5)
        self.spend(at=now() - 10 * DAY, inp=1_000_000)
        self.spend(at=now() - 2 * DAY, inp=1_000_000)
        self.price(frm="2020-01-01", inp=3.0)
        self.price(frm=changed.isoformat(), inp=6.0)
        self.assertAlmostEqual(3.0 + 6.0, self.report()["totals"]["USD"], places=6)

    def test_a_rate_dated_ahead_does_not_re_cost_the_past(self) -> None:
        self.spend(at=now() - DAY, inp=1_000_000)
        self.price(frm="2020-01-01", inp=3.0)
        before = self.report()["totals"]["USD"]
        ahead = (datetime.datetime.now(datetime.UTC).date() + datetime.timedelta(days=30)).isoformat()
        self.price(frm=ahead, inp=99.0)
        self.assertAlmostEqual(before, self.report()["totals"]["USD"], places=6,
                               msg="entering next month's price must not change what last month cost")

    def test_usage_before_the_first_rate_stays_unpriced_while_later_usage_is_costed(self) -> None:
        today = datetime.datetime.now(datetime.UTC).date()
        self.spend(at=now() - 20 * DAY, inp=1_000_000)
        self.spend(at=now() - DAY, inp=1_000_000)
        self.price(frm=(today - datetime.timedelta(days=10)).isoformat(), inp=3.0)
        report = self.report()
        self.assertAlmostEqual(3.0, report["totals"]["USD"], places=6)
        self.assertEqual(1, report["unpriced"]["calls"])
        self.assertEqual(1, report["by_model"][0]["unpriced_calls"])
        self.assertEqual(2, report["by_model"][0]["calls"], "the row still shows every call it covers")

    def test_two_currencies_are_kept_apart_and_never_converted(self) -> None:
        """An exchange rate this product invented would be a figure with nothing behind it."""
        self.spend(model="big", at=now() - DAY, inp=1_000_000)
        self.spend(model="small", at=now() - DAY, inp=1_000_000)
        self.price(model="big", frm="2020-01-01", inp=3.0, currency="USD")
        self.price(model="small", frm="2020-01-01", inp=10.0, currency="SAR")
        report = self.report()
        self.assertEqual(["SAR", "USD"], report["currencies"])
        self.assertAlmostEqual(3.0, report["totals"]["USD"], places=6)
        self.assertAlmostEqual(10.0, report["totals"]["SAR"], places=6)

    def test_cached_tokens_are_not_billed_as_input_end_to_end(self) -> None:
        self.spend(at=now() - DAY, inp=1_000_000, cache_read=900_000)
        self.price(frm="2020-01-01", inp=3.0, cache_read=0.30)
        self.assertAlmostEqual(0.1 * 3.0 + 0.9 * 0.30, self.report()["totals"]["USD"], places=6)

    def test_usage_outside_the_window_is_left_out(self) -> None:
        self.spend(at=now() - 90 * DAY, inp=1_000_000)
        self.price(frm="2020-01-01", inp=3.0)
        self.assertEqual({}, self.report(30)["totals"])


if __name__ == "__main__":
    unittest.main()


class Routes(unittest.TestCase):
    def setUp(self) -> None:
        self.h = Harness()
        self.addCleanup(self.h.close)
        self.ws = self.h.setup_workspace(with_model=False)

    def add(self, **kw):
        body = {"model": "big", "currency": "usd", "input_per_m": 3.0, "output_per_m": 15.0}
        return self.h.api("POST", f"/api/w/{self.ws}/prices", json={**body, **kw}, expect=kw.pop("expect", 200))

    def audited(self, action: str) -> list[dict]:
        return self.h.engine.db.all("SELECT * FROM audit_log WHERE workspace_id = ? AND action = ? ORDER BY id",
                                    (self.ws, action))

    def test_a_rate_is_stored_normalised_and_audited(self) -> None:
        row = self.add(effective_from="2026-06-01", note=" opening contract ")
        self.assertEqual("USD", row["currency"], "the currency is stored upper case whatever was typed")
        self.assertEqual(day("2026-06-01"), row["effective_from"])
        self.assertEqual("opening contract", row["note"])
        self.assertEqual(1, len(self.audited("price.add")))

    def test_a_rate_with_no_date_starts_today_not_at_the_epoch(self) -> None:
        """Dating it from the epoch would silently re-cost every call the workspace has ever made."""
        row = self.add()
        today = datetime.datetime.now(datetime.UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        self.assertAlmostEqual(today, row["effective_from"], places=0)

    def test_a_rate_of_nothing_for_everything_is_refused(self) -> None:
        self.h.api("POST", f"/api/w/{self.ws}/prices",
                   json={"model": "big", "currency": "USD"}, expect=422)

    def test_a_date_that_is_not_a_date_is_refused(self) -> None:
        self.h.api("POST", f"/api/w/{self.ws}/prices",
                   json={"model": "big", "currency": "USD", "input_per_m": 1, "effective_from": "June"}, expect=422)

    def test_a_provider_from_another_workspace_is_refused(self) -> None:
        self.h.api("POST", f"/api/w/{self.ws}/prices",
                   json={"model": "big", "currency": "USD", "input_per_m": 1, "provider_id": "llm_elsewhere"},
                   expect=404)

    def test_an_absurd_rate_is_refused(self) -> None:
        """A typo in a rate is a wrong cost on every figure after it, so the field has a ceiling."""
        self.h.api("POST", f"/api/w/{self.ws}/prices",
                   json={"model": "big", "currency": "USD", "input_per_m": 10_000_000}, expect=422)

    def test_a_correction_re_costs_and_the_trail_keeps_what_it_was(self) -> None:
        self.h.engine.db.insert("usage", {"workspace_id": self.ws, "provider_id": None, "model": "big",
                                          "input_tokens": 1_000_000, "output_tokens": 0, "cache_read": 0,
                                          "cache_write": 0, "source": "test", "created_at": now() - DAY})
        row = self.add(effective_from="2020-01-01", input_per_m=30.0)      # a decimal point in the wrong place
        self.assertAlmostEqual(30.0, self.h.api("GET", f"/api/w/{self.ws}/usage")["totals"]["USD"], places=4)

        self.h.api("PATCH", f"/api/w/{self.ws}/prices/{row['id']}", json={"input_per_m": 3.0})
        self.assertAlmostEqual(3.0, self.h.api("GET", f"/api/w/{self.ws}/usage")["totals"]["USD"], places=4,
                               msg="correcting a rate has to correct the figures it produced")
        entry = self.audited("price.change")[0]
        detail = __import__("json").loads(entry["detail"])
        self.assertEqual(30.0, detail["was"]["input_per_m"])
        self.assertEqual(3.0, detail["now"]["input_per_m"])

    def test_removing_a_rate_leaves_the_usage_unpriced_rather_than_free(self) -> None:
        self.h.engine.db.insert("usage", {"workspace_id": self.ws, "provider_id": None, "model": "big",
                                          "input_tokens": 1_000_000, "output_tokens": 0, "cache_read": 0,
                                          "cache_write": 0, "source": "test", "created_at": now() - DAY})
        row = self.add(effective_from="2020-01-01")
        self.h.api("DELETE", f"/api/w/{self.ws}/prices/{row['id']}")
        report = self.h.api("GET", f"/api/w/{self.ws}/usage")
        self.assertEqual({}, report["totals"])
        self.assertEqual(1, report["unpriced"]["calls"])
        self.assertEqual(3.0, __import__("json").loads(self.audited("price.remove")[0]["detail"])["input_per_m"],
                         "the trail keeps the rate that was removed, since figures were reported from it")

    def test_the_list_offers_the_models_actually_seen(self) -> None:
        for model in ("small", "big"):
            self.h.engine.db.insert("usage", {"workspace_id": self.ws, "provider_id": None, "model": model,
                                              "input_tokens": 10, "output_tokens": 0, "cache_read": 0,
                                              "cache_write": 0, "source": "test", "created_at": now()})
        self.assertEqual(["big", "small"], self.h.api("GET", f"/api/w/{self.ws}/prices")["models_seen"])

    def test_rates_need_the_admin_role(self) -> None:
        self.h.engine.db.execute("UPDATE members SET role = 'editor' WHERE workspace_id = ?", (self.ws,))
        self.h.api("GET", f"/api/w/{self.ws}/prices", expect=403)
        self.h.api("POST", f"/api/w/{self.ws}/prices",
                   json={"model": "big", "currency": "USD", "input_per_m": 1}, expect=403)
        self.h.api("GET", f"/api/w/{self.ws}/usage", expect=403)
