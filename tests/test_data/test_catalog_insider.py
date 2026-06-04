from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from data.catalog import MarketDataCatalog
from data.models import InsiderTradeRecord


def _rec(
    *,
    txn_date: date,
    asof_ts: datetime,
    name: str = "COOK TIMOTHY D",
    role: str = "Chief Executive Officer",
    shares: float = 1000.0,
    price: float = 150.0,
) -> InsiderTradeRecord:
    return InsiderTradeRecord(
        symbol="AAPL",
        market="us",
        txn_date=txn_date,
        asof_ts=asof_ts,
        insider_name=name,
        insider_role=role,
        txn_code="P",
        shares=shares,
        price=price,
        value_usd=shares * price,
        source="sec:form4",
    )


def test_round_trip(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    rec = _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 3, tzinfo=UTC))
    assert cat.put_insider_trades([rec]) == 1
    out = cat.get_insider_trades("AAPL")
    assert len(out) == 1
    assert out[0].insider_name == "COOK TIMOTHY D"
    assert out[0].txn_code == "P"
    assert out[0].value_usd == 150_000.0


def test_idempotent_upsert(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    rec = _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 3, tzinfo=UTC))
    cat.put_insider_trades([rec])
    cat.put_insider_trades([rec])  # same key -> one row
    assert len(cat.get_insider_trades("AAPL")) == 1


def test_double_pit_guard_disclosure_lag(tmp_path) -> None:
    """A trade is invisible until its SEC filing is accepted (asof_ts <= as_of)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_insider_trades(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 3, tzinfo=UTC))]
    )
    # filed 03-03; as-of 03-02 must NOT see it (disclosure not yet public)
    assert cat.get_insider_trades("AAPL", as_of=datetime(2025, 3, 2, tzinfo=UTC)) == []
    # as-of 03-04 sees it
    assert len(cat.get_insider_trades("AAPL", as_of=datetime(2025, 3, 4, tzinfo=UTC))) == 1


def test_double_pit_guard_txn_date_predicate(tmp_path) -> None:
    """Both predicates must hold: a trade dated after as_of is filtered even if its filing
    timestamp is already <= as_of (defensive against odd txn/asof orderings)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_insider_trades(
        [_rec(txn_date=date(2025, 3, 15), asof_ts=datetime(2025, 3, 10, tzinfo=UTC))]
    )
    # asof_ts (03-10) <= 03-12, but txn_date (03-15) > 03-12 -> filtered
    assert cat.get_insider_trades("AAPL", as_of=datetime(2025, 3, 12, tzinfo=UTC)) == []
    assert len(cat.get_insider_trades("AAPL", as_of=datetime(2025, 3, 20, tzinfo=UTC))) == 1


def test_returns_cluster_newest_first(tmp_path) -> None:
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_insider_trades(
        [
            _rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 3, tzinfo=UTC), name="A"),
            _rec(txn_date=date(2025, 3, 5), asof_ts=datetime(2025, 3, 7, tzinfo=UTC), name="B"),
        ]
    )
    out = cat.get_insider_trades("AAPL")  # default returns the whole visible cluster
    assert [r.insider_name for r in out] == ["B", "A"]  # newest asof_ts first


