from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from time import sleep

import duckdb

from .models import (
    CryptoFundingRecord,
    DelistingReturn,
    EarningsRecord,
    EntryPlanRecord,
    FlowRecord,
    FundamentalRecord,
    InsiderTradeRecord,
    InstitutionalHoldingRecord,
    MacroObservation,
    OptionSentimentRecord,
    PriceBar,
    UniverseMember,
    ValuationRecord,
)

DEFAULT_CATALOG_PATH = Path("data/store/trader.duckdb")


def _naive_utc(ts: datetime) -> datetime:
    """Normalize to naive UTC for the tz-less DuckDB TIMESTAMP columns. A tz-aware value would
    otherwise be silently shifted to the host's local time on store (Codex P2). A naive value is
    assumed already-UTC and returned unchanged."""
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo is not None else ts


@dataclass(frozen=True)
class Coverage:
    symbol: str
    market: str
    freq: str
    start: date
    end: date
    rows: int


class MarketDataCatalog:
    def __init__(self, db_path: Path | str = DEFAULT_CATALOG_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    source_symbol TEXT NOT NULL,
                    freq TEXT NOT NULL,
                    ts DATE NOT NULL,
                    open DOUBLE NOT NULL,
                    high DOUBLE NOT NULL,
                    low DOUBLE NOT NULL,
                    close DOUBLE NOT NULL,
                    volume DOUBLE NOT NULL,
                    currency TEXT NOT NULL,
                    source TEXT NOT NULL,
                    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS bars_key
                ON bars(symbol, market, freq, ts)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS universe_members (
                    universe TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE,
                    source TEXT NOT NULL,
                    confidence TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS universe_members_key
                ON universe_members(universe, symbol, market, start_date)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS delisting_returns (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    ts DATE NOT NULL,
                    return_pct DOUBLE NOT NULL,
                    source TEXT NOT NULL,
                    confidence TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS delisting_returns_key
                ON delisting_returns(symbol, market, ts)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS fundamentals_q (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    period_end DATE NOT NULL,
                    asof_ts TIMESTAMP NOT NULL,
                    revenue DOUBLE,
                    operating_income DOUBLE,
                    net_income DOUBLE,
                    free_cash_flow DOUBLE,
                    total_assets DOUBLE,
                    total_equity DOUBLE,
                    total_debt DOUBLE,
                    shares_out DOUBLE,
                    eps DOUBLE,
                    gross_profit DOUBLE,
                    cost_of_revenue DOUBLE,
                    source TEXT NOT NULL
                )
                """
            )
            # Migration: add gross-profit columns to pre-existing fundamentals_q tables.
            # ADD COLUMN IF NOT EXISTS is a no-op when the freshly-created table already has them.
            for _col in ("gross_profit", "cost_of_revenue"):
                con.execute(f"ALTER TABLE fundamentals_q ADD COLUMN IF NOT EXISTS {_col} DOUBLE")
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS fundamentals_key
                ON fundamentals_q(symbol, market, period_end, asof_ts)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS insider_trades (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    txn_date DATE NOT NULL,
                    asof_ts TIMESTAMP NOT NULL,
                    insider_name TEXT NOT NULL,
                    insider_role TEXT NOT NULL,
                    txn_code TEXT NOT NULL,
                    shares DOUBLE,
                    price DOUBLE,
                    value_usd DOUBLE,
                    source TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS insider_trades_key
                ON insider_trades(symbol, market, txn_date, asof_ts, insider_name, txn_code)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS institutional_holdings (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    cusip TEXT NOT NULL,
                    issuer_name TEXT NOT NULL,
                    manager TEXT NOT NULL,
                    report_date DATE NOT NULL,
                    asof_ts TIMESTAMP NOT NULL,
                    shares DOUBLE,
                    value_usd DOUBLE,
                    put_call TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            con.execute(
                # cusip (not symbol) is the issuer key — symbol may be "" for an unmapped CUSIP.
                # asof_ts is in the key so an original 13F-HR and its 13F-HR/A amendment coexist as
                # separate rows; get_institutional_holdings keeps only the latest per quarter.
                """
                CREATE UNIQUE INDEX IF NOT EXISTS institutional_holdings_key
                ON institutional_holdings(manager, market, cusip, report_date, asof_ts, put_call)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS earnings (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    announce_ts TIMESTAMP NOT NULL,
                    period_end DATE NOT NULL,
                    eps_actual DOUBLE,
                    eps_estimate DOUBLE,
                    surprise_pct DOUBLE,
                    revenue_actual DOUBLE,
                    revenue_estimate DOUBLE,
                    source TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS earnings_key
                ON earnings(symbol, market, announce_ts, period_end)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_series (
                    series_id TEXT NOT NULL,
                    country TEXT NOT NULL,
                    category TEXT NOT NULL,
                    asof_date DATE NOT NULL,
                    release_ts TIMESTAMP NOT NULL,
                    value DOUBLE NOT NULL,
                    revision_n INTEGER NOT NULL,
                    series_name TEXT NOT NULL,
                    freq TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS macro_series_key
                ON macro_series(series_id, asof_date, revision_n)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS flows (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    ts DATE NOT NULL,
                    investor TEXT NOT NULL,
                    net_value DOUBLE NOT NULL,
                    buy_value DOUBLE NOT NULL,
                    sell_value DOUBLE NOT NULL,
                    net_volume DOUBLE,
                    release_ts TIMESTAMP,
                    value_kind TEXT NOT NULL DEFAULT 'reported_value',
                    confidence TEXT NOT NULL DEFAULT 'high',
                    source TEXT NOT NULL
                )
                """
            )
            self._ensure_columns(
                con,
                "flows",
                {
                    "net_volume": "DOUBLE",
                    "value_kind": "TEXT DEFAULT 'reported_value'",
                    "confidence": "TEXT DEFAULT 'high'",
                },
            )
            con.execute(
                """
                UPDATE flows
                SET value_kind = 'estimated_close_x_volume',
                    confidence = 'medium'
                WHERE source LIKE 'naver:%'
                  AND value_kind = 'reported_value'
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS flows_key
                ON flows(symbol, market, ts, investor)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS flow_estimates (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    ts DATE NOT NULL,
                    investor TEXT NOT NULL,
                    net_value DOUBLE NOT NULL,
                    buy_value DOUBLE NOT NULL,
                    sell_value DOUBLE NOT NULL,
                    net_volume DOUBLE,
                    release_ts TIMESTAMP,
                    value_kind TEXT NOT NULL DEFAULT 'estimated',
                    confidence TEXT NOT NULL DEFAULT 'low',
                    source TEXT NOT NULL
                )
                """
            )
            self._ensure_columns(
                con,
                "flow_estimates",
                {
                    "net_volume": "DOUBLE",
                    "value_kind": "TEXT DEFAULT 'estimated'",
                    "confidence": "TEXT DEFAULT 'low'",
                },
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS flow_estimates_key
                ON flow_estimates(symbol, market, ts, investor)
                """
            )
            self._quarantine_untrusted_flows(con)
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_funding (
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    ts TIMESTAMP NOT NULL,
                    funding_rate DOUBLE NOT NULL,
                    next_funding_ts TIMESTAMP,
                    mark_price DOUBLE,
                    source TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS crypto_funding_key
                ON crypto_funding(exchange, symbol, ts)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS option_sentiment (
                    date DATE NOT NULL,
                    market TEXT NOT NULL,
                    vix DOUBLE,
                    vix_short DOUBLE,
                    vix_3m DOUBLE,
                    vix_6m DOUBLE,
                    skew DOUBLE,
                    put_call_equity DOUBLE,
                    put_call_index DOUBLE,
                    dvol DOUBLE,
                    source TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS option_sentiment_key
                ON option_sentiment(date, market)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS valuation_scores (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    asof_date DATE NOT NULL,
                    current_price DOUBLE NOT NULL,
                    fair_value DOUBLE NOT NULL,
                    fair_dcf DOUBLE,
                    fair_multiple DOUBLE,
                    fair_rim DOUBLE,
                    dispersion_pct DOUBLE,
                    discount_pct DOUBLE NOT NULL,
                    rating INTEGER NOT NULL,
                    confidence TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS valuation_scores_key
                ON valuation_scores(symbol, market, asof_date)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS entry_plans (
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    asof_ts TIMESTAMP NOT NULL,
                    current_price DOUBLE NOT NULL,
                    fair_value DOUBLE NOT NULL,
                    target_entry DOUBLE NOT NULL,
                    stop_loss DOUBLE NOT NULL,
                    target_exit DOUBLE NOT NULL,
                    ladder_json TEXT NOT NULL,
                    risk_reward DOUBLE NOT NULL,
                    expected_holding_days INTEGER NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS entry_plans_key
                ON entry_plans(symbol, market, asof_ts)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty DOUBLE NOT NULL,
                    price DOUBLE NOT NULL,
                    status TEXT NOT NULL,
                    ts TIMESTAMP NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_positions (
                    strategy TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    qty DOUBLE NOT NULL,
                    avg_cost DOUBLE NOT NULL,
                    updated_ts TIMESTAMP NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS paper_positions_key
                ON paper_positions(strategy, symbol, market)
                """
            )

    def put_bars(self, bars: list[PriceBar]) -> int:
        if not bars:
            return 0
        self.initialize()
        rows = [
            (
                bar.symbol.upper(),
                bar.market.lower(),
                bar.source_symbol,
                bar.freq,
                bar.ts,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.currency,
                bar.source,
            )
            for bar in bars
        ]
        with self._connect() as con:
            con.executemany(
                """
                DELETE FROM bars
                WHERE symbol = ? AND market = ? AND freq = ? AND ts = ?
                """,
                [(row[0], row[1], row[3], row[4]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO bars (
                    symbol, market, source_symbol, freq, ts, open, high, low, close,
                    volume, currency, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_bars(
        self,
        symbol: str,
        market: str = "us",
        freq: str = "1d",
        start: date | None = None,
        end: date | None = None,
    ) -> list[PriceBar]:
        self.initialize()
        query = [
            """
            SELECT symbol, market, source_symbol, ts, open, high, low, close, volume,
                   freq, currency, source
            FROM bars
            WHERE symbol = ? AND market = ? AND freq = ?
            """
        ]
        params: list[object] = [symbol.upper(), market.lower(), freq]
        if start is not None:
            query.append("AND ts >= ?")
            params.append(start)
        if end is not None:
            query.append("AND ts <= ?")
            params.append(end)
        query.append("ORDER BY ts")

        with self._connect() as con:
            result = con.execute("\n".join(query), params).fetchall()

        return [
            PriceBar(
                symbol=row[0],
                market=row[1],
                source_symbol=row[2],
                ts=row[3],
                open=row[4],
                high=row[5],
                low=row[6],
                close=row[7],
                volume=row[8],
                freq=row[9],
                currency=row[10],
                source=row[11],
            )
            for row in result
        ]

    def put_universe_members(self, records: list[UniverseMember]) -> int:
        if not records:
            return 0
        self.initialize()
        rows = [
            (
                item.universe,
                item.symbol.upper(),
                item.market.lower(),
                item.start_date,
                item.end_date,
                item.source,
                item.confidence,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                """
                DELETE FROM universe_members
                WHERE universe = ? AND symbol = ? AND market = ? AND start_date = ?
                """,
                [(row[0], row[1], row[2], row[3]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO universe_members (
                    universe, symbol, market, start_date, end_date, source, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_universe_members(
        self,
        universe: str,
        market: str | None = None,
    ) -> list[UniverseMember]:
        self.initialize()
        query = [
            """
            SELECT universe, symbol, market, start_date, end_date, source, confidence
            FROM universe_members
            WHERE universe = ?
            """
        ]
        params: list[object] = [universe]
        if market is not None:
            query.append("AND market = ?")
            params.append(market.lower())
        query.append("ORDER BY start_date, symbol")
        with self._connect() as con:
            rows = con.execute("\n".join(query), params).fetchall()
        return [
            UniverseMember(
                universe=row[0],
                symbol=row[1],
                market=row[2],
                start_date=row[3],
                end_date=row[4],
                source=row[5],
                confidence=row[6],
            )
            for row in rows
        ]

    def put_delisting_returns(self, records: list[DelistingReturn]) -> int:
        if not records:
            return 0
        self.initialize()
        rows = [
            (
                item.symbol.upper(),
                item.market.lower(),
                item.ts,
                item.return_pct,
                item.source,
                item.confidence,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                """
                DELETE FROM delisting_returns
                WHERE symbol = ? AND market = ? AND ts = ?
                """,
                [(row[0], row[1], row[2]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO delisting_returns (
                    symbol, market, ts, return_pct, source, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_delisting_returns(
        self,
        *,
        symbols: list[str] | None = None,
        market: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[DelistingReturn]:
        self.initialize()
        query = [
            """
            SELECT symbol, market, ts, return_pct, source, confidence
            FROM delisting_returns
            WHERE 1 = 1
            """
        ]
        params: list[object] = []
        if symbols:
            placeholders = ", ".join("?" for _ in symbols)
            query.append(f"AND symbol IN ({placeholders})")
            params.extend(symbol.upper() for symbol in symbols)
        if market is not None:
            query.append("AND market = ?")
            params.append(market.lower())
        if start is not None:
            query.append("AND ts >= ?")
            params.append(start)
        if end is not None:
            query.append("AND ts <= ?")
            params.append(end)
        query.append("ORDER BY ts, symbol")
        with self._connect() as con:
            rows = con.execute("\n".join(query), params).fetchall()
        return [
            DelistingReturn(
                symbol=row[0],
                market=row[1],
                ts=row[2],
                return_pct=row[3],
                source=row[4],
                confidence=row[5],
            )
            for row in rows
        ]

    def put_fundamentals(self, records: list[FundamentalRecord]) -> int:
        if not records:
            return 0
        self.initialize()
        rows = [
            (
                item.symbol.upper(),
                item.market.lower(),
                item.period_end,
                item.asof_ts,
                item.revenue,
                item.operating_income,
                item.net_income,
                item.free_cash_flow,
                item.total_assets,
                item.total_equity,
                item.total_debt,
                item.shares_out,
                item.eps,
                item.gross_profit,
                item.cost_of_revenue,
                item.source,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                """
                DELETE FROM fundamentals_q
                WHERE symbol = ? AND market = ? AND period_end = ? AND asof_ts = ?
                """,
                [(row[0], row[1], row[2], row[3]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO fundamentals_q (
                    symbol, market, period_end, asof_ts, revenue, operating_income,
                    net_income, free_cash_flow, total_assets, total_equity, total_debt,
                    shares_out, eps, gross_profit, cost_of_revenue, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_fundamentals(
        self,
        symbol: str,
        market: str = "us",
        as_of: datetime | None = None,
        limit: int = 1,
    ) -> list[FundamentalRecord]:
        self.initialize()
        query = [
            """
            SELECT symbol, market, period_end, asof_ts, revenue, operating_income,
                   net_income, free_cash_flow, total_assets, total_equity, total_debt,
                   shares_out, eps, gross_profit, cost_of_revenue, source
            FROM fundamentals_q
            WHERE symbol = ? AND market = ?
            """
        ]
        params: list[object] = [symbol.upper(), market.lower()]
        if as_of is not None:
            query.append("AND asof_ts <= ?")
            params.append(as_of)
        query.append("ORDER BY asof_ts DESC, period_end DESC")
        if limit > 0:
            query.append("LIMIT ?")
            params.append(limit)
        with self._connect() as con:
            rows = con.execute("\n".join(query), params).fetchall()
        return [
            FundamentalRecord(
                symbol=row[0],
                market=row[1],
                period_end=row[2],
                asof_ts=row[3],
                revenue=row[4],
                operating_income=row[5],
                net_income=row[6],
                free_cash_flow=row[7],
                total_assets=row[8],
                total_equity=row[9],
                total_debt=row[10],
                shares_out=row[11],
                eps=row[12],
                gross_profit=row[13],
                cost_of_revenue=row[14],
                source=row[15],
            )
            for row in rows
        ]

    def put_insider_trades(self, records: list[InsiderTradeRecord]) -> int:
        if not records:
            return 0
        self.initialize()
        # Aggregate split executions: a single Form 4 can report several transactions by the same
        # insider on the same day with the same code (different price buckets). They are one
        # economic buy for signal purposes, and summing them also avoids a unique-key collision
        # (Codex P2). Key matches the unique index. Timestamps are normalized to naive UTC so the
        # tz-less DuckDB TIMESTAMP column is not shifted to local time (Codex P2).
        agg: dict[tuple, dict] = {}
        for item in records:
            key = (
                item.symbol.upper(),
                item.market.lower(),
                item.txn_date,
                _naive_utc(item.asof_ts),
                item.insider_name,
                item.txn_code,
            )
            b = agg.setdefault(
                key,
                {
                    "shares": 0.0,
                    "value": 0.0,
                    "n_sh": 0,
                    "n_val": 0,
                    "px": None,
                    "role": item.insider_role,
                    "source": item.source,
                },
            )
            if item.shares is not None:
                b["shares"] += item.shares
                b["n_sh"] += 1
            if item.price is not None and b["px"] is None:
                b["px"] = item.price  # fallback when no weighted avg can be computed (Codex P3)
            # Derive notional from shares*price when a feed gives unit price but no precomputed
            # value_usd, so price survives the round-trip (Codex P2).
            value = item.value_usd
            if value is None and item.shares is not None and item.price is not None:
                value = item.shares * item.price
            if value is not None:
                b["value"] += value
                b["n_val"] += 1
        rows = []
        for key, b in agg.items():
            shares = b["shares"] if b["n_sh"] else None
            value = b["value"] if b["n_val"] else None
            # `value is not None` (not truthiness) so a legitimate $0 transaction — e.g. a grant on
            # a non-default code path — keeps price 0.0 instead of becoming indistinguishable from
            # a missing price (Codex P2). `shares` truthiness keeps it nonzero (no div-by-zero).
            price = value / shares if value is not None and shares else b["px"]
            rows.append(
                (
                    key[0],
                    key[1],
                    key[2],
                    key[3],
                    key[4],
                    b["role"],
                    key[5],
                    shares,
                    price,
                    value,
                    b["source"],
                )
            )
        with self._connect() as con:
            con.executemany(
                """
                DELETE FROM insider_trades
                WHERE symbol = ? AND market = ? AND txn_date = ? AND asof_ts = ?
                      AND insider_name = ? AND txn_code = ?
                """,
                [(r[0], r[1], r[2], r[3], r[4], r[6]) for r in rows],
            )
            con.executemany(
                """
                INSERT INTO insider_trades (
                    symbol, market, txn_date, asof_ts, insider_name, insider_role,
                    txn_code, shares, price, value_usd, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_insider_trades(
        self,
        symbol: str,
        market: str = "us",
        as_of: datetime | None = None,
        limit: int = 0,
    ) -> list[InsiderTradeRecord]:
        self.initialize()
        query = [
            """
            SELECT symbol, market, txn_date, asof_ts, insider_name, insider_role,
                   txn_code, shares, price, value_usd, source
            FROM insider_trades
            WHERE symbol = ? AND market = ?
            """
        ]
        params: list[object] = [symbol.upper(), market.lower()]
        if as_of is not None:
            # DOUBLE point-in-time guard: neither the trade nor its disclosure may leak forward.
            # asof_ts <= as_of  (the SEC filing must already be public) AND
            # txn_date <= as_of  (the trade itself must already have happened).
            # Normalize to naive UTC to match the stored (naive-UTC) timestamps (Codex P2).
            as_of_utc = _naive_utc(as_of)
            query.append("AND asof_ts <= ? AND txn_date <= ?")
            params.extend([as_of_utc, as_of_utc.date()])
        # Amendment supersession: a 4/A correction is a NEW row (later asof_ts) for the same
        # transaction key, so keep only the latest VISIBLE filing per transaction — otherwise a
        # consumer summing the cluster double-counts the original + its correction (Codex P2).
        query.append(
            """
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol, market, txn_date, insider_name, txn_code
                ORDER BY asof_ts DESC
            ) = 1
            """
        )
        query.append("ORDER BY asof_ts DESC, txn_date DESC")
        if limit > 0:
            query.append("LIMIT ?")
            params.append(limit)
        with self._connect() as con:
            rows = con.execute("\n".join(query), params).fetchall()
        return [
            InsiderTradeRecord(
                symbol=row[0],
                market=row[1],
                txn_date=row[2],
                asof_ts=row[3],
                insider_name=row[4],
                insider_role=row[5],
                txn_code=row[6],
                shares=row[7],
                price=row[8],
                value_usd=row[9],
                source=row[10],
            )
            for row in rows
        ]

    def put_institutional_holdings(self, records: list[InstitutionalHoldingRecord]) -> int:
        """Store 13F holdings. Pass ALL sub-rows of a filing in ONE call: a DELETE-then-INSERT on the
        unique key makes re-ingesting a filing idempotent (it replaces, never accumulates). A
        position EXITED in an amendment is represented PIT-safely by the ingest emitting a None-shares
        tombstone row (later asof_ts) — the original is kept so a query between the two filings still
        sees it; get_institutional_holdings then surfaces the tombstone as the latest snapshot."""
        if not records:
            return 0
        self.initialize()
        # Aggregate sub-rows that share the unique key: a 13F can split one manager's position for an
        # issuer across several infoTable rows (otherManager allocations). They are one economic
        # position, so sum shares and value (and this also avoids a unique-key collision). asof_ts is
        # normalized to naive UTC so the tz-less DuckDB TIMESTAMP column is not shifted to local time.
        agg: dict[tuple, dict] = {}
        for item in records:
            key = (
                item.manager,
                item.market.lower(),
                item.cusip.replace(" ", "").upper(),
                item.report_date,
                _naive_utc(item.asof_ts),
                item.put_call,
            )
            b = agg.setdefault(
                key,
                {
                    "symbol": item.symbol.upper(),
                    "issuer": item.issuer_name,
                    "shares": 0.0,
                    "value": 0.0,
                    "n_sh": 0,
                    "n_val": 0,
                    "source": item.source,
                },
            )
            if item.shares is not None:
                b["shares"] += item.shares
                b["n_sh"] += 1
            if item.value_usd is not None:
                b["value"] += item.value_usd
                b["n_val"] += 1
        rows = [
            (
                b["symbol"],
                key[1],  # market
                key[2],  # cusip
                b["issuer"],
                key[0],  # manager
                key[3],  # report_date
                key[4],  # asof_ts
                b["shares"] if b["n_sh"] else None,
                b["value"] if b["n_val"] else None,
                key[5],  # put_call
                b["source"],
            )
            for key, b in agg.items()
        ]
        with self._connect() as con:
            con.executemany(
                """
                DELETE FROM institutional_holdings
                WHERE manager = ? AND market = ? AND cusip = ? AND report_date = ?
                      AND asof_ts = ? AND put_call = ?
                """,
                [(r[4], r[1], r[2], r[5], r[6], r[9]) for r in rows],
            )
            con.executemany(
                """
                INSERT INTO institutional_holdings (
                    symbol, market, cusip, issuer_name, manager, report_date, asof_ts,
                    shares, value_usd, put_call, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_institutional_holdings(
        self,
        symbol: str,
        market: str = "us",
        as_of: datetime | None = None,
        limit: int = 0,
    ) -> list[InstitutionalHoldingRecord]:
        """Visible 13F holdings of ``symbol`` (resolved ticker), latest filing per (manager, cusip,
        quarter). Unmapped CUSIPs (symbol == "") are not reachable by this ticker query — resolution
        happens at ingest.

        ``as_of=None`` applies NO point-in-time filter (returns the latest stored snapshot per
        position, matching get_insider_trades). Backtests MUST pass an explicit ``as_of`` — defaulting
        to wall-clock now() would be wrong for a simulated-past query (it would leak real-present
        filings into a 2020 sim). An exited position appears as a shares=None tombstone row."""
        self.initialize()
        query = [
            """
            SELECT symbol, market, cusip, issuer_name, manager, report_date, asof_ts,
                   shares, value_usd, put_call, source
            FROM institutional_holdings
            WHERE symbol = ? AND market = ?
            """
        ]
        params: list[object] = [symbol.upper(), market.lower()]
        if as_of is not None:
            # DOUBLE point-in-time guard: the filing must be public (asof_ts <= as_of) AND the
            # reported quarter must have ended (report_date <= as_of). asof_ts >= report_date always,
            # so asof_ts is the binding constraint, but both are checked for symmetry with Form 4.
            as_of_utc = _naive_utc(as_of)
            query.append("AND asof_ts <= ? AND report_date <= ?")
            params.extend([as_of_utc, as_of_utc.date()])
        # Amendment supersession: a 13F-HR/A restatement is a NEW row (later asof_ts) for the same
        # manager+issuer+quarter, so keep only the latest VISIBLE filing per position.
        query.append(
            """
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY manager, cusip, report_date, put_call
                ORDER BY asof_ts DESC
            ) = 1
            """
        )
        query.append("ORDER BY report_date DESC, manager, cusip")
        if limit > 0:
            query.append("LIMIT ?")
            params.append(limit)
        with self._connect() as con:
            rows = con.execute("\n".join(query), params).fetchall()
        return [
            InstitutionalHoldingRecord(
                symbol=row[0],
                market=row[1],
                cusip=row[2],
                issuer_name=row[3],
                manager=row[4],
                report_date=row[5],
                asof_ts=row[6],
                shares=row[7],
                value_usd=row[8],
                put_call=row[9],
                source=row[10],
            )
            for row in rows
        ]

    def put_earnings(self, records: list[EarningsRecord]) -> int:
        if not records:
            return 0
        self.initialize()
        rows = [
            (
                item.symbol.upper(),
                item.market.lower(),
                item.announce_ts,
                item.period_end,
                item.eps_actual,
                item.eps_estimate,
                item.surprise_pct,
                item.revenue_actual,
                item.revenue_estimate,
                item.source,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                """
                DELETE FROM earnings
                WHERE symbol = ? AND market = ? AND announce_ts = ? AND period_end = ?
                """,
                [(row[0], row[1], row[2], row[3]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO earnings (
                    symbol, market, announce_ts, period_end, eps_actual, eps_estimate,
                    surprise_pct, revenue_actual, revenue_estimate, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def put_macro(self, records: list[MacroObservation]) -> int:
        if not records:
            return 0
        self.initialize()
        rows = [
            (
                item.series_id.upper(),
                item.country.upper(),
                item.category,
                item.asof_date,
                item.release_ts,
                item.value,
                item.revision_n,
                item.series_name,
                item.freq,
                item.unit,
                item.source,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                """
                DELETE FROM macro_series
                WHERE series_id = ? AND asof_date = ? AND revision_n = ?
                """,
                [(row[0], row[3], row[6]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO macro_series (
                    series_id, country, category, asof_date, release_ts, value,
                    revision_n, series_name, freq, unit, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def delete_macro_range(self, series_id: str, start: date, end: date) -> int:
        self.initialize()
        with self._connect() as con:
            result = con.execute(
                """
                DELETE FROM macro_series
                WHERE series_id = ? AND asof_date >= ? AND asof_date <= ?
                """,
                [series_id.upper(), start, end],
            )
        return int(result.rowcount or 0)

    def get_macro(
        self,
        series_id: str,
        as_of: datetime | None = None,
        limit: int = 500,
    ) -> list[MacroObservation]:
        self.initialize()
        query = [
            """
            SELECT series_id, country, category, asof_date, release_ts, value, revision_n,
                   series_name, freq, unit, source
            FROM macro_series
            WHERE series_id = ?
            """
        ]
        params: list[object] = [series_id.upper()]
        if as_of is not None:
            query.append("AND release_ts <= ?")
            params.append(as_of)
        query.append("ORDER BY asof_date DESC, revision_n DESC")
        if limit > 0:
            query.append("LIMIT ?")
            params.append(limit)
        with self._connect() as con:
            rows = con.execute("\n".join(query), params).fetchall()
        return [
            MacroObservation(
                series_id=row[0],
                country=row[1],
                category=row[2],
                asof_date=row[3],
                release_ts=row[4],
                value=row[5],
                revision_n=row[6],
                series_name=row[7],
                freq=row[8],
                unit=row[9],
                source=row[10],
            )
            for row in rows
        ]

    def put_flows(self, records: list[FlowRecord]) -> int:
        if not records:
            return 0
        self.initialize()
        trusted = [record for record in records if _is_reported_flow(record)]
        estimates = [record for record in records if not _is_reported_flow(record)]
        if estimates:
            self._put_flow_records(estimates, "flow_estimates")
        if trusted:
            self._put_flow_records(trusted, "flows")
        return len(records)

    def put_flow_estimates(self, records: list[FlowRecord]) -> int:
        if not records:
            return 0
        self.initialize()
        self._put_flow_records(records, "flow_estimates")
        return len(records)

    def _put_flow_records(self, records: list[FlowRecord], table: str) -> None:
        rows = [
            (
                item.symbol.upper(),
                item.market.lower(),
                item.ts,
                item.investor,
                item.net_value,
                item.buy_value,
                item.sell_value,
                item.net_volume,
                item.release_ts,
                item.value_kind,
                item.confidence,
                item.source,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                f"""
                DELETE FROM {table}
                WHERE symbol = ? AND market = ? AND ts = ? AND investor = ?
                """,
                [(row[0], row[1], row[2], row[3]) for row in rows],
            )
            con.executemany(
                f"""
                INSERT INTO {table} (
                    symbol, market, ts, investor, net_value, buy_value, sell_value,
                    net_volume, release_ts, value_kind, confidence, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_flows(
        self,
        symbol: str,
        market: str = "kospi",
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[FlowRecord]:
        self.initialize()
        query = [
            """
            SELECT symbol, market, ts, investor, net_value, buy_value, sell_value,
                   net_volume, release_ts, value_kind, confidence, source
            FROM flows
            WHERE symbol = ? AND market = ?
            """
        ]
        params: list[object] = [symbol.upper(), market.lower()]
        if as_of is not None:
            query.append("AND (release_ts IS NULL OR release_ts <= ?)")
            params.append(as_of)
        query.append("ORDER BY ts DESC")
        if limit > 0:
            query.append("LIMIT ?")
            params.append(limit)
        with self._connect() as con:
            rows = con.execute("\n".join(query), params).fetchall()
        return [
            FlowRecord(
                symbol=row[0],
                market=row[1],
                ts=row[2],
                investor=row[3],
                net_value=row[4],
                buy_value=row[5],
                sell_value=row[6],
                net_volume=row[7],
                release_ts=row[8],
                value_kind=row[9],
                confidence=row[10],
                source=row[11],
            )
            for row in rows
        ]

    def get_flow_estimates(
        self,
        symbol: str,
        market: str = "kospi",
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[FlowRecord]:
        self.initialize()
        query = [
            """
            SELECT symbol, market, ts, investor, net_value, buy_value, sell_value,
                   net_volume, release_ts, value_kind, confidence, source
            FROM flow_estimates
            WHERE symbol = ? AND market = ?
            """
        ]
        params: list[object] = [symbol.upper(), market.lower()]
        if as_of is not None:
            query.append("AND (release_ts IS NULL OR release_ts <= ?)")
            params.append(as_of)
        query.append("ORDER BY ts DESC")
        if limit > 0:
            query.append("LIMIT ?")
            params.append(limit)
        with self._connect() as con:
            rows = con.execute("\n".join(query), params).fetchall()
        return [
            FlowRecord(
                symbol=row[0],
                market=row[1],
                ts=row[2],
                investor=row[3],
                net_value=row[4],
                buy_value=row[5],
                sell_value=row[6],
                net_volume=row[7],
                release_ts=row[8],
                value_kind=row[9],
                confidence=row[10],
                source=row[11],
            )
            for row in rows
        ]

    def put_crypto_funding(self, records: list[CryptoFundingRecord]) -> int:
        if not records:
            return 0
        self.initialize()
        rows = [
            (
                item.exchange,
                item.symbol.upper(),
                item.ts,
                item.funding_rate,
                item.next_funding_ts,
                item.mark_price,
                item.source,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                "DELETE FROM crypto_funding WHERE exchange = ? AND symbol = ? AND ts = ?",
                [(row[0], row[1], row[2]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO crypto_funding (
                    exchange, symbol, ts, funding_rate, next_funding_ts, mark_price, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def put_option_sentiment(self, records: list[OptionSentimentRecord]) -> int:
        if not records:
            return 0
        self.initialize()
        rows = [
            (
                item.date,
                item.market.upper(),
                item.vix,
                item.vix_short,
                item.vix_3m,
                item.vix_6m,
                item.skew,
                item.put_call_equity,
                item.put_call_index,
                item.dvol,
                item.source,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                "DELETE FROM option_sentiment WHERE date = ? AND market = ?",
                [(row[0], row[1]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO option_sentiment (
                    date, market, vix, vix_short, vix_3m, vix_6m, skew,
                    put_call_equity, put_call_index, dvol, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def delete_option_sentiment_range(self, market: str, start: date, end: date) -> int:
        self.initialize()
        with self._connect() as con:
            result = con.execute(
                """
                DELETE FROM option_sentiment
                WHERE market = ? AND date >= ? AND date <= ?
                """,
                [market.upper(), start, end],
            )
        return int(result.rowcount or 0)

    def put_valuations(self, records: list[ValuationRecord]) -> int:
        if not records:
            return 0
        self.initialize()
        rows = [
            (
                item.symbol.upper(),
                item.market.lower(),
                item.asof_date,
                item.current_price,
                item.fair_value,
                item.fair_dcf,
                item.fair_multiple,
                item.fair_rim,
                item.dispersion_pct,
                item.discount_pct,
                item.rating,
                item.confidence,
                item.source,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                "DELETE FROM valuation_scores WHERE symbol = ? AND market = ? AND asof_date = ?",
                [(row[0], row[1], row[2]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO valuation_scores (
                    symbol, market, asof_date, current_price, fair_value, fair_dcf,
                    fair_multiple, fair_rim, dispersion_pct, discount_pct, rating,
                    confidence, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_valuations(
        self,
        symbol: str | None = None,
        market: str | None = None,
        limit: int = 50,
    ) -> list[ValuationRecord]:
        self.initialize()
        query = [
            """
            SELECT symbol, market, asof_date, current_price, fair_value, fair_dcf,
                   fair_multiple, fair_rim, dispersion_pct, discount_pct, rating,
                   confidence, source
            FROM valuation_scores
            WHERE 1 = 1
            """
        ]
        params: list[object] = []
        if symbol is not None:
            query.append("AND symbol = ?")
            params.append(symbol.upper())
        if market is not None:
            query.append("AND market = ?")
            params.append(market.lower())
        query.append("ORDER BY asof_date DESC, rating DESC")
        if limit > 0:
            query.append("LIMIT ?")
            params.append(limit)
        with self._connect() as con:
            rows = con.execute("\n".join(query), params).fetchall()
        return [
            ValuationRecord(
                symbol=row[0],
                market=row[1],
                asof_date=row[2],
                current_price=row[3],
                fair_value=row[4],
                fair_dcf=row[5],
                fair_multiple=row[6],
                fair_rim=row[7],
                dispersion_pct=row[8],
                discount_pct=row[9],
                rating=row[10],
                confidence=row[11],
                source=row[12],
            )
            for row in rows
        ]

    def put_entry_plans(self, records: list[EntryPlanRecord]) -> int:
        if not records:
            return 0
        self.initialize()
        rows = [
            (
                item.symbol.upper(),
                item.market.lower(),
                item.asof_ts,
                item.current_price,
                item.fair_value,
                item.target_entry,
                item.stop_loss,
                item.target_exit,
                item.ladder_json,
                item.risk_reward,
                item.expected_holding_days,
                item.source,
            )
            for item in records
        ]
        with self._connect() as con:
            con.executemany(
                "DELETE FROM entry_plans WHERE symbol = ? AND market = ? AND asof_ts = ?",
                [(row[0], row[1], row[2]) for row in rows],
            )
            con.executemany(
                """
                INSERT INTO entry_plans (
                    symbol, market, asof_ts, current_price, fair_value, target_entry,
                    stop_loss, target_exit, ladder_json, risk_reward,
                    expected_holding_days, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def coverage(self) -> list[Coverage]:
        self.initialize()
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT symbol, market, freq, min(ts), max(ts), count(*)
                FROM bars
                GROUP BY symbol, market, freq
                ORDER BY market, symbol, freq
                """
            ).fetchall()
        return [Coverage(row[0], row[1], row[2], row[3], row[4], row[5]) for row in rows]

    def _connect(self) -> duckdb.DuckDBPyConnection:
        attempts = 10
        for attempt in range(attempts):
            try:
                return duckdb.connect(str(self.db_path))
            except duckdb.IOException as exc:
                if "Conflicting lock" not in str(exc) or attempt == attempts - 1:
                    raise
                sleep(0.2)
        raise RuntimeError("unreachable")

    def _ensure_columns(
        self,
        con: duckdb.DuckDBPyConnection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        existing = {row[1] for row in con.execute(f"PRAGMA table_info('{table}')").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _quarantine_untrusted_flows(self, con: duckdb.DuckDBPyConnection) -> None:
        con.execute(
            """
            INSERT INTO flow_estimates
            SELECT symbol, market, ts, investor, net_value, buy_value, sell_value,
                   net_volume, release_ts, value_kind, confidence, source
            FROM flows AS source
            WHERE (value_kind != 'reported_value' OR confidence != 'high')
              AND NOT EXISTS (
                  SELECT 1
                  FROM flow_estimates AS target
                  WHERE target.symbol = source.symbol
                    AND target.market = source.market
                    AND target.ts = source.ts
                    AND target.investor = source.investor
              )
            """
        )
        con.execute(
            """
            DELETE FROM flows
            WHERE value_kind != 'reported_value' OR confidence != 'high'
            """
        )


def _is_reported_flow(record: FlowRecord) -> bool:
    return record.value_kind == "reported_value" and record.confidence == "high"
