from datetime import date, datetime, timezone
import unittest

from trading_copilot.events import EventItem
from trading_copilot.fundamentals import analyze_company_facts
from trading_copilot.industry_rotation import IndustryScore
from trading_copilot.macro import FredSeries, MacroObservation, build_macro_dashboard
from trading_copilot.market_data import MarketSnapshot
from trading_copilot.ml_recommendations import (
    build_ml_recommendation,
    format_ml_recommendation_report,
)
from trading_copilot.pattern_mining import PatternResult
from trading_copilot.signals import detect_forecast_signals
from trading_copilot.metrics import build_technical_profile


class MLRecommendationTests(unittest.TestCase):
    def test_bullish_inputs_produce_guarded_buy_research_report(self):
        snapshot = MarketSnapshot(
            ticker="MSFT",
            price=421.50,
            previous_close=420.0,
            change=1.5,
            change_percent=0.36,
            currency="USD",
            as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
            source="quote fixture",
        )
        events = (
            EventItem(
                ticker="MSFT",
                source_type="NEWS",
                title="Microsoft wins multi-year cloud contract with Contoso",
                published_at=date(2026, 5, 6),
                source="news fixture",
                form="NEWS",
            ),
        )
        recommendation = build_ml_recommendation(
            ticker="MSFT",
            snapshot=snapshot,
            technical=build_technical_profile(uptrend(), benchmark_closes=benchmark()),
            macro_dashboard=build_macro_dashboard(StaticMacroProvider()),
            fundamentals=strong_fundamentals(),
            signals=detect_forecast_signals(events),
            pattern_results=(perfect_pattern("MSFT"),),
            sector_score=strong_sector_score(),
            target_price=520.0,
            stop_price=390.0,
            horizon="3-6 months",
            context="Only after earnings reaction stabilizes.",
            risk_budget_pct=2.0,
            max_position_pct=12.0,
        )
        report = format_ml_recommendation_report(recommendation)

        self.assertEqual(recommendation.action, "Consider Buy")
        self.assertGreaterEqual(recommendation.composite_score, 70.0)
        self.assertGreater(recommendation.suggested_weight_pct, 0.0)
        self.assertAlmostEqual(sum(factor.weight for factor in recommendation.factors), 1.0)
        self.assertIn("# ML + AI Recommendation - MSFT", report)
        self.assertIn("Alpha Score", report)
        self.assertIn("Sector Fit", report)
        self.assertIn("Data Quality", report)
        self.assertIn("Counterargument", report)
        self.assertIn("AI Review Packet", report)
        self.assertIn("Human Approval Gate", report)
        self.assertIn("No order routing", report)
        self.assertIn("not investment advice", report.lower())

    def test_negative_signals_and_high_risk_reduce_action(self):
        snapshot = MarketSnapshot(
            ticker="RISK",
            price=100.0,
            previous_close=110.0,
            change=-10.0,
            change_percent=-9.09,
            currency="USD",
            as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
            source="quote fixture",
        )
        events = (
            EventItem(
                ticker="RISK",
                source_type="NEWS",
                title="Risk Co cuts guidance as weak demand hurts margins",
                published_at=date(2026, 5, 6),
                source="news fixture",
                form="NEWS",
            ),
        )
        recommendation = build_ml_recommendation(
            ticker="RISK",
            snapshot=snapshot,
            technical=build_technical_profile(downtrend(), benchmark_closes=benchmark()),
            macro_dashboard=build_macro_dashboard(StaticMacroProvider()),
            fundamentals=weak_fundamentals(),
            signals=detect_forecast_signals(events),
            pattern_results=(),
            sector_score=weak_sector_score(),
            target_price=105.0,
            stop_price=92.0,
            horizon="swing",
            context="",
            risk_budget_pct=2.0,
            max_position_pct=12.0,
        )

        self.assertIn(recommendation.action, {"Wait", "Avoid Add"})
        self.assertGreaterEqual(recommendation.risk_score, 55.0)
        self.assertTrue(any("Negative forecast signal" in risk for risk in recommendation.risks))

    def test_data_gaps_cap_action_and_confidence(self):
        snapshot = MarketSnapshot(
            ticker="GAP",
            price=100.0,
            previous_close=99.0,
            change=1.0,
            change_percent=1.01,
            currency="USD",
            as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
            source="quote fixture",
        )
        recommendation = build_ml_recommendation(
            ticker="GAP",
            snapshot=snapshot,
            technical=build_technical_profile(uptrend(), benchmark_closes=benchmark()),
            macro_dashboard=build_macro_dashboard(StaticMacroProvider()),
            fundamentals=None,
            signals=(),
            pattern_results=(perfect_pattern("GAP"),),
            sector_score=strong_sector_score(),
            target_price=140.0,
            stop_price=95.0,
            horizon="3-6 months",
            context="",
            risk_budget_pct=2.0,
            max_position_pct=12.0,
            data_gaps=("fundamentals unavailable", "quoteSummary unavailable"),
        )

        self.assertNotEqual(recommendation.action, "Consider Buy")
        self.assertEqual(recommendation.confidence, "low")
        self.assertLess(recommendation.data_quality_score, 70.0)

    def test_pattern_scoring_penalizes_small_perfect_samples_with_bad_drawdown(self):
        snapshot = MarketSnapshot(
            ticker="THIN",
            price=100.0,
            previous_close=99.0,
            change=1.0,
            change_percent=1.01,
            currency="USD",
            as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
            source="quote fixture",
        )
        recommendation = build_ml_recommendation(
            ticker="THIN",
            snapshot=snapshot,
            technical=build_technical_profile(uptrend(), benchmark_closes=benchmark()),
            macro_dashboard=build_macro_dashboard(StaticMacroProvider()),
            fundamentals=strong_fundamentals(),
            signals=(),
            pattern_results=(thin_pattern("THIN"),),
            sector_score=strong_sector_score(),
            target_price=130.0,
            stop_price=95.0,
            horizon="3-6 months",
            context="",
            risk_budget_pct=2.0,
            max_position_pct=12.0,
        )
        pattern = next(factor for factor in recommendation.factors if factor.name == "Historical Pattern")

        self.assertLess(pattern.score, 65.0)
        self.assertIn("quality penalty", pattern.read.lower())


