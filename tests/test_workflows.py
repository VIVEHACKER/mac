from pathlib import Path
from datetime import date, datetime, timezone
import tempfile
import unittest

from trading_copilot.market_data import MarketSnapshot
from trading_copilot.events import EventItem
from trading_copilot.macro import FredSeries, MacroObservation
from trading_copilot.sector_rankings import CompanyMetrics
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
            )

            report = workflows.macro_report()

            self.assertIn("# Macro Cycle Dashboard", report)
            self.assertIn("Market Structure Read", report)
            self.assertIn("Consumer Price Index", report)
            self.assertIn("Not investment advice", report)

    def test_sector_ranking_report_uses_korean_tickers_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TradingStore(Path(tmp) / "copilot.db")
            store.initialize()
            workflows = TradingWorkflows(
                SkillRegistry(FINANCIAL_SERVICES),
                store,
                korean_tickers=StaticKoreanTickers(),
            )

            report = workflows.sector_ranking_report(
                market="kr",
                max_tickers=10,
                per_sector_limit=2,
                sector="",
                tickers=(),
                use_korean_tickers=True,
            )
            csv_text = workflows.sector_ranking_csv(
                market="kr",
                max_tickers=10,
                per_sector_limit=2,
                sector="",
                tickers=(),
                use_korean_tickers=True,
            )

            self.assertIn("# Sector Company Rankings - KR", report)
            self.assertIn("Technology Capability", report)
            self.assertIn("Semiconductors", report)
            self.assertLess(report.index("000660.KS"), report.index("005380.KS"))
            self.assertIn("https://www.koreantickers.com/reports?q=000660", report)
            self.assertIn("sector_rank,company_rank,sector", csv_text)

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


class StaticKoreanTickers:
    def metrics(self, market: str = "kr") -> tuple[CompanyMetrics, ...]:
        return (
            CompanyMetrics(
                symbol="005930.KS",
                name="Samsung Electronics",
                market="KOSPI",
                sector="Semiconductors",
                revenue_growth=10.88,
                net_margin=18.0,
                three_month_return=67.56,
                report_count=66,
                pe=41.36,
                peg=1.32,
                sources=("https://www.koreantickers.com/reports?q=005930",),
            ),
            CompanyMetrics(
                symbol="000660.KS",
                name="SK hynix",
                market="KOSPI",
                sector="Semiconductors",
                revenue_growth=46.76,
                net_margin=28.0,
                three_month_return=92.73,
                report_count=31,
                pe=28.02,
                peg=0.24,
                sources=("https://www.koreantickers.com/reports?q=000660",),
            ),
            CompanyMetrics(
                symbol="005380.KS",
                name="Hyundai Motor",
                market="KOSPI",
                sector="Automobiles",
                revenue_growth=6.29,
                net_margin=8.0,
                three_month_return=21.71,
                report_count=17,
                pe=16.10,
                sources=("https://www.koreantickers.com/reports?q=005380",),
            ),
        )


class StaticMacroProvider:
    def series(self, series_id: str) -> FredSeries:
        fixtures = {
            "CPIAUCSL": macro_series("CPIAUCSL", 300.0, 312.0),
            "CPILFESL": macro_series("CPILFESL", 310.0, 321.0),
            "UNRATE": macro_series("UNRATE", 3.8, 4.2),
            "FEDFUNDS": macro_series("FEDFUNDS", 4.5, 5.0),
            "T10Y2Y": point_macro_series("T10Y2Y", -0.35),
            "INDPRO": macro_series("INDPRO", 105.0, 103.5),
            "RSAFS": macro_series("RSAFS", 650000.0, 657000.0),
        }
        return fixtures[series_id]


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


if __name__ == "__main__":
    unittest.main()
