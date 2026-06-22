"""Momentum portfolio-construction primitives (extracted verbatim from the validated walk-forward).

These three functions build the IDEAL / AQR momentum sleeve's per-name weights and are the SAME logic
the validated `aqr_top7_cap20_trail10_pit110` walk-forward and the deployed paper-drill use. They were
extracted here UNCHANGED (no behavior change) so `engine/momentum_basket.py` can reuse them without a
scripts->engine back-import and without re-deriving the weighting (which would risk diverging from the
validated config). `scripts/aqr_ideal_walkforward.py` now imports them from here; `scripts/paper_drill.py`
keeps its own independent copy by design (deferred dedupe).

`prices` is a pandas DataFrame (columns = symbols, DatetimeIndex), as produced by
`data.price_snapshot.read_price_snapshot`. PIT is the caller's job: every slice here is `.loc[:end]`.
"""

from __future__ import annotations

import math

from data.models import PriceBar


def build_pricebars(prices, symbol, end, lookback_bars=260):
    if symbol not in prices.columns:
        return []
    s = prices[symbol].loc[:end].dropna().tail(lookback_bars)
    if len(s) < lookback_bars:
        return []
    return [
        PriceBar(
            symbol=symbol,
            market="us",
            source_symbol=symbol,
            freq="1d",
            ts=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
            open=float(v),
            high=float(v),
            low=float(v),
            close=float(v),
            volume=0.0,
            currency="USD",
            source="yfinance",
        )
        for ts, v in s.items()
    ]


def vol_estimate(prices, symbol, end, window=63):
    if symbol not in prices.columns:
        return 0.30
    r = prices[symbol].loc[:end].pct_change().dropna().tail(window)
    if len(r) < window // 2:
        return 0.30
    return max(float(r.std()) * math.sqrt(252.0), 0.05)


def weights_from_picks(picks, prices, rebal, cap=0.20):
    n = len(picks)
    if n == 0:
        return {}
    total_cap = n * cap
    if total_cap < 1.0 - 1e-6:
        raise ValueError(
            f"infeasible cap: {n} names x cap {cap:.4f} = {total_cap:.4f} < 1.0; cannot fully "
            f"invest within the per-symbol cap (raise cap to >= {1.0 / n:.4f} or hold at least "
            f"{math.ceil(1.0 / cap)} names)."
        )
    # Cap binds for every name (e.g. top5: 5 * 0.20 ≈ 1.0) → equal weight is the unique feasible
    # split and exactly respects the cap. Mirrors paper_drill.weights_from_picks so the model-gate
    # report validates the SAME portfolio the order generator builds (Codex P1). 1e-6 tol catches
    # this module's feasibility-padded cap = max(0.20, 1/n + 1e-9).
    if total_cap <= 1.0 + 1e-6:
        return {p.symbol: 1.0 / n for p in picks}
    raw = {p.symbol: 1.0 / vol_estimate(prices, p.symbol, rebal) for p in picks}
    for _ in range(10):
        total = sum(raw.values())
        if total <= 0:
            return {}
        w = {s: x / total for s, x in raw.items()}
        over = {s: x for s, x in w.items() if x > cap}
        if not over:
            return w
        excess = sum(x - cap for x in over.values())
        free = [s for s in w if s not in over]
        for s in over:
            raw[s] = cap * total
        if free:
            ft = sum(raw[s] for s in free)
            if ft > 0:
                for s in free:
                    raw[s] *= (ft + excess * total) / ft
    return {s: x / sum(raw.values()) for s, x in raw.items()}
