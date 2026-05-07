from __future__ import annotations

from datetime import datetime

from engine.live import LiveTradingBlockedError, assert_live_trading_enabled
from engine.paper import PaperBroker
from risk.kill_switch import check_kill_switch


def test_paper_broker_and_kill_switch(monkeypatch) -> None:
    broker = PaperBroker(initial_cash=1_000)
    order = broker.submit_market_order(
        strategy="test",
        symbol="MSFT",
        market="us",
        side="buy",
        qty=2,
        price=100,
        ts=datetime(2025, 1, 2),
    )

    assert order.status == "filled"
    assert broker.cash == 800
    assert broker.equity({"MSFT": 110}) == 1_020
    assert check_kill_switch(start_equity=1_000, current_equity=970, gross_exposure=0.5).halted

    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    try:
        assert_live_trading_enabled()
    except LiveTradingBlockedError:
        pass
    else:
        raise AssertionError("live trading should be blocked by default")
