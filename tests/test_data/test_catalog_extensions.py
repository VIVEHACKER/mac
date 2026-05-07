from __future__ import annotations

from datetime import date, datetime

from data.catalog import MarketDataCatalog
from data.models import (
    DelistingReturn,
    FlowRecord,
    FundamentalRecord,
    MacroObservation,
    UniverseMember,
    ValuationRecord,
)
from data.quality import evaluate_catalog_quality


def test_catalog_enforces_fundamental_point_in_time(tmp_path) -> None:
    catalog = MarketDataCatalog(tmp_path / "test.duckdb")
    catalog.put_fundamentals(
        [
            FundamentalRecord("MSFT", "us", date(2024, 12, 31), datetime(2025, 2, 1), net_income=10),
            FundamentalRecord("MSFT", "us", date(2025, 3, 31), datetime(2025, 5, 1), net_income=20),
        ]
    )

    rows = catalog.get_fundamentals("MSFT", as_of=datetime(2025, 3, 1))

    assert len(rows) == 1
    assert rows[0].net_income == 10


def test_catalog_round_trips_macro_and_valuation(tmp_path) -> None:
    catalog = MarketDataCatalog(tmp_path / "test.duckdb")
    catalog.put_macro(
        [
            MacroObservation(
                "DGS10",
                "US",
                "rates",
                date(2025, 1, 2),
                datetime(2025, 1, 2, 18),
                4.5,
            )
        ]
    )
    catalog.put_valuations(
        [
            ValuationRecord(
                "MSFT",
                "us",
                date(2025, 1, 2),
                current_price=100,
                fair_value=130,
                discount_pct=0.3,
                rating=2,
                confidence="high",
            )
        ]
    )

    assert catalog.get_macro("DGS10")[0].value == 4.5
    assert catalog.get_valuations(symbol="MSFT")[0].rating == 2


def test_macro_refresh_range_removes_stale_fallback_rows(tmp_path) -> None:
    catalog = MarketDataCatalog(tmp_path / "test.duckdb")
    catalog.put_macro(
        [
            MacroObservation(
                "DGS10",
                "US",
                "rates",
                date(2026, 5, 7),
                datetime(2026, 5, 7, 18),
                4.37,
                source="yahoo-fallback:^TNX",
            )
        ]
    )

    catalog.delete_macro_range("DGS10", date(2026, 5, 1), date(2026, 5, 7))
    catalog.put_macro(
        [
            MacroObservation(
                "DGS10",
                "US",
                "rates",
                date(2026, 5, 6),
                datetime(2026, 5, 6, 18),
                4.36,
                source="treasury:daily_treasury_yield_curve:BC_10YEAR",
            )
        ]
    )

    latest = catalog.get_macro("DGS10")[0]
    assert latest.asof_date == date(2026, 5, 6)
    assert latest.source.startswith("treasury:")


def test_catalog_round_trips_universe_members(tmp_path) -> None:
    catalog = MarketDataCatalog(tmp_path / "test.duckdb")
    catalog.put_universe_members(
        [
            UniverseMember(
                universe="LIQUID_ETF_2008",
                symbol="QQQ",
                market="us",
                start_date=date(2008, 1, 1),
                source="test",
            )
        ]
    )

    rows = catalog.get_universe_members("LIQUID_ETF_2008", market="us")

    assert rows[0].symbol == "QQQ"
    assert rows[0].start_date == date(2008, 1, 1)


def test_catalog_round_trips_delisting_returns(tmp_path) -> None:
    catalog = MarketDataCatalog(tmp_path / "test.duckdb")
    catalog.put_delisting_returns(
        [
            DelistingReturn(
                symbol="OLD",
                market="us",
                ts=date(2020, 6, 1),
                return_pct=-1.0,
                source="test",
            )
        ]
    )

    rows = catalog.get_delisting_returns(
        symbols=["OLD"],
        market="us",
        start=date(2020, 1, 1),
        end=date(2020, 12, 31),
    )

    assert rows[0].symbol == "OLD"
    assert rows[0].return_pct == -1.0


def test_catalog_round_trips_flow_quality_fields(tmp_path) -> None:
    catalog = MarketDataCatalog(tmp_path / "test.duckdb")
    catalog.put_flows(
        [
            FlowRecord(
                "005930",
                "kospi",
                date(2026, 5, 7),
                "foreign",
                net_value=1000,
                net_volume=10,
                value_kind="estimated_close_x_volume",
                confidence="medium",
                source="naver:frgn:005930",
            )
        ]
    )

    assert catalog.get_flows("005930") == []
    row = catalog.get_flow_estimates("005930")[0]
    issues = evaluate_catalog_quality(catalog, as_of=date(2026, 5, 8), required_macro=())

    assert row.net_volume == 10
    assert row.value_kind == "estimated_close_x_volume"
    assert any(issue.area == "flow_estimate" and issue.severity == "info" for issue in issues)
    assert not any(issue.severity in {"error", "warn"} for issue in issues)


def test_quality_can_require_reported_flow(tmp_path) -> None:
    catalog = MarketDataCatalog(tmp_path / "test.duckdb")
    issues = evaluate_catalog_quality(
        catalog,
        as_of=date(2026, 5, 8),
        required_macro=(),
        required_flows=(("005930", "kospi"),),
    )

    assert any(issue.severity == "error" and issue.item == "kospi:005930" for issue in issues)

    catalog.put_flows(
        [
            FlowRecord(
                "005930",
                "kospi",
                date(2026, 5, 7),
                "foreign",
                net_value=1000,
                source="krx-csv:flow.csv",
            )
        ]
    )
    issues = evaluate_catalog_quality(
        catalog,
        as_of=date(2026, 5, 8),
        required_macro=(),
        required_flows=(("005930", "kospi"),),
    )

    assert not any(issue.severity == "error" for issue in issues)
