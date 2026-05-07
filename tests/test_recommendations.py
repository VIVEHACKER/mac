from datetime import datetime, timezone
import unittest

from trading_copilot.market_data import MarketSnapshot
from trading_copilot.events import EventItem
from trading_copilot.recommendations import build_recommendation_report, score_recommendation
from trading_copilot.storage import Thesis


class RecommendationTests(unittest.TestCase):
    def test_long_thesis_with_good_reward_risk_returns_consider_buy(self):
        result = score_recommendation(
            thesis=Thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            ),
            snapshot=SNAPSHOT,
            target_price=130.0,
            stop_price=90.0,
            horizon="swing",
            context="Enter only after earnings reaction stabilizes.",
        )

        self.assertEqual(result.recommendation, "Consider Buy")
        self.assertEqual(result.stance, "bullish")
        self.assertGreaterEqual(result.reward_risk, 3.0)
        self.assertIn("upside", " ".join(result.reasons).lower())
        self.assertIn("stop", " ".join(result.risks).lower())

    def test_missing_thesis_returns_wait_even_with_target(self):
        result = score_recommendation(
            thesis=None,
            snapshot=SNAPSHOT,
            target_price=130.0,
            stop_price=90.0,
            horizon="swing",
            context="",
        )

        self.assertEqual(result.recommendation, "Wait")
        self.assertEqual(result.stance, "insufficient data")
        self.assertIn("stored thesis", " ".join(result.risks).lower())

    def test_long_target_below_current_price_is_not_bullish(self):
        result = score_recommendation(
            thesis=Thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            ),
            snapshot=SNAPSHOT,
            target_price=95.0,
            stop_price=90.0,
            horizon="swing",
            context="",
        )

        self.assertEqual(result.recommendation, "Avoid Add")
        self.assertLess(result.upside_percent, 0)

    def test_report_contains_recommendation_and_no_order_execution(self):
        result = score_recommendation(
            thesis=Thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            ),
            snapshot=SNAPSHOT,
            target_price=130.0,
            stop_price=90.0,
            horizon="swing",
            context="",
        )

        report = build_recommendation_report(result)

        self.assertIn("Recommendation: Consider Buy", report)
        self.assertIn("Not investment advice", report)
        self.assertIn("Human Approval Gate", report)
        self.assertIn("No order routing", report)
        self.assertIn("Source:", report)

    def test_recent_sec_event_lowers_confidence_and_is_reported(self):
        result = score_recommendation(
            thesis=Thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            ),
            snapshot=SNAPSHOT,
            target_price=130.0,
            stop_price=90.0,
            horizon="swing",
            context="",
            events=(SEC_EVENT,),
        )

        report = build_recommendation_report(result)

        self.assertEqual(result.score, 4)
        self.assertIn("Recent SEC filing", " ".join(result.risks))
        self.assertIn("## Recent Events", report)
        self.assertIn("8-K", report)

    def test_negative_guidance_signal_lowers_score_and_is_reported(self):
        result = score_recommendation(
            thesis=Thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            ),
            snapshot=SNAPSHOT,
            target_price=130.0,
            stop_price=90.0,
            horizon="swing",
            context="",
            events=(NEGATIVE_NEWS_EVENT,),
        )

        report = build_recommendation_report(result)

        self.assertEqual(result.score, 4)
        self.assertIn("Negative forecast signal", " ".join(result.risks))
        self.assertIn("cuts guidance", report)

    def test_positive_contract_signal_raises_score_and_is_reported(self):
        result = score_recommendation(
            thesis=Thesis(
                ticker="MSFT",
                direction="long",
                statement="Azure growth can support earnings revisions.",
                invalidation="Cloud growth decelerates below peer median.",
            ),
            snapshot=SNAPSHOT,
            target_price=130.0,
            stop_price=90.0,
            horizon="swing",
            context="",
            events=(POSITIVE_NEWS_EVENT,),
        )

        report = build_recommendation_report(result)

        self.assertEqual(result.score, 6)
        self.assertIn("Positive forecast signal", " ".join(result.reasons))
        self.assertIn("Forecast Signals", report)


SNAPSHOT = MarketSnapshot(
    ticker="MSFT",
    price=100.0,
    previous_close=99.0,
    change=1.0,
    change_percent=1.010101,
    currency="USD",
    as_of=datetime(2026, 5, 7, 5, 20, tzinfo=timezone.utc),
    source="Yahoo test fixture",
)

SEC_EVENT = EventItem(
    ticker="MSFT",
    source_type="SEC",
    title="8-K filed 2026-05-06 - CURRENT REPORT",
    published_at=datetime(2026, 5, 6, tzinfo=timezone.utc).date(),
    source="https://www.sec.gov/Archives/edgar/data/789019/000095017026000001/msft-20260506.htm",
    form="8-K",
)

NEGATIVE_NEWS_EVENT = EventItem(
    ticker="MSFT",
    source_type="NEWS",
    title="Microsoft cuts guidance as Azure demand slows",
    published_at=datetime(2026, 5, 6, tzinfo=timezone.utc).date(),
    source="https://example.com/guidance-cut",
    form="NEWS",
)

POSITIVE_NEWS_EVENT = EventItem(
    ticker="MSFT",
    source_type="NEWS",
    title="Microsoft wins multi-year cloud contract with Contoso",
    published_at=datetime(2026, 5, 6, tzinfo=timezone.utc).date(),
    source="https://example.com/contract-win",
    form="NEWS",
)


if __name__ == "__main__":
    unittest.main()
