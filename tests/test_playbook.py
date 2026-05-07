from datetime import date, datetime, timezone
import unittest

from trading_copilot.industry_rotation import (
    DEFAULT_INDUSTRY_PROXIES,
    PricePoint,
)
from trading_copilot.macro import (
    FredSeries,
    MacroObservation,
)
from trading_copilot.market_data import MarketSnapshot
from trading_copilot.fundamentals import analyze_company_facts
from trading_copilot.playbook import (
    FundamentalsScore,
    PlaybookBuilder,
    composite_score,
    format_playbook_report,
    score_cycle,
    score_technical,
)


FUNDAMENTALS_FIXTURE = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {"form": "10-K", "fy": 2025, "fp": "FY", "filed": "2025-07-30", "end": "2025-06-30", "val": 200000.0},
                        {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 240000.0},
                    ]
                }
            },
            "NetIncomeLoss": {
                "units": {
                    "USD": [
                        {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 60000.0},
                    ]
                }
            },
            "OperatingIncomeLoss": {
                "units": {
                    "USD": [
                        {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 72000.0},
                    ]
                }
            },
            "Assets": {
                "units": {
                    "USD": [
                        {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 400000.0},
                    ]
                }
            },
            "Liabilities": {
                "units": {
                    "USD": [
                        {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 100000.0},
                    ]
                }
            },
            "StockholdersEquity": {
                "units": {
                    "USD": [
                        {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 240000.0},
                    ]
                }
            },
            "EarningsPerShareDiluted": {
                "units": {
                    "USD/shares": [
                        {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": 8.0},
                    ]
                }
            },
        }
    }
}


class StaticFundamentalsProvider:
    def analysis(self, ticker: str):
        return analyze_company_facts(ticker, FUNDAMENTALS_FIXTURE, source="fixture")


class FailingFundamentalsProvider:
    def analysis(self, ticker: str):
        raise RuntimeError("simulated SEC fetch failure")


def make_history(symbol: str, *, drift: float, days: int = 260) -> tuple[PricePoint, ...]:
    points = []
    today = date(2026, 5, 7)
    base = 100.0
    for i in range(days):
        price = base * ((1.0 + drift) ** i)
        offset = days - 1 - i
        points.append(PricePoint(symbol.upper(), date.fromordinal(today.toordinal() - offset), price))
    return tuple(points)


class StaticHistoryProvider:
    def __init__(self, mapping: dict[str, tuple[PricePoint, ...]]):
        self.mapping = mapping

    def history(
        self,
        symbol: str,
        range_period: str = "6mo",
        interval: str = "1d",
    ) -> tuple[PricePoint, ...]:
        normalized = symbol.upper()
        if normalized in self.mapping:
            return self.mapping[normalized]
        # Provide a flat default for unmapped sectors so analyze_industries doesn't blow up
        return make_history(normalized, drift=0.0)


class StaticMacroProvider:
    def __init__(self, mapping: dict[str, FredSeries]):
        self.mapping = mapping

    def series(self, series_id: str) -> FredSeries:
        return self.mapping[series_id]


class StaticMarketData:
    def __init__(self, last_close: float):
        self.last_close = last_close

    def snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(
            ticker=ticker.upper(),
            price=self.last_close,
            previous_close=self.last_close * 0.995,
            change=self.last_close * 0.005,
            change_percent=0.5,
            currency="USD",
            as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
            source="fixture",
        )


