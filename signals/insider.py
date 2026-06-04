"""Insider open-market buying signal (SEC Form 4).

The documented edge is a CEO/CFO/Chairman spending real cash on the open market (transaction code
``P``) — and, more so, a *cluster* of independent insiders doing it at once. Grants, option
exercises, and sales (A/M/F/S) are net-zero noise and are excluded by default. Insider *selling*
is also excluded: it is famously uninformative (diversification, taxes, scheduled 10b5-1 plans).

This is a STANDALONE signal. It is deliberately NOT wired into the compounder ``_WEIGHTS`` (which is
a validated SCREEN, not a return-predictor): per the project's validate-before-trust rule, no input
earns a portfolio weight until an out-of-sample IC check confirms it. The role weights below are
heuristic v1 priors (Cohen-Malloy-Pomorski 2012: officers > 10%-owners, whose Form 4s are often
mechanical index/activist crossings) and will be recalibrated against that check.

Point-in-time: the catalog's ``get_insider_trades(symbol, as_of=...)`` is the PRIMARY PIT guard
(double-timestamp + amendment supersession). The guard here is defense-in-depth so the signal is
correct even if a caller forgets to pass ``as_of`` to the catalog. ``as_of`` accepts a ``datetime``
for intraday precision (a 09:30 cutoff will not see a filing accepted at 23:58 that night); a bare
``date`` is treated as the close of that day (same-day filings visible).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from types import MappingProxyType

from data.models import InsiderTradeRecord
from strategies._base import StrategySignal

OPEN_MARKET_BUY = "P"

# Role conviction multipliers, matched as case-insensitive substrings of the free-text Form 4 role.
# HEURISTIC v1 priors — see the module docstring. The highest matching weight wins. Exposed as a
# read-only proxy so a caller cannot silently corrupt the shared default by item-assignment.
DEFAULT_ROLE_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "chief executive": 1.0,
        "ceo": 1.0,
        "chief financial": 1.0,
        "cfo": 1.0,
        "chairman": 0.9,
        "chair": 0.9,
        "president": 0.9,
        "chief": 0.9,  # other C-suite (COO/CTO/...)
        "coo": 0.9,
        "director": 0.5,
        "ten percent": 0.25,
        "10% owner": 0.25,
    }
)
_OTHER_OFFICER_WEIGHT = 0.6  # a non-empty title that is not a director/10%-owner is some officer
_DEFAULT_WEIGHT = 0.5  # empty/unknown role: a buy is still a buy, weighted neutrally
# A "vice president" / [E/S/G]VP is a mid-level officer, NOT the company President — but the literal
# "president" substring would otherwise borrow C-suite weight. Matched to demote that artifact.
_VICE_PRESIDENT = re.compile(r"vice president|\b[esg]?vp\b")


def _role_weight(role: str, weights: Mapping[str, float]) -> float:
    r = role.strip().lower()
    if not r:
        return _DEFAULT_WEIGHT
    matched = [w for key, w in weights.items() if key in r]
    if matched:
        if _VICE_PRESIDENT.search(r):
            # Drop the bare "president" artifact; keep any genuine C-suite token (e.g. "EVP & CFO").
            non_president = [w for key, w in weights.items() if key != "president" and key in r]
            return max(non_president) if non_president else _OTHER_OFFICER_WEIGHT
        return max(matched)
    if "owner" in r:  # any "...% owner" phrasing not already keyed
        return weights.get("10% owner", 0.25)
    return _OTHER_OFFICER_WEIGHT


def _as_cutoff(value: date) -> datetime:
    """Normalize an ``as_of`` to a precise datetime cutoff. ``datetime`` is kept verbatim (intraday
    precision); a bare ``date`` becomes the end of that day so same-day filings are visible."""
    if isinstance(value, datetime):
        return value
    return datetime(value.year, value.month, value.day, 23, 59, 59, 999999)


def _notional(record: InsiderTradeRecord) -> float | None:
    """Dollar value of a buy: the reported ``value_usd``, else reconstructed from shares*price.
    Returns ``None`` only when neither is available (a genuinely uncovered record)."""
    if record.value_usd is not None:
        return max(record.value_usd, 0.0)
    if record.shares is not None and record.price is not None:
        return max(record.shares * record.price, 0.0)
    return None


def insider_buying_signal(
    records: list[InsiderTradeRecord],
    *,
    as_of: date | None = None,
    lookback_days: int = 90,
    min_buyers: int = 1,
    codes: tuple[str, ...] = (OPEN_MARKET_BUY,),
    role_weights: Mapping[str, float] | None = None,
) -> StrategySignal | None:
    """Score recent open-market insider buying for one symbol. Returns a long ``StrategySignal`` or
    ``None`` when no qualifying cluster is visible in the lookback window.

    The lookback window is measured on the DISCLOSURE date (``asof_ts``) — the moment the buy
    becomes actionable — so every record scored is, by construction, already public as of ``as_of``.

    ``as_of`` defaults to the most recently DISCLOSED buy in ``records`` (relative recency). This
    keeps the function pure (no wall-clock read), but means an ad-hoc "is there buying *now*?" query
    over a STALE record set yields a stale signal. Production callers should pass an explicit
    ``as_of`` (e.g. today); the returned ``StrategySignal.as_of`` always exposes the anchor used, so
    staleness is inspectable downstream.
    """
    if lookback_days < 0:
        raise ValueError(f"lookback_days must be >= 0, got {lookback_days}")
    weights = role_weights if role_weights is not None else DEFAULT_ROLE_WEIGHTS
    buys = [r for r in records if r.txn_code in codes]
    if not buys:
        return None

    # Backtests pass an explicit cutoff; ad-hoc callers default to the most recently DISCLOSED buy
    # (full-precision asof_ts — never truncated to a date, which would leak same-day filings).
    cutoff = max(r.asof_ts for r in buys) if as_of is None else _as_cutoff(as_of)
    window_start = (cutoff - timedelta(days=lookback_days)).date()

    visible = [
        r
        for r in buys
        # defense-in-depth PIT: filing public at full precision (asof_ts <= cutoff), trade not
        # future-dated (txn_date <= cutoff date), and disclosed within the lookback window.
        if r.asof_ts <= cutoff and r.txn_date <= cutoff.date() and r.asof_ts.date() >= window_start
    ]
    if not visible:
        return None

    buyers = {r.insider_name for r in visible if r.insider_name}
    # Distinct-buyer cluster gate. With no names at all we cannot tell individuals apart, so count a
    # conservative 1 (never len(visible), which would count transactions and let one anonymous
    # filer bypass min_buyers).
    n_buyers = len(buyers) if buyers else 1
    if n_buyers < min_buyers:
        return None

    notionals = [_notional(r) for r in visible]
    n_valued = sum(1 for v in notionals if v is not None and v > 0)
    dollar_score = sum(
        v * _role_weight(r.insider_role, weights)
        for r, v in zip(visible, notionals, strict=True)
        if v is not None
    )
    if dollar_score > 0:
        score = dollar_score
        # Disclose partial coverage so a $-cluster missing some notionals is not ranked as if it
        # had full dollar data (it would otherwise tie a smaller, fully-covered cluster).
        basis = "$" if n_valued == len(visible) else f"$/partial({n_valued}/{len(visible)})"
    else:
        # No usable dollar value on any buy (rare for modern Form 4s). Fall back to a role-weighted
        # COUNT so the cluster still surfaces; flag the unit switch so it is not ranked $-vs-count.
        score = sum(_role_weight(r.insider_role, weights) for r in visible)
        basis = "count"

    latest = max(visible, key=lambda r: r.asof_ts)
    reason = (
        f"insider buys: {len(visible)} by {n_buyers} insider(s) in {lookback_days}d, "
        f"{basis}-weighted {score:,.0f}"
    )
    return StrategySignal(
        symbol=latest.symbol,
        market=latest.market,
        as_of=cutoff.date(),
        score=score,
        direction="long",
        reason=reason,
    )
