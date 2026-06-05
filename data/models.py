from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class PriceBar:
    symbol: str
    market: str
    source_symbol: str
    ts: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    freq: str = "1d"
    currency: str = ""
    source: str = ""


@dataclass(frozen=True)
class UniverseMember:
    universe: str
    symbol: str
    market: str
    start_date: date
    end_date: date | None = None
    source: str = ""
    confidence: str = "high"


@dataclass(frozen=True)
class DelistingReturn:
    symbol: str
    market: str
    ts: date
    return_pct: float
    source: str = ""
    confidence: str = "high"


@dataclass(frozen=True)
class FundamentalRecord:
    symbol: str
    market: str
    period_end: date
    asof_ts: datetime
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    free_cash_flow: float | None = None
    total_assets: float | None = None
    total_equity: float | None = None
    total_debt: float | None = None
    shares_out: float | None = None
    eps: float | None = None
    gross_profit: float | None = None
    cost_of_revenue: float | None = None
    source: str = ""


@dataclass(frozen=True)
class EarningsRecord:
    symbol: str
    market: str
    announce_ts: datetime
    period_end: date
    eps_actual: float | None = None
    eps_estimate: float | None = None
    surprise_pct: float | None = None
    revenue_actual: float | None = None
    revenue_estimate: float | None = None
    source: str = ""


@dataclass(frozen=True)
class MacroObservation:
    series_id: str
    country: str
    category: str
    asof_date: date
    release_ts: datetime
    value: float
    revision_n: int = 0
    series_name: str = ""
    freq: str = ""
    unit: str = ""
    source: str = ""


@dataclass(frozen=True)
class FlowRecord:
    symbol: str
    market: str
    ts: date
    investor: str
    net_value: float
    buy_value: float = 0.0
    sell_value: float = 0.0
    net_volume: float | None = None
    release_ts: datetime | None = None
    value_kind: str = "reported_value"
    confidence: str = "high"
    source: str = ""


@dataclass(frozen=True)
class CryptoFundingRecord:
    exchange: str
    symbol: str
    ts: datetime
    funding_rate: float
    next_funding_ts: datetime | None = None
    mark_price: float | None = None
    source: str = ""


@dataclass(frozen=True)
class OptionSentimentRecord:
    date: date
    market: str
    vix: float | None = None
    vix_short: float | None = None
    vix_3m: float | None = None
    vix_6m: float | None = None
    skew: float | None = None
    put_call_equity: float | None = None
    put_call_index: float | None = None
    dvol: float | None = None
    source: str = ""


@dataclass(frozen=True)
class ValuationRecord:
    symbol: str
    market: str
    asof_date: date
    current_price: float
    fair_value: float
    fair_dcf: float | None = None
    fair_multiple: float | None = None
    fair_rim: float | None = None
    dispersion_pct: float | None = None
    discount_pct: float = 0.0
    rating: int = 0
    confidence: str = "low"
    source: str = ""


@dataclass(frozen=True)
class EntryPlanRecord:
    symbol: str
    market: str
    asof_ts: datetime
    current_price: float
    fair_value: float
    target_entry: float
    stop_loss: float
    target_exit: float
    ladder_json: str
    risk_reward: float
    expected_holding_days: int = 180
    source: str = ""


@dataclass(frozen=True)
class OrderBookLevel:
    """A single price level in an L2 order-book ladder."""

    price: float
    size: float


@dataclass(frozen=True)
class OrderBookSnapshot:
    """A point-in-time L2 order-book snapshot (crypto, via ccxt fetch_order_book).

    ``bids`` are sorted by price descending, ``asks`` by price ascending, mirroring
    the ccxt contract. ``ts`` carries the full timestamp (exchange or local fallback).
    See docs/CHART_READING.md §10.
    """

    exchange: str
    symbol: str
    ts: datetime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    source: str = ""


@dataclass(frozen=True)
class OpenInterestRecord:
    """A single open-interest observation (crypto perpetual/futures, via ccxt).

    ``open_interest_amount`` is the contract quantity (preferred for change-rate
    detection); ``open_interest_value`` is the USD notional (preferred for absolute
    size). See docs/CHART_READING.md §11 for the Amount-vs-Value distinction.
    """

    exchange: str
    symbol: str
    ts: datetime
    open_interest_amount: float
    open_interest_value: float | None = None
    source: str = ""
