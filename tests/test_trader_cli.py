from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from math import exp, sin
from pathlib import Path

import pytest

from data.catalog import MarketDataCatalog
from data.models import FundamentalRecord, MacroObservation, PriceBar, UniverseMember
from trader import cli
from trader.execution.broker import AccountSnapshot, BrokerClock
from valuation.option_vol import OptionQuote


def test_workspace_defaults_are_prepended() -> None:
    forwarded = cli._with_workspace_defaults(["init"])

    assert forwarded[:2] == ["--financial-services-dir", str(cli.DEFAULT_FINANCIAL_SERVICES_DIR)]
    assert forwarded[2:4] == ["--db", str(cli.DEFAULT_DB)]
    assert forwarded[-1] == "init"


def test_workspace_defaults_do_not_override_user_values() -> None:
    forwarded = cli._with_workspace_defaults(
        ["--financial-services-dir", "/tmp/fs", "--db", "/tmp/copilot.db", "init"]
    )

    assert forwarded == [
        "--financial-services-dir",
        "/tmp/fs",
        "--db",
        "/tmp/copilot.db",
        "init",
    ]


def test_root_help_exits_successfully(capsys) -> None:
    result = cli.main(["--help"])

    captured = capsys.readouterr()
    assert result == 0
    assert "ingest" in captured.out
    assert "portfolio" in captured.out
    assert "backtest" in captured.out


def test_parse_symbols_accepts_comma_separated_values() -> None:
    assert cli._parse_symbols("msft, aapl,NVDA") == ["MSFT", "AAPL", "NVDA"]


def test_parse_ints_accepts_comma_separated_values() -> None:
    assert cli._parse_ints("63, 126,252") == [63, 126, 252]


def test_parse_floats_enforces_bounds() -> None:
    assert cli._parse_floats("0.7, 1", min_value=0, max_value=1, min_exclusive=True) == [0.7, 1.0]
    with pytest.raises(ValueError, match="> 0"):
        cli._parse_floats("0", min_value=0, min_exclusive=True)


def test_parse_defensive_symbols_accepts_cash_aliases() -> None:
    assert cli._parse_defensive_symbols("TLT, cash,none") == ["TLT", None, None]


def test_parse_symbol_market_pairs_accepts_default_and_explicit_markets() -> None:
    assert cli._parse_symbol_market_pairs("qqq,005930:kospi", default_market="us") == (
        ("QQQ", "us"),
        ("005930", "kospi"),
    )


def test_pit_member_filter_follows_requested_symbols() -> None:
    members = [
        UniverseMember("TEST", "AAA", "us", date(2025, 1, 1)),
        UniverseMember("TEST", "BBB", "us", date(2025, 1, 1)),
    ]

    symbols = cli._symbols_for_request("AAA", members)
    filtered = cli._filter_pit_members(members, symbols)

    assert symbols == ["AAA"]
    assert [member.symbol for member in filtered] == ["AAA"]
    assert cli._symbols_for_request("CCC", members) == []
    with pytest.raises(ValueError, match="PIT universe"):
        cli._filter_pit_members(members, [])


def test_catalog_symbol_normalizes_market_specific_symbols() -> None:
    assert cli._catalog_symbol("5930", "kospi") == "005930"
    assert cli._catalog_symbol("btc", "crypto") == "BTC/USDT"
    assert cli._catalog_symbol("msft", "us") == "MSFT"


def test_us_yahoo_bars_without_adjusted_marker_need_refresh() -> None:
    legacy = _bar("MSFT", source="https://query1.finance.yahoo.com/v8/finance/chart/MSFT")
    adjusted = _bar(
        "MSFT", source="https://query1.finance.yahoo.com/v8/finance/chart/MSFT?adjusted=true"
    )

    assert cli._bars_need_refresh([legacy], market="us", provider="auto")
    assert not cli._bars_need_refresh([adjusted], market="us", provider="auto")
    assert not cli._bars_need_refresh([legacy], market="kospi", provider="auto")


