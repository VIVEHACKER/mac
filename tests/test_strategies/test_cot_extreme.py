from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from data.ingest.cot_sp500 import MARKET, parse_cot_frame
from signals.cot_extreme import cot_extreme_signal, cot_index


def _raw_row(d: str, ncl: int, ncs: int, cl: int, cs: int, oi: int, market: str = MARKET) -> dict:
    return {
        "As of Date in Form YYYY-MM-DD": d,
        "Market and Exchange Names": market,
        "Noncommercial Positions-Long (All)": ncl,
        "Noncommercial Positions-Short (All)": ncs,
        "Commercial Positions-Long (All)": cl,
        "Commercial Positions-Short (All)": cs,
        "Open Interest (All)": oi,
    }


def test_parse_filters_market_and_computes_nets() -> None:
    raw = pd.DataFrame(
        [
            _raw_row("2024-01-02", 100, 40, 50, 110, 300),
            _raw_row("2024-01-09", 120, 30, 45, 120, 310),
            _raw_row("2024-01-02", 999, 999, 999, 999, 999, market="GOLD - COMEX"),
        ]
    )
    out = parse_cot_frame(raw)
    assert list(out.columns) == ["nc_net", "comm_net", "open_interest"]
    assert len(out) == 2  # the GOLD row is filtered out
    assert out.iloc[0]["nc_net"] == 60  # 100 - 40
    assert out.iloc[0]["comm_net"] == -60  # 50 - 110


def test_parse_rejects_missing_columns_and_empty_market() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        parse_cot_frame(pd.DataFrame({"As of Date in Form YYYY-MM-DD": ["2024-01-02"]}))
    full = pd.DataFrame([_raw_row("2024-01-02", 1, 1, 1, 1, 1)])
    with pytest.raises(ValueError, match="no rows for market"):
        parse_cot_frame(full, market="NONEXISTENT")


def test_cot_index_locates_position_in_range() -> None:
    assert cot_index([0.0] * 155, window=156) is None  # insufficient history
    rising = list(range(156))  # 0..155, latest is the max
    assert cot_index(rising, window=156) == pytest.approx(100.0)
    falling = list(range(156, 0, -1))  # latest is the min
    assert cot_index(falling, window=156) == pytest.approx(0.0)
    assert cot_index([5.0] * 156, window=156) == 50.0  # flat window -> mid


def test_signal_is_contrarian_at_extremes() -> None:
    as_of = date(2024, 6, 1)
    crowded_long = [*range(155), 1000]  # latest far above the trailing range -> index 100
    sig = cot_extreme_signal(as_of, crowded_long, window=156)
    assert sig is not None and sig.direction == "short"  # contrarian: crowded long -> short

    crowded_short = [*range(155, 0, -1), -1000]  # latest far below -> index 0
    sig2 = cot_extreme_signal(as_of, crowded_short, window=156)
    assert sig2 is not None and sig2.direction == "long"

    middling = [*range(155), 78]  # range 0..154, latest 78 -> index ~51 (neither extreme)
    assert cot_extreme_signal(as_of, middling, window=156) is None
