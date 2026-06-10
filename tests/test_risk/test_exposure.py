from __future__ import annotations

import pytest

from engine.paper import PaperBroker
from risk.exposure import ExposureLimits, build_exposure_report, check_exposure_limits
from trader.execution.broker import PositionSnapshot
from trader.execution.intents import OrderIntent


def _pos(symbol: str, mv: float, qty: float = 1.0) -> PositionSnapshot:
    return PositionSnapshot(symbol=symbol, market="us", qty=qty, market_value=mv)


def test_long_only_book_gross_equals_net() -> None:
    report = build_exposure_report([_pos("AAA", 30_000), _pos("BBB", 20_000)], 100_000)
    assert report.gross_exposure == pytest.approx(0.5)
    assert report.net_exposure == pytest.approx(0.5)
    assert report.long_exposure == pytest.approx(0.5)
    assert report.short_exposure == 0.0
    assert report.top_symbol == "AAA"
    assert report.top_weight == pytest.approx(0.3)


def test_short_positions_split_gross_and_net() -> None:
    report = build_exposure_report([_pos("AAA", 60_000), _pos("BBB", -40_000)], 100_000)
    assert report.gross_exposure == pytest.approx(1.0)
    assert report.net_exposure == pytest.approx(0.2)
    assert report.short_exposure == pytest.approx(0.4)


def test_sector_weights_aggregate_and_unmapped_bucket() -> None:
    report = build_exposure_report(
        [_pos("AAA", 30_000), _pos("BBB", 20_000), _pos("CCC", 10_000)],
        100_000,
        sectors={"AAA": "Tech", "BBB": "Tech"},
    )
    assert report.sector_weights["Tech"] == pytest.approx(0.5)
    assert report.sector_weights[""] == pytest.approx(0.1)  # CCC unmapped


def test_non_positive_equity_rejected() -> None:
    with pytest.raises(ValueError, match="equity"):
        build_exposure_report([], 0.0)


def test_limits_flag_every_breach() -> None:
    report = build_exposure_report(
        [_pos("AAA", 50_000), _pos("BBB", 70_000)],
        100_000,
        sectors={"AAA": "Tech", "BBB": "Tech"},
    )
    check = check_exposure_limits(
        report,
        ExposureLimits(max_gross_exposure=1.0, max_single_name=0.25, max_sector=0.40),
    )
    assert not check.passed
    text = " | ".join(check.reasons)
    assert "gross exposure" in text
    assert "single-name AAA" in text and "single-name BBB" in text
    assert "sector Tech" in text


def test_clean_book_passes() -> None:
    report = build_exposure_report([_pos("AAA", 8_000)], 100_000, sectors={"AAA": "Tech"})
    assert check_exposure_limits(report).passed


def test_reads_live_paper_broker_book() -> None:
    # The monitor consumes the same BrokerAdapter surface the live loop uses.
    marks = {"AAA": 100.0, "BBB": 200.0}
    broker = PaperBroker(100_000.0, marks=marks)
    for symbol, qty in (("AAA", 80), ("BBB", 40)):
        broker.submit_order(
            OrderIntent(strategy="t", symbol=symbol, market="us", side="buy", qty=qty).normalized()
        )
    report = build_exposure_report(broker.list_positions(), broker.get_account().equity)
    assert report.gross_exposure == pytest.approx(0.16, abs=1e-9)  # 8k + 8k over 100k
    assert report.symbol_weights == {
        "AAA": pytest.approx(0.08),
        "BBB": pytest.approx(0.08),
    }