def perfect_pattern(ticker: str) -> PatternResult:
    return PatternResult(
        condition="Dollar 6M Change <= -5",
        series_id="DTWEXBGS_PCT_6M",
        asset=ticker,
        horizon_days=63,
        outcome_label="Forward return > 0.00%",
        samples=13,
        wins=13,
        win_rate=100.0,
        wilson_lower_95=77.19,
        average_return=6.0,
        median_return=5.5,
        best_return=12.0,
        worst_return=1.4,
        worst_drawdown=-3.5,
        historical_perfect=True,
        read="Historical perfect sample: 13/13 wins. This is not a guarantee.",
        sources=("pattern fixture",),
        trials=(),
    )


def thin_pattern(ticker: str) -> PatternResult:
    return PatternResult(
        condition="VIX >= 50",
        series_id="VIXCLS",
        asset=ticker,
        horizon_days=63,
        outcome_label="Forward return > 0.00%",
        samples=3,
        wins=3,
        win_rate=100.0,
        wilson_lower_95=43.85,
        average_return=5.0,
        median_return=4.0,
        best_return=12.0,
        worst_return=0.5,
        worst_drawdown=-38.0,
        historical_perfect=True,
        read="Historical perfect sample: 3/3 wins. This is not a guarantee.",
        sources=("pattern fixture",),
        trials=(),
    )


def strong_sector_score() -> IndustryScore:
    return IndustryScore(
        symbol="IGV",
        name="Software",
        group="Technology",
        theme="offensive",
        one_month=6.0,
        three_month=18.0,
        six_month=35.0,
        relative_one_month=4.0,
        relative_three_month=12.0,
        relative_six_month=25.0,
        acceleration=2.0,
        leadership_score=16.0,
        next_leader_score=7.0,
        read="Leading industry proxy.",
        source="sector fixture",
    )


def weak_sector_score() -> IndustryScore:
    return IndustryScore(
        symbol="XRT",
        name="Retail",
        group="Consumer",
        theme="cyclical",
        one_month=-6.0,
        three_month=-14.0,
        six_month=-20.0,
        relative_one_month=-7.0,
        relative_three_month=-18.0,
        relative_six_month=-25.0,
        acceleration=-1.3,
        leadership_score=-18.0,
        next_leader_score=-8.0,
        read="Lagging industry proxy.",
        source="sector fixture",
    )


