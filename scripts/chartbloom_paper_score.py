"""Score the A-1 forward-OOS CHoCH ledger against realised prices.

Reads out/chartbloom-oos-ledger.jsonl, fetches each symbol's bars covering the logged period,
and for every entry whose horizon has elapsed computes the realised direction-signed forward
return. Reports the live CHoCH+FVG vs CHoCH-noFVG forward means and their spread per horizon,
plus the live/in-sample ratio. A live spread far below the in-sample figure
(merr_corpus/CHARTBLOOM_VALIDATION_RESULTS.md A-1) is the gate's premise overfitting in the wild
→ flip engine/chart/read._CHOCH_FVG_GATE to False.

Usage:
    python -m scripts.chartbloom_paper_score --tf 4h
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from data.ingest.ccxt_crypto import fetch_ccxt_bars
from engine.chartbloom_oos import (
    entry_key,
    load_chartbloom_ledger,
    score_chartbloom_ledger,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = ROOT / "out" / "chartbloom-oos-ledger.jsonl"
HORIZONS = (6, 12, 24)
# In-sample CHoCH+FVG − CHoCH-noFVG forward spread per horizon (crypto4h+stock pooled, n=1591):
# +6: +0.05−(−0.11)=+0.16%p / +12: +0.76−(−0.28)=+1.04%p / +24: +0.93−(−0.54)=+1.47%p.
INSAMPLE_SPREAD = {6: 0.0016, 12: 0.0104, 24: 0.0147}


def _parse_date(ts: str) -> date:
    return datetime.fromisoformat(ts).date()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", default="4h")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()

    entries = load_chartbloom_ledger(args.ledger)
    if not entries:
        print(f"원장 비어있음: {args.ledger}  — 먼저 scripts.chartbloom_paper_log 실행")
        return 0

    end = date.today()
    symbols = sorted({e.symbol for e in entries})
    bars_by_symbol: dict[str, list] = {}
    for sym in symbols:
        first = min(_parse_date(e.logged_ts) for e in entries if e.symbol == sym)
        try:
            bars_by_symbol[sym] = fetch_ccxt_bars(
                sym,
                first - timedelta(days=2),
                end,
                timeframe=args.tf,
                exchange_id=args.exchange,
                intraday=(args.tf != "1d"),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym}: FETCH FAIL — {exc}")
            bars_by_symbol[sym] = []

    ts_index = {
        sym: {str(b.ts): i for i, b in enumerate(bars)} for sym, bars in bars_by_symbol.items()
    }

    n_fvg = sum(1 for e in entries if e.has_fvg)
    print("=" * 70)
    print(
        f" CHoCH A-1 forward-OOS 트랙레코드 ({len(entries)} 기록: +FVG {n_fvg} / no-FVG {len(entries) - n_fvg}, {len(symbols)} 심볼)"
    )
    print("=" * 70)
    for h in HORIZONS:
        realized: dict[str, float] = {}
        for e in entries:
            bars = bars_by_symbol.get(e.symbol) or []
            idx = ts_index.get(e.symbol, {}).get(e.logged_ts)
            if idx is None or idx + h >= len(bars) or e.entry_price <= 0:
                continue
            fwd = bars[idx + h].close / e.entry_price - 1.0
            realized[entry_key(e)] = -fwd if e.direction == "short" else fwd
        rec = score_chartbloom_ledger(
            entries, realized, horizon=h, insample_spread=INSAMPLE_SPREAD.get(h)
        )
        print(f"\n## Horizon +{h}봉  (성숙 {rec.n_matured}/{len(entries)})")
        if rec.n_matured == 0:
            print(f"  아직 성숙한 기록 없음 — 신호 로깅 후 +{h}봉 경과 필요")
            continue
        print(
            f"  CHoCH+FVG  n={rec.with_fvg_n:<4} mean {rec.with_fvg_mean_fwd * 100:+.2f}% | "
            f"hit {rec.with_fvg_hit_rate * 100:.0f}%"
        )
        print(
            f"  CHoCH-noFVG n={rec.no_fvg_n:<4} mean {rec.no_fvg_mean_fwd * 100:+.2f}% | "
            f"hit {rec.no_fvg_hit_rate * 100:.0f}%"
        )
        vs = f"{rec.vs_insample:.2f}x" if rec.vs_insample is not None else "n/a"
        print(
            f"  +FVG − noFVG spread(live): {rec.fvg_minus_nofvg * 100:+.2f}%p | in-sample 대비 {vs}"
        )
    print(
        "\n주: spread > 0 이고 in-sample 대비 ≈1x 이상이면 A-1 OOS 확정 → 게이트 유지."
        "\n    spread ≤ 0 (또는 vs_insample « 1) 이면 과최적화 → read._CHOCH_FVG_GATE=False 롤백."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
