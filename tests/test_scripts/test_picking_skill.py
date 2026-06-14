from __future__ import annotations

import pandas as pd
import pytest

from scripts.picking_skill import _fwd_return


def test_fwd_return_basic_and_guards() -> None:
    idx = pd.to_datetime(["2020-01-02", "2020-02-03"])
    prices = pd.DataFrame({"AAA": [100.0, 110.0], "BBB": [50.0, float("nan")]}, index=idx)
    assert _fwd_return(prices, "AAA", idx[0], idx[1]) == pytest.approx(0.10)
    assert _fwd_return(prices, "BBB", idx[0], idx[1]) is None  # NaN forward price -> None
    assert _fwd_return(prices, "ZZZ", idx[0], idx[1]) is None  # missing symbol -> None
