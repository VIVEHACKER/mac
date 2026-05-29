"""VXX 5% permanent sleeve overlay 근사 (변형 B).

단순화 가정:
- 매일 portfolio 95% + VXX 5% 합성 (월별 리밸런싱 근사: 비중 고정)
- VXX 부재 기간(2016-01-01 ~ 2018-01-24)은 SHY로 대체
- VXX contango 슬리피지는 실제 VXX 일수익률에 이미 반영됨
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb

VXX_WEIGHT = 0.05
TRADING_DAYS = 252


def _load_closes(db_path: str, symbols: list[str]) -> dict[str, dict]:
    con = duckdb.connect(db_path, read_only=True)
    placeholders = ",".join(f"'{s}'" for s in symbols)
    rows = con.execute(
        f"SELECT symbol, ts, close FROM bars WHERE symbol IN ({placeholders}) ORDER BY ts"
    ).fetchall()
    con.close()
    result: dict[str, dict] = {s: {} for s in symbols}
    for sym, ts, close in rows:
        result[sym][ts] = close
    return result


def _daily_returns(closes: dict) -> dict:
    dates = sorted(closes)
    rets = {}
    for i in range(1, len(dates)):
        d, pd = dates[i], dates[i - 1]
        if closes[pd] > 0:
            rets[d] = closes[d] / closes[pd] - 1.0
    return rets


def run(baseline_result, db_path: str = "data/store/trader.duckdb") -> dict:
    closes = _load_closes(db_path, ["VXX", "SHY"])
    vxx_rets = _daily_returns(closes["VXX"])
    shy_rets = _daily_returns(closes["SHY"])

    curve = baseline_result.equity_curve
    initial = curve[0].equity / (1 + curve[0].portfolio_return) if curve else 10000.0

    equity = initial
    peak = equity
    max_dd = 0.0
    log_returns: list[float] = []

    for pt in curve:
        base_r = pt.portfolio_return
        sleeve_r = vxx_rets.get(pt.ts) or shy_rets.get(pt.ts) or 0.0
        adj_r = (1 - VXX_WEIGHT) * base_r + VXX_WEIGHT * sleeve_r

        equity *= 1 + adj_r
        peak = max(peak, equity)
        dd = 1 - equity / peak
        max_dd = max(max_dd, dd)
        log_returns.append(math.log(1 + adj_r) if adj_r > -1 else -10.0)

    years = (curve[-1].ts - curve[0].ts).days / 365.25
    cagr = (equity / initial) ** (1 / years) - 1
    # rf=0, 엔진 _sharpe()와 동일 방식
    raw_rets = [math.exp(r) - 1 for r in log_returns]
    n = len(raw_rets)
    mean_r = sum(raw_rets) / n
    pstd_r = (sum((r - mean_r) ** 2 for r in raw_rets) / n) ** 0.5
    sharpe = mean_r / pstd_r * TRADING_DAYS**0.5 if pstd_r > 0 else 0.0

    excess = cagr - baseline_result.benchmark_annualized_return

    return {
        "hedge": "B: VXX 5% permanent sleeve",
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "excess_vs_spy": excess,
        "cost_drag": "VXX contango (~5%/월 정상시) 실제 가격에 반영",
        "note": (
            "VXX 부재(2016-2018.01.24)는 SHY 대체. "
            "95% 전략 + 5% VXX 일별 합성. "
            "실제 리밸런싱 마찰비 미반영."
        ),
    }


if __name__ == "__main__":
    # Run via: uv run python scripts/vxx_sleeve_overlay.py
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
