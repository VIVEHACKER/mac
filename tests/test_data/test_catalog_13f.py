from __future__ import annotations

from datetime import UTC, date, datetime

from data.catalog import MarketDataCatalog
from data.models import InstitutionalHoldingRecord


def _holding(
    *,
    symbol: str = "AAPL",
    cusip: str = "037833100",
    manager: str = "BERKSHIRE HATHAWAY INC",
    report_date: date = date(2025, 3, 31),
    asof_ts: datetime = datetime(2025, 5, 15, 16, 0),
    shares: float | None = 5000.0,
    value_usd: float | None = 1_000_000.0,
    put_call: str = "",
    market: str = "us",
) -> InstitutionalHoldingRecord:
    return InstitutionalHoldingRecord(
        symbol=symbol,
        market=market,
        cusip=cusip,
        issuer_name="APPLE INC",
        manager=manager,
        report_date=report_date,
        asof_ts=asof_ts,
        shares=shares,
        value_usd=value_usd,
        put_call=put_call,
        source="sec:13f",
    )


def test_put_get_roundtrip(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    assert cat.put_institutional_holdings([_holding()]) == 1
    out = cat.get_institutional_holdings("AAPL")
    assert len(out) == 1
    assert out[0].manager == "BERKSHIRE HATHAWAY INC"
    assert out[0].shares == 5000.0
    assert out[0].cusip == "037833100"


def test_put_empty_returns_zero(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    assert cat.put_institutional_holdings([]) == 0


def test_pit_hides_filing_before_acceptance(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_institutional_holdings([_holding(asof_ts=datetime(2025, 5, 15, 16, 0, tzinfo=UTC))])
    # Not visible the day before the SEC accepted the filing...
    assert cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 5, 14, tzinfo=UTC)) == []
    # ...nor earlier the SAME day, before the 16:00 acceptance (pins TIMESTAMP, not DATE, precision)...
    assert cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 5, 15, 10, tzinfo=UTC)) == []
    # ...visible same day after acceptance, and the day after.
    assert (
        len(cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 5, 15, 17, tzinfo=UTC)))
        == 1
    )
    assert len(cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 5, 16, tzinfo=UTC))) == 1


def test_amendment_supersedes_original_and_respects_pit(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    original = _holding(asof_ts=datetime(2025, 5, 15, 16, 0), shares=5000.0)
    amended = _holding(asof_ts=datetime(2025, 5, 20, 16, 0), shares=6000.0)  # 13F-HR/A restatement
    cat.put_institutional_holdings([original, amended])

    # After both are public: only the latest filing for the (manager, cusip, quarter) survives.
    latest = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert len(latest) == 1
    assert latest[0].shares == 6000.0

    # Between the two filings: only the original is visible (PIT).
    mid = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 5, 17, tzinfo=UTC))
    assert len(mid) == 1
    assert mid[0].shares == 5000.0


def test_aggregates_split_rows_in_one_filing(tmp_path) -> None:
    # A 13F can split one manager's position for an issuer across sub-rows (otherManager
    # allocations). They are one economic position — sum shares and value.
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_institutional_holdings(
        [
            _holding(shares=3000.0, value_usd=600_000.0),
            _holding(shares=2000.0, value_usd=400_000.0),
        ]
    )
    out = cat.get_institutional_holdings("AAPL")
    assert len(out) == 1
    assert out[0].shares == 5000.0
    assert out[0].value_usd == 1_000_000.0


def test_share_and_option_lines_kept_separate(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_institutional_holdings(
        [
            _holding(put_call="", shares=5000.0),
            _holding(put_call="Call", shares=1000.0),
        ]
    )
    out = cat.get_institutional_holdings("AAPL")
    assert {r.put_call for r in out} == {"", "Call"}


def test_multiple_managers_same_symbol(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_institutional_holdings(
        [
            _holding(manager="BERKSHIRE HATHAWAY INC", shares=5000.0),
            _holding(manager="SCION ASSET MANAGEMENT", shares=1000.0),
        ]
    )
    out = cat.get_institutional_holdings("AAPL")
    assert {r.manager for r in out} == {"BERKSHIRE HATHAWAY INC", "SCION ASSET MANAGEMENT"}


def test_get_unknown_symbol_empty(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_institutional_holdings([_holding()])
    assert cat.get_institutional_holdings("NVDA") == []


def test_holdings_separated_by_market(tmp_path) -> None:
    # market is part of the identity (mirrors insider_trades): same manager/cusip/quarter/filing in
    # two markets must NOT collapse-and-sum into one row.
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_institutional_holdings(
        [_holding(market="us", shares=5000.0), _holding(market="lse", shares=3000.0)]
    )
    us = cat.get_institutional_holdings("AAPL", market="us")
    lse = cat.get_institutional_holdings("AAPL", market="lse")
    assert len(us) == 1 and us[0].shares == 5000.0
    assert len(lse) == 1 and lse[0].shares == 3000.0


def test_put_is_idempotent_on_reingest(tmp_path) -> None:
    # Re-putting a filing's complete row set must REPLACE, not accumulate. DELETE-then-INSERT makes
    # re-running an ingest safe (no doubling). The contract: pass all sub-rows of a filing in ONE
    # put call (the ingest parses a filing into a list, then puts once).
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_institutional_holdings([_holding(shares=5000.0, value_usd=1_000_000.0)])
    cat.put_institutional_holdings([_holding(shares=5000.0, value_usd=1_000_000.0)])  # re-ingest
    out = cat.get_institutional_holdings("AAPL")
    assert len(out) == 1
    assert out[0].shares == 5000.0  # not 10000 — idempotent, not accumulated


def test_amendment_tombstone_closes_position_pit_safe(tmp_path) -> None:
    # PIT-preserving exit: an amended filing that drops a CUSIP is ingested as a None-shares
    # tombstone (later asof_ts), which supersedes the original WITHOUT deleting it — so a query
    # between the two filings still sees the original position.
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    original = _holding(asof_ts=datetime(2025, 5, 15, 16, 0), shares=5000.0, value_usd=1_000_000.0)
    tombstone = _holding(asof_ts=datetime(2025, 5, 20, 16, 0), shares=None, value_usd=None)
    cat.put_institutional_holdings([original, tombstone])

    closed = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 6, 1, tzinfo=UTC))
    assert len(closed) == 1
    assert closed[0].shares is None  # latest filing reports no position → exited

    # PIT preserved: before the amendment, the original 5000-share position is still visible.
    before = cat.get_institutional_holdings("AAPL", as_of=datetime(2025, 5, 17, tzinfo=UTC))
    assert len(before) == 1 and before[0].shares == 5000.0
