from pathlib import Path
from datetime import date, datetime, timedelta, timezone
import tempfile
import unittest

from trading_copilot.market_data import MarketSnapshot
from trading_copilot.events import EventItem
from trading_copilot.earnings_calendar import EarningsEvent
from trading_copilot.economic_calendar import EconomicEvent
from trading_copilot.fundamentals import analyze_company_facts
from trading_copilot.industry_rotation import PricePoint
from trading_copilot.macro import FredSeries, MacroObservation
from trading_copilot.news_monitor import FastNewsItem
from trading_copilot.universe import UniverseMember
from trading_copilot.skill_registry import SkillRegistry
from trading_copilot.storage import TradingStore
from trading_copilot.workflows import TradingWorkflows


ROOT = Path(__file__).resolve().parents[2]
FINANCIAL_SERVICES = ROOT / "financial-services"


class TradingWorkflowTests(unittest.TestCase):
    def test_pretrade_report_has_guardrails_sources_and_no_trade_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            store.upsert_thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth and operating leverage can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            )

            workflows = TradingWorkflows(SkillRegistry(FINANCIAL_SERVICES), store)
            report = workflows.pretrade_report(
                ticker="MSFT",
                side="buy",
                horizon="swing",
                risk_budget="1% portfolio risk",
                user_context="Entering only after earnings reaction stabilizes.",
            )

            self.assertIn("Human Approval Gate", report)
            self.assertIn("Not investment advice", report)
            self.assertIn("Source Skills", report)
            self.assertIn("thesis-tracker", report)
            self.assertIn("No order should be placed by this tool", report)
            self.assertNotIn("Recommendation: Buy", report)

    def test_morning_brief_uses_watchlist_when_tickers_not_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            store.add_watchlist_item("AAPL", "mega-cap quality check")
            store.add_watchlist_item("TSLA", "event risk watch")

            workflows = TradingWorkflows(SkillRegistry(FINANCIAL_SERVICES), store)
            report = workflows.morning_brief([])

            self.assertIn("AAPL", report)
            self.assertIn("TSLA", report)
            self.assertIn("Catalysts", report)
            self.assertIn("No trade recommendations", report)

    def test_morning_brief_can_include_market_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()

            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                market_data=StaticMarketData(),
            )
            report = workflows.morning_brief(["MSFT"], include_market_data=True)

            self.assertIn("## Market Snapshot", report)
            self.assertIn("MSFT: 421.50 USD", report)
            self.assertIn("Source:", report)
            self.assertIn("Yahoo test fixture", report)

    def test_recommendation_report_uses_stored_thesis_and_market_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            store.upsert_thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            )

            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                market_data=StaticMarketData(),
            )
            report = workflows.recommendation_report(
                ticker="MSFT",
                target_price=520.0,
                stop_price=390.0,
                horizon="swing",
                context="Only after earnings reaction stabilizes.",
            )

            self.assertIn("Recommendation: Consider Buy", report)
            self.assertIn("Stored Thesis", report)
            self.assertIn("No order routing", report)

    def test_recommendation_report_can_include_sec_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            store.upsert_thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            )

            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                market_data=StaticMarketData(),
                events=StaticEvents(),
            )
            report = workflows.recommendation_report(
                ticker="MSFT",
                target_price=520.0,
                stop_price=390.0,
                horizon="swing",
                context="",
                include_sec_events=True,
            )

            self.assertIn("Recent SEC filing risk", report)
            self.assertIn("8-K", report)
            self.assertIn("SEC test fixture", report)

    def test_recommendation_report_can_include_news_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            store.upsert_thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            )

            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                market_data=StaticMarketData(),
                news=StaticNews(),
            )
            report = workflows.recommendation_report(
                ticker="MSFT",
                target_price=520.0,
                stop_price=390.0,
                horizon="swing",
                context="",
                include_news=True,
            )

            self.assertIn("Recent Events", report)
            self.assertIn("Microsoft shares rise", report)
            self.assertIn("News test fixture", report)

    def test_news_report_formats_recent_headlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                news=StaticNews(),
            )

            report = workflows.news_report("MSFT", limit=1)

            self.assertIn("# Recent News - MSFT", report)
            self.assertIn("Microsoft shares rise", report)
            self.assertIn("No investment recommendation", report)

    def test_fast_news_report_is_available_from_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                fast_news_providers=(StaticFastNews(),),
            )

            report = workflows.fast_news_report("MSFT", limit=5)

            self.assertIn("# Fast News Monitor - MSFT", report)
            self.assertIn("Microsoft wins new AI contract", report)
            self.assertIn("Not investment advice", report)

    def test_earnings_calendar_report_is_available_from_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                earnings_calendar=StaticEarningsCalendar(),
            )

            report = workflows.earnings_calendar_report("MSFT")

            self.assertIn("# Earnings Calendar - MSFT", report)
            self.assertIn("2026-07-23", report)
            self.assertIn("EPS Estimate", report)

    def test_fundamentals_report_is_available_from_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                fundamentals=StaticFundamentals(),
            )

            report = workflows.fundamentals_report("MSFT")

            self.assertIn("# Fundamentals Snapshot - MSFT", report)
            self.assertIn("Revenue Growth", report)
            self.assertIn("Free Cash Flow", report)

    def test_signals_report_combines_news_and_sec_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                events=StaticEvents(),
                news=StaticSignalNews(),
            )

            report = workflows.signals_report("MSFT", event_limit=1, news_limit=1)

            self.assertIn("# Earnings Forecast Signals - MSFT", report)
            self.assertIn("Positive Catalysts", report)
            self.assertIn("multi-year cloud contract", report)
            self.assertIn("Watch Items", report)

    def test_recommendation_report_can_include_forecast_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            store.upsert_thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            )

            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                market_data=StaticMarketData(),
                events=StaticEvents(),
                news=StaticSignalNews(),
            )
            report = workflows.recommendation_report(
                ticker="MSFT",
                target_price=520.0,
                stop_price=390.0,
                horizon="swing",
                context="",
                include_news=True,
                include_signals=True,
            )

            self.assertIn("Positive forecast signal", report)
            self.assertIn("Forecast Signals", report)

    def test_ml_recommendation_report_combines_model_factors_and_guardrails(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()

            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                market_data=StaticMarketData(),
                events=StaticEvents(),
                news=StaticSignalNews(),
                macro=StaticMacroProvider(),
                industry_history=StaticIndustryHistory(),
                fundamentals=StaticFundamentals(),
            )
            report = workflows.ml_recommendation_report(
                ticker="MSFT",
                target_price=520.0,
                stop_price=390.0,
                horizon="3-6 months",
                context="Only after earnings reaction stabilizes.",
                include_news=True,
                include_signals=True,
                pattern_horizon=2,
                min_pattern_samples=1,
            )

            self.assertIn("# ML + AI Recommendation - MSFT", report)
            self.assertIn("Composite Score", report)
            self.assertIn("Alpha Score", report)
            self.assertIn("Factor Breakdown", report)
            self.assertIn("AI Review Packet", report)
            self.assertIn("Human Approval Gate", report)
            self.assertIn("No order routing", report)

    def test_screen_all_report_ranks_universe_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                market_data=StaticMarketData(),
                news=StaticScreenNews(),
                universe=StaticUniverse(),
            )

            report = workflows.screen_all_report(
                market="us",
                max_tickers=2,
                limit=2,
                include_news=True,
                include_sec_events=False,
                include_etfs=False,
                include_spacs=False,
            )

            self.assertIn("# US Market Screen", report)
            self.assertIn("GOOD", report)
            self.assertIn("BAD", report)
            self.assertLess(report.index("GOOD"), report.index("BAD"))

    def test_aggressive_portfolio_report_is_available_from_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                market_data=StaticMarketData(),
            )

            report = workflows.aggressive_portfolio_report(
                target_annual_return=100,
                single_stock_pool=("MSFT", "GOOD", "BAD", "TSLA"),
            )

            self.assertIn("Aggressive 100% Target Portfolio Draft", report)
            self.assertIn("Single Stocks: 3", report)
            self.assertIn("not guaranteed", report.lower())

    def test_macro_report_is_available_from_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                macro=StaticMacroProvider(),
                industry_history=StaticIndustryHistory(),
            )

            report = workflows.macro_report()

            self.assertIn("# Macro Cycle Dashboard", report)
            self.assertIn("Market Structure Read", report)
            self.assertIn("Consumer Price Index", report)
            self.assertIn("Corn Futures Proxy", report)
            self.assertIn("Not investment advice", report)

    def test_economic_calendar_report_is_available_from_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                economic_calendar_providers=(StaticEconomicCalendar(),),
            )

            report = workflows.economic_calendar_report(days=60, start=date(2026, 5, 7))

            self.assertIn("# Economic Release Calendar", report)
            self.assertIn("Employment Situation", report)
            self.assertIn("FOMC Rate Decision", report)
            self.assertIn("Not investment advice", report)

    def test_industry_leadership_report_is_available_from_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                industry_history=StaticIndustryHistory(),
            )

            report = workflows.industry_leadership_report(current_limit=1, next_limit=1)
            csv_text = workflows.industry_leadership_csv()

            self.assertIn("# Industry Leadership Radar", report)
            self.assertIn("Current Leaders", report)
            self.assertIn("Next Leader Candidates", report)
            self.assertIn("SMH", report)
            self.assertIn("COPX", report)
            self.assertIn("symbol,name,group,theme", csv_text)

    def test_pattern_mining_report_is_available_from_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                macro=StaticMacroProvider(),
                industry_history=StaticIndustryHistory(),
            )

            report = workflows.patterns_report(assets=("SPY",), horizons=(2,), min_samples=1)
            csv_text = workflows.patterns_csv(assets=("SPY",), horizons=(2,), min_samples=1)

            self.assertIn("# Historical Pattern Mining", report)
            self.assertIn("Multiple-testing", report)
            self.assertIn("Not investment advice", report)
            self.assertIn("condition,asset,horizon_days", csv_text)

    def test_events_report_formats_recent_sec_filings(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                events=StaticEvents(),
            )

            report = workflows.events_report("MSFT", limit=1)

            self.assertIn("# Recent SEC Events - MSFT", report)
            self.assertIn("8-K", report)
            self.assertIn("No investment recommendation", report)


