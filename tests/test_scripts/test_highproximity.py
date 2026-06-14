from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.highproximity_skill import HIGH_WINDOW, _signals


def test_signals_proximity_and_momentum() -> None:
    n = HIGH_WINDOW + 30
    idx = pd.bdate_range("2019-01-01", periods=n)
    # Rises to a peak then pulls back 10% -> proximity = 0.90, momentum still positive.
    up = np.linspace(100, 200, n - 10)
    down = np.linspace(200, 180, 10)
    prices = pd.DataFrame({"AAA": np.concatenate([up, down])}, index=idx)
    sig = _signals(prices, "AAA", idx[-1])
    assert sig is not None
    raw, prox = sig
    assert prox == 0.90  # 180 / 200 high
    assert raw > 0  # still up over 126d


def test_signals_at_the_high_is_one() -> None:
    n = HIGH_WINDOW + 5
    idx = pd.bdate_range("2019-01-01", periods=n)
    prices = pd.DataFrame({"AAA": np.linspace(100, 150, n)}, index=idx)  # monotonic up
    sig = _signals(prices, "AAA", idx[-1])
    assert sig is not None and sig[1] == 1.0  # latest IS the 52w high


def test_signals_none_on_short_history() -> None:
    idx = pd.bdate_range("2020-01-01", periods=HIGH_WINDOW - 5)
    prices = pd.DataFrame({"AAA": np.linspace(100, 110, len(idx))}, index=idx)
    assert _signals(prices, "AAA", idx[-1]) is None
