"""Step 7: the live forward-validation gates may be RAISED by env but not silently lowered
below their safe floor — closing the audit's "one .env toggle nukes forward validation" risk.
"""

from __future__ import annotations

import pytest

from engine.live import load_live_trading_policy

_GATE_VARS = [
    "LIVE_MIN_PAPER_DAYS",
    "LIVE_MIN_SHADOW_DAYS",
    "LIVE_MIN_PAPER_OOS_PERIODS",
    "LIVE_MIN_PAPER_OOS_VS_BACKTEST",
    "LIVE_ACCEPT_REDUCED_VALIDATION",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (*_GATE_VARS, "LIVE_BROKER"):
        monkeypatch.delenv(var, raising=False)


def _live(monkeypatch):
    monkeypatch.setenv("LIVE_BROKER", "alpaca-live")


def test_live_defaults_are_the_safe_floor(monkeypatch) -> None:
    _live(monkeypatch)
    policy = load_live_trading_policy()
    assert policy.min_paper_oos_periods == 6
    assert policy.min_paper_days == 30
    assert policy.min_shadow_days == 10
    assert policy.min_paper_oos_vs_backtest == 0.5


def test_env_cannot_lower_gate_below_floor_without_ack(monkeypatch) -> None:
    _live(monkeypatch)
    monkeypatch.setenv("LIVE_MIN_PAPER_OOS_PERIODS", "0")
    monkeypatch.setenv("LIVE_MIN_PAPER_DAYS", "0")
    monkeypatch.setenv("LIVE_MIN_SHADOW_DAYS", "0")
    monkeypatch.setenv("LIVE_MIN_PAPER_OOS_VS_BACKTEST", "0.0")
    policy = load_live_trading_policy()
    # The floor holds — a single .env toggle does not nuke forward validation.
    assert policy.min_paper_oos_periods == 6
    assert policy.min_paper_days == 30
    assert policy.min_shadow_days == 10
    assert policy.min_paper_oos_vs_backtest == 0.5


def test_explicit_ack_allows_reduced_gates(monkeypatch) -> None:
    _live(monkeypatch)
    monkeypatch.setenv("LIVE_MIN_PAPER_OOS_PERIODS", "0")
    monkeypatch.setenv("LIVE_MIN_PAPER_OOS_VS_BACKTEST", "0.0")
    monkeypatch.setenv("LIVE_ACCEPT_REDUCED_VALIDATION", "true")
    policy = load_live_trading_policy()
    # A deliberate, auditable override is honoured.
    assert policy.min_paper_oos_periods == 0
    assert policy.min_paper_oos_vs_backtest == 0.0


def test_env_can_still_raise_gates(monkeypatch) -> None:
    _live(monkeypatch)
    monkeypatch.setenv("LIVE_MIN_PAPER_OOS_PERIODS", "12")
    monkeypatch.setenv("LIVE_MIN_PAPER_OOS_VS_BACKTEST", "0.8")
    policy = load_live_trading_policy()
    assert policy.min_paper_oos_periods == 12  # stricter than the floor is always allowed
    assert policy.min_paper_oos_vs_backtest == 0.8


def test_paper_broker_has_no_live_floor(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_BROKER", "alpaca-paper")
    policy = load_live_trading_policy()
    # Non-live broker: floor is 0, so paper/fake runs are unconstrained by the live gates.
    assert policy.min_paper_oos_periods == 0
    assert policy.min_paper_days == 0
    assert policy.min_shadow_days == 0
    assert policy.min_paper_oos_vs_backtest == 0.0
