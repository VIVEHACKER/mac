from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord
from signals.capital import net_issuance_signal


def _fund(
    *,
    period_end: date,
    shares_out: float | None,
    asof_ts: datetime | None = None,
    symbol: str = "AAPL",
) -> FundamentalRecord:
    # A 10-Q is filed ~40 days after quarter-end; default asof_ts there if not given.
    if asof_ts is None:
        asof_ts = datetime(period_end.year, period_end.month, period_end.day, 0, 0)
    return FundamentalRecord(
        symbol=symbol, market="us", period_end=period_end, asof_ts=asof_ts, shares_out=shares_out
    )


def _quarters(shares: list[float], symbol: str = "AAPL") -> list[FundamentalRecord]:
    ends = [
        date(2024, 3, 31),
        date(2024, 6, 30),
        date(2024, 9, 30),
        date(2024, 12, 31),
        date(2025, 3, 31),
        date(2025, 6, 30),
    ]
    return [
        _fund(
            period_end=ends[i],
            shares_out=s,
            asof_ts=datetime(ends[i].year, ends[i].month, ends[i].day),
            symbol=symbol,
        )
        for i, s in enumerate(shares)
    ]


def test_returns_none_on_insufficient_history() -> None:
    assert net_issuance_signal(_quarters([1000, 990]), lookback_quarters=4) is None


def test_buyback_is_long_with_positive_score() -> None:
    sig = net_issuance_signal(_quarters([1000, 990, 980, 970, 960]), lookback_quarters=4)
    assert sig is not None
    assert sig.direction == "long"
    assert sig.score > 0  # -net_issuance; buybacks are bullish (net-issuance anomaly)
    assert "buyback" in sig.reason


def test_dilution_is_short_with_negative_score() -> None:
    sig = net_issuance_signal(_quarters([1000, 1010, 1020, 1030, 1050]), lookback_quarters=4)
    assert sig is not None
    assert sig.direction == "short"
    assert sig.score < 0
    assert "dilution" in sig.reason


def test_deadband_filters_option_grant_noise() -> None:
    # A sub-1% drift (routine option grants) is not a capital-allocation signal.
    assert net_issuance_signal(_quarters([1000, 1005]), lookback_quarters=1) is None


def test_large_raise_flagged_for_hunt_inspection() -> None:
    sig = net_issuance_signal(_quarters([1000, 1250]), lookback_quarters=1, large_raise=0.10)
    assert sig is not None
    assert "large raise" in sig.reason  # surfaced for the hunt sleeve to verify with offering data


def test_pit_excludes_filings_after_as_of() -> None:
    recs = _quarters([1000, 1010, 1020, 1030, 1100])
    # As of just after the 4th quarter's filing, the dilutive latest quarter is not yet visible.
    sig = net_issuance_signal(recs, as_of=date(2025, 1, 1), lookback_quarters=3)
    assert sig is not None
    # latest visible = 2024-12-31 (1030) vs 3q prior 2024-03-31 (1000) → exactly +3.0% dilution.
    # Assert the magnitude (not just direction): if the post-as_of 1,100 spike leaked, the % changes.
    assert sig.direction == "short"
    assert (
        "+3.0%" in sig.reason
    )  # locks the PIT guard — a leak of the 1,100 filing would shift this


def test_dedup_period_end_keeps_latest_asof() -> None:
    # An amended fundamental (same period_end, later asof_ts) supersedes the original share count.
    base = _quarters([1000, 1010, 1020, 1030, 1040])
    amended_latest = _fund(
        period_end=date(2025, 3, 31),
        shares_out=900.0,  # restated DOWN (buyback) — must win over the 1040 original
        asof_ts=datetime(2025, 6, 1),
    )
    sig = net_issuance_signal([*base, amended_latest], lookback_quarters=4)
    assert sig is not None
    assert sig.direction == "long"  # 900 vs 1000 = buyback, using the amended figure


