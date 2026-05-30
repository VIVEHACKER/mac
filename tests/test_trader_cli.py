from __future__ import annotations

from datetime import date, datetime, timedelta
from math import exp, sin
from pathlib import Path

import pytest

from data.catalog import MarketDataCatalog
from data.models import FundamentalRecord, MacroObservation, PriceBar, UniverseMember
from trader import cli
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
) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_TRADING_ACK_RISK", "true")
    monkeypatch.setenv("LIVE_ORDER_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("LIVE_STRATEGY_ID", strategy_id)
    monkeypatch.setenv("LIVE_BROKER", broker)
    monkeypatch.setenv("LIVE_MAX_CAPITAL", "10000")
    monkeypatch.setenv("LIVE_POLICY_VERSION", "test-policy-v1")
    if min_paper_days is not None:
        monkeypatch.setenv("LIVE_MIN_PAPER_DAYS", min_paper_days)
    if min_shadow_days is not None:
        monkeypatch.setenv("LIVE_MIN_SHADOW_DAYS", min_shadow_days)


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
        stress_windows_tested=2,
        worst_stress_return=0.35,
        stress_passed=True,
    )
    cli.ResearchRegistry(registry).append(evidence, cli.evaluate_promotion(evidence))


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
