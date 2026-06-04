from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from data.catalog import MarketDataCatalog
from data.models import PriceBar
from trader import cli


def _chart_bars(symbol: str, market: str, n: int = 140) -> list[PriceBar]:
    """An uptrend with regular pullbacks (so structure is not a flat line)."""
    base = date(2025, 1, 1)
    bars: list[PriceBar] = []
    for i in range(n):
        wob = 6.0 * math.sin(i / 4.0)
        close = 100.0 + 1.2 * i + wob
        open_ = close - 1.0 * math.cos(i / 4.0)
        high = max(open_, close) + 2.0
        low = min(open_, close) - 2.0
        bars.append(
            PriceBar(
                symbol=symbol,
                market=market,
                source_symbol=symbol,
                ts=base + timedelta(days=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1000.0 + (i % 7) * 200.0,
                freq="1d",
            )
        )
    return bars


def test_chart_read_command_runs_against_catalog_bars(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_bars(_chart_bars("AAA", "us"))

    result = cli.main(
        [
            "chart-read",
            "AAA",
            "--market",
            "us",
            "--source",
            "catalog",
            "--direction",
            "long",
            "--catalog-db",
            str(catalog_db),
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert "결정" in captured.out
    assert "컨플루언스" in captured.out


def test_chart_read_command_reports_insufficient_bars(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_bars(_chart_bars("BBB", "us", n=5))

    result = cli.main(
        [
            "chart-read",
            "BBB",
            "--market",
            "us",
            "--source",
            "catalog",
            "--catalog-db",
            str(catalog_db),
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert "봉이 부족" in captured.out


def test_chart_read_crypto_catalog_still_fetches_microstructure(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--with-orderbook/--with-oi must fetch live microstructure even when OHLCV bars
    come from the catalog (codex review P2 #2)."""
    catalog_db = tmp_path / "catalog.duckdb"
    catalog = MarketDataCatalog(catalog_db)
    catalog.put_bars(_chart_bars("BTC/USDT", "crypto"))

    called = {"ob": False, "oi": False}

    def fake_ob(*_args: object, **_kwargs: object) -> object:
        called["ob"] = True
        raise RuntimeError("forced unavailable")

    def fake_oi(*_args: object, **_kwargs: object) -> object:
        called["oi"] = True
        raise RuntimeError("forced unavailable")

    monkeypatch.setattr(cli, "fetch_order_book", fake_ob)
    monkeypatch.setattr(cli, "fetch_open_interest_history", fake_oi)

    result = cli.main(
        [
            "chart-read",
            "BTC/USDT",
            "--market",
            "crypto",
            "--tf",
            "1d",
            "--source",
            "catalog",
            "--with-orderbook",
            "--with-oi",
            "--catalog-db",
            str(catalog_db),
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert called["ob"] is True
    assert called["oi"] is True
    assert "결정" in captured.out
