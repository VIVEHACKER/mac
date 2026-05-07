from datetime import date, timedelta
import unittest

from trading_copilot.industry_rotation import PricePoint
from trading_copilot.macro import FredSeries, MacroObservation
from trading_copilot.pattern_mining import (
    ASSET_SETS,
    Condition,
    OutcomeRule,
    PatternSpec,
    condition_event_dates,
    derived_inverse_percent_change_series,
    derived_percent_change_series,
    expand_asset_set,
    evaluate_pattern,
    format_pattern_report,
    mine_default_patterns,
    wilson_lower_bound,
)


class PatternMiningTests(unittest.TestCase):
    def test_condition_event_dates_count_only_condition_starts(self):
        series = FredSeries(
            "VIXCLS",
            "VIX",
            "fixture",
            (
                MacroObservation("VIXCLS", date(2026, 1, 1), 20.0),
                MacroObservation("VIXCLS", date(2026, 1, 2), 82.0),
                MacroObservation("VIXCLS", date(2026, 1, 3), 85.0),
                MacroObservation("VIXCLS", date(2026, 1, 4), 30.0),
                MacroObservation("VIXCLS", date(2026, 1, 5), 81.0),
            ),
        )

        events = condition_event_dates(series, Condition("VIX >= 80", ">=", 80.0))

        self.assertEqual(events, (date(2026, 1, 2), date(2026, 1, 5)))

    def test_evaluate_pattern_marks_historical_perfect_sample_without_calling_it_certain(self):
        spec = PatternSpec(
            condition_label="VIX >= 80",
            condition=Condition("VIX >= 80", ">=", 80.0),
            asset="SPY",
            horizon_days=2,
            outcome=OutcomeRule("positive_return"),
        )
        result = evaluate_pattern(
            spec,
            condition_series=FredSeries(
                "VIXCLS",
                "VIX",
                "fred fixture",
                (
                    MacroObservation("VIXCLS", date(2026, 1, 1), 20.0),
                    MacroObservation("VIXCLS", date(2026, 1, 2), 82.0),
                    MacroObservation("VIXCLS", date(2026, 1, 3), 30.0),
                    MacroObservation("VIXCLS", date(2026, 1, 4), 81.0),
                ),
            ),
            prices=price_points("SPY", [100.0, 90.0, 95.0, 96.0, 97.0, 102.0]),
            min_samples=2,
        )

        self.assertEqual(result.samples, 2)
        self.assertEqual(result.wins, 2)
        self.assertEqual(result.win_rate, 100.0)
        self.assertTrue(result.historical_perfect)
        self.assertLess(result.wilson_lower_95, 100.0)
        self.assertIn("historical perfect", result.read.lower())
        self.assertIn("not a guarantee", result.read.lower())

    def test_evaluate_pattern_can_measure_adjustment_after_yield_curve_inversion(self):
        spec = PatternSpec(
            condition_label="10Y-2Y < 0",
            condition=Condition("10Y-2Y < 0", "<", 0.0),
            asset="SPY",
            horizon_days=3,
            outcome=OutcomeRule("drawdown_at_or_below", threshold=-10.0),
        )
        result = evaluate_pattern(
            spec,
            condition_series=FredSeries(
                "T10Y2Y",
                "10Y-2Y",
                "fred fixture",
                (
                    MacroObservation("T10Y2Y", date(2026, 1, 1), 0.2),
                    MacroObservation("T10Y2Y", date(2026, 1, 2), -0.1),
                    MacroObservation("T10Y2Y", date(2026, 1, 3), 0.1),
                    MacroObservation("T10Y2Y", date(2026, 1, 4), -0.2),
                ),
            ),
            prices=price_points("SPY", [100.0, 100.0, 88.0, 92.0, 100.0, 95.0, 94.0]),
            min_samples=2,
        )

        self.assertEqual(result.samples, 2)
        self.assertEqual(result.wins, 1)
        self.assertEqual(result.win_rate, 50.0)
        self.assertIn("max drawdown", result.outcome_label.lower())

    def test_mine_default_patterns_and_report_include_overfitting_warning(self):
        report = mine_default_patterns(
            macro_provider=StaticMacroProvider(),
            history_provider=StaticHistoryProvider(),
            assets=("GLD",),
            horizons=(2,),
            min_samples=1,
        )
        text = format_pattern_report(report, limit=5)

        self.assertGreater(report.hypotheses_tested, 0)
        self.assertIn("# Historical Pattern Mining", text)
        self.assertIn("Multiple-testing", text)
        self.assertIn("Not investment advice", text)
        self.assertIn("VIX", text)

    def test_asset_set_expands_commodities_energy_metals_bonds_cash_and_coal_proxies(self):
        assets = expand_asset_set("macro")

        for symbol in ("GLD", "SLV", "TLT", "USO", "UNG", "CPER", "COPX", "REMX", "URA", "BTU", "CNR", "ZC=F"):
            self.assertIn(symbol, assets)
        for symbol in ("EMB", "VWOB", "EMLC", "SGOV", "BIL", "USFR", "JPST"):
            self.assertIn(symbol, assets)
        self.assertIn("coal", ASSET_SETS)
        self.assertIn("em_bonds", ASSET_SETS)
        self.assertIn("mmf", ASSET_SETS)

    def test_derived_percent_change_series_supports_inflation_and_dollar_conditions(self):
        series = FredSeries(
            "CPIAUCSL",
            "Consumer Price Index",
            "fred fixture",
            (
                MacroObservation("CPIAUCSL", date(2025, 1, 1), 100.0),
                MacroObservation("CPIAUCSL", date(2026, 1, 1), 106.0),
            ),
        )

        derived = derived_percent_change_series(series, months=12, label="CPI YoY")

        self.assertEqual(derived.series_id, "CPIAUCSL_YOY_12M")
        self.assertAlmostEqual(derived.observations[-1].value, 6.0)

    def test_inverse_percent_change_series_turns_usd_quote_into_currency_strength(self):
        series = FredSeries(
            "DEXJPUS",
            "Japanese Yen to U.S. Dollar",
            "fred fixture",
            (
                MacroObservation("DEXJPUS", date(2025, 7, 1), 150.0),
                MacroObservation("DEXJPUS", date(2026, 1, 1), 135.0),
            ),
        )

        derived = derived_inverse_percent_change_series(series, months=6, label="Yen 6M Strength")

        self.assertEqual(derived.series_id, "DEXJPUS_INV_PCT_6M")
        self.assertAlmostEqual(derived.observations[-1].value, 11.1111, places=3)

    def test_mine_default_patterns_can_use_macro_asset_set_and_economic_conditions(self):
        report = mine_default_patterns(
            macro_provider=StaticMacroProvider(),
            history_provider=StaticHistoryProvider(),
            assets=expand_asset_set("macro")[:3],
            horizons=(2,),
            min_samples=1,
        )
        conditions = {result.condition for result in report.results}

        self.assertIn("CPI YoY >= 5", conditions)
        self.assertIn("Fed Funds 6M Change >= 1", conditions)
        self.assertIn("Dollar 6M Change >= 5", conditions)
        self.assertIn("Euro 6M Strength >= 5", conditions)
        self.assertIn("Yen 6M Strength <= -5", conditions)
        self.assertIn("Corn 6M Change >= 20", conditions)

    def test_wilson_lower_bound_is_conservative_for_small_perfect_samples(self):
        self.assertAlmostEqual(wilson_lower_bound(2, 2), 34.24, places=1)


