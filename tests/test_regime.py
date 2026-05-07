from datetime import date
import unittest

from trading_copilot.industry_rotation import PricePoint
from trading_copilot.macro import FredSeries, MacroObservation
from trading_copilot.regime import (
    RegimeIndicators,
    TEMPLATES,
    build_regime_report,
    classify_regime,
    format_regime_report,
)


def make_indicators(**kwargs) -> RegimeIndicators:
    defaults = dict(
        cpi_yoy=2.5,
        core_cpi_yoy=2.5,
        cpi_6m_delta=0.0,
        unemployment=4.0,
        unemployment_6m_delta=0.0,
        fed_funds=4.5,
        fed_funds_6m_delta=0.0,
        yield_curve_spread=0.5,
        indpro_yoy=1.5,
        retail_yoy=2.5,
        vix=15.0,
        vix_20d_avg=15.0,
        spy_drawdown_252d=-0.02,
        credit_spread_hy=3.5,
        sources=(),
    )
    defaults.update(kwargs)
    return RegimeIndicators(**defaults)


class RegimeClassifierTests(unittest.TestCase):
    def test_panic_when_vix_very_high(self):
        reading = classify_regime(make_indicators(vix=42.0, spy_drawdown_252d=-0.18))
        self.assertEqual(reading.primary, "panic")
        self.assertGreaterEqual(reading.confidence, 0.8)

    def test_panic_when_two_panic_signals_combine(self):
        reading = classify_regime(
            make_indicators(vix=31.0, credit_spread_hy=6.5)
        )
        self.assertEqual(reading.primary, "panic")

    def test_recession_when_unemployment_and_indpro_break(self):
        reading = classify_regime(
            make_indicators(
                unemployment_6m_delta=0.6,
                indpro_yoy=-1.0,
                yield_curve_spread=-0.4,
            )
        )
        self.assertEqual(reading.primary, "recession")

    def test_inflation_shock_when_cpi_hot_and_accelerating(self):
        reading = classify_regime(
            make_indicators(cpi_yoy=4.5, cpi_6m_delta=0.5, indpro_yoy=2.0)
        )
        self.assertEqual(reading.primary, "inflation_shock")

    def test_deflation_when_cpi_sub_one_and_cooling(self):
        reading = classify_regime(
            make_indicators(cpi_yoy=0.5, cpi_6m_delta=-0.5, indpro_yoy=-0.5)
        )
        self.assertEqual(reading.primary, "deflation_risk")

    def test_easy_money_when_fed_cutting_and_calm(self):
        reading = classify_regime(
            make_indicators(fed_funds_6m_delta=-1.0, vix=14.0, cpi_yoy=2.2)
        )
        self.assertEqual(reading.primary, "easy_money")

    def test_stagflation_when_cpi_hot_and_growth_weak(self):
        reading = classify_regime(
            make_indicators(cpi_yoy=3.8, indpro_yoy=0.2, cpi_6m_delta=0.0)
        )
        # Tightened thresholds: CPI ≥ 3.5 AND INDPRO < 0.5 with no SPY 200d-MA bull.
        self.assertIn(reading.primary, {"stagflation", "inflation_shock"})

    def test_disinflation_expansion_when_cooling_and_growing(self):
        reading = classify_regime(
            make_indicators(cpi_yoy=2.0, cpi_6m_delta=-0.4, indpro_yoy=2.0)
        )
        self.assertEqual(reading.primary, "disinflation_expansion")

    def test_transition_when_no_signals_fire(self):
        reading = classify_regime(make_indicators())
        self.assertEqual(reading.primary, "transition")


class TemplatesTests(unittest.TestCase):
    def test_all_regimes_have_template(self):
        for regime_name in (
            "panic", "recession", "inflation_shock", "deflation_risk",
            "easy_money", "stagflation", "disinflation_expansion", "transition",
        ):
            self.assertIn(regime_name, TEMPLATES)

    def test_each_template_allocations_sum_to_100(self):
        for name, template in TEMPLATES.items():
            total = sum(a.weight_pct for a in template.allocations)
            self.assertAlmostEqual(
                total, 100.0, places=1,
                msg=f"{name} allocations sum to {total}, expected 100",
            )

    def test_each_template_has_required_fields(self):
        for name, template in TEMPLATES.items():
            self.assertGreater(len(template.allocations), 0, f"{name} has no allocations")
            self.assertGreater(len(template.exit_triggers), 0, f"{name} has no exit triggers")
            self.assertTrue(template.description, f"{name} has empty description")
            self.assertTrue(template.vix_throttle, f"{name} has no VIX throttle")


class FormatTests(unittest.TestCase):
    def test_format_report_includes_key_sections(self):
        from trading_copilot.regime import RegimeReport, classify_regime

        reading = classify_regime(make_indicators(vix=42.0, spy_drawdown_252d=-0.18))
        template = TEMPLATES[reading.primary]
        report = RegimeReport(as_of=date(2026, 5, 7), reading=reading, template=template)
        text = format_regime_report(report)

        self.assertIn("# Macro Regime Engine", text)
        self.assertIn("Primary Regime:", text)
        self.assertIn("Triggers", text)
        self.assertIn("Indicator Snapshot", text)
        self.assertIn("Suggested Portfolio", text)
        self.assertIn("Avoid", text)
        self.assertIn("Exit / Regime-Change Triggers", text)
        self.assertIn("Operating Rules", text)
        self.assertIn("Not investment advice", text)