class StaticMarketData:
    def snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(
            ticker=ticker.upper(),
            price=421.5,
            previous_close=420.0,
            change=1.5,
            change_percent=0.3571428571,
            currency="USD",
            as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
            source="Yahoo test fixture",
        )


class StaticEvents:
    def recent_events(self, ticker: str, limit: int = 5) -> tuple[EventItem, ...]:
        return (
            EventItem(
                ticker=ticker.upper(),
                source_type="SEC",
                title="8-K filed 2026-05-06 - CURRENT REPORT",
                published_at=datetime(2026, 5, 6, tzinfo=timezone.utc).date(),
                source="SEC test fixture",
                form="8-K",
            ),
        )[:limit]


class StaticNews:
    def recent_events(self, ticker: str, limit: int = 5) -> tuple[EventItem, ...]:
        return (
            EventItem(
                ticker=ticker.upper(),
                source_type="NEWS",
                title="Microsoft shares rise after cloud update",
                published_at=datetime(2026, 5, 6, tzinfo=timezone.utc).date(),
                source="News test fixture",
                form="NEWS",
            ),
        )[:limit]


class StaticSignalNews:
    def recent_events(self, ticker: str, limit: int = 5) -> tuple[EventItem, ...]:
        return (
            EventItem(
                ticker=ticker.upper(),
                source_type="NEWS",
                title="Microsoft wins multi-year cloud contract with Contoso",
                published_at=datetime(2026, 5, 6, tzinfo=timezone.utc).date(),
                source="Signal news fixture",
                form="NEWS",
            ),
        )[:limit]


