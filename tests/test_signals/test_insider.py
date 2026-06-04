from __future__ import annotations

from datetime import date, datetime

import pytest

from data.models import InsiderTradeRecord
from signals.insider import DEFAULT_ROLE_WEIGHTS, insider_buying_signal


def _rec(
    *,
    txn_date: date,
    asof_ts: datetime,
    role: str = "CEO",
    code: str = "P",
    value_usd: float | None = 1_000_000.0,
    name: str = "COOK TIMOTHY D",
    symbol: str = "AAPL",
    shares: float | None = None,
    price: float | None = None,
) -> InsiderTradeRecord:
    return InsiderTradeRecord(
        symbol=symbol,
        market="us",
        txn_date=txn_date,
        asof_ts=asof_ts,
        insider_name=name,
        insider_role=role,
        txn_code=code,
        shares=shares,
        price=price,
        value_usd=value_usd,
        source="sec:form4",
    )


def test_returns_none_on_empty() -> None:
    assert insider_buying_signal([]) is None


def test_returns_none_when_no_open_market_buys() -> None:
    # Grants (A), option exercises (M), sales (S) are not conviction open-market buys.
    recs = [
        _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), code=code)
        for code in ("A", "M", "S")
    ]
    assert insider_buying_signal(recs) is None


def test_single_ceo_buy_fires_long_with_dollar_score() -> None:
    sig = insider_buying_signal(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), role="CEO")]
    )
    assert sig is not None
    assert sig.direction == "long"
    assert sig.symbol == "AAPL"
    assert sig.market == "us"
    assert sig.score == 1_000_000.0  # value_usd * CEO weight 1.0


def test_ceo_buy_scores_higher_than_director_for_same_dollars() -> None:
    ceo = insider_buying_signal(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), role="CEO")]
    )
    director = insider_buying_signal(
        [
            _rec(
                txn_date=date(2025, 3, 1),
                asof_ts=datetime(2025, 3, 2),
                role="Director",
                name="SOME DIRECTOR",
            )
        ]
    )
    assert ceo is not None and director is not None
    assert ceo.score > director.score


def test_ten_percent_owner_weighted_below_director() -> None:
    # A 10%-owner Form 4 is often a mechanical index/activist crossing, not a conviction buy.
    owner = insider_buying_signal(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), role="10% owner")]
    )
    director = insider_buying_signal(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), role="Director")]
    )
    assert owner is not None and director is not None
    assert owner.score < director.score


def test_cluster_gate_requires_min_distinct_buyers() -> None:
    one_buyer = [
        _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), name="COOK TIMOTHY D"),
        _rec(txn_date=date(2025, 3, 3), asof_ts=datetime(2025, 3, 4), name="COOK TIMOTHY D"),
    ]
    assert insider_buying_signal(one_buyer, min_buyers=2) is None

    two_buyers = [
        _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), name="COOK TIMOTHY D"),
        _rec(txn_date=date(2025, 3, 3), asof_ts=datetime(2025, 3, 4), name="MAESTRI LUCA"),
    ]
    assert insider_buying_signal(two_buyers, min_buyers=2) is not None


def test_lookback_window_excludes_stale_disclosures() -> None:
    # Disclosed 200 days before the eval date; a 90d lookback must drop it.
    stale = [_rec(txn_date=date(2024, 8, 1), asof_ts=datetime(2024, 8, 2))]
    assert insider_buying_signal(stale, as_of=date(2025, 3, 1), lookback_days=90) is None


def test_pit_excludes_disclosures_not_yet_public() -> None:
    # The filing was accepted AFTER the eval date — invisible at that point in time.
    future = [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 10))]
    assert insider_buying_signal(future, as_of=date(2025, 3, 5)) is None


def test_score_aggregates_multiple_buys_in_window() -> None:
    recs = [
        _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), value_usd=1_000_000.0),
        _rec(
            txn_date=date(2025, 3, 5),
            asof_ts=datetime(2025, 3, 6),
            value_usd=500_000.0,
            name="MAESTRI LUCA",
        ),
    ]
    sig = insider_buying_signal(recs, as_of=date(2025, 3, 10))
    assert sig is not None
    assert sig.score == 1_500_000.0  # both CEO-weighted (1.0), summed


def test_count_fallback_when_no_dollar_value() -> None:
    # Rare malformed/partial Form 4 with no usable dollar figure: the cluster must still surface,
    # via a role-weighted COUNT, and the reason must flag the unit switch (not $-comparable).
    recs = [
        _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), value_usd=None),
        _rec(
            txn_date=date(2025, 3, 3),
            asof_ts=datetime(2025, 3, 4),
            value_usd=None,
            name="MAESTRI LUCA",
        ),
    ]
    sig = insider_buying_signal(recs, as_of=date(2025, 3, 10))
    assert sig is not None
    assert sig.score == 2.0  # two CEO-weighted (1.0) buys, count basis
    assert "count" in sig.reason


def test_default_as_of_anchors_to_latest_disclosure() -> None:
    recs = [
        _rec(txn_date=date(2024, 8, 1), asof_ts=datetime(2024, 8, 2), value_usd=999.0),  # stale
        _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), value_usd=1_000_000.0),
    ]
    sig = insider_buying_signal(recs, lookback_days=90)  # as_of defaults to latest disclosure
    assert sig is not None
    assert sig.score == 1_000_000.0  # only the recent buy is within 90d of the latest disclosure


