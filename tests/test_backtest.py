from datetime import date
import unittest

from trading_copilot.industry_rotation import PricePoint
from trading_copilot.macro import FredSeries, MacroObservation
from trading_copilot.backtest import (
    backtest_to_csv,
    format_backtest_report,
    run_backtest,
)


class StaticMacroProvider:
    def __init__(self, mapping):
        self.mapping = mapping

    def series(self, series_id: str) -> FredSeries:
        return self.mapping[series_id]


def monthly_series(series_id, points):
    return FredSeries(
        series_id=series_id,
        name=series_id,
        source=f"fixture/{series_id}",
        observations=tuple(MacroObservation(series_id, d, v) for d, v in points),
    )


def make_macro_fixture():
    """Provide enough macro history that as_of=2024-06 .. 2026-04 all resolve."""
    months = [date(2023 + (i // 12), (i % 12) + 1, 1) for i in range(36)]

    def linear(series_id, base, step):
        return monthly_series(
            series_id,
            [(d, base + step * i) for i, d in enumerate(months)],
        )

    return StaticMacroProvider(
        {
            "CPIAUCSL": linear("CPIAUCSL", 295.0, 0.5),
            "CPILFESL": linear("CPILFESL", 305.0, 0.4),
            "UNRATE": monthly_series(
                "UNRATE", [(d, 3.7 + 0.01 * i) for i, d in enumerate(months)]
            ),
            "FEDFUNDS": monthly_series(
                "FEDFUNDS",
                [(d, 5.25 - 0.02 * i) for i, d in enumerate(months)],
            ),
            "T10Y2Y": monthly_series(
                "T10Y2Y", [(d, 0.5) for d in months]
            ),
            "INDPRO": linear("INDPRO", 100.0, 0.05),
            "RSAFS": linear("RSAFS", 600000.0, 1500.0),
            "BAMLH0A0HYM2": monthly_series(
                "BAMLH0A0HYM2", [(d, 3.0) for d in months]
            ),
        }
    )


class StaticHistoryProvider:
    def __init__(self, mapping):
        self.mapping = mapping

    def history(self, symbol, range_period="6mo", interval="1d"):
        return self.mapping.get(symbol.upper(), ())


def daily_uptrend(symbol, start_date, days, daily_return=0.0008):
    points = []
    price = 100.0
    for i in range(days):
        d = date.fromordinal(start_date.toordinal() + i)
        points.append(PricePoint(symbol.upper(), d, price))
        price *= (1.0 + daily_return)
    return tuple(points)


def make_history_fixture():
    base_start = date(2023, 1, 1)
    days = 1200
    history: dict[str, tuple[PricePoint, ...]] = {}
    template_etfs = [
        "SPY", "VOO", "BIL", "SHV", "TLT", "TIP",
        "GLD", "SLV", "DBC", "XLE", "XLB", "XLF",
        "XLP", "XLU", "XLV", "XLI", "XLY", "XLC", "XLRE",
        "QQQ", "SMH", "IGV", "IWM", "EEM",
        "VTV", "SCHD", "UUP", "VIXY", "IBIT",
    ]
    drift_per_etf = {
        "SPY": 0.0006,
        "QQQ": 0.0010,
        "SMH": 0.0014,
        "GLD": 0.0004,
        "TLT": -0.0001,
        "BIL": 0.0001,
        "VIXY": -0.0006,
    }
    for etf in template_etfs:
        history[etf] = daily_uptrend(etf, base_start, days, drift_per_etf.get(etf, 0.0005))
    history["^VIX"] = tuple(
        PricePoint("^VIX", date.fromordinal(base_start.toordinal() + i), 16.0)
        for i in range(days)
    )
    return history


class BacktestTests(unittest.TestCase):
    def test_run_backtest_produces_points_and_aggregates(self):
        result = run_backtest(
            start=date(2024, 1, 1),
            end=date(2024, 12, 1),
            history_provider=StaticHistoryProvider(make_history_fixture()),
            macro_provider=make_macro_fixture(),
            holding_days=21,
        )
        self.assertGreater(len(result.points), 6)
        self.assertEqual(result.benchmark, "SPY")
        # Avg + cumulative are computable
        self.assertIsInstance(result.cumulative_portfolio, float)
        self.assertIsInstance(result.cumulative_benchmark, float)
        # Each point has finite returns and positive coverage
        for point in result.points:
            self.assertGreater(point.coverage_pct, 50.0)
            self.assertIsInstance(point.portfolio_return, float)
            self.assertIsInstance(point.benchmark_return, float)

    def test_format_backtest_report_includes_required_sections(self):
        result = run_backtest(
            start=date(2024, 6, 1),
            end=date(2024, 12, 1),
            history_provider=StaticHistoryProvider(make_history_fixture()),
            macro_provider=make_macro_fixture(),
            holding_days=21,
        )
        text = format_backtest_report(result)
        self.assertIn("# Macro Regime Backtest", text)
        self.assertIn("Aggregate Performance", text)
        self.assertIn("Regime Distribution", text)
        self.assertIn("Period Detail", text)
        self.assertIn("Caveats", text)
        self.assertIn("Not investment advice", text)

    def test_backtest_csv_includes_one_row_per_point(self):
        result = run_backtest(
            start=date(2024, 6, 1),
            end=date(2024, 12, 1),
            history_provider=StaticHistoryProvider(make_history_fixture()),
            macro_provider=make_macro_fixture(),
            holding_days=21,
        )
        csv = backtest_to_csv(result)
        rows = [line for line in csv.strip().split("\n") if line]
        self.assertEqual(len(rows), 1 + len(result.points))


if __name__ == "__main__":
    unittest.main()
