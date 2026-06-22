"""Live marks for the fund-book forward OOS: fetch current closes for the ledger's held names + SPY.

Pinned price snapshots are frozen at one date, so they cannot supply forward marks. The realised
forward return is the actual market outcome, so marks for scoring must be LIVE prices accumulated over
time. This fetches yfinance closes from the earliest rebal date to today for exactly the symbols the
ledger holds (+ benchmark), and writes the WIDE marks CSV the drill/engine consume.

Two readouts:
  • score_ledger (engine) scores CLOSED periods (entry_i marked at entry_{i+1}) — needs >=2 entries.
  • open_book_mtm() here marks the OPEN (latest) entry to today vs the benchmark — unrealised excess
    since inception, meaningful with a single entry.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def ledger_symbols(entries: list) -> list[str]:
    """원장 엔트리들의 보유종목 + 벤치마크 심볼 합집합(정렬, 대문자)."""
    syms: set[str] = set()
    for e in entries:
        syms.update(s.upper() for s in e.weights)
        syms.add(e.benchmark_symbol.upper())
    return sorted(syms)


def fetch_closes_yf(symbols: list[str], start: str, end) -> dict[str, dict[str, float]]:
    """yfinance 일별 종가(auto_adjust)를 {date_iso: {symbol: close}} 로. end 는 exclusive."""
    import pandas as pd
    import yfinance as yf

    syms = list(symbols)
    raw = yf.download(syms, start=start, end=end, auto_adjust=True, progress=False)
    close = raw["Close"]
    if isinstance(close, pd.Series):  # 단일 심볼이면 Series → DataFrame
        close = close.to_frame(name=syms[0])
    table: dict[str, dict[str, float]] = {}
    for ts, row in close.iterrows():
        iso = ts.date().isoformat()
        marks = {str(sym).upper(): float(val) for sym, val in row.items() if pd.notna(val)}
        if marks:
            table[iso] = marks
    return table


def build_live_marks(
    entries: list, *, end: date, fetch=fetch_closes_yf, extra_symbols=()
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """원장 종목+벤치(+extra_symbols)의 라이브 종가를 가장 이른 rebal_date 부터 end 까지 → (dates, table).

    ``extra_symbols`` = 현재 조립 북의 보유종목 — 미래 리밸이 신규 종목을 선택해도 record 시 entry
    price 가 marks 에 있도록 미리 포함시킨다(Codex P1). ``fetch`` 주입 가능(테스트=네트워크 무관)."""
    syms = sorted(set(ledger_symbols(entries)) | {s.upper() for s in extra_symbols})
    start = min(e.rebal_date for e in entries)  # ISO 문자열 = 사전순 최소
    table = fetch(syms, start, end)
    return sorted(table), table


def _weighted_return(
    weights: dict[str, float], entry_prices: dict[str, float], marks: dict[str, float]
) -> tuple[float, int] | None:
    """마크된 종목으로 renormalise 한 가중 미실현 수익률 + 마크된 종목 수. 마크 0개면 None."""
    total_w = 0.0
    weighted = 0.0
    marked = 0
    for sym, w in weights.items():
        mark = marks.get(sym)
        buy = entry_prices.get(sym)
        if mark is None or buy is None or buy <= 0:
            continue
        weighted += w * (mark / buy - 1.0)
        total_w += w
        marked += 1
    if total_w <= 0.0:
        return None
    return weighted / total_w, marked


def _benchmark_return(entry, marks: dict[str, float]) -> float | None:
    bench_mark = marks.get(entry.benchmark_symbol)
    if bench_mark is None or entry.benchmark_price <= 0:
        return None
    return bench_mark / entry.benchmark_price - 1.0


def open_book_mtm(entry, marks: dict[str, float]) -> dict | None:
    """열린(마지막) 엔트리의 미실현 mark-to-market vs 벤치마크. 보유 마크 0개/벤치 마크 없으면 None."""
    r = _weighted_return(entry.weights, entry.entry_prices, marks)
    bench = _benchmark_return(entry, marks)
    if r is None or bench is None:
        return None
    port, marked = r
    return {
        "port_return": port,
        "benchmark_return": bench,
        "unrealized_excess": port - bench,
        "marked": marked,
    }


def open_book_mtm_by_sleeve(entry, marks: dict[str, float]) -> dict[str, dict]:
    """슬리브별 미실현 MTM(entry.sleeve_weights 기준) vs 동일 벤치마크 — 미실현 초과를 슬리브로 귀속.

    realized `score_by_sleeve` 의 미실현 버전. sleeve_weights 없는(레거시) 엔트리/벤치 마크 없으면 {}."""
    sleeve_weights = getattr(entry, "sleeve_weights", None) or {}
    bench = _benchmark_return(entry, marks)
    if not sleeve_weights or bench is None:
        return {}
    out: dict[str, dict] = {}
    for sleeve, weights in sleeve_weights.items():
        r = _weighted_return(weights, entry.entry_prices, marks)
        if r is None:
            continue
        port, marked = r
        out[sleeve] = {
            "port_return": port,
            "unrealized_excess": port - bench,
            "marked": marked,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    from engine.fund_book_oos import load_ledger
    from scripts.fund_marks import DEFAULT_OUT, write_wide_marks

    p = argparse.ArgumentParser(description="Live marks (yfinance) for the fund-book OOS ledger")
    p.add_argument("--ledger", type=Path, default=ROOT / "out" / "fund-book-oos.jsonl")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--with-book",
        action="store_true",
        help="현재 조립 북의 보유종목도 마크에 포함(미래 리밸 신규 종목 entry price 보장)",
    )
    # --with-book 시 build_fund_book 에 넘길 스냅샷 경로(미지정 시 build_fund_book 디폴트)
    p.add_argument("--snapshot", type=Path, default=None)
    p.add_argument("--prices", type=Path, default=None)
    p.add_argument("--price-history", type=Path, default=None)
    p.add_argument("--momentum-snapshot", type=Path, default=None)
    args = p.parse_args(argv)

    entries = load_ledger(args.ledger)
    if not entries:
        raise SystemExit(f"원장이 비어 있음({args.ledger}) — 먼저 fund_book_oos.py 로 T0 기록")

    extra: list[str] = []
    if args.with_book:
        from scripts.fund_book import build_fund_book

        bf_kwargs = {
            k: getattr(args, k)
            for k in ("snapshot", "prices", "price_history", "momentum_snapshot")
            if getattr(args, k) is not None
        }
        book, _ = build_fund_book(**bf_kwargs)
        extra = [pos.symbol for pos in book.positions]

    end = date.today() + timedelta(days=1)  # yfinance end exclusive → 오늘 포함
    dates, table = build_live_marks(entries, end=end, extra_symbols=extra)
    if not dates:
        raise SystemExit("라이브 종가를 못 가져옴(네트워크/심볼 확인)")
    rows = write_wide_marks(args.out, dates, table)
    print(
        f"wrote {rows} dates × {len(ledger_symbols(entries))} symbols → {args.out} (latest {dates[-1]})"
    )

    mtm = open_book_mtm(entries[-1], table[dates[-1]])
    if mtm:
        print(
            f"open book MTM since {entries[-1].rebal_date}: "
            f"port {mtm['port_return']:+.2%} | bench {mtm['benchmark_return']:+.2%} | "
            f"unrealized excess {mtm['unrealized_excess']:+.2%} "
            f"({mtm['marked']}/{len(entries[-1].weights)} marked)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
