"""Pre-trade SECTOR concentration cap (live-readiness audit P1: RiskPolicy had no sector field,
so the submission path could not structurally gate sector crowding — only the reporting monitor
in risk/exposure.py knew about sectors). evaluate_pretrade_order now blocks an order that would
push its sector's aggregate weight over policy.max_sector_weight, mirroring the per-symbol cap.
"""

from __future__ import annotations

from risk.halt_state import HaltStateStore
from risk.policy import RiskPolicy
from risk.pretrade import evaluate_pretrade_order
from risk.sectors import load_sector_map
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.broker import AccountSnapshot, PositionSnapshot
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.runner import process_order_intents

# AAPL (tech) is already 30% of a 100k book; the per-symbol/notional gates are opened wide so the
# tests isolate the sector cap.
_AAPL = PositionSnapshot("AAPL", "us", qty=300, market_value=30_000.0)
_MARKS = {"AAPL": 100.0, "MSFT": 100.0, "XOM": 100.0}
_SECTORS = {"AAPL": "tech", "MSFT": "tech", "XOM": "energy"}


def _policy(max_sector_weight: float = 1.0) -> RiskPolicy:
    return RiskPolicy(
        max_order_notional=1e9,
        max_daily_new_notional=1e9,
        max_symbol_weight=1.0,
        max_gross_exposure=10.0,
        min_cash_fraction=0.0,
        max_sector_weight=max_sector_weight,
    )


def _account() -> AccountSnapshot:
    return AccountSnapshot(account_id="t", buying_power=1e6, cash=1e6, equity=100_000.0)


def _buy(symbol: str, qty: float) -> OrderIntent:
    return OrderIntent(
        strategy="s",
        symbol=symbol,
        market="us",
        side="buy",
        qty=qty,
        order_type="limit",
        limit_price=100.0,
    )


def test_risk_policy_default_sector_cap_is_permissive() -> None:
    assert RiskPolicy().max_sector_weight == 1.0  # off by default — existing callers unaffected


def test_pretrade_blocks_order_that_breaches_sector_cap() -> None:
    # AAPL tech 30% held; buying MSFT (tech) 20% → tech 50% > 40% cap → block.
    result = evaluate_pretrade_order(
        _buy("MSFT", 200),
        policy=_policy(0.40),
        account=_account(),
        positions=[_AAPL],
        marks=_MARKS,
        sectors=_SECTORS,
    )
    assert not result.passed
    assert any("sector" in r and "tech" in r for r in result.reasons), result.reasons


def test_pretrade_allows_order_in_uncrowded_sector() -> None:
    # Buying XOM (energy) 20% → energy 20%, tech 30%, both < 40% → pass.
    result = evaluate_pretrade_order(
        _buy("XOM", 200),
        policy=_policy(0.40),
        account=_account(),
        positions=[_AAPL],
        marks=_MARKS,
        sectors=_SECTORS,
    )
    assert result.passed, result.reasons


def test_pretrade_skips_sector_check_without_sector_data() -> None:
    # No sector map → cannot gate sectors → the tech-crowding buy passes (backward compatible).
    result = evaluate_pretrade_order(
        _buy("MSFT", 200),
        policy=_policy(0.40),
        account=_account(),
        positions=[_AAPL],
        marks=_MARKS,
        sectors=None,
    )
    assert result.passed, result.reasons


def test_pretrade_default_policy_does_not_gate_sectors() -> None:
    # Permissive default cap (1.0): even with sector data the tech-crowding buy passes.
    result = evaluate_pretrade_order(
        _buy("MSFT", 200),
        policy=_policy(),
        account=_account(),
        positions=[_AAPL],
        marks=_MARKS,
        sectors=_SECTORS,
    )
    assert result.passed, result.reasons


def test_pretrade_allows_risk_reducing_sell_in_over_cap_sector() -> None:
    # Tech is already 40% (over a 30% cap). Selling some AAPL reduces tech to 35% — still over
    # cap but moving toward compliance. A de-risking order must NOT be blocked (codex P2).
    over_cap_aapl = PositionSnapshot("AAPL", "us", qty=400, market_value=40_000.0)
    sell = OrderIntent(
        strategy="s",
        symbol="AAPL",
        market="us",
        side="sell",
        qty=50,
        order_type="limit",
        limit_price=100.0,
    )
    result = evaluate_pretrade_order(
        sell,
        policy=_policy(0.30),
        account=_account(),
        positions=[over_cap_aapl],
        marks=_MARKS,
        sectors=_SECTORS,
    )
    assert result.passed, result.reasons


