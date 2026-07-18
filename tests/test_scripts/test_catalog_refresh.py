"""카탈로그 리프레시 드라이버 — 심볼 수집(합집합), 재시도/백오프, 실패 집계, 탑업 시작점."""

from __future__ import annotations

from datetime import date

from scripts.catalog_refresh import (
    FULL_START,
    INCREMENTAL_OVERLAP_DAYS,
    collect_symbols,
    market_map_symbols,
    refresh_symbols,
)


class _Bar:
    def __init__(self, ts: date) -> None:
        self.ts = ts


class _FakeCatalog:
    def __init__(self, fail_symbols: set[str] | None = None) -> None:
        self.stored: list[str] = []
        self._fail = fail_symbols or set()

    def put_bars(self, bars: list) -> int:
        symbol = bars[0].symbol
        if symbol in self._fail:
            raise RuntimeError("lock contention")
        self.stored.append(symbol)
        return len(bars)


def _bars_for(symbol: str) -> list:
    bar = _Bar(date(2026, 7, 16))
    bar.symbol = symbol  # type: ignore[attr-defined]
    return [bar]


def test_market_map_symbols_include_theme_and_catalog_macro() -> None:
    symbols = market_map_symbols()
    assert "NVDA" in symbols  # US 테마
    assert "TLT" in symbols  # catalog 소스 매크로
    assert symbols == sorted(symbols)


def test_collect_symbols_unions_catalog_and_map(tmp_path) -> None:
    # 카탈로그가 없으면 map 심볼만
    assert "NVDA" in collect_symbols(tmp_path / "missing.duckdb")

    import duckdb

    db = tmp_path / "cat.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE bars (symbol TEXT, market TEXT, freq TEXT, ts DATE)")
    con.execute("INSERT INTO bars VALUES ('ZZZT', 'us', '1d', DATE '2026-06-26')")
    con.execute("INSERT INTO bars VALUES ('BTC', 'crypto', '1d', DATE '2026-05-07')")
    con.close()
    symbols = collect_symbols(db)
    assert "ZZZT" in symbols  # 기존 카탈로그 심볼 유지
    assert "BTC" not in symbols  # US 만
    assert "NVDA" in symbols


def test_refresh_retries_with_backoff_then_succeeds() -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def fetch(symbol: str, market: str, start: date, end: date) -> list:
        calls.append(symbol)
        if len(calls) < 3:  # 처음 두 시도는 실패
            raise RuntimeError("rate limited")
        return _bars_for(symbol)

    catalog = _FakeCatalog()
    ok, failed = refresh_symbols(
        ["AAPL"],
        catalog=catalog,  # type: ignore[arg-type]
        fetch_bars=fetch,
        sleep=sleeps.append,
        retry_delays=(1.0, 2.0),
        throttle_s=0.0,
    )
    assert ok == ["AAPL"] and failed == []
    assert calls == ["AAPL"] * 3
    assert sleeps == [1.0, 2.0]  # 백오프만 (스로틀 0)


def test_refresh_counts_failures_and_continues() -> None:
    def fetch(symbol: str, market: str, start: date, end: date) -> list:
        if symbol == "BAD":
            raise RuntimeError("no data")
        return _bars_for(symbol)

    catalog = _FakeCatalog(fail_symbols={"LOCKED"})
    ok, failed = refresh_symbols(
        ["BAD", "GOOD", "LOCKED"],
        catalog=catalog,  # type: ignore[arg-type]
        fetch_bars=fetch,
        sleep=lambda _s: None,
        retry_delays=(),
        throttle_s=0.0,
    )
    assert ok == ["GOOD"]
    assert failed == ["BAD", "LOCKED"]  # fetch 실패 + put 실패 모두 집계, run 은 계속
    assert catalog.stored == ["GOOD"]


def test_refresh_incremental_uses_overlap_start_full_otherwise() -> None:
    seen_starts: dict[str, date] = {}

    def fetch(symbol: str, market: str, start: date, end: date) -> list:
        seen_starts[symbol] = start
        return _bars_for(symbol)

    last = date(2026, 6, 26)
    refresh_symbols(
        ["KNOWN", "NEWCOMER"],
        catalog=_FakeCatalog(),  # type: ignore[arg-type]
        fetch_bars=fetch,
        mode="incremental",
        starts={"KNOWN": last},
        sleep=lambda _s: None,
        throttle_s=0.0,
    )
    from datetime import timedelta

    assert seen_starts["KNOWN"] == last - timedelta(days=INCREMENTAL_OVERLAP_DAYS)
    assert seen_starts["NEWCOMER"] == FULL_START  # 카탈로그에 없던 심볼은 풀 히스토리


def test_map_scope_symbols_include_oos_ledger(tmp_path) -> None:
    import json

    ledger_dir = tmp_path / "out"
    ledger_dir.mkdir()
    (ledger_dir / "paper-oos-ledger-x.jsonl").write_text(
        json.dumps(
            {
                "rebal_date": "2026-06-05",
                "strategy_id": "x",
                "weights": {"ZQQZ": 1.0},
                "entry_prices": {"ZQQZ": 1.0},
                "benchmark_symbol": "SPY",
                "benchmark_price": 700.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    from scripts.catalog_refresh import map_scope_symbols

    symbols = map_scope_symbols(tmp_path)
    assert "ZQQZ" in symbols and "SPY" in symbols  # 원장 포지션 + 벤치마크
    assert "NVDA" in symbols and "TLT" in symbols  # market-map 심볼


def test_put_bars_lock_retry_succeeds_second_attempt() -> None:
    class _FlakyCatalog:
        def __init__(self) -> None:
            self.calls = 0

        def put_bars(self, bars: list) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("write lock held by another cron")
            return len(bars)

    sleeps: list[float] = []
    catalog = _FlakyCatalog()
    ok, failed = refresh_symbols(
        ["AAPL"],
        catalog=catalog,  # type: ignore[arg-type]
        fetch_bars=lambda s, m, a, b: _bars_for(s),
        sleep=sleeps.append,
        retry_delays=(),
        throttle_s=0.0,
    )
    assert ok == ["AAPL"] and failed == []
    assert catalog.calls == 2
    assert sleeps == [5.0]  # 락 재시도 백오프
