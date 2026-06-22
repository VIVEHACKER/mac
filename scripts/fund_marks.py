"""Build a WIDE marks CSV (date + symbol columns) from LONG price snapshots.

The fund-book OOS drill (`scripts/fund_book_oos.py --marks`) and the engine loader
(`engine.fund_book_oos.load_mark_price_history_csv`) expect WIDE marks: first column = date,
every other column = a symbol's close. Our pinned price snapshots are LONG (`symbol,date,close`)
and split across two universes (megacaps + SPY in prices-ideal, sp400-600 in prices-*), so this
driver merges them and pivots to WIDE — covering every held name + benchmark in one file.

Pinned-snapshot note: snapshots are frozen at one date, so the WIDE file only advances when the
snapshots are regenerated (scripts/snapshot_prices.py). Forward scoring accumulates as that happens.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / "out" / "fund-marks.csv"
# LONG 스냅샷 글롭 (로컬 trader-fund → sibling ../trader 폴백, gitignore 대응)
_LONG_PATTERNS = ["prices-ideal-*.csv", "prices-2*.csv"]


def _resolve_default_prices() -> list[Path]:
    out: list[Path] = []
    for base in (ROOT / "data" / "snapshots", ROOT.parent / "trader" / "data" / "snapshots"):
        for pat in _LONG_PATTERNS:
            hits = sorted(base.glob(pat))
            if hits:
                out.append(hits[-1])  # 최신 날짜
        if out:
            return out
    return out


def build_wide_marks(
    long_paths: list[Path], *, since: date | None = None
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """LONG (symbol,date,close) CSV 들을 읽어 (정렬된 ISO date 목록, {date: {symbol: close}}) 반환.

    ``since`` 이전 날짜는 제외. 동일 (date, symbol) 충돌 시 마지막 파일 값이 우선(megacap/sp400-600은
    심볼이 겹치지 않으므로 실제 충돌은 드뭄)."""
    table: dict[str, dict[str, float]] = {}
    for path in long_paths:
        with Path(path).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            cols = {c.lower(): c for c in (reader.fieldnames or [])}
            sym_c = cols.get("symbol") or (reader.fieldnames or ["symbol"])[0]
            date_c = cols.get("date") or (reader.fieldnames or ["", "date"])[1]
            close_c = cols.get("close") or (reader.fieldnames or ["", "", "close"])[2]
            for row in reader:
                raw_date = (row.get(date_c) or "").strip()
                if not raw_date:
                    continue
                iso = raw_date[:10]
                if since is not None and date.fromisoformat(iso) < since:
                    continue
                sym = (row.get(sym_c) or "").strip().upper()
                raw_close = (row.get(close_c) or "").strip()
                if not sym or not raw_close:
                    continue
                try:
                    close = float(raw_close)
                except ValueError:
                    continue
                table.setdefault(iso, {})[sym] = close
    return sorted(table), table


def write_wide_marks(path: Path, dates: list[str], table: dict[str, dict[str, float]]) -> int:
    """WIDE CSV (header: date,SYM1,SYM2,...; date 당 1행) 작성. 작성한 데이터 행 수 반환."""
    symbols = sorted({sym for d in dates for sym in table.get(d, {})})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", *symbols])
        for d in dates:
            row = table.get(d, {})
            writer.writerow(
                [d, *(("" if sym not in row else f"{row[sym]:.6f}") for sym in symbols)]
            )
    return len(dates)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build WIDE marks CSV from LONG price snapshots")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--since", type=str, default=None, help="YYYY-MM-DD; drop earlier dates")
    p.add_argument(
        "--prices",
        type=Path,
        nargs="+",
        default=None,
        help="LONG (symbol,date,close) CSVs; default: latest prices-ideal-* + prices-2* (trader fallback)",
    )
    args = p.parse_args(argv)

    paths = args.prices or _resolve_default_prices()
    if not paths:
        raise SystemExit(
            "LONG 가격 스냅샷을 못 찾음(prices-ideal-*/prices-2*). --prices 로 직접 지정하세요."
        )
    since = date.fromisoformat(args.since) if args.since else None
    dates, table = build_wide_marks(list(paths), since=since)
    if not dates:
        raise SystemExit(f"마크가 비어 있음(since={args.since}, paths={[str(p) for p in paths]})")
    n_syms = len({sym for d in dates for sym in table[d]})
    rows = write_wide_marks(args.out, dates, table)
    print(f"wrote {rows} dates × {n_syms} symbols → {args.out} (latest {dates[-1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
