from __future__ import annotations

from datetime import date

from risk.shortability import (
    ShortAvailability,
    check_shortability,
    load_short_availability_csv,
)


def test_check_shortability_blocks_unshortable_symbol() -> None:
    result = check_shortability(
        "AAA",
        "us",
        [
            ShortAvailability(
                symbol="AAA",
                market="us",
                asof_date=date(2026, 5, 7),
                shortable=False,
                borrow_fee_bps=100,
                confidence="high",
            )
        ],
        asof_date=date(2026, 5, 8),
        require_row=True,
    )

    assert not result.passed
    assert result.reasons == ("AAA: not shortable",)


def test_check_shortability_blocks_expensive_borrow() -> None:
    result = check_shortability(
        "AAA",
        "us",
        [
            ShortAvailability(
                symbol="AAA",
                market="us",
                asof_date=date(2026, 5, 7),
                shortable=True,
                borrow_fee_bps=800,
                confidence="high",
            )
        ],
        asof_date=date(2026, 5, 8),
        max_borrow_fee_bps=500,
    )

    assert not result.passed
    assert "borrow fee" in result.reasons[0]


def test_check_shortability_blocks_stale_or_low_confidence_rows() -> None:
    result = check_shortability(
        "AAA",
        "us",
        [
            ShortAvailability(
                symbol="AAA",
                market="us",
                asof_date=date(2026, 5, 1),
                shortable=True,
                borrow_fee_bps=100,
                confidence="low",
            )
        ],
        asof_date=date(2026, 5, 8),
        max_age_days=2,
        min_confidence="medium",
    )

    assert not result.passed
    assert any("days old" in reason for reason in result.reasons)
    assert any("confidence" in reason for reason in result.reasons)


def test_load_short_availability_csv(tmp_path) -> None:
    path = tmp_path / "shortability.csv"
    path.write_text(
        "symbol,market,asof_date,shortable,borrow_fee_bps,source,confidence\n"
        "AAA,us,2026-05-07,true,125,manual,HIGH\n",
        encoding="utf-8",
    )

    rows = load_short_availability_csv(path)

    assert rows == [
        ShortAvailability(
            symbol="AAA",
            market="us",
            asof_date=date(2026, 5, 7),
            shortable=True,
            borrow_fee_bps=125,
            source="manual",
            confidence="high",
        )
    ]