class StaticFastNews:
    def recent_news(self, ticker: str, limit: int = 10) -> tuple[FastNewsItem, ...]:
        return (
            FastNewsItem(
                ticker=ticker.upper(),
                source_type="WIRE",
                title="Microsoft wins new AI contract",
                published_at=datetime(2026, 5, 7, 12, 30, tzinfo=timezone.utc),
                source="https://example.com/msft-contract",
                provider="fixture",
                sentiment="positive",
            ),
        )[:limit]


class StaticEarningsCalendar:
    def earnings(self, ticker: str, horizon: str = "3month") -> tuple[EarningsEvent, ...]:
        return (
            EarningsEvent(
                ticker=ticker.upper(),
                company_name="Microsoft Corporation",
                report_date=date(2026, 7, 23),
                fiscal_date_ending=date(2026, 6, 30),
                estimate=3.25,
                currency="USD",
                source="Alpha fixture",
            ),
        )


class StaticFundamentals:
    def analysis(self, ticker: str):
        return analyze_company_facts(
            ticker,
            {
                "facts": {
                    "us-gaap": {
                        "Revenues": fact(
                            [
                                {"form": "10-K", "filed": "2025-07-30", "end": "2025-06-30", "val": 200000},
                                {"form": "10-K", "filed": "2026-07-30", "end": "2026-06-30", "val": 220000},
                            ]
                        ),
                        "NetIncomeLoss": fact(
                            [{"form": "10-K", "filed": "2026-07-30", "end": "2026-06-30", "val": 88000}]
                        ),
                        "Assets": fact(
                            [{"form": "10-K", "filed": "2026-07-30", "end": "2026-06-30", "val": 500000}]
                        ),
                        "Liabilities": fact(
                            [{"form": "10-K", "filed": "2026-07-30", "end": "2026-06-30", "val": 160000}]
                        ),
                        "StockholdersEquity": fact(
                            [{"form": "10-K", "filed": "2026-07-30", "end": "2026-06-30", "val": 320000}]
                        ),
                        "NetCashProvidedByUsedInOperatingActivities": fact(
                            [{"form": "10-K", "filed": "2026-07-30", "end": "2026-06-30", "val": 70000}]
                        ),
                        "PaymentsToAcquirePropertyPlantAndEquipment": fact(
                            [{"form": "10-K", "filed": "2026-07-30", "end": "2026-06-30", "val": 15000}]
                        ),
                    }
                }
            },
            source="SEC fixture",
        )


