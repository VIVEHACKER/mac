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
    adjusted = _bar("MSFT", source="https://query1.finance.yahoo.com/v8/finance/chart/MSFT?adjusted=true")

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

    result = cli.main(["vix-calc", "--file", str(chain), "--as-of", "2026-05-08"])

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
        "symbol,market,asof_date,shortable,borrow_fee_bps\n"
        "AAA,us,2026-04-30,false,100\n",
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