def test_pretrade_blocks_same_symbol_buy_even_when_order_mark_below_snapshot() -> None:
    # codex P2: a BUY adds exposure even if the order mark ($75) is below the broker snapshot
    # price ($100), so projected market value can look <= current. The de-risking exemption must
    # key on order SIDE, not a cross-mark value comparison — an exposure-increasing buy stays gated.
    held = PositionSnapshot("AAPL", "us", qty=400, market_value=40_000.0)  # snapshot @ $100
    buy = OrderIntent(
        strategy="s",
        symbol="AAPL",
        market="us",
        side="buy",
        qty=100,
        order_type="limit",
        limit_price=75.0,
    )
    result = evaluate_pretrade_order(
        buy,
        policy=_policy(0.30),
        account=_account(),
        positions=[held],
        marks={"AAPL": 75.0},
        sectors={"AAPL": "tech"},
    )
    assert not result.passed
    assert any("sector" in reason for reason in result.reasons), result.reasons


def test_pretrade_blocks_short_that_increases_sector_exposure_when_shorting_allowed() -> None:
    # codex P2: with allow_short, an opening short-sell INCREASES absolute sector exposure and
    # must be gated even though side != "buy". Tech is 25% from an MSFT long; shorting AAPL adds
    # 20% absolute -> tech 45% > 30% cap.
    policy = RiskPolicy(
        max_order_notional=1e9,
        max_daily_new_notional=1e9,
        max_symbol_weight=1.0,
        max_gross_exposure=10.0,
        min_cash_fraction=0.0,
        max_sector_weight=0.30,
        allow_short=True,
    )
    msft = PositionSnapshot("MSFT", "us", qty=250, market_value=25_000.0)
    short_aapl = OrderIntent(
        strategy="s",
        symbol="AAPL",
        market="us",
        side="sell",
        qty=200,
        order_type="limit",
        limit_price=100.0,
    )
    result = evaluate_pretrade_order(
        short_aapl,
        policy=policy,
        account=_account(),
        positions=[msft],
        marks={"AAPL": 100.0, "MSFT": 100.0},
        sectors={"AAPL": "tech", "MSFT": "tech"},
    )
    assert not result.passed
    assert any("sector" in reason for reason in result.reasons), result.reasons


def test_pretrade_allows_buy_to_cover_that_reduces_short_sector_exposure() -> None:
    # The mirror: buying to cover an over-cap SHORT sector reduces absolute exposure -> exempt,
    # even though side == "buy".
    policy = RiskPolicy(
        max_order_notional=1e9,
        max_daily_new_notional=1e9,
        max_symbol_weight=1.0,
        max_gross_exposure=10.0,
        min_cash_fraction=0.0,
        max_sector_weight=0.30,
        allow_short=True,
    )
    short_aapl_pos = PositionSnapshot("AAPL", "us", qty=-400, market_value=-40_000.0)  # tech 40%
    cover = OrderIntent(
        strategy="s",
        symbol="AAPL",
        market="us",
        side="buy",
        qty=50,
        order_type="limit",
        limit_price=100.0,
    )
    result = evaluate_pretrade_order(
        cover,
        policy=policy,
        account=_account(),
        positions=[short_aapl_pos],
        marks={"AAPL": 100.0},
        sectors={"AAPL": "tech"},
    )
    assert result.passed, result.reasons


def test_runner_blocks_sector_breaching_order(tmp_path) -> None:
    # The cap is reachable through the live execution path, not just the bare function.
    broker = FakeBrokerAdapter(
        account=AccountSnapshot("t", buying_power=1e6, cash=1e6, equity=100_000.0),
        positions=[_AAPL],
    )
    results = process_order_intents(
        [_buy("MSFT", 200)],
        broker=broker,
        store=JsonlOrderStore(tmp_path / "orders.jsonl"),
        halt_store=HaltStateStore(tmp_path / "halt.json"),
        policy=_policy(0.40),
        marks=_MARKS,
        dry_run=True,
        sectors=_SECTORS,
    )
    assert results[0].status == "risk_block"
    assert any("sector" in reason for reason in results[0].reasons), results[0].reasons


def test_load_sector_map_reads_csv_and_skips_blank_sector(tmp_path) -> None:
    path = tmp_path / "universe-sectors.csv"
    path.write_text("symbol,sic,sector\naapl,3571,tech\nXOM,2911,energy\nZZZ,,\n", encoding="utf-8")
    assert load_sector_map(path) == {"AAPL": "tech", "XOM": "energy"}  # blank-sector row dropped
