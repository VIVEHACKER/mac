"""Score the forward-OOS chart-signal ledger against realised prices.

Reads out/chart-oos-ledger.jsonl, fetches each symbol's bars covering the logged period,
and for every entry whose horizon has elapsed computes the realised forward return. Reports
the live ACT(ENTER+SCALE)−AVOID spread per horizon and the live/backtest ratio. A live spread
far below the backtested figure (docs/CHART_VALIDATION.md) is overfitting in the wild.

Usage:
    python -m scripts.chart_paper_score --tf 4h
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from data.ingest.ccxt_crypto import fetch_ccxt_bars
from engine.chart_oos import entry_key, load_chart_ledger, score_chart_ledger

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = ROOT / "out" / "chart-oos-ledger.jsonl"
HORIZONS = (5, 10, 20)
# Backtested (gated, crypto 4h) ACT−AVOID spread per horizon — the live yardstick.
BACKTEST_ACT_AVOID = {5: 0.0015, 10: 0.0032, 20: 0.0050}


def _parse_date(ts: str) -> date:
    return datetime.fromisoformat(ts).date()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", default="4h")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()

    entries = load_chart_ledger(args.ledger)
    if not entries:
        print(f"원장 비어있음: {args.ledger}  — 먼저 scripts.chart_paper_log 실행")
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

    print("=" * 66)
    print(f" 차트 신호 forward-OOS 트랙레코드 ({len(entries)} 기록, {len(symbols)} 심볼)")
    print("=" * 66)
    for h in HORIZONS:
        realized: dict[str, float] = {}
        for e in entries:
            bars = bars_by_symbol.get(e.symbol) or []
            idx = ts_index.get(e.symbol, {}).get(e.logged_ts)
            if idx is None or idx + h >= len(bars) or e.entry_price <= 0:
                continue
            fwd = bars[idx + h].close / e.entry_price - 1.0
            realized[entry_key(e)] = -fwd if e.direction == "short" else fwd
        rec = score_chart_ledger(
            entries, realized, horizon=h, backtest_act_avoid=BACKTEST_ACT_AVOID.get(h)
        )
        print(f"\n## Horizon +{h}봉  (성숙 {rec.n_matured}/{len(entries)})")
        if rec.n_matured == 0:
            print(f"  아직 성숙한 기록 없음 — 신호 로깅 후 +{h}봉 경과 필요")
            continue
        for b in rec.buckets:
            if b.n:
                print(
                    f"  {b.decision:<18} n={b.n:<4} mean {b.mean_fwd * 100:+.2f}% | hit {b.hit_rate * 100:.0f}%"
                )
        vs = f"{rec.vs_backtest:.2f}x" if rec.vs_backtest is not None else "n/a"
        print(
            f"  ACT−AVOID(live): {rec.act_minus_avoid * 100:+.2f}% (ACT n={rec.act_n}, "
            f"적중 {rec.act_hit_rate * 100:.0f}%) | 백테스트 대비 {vs}"
        )
    print("\n주: vs_backtest < 1 이면 라이브가 백테스트보다 약함 = 과최적화 신호.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
