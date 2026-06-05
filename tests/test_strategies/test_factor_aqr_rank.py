from __future__ import annotations

from datetime import date, datetime

from data.models import FundamentalRecord, PriceBar
from strategies.factor_aqr import aqr_rank_for, aqr_ranked


def _bars(symbol: str, closes: list[float]) -> list[PriceBar]:
    return [
        PriceBar(
            symbol=symbol,
            market="us",
            source_symbol=symbol,
            ts=date(2024, 1, 1 + index),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000.0,
        )
        for index, close in enumerate(closes)
    ]


def _fundamentals(symbol: str, net_income: float, equity: float, fcf: float) -> FundamentalRecord:
    return FundamentalRecord(
        symbol=symbol,
        market="us",
        period_end=date(2023, 12, 31),
        asof_ts=datetime(2024, 1, 1),
        net_income=net_income,
        total_equity=equity,
        free_cash_flow=fcf,
        shares_out=100.0,
    )


def _universe() -> tuple[dict[str, list[PriceBar]], dict[str, FundamentalRecord]]:
    # AAA dominates BBB dominates CCC on every raw factor -> composite order AAA>BBB>CCC.
    bars = {
        "AAA": _bars("AAA", [10.0, 11.0, 13.0]),
        "BBB": _bars("BBB", [10.0, 10.0, 11.0]),
        "CCC": _bars("CCC", [10.0, 10.0, 10.0]),
    }
    fundamentals = {
        "AAA": _fundamentals("AAA", net_income=50.0, equity=100.0, fcf=40.0),
        "BBB": _fundamentals("BBB", net_income=30.0, equity=100.0, fcf=20.0),
        "CCC": _fundamentals("CCC", net_income=10.0, equity=100.0, fcf=5.0),
    }
    return bars, fundamentals


def test_aqr_ranked_orders_and_percentiles() -> None:
    bars, fundamentals = _universe()

    ranked = aqr_ranked(bars, fundamentals, lookback=2)

    assert [rf.score.symbol for rf in ranked] == ["AAA", "BBB", "CCC"]
    assert [rf.rank for rf in ranked] == [1, 2, 3]
    assert all(rf.universe_size == 3 for rf in ranked)
    # best -> 100, middle -> 50, worst -> 0
    assert ranked[0].percentile == 100.0
    assert ranked[1].percentile == 50.0
    assert ranked[2].percentile == 0.0


def test_aqr_rank_for_returns_single_ticker_with_universe_context() -> None:
    bars, fundamentals = _universe()

    bbb = aqr_rank_for("bbb", bars, fundamentals, lookback=2)

    assert bbb is not None
    assert bbb.score.symbol == "BBB"
    assert bbb.rank == 2
    assert bbb.universe_size == 3
    assert bbb.percentile == 50.0


def test_single_name_universe_gets_neutral_percentile_not_top() -> None:
    # A cross-sectional signal is meaningless with one name; it must NOT be promoted
    # to top percentile (which could drive a false BUY).
    bars = {"AAA": _bars("AAA", [10.0, 11.0, 13.0])}
    fundamentals = {"AAA": _fundamentals("AAA", 50.0, 100.0, 40.0)}

    ranked = aqr_ranked(bars, fundamentals, lookback=2)

    assert len(ranked) == 1
    assert ranked[0].universe_size == 1
    assert ranked[0].percentile == 50.0


def test_aqr_rank_for_unknown_ticker_is_none() -> None:
    bars, fundamentals = _universe()

    assert aqr_rank_for("ZZZ", bars, fundamentals, lookback=2) is None


def test_aqr_rank_for_excludes_ticker_with_insufficient_history() -> None:
    bars, fundamentals = _universe()
    # Only two bars -> len(ordered) <= lookback(2) -> excluded from the universe.
    bars["DDD"] = _bars("DDD", [10.0, 12.0])
    fundamentals["DDD"] = _fundamentals("DDD", net_income=99.0, equity=100.0, fcf=90.0)

    assert aqr_rank_for("DDD", bars, fundamentals, lookback=2) is None