def fact(values):
    return {"units": {"USD": values}}


class StaticScreenNews:
    def recent_events(self, ticker: str, limit: int = 5) -> tuple[EventItem, ...]:
        if ticker.upper() == "GOOD":
            title = "Good Co wins contract award with major customer"
            source = "good news fixture"
        else:
            title = "Bad Co cuts guidance as demand slows"
            source = "bad news fixture"
        return (
            EventItem(
                ticker=ticker.upper(),
                source_type="NEWS",
                title=title,
                published_at=datetime(2026, 5, 6, tzinfo=timezone.utc).date(),
                source=source,
                form="NEWS",
            ),
        )[:limit]


class StaticUniverse:
    def members(
        self,
        market: str,
        include_etfs: bool = False,
        include_spacs: bool = False,
    ) -> tuple[UniverseMember, ...]:
        return (
            UniverseMember("GOOD", "Good Co", "NASDAQ", "test"),
            UniverseMember("BAD", "Bad Co", "NYSE", "test"),
        )


class StaticMacroProvider:
    def series(self, series_id: str) -> FredSeries:
        fixtures = {
            "CPIAUCSL": macro_series("CPIAUCSL", 300.0, 312.0),
            "CPILFESL": macro_series("CPILFESL", 310.0, 321.0),
            "UNRATE": macro_series("UNRATE", 3.8, 4.2),
            "FEDFUNDS": macro_series("FEDFUNDS", 4.5, 5.0),
            "T10Y2Y": point_macro_series("T10Y2Y", -0.35),
            "VIXCLS": macro_series("VIXCLS", 20.0, 82.0),
            "INDPRO": macro_series("INDPRO", 105.0, 103.5),
            "RSAFS": macro_series("RSAFS", 650000.0, 657000.0),
            "DTWEXBGS": macro_series("DTWEXBGS", 115.0, 121.0),
            "DEXUSEU": macro_series("DEXUSEU", 1.05, 1.12),
            "DEXJPUS": macro_series("DEXJPUS", 150.0, 140.0),
            "DEXUSUK": macro_series("DEXUSUK", 1.25, 1.32),
            "DEXSZUS": macro_series("DEXSZUS", 0.90, 0.86),
            "DEXCHUS": macro_series("DEXCHUS", 7.25, 7.00),
            "PMAIZMTUSDM": macro_series("PMAIZMTUSDM", 180.0, 225.0),
        }
        return fixtures[series_id]


