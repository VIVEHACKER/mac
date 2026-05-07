from datetime import date, datetime, timezone
import unittest

from trading_copilot.events import EventItem
from trading_copilot.market_data import MarketSnapshot
from trading_copilot.screening import screen_members, format_screen_report
from trading_copilot.universe import UniverseMember


class ScreeningTests(unittest.TestCase):
    def test_screen_members_ranks_positive_contract_signal_above_negative_guidance(self):
        candidates = screen_members(
            members=(
                UniverseMember("GOOD", "Good Co", "NASDAQ", "test"),
                UniverseMember("BAD", "Bad Co", "NYSE", "test"),
            ),
            market_data=StaticMarketData(),
            event_lookup=StaticEventLookup(),
            max_tickers=10,
        )

        self.assertEqual([candidate.symbol for candidate in candidates], ["GOOD", "BAD"])
        self.assertGreater(candidates[0].score, candidates[1].score)
        self.assertIn("contract", " ".join(candidates[0].reasons).lower())
        self.assertIn("guidance", " ".join(candidates[1].risks).lower())

    def test_format_screen_report_shows_universe_coverage_and_reasons(self):
        candidates = screen_members(
            members=(UniverseMember("GOOD", "Good Co", "NASDAQ", "test"),),
            market_data=StaticMarketData(),
            event_lookup=StaticEventLookup(),
            max_tickers=1,
        )

        report = format_screen_report(
            title="US Market Screen",
            candidates=candidates,
            total_universe=2,
            processed=1,
            capped=True,
        )

        self.assertIn("# US Market Screen", report)
        self.assertIn("Processed: 1 / 2", report)
        self.assertIn("Capped: yes", report)
        self.assertIn("GOOD", report)
        self.assertIn("contract", report.lower())


class StaticMarketData:
    def snapshot(self, ticker: str) -> MarketSnapshot:
        change = 2.5 if ticker == "GOOD" else -3.0
        return MarketSnapshot(
            ticker=ticker,
            price=100.0,
            previous_close=100.0 - change,
            change=change,
            change_percent=change,
            currency="USD",
            as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
            source=f"quote fixture {ticker}",
        )


class StaticEventLookup:
    def events_for(self, ticker: str) -> tuple[EventItem, ...]:
        if ticker == "GOOD":
            return (
                EventItem(
                    ticker=ticker,
                    source_type="NEWS",
                    title="Good Co wins contract award with major customer",
                    published_at=date(2026, 5, 6),
                    source="good fixture",
                    form="NEWS",
                ),
            )
        return (
            EventItem(
                ticker=ticker,
                source_type="NEWS",
                title="Bad Co cuts guidance as demand slows",
                published_at=date(2026, 5, 6),
                source="bad fixture",
                form="NEWS",
            ),
        )


if __name__ == "__main__":
    unittest.main()