def test_ignores_records_with_no_shares_out() -> None:
    recs = _quarters([1000, 1010, 1020, 1030, 1080])
    recs[2] = _fund(period_end=date(2024, 9, 30), shares_out=None)  # a gap quarter
    sig = net_issuance_signal(recs, lookback_quarters=4)
    assert sig is not None  # the None-shares quarter is dropped, the series still computes


def test_symbol_and_market_propagated() -> None:
    sig = net_issuance_signal(_quarters([1000, 1100], symbol="NVDA"), lookback_quarters=1)
    assert sig is not None
    assert sig.symbol == "NVDA"
    assert sig.market == "us"


def test_pit_intraday_datetime_excludes_same_day_later_filing() -> None:
    # A restatement accepted at 23:59 is NOT public at a 09:30 cutoff the same day — truncating the
    # datetime cutoff to a date would leak it (and let it win dedup with a wrong share count).
    recs = [
        _fund(period_end=date(2024, 3, 31), shares_out=1000.0, asof_ts=datetime(2024, 4, 15)),
        _fund(period_end=date(2024, 12, 31), shares_out=1000.0, asof_ts=datetime(2025, 1, 15)),
        _fund(
            period_end=date(2024, 12, 31), shares_out=900.0, asof_ts=datetime(2025, 3, 31, 23, 59)
        ),
    ]
    sig = net_issuance_signal(recs, as_of=datetime(2025, 3, 31, 9, 30), lookback_quarters=4)
    assert sig is None  # restatement invisible at 09:30 → 1000 vs 1000 = no change


def test_forward_split_suppresses_signal() -> None:
    # A 2-for-1 split doubles shares with no capital flow; must NOT be read as 100% dilution.
    assert (
        net_issuance_signal(_quarters([1000, 1010, 1020, 1030, 2060]), lookback_quarters=4) is None
    )


def test_reverse_split_suppresses_signal() -> None:
    # A 10-for-1 reverse split collapses shares; must NOT be read as a 90% buyback.
    recs = _quarters([10000, 10100, 10200, 10300, 1030])
    assert net_issuance_signal(recs, lookback_quarters=4) is None


def test_asof_none_anchors_to_latest_disclosure_not_latest_period() -> None:
    base = _quarters([1000, 1010, 1020, 1030, 1040])
    # A later-filed amendment of an OLDER period (asof 2025-06-01 > the newest period's asof).
    amend = _fund(period_end=date(2024, 12, 31), shares_out=1035.0, asof_ts=datetime(2025, 6, 1))
    sig = net_issuance_signal([*base, amend], lookback_quarters=4)
    assert sig is not None
    assert sig.as_of == date(2025, 6, 1)  # anchored to last disclosure, not the max-period record


def test_dedup_tie_on_equal_asof_is_deterministic() -> None:
    r1 = _fund(period_end=date(2025, 3, 31), shares_out=1040.0, asof_ts=datetime(2025, 5, 1))
    r2 = _fund(period_end=date(2025, 3, 31), shares_out=1041.0, asof_ts=datetime(2025, 5, 1))
    base = _quarters([1000, 1010, 1020, 1030])
    ab = net_issuance_signal([*base, r1, r2], lookback_quarters=4)
    ba = net_issuance_signal([*base, r2, r1], lookback_quarters=4)
    assert ab is not None and ba is not None
    assert ab.score == ba.score  # input order must not change the result


def test_prior_prefers_quarter_at_or_before_lookback_target() -> None:
    # A near-side record is closest to the target date, but a record at/before the target exists —
    # use the older one so the lookback window is not silently shortened.
    recs = [
        _fund(period_end=date(2023, 9, 30), shares_out=1000.0, asof_ts=datetime(2023, 9, 30)),
        _fund(period_end=date(2024, 6, 15), shares_out=1200.0, asof_ts=datetime(2024, 6, 15)),
        _fund(period_end=date(2025, 3, 31), shares_out=1100.0, asof_ts=datetime(2025, 3, 31)),
    ]
    sig = net_issuance_signal(recs, lookback_quarters=4)
    assert sig is not None
    assert sig.direction == "short"  # uses 1000 (before target) → +10% dilution, not 1200 near-side
