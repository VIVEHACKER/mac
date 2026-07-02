"""Delta rebalance plan (semi-auto operating model). paper_drill previously emitted BUY-only
order lists — correct for month 1 from flat, but from month 2 it would re-buy the whole target
book on top of existing holdings (double-buy) and never sell dropped names. ``build_delta_plan``
routes the validated weights through the SAME decision->order layer live uses
(``targets_from_weights`` + ``plan_rebalance``: delta vs prior book, sells first) and
pre-validates every intent through the SAME gate live uses (``process_order_intents`` dry-run
with ``live_risk_policy()`` + the sector map), so the operator approves a plan the live gates
have already seen.
"""

from __future__ import annotations

import json

import pytest

from scripts.paper_drill import build_delta_plan, write_plan_json

_ENV = [
    "LIVE_MAX_CAPITAL",
    "LIVE_MAX_SECTOR_WEIGHT",
    "LIVE_MAX_LIMIT_DEVIATION",
    "LIVE_ALLOW_MARKET_ORDERS",
    "LIVE_BROKER",
    "LIVE_POLICY_VERSION",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)


def _plan(tmp_path, **kwargs):
    defaults = {
        "strategy_id": "aqr_top5_cap20_trail10_pit110",
        "rebalance_key": "aqr_top5_cap20_trail10_pit110-2026-07-02",
        "weights": {},
        "marks": {"AAPL": 100.0, "MSFT": 100.0},
        "prior_positions": {},
        "nav": 10_000.0,
        "target_capital": 10_000.0,
        "sectors": None,
        "work_dir": tmp_path,
    }
    defaults.update(kwargs)
    return build_delta_plan(**defaults)


def test_delta_plan_sells_dropped_names_before_buys(tmp_path) -> None:
    # Month 2: MSFT leaves the basket, AAPL enters. The plan must SELL MSFT (exit) before
    # BUYING AAPL — never a buy-only list on top of existing holdings.
    plan = _plan(
        tmp_path,
        weights={"AAPL": 0.2},
        prior_positions={"MSFT": 5.0},
    )
    sides = [(i["symbol"], i["side"]) for i in plan["intents"]]
    assert sides == [("MSFT", "sell"), ("AAPL", "buy")]
    assert plan["intents"][0]["qty"] == 5  # full exit of the dropped name
    assert plan["intents"][1]["qty"] == 20  # floor(10000*0.2/100)
    assert plan["all_pass"] is True


def test_delta_plan_does_not_rebuy_held_position(tmp_path) -> None:
    # The target already equals the holding -> no intent for that symbol (no double-buy).
    plan = _plan(
        tmp_path,
        weights={"AAPL": 0.2},
        prior_positions={"AAPL": 20.0},
    )
    assert plan["intents"] == []
    assert plan["all_pass"] is True


def test_delta_plan_flags_sector_cap_breach_in_pretrade(tmp_path) -> None:
    # Tech held 20% (MSFT stays in the book) + AAPL buy 20% -> projected tech 40% > 35% live
    # default cap: the plan surfaces the sector block BEFORE the operator ever submits.
    plan = _plan(
        tmp_path,
        weights={"MSFT": 0.2, "AAPL": 0.2},
        prior_positions={"MSFT": 20.0},
        sectors={"AAPL": "tech", "MSFT": "tech"},
    )
    assert plan["all_pass"] is False
    blocked = [c for c in plan["pretrade"] if c["status"] == "risk_block"]
    assert blocked and any("sector" in r for c in blocked for r in c["reasons"])


def test_empty_target_book_with_no_holdings_warns(tmp_path) -> None:
    plan = _plan(tmp_path, weights={}, prior_positions={})
    assert plan["intents"] == []
    assert plan["empty"] is True
    assert plan["warning"]  # an explicitly surfaced warning, not a silent empty plan


def test_empty_target_book_with_holdings_is_full_liquidation_warning(tmp_path) -> None:
    # An empty target with an existing book means "sell everything" — legal but loud.
    plan = _plan(tmp_path, weights={}, prior_positions={"AAPL": 20.0})
    assert [(i["symbol"], i["side"]) for i in plan["intents"]] == [("AAPL", "sell")]
    assert plan["warning"] and "liquidat" in plan["warning"].lower()


def test_cli_rebalance_plan_forwards_to_paper_drill(monkeypatch) -> None:
    # `trader rebalance-plan` is the CLI surface of the semi-auto model — a thin forwarder to
    # the validated paper_drill generator (single implementation, no duplicated ranking code).
    import scripts.paper_drill as paper_drill_mod
    from trader import cli

    captured: dict = {}

    def fake_main(argv=None):
        captured["argv"] = list(argv or [])
        return 0

    monkeypatch.setattr(paper_drill_mod, "main", fake_main)

    code = cli.main(
        [
            "rebalance-plan",
            "--top-n",
            "7",
            "--strategy-id",
            "aqr_top7_cap20_trail10_pit110",
            "--no-record-oos",
        ]
    )

    assert code == 0
    argv = captured["argv"]
    assert argv[argv.index("--top-n") + 1] == "7"
    assert "--strategy-id" in argv and "--no-record-oos" in argv


def test_write_plan_json_roundtrip(tmp_path) -> None:
    plan = _plan(tmp_path, weights={"AAPL": 0.2}, prior_positions={})
    path = write_plan_json(plan, out_dir=tmp_path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["strategy_id"] == plan["strategy_id"]
    assert loaded["rebalance_key"] == plan["rebalance_key"]
    assert loaded["intents"] == plan["intents"]
    assert "rebalance-plan" in path.name
