from __future__ import annotations

from datetime import datetime

from data.models import EntryPlanRecord
from trader.recommendation_report import build_trade_recommendations, recommendation_markdown
from valuation.confidence import ConfidenceBreakdown
from valuation.recommendation import AQREvaluation


def _evaluation(*, action: str = "BUY") -> AQREvaluation:
    return AQREvaluation(
        ticker="AAA",
        market="us",
        as_of=datetime(2026, 7, 10),
        in_validated_universe=True,
        rank=1,
        percentile=100.0,
        universe_size=106,
        in_top_n=True,
        composite=3.2,
        momentum=0.25,
        value=0.04,
        quality=0.12,
        current_price=100.0,
        fair_value=105.0,
        fair_value_dispersion=0.1,
        valuation_credible=True,
        entry_plan=EntryPlanRecord(
            symbol="AAA",
            market="us",
            asof_ts=datetime(2026, 7, 10),
            current_price=100.0,
            fair_value=100.0,
            target_entry=96.0,
            stop_loss=88.0,
            target_exit=115.0,
            ladder_json="[]",
            risk_reward=1.5,
        ),
        confidence=ConfidenceBreakdown(
            score=78.0,
            band="high",
            reliability=0.9,
            signal_strength=1.0,
            in_validated_universe=True,
            reasons=("validated rank",),
        ),
        action=action,
        reasons=("AQR rank 1/106", "strategy reliability 0.90"),
    )


def test_recommendation_joins_signal_levels_position_and_pretrade() -> None:
    rows = build_trade_recommendations(
        [_evaluation()],
        target_weights={"AAA": 0.2},
        target_book={"AAA": 20.0},
        intents=[
            {
                "symbol": "AAA",
                "side": "buy",
                "qty": 5.0,
                "limit_price": 100.0,
                "client_order_id": "cid-1",
            }
        ],
        pretrade=[{"client_order_id": "cid-1", "status": "accepted", "reasons": []}],
        target_capital=10_000.0,
        nav=10_000.0,
    )

    row = rows[0]
    assert row.actionable is True
    assert row.decision == "BUY / ADD"
    assert row.advisory_entry == 96.0
    assert row.advisory_stop == 88.0
    assert row.stop_loss == 90.0
    assert row.stop_basis == "2.0% NAV risk cap + 1.50R minimum"
    assert row.target_exit == 115.0
    assert row.risk_to_stop == 200.0
    assert row.allocation_drift == 0.0
    report = "\n".join(recommendation_markdown(rows, nav=10_000, target_capital=10_000, fractional=True))
    assert "실행 지정가" in report
    assert "참고 평균진입" in report
    assert "AAA" in report


def test_non_buy_signal_blocks_recommendation_even_if_pretrade_passes() -> None:
    rows = build_trade_recommendations(
        [_evaluation(action="HOLD")],
        target_weights={"AAA": 0.2},
        target_book={"AAA": 20.0},
        intents=[
            {
                "symbol": "AAA",
                "side": "buy",
                "qty": 20.0,
                "limit_price": 100.0,
                "client_order_id": "cid-1",
            }
        ],
        pretrade=[{"client_order_id": "cid-1", "status": "accepted", "reasons": []}],
        target_capital=10_000.0,
        nav=10_000.0,
    )

    assert rows[0].actionable is False
    assert "not BUY" in rows[0].blockers[0]
