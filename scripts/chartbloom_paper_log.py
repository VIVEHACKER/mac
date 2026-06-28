"""Pre-register fresh CHoCH signals (tagged by FVG-accompaniment) into the A-1 forward-OOS ledger.

Run on a schedule (e.g. a cron every few hours for crypto 4h). Each run appends, per symbol, any
CHoCH event confirmed on the last ``--recent`` CLOSED bars that is not yet logged — pre-registered
with its bar timestamp, direction, FVG-accompaniment flag, and entry price. Later,
scripts/chartbloom_paper_score.py scores the matured entries against prices that arrived
afterwards. That forward record is the only honest test of whether the A-1 finding
(merr_corpus/CHARTBLOOM_VALIDATION_RESULTS.md) — and the _CHOCH_FVG_GATE built on it — survives
out of sample.

FVG-accompaniment uses the SAME definition as the deployed gate (engine/chart/read._has_supporting_fvg):
a same-direction, unmitigated active FVG present at the decision bar.

Usage:
    python -m scripts.chartbloom_paper_log --symbols BTC/USDT,ETH/USDT,SOL/USDT --tf 4h
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from data.ingest.ccxt_crypto import fetch_ccxt_bars
from engine.chart.fvg import run_fvg
from engine.chart.structure import detect_swing_structure
from engine.chartbloom_oos import ChochSignalEntry, append_choch_signal

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = ROOT / "out" / "chartbloom-oos-ledger.jsonl"
_TF_HOURS = {
    "15m": 0.25,
    "30m": 0.5,
    "1h": 1.0,
    "2h": 2.0,
    "4h": 4.0,
    "6h": 6.0,
    "12h": 12.0,
    "1d": 24.0,
}


def _has_supporting_fvg(fvg_result, want_token: str) -> bool:
    """Same-direction unmitigated active FVG present? (mirrors read._has_supporting_fvg)."""
    if fvg_result is None:
        return False
    for z in fvg_result.active_fvgs:
        if z.direction == want_token and not z.mitigated and z.mitigation_type != "full":
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT")
    parser.add_argument("--tf", default="4h")
    parser.add_argument("--lookback", type=int, default=300)
    parser.add_argument("--recent", type=int, default=3, help="최근 N개 닫힌 봉 내 CHoCH까지 기록")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()

    end = date.today()
    span_days = max(5, int((args.lookback * _TF_HOURS.get(args.tf, 4.0)) / 24) + 5)
    start = end - timedelta(days=span_days)

    logged = skipped = 0
    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        try:
            bars = fetch_ccxt_bars(
                sym,
                start,
                end,
                timeframe=args.tf,
                exchange_id=args.exchange,
                intraday=(args.tf != "1d"),
            )
        except Exception as exc:  # noqa: BLE001 - one symbol's fetch failure must not abort the run
            print(f"  {sym}: FETCH FAIL — {exc}")
            continue
        closed = bars[:-1]  # drop the still-forming bar
        if len(closed) < 60:
            print(f"  {sym}: 봉 부족({len(closed)})")
            continue
        window = closed[-args.lookback :]
        ms = detect_swing_structure(window)
        fvgs = run_fvg(window)
        last_idx = len(window) - 1
        # 최근 N개 닫힌 봉에서 확정된 CHoCH 이벤트
        chochs = [
            e
            for e in (ms.events or [])
            if e.event_type.endswith("CHoCH") and e.bar_index >= last_idx - (args.recent - 1)
        ]
        if not chochs:
            print(f"  {sym}: 최근 {args.recent}봉 내 CHoCH 없음")
            continue
        for e in chochs:
            up = e.event_type.endswith("CHoCH") and (
                "UP" in e.event_type.upper() or (e.direction or "").upper().startswith("BULL")
            )
            direction = "long" if up else "short"
            want = "bullish" if up else "bearish"
            bar = window[e.bar_index]
            entry = ChochSignalEntry(
                logged_ts=str(bar.ts),
                symbol=sym,
                market="crypto",
                timeframe=args.tf,
                direction=direction,
                has_fvg=_has_supporting_fvg(fvgs, want),
                entry_price=bar.close,
            )
            try:
                append_choch_signal(args.ledger, entry)
                logged += 1
                print(
                    f"  {sym} @ {entry.logged_ts}: CHoCH {direction} "
                    f"{'+FVG' if entry.has_fvg else 'no-FVG'} → logged"
                )
            except ValueError:
                skipped += 1

    print(f"\n원장: {args.ledger}  | 신규 {logged} · 중복 {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