def price_points(symbol: str, closes: list[float]) -> tuple[PricePoint, ...]:
    start = date(2026, 1, 1)
    return tuple(
        PricePoint(symbol, start + timedelta(days=index), close)
        for index, close in enumerate(closes)
    )


class StaticMacroProvider:
    def series(self, series_id: str) -> FredSeries:
        observations = {
            "VIXCLS": (
                MacroObservation("VIXCLS", date(2026, 1, 1), 20.0),
                MacroObservation("VIXCLS", date(2026, 1, 2), 82.0),
                MacroObservation("VIXCLS", date(2026, 1, 3), 30.0),
                MacroObservation("VIXCLS", date(2026, 1, 4), 81.0),
            ),
            "T10Y2Y": (
                MacroObservation("T10Y2Y", date(2026, 1, 1), 0.2),
                MacroObservation("T10Y2Y", date(2026, 1, 2), -0.1),
                MacroObservation("T10Y2Y", date(2026, 1, 3), 0.1),
                MacroObservation("T10Y2Y", date(2026, 1, 4), -0.2),
            ),
            "CPIAUCSL": (
                MacroObservation("CPIAUCSL", date(2024, 1, 1), 100.0),
                MacroObservation("CPIAUCSL", date(2025, 1, 1), 104.0),
                MacroObservation("CPIAUCSL", date(2026, 1, 1), 110.0),
            ),
            "FEDFUNDS": (
                MacroObservation("FEDFUNDS", date(2025, 7, 1), 2.0),
                MacroObservation("FEDFUNDS", date(2026, 1, 1), 3.5),
            ),
            "DTWEXBGS": (
                MacroObservation("DTWEXBGS", date(2025, 7, 1), 100.0),
                MacroObservation("DTWEXBGS", date(2026, 1, 1), 106.0),
            ),
            "UNRATE": (
                MacroObservation("UNRATE", date(2025, 7, 1), 3.8),
                MacroObservation("UNRATE", date(2026, 1, 1), 4.3),
            ),
            "DEXUSEU": (
                MacroObservation("DEXUSEU", date(2025, 7, 1), 1.0),
                MacroObservation("DEXUSEU", date(2026, 1, 1), 1.06),
            ),
            "DEXUSUK": (
                MacroObservation("DEXUSUK", date(2025, 7, 1), 1.25),
                MacroObservation("DEXUSUK", date(2026, 1, 1), 1.32),
            ),
            "DEXJPUS": (
                MacroObservation("DEXJPUS", date(2025, 7, 1), 140.0),
                MacroObservation("DEXJPUS", date(2026, 1, 1), 148.0),
            ),
            "DEXSZUS": (
                MacroObservation("DEXSZUS", date(2025, 7, 1), 0.90),
                MacroObservation("DEXSZUS", date(2026, 1, 1), 0.84),
            ),
            "DEXCHUS": (
                MacroObservation("DEXCHUS", date(2025, 7, 1), 7.25),
                MacroObservation("DEXCHUS", date(2026, 1, 1), 7.00),
            ),
            "PMAIZMTUSDM": (
                MacroObservation("PMAIZMTUSDM", date(2025, 7, 1), 180.0),
                MacroObservation("PMAIZMTUSDM", date(2026, 1, 1), 225.0),
            ),
        }[series_id]
        return FredSeries(series_id, series_id, f"fred fixture {series_id}", observations)


class StaticHistoryProvider:
    def history(
        self,
        symbol: str,
        range_period: str = "max",
        interval: str = "1d",
    ) -> tuple[PricePoint, ...]:
        return price_points(symbol, [100.0, 90.0, 95.0, 96.0, 97.0, 102.0])


if __name__ == "__main__":
    unittest.main()
