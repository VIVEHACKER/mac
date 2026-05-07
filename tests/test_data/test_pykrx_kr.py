from __future__ import annotations

from datetime import date

import pandas as pd

from data.ingest.pykrx_kr import fetch_pykrx_bars, normalize_kr_symbol


def test_normalize_kr_symbol_zero_pads_numeric_ticker() -> None:
    assert normalize_kr_symbol("5930") == "005930"


def test_fetch_pykrx_bars_uses_injected_fetcher() -> None:
    frame = pd.DataFrame(
        {
            "시가": [70_000],
            "고가": [72_000],
            "저가": [69_000],
            "종가": [71_000],
            "거래량": [12_345],
        },
        index=pd.to_datetime(["2026-05-07"]),
    )

    def fetcher(from_date: str, to_date: str, ticker: str) -> pd.DataFrame:
        assert from_date == "20260501"
        assert to_date == "20260507"
        assert ticker == "005930"
        return frame

    bars = fetch_pykrx_bars(
        "5930",
        market="kospi",
        start=date(2026, 5, 1),
        end=date(2026, 5, 7),
        fetch_ohlcv=fetcher,
    )

    assert len(bars) == 1
    assert bars[0].symbol == "005930"
    assert bars[0].market == "kospi"
    assert bars[0].currency == "KRW"
    assert bars[0].close == 71_000
