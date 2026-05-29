"""SPY put 5% notional overlay 근사 (변형 A).

단순화 가정:
- 연 비용 2.5%/yr = 매일 균등 차감 (252거래일 기준)
- 매월 말 SPY가 -10% 이상 하락 시: payoff = -(spm + 0.10) * 0.80 * 0.05
  (5% notional, 80% 회수율, 초과 하락분만)
- 옵션 만기/갱신 슬리피지 등은 2.5% 연비에 포함된 것으로 간주
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb

ANNUAL_COST = 0.025  # 2.5%/yr drag
NOTIONAL = 0.05  # 5% hedge ratio
STRIKE_OTM = 0.10  # 5% OTM put → 손실 -10% 부터 보호
RECOVERY_RATE = 0.80  # payoff 80% 회수
TRADING_DAYS = 252
DAILY_COST = ANNUAL_COST / TRADING_DAYS


def _load_spy_monthly(db_path: str) -> dict[tuple[int, int], float]:
    """월말 SPY 월수익률 반환 {(year, month): return}."""
    con = duckdb.connect(db_path, read_only=True)
    rows = con.execute("SELECT ts, close FROM bars WHERE symbol='SPY' ORDER BY ts").fetchall()
    con.close()
    monthly: dict[tuple[int, int], float] = {}
    prev_close: float | None = None
    prev_ym: tuple[int, int] | None = None
    for ts, close in rows:
        ym = (ts.year, ts.month)
        if prev_ym is not None and ym != prev_ym and prev_close is not None:
            monthly[prev_ym] = close / prev_close - 1.0  # type: ignore[assignment]
        prev_close = close
        prev_ym = ym
    return monthly


def run(baseline_result, db_path: str = "data/store/trader.duckdb") -> dict:
    """baseline FactorPortfolioResult에 put overlay 적용 후 지표 반환."""
    spy_monthly = _load_spy_monthly(db_path)
    curve = baseline_result.equity_curve
    initial = curve[0].equity / (1 + curve[0].portfolio_return) if curve else 10000.0

    equity = initial
    peak = equity
    max_dd = 0.0
    log_returns: list[float] = []
    prev_ym: tuple[int, int] | None = None

    for pt in curve:
        ym = (pt.ts.year, pt.ts.month)
        base_r = pt.portfolio_return
        adj_r = base_r - DAILY_COST

        # 월 마지막 날 — put payoff 적용
        if prev_ym is not None and ym != prev_ym:
            spm = spy_monthly.get(prev_ym, 0.0)
            if spm < -STRIKE_OTM:
                payoff = -(spm + STRIKE_OTM) * RECOVERY_RATE * NOTIONAL
                adj_r += payoff

        equity *= 1 + adj_r
        peak = max(peak, equity)
        dd = 1 - equity / peak
        max_dd = max(max_dd, dd)
        log_returns.append(math.log(1 + adj_r) if adj_r > -1 else -10.0)
        prev_ym = ym

    years = (curve[-1].ts - curve[0].ts).days / 365.25
    cagr = (equity / initial) ** (1 / years) - 1
    # rf=0, 엔진 _sharpe()와 동일 방식 (mean/pstdev * sqrt(252))
    raw_rets = [math.exp(r) - 1 for r in log_returns]
    n = len(raw_rets)
    mean_r = sum(raw_rets) / n
    pstd_r = (sum((r - mean_r) ** 2 for r in raw_rets) / n) ** 0.5
    sharpe = mean_r / pstd_r * TRADING_DAYS**0.5 if pstd_r > 0 else 0.0

    excess = cagr - baseline_result.benchmark_annualized_return

    return {
        "hedge": "A: SPY put 5% notional",
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "excess_vs_spy": excess,
        "cost_drag": f"~{ANNUAL_COST * 100:.1f}%/yr (fixed)",
        "note": (
            "5% OTM SPY put 근사. 연2.5% drag 균등 차감 + "
            "월SPY<-10% 시 초과분×80%×5% payoff. "
            "IV smile/만기슬리피지/early assignment 미반영."
        ),
    }


if __name__ == "__main__":
    # Run via: uv run python scripts/put_hedge_overlay.py
    # Requires a pickled FactorPortfolioResult at /tmp/baseline_result.pkl.
    # Generate with the run_factor_rotation_backtest API (see trader/cli.py).
    import pickle

    result_path = Path("/tmp/baseline_result.pkl")
    if not result_path.exists():
        raise SystemExit("No baseline result found. Run the backtest first.")
    with result_path.open("rb") as fh:
        baseline = pickle.load(fh)  # noqa: S301
    stats = run(baseline)
    for k, v in stats.items():
        print(f"{k}: {v}")
