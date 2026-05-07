from __future__ import annotations

from datetime import date, timedelta

import pytest

from valuation.option_vol import (
    OptionQuote,
    calculate_vix_like_index,
    load_option_quotes_csv,
    validate_option_chain,
)


def test_calculate_vix_like_index_uses_near_and_next_expirations() -> None:
    asof = date(2026, 5, 8)
    quotes = [*_chain(asof + timedelta(days=20)), *_chain(asof + timedelta(days=40), scale=1.1)]

    result = calculate_vix_like_index(
        quotes,
        asof_date=asof,
        target_days=30,
        risk_free_rate=0.04,
    )

    assert result.near.days_to_expiration == 20
    assert result.next.days_to_expiration == 40
    assert result.near.reference_strike == 100
    assert 0.1 < result.volatility < 1.0
    assert result.risk_free_rate == 0.04
    assert result.source == "option-chain:vix-formula"


def test_option_quote_csv_loader_accepts_expiry_alias(tmp_path) -> None:
    path = tmp_path / "chain.csv"
    path.write_text(
        "\n".join(
            [
                "expiry,strike,call_bid,call_ask,put_bid,put_ask",
                "2026-06-19,100,3.9,4.1,3.8,4.0",
            ]
        ),
        encoding="utf-8",
    )

    rows = load_option_quotes_csv(path)

    assert rows == [
        OptionQuote(
            expiration=date(2026, 6, 19),
            strike=100,
            call_bid=3.9,
            call_ask=4.1,
            put_bid=3.8,
            put_ask=4.0,
        )
    ]


def test_calculate_vix_like_index_rejects_empty_chain() -> None:
    with pytest.raises(ValueError, match="no option quotes"):
        calculate_vix_like_index([], asof_date=date(2026, 5, 8))


def test_validate_option_chain_warns_when_term_structure_is_missing() -> None:
    asof = date(2026, 5, 8)

    quality = validate_option_chain(_chain(asof + timedelta(days=20)), asof_date=asof)

    assert quality.errors == ()
    assert "only one expiration" in quality.warnings[0]


def test_validate_option_chain_warns_on_stale_and_wide_quotes() -> None:
    asof = date(2026, 5, 8)
    quotes = [
        OptionQuote(
            expiration=asof + timedelta(days=20),
            strike=100,
            call_bid=1,
            call_ask=3,
            put_bid=1,
            put_ask=1.1,
            call_last_trade=asof - timedelta(days=10),
            put_last_trade=None,
        ),
        OptionQuote(expiration=asof + timedelta(days=20), strike=105, call_bid=1, call_ask=1.1, put_bid=1, put_ask=1.1),
        OptionQuote(expiration=asof + timedelta(days=20), strike=110, call_bid=1, call_ask=1.1, put_bid=1, put_ask=1.1),
    ]

    quality = validate_option_chain(
        quotes,
        asof_date=asof,
        max_quote_age_days=7,
        require_last_trade=True,
        max_bid_ask_spread_pct=0.5,
    )

    assert any("stale" in warning for warning in quality.warnings)
    assert any("missing last trade" in warning for warning in quality.warnings)
    assert any("bid/ask spread" in warning for warning in quality.warnings)


def _chain(expiration: date, scale: float = 1.0) -> list[OptionQuote]:
    rows = [
        (80, 20.5, 0.20),
        (90, 11.0, 0.85),
        (95, 7.0, 1.80),
        (100, 4.0, 4.00),
        (105, 2.00, 7.10),
        (110, 0.90, 11.0),
        (120, 0.25, 20.5),
    ]
    return [
        OptionQuote(
            expiration=expiration,
            strike=strike,
            call_bid=call_mid * scale * 0.98,
            call_ask=call_mid * scale * 1.02,
            put_bid=put_mid * scale * 0.98,
            put_ask=put_mid * scale * 1.02,
        )
        for strike, call_mid, put_mid in rows
    ]
