from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.aqr_ideal_walkforward import BENCHMARK
from scripts.residual_momentum_skill import LOOKBACK, _bucket, _signals


def test_signals_strips_market_beta() -> None:
    n = LOOKBACK + 40
    idx = pd.bdate_range("2019-01-01", periods=n)
    rng = np.random.default_rng(0)
    # A real market: positive drift WITH return variance (so beta is estimable).
    spy_ret = rng.normal(0.0008, 0.01, n)
    spy = 100 * np.cumprod(1 + spy_ret)
    # Pure-beta-2 name, NO alpha: big RAW momentum (rode the up market 2x) but the
    # residual (market stripped out) should be far smaller.
    name_ret = 2.0 * spy_ret + rng.normal(0, 0.002, n)
    name = 100 * np.cumprod(1 + name_ret)
    prices = pd.DataFrame({"AAA": name, BENCHMARK: spy}, index=idx)
    rebal = idx[-21]
    spy_then = prices[BENCHMARK].loc[:rebal]
    spy_mom = float(spy_then.iloc[-1]) / float(spy_then.iloc[-1 - LOOKBACK]) - 1.0
    sig = _signals(prices, "AAA", rebal, spy_mom)
    assert sig is not None
    raw, resid = sig
    assert raw > 0  # rode the up market
    assert abs(resid) < 0.5 * abs(raw)  # residual strips most of the market-driven momentum


def test_signals_none_on_short_history() -> None:
    idx = pd.bdate_range("2020-01-01", periods=LOOKBACK - 10)
    prices = pd.DataFrame(
        {"AAA": np.linspace(100, 110, len(idx)), BENCHMARK: np.linspace(100, 105, len(idx))},
        index=idx,
    )
    assert _signals(prices, "AAA", idx[-1], 0.05) is None


def test_bucket_orders_and_spreads() -> None:
    # signal perfectly ranks forward returns -> top > universe > bottom, IC = 1.
    scored = [(float(i), float(i)) for i in range(20)]
    top, uni, bot, ic = _bucket(scored)
    assert top > uni > bot
    assert ic == 1.0
