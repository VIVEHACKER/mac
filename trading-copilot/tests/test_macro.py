from datetime import date
import unittest

from trading_copilot.macro import (
    FredSeries,
    MacroObservation,
    build_macro_dashboard,
    classify_macro_regime,
    format_macro_report,
    parse_fred_csv,
    percent_change_since_months,
)


class MacroTests(unittest.TestCase):
    def test_parse_fred_csv_skips_missing_values(self):
        csv_text = "\n".join(
            [
                "observation_date,CPIAUCSL",
                "2025-01-01,300.0",
                "2025-02-01,.",
                "2026-01-01,312.0",
            ]
        )

        series = parse_fred_csv(csv_text, "CPIAUCSL", "https://fred.test/cpi.csv")

        self.assertEqual(series.series_id, "CPIAUCSL")
        self.assertEqual(len(series.observations), 2)
        self.assertEqual(series.observations[-1].value, 312.0)
        self.assertEqual(series.source, "https://fred.test/cpi.csv")

    def test_percent_change_since_months_calculates_cpi_yoy(self):
        series = FredSeries(
            series_id="CPIAUCSL",
            name="CPI",
            source="fixture",
            observations=(
                MacroObservation("CPIAUCSL", date(2025, 1, 1), 300.0),
                MacroObservation("CPIAUCSL", date(2026, 1, 1), 312.0),
            ),
        )

        change = percent_change_since_months(series, 12)

        self.assertAlmostEqual(change, 4.0)

    def test_classify_macro_regime_flags_late_cycle_tightening(self):
        regime = classify_macro_regime(
            headline_cpi_yoy=4.2,
            core_cpi_yoy=3.9,
            headline_cpi_6m_delta=0.6,
            unemployment_6m_delta=0.2,
            fed_funds_6m_delta=0.5,
            yield_curve_spread=-0.35,
            industrial_production_yoy=-1.2,
            retail_sales_yoy=0.4,
        )

        self.assertEqual(regime.name, "Late-Cycle Tightening / Stagflation Risk")
        self.assertIn("inflation", " ".join(regime.watch_items).lower())
        self.assertIn("duration", " ".join(regime.portfolio_implications).lower())

    def test_build_macro_dashboard_and_report_formats_market_structure(self):
        provider = StaticMacroProvider(
            {
                "CPIAUCSL": monthly_series("CPIAUCSL", 300.0, 312.0),
                "CPILFESL": monthly_series("CPILFESL", 310.0, 321.0),
                "UNRATE": monthly_series("UNRATE", 3.8, 4.2),
                "FEDFUNDS": monthly_series("FEDFUNDS", 4.5, 5.0),
                "T10Y2Y": point_series("T10Y2Y", -0.35),
                "INDPRO": monthly_series("INDPRO", 105.0, 103.5),
                "RSAFS": monthly_series("RSAFS", 650000.0, 657000.0),
            }
        )

        dashboard = build_macro_dashboard(provider)
        report = format_macro_report(dashboard)

        self.assertEqual(dashboard.regime.name, "Late-Cycle Tightening / Stagflation Risk")
        self.assertIn("# Macro Cycle Dashboard", report)
        self.assertIn("Consumer Price Index", report)
        self.assertIn("10Y-2Y Treasury Spread", report)
        self.assertIn("Market Structure Read", report)
        self.assertIn("Not investment advice", report)


def monthly_series(series_id: str, previous: float, latest: float) -> FredSeries:
    return FredSeries(
        series_id=series_id,
        name=series_id,
        source=f"https://fred.test/{series_id}.csv",
        observations=(
            MacroObservation(series_id, date(2025, 1, 1), previous),
            MacroObservation(series_id, date(2025, 7, 1), previous * 0.99),
            MacroObservation(series_id, date(2026, 1, 1), latest),
        ),
    )


def point_series(series_id: str, latest: float) -> FredSeries:
    return FredSeries(
        series_id=series_id,
        name=series_id,
        source=f"https://fred.test/{series_id}.csv",
        observations=(MacroObservation(series_id, date(2026, 1, 1), latest),),
    )


class StaticMacroProvider:
    def __init__(self, series_by_id: dict[str, FredSeries]):
        self.series_by_id = series_by_id

    def series(self, series_id: str) -> FredSeries:
        return self.series_by_id[series_id]


if __name__ == "__main__":
    unittest.main()