def test_pair_command_runs_against_catalog_bars(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    first, second = _synthetic_pair()
    catalog.put_bars(first)
    catalog.put_bars(second)

    result = cli.main(
        [
            "pair",
            "AAA",
            "BBB",
            "--start",
            "2026-01-01",
            "--end",
            "2026-04-30",
            "--no-fetch",
            "--validate",
            "--lookback",
            "50",
            "--entry-z",
            "1",
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Pair Analysis - AAA/BBB" in captured.out
    assert "Long BBB / Short AAA" in captured.out
    assert "Cost-adjusted Rolling Validation" in captured.out


def test_factor_portfolio_returns_output_writes_daily_series(tmp_path) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_bars(_long_bars("AAA", 10.0, 0.0012))
    catalog.put_bars(_long_bars("BBB", 10.0, 0.0003))
    catalog.put_bars(_long_bars("TLT", 10.0, 0.0001))
    catalog.put_bars(_long_bars("SPY", 10.0, 0.0005))
    returns_csv = tmp_path / "returns.csv"

    result = cli.main(
        [
            "factor-portfolio",
            "AAA,BBB,TLT",
            "--start",
            "2021-01-01",
            "--end",
            "2023-12-31",
            "--momentum-lookback",
            "60",
            "--reversal-lookback",
            "10",
            "--volatility-lookback",
            "20",
            "--risk-filter-lookback",
            "0",
            "--top-n",
            "1",
            "--rebalance-days",
            "21",
            "--benchmark",
            "SPY",
            "--benchmark-market",
            "us",
            "--no-fetch",
            "--skip-universe-audit",
            "--returns-output",
            str(returns_csv),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    assert result == 0
    assert returns_csv.exists()
    lines = returns_csv.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "date,portfolio_return,benchmark_return"
    assert len(lines) > 1
    fields = lines[1].split(",")
    assert len(fields) == 3
    date.fromisoformat(fields[0])
    float(fields[1])
    float(fields[2])


def test_vix_calc_command_runs_from_option_csv(tmp_path, capsys) -> None:
    chain = tmp_path / "chain.csv"
    chain.write_text(
        "\n".join(
            [
                "expiration,strike,call_bid,call_ask,put_bid,put_ask",
                "2026-05-28,80,20.09,20.91,0.196,0.204",
                "2026-05-28,90,10.78,11.22,0.833,0.867",
                "2026-05-28,95,6.86,7.14,1.764,1.836",
                "2026-05-28,100,3.92,4.08,3.92,4.08",
                "2026-05-28,105,1.96,2.04,6.958,7.242",
                "2026-05-28,110,0.882,0.918,10.78,11.22",
                "2026-05-28,120,0.245,0.255,20.09,20.91",
                "2026-06-17,80,22.10,23.00,0.216,0.224",
                "2026-06-17,90,11.86,12.34,0.916,0.954",
                "2026-06-17,95,7.55,7.85,1.940,2.020",
                "2026-06-17,100,4.31,4.49,4.31,4.49",
                "2026-06-17,105,2.16,2.24,7.65,7.97",
                "2026-06-17,110,0.970,1.010,11.86,12.34",
                "2026-06-17,120,0.270,0.280,22.10,23.00",
            ]
        ),
        encoding="utf-8",
    )

    result = cli.main(
        [
            "vix-calc",
            "--file",
            str(chain),
            "--as-of",
            "2026-05-08",
            "--catalog-db",
            str(tmp_path / "catalog.duckdb"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "VIX-like Option Volatility" in captured.out
    assert "VIX Points" in captured.out


def test_vix_calc_uses_catalog_risk_free_rate_and_stores_result(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_macro(
        [
            MacroObservation(
                series_id="DGS10",
                country="US",
                category="rates",
                asof_date=date(2026, 5, 7),
                release_ts=datetime(2026, 5, 7, 18),
                value=4.5,
                source="manual",
            )
        ]
    )
    chain = _option_chain_csv(tmp_path)

    result = cli.main(
        [
            "vix-calc",
            "--file",
            str(chain),
            "--as-of",
            "2026-05-08",
            "--store",
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Risk-free Rate | 4.500%" in captured.out
    assert "Stored | option_sentiment/US" in captured.out


def test_pair_command_can_block_unshortable_signal(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    first, second = _synthetic_pair()
    catalog.put_bars(first)
    catalog.put_bars(second)
    shortability = tmp_path / "shortability.csv"
    shortability.write_text(
        "symbol,market,asof_date,shortable,borrow_fee_bps\nAAA,us,2026-04-30,false,100\n",
        encoding="utf-8",
    )

    result = cli.main(
        [
            "pair",
            "AAA",
            "BBB",
            "--start",
            "2026-01-01",
            "--end",
            "2026-04-30",
            "--no-fetch",
            "--lookback",
            "50",
            "--entry-z",
            "1",
            "--shortability-csv",
            str(shortability),
            "--require-shortability",
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "Shortability Gate" in captured.out
    assert "AAA: not shortable" in captured.out


def test_vix_calc_can_fetch_yahoo_option_quotes(monkeypatch, capsys) -> None:
    def fake_fetch_yahoo_option_quotes(*_args, **_kwargs):
        class _Fetched:
            source = "yahoo-options:SPY"
            quotes = [
                *_option_chain(date(2026, 5, 28)),
                *_option_chain(date(2026, 6, 17), scale=1.1),
            ]

        return _Fetched()

    monkeypatch.setattr(cli, "fetch_yahoo_option_quotes", fake_fetch_yahoo_option_quotes)

    result = cli.main(
        [
            "vix-calc",
            "--source",
            "yahoo",
            "--underlying",
            "SPY",
            "--as-of",
            "2026-05-08",
            "--risk-free-rate",
            "0.04",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Quote Source | yahoo-options:SPY" in captured.out


def test_fundamentals_csv_imports_bulk_records(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    fundamentals = tmp_path / "fundamentals.csv"
    fundamentals.write_text(
        "symbol,market,period_end,asof_ts,net_income,free_cash_flow,total_equity,total_debt,shares_out,source\n"
        "AAA,us,2024-12-31,2025-02-15T09:30:00,100,80,500,50,10,filing\n",
        encoding="utf-8",
    )

    result = cli.main(
        [
            "fundamentals",
            "ALL",
            "--provider",
            "csv",
            "--file",
            str(fundamentals),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    rows = MarketDataCatalog(catalog_db).get_fundamentals("AAA")
    assert result == 0
    assert "Stored 1 fundamentals record" in captured.out
    assert rows[0].net_income == 100


def test_universe_audit_strict_blocks_missing_fundamentals(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_universe_members(
        [
            UniverseMember(
                universe="TEST",
                symbol="AAA",
                market="us",
                start_date=date(2025, 1, 1),
            )
        ]
    )
    catalog.put_bars([_price_bar("AAA", date(2025, 1, day), 10 + day) for day in range(1, 11)])

    blocked = cli.main(
        [
            "universe-audit",
            "ALL",
            "--pit-universe",
            "TEST",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-10",
            "--require-fundamentals",
            "--strict",
            "--catalog-db",
            str(catalog_db),
        ]
    )

    blocked_output = capsys.readouterr()
    assert blocked == 2
    assert "fundamentals" in blocked_output.out

    catalog.put_fundamentals(
        [
            FundamentalRecord(
                symbol="AAA",
                market="us",
                period_end=date(2024, 12, 31),
                asof_ts=datetime(2024, 12, 31, 18),
                net_income=100,
            )
        ]
    )
    passed = cli.main(
        [
            "universe-audit",
            "ALL",
            "--pit-universe",
            "TEST",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-10",
            "--require-fundamentals",
            "--strict",
            "--catalog-db",
            str(catalog_db),
        ]
    )

    passed_output = capsys.readouterr()
    assert passed == 0
    assert "Ready | yes" in passed_output.out


def test_portfolio_preflight_blocks_missing_delisting_return(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_universe_members(
        [
            UniverseMember(
                universe="TEST",
                symbol="AAA",
                market="us",
                start_date=date(2025, 1, 1),
                end_date=date(2025, 1, 10),
            ),
            UniverseMember(
                universe="TEST",
                symbol="BBB",
                market="us",
                start_date=date(2025, 1, 1),
            ),
        ]
    )
    catalog.put_bars([_price_bar("AAA", date(2025, 1, day), 10 + day) for day in range(1, 11)])
    catalog.put_bars([_price_bar("BBB", date(2025, 1, day), 20 + day) for day in range(1, 11)])

    result = cli.main(
        [
            "portfolio",
            "ALL",
            "--pit-universe",
            "TEST",
            "--start",
            "2025-01-01",
            "--end",
            "2025-01-10",
            "--lookback",
            "2",
            "--rebalance-days",
            "2",
            "--no-fetch",
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "Backtest blocked by universe audit errors" in captured.out
    assert "delisting" in captured.out


def test_live_halt_latch_persists(tmp_path, capsys) -> None:
    halt_state = tmp_path / "halt.json"

    activated = cli.main(
        ["live-halt", "activate", "--reason", "drill", "--halt-state", str(halt_state)]
    )
    activated_output = capsys.readouterr()
    status = cli.main(["live-halt", "status", "--halt-state", str(halt_state)])
    status_output = capsys.readouterr()
    cleared = cli.main(["live-halt", "clear", "--reason", "done", "--halt-state", str(halt_state)])
    cleared_output = capsys.readouterr()

    assert activated == 2
    assert "Halt: yes" in activated_output.out
    assert status == 2
    assert "Reason: drill" in status_output.out
    assert cleared == 0
    assert "Halt: no" in cleared_output.out


def test_live_dry_run_records_order_gate(tmp_path, capsys) -> None:
    result = cli.main(
        [
            "live-dry-run",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "100",
            "--order-log",
            str(tmp_path / "orders.jsonl"),
            "--halt-state",
            str(tmp_path / "halt.json"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Live Order Gate" in captured.out
    assert "accepted" in captured.out


def test_live_dry_run_defaults_to_limit_order(tmp_path, capsys) -> None:
    # Codex #5: the rehearsal must default to limit (the same safe default as
    # live-submit), not market, and a limit with no explicit price uses the mark.
    result = cli.main(
        [
            "live-dry-run",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "100",
            "--order-log",
            str(tmp_path / "orders.jsonl"),
            "--halt-state",
            str(tmp_path / "halt.json"),
        ]
    )
    captured = capsys.readouterr()
    assert result == 0
    assert "accepted" in captured.out


def test_live_dry_run_blocks_market_order_without_env(tmp_path, monkeypatch, capsys) -> None:
    # Codex #5: rehearsal must mirror the live guard — market is rejected unless
    # LIVE_ALLOW_MARKET_ORDERS=true, so the dry-run cannot give false confidence.
    monkeypatch.delenv("LIVE_ALLOW_MARKET_ORDERS", raising=False)
    result = cli.main(
        [
            "live-dry-run",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "100",
            "--order-type",
            "market",
            "--order-log",
            str(tmp_path / "orders.jsonl"),
            "--halt-state",
            str(tmp_path / "halt.json"),
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "risk_block" in captured.out


def test_live_dry_run_allows_market_order_with_env(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("LIVE_ALLOW_MARKET_ORDERS", "true")
    result = cli.main(
        [
            "live-dry-run",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "100",
            "--order-type",
            "market",
            "--order-log",
            str(tmp_path / "orders.jsonl"),
            "--halt-state",
            str(tmp_path / "halt.json"),
        ]
    )
    captured = capsys.readouterr()
    assert result == 0
    assert "accepted" in captured.out


def test_live_dry_run_blocks_halted_system(tmp_path, capsys) -> None:
    halt_state = tmp_path / "halt.json"
    cli.main(["live-halt", "activate", "--reason", "risk drill", "--halt-state", str(halt_state)])
    capsys.readouterr()

    result = cli.main(
        [
            "live-dry-run",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "100",
            "--order-log",
            str(tmp_path / "orders.jsonl"),
            "--halt-state",
            str(halt_state),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "risk_block" in captured.out
    assert "halted: risk drill" in captured.out


def test_live_readiness_blocks_unapproved_strategy(tmp_path, monkeypatch, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _set_live_env(monkeypatch, strategy_id="unapproved")

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "latest registry decision is not approved" in captured.out


def test_live_submit_requires_ack_for_submit(tmp_path, monkeypatch, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _set_live_env(monkeypatch, strategy_id="approved-live")

    result = cli.main(
        [
            "live-submit",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "100",
            "--submit",
            "--as-of",
            "2026-05-25",
            "--order-log",
            str(tmp_path / "orders.jsonl"),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--registry",
            str(registry),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "--ack-live-order is required" in captured.out


def test_live_submit_can_submit_to_fake_after_all_gates_pass(tmp_path, monkeypatch, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    order_log = tmp_path / "orders.jsonl"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _set_live_env(monkeypatch, strategy_id="approved-live")

    result = cli.main(
        [
            "live-submit",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "100",
            "--submit",
            "--ack-live-order",
            "--as-of",
            "2026-05-25",
            "--order-log",
            str(order_log),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--equity-state",
            str(tmp_path / "equity.json"),
            "--registry",
            str(registry),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Live Submit Gate" in captured.out
    assert "filled" in captured.out
    assert "broker_submit" in order_log.read_text(encoding="utf-8")


def test_live_submit_blocks_price_far_from_live_catalog_mark(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _set_live_env(monkeypatch, strategy_id="approved-live")

    result = cli.main(
        [
            "live-submit",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "120",
            "--submit",
            "--ack-live-order",
            "--as-of",
            "2026-05-25",
            "--order-log",
            str(tmp_path / "orders.jsonl"),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--registry",
            str(registry),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "deviates" in captured.out
    assert "latest catalog close" in captured.out


def test_live_readiness_requires_paper_and_shadow_drills_for_live_broker(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-live",
        min_paper_days="1",
        min_shadow_days="1",
        min_paper_oos_periods="0",
    )

    blocked = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert blocked == 2
    assert "paper drill days 0 < 1" in captured.out
    assert "shadow drill days 0 < 1" in captured.out


def test_live_readiness_requires_paper_oos_closed_periods_for_live_broker(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-live",
        min_paper_days="0",
        min_shadow_days="0",
        min_paper_oos_periods="2",
    )

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
            "--paper-oos-dir",
            str(tmp_path / "oos"),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "paper OOS closed periods 0 < 2" in captured.out


def test_live_readiness_accepts_paper_oos_closed_periods_for_live_broker(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    paper_oos_dir = tmp_path / "oos"
    paper_oos_prices = tmp_path / "paper-oos-prices.csv"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _write_paper_oos_ledger(
        paper_oos_dir / "paper-oos-ledger-approved-live.jsonl",
        strategy_id="approved-live",
        rebal_dates=["2026-03-01", "2026-04-01", "2026-05-01"],
    )
    _write_paper_oos_prices(
        paper_oos_prices,
        rows=[
            ("2026-04-01", 101.0, 100.0),
            ("2026-05-01", 101.0, 100.0),
        ],
    )
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-live",
        min_paper_days="0",
        min_shadow_days="0",
        min_paper_oos_periods="2",
    )

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
            "--paper-oos-dir",
            str(paper_oos_dir),
            "--paper-oos-prices",
            str(paper_oos_prices),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Ready | yes" in captured.out
    assert "Operational Confidence | 100%" in captured.out
    assert "Required Paper OOS Periods | 2" in captured.out


def test_live_readiness_confidence_scores_hard_blockers(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    for name in (
        "LIVE_TRADING_ENABLED",
        "LIVE_TRADING_ACK_RISK",
        "LIVE_ORDER_SUBMISSION_ENABLED",
        "LIVE_STRATEGY_ID",
        "LIVE_BROKER",
        "LIVE_MAX_CAPITAL",
        "LIVE_POLICY_VERSION",
    ):
        monkeypatch.setenv(name, "")

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(tmp_path / "registry.jsonl"),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--catalog-db",
            str(tmp_path / "catalog.duckdb"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "Operational Confidence | 40%" in captured.out
    assert "Confidence Band | blocked-low" in captured.out
    assert "| live-policy | -35%" in captured.out
    assert "| price | -25%" in captured.out
    assert "## Next Actions" in captured.out
    assert "LIVE_TRADING_ENABLED=true" in captured.out
    assert "live-price-stream QQQ" in captured.out


def test_live_readiness_broker_preflight_requires_alpaca_credentials(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-paper",
        min_paper_days="0",
        min_shadow_days="0",
        min_paper_oos_periods="0",
    )
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "")

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-broker-preflight",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "ALPACA_API_KEY and ALPACA_SECRET_KEY are required" in captured.out
    assert "| broker-preflight | -30%" in captured.out
    assert "Replace or enable ALPACA_API_KEY/ALPACA_SECRET_KEY" in captured.out


def test_live_readiness_broker_preflight_blocks_closed_market_and_low_buying_power(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-paper",
        min_paper_days="0",
        min_shadow_days="0",
        min_paper_oos_periods="0",
    )
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")

    class _ClosedMarketAdapter:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_account(self) -> AccountSnapshot:
            return AccountSnapshot(
                account_id="acc",
                buying_power=100.0,
                cash=100.0,
                equity=10_000.0,
            )

        def list_positions(self) -> list:
            return []

        def get_clock(self) -> BrokerClock:
            return BrokerClock(
                is_open=False,
                timestamp=datetime(2026, 5, 25, 12, tzinfo=cli.UTC),
                next_open=datetime(2026, 5, 26, 13, 30, tzinfo=cli.UTC),
            )

    monkeypatch.setattr(cli, "AlpacaBrokerAdapter", _ClosedMarketAdapter)

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-broker-preflight",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "buying power 100.00 < max order notional 2,500.00" in captured.out
    assert "market is closed" in captured.out
    assert "Operational Confidence | 70%" in captured.out
    assert "| broker-preflight | -30%" in captured.out
    assert "Rerun broker preflight during the regular market session" in captured.out
    assert "Reduce LIVE_MAX_CAPITAL or fund the broker account" in captured.out


def test_live_readiness_ignores_future_paper_oos_rows_before_as_of(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    # Codex P2 (PIT): rebalances dated after --as-of were not knowable then and
    # must not satisfy the closed-period gate. Only 2026-03-01 <= as_of survives,
    # so closed periods = 0 < 2 and readiness must block.
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    paper_oos_dir = tmp_path / "oos"
    paper_oos_prices = tmp_path / "paper-oos-prices.csv"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _write_paper_oos_ledger(
        paper_oos_dir / "paper-oos-ledger-approved-live.jsonl",
        strategy_id="approved-live",
        rebal_dates=["2026-03-01", "2026-06-01", "2026-07-01"],  # 2 future rows
    )
    _write_paper_oos_prices(
        paper_oos_prices,
        rows=[("2026-06-01", 101.0, 100.0), ("2026-07-01", 101.0, 100.0)],
    )
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-live",
        min_paper_days="0",
        min_shadow_days="0",
        min_paper_oos_periods="2",
    )

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
            "--paper-oos-dir",
            str(paper_oos_dir),
            "--paper-oos-prices",
            str(paper_oos_prices),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "paper OOS closed periods 0 < 2" in captured.out


def test_live_readiness_requires_paper_oos_prices_for_live_broker(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    paper_oos_dir = tmp_path / "oos"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _write_paper_oos_ledger(
        paper_oos_dir / "paper-oos-ledger-approved-live.jsonl",
        strategy_id="approved-live",
        rebal_dates=["2026-03-01", "2026-04-01", "2026-05-01"],
    )
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-live",
        min_paper_days="0",
        min_shadow_days="0",
        min_paper_oos_periods="2",
    )

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
            "--paper-oos-dir",
            str(paper_oos_dir),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "paper OOS prices CSV is required" in captured.out


def test_live_readiness_blocks_weak_paper_oos_ratio_for_live_broker(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    paper_oos_dir = tmp_path / "oos"
    paper_oos_prices = tmp_path / "paper-oos-prices.csv"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _write_paper_oos_ledger(
        paper_oos_dir / "paper-oos-ledger-approved-live.jsonl",
        strategy_id="approved-live",
        rebal_dates=["2026-03-01", "2026-04-01", "2026-05-01"],
    )
    _write_paper_oos_prices(
        paper_oos_prices,
        rows=[
            ("2026-04-01", 100.0, 101.0),
            ("2026-05-01", 100.0, 101.0),
        ],
    )
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-live",
        min_paper_days="0",
        min_shadow_days="0",
        min_paper_oos_periods="2",
        min_paper_oos_vs_backtest="0.5",
    )

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
            "--paper-oos-dir",
            str(paper_oos_dir),
            "--paper-oos-prices",
            str(paper_oos_prices),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "paper OOS live/backtest ratio" in captured.out


def test_live_readiness_blocks_when_ratio_gate_required_but_uncomputable(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    # Codex P2 regression: ratio gate required (>0) but backtest excess is 0, so
    # vs_backtest is None. The gate must BLOCK, not silently pass, with enough periods.
    catalog_db = tmp_path / "catalog.duckdb"
    registry = tmp_path / "registry.jsonl"
    paper_oos_dir = tmp_path / "oos"
    paper_oos_prices = tmp_path / "paper-oos-prices.csv"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _write_paper_oos_ledger(
        paper_oos_dir / "paper-oos-ledger-approved-live.jsonl",
        strategy_id="approved-live",
        rebal_dates=["2026-03-01", "2026-04-01", "2026-05-01"],
    )
    _write_paper_oos_prices(
        paper_oos_prices,
        rows=[
            ("2026-04-01", 100.0, 101.0),
            ("2026-05-01", 100.0, 101.0),
        ],
    )
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-live",
        min_paper_days="0",
        min_shadow_days="0",
        min_paper_oos_periods="2",
        min_paper_oos_vs_backtest="0.5",
        paper_oos_backtest_excess="0",  # makes vs_backtest uncomputable
    )

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
            "--paper-oos-dir",
            str(paper_oos_dir),
            "--paper-oos-prices",
            str(paper_oos_prices),
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "ratio gate is required" in captured.out
    assert "LIVE_PAPER_OOS_BACKTEST_EXCESS" in captured.out


def test_live_readiness_reports_catalog_failure_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    registry = tmp_path / "registry.jsonl"
    _approve_strategy(registry, "approved-live")
    _set_live_env(monkeypatch, strategy_id="approved-live")

    def fail_quality(*args, **kwargs):
        raise RuntimeError("catalog locked")

    monkeypatch.setattr(cli, "evaluate_catalog_quality", fail_quality)
    result = cli.main(
        [
            "live-readiness",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--catalog-db",
            str(tmp_path / "catalog.duckdb"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "catalog quality check failed: catalog locked" in captured.out


def test_live_readiness_uses_live_catalog_env_by_default(tmp_path, monkeypatch, capsys) -> None:
    catalog_db = tmp_path / "live-prices.duckdb"
    registry = tmp_path / "registry.jsonl"
    MarketDataCatalog(catalog_db).put_bars([_live_price_bar("QQQ", date(2026, 5, 25), 100)])
    _approve_strategy(registry, "approved-live")
    _set_live_env(monkeypatch, strategy_id="approved-live")
    monkeypatch.setenv("LIVE_CATALOG_DB", str(catalog_db))

    result = cli.main(
        [
            "live-readiness",
            "--require-order-submission",
            "--require-price",
            "QQQ",
            "--as-of",
            "2026-05-25",
            "--registry",
            str(registry),
            "--halt-state",
            str(tmp_path / "halt.json"),
            "--drill-log",
            str(tmp_path / "drills.jsonl"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Ready | yes" in captured.out


def test_live_drill_records_status(tmp_path, monkeypatch, capsys) -> None:
    _set_live_env(
        monkeypatch,
        strategy_id="approved-live",
        broker="alpaca-live",
        min_paper_days="1",
        min_shadow_days="1",
    )
    drill_log = tmp_path / "drills.jsonl"

    paper = cli.main(
        [
            "live-drill",
            "record",
            "--mode",
            "paper",
            "--day",
            "2026-05-25",
            "--drill-log",
            str(drill_log),
        ]
    )
    capsys.readouterr()
    shadow = cli.main(
        [
            "live-drill",
            "record",
            "--mode",
            "shadow",
            "--day",
            "2026-05-25",
            "--drill-log",
            str(drill_log),
        ]
    )

    captured = capsys.readouterr()
    assert paper == 2
    assert shadow == 0
    assert "Ready | yes" in captured.out


def test_live_reconcile_latches_halt_on_position_mismatch(tmp_path, capsys) -> None:
    halt_state = tmp_path / "halt.json"

    result = cli.main(
        [
            "live-reconcile",
            "--broker",
            "fake",
            "--expected",
            "QQQ:us:2",
            "--fake-position",
            "QQQ:us:1:100",
            "--halt-state",
            str(halt_state),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "Mismatches | 1" in captured.out
    assert "Halt Latched | yes" in captured.out
    assert "broker position reconciliation mismatch" in halt_state.read_text(encoding="utf-8")


def test_model_gate_records_approval(tmp_path, capsys) -> None:
    registry = tmp_path / "registry.jsonl"

    result = cli.main(
        [
            "model-gate",
            "--strategy-id",
            "qqq-tlt-defensive",
            "--params",
            "M63/R5/V21",
            "--windows",
            "8",
            "--positive-test-rate",
            "0.625",
            "--avg-test-excess",
            "0.0208",
            "--worst-test-mdd",
            "0.23",
            "--fee-stress-passed",
            "--pit-audit-passed",
            "--registry",
            str(registry),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "APPROVED" in captured.out
    assert "qqq-tlt-defensive" in registry.read_text(encoding="utf-8")


def test_validate_model_command_runs_validation_suite(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    for symbol, daily_return in {
        "AAA": 0.0010,
        "BBB": 0.0002,
        "TLT": 0.0001,
        "SPY": 0.0003,
    }.items():
        catalog.put_bars(_long_bars(symbol, 10.0, daily_return))

    result = cli.main(
        [
            "validate-model",
            "AAA,BBB,TLT",
            "--start",
            "2020-01-01",
            "--end",
            "2023-12-31",
            "--benchmark",
            "SPY",
            "--no-fetch",
            "--momentum-lookback",
            "21",
            "--reversal-lookback",
            "5",
            "--volatility-lookback",
            "10",
            "--risk-filter-lookback",
            "20",
            "--ensemble-momentum-lookbacks",
            "21,42",
            "--ensemble-risk-filter-lookbacks",
            "0,20",
            "--defensive-basket",
            "TLT,CASH",
            "--defensive-selection-lookback",
            "10",
            "--volatility-target",
            "0.08",
            "--max-leverage",
            "1.0",
            "--crash-hedge-symbols",
            "TLT",
            "--crash-hedge-weight",
            "0.2",
            "--crash-hedge-trigger-lookback",
            "10",
            "--crash-hedge-trigger-drawdown",
            "0.05",
            "--crash-hedge-selection-lookback",
            "5",
            "--crash-hedge-hold-days",
            "3",
            "--crash-hedge-weights",
            "0.2",
            "--crash-hedge-trigger-lookbacks",
            "5,10",
            "--crash-hedge-trigger-drawdowns",
            "0.05",
            "--crash-hedge-selection-lookbacks",
            "5",
            "--crash-hedge-hold-days-values",
            "0,3",
            "--top-n",
            "1",
            "--rebalance-days",
            "21",
            "--weighting",
            "equal",
            "--defensive-only",
            "--train-years",
            "1",
            "--test-years",
            "1",
            "--step-years",
            "1",
            "--momentum-lookbacks",
            "21",
            "--top-ns",
            "1",
            "--risk-filter-lookbacks",
            "0,20",
            "--weighting-modes",
            "equal",
            "--rebalance-days-values",
            "21",
            "--fee-stress-bps",
            "2,5",
            "--stress-windows",
            "stress:2022-01-01:2022-06-30",
            "--min-walk-forward-windows",
            "1",
            "--min-positive-test-rate",
            "0.5",
            "--min-parameter-positive-rate",
            "0.5",
            "--min-stress-windows",
            "1",
            "--min-stress-return",
            "-1",
            "--catalog-db",
            str(catalog_db),
        ]
    )

    captured = capsys.readouterr()
    assert result in {0, 2}
    assert "Factor Validation Suite" in captured.out
    assert "Fee Stress" in captured.out
    assert "Parameter Perturbation" in captured.out


def _set_live_env(
    monkeypatch,
    *,
    strategy_id: str,
    broker: str = "fake",
    min_paper_days: str | None = None,
    min_shadow_days: str | None = None,
    min_paper_oos_periods: str | None = None,
    min_paper_oos_vs_backtest: str | None = None,
    paper_oos_backtest_excess: str | None = None,
) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_TRADING_ACK_RISK", "true")
    monkeypatch.setenv("LIVE_ORDER_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("LIVE_STRATEGY_ID", strategy_id)
    monkeypatch.setenv("LIVE_BROKER", broker)
    monkeypatch.setenv("LIVE_MAX_CAPITAL", "10000")
    monkeypatch.setenv("LIVE_POLICY_VERSION", "test-policy-v1")
    # These readiness tests deliberately choose explicit gate thresholds (including reduced
    # ones) to isolate a single gate, so acknowledge reduced validation — otherwise the live
    # floor (Step 7) would clamp the chosen thresholds. The floor itself is covered separately
    # in tests/test_risk/test_live_gate_floor.py.
    monkeypatch.setenv("LIVE_ACCEPT_REDUCED_VALIDATION", "true")
    if min_paper_days is not None:
        monkeypatch.setenv("LIVE_MIN_PAPER_DAYS", min_paper_days)
    if min_shadow_days is not None:
        monkeypatch.setenv("LIVE_MIN_SHADOW_DAYS", min_shadow_days)
    if min_paper_oos_periods is not None:
        monkeypatch.setenv("LIVE_MIN_PAPER_OOS_PERIODS", min_paper_oos_periods)
    if min_paper_oos_vs_backtest is not None:
        monkeypatch.setenv("LIVE_MIN_PAPER_OOS_VS_BACKTEST", min_paper_oos_vs_backtest)
    if paper_oos_backtest_excess is not None:
        monkeypatch.setenv("LIVE_PAPER_OOS_BACKTEST_EXCESS", paper_oos_backtest_excess)


def _approve_strategy(registry: Path, strategy_id: str) -> None:
    evidence = cli.make_evidence(
        strategy_id=strategy_id,
        parameter_label="approved-test",
        windows=8,
        positive_test_rate=0.75,
        average_test_annualized_excess=0.02,
        worst_test_drawdown=0.20,
        fee_stress_passed=True,
        pit_audit_passed=True,
        full_sample_annualized_return=0.18,
        full_sample_max_drawdown=0.25,
        stress_windows_tested=3,
        worst_stress_excess=0.10,
        mean_stress_excess=0.15,
    )
    cli.ResearchRegistry(registry).append(evidence, cli.evaluate_promotion(evidence))


def _write_paper_oos_ledger(
    path: Path,
    *,
    strategy_id: str,
    rebal_dates: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for rebal_date in rebal_dates:
        lines.append(
            json.dumps(
                {
                    "rebal_date": rebal_date,
                    "strategy_id": strategy_id,
                    "weights": {"QQQ": 1.0},
                    "entry_prices": {"QQQ": 100.0},
                    "benchmark_symbol": "SPY",
                    "benchmark_price": 100.0,
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_paper_oos_prices(path: Path, *, rows: list[tuple[str, float, float]]) -> None:
    lines = ["Date,QQQ,SPY"]
    lines.extend(f"{day},{qqq},{spy}" for day, qqq, spy in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bar(symbol: str, source: str) -> PriceBar:
    return PriceBar(
        symbol=symbol,
        market="us",
        source_symbol=symbol,
        ts=date(2025, 1, 1),
        open=100,
        high=100,
        low=100,
        close=100,
        volume=100,
        source=source,
    )


def _synthetic_pair(length: int = 120) -> tuple[list[PriceBar], list[PriceBar]]:
    start = date(2026, 1, 1)
    first: list[PriceBar] = []
    second: list[PriceBar] = []
    for index in range(length):
        second_log = 4.5 + index * 0.001
        residual = 0.004 * sin(index / 5)
        if index == length - 1:
            residual = 0.08
        first_log = 0.2 + 1.15 * second_log + residual
        ts = start + timedelta(days=index)
        first.append(_price_bar("AAA", ts, exp(first_log)))
        second.append(_price_bar("BBB", ts, exp(second_log)))
    return first, second


def _price_bar(symbol: str, ts: date, close: float) -> PriceBar:
    return PriceBar(
        symbol=symbol,
        market="us",
        source_symbol=symbol,
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
    )


def _live_price_bar(symbol: str, ts: date, close: float) -> PriceBar:
    return PriceBar(
        symbol=symbol,
        market="us",
        source_symbol=symbol,
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
        source="alpaca:paper:latest_bar",
    )


def _long_bars(symbol: str, start_close: float, daily_return: float) -> list[PriceBar]:
    close = start_close
    bars: list[PriceBar] = []
    for index in range(1_500):
        close *= 1 + daily_return
        ts = date(2020, 1, 1) + timedelta(days=index)
        bars.append(_price_bar(symbol, ts, close))
    return bars


def _option_chain_csv(tmp_path: Path) -> Path:
    chain = tmp_path / "chain.csv"
    chain.write_text(
        "\n".join(
            [
                "expiration,strike,call_bid,call_ask,put_bid,put_ask",
                "2026-05-28,80,20.09,20.91,0.196,0.204",
                "2026-05-28,90,10.78,11.22,0.833,0.867",
                "2026-05-28,95,6.86,7.14,1.764,1.836",
                "2026-05-28,100,3.92,4.08,3.92,4.08",
                "2026-05-28,105,1.96,2.04,6.958,7.242",
                "2026-05-28,110,0.882,0.918,10.78,11.22",
                "2026-05-28,120,0.245,0.255,20.09,20.91",
                "2026-06-17,80,22.10,23.00,0.216,0.224",
                "2026-06-17,90,11.86,12.34,0.916,0.954",
                "2026-06-17,95,7.55,7.85,1.940,2.020",
                "2026-06-17,100,4.31,4.49,4.31,4.49",
                "2026-06-17,105,2.16,2.24,7.65,7.97",
                "2026-06-17,110,0.970,1.010,11.86,12.34",
                "2026-06-17,120,0.270,0.280,22.10,23.00",
            ]
        ),
        encoding="utf-8",
    )
    return chain


def _option_chain(expiration: date, scale: float = 1.0) -> list[OptionQuote]:
    rows = [
        (80, 20.5, 0.20),
        (90, 11.0, 0.85),
        (95, 7.0, 1.80),
        (100, 4.0, 4.00),
        (105, 2.00, 7.10),
        (110, 0.90, 11.0),
        (120, 0.25, 20.5),
    ]
    return [
        OptionQuote(
            expiration=expiration,
            strike=strike,
            call_bid=call_mid * scale * 0.98,
            call_ask=call_mid * scale * 1.02,
            put_bid=put_mid * scale * 0.98,
            put_ask=put_mid * scale * 1.02,
            call_last_trade=date(2026, 5, 8),
            put_last_trade=date(2026, 5, 8),
        )
        for strike, call_mid, put_mid in rows
    ]


def test_compounder_scan_runs_and_reports(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_bars(_long_bars("AAA", 10.0, 0.0010))
    catalog.put_bars(_long_bars("BBB", 10.0, 0.0002))
    catalog.put_fundamentals(
        [
            FundamentalRecord(
                "AAA",
                "us",
                date(2023, 12, 31),
                datetime(2024, 3, 1),
                revenue=200.0,
                net_income=40.0,
                free_cash_flow=30.0,
                total_equity=100.0,
                total_debt=10.0,
                shares_out=50.0,
                eps=5.0,
            ),
            FundamentalRecord(
                "AAA",
                "us",
                date(2020, 12, 31),
                datetime(2021, 3, 1),
                revenue=100.0,
                net_income=10.0,
                free_cash_flow=8.0,
                total_equity=100.0,
                total_debt=10.0,
                shares_out=50.0,
                eps=2.0,
            ),
            FundamentalRecord(
                "BBB",
                "us",
                date(2023, 12, 31),
                datetime(2024, 3, 1),
                revenue=110.0,
                net_income=2.0,
                free_cash_flow=1.0,
                total_equity=100.0,
                total_debt=200.0,
                shares_out=60.0,
                eps=0.2,
            ),
            FundamentalRecord(
                "BBB",
                "us",
                date(2020, 12, 31),
                datetime(2021, 3, 1),
                revenue=100.0,
                net_income=2.0,
                free_cash_flow=1.0,
                total_equity=100.0,
                total_debt=200.0,
                shares_out=55.0,
                eps=0.2,
            ),
        ]
    )

    result = cli.main(
        [
            "compounder-scan",
            "AAA,BBB",
            "--as-of",
            "2024-06-30",
            "--top-n",
            "2",
            "--no-fetch",
            "--catalog-db",
            str(catalog_db),
        ]
    )
    captured = capsys.readouterr()
    assert result == 0
    assert "AAA" in captured.out
    assert "/100" in captured.out  # archetype score rendered


def test_compounder_scan_sectors_csv_excludes_financial_fcf(tmp_path, capsys) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_bars(_long_bars("BNKX", 10.0, 0.0010))
    catalog.put_bars(_long_bars("TCH", 10.0, 0.0011))
    catalog.put_fundamentals(
        [
            FundamentalRecord(
                "BNKX",
                "us",
                date(2023, 12, 31),
                datetime(2024, 3, 1),
                revenue=200.0,
                net_income=40.0,
                free_cash_flow=900.0,
                total_equity=100.0,
                total_debt=10.0,
                shares_out=50.0,
                eps=5.0,
            ),
            FundamentalRecord(
                "BNKX",
                "us",
                date(2020, 12, 31),
                datetime(2021, 3, 1),
                revenue=100.0,
                net_income=10.0,
                free_cash_flow=400.0,
                total_equity=100.0,
                total_debt=10.0,
                shares_out=50.0,
                eps=2.0,
            ),
            FundamentalRecord(
                "TCH",
                "us",
                date(2023, 12, 31),
                datetime(2024, 3, 1),
                revenue=200.0,
                net_income=40.0,
                free_cash_flow=30.0,
                total_equity=100.0,
                total_debt=10.0,
                shares_out=50.0,
                eps=5.0,
            ),
            FundamentalRecord(
                "TCH",
                "us",
                date(2020, 12, 31),
                datetime(2021, 3, 1),
                revenue=100.0,
                net_income=10.0,
                free_cash_flow=8.0,
                total_equity=100.0,
                total_debt=10.0,
                shares_out=50.0,
                eps=2.0,
            ),
        ]
    )
    sectors = tmp_path / "sectors.csv"
    sectors.write_text("symbol,sic,sector\nBNKX,6021,financials\nTCH,7372,tech\n", encoding="utf-8")

    result = cli.main(
        [
            "compounder-scan",
            "BNKX,TCH",
            "--as-of",
            "2024-06-30",
            "--top-n",
            "2",
            "--no-fetch",
            "--sectors-csv",
            str(sectors),
            "--catalog-db",
            str(catalog_db),
        ]
    )
    captured = capsys.readouterr()
    assert result == 0
    assert "[financials]" in captured.out  # BNKX dossier tagged
    assert "FCF-based metrics excluded" in captured.out


# ---------------------------------------------------------------------------
# BUG C: compounder-scan must not use bars beyond --as-of (price look-ahead)
# ---------------------------------------------------------------------------


def test_compounder_scan_uses_as_of_price_not_future_bar(tmp_path, capsys) -> None:
    """BUG C regression: bars[-1] leaks future price; fix uses last bar <= as_of."""
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)

    as_of = date(2024, 1, 31)
    early_close = 10.0  # the price that should be used (on or before as_of)
    late_close = 999.0  # future bar — must NOT affect valuation

    # GOODCO has two bars: one on the as_of date, one well after it.
    early_bar = _price_bar("GOODCO", as_of, early_close)
    late_bar = _price_bar("GOODCO", date(2025, 6, 1), late_close)
    catalog.put_bars([early_bar, late_bar])

    # NOPRICE only has a bar AFTER as_of — should be excluded from results.
    catalog.put_bars([_price_bar("NOPRICE", date(2025, 1, 1), 50.0)])

    # Fundamentals for both, filed before as_of.
    catalog.put_fundamentals(
        [
            FundamentalRecord(
                "GOODCO",
                "us",
                date(2023, 12, 31),
                datetime(2024, 1, 15),  # asof_ts before as_of
                revenue=100.0,
                net_income=20.0,
                free_cash_flow=15.0,
                total_equity=80.0,
                total_debt=5.0,
                shares_out=10.0,
                eps=2.0,
            ),
            FundamentalRecord(
                "NOPRICE",
                "us",
                date(2023, 12, 31),
                datetime(2024, 1, 15),
                revenue=100.0,
                net_income=20.0,
                free_cash_flow=15.0,
                total_equity=80.0,
                total_debt=5.0,
                shares_out=10.0,
                eps=2.0,
            ),
        ]
    )

    result = cli.main(
        [
            "compounder-scan",
            "GOODCO,NOPRICE",
            "--as-of",
            as_of.isoformat(),
            "--top-n",
            "5",
            "--no-fetch",
            "--catalog-db",
            str(catalog_db),
        ]
    )
    captured = capsys.readouterr()
    assert result == 0

    # NOPRICE has no bar on or before as_of → must be absent from output.
    assert "NOPRICE" not in captured.out, (
        "NOPRICE has no bar <= as_of; it should be excluded from compounder-scan output"
    )

    # GOODCO P/E = price / eps.  With the correct (early) close = 10.0 and
    # shares_out=10 → market_cap=100 → P/E = market_cap / net_income = 100/20 = 5.
    # With the wrong (late) close = 999.0 → market_cap=9990 → P/E ≈ 499.5.
    # The output contains "GOODCO" and a P/E line — verify late_close is not used
    # by checking the output does NOT contain the string "999" (the leaking price).
    assert "GOODCO" in captured.out, "GOODCO should appear in compounder-scan output"
    assert "999" not in captured.out, (
        "Future bar close (999.0) leaked into compounder-scan output; "
        "bars[-1] used instead of the as-of-or-earlier close"
    )


# ---------------------------------------------------------------------------
# BUG E: validate-model --record-gate must record pit_audit_passed=False when
#         --skip-universe-audit bypasses the PIT audit.
# ---------------------------------------------------------------------------


def test_validate_model_record_gate_pit_audit_false_when_skipped(tmp_path, capsys) -> None:
    """BUG E regression: pit_audit_passed must be False when --skip-universe-audit is set.

    The bug: pit_audit_passed=bool(pit_members).  When a --pit-universe IS provided
    (pit_members is non-empty) but --skip-universe-audit is also given, the audit is
    bypassed yet the evidence still records pit_audit_passed=True.
    """
    import json

    catalog_db = tmp_path / "catalog.duckdb"
    registry_path = tmp_path / "registry.jsonl"
    catalog = MarketDataCatalog(catalog_db)
    for symbol, daily_return in {
        "AAA": 0.0010,
        "BBB": 0.0002,
        "SPY": 0.0003,
    }.items():
        catalog.put_bars(_long_bars(symbol, 10.0, daily_return))

    # Seed a real PIT universe so pit_members is non-empty → triggers the bug.
    catalog.put_universe_members(
        [
            UniverseMember(
                universe="test-u",
                symbol="AAA",
                market="us",
                start_date=date(2020, 1, 1),
                end_date=None,
            ),
            UniverseMember(
                universe="test-u",
                symbol="BBB",
                market="us",
                start_date=date(2020, 1, 1),
                end_date=None,
            ),
        ]
    )

    result = cli.main(
        [
            "validate-model",
            "AAA,BBB",
            "--start",
            "2020-01-01",
            "--end",
            "2023-12-31",
            "--benchmark",
            "SPY",
            "--no-fetch",
            "--momentum-lookback",
            "21",
            "--reversal-lookback",
            "5",
            "--volatility-lookback",
            "10",
            "--risk-filter-lookback",
            "20",
            "--top-n",
            "1",
            "--rebalance-days",
            "21",
            "--weighting",
            "equal",
            "--train-years",
            "1",
            "--test-years",
            "1",
            "--step-years",
            "1",
            "--momentum-lookbacks",
            "21",
            "--top-ns",
            "1",
            "--risk-filter-lookbacks",
            "0",
            "--weighting-modes",
            "equal",
            "--rebalance-days-values",
            "21",
            "--fee-stress-bps",
            "2",
            "--min-walk-forward-windows",
            "1",
            "--min-positive-test-rate",
            "0.5",
            "--min-parameter-positive-rate",
            "0.5",
            "--record-gate",
            "--strategy-id",
            "test-skip-audit",
            "--registry",
            str(registry_path),
            "--pit-universe",
            "test-u",
            "--skip-universe-audit",
            "--catalog-db",
            str(catalog_db),
        ]
    )

    assert result in {0, 2}

    rows = [json.loads(line) for line in registry_path.read_text().splitlines() if line.strip()]
    assert rows, "registry should have at least one row after --record-gate"
    latest = rows[-1]
    evidence_payload = latest["evidence"]
    assert evidence_payload["pit_audit_passed"] is False, (
        f"Expected pit_audit_passed=False when --skip-universe-audit is set, "
        f"got {evidence_payload['pit_audit_passed']!r}"
    )


def test_live_equity_refs_tracks_peak_and_fails_closed_on_zero(tmp_path) -> None:
    """0.1b wiring: the live-submit path derives kill-switch refs from the broker's equity and
    persists the all-time peak; a non-positive equity returns (None, None) so the pre-trade equity
    check fails the order closed rather than the kill-switch raising on a zero reference."""
    from trader.execution.adapters.fake import FakeBrokerAdapter
    from trader.execution.broker import AccountSnapshot

    path = tmp_path / "equity.json"
    healthy = FakeBrokerAdapter(
        account=AccountSnapshot("t", buying_power=12_000, cash=12_000, equity=12_000)
    )
    ref, peak = cli._live_equity_refs(healthy, path, "fake")
    assert ref == 12_000.0
    assert peak == 12_000.0

    # equity drops same session -> peak persists (same broker+account key), reference unchanged
    dropped = FakeBrokerAdapter(
        account=AccountSnapshot("t", buying_power=9_000, cash=9_000, equity=9_000)
    )
    ref2, peak2 = cli._live_equity_refs(dropped, path, "fake")
    assert peak2 == 12_000.0
    assert ref2 == 12_000.0

    # a DIFFERENT account does not inherit the first account's peak (separate state key)
    other = FakeBrokerAdapter(
        account=AccountSnapshot("other", buying_power=5_000, cash=5_000, equity=5_000)
    )
    ref3, peak3 = cli._live_equity_refs(other, path, "fake")
    assert peak3 == 5_000.0
    assert ref3 == 5_000.0

    # broken/empty account -> fail closed via pre-trade, not a kill-switch crash
    broke = FakeBrokerAdapter(account=AccountSnapshot("t", buying_power=0, cash=0, equity=0))
    assert cli._live_equity_refs(broke, path, "fake") == (None, None)


def test_live_equity_refs_uses_broker_prior_close_baseline(tmp_path) -> None:
    """0.1b P1: the live wiring feeds the broker's prior-day close as the daily baseline, so a
    fresh state on a gap-down open still arms the daily latch."""
    from trader.execution.adapters.fake import FakeBrokerAdapter
    from trader.execution.broker import AccountSnapshot

    broker = FakeBrokerAdapter(
        account=AccountSnapshot(
            "acct", buying_power=9_500, cash=9_500, equity=9_500, last_equity=10_000
        )
    )
    ref, peak = cli._live_equity_refs(broker, tmp_path / "equity.json", "alpaca-paper")
    assert ref == 10_000.0  # prior close, not the gapped-down current 9_500
    assert peak == 10_000.0


def _yahoo_bar(symbol: str, close: float) -> PriceBar:
    return PriceBar(
        symbol=symbol,
        market="us",
        source_symbol=symbol,
        ts=date(2026, 6, 9),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000.0,
        freq="1d",
        source="yahoo:chart adjusted=true",
    )


def test_live_price_ingest_yahoo_fallback_is_keyless(tmp_path, capsys, monkeypatch) -> None:
    # --source yahoo must work with NO Alpaca keys: it fills the live catalog with the
    # latest EOD close per symbol (paper-loop marks while broker keys are unavailable).
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.setattr(
        cli, "fetch_yahoo_bars", lambda symbol, market, start, end: [_yahoo_bar(symbol, 101.5)]
    )

    db = tmp_path / "live.duckdb"
    result = cli.main(
        ["live-price-ingest", "QQQ,SPY", "--source", "yahoo", "--catalog-db", str(db)]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "yahoo" in captured.out
    stored = MarketDataCatalog(db).get_bars(symbol="QQQ", market="us", freq="1d")
    assert [bar.close for bar in stored] == [101.5]


def test_live_price_ingest_yahoo_partial_failure_exits_nonzero(
    tmp_path, capsys, monkeypatch
) -> None:
    from data.ingest.yahoo import YahooDataError

    def fetch(symbol, market, start, end):
        if symbol == "BAD":
            raise YahooDataError("no result")
        return [_yahoo_bar(symbol, 99.0)]

    monkeypatch.setattr(cli, "fetch_yahoo_bars", fetch)
    result = cli.main(
        [
            "live-price-ingest",
            "QQQ,BAD",
            "--source",
            "yahoo",
            "--catalog-db",
            str(tmp_path / "l.duckdb"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2  # one symbol failed -> non-zero, failure visible
    assert "BAD" in captured.out


def test_live_price_ingest_alpaca_failure_is_summarized(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")

    def fail_fetch(_symbols, *, api_key: str, secret_key: str, feed: str) -> list[PriceBar]:
        assert api_key == "key"
        assert secret_key == "secret"
        assert feed == "iex"
        raise RuntimeError("<html><body><h1>401 Authorization Required</h1></body></html>")

    monkeypatch.setattr(cli, "fetch_alpaca_latest_stock_bars", fail_fetch)

    result = cli.main(
        [
            "live-price-ingest",
            "QQQ",
            "--source",
            "alpaca",
            "--catalog-db",
            str(tmp_path / "l.duckdb"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "401 Authorization Required" in captured.out
    assert "<html>" not in captured.out


def test_live_dry_run_submit_fake_is_armed_not_crashed(tmp_path, capsys) -> None:
    # --submit-fake flips dry_run=False; the runner's fail-closed arming guard must be
    # satisfied by the drill's synthetic equity — not crash with ValueError (Codex P2).
    result = cli.main(
        [
            "live-dry-run",
            "QQQ",
            "--side",
            "buy",
            "--qty",
            "2",
            "--price",
            "100",
            "--submit-fake",
            "--order-log",
            str(tmp_path / "orders.jsonl"),
            "--halt-state",
            str(tmp_path / "halt.json"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Live Order Gate" in captured.out
    assert "filled" in captured.out  # fake broker filled the armed submission


def _paper_state(out_dir: Path, strategy_id: str, positions: dict, nav: float = 100_000.0) -> None:
    import json

    (out_dir / f"paper-drill-state-{strategy_id}.json").write_text(
        json.dumps(
            {
                "nav": nav,
                "peak": nav,
                "last_rebal": "2026-06-10",
                "positions": positions,
                "strategy_id": strategy_id,
            }
        )
    )


def test_paper_exposure_reports_and_passes(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("scripts.paper_drill.OUT_DIR", tmp_path)  # isolate state files
    sid = "exposure-test"
    _paper_state(tmp_path, sid, {"AAA": 100, "BBB": 50})
    db = tmp_path / "cat.duckdb"
    MarketDataCatalog(db).put_bars([_yahoo_bar("AAA", 100.0), _yahoo_bar("BBB", 200.0)])
    # _yahoo_bar stamps 2026-06-09; allow that age so the marks count as fresh.
    result = cli.main(
        [
            "paper-exposure",
            "--strategy-id",
            sid,
            "--catalog-db",
            str(db),
            "--max-mark-age-days",
            "9999",
        ]
    )
    captured = capsys.readouterr()
    assert result == 0
    assert "Limits: PASS" in captured.out
    assert "AAA" in captured.out and "20.00%" in captured.out  # 50*200/100k


def test_paper_exposure_fails_closed_on_missing_mark(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("scripts.paper_drill.OUT_DIR", tmp_path)
    sid = "exposure-missing"
    _paper_state(tmp_path, sid, {"AAA": 100, "ZZZ": 10})
    db = tmp_path / "cat.duckdb"
    MarketDataCatalog(db).put_bars([_yahoo_bar("AAA", 100.0)])
    result = cli.main(
        [
            "paper-exposure",
            "--strategy-id",
            sid,
            "--catalog-db",
            str(db),
            "--max-mark-age-days",
            "9999",
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "FAIL-CLOSED" in captured.out and "ZZZ" in captured.out


def test_paper_exposure_flags_breach(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("scripts.paper_drill.OUT_DIR", tmp_path)
    sid = "exposure-breach"
    _paper_state(tmp_path, sid, {"AAA": 400}, nav=100_000.0)  # 40% single name
    db = tmp_path / "cat.duckdb"
    MarketDataCatalog(db).put_bars([_yahoo_bar("AAA", 100.0)])
    result = cli.main(
        [
            "paper-exposure",
            "--strategy-id",
            sid,
            "--catalog-db",
            str(db),
            "--max-mark-age-days",
            "9999",
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "BREACH" in captured.out and "single-name AAA" in captured.out
