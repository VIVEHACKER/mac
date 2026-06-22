"""Refresh price snapshots to TODAY (yfinance, no API keys) so the LIVE fund book assembles on
current prices — the "actually usable" data path.

WHY this is enough (no fundamentals key needed): `scripts.core_basket.build_universe` resolves the PIT
cutoff `effective` from the PRICE snapshot's latest date (`closes.index.max()`), then looks fundamentals
up point-in-time (`asof_ts <= effective`). So fresh prices alone advance the whole assembly (core, hunt,
momentum) to today, while fundamentals stay PIT-correct from the existing catalog snapshot.

Both price families MUST advance together (momentum uses the SAME `effective` as core/hunt, which comes
from the broad price snapshot — refreshing only prices-ideal would be clamped to the stale broad date):
  prices-<today>.csv        : sp400-600 + megacap-gp  (core/hunt valuation prices, the `prices` family)
  prices-ideal-<today>.csv  : MEGACAPS(106) + SPY     (momentum sleeve time series, the `price_history`)

The dashboard resolver auto-picks the latest date of each family, so a fresh run makes the fund show
today's picks with no further wiring. The pinned 2026-06-01 snapshots (validation/backtest) are left
untouched — this only ADDS today-dated files for the live view.

Usage:
    python scripts/refresh_prices.py            # start 2018-01-01 .. today
    python scripts/refresh_prices.py --start 2011-01-01
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yfinance as yf  # noqa: E402

from data.price_snapshot import write_price_snapshot  # noqa: E402
from scripts.aqr_ideal_walkforward import MEGACAPS  # noqa: E402

SNAP_DIR = ROOT / "data" / "snapshots"
UNIV_DIR = ROOT / "data" / "universes"
BROAD_UNIVERSES = ["sp400-600-current.csv", "megacap-gp.csv"]


def _load_csv_symbols(*csv_names: str) -> list[str]:
    syms: set[str] = set()
    for name in csv_names:
        path = UNIV_DIR / name
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("symbol"):
                    syms.add(row["symbol"].upper())
    return sorted(syms)


def _download_closes(symbols: list[str], start: str, end: str):
    raw = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False)
    return raw["Close"]


def refresh(start: str, *, today: date) -> list[str]:
    """오늘 날짜의 두 가격 패밀리를 yfinance 로 생성. 작성한 파일 경로 목록 반환.

    원자성: 두 패밀리를 **먼저 모두 다운로드·검증**(coverage 비어있지 않음 + 종료일 일치)한 뒤에만
    파일을 게시한다 — 한쪽만 게시돼 대시보드가 fresh/stale 을 섞어 조립하는 일을 막는다(Codex P2)."""
    end = (today + timedelta(days=1)).isoformat()  # yfinance end exclusive → 오늘 포함

    broad = _load_csv_symbols(*BROAD_UNIVERSES)
    print(
        f"[refresh] downloading prices (sp400-600 + megacap-gp): {len(broad)} symbols {start}..{today}"
    )
    broad_closes = _download_closes(broad, start, end)
    ideal = sorted(set(MEGACAPS) | {"SPY"})
    print(
        f"[refresh] downloading prices-ideal (MEGACAPS + SPY): {len(ideal)} symbols {start}..{today}"
    )
    ideal_closes = _download_closes(ideal, start, end)

    # 게시 전 검증: 둘 다 데이터가 있고 종료일이 일치해야 한다(단일-cutoff 정합).
    if broad_closes.dropna(how="all").empty or ideal_closes.dropna(how="all").empty:
        raise SystemExit("다운로드 결과가 비어 있음(네트워크/심볼) — 아무 것도 게시하지 않음")
    bd = broad_closes.index.max().date()
    idd = ideal_closes.index.max().date()
    if bd != idd:
        raise SystemExit(
            f"두 패밀리 종료일 불일치(broad={bd}, ideal={idd}) — 섞인 조립 방지 위해 게시 안 함"
        )

    # 둘 다 성공 → 이제 게시(쓰기).
    m1 = write_price_snapshot(broad_closes, SNAP_DIR, name=f"prices-{today}")
    m2 = write_price_snapshot(ideal_closes, SNAP_DIR, name=f"prices-ideal-{today}")
    print(
        f"  -> prices-{today}.csv  rows={m1.row_count} symbols={m1.symbol_count} "
        f"dates {m1.date_start}..{m1.date_end}"
    )
    print(
        f"  -> prices-ideal-{today}.csv  rows={m2.row_count} symbols={m2.symbol_count} "
        f"dates {m2.date_start}..{m2.date_end}"
    )
    return [str(SNAP_DIR / f"prices-{today}.csv"), str(SNAP_DIR / f"prices-ideal-{today}.csv")]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Refresh price snapshots to today (yfinance, no keys)")
    p.add_argument("--start", default="2018-01-01", help="history start (default 2018-01-01)")
    args = p.parse_args(argv)
    refresh(args.start, today=date.today())
    print(
        "\n대시보드/`build_fund_book` 가 최신 날짜 스냅샷을 자동 선택합니다 — 펀드가 오늘 기준으로 조립됩니다.\n"
        "(펀더멘털은 기존 스냅샷에서 PIT 룩업; 카탈로그 갱신은 키 필요로 별도 단계)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
