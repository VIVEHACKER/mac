import math
import unittest
from datetime import date

from trading_copilot.macro import FredSeries, MacroObservation
from trading_copilot.macro_forecast import (
    KR_SPECS,
    US_SPECS,
    forecast_series,
    m_random_walk,
    m_seasonal,
    month_add,
    mom_series,
    to_monthly,
)


def _months(start: tuple[int, int], n: int):
    keys = []
    cur = start
    for _ in range(n):
        keys.append(cur)
        cur = month_add(cur, 1)
    return keys


def _synthetic_index(n: int = 84, base: float = 100.0) -> list[tuple[str, float]]:
    """Deterministic index with seasonal MoM (~0.3% +/- 0.2% by month)."""
    keys = _months((2019, 1), n)
    value = base
    rows = []
    for y, m in keys:
        mom = 0.003 + 0.002 * math.sin(2 * math.pi * m / 12.0)
        value *= 1.0 + mom
        rows.append((date(y, m, 1).isoformat(), round(value, 4)))
    return rows


class FakeProvider:
    def __init__(self, rows_by_id: dict[str, list[tuple[str, float]]]):
        self._rows = rows_by_id

    def series(self, series_id: str) -> FredSeries:
        rows = self._rows[series_id]
        obs = tuple(MacroObservation(series_id, date.fromisoformat(d), v) for d, v in rows)
        return FredSeries(series_id, series_id, "fake://" + series_id, obs)


class HelperTests(unittest.TestCase):
    def test_month_add_wraps_year(self):
        self.assertEqual(month_add((2026, 12), 1), (2027, 1))
        self.assertEqual(month_add((2026, 1), -1), (2025, 12))
        self.assertEqual(month_add((2026, 5), -12), (2025, 5))

    def test_mom_series_drops_non_contiguous(self):
        monthly = {(2026, 1): 100.0, (2026, 2): 101.0, (2026, 4): 103.0}
        keys, vals = mom_series(monthly)
        # Feb is contiguous with Jan; April is not contiguous with Feb -> dropped.
        self.assertEqual(keys, [(2026, 2)])
        self.assertAlmostEqual(vals[0], 1.0, places=6)

    def test_seasonal_returns_prior_year_same_month(self):
        keys = [(2025, 5), (2025, 6)]
        mom = [0.4, 0.6]
        self.assertAlmostEqual(m_seasonal(keys, mom, (2026, 5)), 0.4)
        self.assertIsNone(m_seasonal(keys, mom, (2026, 7)))

    def test_random_walk_is_last_value(self):
        self.assertEqual(m_random_walk([0.1, 0.2, 0.3]), 0.3)


class ForecastSeriesTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeProvider({"IDX": _synthetic_index(84)})

    def test_target_is_month_after_last_observation(self):
        f = forecast_series(self.provider, "IDX")
        last = f.last_observed
        self.assertEqual(f.target, month_add((last.year, last.month), 1))

    def test_last_mom_matches_raw_data(self):
        rows = self.provider._rows["IDX"]
        expected = (rows[-1][1] / rows[-2][1] - 1.0) * 100.0
        f = forecast_series(self.provider, "IDX")
        self.assertAlmostEqual(f.last_mom, expected, places=4)

    def test_forecast_and_yoy_are_finite_and_consistent(self):
        f = forecast_series(self.provider, "IDX")
        self.assertTrue(math.isfinite(f.ensemble_mom))
        # implied YoY must equal projected-index 12-month change
        monthly = to_monthly(self.provider.series("IDX").observations)
        py = month_add(f.target, -12)
        expected_yoy = (f.proj_index / monthly[py] - 1.0) * 100.0
        self.assertAlmostEqual(f.yoy, expected_yoy, places=4)

    def test_seasonal_series_beats_random_walk_on_seasonal_data(self):
        # On a purely seasonal series the ensemble should beat the naive RW.
        f = forecast_series(self.provider, "IDX")
        self.assertGreater(f.skill_pct, 0.0)
        self.assertGreaterEqual(f.n_test, 12)

    def test_interval_brackets_point_forecast(self):
        f = forecast_series(self.provider, "IDX")
        lo80, hi80 = f.pi80
        self.assertLessEqual(lo80, f.ensemble_mom)
        self.assertLessEqual(f.ensemble_mom, hi80)

    def test_short_history_still_produces_target(self):
        prov = FakeProvider({"IDX": _synthetic_index(20)})
        f = forecast_series(prov, "IDX")
        self.assertIsNotNone(f.target)
        self.assertEqual(len(f.target), 2)

    def test_short_history_ensemble_is_finite(self):
        # codex P2 regression: with <12 backtest folds every model used to be
        # dropped and the ensemble degenerated to NaN.
        prov = FakeProvider({"IDX": _synthetic_index(20)})
        f = forecast_series(prov, "IDX")
        self.assertTrue(math.isfinite(f.ensemble_mom))
        self.assertGreater(len(f.weights), 0)

    def test_interval_shifts_against_residual_bias(self):
        # codex P2 regression: residuals are (prediction - actual); on a steadily
        # accelerating series the ensemble under-forecasts (resid < 0), so the
        # empirical band must sit ABOVE the point forecast, not below it.
        keys = _months((2019, 1), 84)
        value, rows = 100.0, []
        for i, (y, m) in enumerate(keys):
            value *= 1.0 + (0.001 + 0.00005 * i)  # accelerating MoM
            rows.append((date(y, m, 1).isoformat(), round(value, 6)))
        f = forecast_series(FakeProvider({"IDX": rows}), "IDX")
        upper_margin = f.pi80[1] - f.ensemble_mom
        lower_margin = f.ensemble_mom - f.pi80[0]
        self.assertGreater(upper_margin, lower_margin)

    def test_energy_bridge_note_added_when_energy_supplied(self):
        prov = FakeProvider({"IDX": _synthetic_index(84), "OIL": _synthetic_index(84, base=70.0)})
        f = forecast_series(prov, "IDX", energy_provider=prov, energy_series_id="OIL")
        self.assertTrue(any("energy bridge uses OIL" in n for n in f.notes))


class RegionSpecTests(unittest.TestCase):
    def test_us_specs_cover_headline_core_ppi(self):
        ids = [s.series_id for s in US_SPECS]
        self.assertEqual(ids, ["CPIAUCSL", "CPILFESL", "PPIFIS"])

    def test_kr_specs_use_ecos_codes(self):
        ids = [s.series_id for s in KR_SPECS]
        self.assertIn("901Y009/0", ids)
        self.assertIn("404Y014/*AA", ids)


if __name__ == "__main__":
    unittest.main()