def disinflationary_macro() -> StaticMacroProvider:
    """Macro fixture that triggers Disinflationary Expansion regime."""
    def monthly(series_id: str, previous: float, latest: float) -> FredSeries:
        return FredSeries(
            series_id=series_id,
            name=series_id,
            source=f"fixture/{series_id}",
            observations=(
                MacroObservation(series_id, date(2025, 1, 1), previous),
                MacroObservation(series_id, date(2025, 7, 1), previous * 1.005),
                MacroObservation(series_id, date(2026, 1, 1), latest),
            ),
        )

    def point(series_id: str, latest: float) -> FredSeries:
        return FredSeries(
            series_id=series_id,
            name=series_id,
            source=f"fixture/{series_id}",
            observations=(MacroObservation(series_id, date(2026, 1, 1), latest),),
        )

    return StaticMacroProvider(
        {
            # CPI cooling: 6m delta negative, latest YoY low
            "CPIAUCSL": monthly("CPIAUCSL", 305.0, 308.0),
            "CPILFESL": monthly("CPILFESL", 310.0, 312.0),
            "UNRATE": monthly("UNRATE", 3.7, 3.6),
            "FEDFUNDS": monthly("FEDFUNDS", 5.25, 5.0),
            "T10Y2Y": point("T10Y2Y", 0.6),
            "INDPRO": monthly("INDPRO", 100.0, 102.5),
            "RSAFS": monthly("RSAFS", 600000.0, 618000.0),
        }
    )


