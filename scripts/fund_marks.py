"""Build a WIDE marks CSV (date + symbol columns) from LONG price snapshots.

The fund-book OOS drill (`scripts/fund_book_oos.py --marks`) and the engine loader
(`engine.fund_book_oos.load_mark_price_history_csv`) expect WIDE marks: first column = date,
every other column = a symbol's close. Our pinned price snapshots are LONG (`symbol,date,close`)
and split across two universes (megacaps + SPY in prices-ideal, sp400-600 in prices-*), so this
driver merges them and pivots to WIDE — covering every held name + benchmark in one file.

Integrity: default pinned inputs are read through `read_price_snapshot(verify=True)`, which enforces
the `.manifest.json` sha256 check (fail-closed on tampered/mismatched snapshots). Pinned snapshots are
frozen at one date, so the WIDE file only advances when snapshots are regenerated.
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
    # 각 가격 패밀리를 독립적으로 로컬→sibling 순 해석 — 로컬에 한 패밀리만 있어도
    # 누락 패밀리(예: SPY/megacap)는 ../trader 폴백에서 채운다(부분 스냅샷 대응).
    bases = (ROOT / "data" / "snapshots", ROOT.parent / "trader" / "data" / "snapshots")
    out: list[Path] = []
    for pat in _LONG_PATTERNS:
        for base in bases:
            hits = sorted(base.glob(pat))
            if hits:
                out.append(hits[-1])  # 최신 날짜
                break  # 이 패밀리 해결 → 다음 패턴
    return out


def build_wide_marks(
    long_paths: list[Path], *, since: date | None = None, verify: bool = True
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """LONG 스냅샷들을 (정렬된 ISO date 목록, {date: {symbol: close}}) 로 병합.

    ``read_price_snapshot(verify=...)`` 로 읽어 manifest 해시 검증을 통과한 데이터만 사용한다
    (verify=True 면 manifest 불일치/누락 시 fail-closed). ``since`` 이전 날짜는 제외. 심볼이
    겹치면 뒤 파일이 우선(megacap/sp400-600 은 실제로 겹치지 않음)."""
    import pandas as pd

    from data.price_snapshot import read_price_snapshot

    table: dict[str, dict[str, float]] = {}
    for path in long_paths:
        wide = read_price_snapshot(Path(path), verify=verify)  # date index × symbol cols, 해시검증
        if since is not None:
            wide = wide.loc[wide.index >= pd.Timestamp(since)]
        for ts, row in wide.iterrows():
            iso = ts.date().isoformat()
            dest = table.setdefault(iso, {})
            for sym, val in row.items():
                if pd.notna(val):
                    dest[str(sym).upper()] = float(val)
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
    p.add_argument(
        "--no-verify",
        action="store_true",
        help="skip manifest hash verification (only for explicit --prices without manifests)",
    )
    args = p.parse_args(argv)

    using_defaults = args.prices is None
    paths = args.prices or _resolve_default_prices()
    if not paths:
        raise SystemExit(
            "LONG 가격 스냅샷을 못 찾음(prices-ideal-*/prices-2*). --prices 로 직접 지정하세요."
        )
    # 기본 스냅샷은 두 패밀리(SPY/megacap + sp400-600)가 모두 있어야 marks 가 완전하다.
    if using_defaults and len(paths) < len(_LONG_PATTERNS):
        raise SystemExit(
            f"기본 스냅샷 패밀리 일부만 찾음({[p.name for p in paths]}) — "
            "prices-ideal-*(SPY/megacap)·prices-2*(sp400-600) 둘 다 필요. --prices 로 명시 지정하세요."
        )
    # 기본(핀) 입력은 manifest 검증 강제; 명시 --prices 는 의도적이므로 --no-verify 허용.
    verify = not args.no_verify if not using_defaults else True
    since = date.fromisoformat(args.since) if args.since else None
    dates, table = build_wide_marks(list(paths), since=since, verify=verify)
    if not dates:
        raise SystemExit(f"마크가 비어 있음(since={args.since}, paths={[str(p) for p in paths]})")
    n_syms = len({sym for d in dates for sym in table[d]})
    rows = write_wide_marks(args.out, dates, table)
    print(f"wrote {rows} dates × {n_syms} symbols → {args.out} (latest {dates[-1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