class StaticEconomicCalendar:
    def upcoming_events(self, start: date, days: int) -> tuple[EconomicEvent, ...]:
        return (
            EconomicEvent(
                name="Employment Situation",
                category="labor",
                importance="critical",
                scheduled_for=datetime(2026, 6, 5, 8, 30, tzinfo=timezone.utc),
                source="BLS fixture",
                notes="Jobs report fixture",
            ),
            EconomicEvent(
                name="FOMC Rate Decision",
                category="fed",
                importance="critical",
                scheduled_for=datetime(2026, 6, 17, 14, 0, tzinfo=timezone.utc),
                source="Fed fixture",
                notes="FOMC fixture",
            ),
        )


def macro_series(series_id: str, previous: float, latest: float) -> FredSeries:
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


def point_macro_series(series_id: str, latest: float) -> FredSeries:
    return FredSeries(
        series_id=series_id,
        name=series_id,
        source=f"https://fred.test/{series_id}.csv",
        observations=(MacroObservation(series_id, date(2026, 1, 1), latest),),
    )


class StaticIndustryHistory:
    def history(
        self,
        symbol: str,
        range_period: str = "6mo",
        interval: str = "1d",
    ) -> tuple[PricePoint, ...]:
        returns = {
            "SPY": (2.0, 6.0, 8.0),
            "SMH": (6.0, 25.0, 45.0),
            "COPX": (9.0, 10.0, 5.0),
        }.get(symbol.upper(), (0.5, 1.0, 2.0))
        return price_points_from_returns(symbol, *returns)


def price_points_from_returns(
    symbol: str,
    one_month: float,
    three_month: float,
    six_month: float,
) -> tuple[PricePoint, ...]:
    current = 100.0
    anchors = {
        0: current / (1.0 + six_month / 100.0),
        63: current / (1.0 + three_month / 100.0),
        105: current / (1.0 + one_month / 100.0),
        126: current,
    }
    values = [0.0] * 127
    indexes = sorted(anchors)
    for start_index, end_index in zip(indexes, indexes[1:]):
        start_value = anchors[start_index]
        end_value = anchors[end_index]
        span = end_index - start_index
        for offset in range(span + 1):
            ratio = offset / span if span else 0.0
            values[start_index + offset] = start_value + (end_value - start_value) * ratio
    start = date(2026, 1, 1)
    return tuple(
        PricePoint(symbol.upper(), start + timedelta(days=index), value)
        for index, value in enumerate(values)
    )


if __name__ == "__main__":
    unittest.main()