class PlaybookTests(unittest.TestCase):
    def test_score_cycle_assigns_high_value_to_disinflationary(self):
        from trading_copilot.macro import MacroDashboard, MacroRegime

        regime = MacroRegime(
            name="Disinflationary Expansion",
            summary="",
            watch_items=(),
            portfolio_implications=(),
        )
        dashboard = MacroDashboard(
            as_of=date(2026, 1, 1),
            regime=regime,
            inflation=(),
            labor_policy=(),
            growth_cycle=(),
            sources=(),
        )
        cycle = score_cycle(dashboard)
        self.assertGreaterEqual(cycle.value, 80.0)
        self.assertTrue(cycle.is_risk_on)

    def test_score_cycle_assigns_low_value_to_recession(self):
        from trading_copilot.macro import MacroDashboard, MacroRegime

        regime = MacroRegime(
            name="Slowdown / Recession Risk",
            summary="",
            watch_items=(),
            portfolio_implications=(),
        )
        dashboard = MacroDashboard(
            as_of=date(2026, 1, 1),
            regime=regime,
            inflation=(),
            labor_policy=(),
            growth_cycle=(),
            sources=(),
        )
        cycle = score_cycle(dashboard)
        self.assertLessEqual(cycle.value, 30.0)
        self.assertTrue(cycle.is_risk_off)

    def test_score_technical_high_for_strong_momentum(self):
        from trading_copilot.metrics import build_technical_profile

        # Strong uptrend
        closes = tuple(100.0 * (1.005 ** i) for i in range(260))
        benchmark = tuple(100.0 * (1.001 ** i) for i in range(260))
        profile = build_technical_profile(closes, benchmark)
        result = score_technical(profile)
        self.assertGreaterEqual(result.composite, 70.0)

    def test_score_technical_low_for_deep_drawdown(self):
        from trading_copilot.metrics import build_technical_profile

        # Short up phase then long down phase so 12-1m, drawdown, and distance-from-high are all bad
        rising = [100.0 * (1.005 ** i) for i in range(50)]
        falling = [rising[-1] * (0.985 ** i) for i in range(210)]
        closes = tuple(rising + falling)
        benchmark = tuple(100.0 * (1.001 ** i) for i in range(260))
        profile = build_technical_profile(closes, benchmark)
        result = score_technical(profile)
        self.assertLess(result.composite, 35.0)

    def test_composite_weights_sum_correctly(self):
        from trading_copilot.playbook import CycleScore, SectorScore, TechnicalScore

        cycle = CycleScore("Disinflationary Expansion", 80.0, True, False)
        sector = SectorScore("Tech (XLK)", 5.0, 4.0, 6.0, True, False)
        technical = TechnicalScore(80.0, 80.0, 80.0, 80.0)
        composite = composite_score(cycle, sector, technical)
        # 0.30*80 + 0.30*sector_value + 0.40*80
        self.assertGreater(composite.overall, 70.0)
        self.assertLess(composite.overall, 100.0)

    def test_playbook_builds_full_report_with_sizing(self):
        history = StaticHistoryProvider(
            {
                "MSFT": make_history("MSFT", drift=0.003),
                "SPY": make_history("SPY", drift=0.001),
            }
        )
        # Pre-populate sector ETFs so analyze_industries doesn't fail
        for proxy in DEFAULT_INDUSTRY_PROXIES:
            history.mapping.setdefault(proxy.symbol.upper(), make_history(proxy.symbol, drift=0.0005))

        builder = PlaybookBuilder(
            market_data=StaticMarketData(last_close=420.0),
            history_provider=history,
            macro_provider=disinflationary_macro(),
        )
        playbook = builder.build(
            "MSFT",
            aum=100_000,
            target_vol=0.35,
            kelly_multiplier=0.5,
            max_position_pct=0.25,
            max_risk_pct_of_aum=0.02,
            win_probability=0.6,
            upside_pct=0.5,
            downside_pct=0.2,
        )

        self.assertEqual(playbook.ticker, "MSFT")
        self.assertIsNotNone(playbook.sizing)
        self.assertGreater(playbook.sizing.shares, 0)
        self.assertLessEqual(playbook.sizing.risk_pct_of_aum, 2.0 + 0.001)

        report = format_playbook_report(playbook)
        self.assertIn("# Buy-Now Playbook - MSFT", report)
        self.assertIn("Composite Score:", report)
        self.assertIn("Position Sizing", report)
        self.assertIn("Sector Context", report)
        self.assertIn("Not investment advice", report)

    def test_playbook_includes_fundamentals_when_provider_supplied(self):
        history = StaticHistoryProvider(
            {
                "MSFT": make_history("MSFT", drift=0.003),
                "SPY": make_history("SPY", drift=0.001),
            }
        )
        for proxy in DEFAULT_INDUSTRY_PROXIES:
            history.mapping.setdefault(proxy.symbol.upper(), make_history(proxy.symbol, drift=0.0005))

        builder = PlaybookBuilder(
            market_data=StaticMarketData(last_close=80.0),  # P/E = 80/8 = 10 -> cheap
            history_provider=history,
            macro_provider=disinflationary_macro(),
            fundamentals_provider=StaticFundamentalsProvider(),
        )
        playbook = builder.build("MSFT", aum=100_000)

        self.assertIsNotNone(playbook.fundamentals)
        self.assertIsNotNone(playbook.fundamentals_score)
        self.assertIsNotNone(playbook.composite.quality)
        self.assertIsNotNone(playbook.composite.value)
        # Quality fixture: ROE=60000/240000=25%, op margin 30%, growth 20%, ROA 15%, L/E 0.42 -> high
        self.assertGreaterEqual(playbook.fundamentals_score.quality, 80.0)
        # Value fixture at price 80: P/E = 10 -> 50/50, PEG = 10/20 = 0.5 -> 50/50 -> 100/100
        self.assertGreaterEqual(playbook.fundamentals_score.value, 90.0)

        report = format_playbook_report(playbook)
        self.assertIn("Quality Score:", report)
        self.assertIn("Value Score:", report)
        self.assertIn("Trailing P/E", report)

    def test_playbook_falls_back_when_fundamentals_provider_fails(self):
        history = StaticHistoryProvider(
            {
                "MSFT": make_history("MSFT", drift=0.003),
                "SPY": make_history("SPY", drift=0.001),
            }
        )
        for proxy in DEFAULT_INDUSTRY_PROXIES:
            history.mapping.setdefault(proxy.symbol.upper(), make_history(proxy.symbol, drift=0.0005))

        builder = PlaybookBuilder(
            market_data=StaticMarketData(last_close=420.0),
            history_provider=history,
            macro_provider=disinflationary_macro(),
            fundamentals_provider=FailingFundamentalsProvider(),
        )
        playbook = builder.build("MSFT")

        self.assertIsNone(playbook.fundamentals)
        self.assertIsNone(playbook.fundamentals_score)
        self.assertIsNone(playbook.composite.quality)
        self.assertIsNone(playbook.composite.value)
        self.assertIsNotNone(playbook.fundamentals_error)
        self.assertIn("simulated", playbook.fundamentals_error)


if __name__ == "__main__":
    unittest.main()