def disinflation_macro() -> "StaticMacroProvider":
    """Macro fixture that targets the disinflation_expansion regime."""
    def monthly(series_id, *values):
        return FredSeries(
            series_id=series_id,
            name=series_id,
            source=f"fixture/{series_id}",
            observations=tuple(
                MacroObservation(series_id, d, v) for d, v in values
            ),
        )

    return StaticMacroProvider(
        {
            "CPIAUCSL": monthly(
                "CPIAUCSL",
                (date(2024, 1, 1), 305.0),
                (date(2024, 7, 1), 308.0),
                (date(2025, 1, 1), 311.0),
                (date(2025, 7, 1), 313.0),
                (date(2026, 1, 1), 315.0),
            ),
            "CPILFESL": monthly(
                "CPILFESL",
                (date(2024, 1, 1), 308.0),
                (date(2024, 7, 1), 311.0),
                (date(2025, 1, 1), 314.0),
                (date(2025, 7, 1), 316.0),
                (date(2026, 1, 1), 318.0),
            ),
            "UNRATE": monthly(
                "UNRATE",
                (date(2025, 7, 1), 3.7),
                (date(2026, 1, 1), 3.7),
            ),
            "FEDFUNDS": monthly(
                "FEDFUNDS",
                (date(2025, 7, 1), 5.25),
                (date(2026, 1, 1), 5.0),
            ),
            "T10Y2Y": monthly("T10Y2Y", (date(2026, 1, 1), 0.5)),
            "INDPRO": monthly(
                "INDPRO",
                (date(2025, 1, 1), 100.0),
                (date(2025, 7, 1), 101.5),
                (date(2026, 1, 1), 103.5),
            ),
            "RSAFS": monthly(
                "RSAFS",
                (date(2025, 1, 1), 600000.0),
                (date(2025, 7, 1), 610000.0),
                (date(2026, 1, 1), 620000.0),
            ),
            "DTWEXBGS": monthly(
                "DTWEXBGS",
                (date(2025, 7, 1), 115.0),
                (date(2026, 1, 1), 113.0),
            ),
            "DEXUSEU": monthly(
                "DEXUSEU",
                (date(2025, 7, 1), 1.05),
                (date(2026, 1, 1), 1.08),
            ),
            "DEXJPUS": monthly(
                "DEXJPUS",
                (date(2025, 7, 1), 145.0),
                (date(2026, 1, 1), 142.0),
            ),
            "DEXUSUK": monthly(
                "DEXUSUK",
                (date(2025, 7, 1), 1.25),
                (date(2026, 1, 1), 1.29),
            ),
            "DEXSZUS": monthly(
                "DEXSZUS",
                (date(2025, 7, 1), 0.90),
                (date(2026, 1, 1), 0.88),
            ),
            "DEXCHUS": monthly(
                "DEXCHUS",
                (date(2025, 7, 1), 7.20),
                (date(2026, 1, 1), 7.10),
            ),
            "PMAIZMTUSDM": monthly(
                "PMAIZMTUSDM",
                (date(2025, 7, 1), 180.0),
                (date(2026, 1, 1), 185.0),
            ),
            "BAMLH0A0HYM2": monthly(
                "BAMLH0A0HYM2",
                (date(2026, 1, 1), 3.2),
            ),
        }
    )


class StaticMacroProvider:
    def __init__(self, mapping):
        self.mapping = mapping

    def series(self, series_id):
        return self.mapping[series_id]


class StaticHistoryProvider:
    def __init__(self, mapping):
        self.mapping = mapping

    def history(self, symbol, range_period="6mo", interval="1d"):
        return self.mapping[symbol.upper()]


def make_history(symbol, prices):
    today = date(2026, 5, 7)
    n = len(prices)
    return tuple(
        PricePoint(symbol.upper(), date.fromordinal(today.toordinal() - (n - 1 - i)), p)
        for i, p in enumerate(prices)
    )


class IntegrationTests(unittest.TestCase):
    def test_build_regime_report_picks_disinflation_with_calm_vix(self):
        # Calm VIX (15) and gentle SPY uptrend
        history = StaticHistoryProvider(
            {
                "^VIX": make_history("^VIX", [15.0] * 60),
                "SPY": make_history("SPY", [400.0 + i * 0.5 for i in range(252)]),
            }
        )
        report = build_regime_report(
            macro_provider=disinflation_macro(),
            history_provider=history,
        )
        self.assertEqual(report.reading.primary, "disinflation_expansion")
        self.assertEqual(report.template.regime, "disinflation_expansion")
        self.assertGreater(len(report.template.allocations), 0)


if __name__ == "__main__":
    unittest.main()
