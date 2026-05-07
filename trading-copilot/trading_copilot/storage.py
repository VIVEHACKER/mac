from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator


@dataclass(frozen=True)
class WatchlistItem:
    ticker: str
    note: str


@dataclass(frozen=True)
class Thesis:
    ticker: str
    direction: str
    statement: str
    invalidation: str


class TradingStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    ticker TEXT PRIMARY KEY,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS thesis (
                    ticker TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    invalidation TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS trade_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    side TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    risk_budget TEXT NOT NULL,
                    context TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def add_watchlist_item(self, ticker: str, note: str = "") -> None:
        self.initialize()
        normalized = normalize_ticker(ticker)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watchlist (ticker, note)
                VALUES (?, ?)
                ON CONFLICT(ticker) DO UPDATE SET note = excluded.note
                """,
                (normalized, note.strip()),
            )

    def list_watchlist(self) -> list[WatchlistItem]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ticker, note FROM watchlist ORDER BY ticker"
            ).fetchall()
        return [WatchlistItem(ticker=row["ticker"], note=row["note"]) for row in rows]

    def upsert_thesis(
        self, ticker: str, direction: str, statement: str, invalidation: str
    ) -> None:
        self.initialize()
        normalized = normalize_ticker(ticker)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO thesis (ticker, direction, statement, invalidation, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(ticker) DO UPDATE SET
                    direction = excluded.direction,
                    statement = excluded.statement,
                    invalidation = excluded.invalidation,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    normalized,
                    direction.strip().lower(),
                    statement.strip(),
                    invalidation.strip(),
                ),
            )

    def get_thesis(self, ticker: str) -> Thesis | None:
        self.initialize()
        normalized = normalize_ticker(ticker)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ticker, direction, statement, invalidation
                FROM thesis
                WHERE ticker = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return Thesis(
            ticker=row["ticker"],
            direction=row["direction"],
            statement=row["statement"],
            invalidation=row["invalidation"],
        )

    def record_pretrade(
        self,
        ticker: str,
        side: str,
        horizon: str,
        risk_budget: str,
        context: str,
    ) -> None:
        self.initialize()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trade_journal (ticker, side, horizon, risk_budget, context)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalize_ticker(ticker),
                    side.strip().lower(),
                    horizon.strip(),
                    risk_budget.strip(),
                    context.strip(),
                ),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("Ticker is required")
    return normalized


def tickers_from_items(items: Iterable[WatchlistItem]) -> list[str]:
    return [item.ticker for item in items]
