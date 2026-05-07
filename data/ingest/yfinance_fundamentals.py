from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from data.models import FundamentalRecord


class FundamentalDataError(RuntimeError):
    pass


def fetch_yfinance_fundamentals(
    symbol: str,
    market: str,
    *,
    fetch_info: Callable[[str], dict[str, Any]] | None = None,
    asof_ts: datetime | None = None,
) -> list[FundamentalRecord]:
    normalized = symbol.strip().upper()
    info = (fetch_info or _fetch_info)(normalized)
    record = _record_from_info(
        normalized,
        market=market,
        info=info,
        asof_ts=asof_ts or datetime.now(tz=UTC),
    )
    return [record]


def _record_from_info(
    symbol: str,
    *,
    market: str,
    info: dict[str, Any],
    asof_ts: datetime,
) -> FundamentalRecord:
    shares_out = _number(info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"))
    revenue = _number(info.get("totalRevenue"))
    net_income = _number(info.get("netIncomeToCommon"))
    free_cash_flow = _number(info.get("freeCashflow") or info.get("freeCashFlow"))
    total_debt = _number(info.get("totalDebt"))
    total_cash = _number(info.get("totalCash"))
    book_value = _number(info.get("bookValue"))
    total_equity = book_value * shares_out if book_value is not None and shares_out is not None else None
    eps = _number(info.get("trailingEps") or info.get("forwardEps"))

    if shares_out is None and revenue is None and net_income is None and eps is None:
        raise FundamentalDataError(f"{symbol}: yfinance returned no usable fundamentals")

    return FundamentalRecord(
        symbol=symbol,
        market=market.lower(),
        period_end=asof_ts.date(),
        asof_ts=asof_ts.replace(tzinfo=None),
        revenue=revenue,
        net_income=net_income,
        free_cash_flow=free_cash_flow,
        total_equity=total_equity,
        total_debt=total_debt - total_cash if total_debt is not None and total_cash is not None else total_debt,
        shares_out=shares_out,
        eps=eps,
        source="yfinance:info",
    )


def _fetch_info(symbol: str) -> dict[str, Any]:
    import yfinance as yf

    return dict(yf.Ticker(symbol).info)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