def strong_fundamentals():
    return analyze_company_facts(
        "MSFT",
        {
            "facts": {
                "us-gaap": {
                    "Revenues": fact(200000.0, 240000.0),
                    "NetIncomeLoss": fact_latest(72000.0),
                    "OperatingIncomeLoss": fact_latest(86000.0),
                    "Assets": fact_latest(400000.0),
                    "Liabilities": fact_latest(110000.0),
                    "StockholdersEquity": fact_latest(250000.0),
                    "NetCashProvidedByUsedInOperatingActivities": fact_latest(90000.0),
                    "PaymentsToAcquirePropertyPlantAndEquipment": fact_latest(22000.0),
                    "EarningsPerShareDiluted": eps_fact(8.0),
                },
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {"shares": [{"end": "2026-06-30", "val": 7500.0}]}
                    }
                },
            }
        },
        source="fundamentals fixture",
    )


def weak_fundamentals():
    return analyze_company_facts(
        "RISK",
        {
            "facts": {
                "us-gaap": {
                    "Revenues": fact(240000.0, 200000.0),
                    "NetIncomeLoss": fact_latest(-10000.0),
                    "Assets": fact_latest(200000.0),
                    "Liabilities": fact_latest(260000.0),
                    "StockholdersEquity": fact_latest(50000.0),
                    "NetCashProvidedByUsedInOperatingActivities": fact_latest(-1000.0),
                    "PaymentsToAcquirePropertyPlantAndEquipment": fact_latest(5000.0),
                }
            }
        },
        source="fundamentals fixture",
    )


def fact(previous: float, latest: float):
    return {
        "units": {
            "USD": [
                {"form": "10-K", "fy": 2025, "fp": "FY", "filed": "2025-07-30", "end": "2025-06-30", "val": previous},
                {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": latest},
            ]
        }
    }


def fact_latest(value: float):
    return {
        "units": {
            "USD": [
                {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": value},
            ]
        }
    }


def eps_fact(value: float):
    return {
        "units": {
            "USD/shares": [
                {"form": "10-K", "fy": 2026, "fp": "FY", "filed": "2026-07-30", "end": "2026-06-30", "val": value},
            ]
        }
    }


def uptrend() -> tuple[float, ...]:
    return tuple(100.0 * (1.0012 ** index) for index in range(260))


def downtrend() -> tuple[float, ...]:
    return tuple(140.0 * (0.9985 ** index) for index in range(260))


def benchmark() -> tuple[float, ...]:
    return tuple(100.0 * (1.0004 ** index) for index in range(260))


class StaticMacroProvider:
    def series(self, series_id: str) -> FredSeries:
        fixtures = {
            "CPIAUCSL": macro_series("CPIAUCSL", 300.0, 306.0),
            "CPILFESL": macro_series("CPILFESL", 310.0, 316.0),
            "UNRATE": macro_series("UNRATE", 3.8, 3.7),
            "FEDFUNDS": macro_series("FEDFUNDS", 4.5, 4.0),
            "T10Y2Y": point_macro_series("T10Y2Y", 0.6),
            "INDPRO": macro_series("INDPRO", 105.0, 108.0),
            "RSAFS": macro_series("RSAFS", 650000.0, 690000.0),
            "DTWEXBGS": macro_series("DTWEXBGS", 115.0, 113.0),
            "DEXUSEU": macro_series("DEXUSEU", 1.05, 1.08),
            "DEXJPUS": macro_series("DEXJPUS", 145.0, 142.0),
            "DEXUSUK": macro_series("DEXUSUK", 1.25, 1.29),
            "DEXSZUS": macro_series("DEXSZUS", 0.90, 0.88),
            "DEXCHUS": macro_series("DEXCHUS", 7.20, 7.10),
            "PMAIZMTUSDM": macro_series("PMAIZMTUSDM", 180.0, 185.0),
        }
        return fixtures[series_id]


def macro_series(series_id: str, previous: float, latest: float) -> FredSeries:
    return FredSeries(
        series_id=series_id,
        name=series_id,
        source=f"macro fixture {series_id}",
        observations=(
            MacroObservation(series_id, date(2025, 1, 1), previous),
            MacroObservation(series_id, date(2025, 7, 1), previous * 0.99),
            MacroObservation(series_id, date(2026, 1, 1), latest),
        ),
    )


def point_macro_series(series_id: str, latest: float) -> FredSeries:
    return FredSeries(
        series_id=series_id,
        name=series_id,
        source=f"macro fixture {series_id}",
        observations=(MacroObservation(series_id, date(2026, 1, 1), latest),),
    )


if __name__ == "__main__":
    unittest.main()