def test_aggregates_split_executions(tmp_path) -> None:
    """Several transactions by one insider, same day/code/filing (split executions at different
    prices) aggregate into ONE row (total shares + value) — no unique-key collision."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    asof = datetime(2025, 3, 3, tzinfo=UTC)
    cat.put_insider_trades(
        [
            _rec(txn_date=date(2025, 3, 1), asof_ts=asof, shares=1000, price=150.0),
            _rec(txn_date=date(2025, 3, 1), asof_ts=asof, shares=500, price=152.0),
        ]
    )
    out = cat.get_insider_trades("AAPL")
    assert len(out) == 1
    assert out[0].shares == 1500.0
    assert out[0].value_usd == 1000 * 150.0 + 500 * 152.0
    assert out[0].price == pytest.approx((1000 * 150.0 + 500 * 152.0) / 1500.0)


def test_derives_value_when_value_usd_absent(tmp_path) -> None:
    """A record with shares + price but no precomputed value_usd must still round-trip its price
    (value derived from shares*price) — value_usd is optional in the model (Codex P2)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    rec = InsiderTradeRecord(
        symbol="AAPL",
        market="us",
        txn_date=date(2025, 3, 1),
        asof_ts=datetime(2025, 3, 3, tzinfo=UTC),
        insider_name="X",
        insider_role="CEO",
        txn_code="P",
        shares=1000.0,
        price=150.0,
        value_usd=None,
        source="feed",
    )
    cat.put_insider_trades([rec])
    out = cat.get_insider_trades("AAPL")
    assert out[0].value_usd == 150_000.0
    assert out[0].price == pytest.approx(150.0)


def test_preserves_zero_dollar_price(tmp_path) -> None:
    """A legitimate $0 transaction (e.g. a grant on a non-default code path) must round-trip
    price=0.0, not collapse to None (which would mean 'missing') — Codex P2."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    rec = InsiderTradeRecord(
        symbol="AAPL",
        market="us",
        txn_date=date(2025, 3, 1),
        asof_ts=datetime(2025, 3, 3, tzinfo=UTC),
        insider_name="X",
        insider_role="Director",
        txn_code="A",
        shares=500.0,
        price=0.0,
        value_usd=0.0,
        source="feed",
    )
    cat.put_insider_trades([rec])
    out = cat.get_insider_trades("AAPL")
    assert out[0].price == 0.0
    assert out[0].value_usd == 0.0


def test_preserves_price_without_shares(tmp_path) -> None:
    """A record with a price but no shares (no weighted avg computable) keeps its supplied
    price rather than dropping it to None (Codex P3)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    rec = InsiderTradeRecord(
        symbol="AAPL",
        market="us",
        txn_date=date(2025, 3, 1),
        asof_ts=datetime(2025, 3, 3, tzinfo=UTC),
        insider_name="X",
        insider_role="Director",
        txn_code="P",
        shares=None,
        price=150.0,
        value_usd=None,
        source="feed",
    )
    cat.put_insider_trades([rec])
    out = cat.get_insider_trades("AAPL")
    assert out[0].price == 150.0
    assert out[0].shares is None


def test_amendment_supersedes_original(tmp_path) -> None:
    """A 4/A correction (later asof_ts, same transaction key) supersedes the original so the cluster
    is not double-counted; before the amendment is public, the original is still returned (Codex P2)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_insider_trades(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 3, tzinfo=UTC), shares=1000)]
    )
    cat.put_insider_trades(  # amendment: corrected to 800 shares, filed later
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 10, tzinfo=UTC), shares=800)]
    )
    out = cat.get_insider_trades("AAPL")
    assert len(out) == 1  # only the latest correction, not both
    assert out[0].shares == 800.0
    # before the amendment is public, the original is the latest visible
    early = cat.get_insider_trades("AAPL", as_of=datetime(2025, 3, 5, tzinfo=UTC))
    assert len(early) == 1
    assert early[0].shares == 1000.0


def test_utc_acceptance_time_not_shifted(tmp_path) -> None:
    """A tz-aware UTC acceptance time must store as true UTC, not shift to host-local; a naive-UTC
    as_of just before vs after the acceptance hour must gate visibility correctly (Codex P2)."""
    cat = MarketDataCatalog(tmp_path / "cat.duckdb")
    cat.put_insider_trades(
        [_rec(txn_date=date(2025, 3, 1), asof_ts=datetime(2025, 3, 3, 21, 0, tzinfo=UTC))]
    )
    assert cat.get_insider_trades("AAPL", as_of=datetime(2025, 3, 3, 20, 0)) == []
    assert len(cat.get_insider_trades("AAPL", as_of=datetime(2025, 3, 3, 22, 0))) == 1