def test_unknown_role_still_fires_with_default_weight() -> None:
    sig = insider_buying_signal(
        [
            _rec(
                txn_date=date(2025, 3, 1),
                asof_ts=datetime(2025, 3, 2),
                role="",
                value_usd=2_000_000.0,
            )
        ]
    )
    assert sig is not None
    assert 0.0 < sig.score < 2_000_000.0  # neutral default weight (<1.0), but a buy is still a buy


def test_other_officer_title_weighted_above_director() -> None:
    # A non-C-suite officer title (e.g. EVP) is still an officer — outrank a plain director.
    evp = insider_buying_signal(
        [
            _rec(
                txn_date=date(2025, 3, 1),
                asof_ts=datetime(2025, 3, 2),
                role="Executive Vice President",
            )
        ]
    )
    director = insider_buying_signal(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), role="Director")]
    )
    assert evp is not None and director is not None
    assert evp.score > director.score


def test_pit_excludes_same_day_after_hours_filing_at_intraday_cutoff() -> None:
    # A datetime cutoff (e.g. market open 09:30) must NOT see a filing accepted at 23:58 the same
    # night. Truncating asof_ts to a date would leak it forward — the bug this guards against.
    after_hours = [_rec(txn_date=date(2025, 3, 4), asof_ts=datetime(2025, 3, 5, 23, 58))]
    assert insider_buying_signal(after_hours, as_of=datetime(2025, 3, 5, 9, 30)) is None
    # ...but a pre-open filing at 08:45 the same morning IS visible at the 09:30 cutoff.
    pre_open = [_rec(txn_date=date(2025, 3, 4), asof_ts=datetime(2025, 3, 5, 8, 45))]
    assert insider_buying_signal(pre_open, as_of=datetime(2025, 3, 5, 9, 30)) is not None


def test_date_as_of_is_end_of_day_inclusive() -> None:
    # A bare date cutoff means "as of the close of that day" — same-day filings are visible.
    same_day = [_rec(txn_date=date(2025, 3, 5), asof_ts=datetime(2025, 3, 5, 23, 58))]
    assert insider_buying_signal(same_day, as_of=date(2025, 3, 5)) is not None


def test_vice_president_weighted_below_actual_president() -> None:
    # "Vice President" contains the substring "president" but is a mid-level officer, NOT the
    # company President. It must not borrow C-suite weight.
    vp = insider_buying_signal(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), role="Vice President")]
    )
    president = insider_buying_signal(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), role="President")]
    )
    assert vp is not None and president is not None
    assert vp.score < president.score


def test_evp_cfo_retains_cfo_weight_despite_vp_demotion() -> None:
    # The VP demotion must not clobber a genuine C-suite token in a compound title.
    evp_cfo = insider_buying_signal(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), role="EVP & CFO")]
    )
    cfo = insider_buying_signal(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), role="CFO")]
    )
    assert evp_cfo is not None and cfo is not None
    assert evp_cfo.score == cfo.score


def test_empty_name_fallback_does_not_over_count_buyers() -> None:
    # Two anonymous (name-stripped) buys must not be read as a 2-person cluster.
    recs = [
        _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), name="", value_usd=500_000.0),
        _rec(txn_date=date(2025, 3, 3), asof_ts=datetime(2025, 3, 4), name="", value_usd=500_000.0),
    ]
    assert insider_buying_signal(recs, min_buyers=2) is None
    assert insider_buying_signal(recs, min_buyers=1) is not None


def test_mixed_dollar_coverage_flags_partial() -> None:
    # One known notional + one truly unknown (no value, no shares/price): scored on $ but the
    # reason must disclose the coverage gap so it is not silently ranked as a full-dollar cluster.
    recs = [
        _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2), value_usd=1_000_000.0),
        _rec(
            txn_date=date(2025, 3, 3),
            asof_ts=datetime(2025, 3, 4),
            name="MAESTRI LUCA",
            value_usd=None,
            shares=None,
            price=None,
        ),
    ]
    sig = insider_buying_signal(recs, as_of=date(2025, 3, 10))
    assert sig is not None
    assert "partial" in sig.reason


def test_notional_reconstructed_from_shares_and_price() -> None:
    # value_usd missing but shares & price present → reconstruct shares*price, full $ basis.
    recs = [
        _rec(
            txn_date=date(2025, 3, 1),
            asof_ts=datetime(2025, 3, 2),
            value_usd=None,
            shares=1000.0,
            price=150.0,
        )
    ]
    sig = insider_buying_signal(recs, as_of=date(2025, 3, 10))
    assert sig is not None
    assert sig.score == 150_000.0  # 1000 * 150 * CEO weight 1.0
    assert "partial" not in sig.reason


def test_default_role_weights_is_immutable() -> None:
    with pytest.raises(TypeError):
        DEFAULT_ROLE_WEIGHTS["ceo"] = 0.0  # type: ignore[index]


def test_negative_lookback_raises() -> None:
    with pytest.raises(ValueError, match="lookback_days"):
        insider_buying_signal(
            [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 2))],
            lookback_days=-1,
        )
