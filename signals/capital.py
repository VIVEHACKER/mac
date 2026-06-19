"""Net share-issuance signal (capital raises / buybacks) from point-in-time fundamentals.

The data-available proxy for "capital raising" is the change in shares outstanding. It captures the
academically robust NET-ISSUANCE anomaly (Pontiff-Woodgate 2008, Daniel-Titman 2006): the *average*
share issuer underperforms and the *average* repurchaser outperforms, so the default factor is
``score = -net_issuance`` (a buyback is bullish, dilution bearish).

Caveat — this is NOT the user's "good capital raise = sharp rise" edge. That edge is a *selective*
strategic/premium raise (a great company getting funded), the OPPOSITE of the average diluter this
proxy measures. A share-count delta cannot tell the two apart, so a LARGE raise is merely FLAGGED in
the reason for the hunt sleeve to verify later with offering-level data (8-K 1.01, 424B pricing,
investor identity). Until that richer signal exists — and an OOS/IC check — this stays STANDALONE
and is NOT injected into the compounder ``_WEIGHTS`` (which already carries a validated
``share_growth`` factor over a fixed window; this signal is the configurable-lookback event view).

Records come from ``MarketDataCatalog.get_fundamentals(symbol, as_of=..., limit=N>1)`` — that is the
primary PIT guard; the ``as_of`` filter here is defense-in-depth.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from data.models import FundamentalRecord
from strategies._base import StrategySignal

_QUARTER_DAYS = 91.3125
# A quarter-over-quarter shares step to >= 1.5x (or <= 1/1.5x) is almost certainly a stock split /
# reverse split — a share-count change with NO capital flow — so the issuance rate is not meaningful.
_SPLIT_RATIO = 1.5


def _as_cutoff(value: date) -> datetime:
    """date|datetime -> precise datetime cutoff. A bare ``date`` is the end of that day (same-day
    filings visible); a ``datetime`` keeps intraday precision (a later same-day filing stays
    invisible). Mirrors the insider signal so PIT semantics are identical across signals."""
    if isinstance(value, datetime):
        return value
    return datetime(value.year, value.month, value.day, 23, 59, 59, 999999)


def net_issuance_signal(
    records: list[FundamentalRecord],
    *,
    as_of: date | None = None,
    lookback_quarters: int = 4,
    min_change: float = 0.01,
    large_raise: float = 0.10,
) -> StrategySignal | None:
    """Net share issuance for one symbol over ~``lookback_quarters``. Returns a ``StrategySignal``
    (long on a buyback, short on dilution) or ``None`` when history is too short or the change is
    within the ``min_change`` deadband (routine option-grant drift)."""
    if lookback_quarters < 1:
        raise ValueError(f"lookback_quarters must be >= 1, got {lookback_quarters}")

    cutoff_dt = _as_cutoff(as_of) if as_of is not None else None
    valid = [r for r in records if r.shares_out is not None and r.shares_out > 0]
    if cutoff_dt is not None:
        # Full-precision PIT (defense-in-depth): a same-day-but-later filing is not yet visible.
        valid = [r for r in valid if r.asof_ts <= cutoff_dt]
    if len(valid) < 2:
        return None

    # Dedup by period_end, keeping the latest-FILED figure (a restated 10-Q/K supersedes the original
    # share count). The shares_out secondary key makes a same-asof_ts tie deterministic regardless of
    # input order. Then order the clean quarterly series.
    by_period: dict[date, FundamentalRecord] = {}
    for r in sorted(valid, key=lambda r: (r.asof_ts, r.shares_out or 0.0)):
        by_period[r.period_end] = r
    series = sorted(by_period.values(), key=lambda r: r.period_end)
    if len(series) < 2:
        return None

    latest = series[-1]
    older = series[:-1]
    target_days = lookback_quarters * _QUARTER_DAYS
    target = latest.period_end - timedelta(days=round(target_days))
    # Prefer a quarter at/before the lookback target so the window is not silently shortened; if no
    # record is old enough, fall back to the closest available (gap-robust on the far side).
    before = [r for r in older if r.period_end <= target]
    prior = min(before or older, key=lambda r: abs((r.period_end - target).days))
    span_days = (latest.period_end - prior.period_end).days
    if span_days < target_days * 0.5:
        return None  # the oldest available quarter is too recent to measure this lookback honestly

    # Split guard: a stock split / reverse split changes shares_out with no capital flow, so a large
    # step anywhere in the measured window means the endpoint counts are not comparable — suppress
    # rather than emit a fabricated +/-100%+ issuance (AAPL/NVDA/TSLA/GOOGL/AMZN all split 2020-24).
    window = series[series.index(prior) :]
    for prev, curr in zip(window, window[1:], strict=False):
        prev_sh, curr_sh = prev.shares_out, curr.shares_out
        if prev_sh and curr_sh:
            ratio = curr_sh / prev_sh
            if ratio >= _SPLIT_RATIO or ratio <= 1.0 / _SPLIT_RATIO:
                return None

    latest_sh, prior_sh = latest.shares_out, prior.shares_out
    if latest_sh is None or prior_sh is None:  # already filtered; keeps the types honest
        return None
    net_issuance = (latest_sh - prior_sh) / prior_sh
    if abs(net_issuance) < min_change:
        return None

    n_q = max(1, round(span_days / _QUARTER_DAYS))
    label = "buyback" if net_issuance < 0 else "dilution"
    flag = " [large raise — verify catalyst]" if net_issuance >= large_raise else ""
    # Anchor to the LAST disclosure actually seen (max visible asof_ts), never the evaluation cutoff
    # — that is the real freshness of the signal, so a stale fundamentals set is not misreported as
    # current. `valid` is already PIT-filtered, so this is the latest filing visible at `as_of`.
    anchor = max(r.asof_ts for r in valid).date()
    return StrategySignal(
        symbol=latest.symbol,
        market=latest.market,
        as_of=anchor,
        score=-net_issuance,  # buyback (negative issuance) -> positive/bullish, per the anomaly
        direction="long" if net_issuance < 0 else "short",
        reason=f"net issuance {net_issuance:+.1%} over {n_q}q ({label}){flag}",
    )
