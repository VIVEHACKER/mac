from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from data.catalog import DEFAULT_CATALOG_PATH, MarketDataCatalog
from data.delistings import load_delisting_returns_csv
from data.fundamentals_csv import load_fundamentals_csv
from data.ingest.alpaca_live import (
    AlpacaStreamTimeoutError,
    fetch_alpaca_latest_stock_bars,
    stream_alpaca_stock_bars,
)
from data.ingest.ccxt_crypto import fetch_ccxt_bars, normalize_crypto_symbol
from data.ingest.crypto_microstructure import fetch_funding_history
from data.ingest.crypto_open_interest import fetch_open_interest_history, to_perp_symbol
from data.ingest.crypto_orderbook import fetch_order_book
from data.ingest.fred_macro import fetch_fred_series
from data.ingest.krx_flow_csv import KrxFlowCsvError, parse_krx_flow_csv
from data.ingest.krx_flows import KrxFlowError, fetch_krx_flows, fetch_naver_investor_flows
from data.ingest.option_sentiment import vix_from_macro
from data.ingest.pykrx_kr import fetch_pykrx_bars, normalize_kr_symbol
from data.ingest.yahoo import YAHOO_ADJUSTED_SOURCE_MARKER, YahooDataError, fetch_yahoo_bars
from data.ingest.yahoo_options import YahooOptionChainError, fetch_yahoo_option_quotes
from data.ingest.yfinance_fundamentals import fetch_yfinance_fundamentals
from data.models import (
    CryptoFundingRecord,
    DelistingReturn,
    FlowRecord,
    FundamentalRecord,
    MacroObservation,
    OpenInterestRecord,
    OptionSentimentRecord,
    OrderBookSnapshot,
    PriceBar,
    UniverseMember,
    ValuationRecord,
)
from data.quality import DataQualityIssue, evaluate_catalog_quality, format_quality_report
from data.universe import load_universe_members_csv
from data.universe_audit import format_universe_audit_report, run_universe_audit
from engine.backtest import format_backtest_report, run_momentum_backtest
from engine.chart.read import format_chart_read, read_chart
from engine.compounder import rank_compounders
from engine.compounder_dossier import build_dossier, format_dossier_markdown
from engine.factor_portfolio import (
    FactorPortfolioResult,
    FactorWeights,
    format_factor_portfolio_report,
    run_factor_rotation_backtest,
)
from engine.live import (
    LiveTradingBlockedError,
    LiveTradingPolicy,
    assert_live_order_submission_enabled,
    assert_live_trading_enabled,
    live_risk_policy,
    load_live_trading_policy,
)
from engine.paper import PaperBroker
from engine.paper_oos import (
    PaperOOSEntry,
    load_ledger,
    load_mark_price_history_csv,
    mark_prices_at_dates,
    score_ledger,
)
from engine.portfolio import (
    format_portfolio_report,
    format_screen_report,
    run_momentum_rotation_backtest,
    screen_momentum,
)
from engine.robustness import format_robustness_report, run_momentum_robustness_grid
from engine.validation import (
    FactorValidationThresholds,
    StressWindow,
    format_factor_validation_suite,
    parse_stress_windows,
    run_factor_validation_suite,
)
from engine.walkforward import (
    SELECTION_METRICS,
    format_walk_forward_report,
    run_factor_walk_forward,
)
from risk.equity_track import EquityTrackStore
from risk.exposure import ExposureLimits, build_exposure_report, check_exposure_limits
from risk.halt_state import HaltStateStore
from risk.kill_switch import check_kill_switch
from risk.policy import RiskPolicy
from risk.shortability import (
    ShortabilityCheck,
    check_shortability,
    load_short_availability_csv,
)
from strategies.factor_aqr import rank_aqr_factors
from strategies.statarb_pairs import (
    PairAnalysis,
    PairBacktestResult,
    analyze_pair,
    backtest_pair_mean_reversion,
)
from trader.execution.adapters.alpaca import AlpacaBrokerAdapter
from trader.execution.adapters.fake import FakeBrokerAdapter
from trader.execution.adapters.manual import ManualBrokerAdapter
from trader.execution.broker import AccountSnapshot, BrokerAdapter, BrokerClock, PositionSnapshot
from trader.execution.intents import OrderIntent
from trader.execution.order_store import JsonlOrderStore
from trader.execution.reconciler import (
    expected_positions_from_store,
    reconcile_in_flight,
    reconcile_positions,
)
from trader.execution.runner import FillPoll, process_order_intents
from trader.operations.drills import DrillLog, DrillRecord, DrillSummary
from trader.research_registry import (
    PromotionGate,
    ResearchRegistry,
    evaluate_promotion,
    make_evidence,
)
from valuation.composite import composite_fair_value, confidence_from_dispersion
from valuation.dcf import discounted_cash_flow
from valuation.entry import average_true_range_pct, make_entry_plan
from valuation.option_vol import (
    VixCalculationResult,
    calculate_vix_like_index,
    load_option_quotes_csv,
)
from valuation.score import discount_pct, rating_from_discount

ROOT = Path(__file__).resolve().parents[1]
COPILOT_DIR = ROOT / "trading-copilot"
DEFAULT_FINANCIAL_SERVICES_DIR = ROOT / "financial-services"
DEFAULT_DB = COPILOT_DIR / "data" / "copilot.db"
DEFAULT_CATALOG_DB = ROOT / DEFAULT_CATALOG_PATH
DEFAULT_LIVE_CATALOG_DB = ROOT / "data" / "store" / "live-prices.duckdb"
DEFAULT_ORDER_LOG = ROOT / "data" / "store" / "live-orders.jsonl"
DEFAULT_MANUAL_TICKET_LOG = ROOT / "data" / "store" / "manual-order-tickets.jsonl"
DEFAULT_HALT_STATE = ROOT / "data" / "store" / "live-halt.json"
DEFAULT_EQUITY_STATE = ROOT / "data" / "store" / "live-equity.json"
DEFAULT_RESEARCH_REGISTRY = ROOT / "data" / "store" / "research-registry.jsonl"
DEFAULT_DRILL_LOG = ROOT / "data" / "store" / "live-drills.jsonl"
DEFAULT_PAPER_OOS_DIR = ROOT / "out"
CORE_COMMANDS = {
    "init",
    "ingest",
    "bars",
    "chart-read",
    "status",
    "screen",
    "backtest",
    "portfolio",
    "factor-portfolio",
    "compounder-scan",
    "walk-forward",
    "validate-model",
    "universe-audit",
    "pair",
    "vix-calc",
    "macro",
    "fundamentals",
    "flows",
    "factor",
    "valuate",
    "entry",
    "paper",
    "paper-exposure",
    "risk-check",
    "live-policy",
    "live-readiness",
    "live-halt",
    "live-drill",
    "live-reconcile",
    "live-cancel",
    "rebalance-plan",
    "live-price-ingest",
    "live-price-stream",
    "live-dry-run",
    "live-submit",
    "live-ticket",
    "model-gate",
    "robustness",
    "quality",
    "dashboard",
    "market-map",
    "copilot",
}


def main(argv: list[str] | None = None) -> int:
    """Run the root trading system, with copilot commands kept as fallback."""
    args = list(sys.argv[1:] if argv is None else argv)
    load_dotenv(ROOT / ".env")

    if not args or args[0] in {"-h", "--help"}:
        build_parser().print_help()
        return 0
    if args[0] not in CORE_COMMANDS:
        return _run_copilot(args)

    parser = build_parser()
    parsed = parser.parse_args(args)

    if parsed.command == "copilot":
        return _run_copilot(parsed.args)
    if parsed.command == "init":
        return _run_init(parsed.catalog_db)
    if parsed.command == "ingest":
        return _run_ingest(parsed)
    if parsed.command == "bars":
        return _run_bars(parsed)
    if parsed.command == "chart-read":
        return _run_chart_read(parsed)
    if parsed.command == "status":
        return _run_status(parsed.catalog_db)
    if parsed.command == "screen":
        return _run_screen(parsed)
    if parsed.command == "backtest":
        return _run_backtest(parsed)
    if parsed.command == "portfolio":
        return _run_portfolio(parsed)
    if parsed.command == "factor-portfolio":
        return _run_factor_portfolio(parsed)
    if parsed.command == "compounder-scan":
        return _run_compounder_scan(parsed)
    if parsed.command == "walk-forward":
        return _run_walk_forward(parsed)
    if parsed.command == "validate-model":
        return _run_validate_model(parsed)
    if parsed.command == "universe-audit":
        return _run_universe_audit(parsed)
    if parsed.command == "pair":
        return _run_pair(parsed)
    if parsed.command == "vix-calc":
        return _run_vix_calc(parsed)
    if parsed.command == "macro":
        return _run_macro(parsed)
    if parsed.command == "fundamentals":
        return _run_fundamentals(parsed)
    if parsed.command == "flows":
        return _run_flows(parsed)
    if parsed.command == "factor":
        return _run_factor(parsed)
    if parsed.command == "valuate":
        return _run_valuate(parsed)
    if parsed.command == "entry":
        return _run_entry(parsed)
    if parsed.command == "paper":
        return _run_paper(parsed)
    if parsed.command == "paper-exposure":
        return _run_paper_exposure(parsed)
    if parsed.command == "risk-check":
        return _run_risk_check(parsed)
    if parsed.command == "live-policy":
        return _run_live_policy()
    if parsed.command == "live-readiness":
        return _run_live_readiness(parsed)
    if parsed.command == "live-halt":
        return _run_live_halt(parsed)
    if parsed.command == "live-drill":
        return _run_live_drill(parsed)
    if parsed.command == "live-reconcile":
        return _run_live_reconcile(parsed)
    if parsed.command == "live-cancel":
        return _run_live_cancel(parsed)
    if parsed.command == "rebalance-plan":
        return _run_rebalance_plan(parsed)
    if parsed.command == "live-price-ingest":
        return _run_live_price_ingest(parsed)
    if parsed.command == "live-price-stream":
        return _run_live_price_stream(parsed)
    if parsed.command == "live-dry-run":
        return _run_live_dry_run(parsed)
    if parsed.command == "live-submit":
        return _run_live_submit(parsed)
    if parsed.command == "live-ticket":
        return _run_live_ticket(parsed)
    if parsed.command == "model-gate":
        return _run_model_gate(parsed)
    if parsed.command == "robustness":
        return _run_robustness(parsed)
    if parsed.command == "quality":
        return _run_quality(parsed)
    if parsed.command == "dashboard":
        return _run_dashboard(parsed)
    if parsed.command == "market-map":
        return _run_market_map(parsed)

    parser.print_help()
    return 2


def _default_live_catalog_db() -> Path:
    return Path(os.getenv("LIVE_CATALOG_DB", str(DEFAULT_LIVE_CATALOG_DB)))


def _default_live_mark_deviation() -> float:
    return float(os.getenv("LIVE_MAX_MARK_DEVIATION", "0.02") or "0.02")


def _default_paper_oos_prices() -> Path | None:
    raw = os.getenv("LIVE_PAPER_OOS_PRICES", "").strip()
    return Path(raw) if raw else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trader",
        description=(
            "Runnable trading MVP: ingest price data, query DuckDB, and backtest a "
            "time-series momentum strategy. Unknown commands are forwarded to trading-copilot."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Initialize the root DuckDB catalog and copilot DB.")
    init.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    ingest = sub.add_parser("ingest", help="Fetch daily bars and store them in DuckDB.")
    _add_market_symbols_args(ingest)
    _add_date_args(ingest)
    _add_provider_arg(ingest)
    ingest.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    bars = sub.add_parser("bars", help="Print stored bars from the DuckDB catalog.")
    _add_market_symbol_args(bars)
    _add_date_args(bars, required=False)
    bars.add_argument("--limit", type=int, default=10)
    bars.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    chart_read = sub.add_parser(
        "chart-read",
        help="Read the chart (FVG/OB/매물대/볼륨/호가/OI/패턴) — ADVISORY context only. "
        "Validation found NO predictive edge in the entry states (ENTER hit-rate 38-42%%, "
        "IC negative at all horizons; docs/CHART_VALIDATION.md): not an entry trigger.",
    )
    _add_market_symbol_args(chart_read)
    chart_read.add_argument(
        "--tf", default=None, help="Timeframe e.g. 15m, 1h, 4h, 1d. Default: crypto 4h, stocks 1d."
    )
    chart_read.add_argument("--direction", default="long", choices=["long", "short"])
    chart_read.add_argument(
        "--mean-reversion",
        default="auto",
        choices=["auto", "on", "off"],
        help="평균회귀 프리미엄-추격 게이트(되돌림만 진입). auto=크립토 on/주식 off. "
        "근거 docs/CHART_VALIDATION.md.",
    )
    chart_read.add_argument("--lookback", type=int, default=300, help="Bars to analyze.")
    chart_read.add_argument("--exchange", default="binance", help="ccxt exchange id (crypto).")
    chart_read.add_argument(
        "--source",
        default="auto",
        choices=["auto", "catalog", "live"],
        help="auto: catalog if present, else live fetch.",
    )
    chart_read.add_argument(
        "--with-orderbook", action="store_true", help="Fetch live L2 order book (crypto)."
    )
    chart_read.add_argument(
        "--with-oi", action="store_true", help="Fetch open interest + funding (crypto)."
    )
    _add_provider_arg(chart_read)
    chart_read.add_argument("--output", type=Path)
    chart_read.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    status = sub.add_parser("status", help="Show stored catalog coverage.")
    status.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    screen = sub.add_parser("screen", help="Rank a stored/fetched universe by lookback momentum.")
    _add_market_symbols_args(screen)
    _add_date_args(screen)
    screen.add_argument("--lookback", type=int, default=126)
    screen.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(screen)
    screen.add_argument("--output", type=Path)
    screen.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    backtest = sub.add_parser(
        "backtest",
        help="Run a no-key Yahoo-backed momentum backtest, auto-ingesting missing bars.",
    )
    _add_market_symbol_args(backtest)
    _add_date_args(backtest)
    backtest.add_argument("--lookback", type=int, default=126)
    backtest.add_argument("--initial-cash", type=float, default=10_000.0)
    backtest.add_argument("--fee-bps", type=float, default=2.0)
    backtest.add_argument("--max-position", type=float, default=1.0)
    backtest.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(backtest)
    _add_benchmark_args(backtest)
    backtest.add_argument("--output", type=Path)
    backtest.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    portfolio = sub.add_parser(
        "portfolio",
        help="Backtest a top-N cross-sectional momentum rotation portfolio.",
    )
    _add_market_symbols_args(portfolio)
    _add_date_args(portfolio)
    portfolio.add_argument("--lookback", type=int, default=126)
    portfolio.add_argument("--top-n", type=int, default=3)
    portfolio.add_argument("--rebalance-days", type=int, default=21)
    portfolio.add_argument("--initial-cash", type=float, default=10_000.0)
    portfolio.add_argument("--fee-bps", type=float, default=2.0)
    portfolio.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(portfolio)
    _add_benchmark_args(portfolio)
    _add_pit_universe_args(portfolio)
    _add_delisting_return_args(portfolio)
    _add_preflight_audit_args(portfolio)
    portfolio.add_argument("--output", type=Path)
    portfolio.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    factor_portfolio = sub.add_parser(
        "factor-portfolio",
        help="Backtest multi-factor rotation with reversal, low-vol and risk filter controls.",
    )
    _add_market_symbols_args(factor_portfolio)
    _add_date_args(factor_portfolio)
    _add_factor_portfolio_args(factor_portfolio)
    factor_portfolio.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(factor_portfolio)
    _add_benchmark_args(factor_portfolio)
    _add_pit_universe_args(factor_portfolio)
    _add_delisting_return_args(factor_portfolio)
    _add_preflight_audit_args(factor_portfolio)
    factor_portfolio.add_argument("--output", type=Path)
    factor_portfolio.add_argument(
        "--returns-output",
        type=Path,
        help="Write the daily return series (date,portfolio_return,benchmark_return) to this CSV "
        "for downstream statistical-significance analysis (DSR, block bootstrap).",
    )
    factor_portfolio.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    compounder = sub.add_parser(
        "compounder-scan",
        help="Score a universe as long-term compounder candidates (3 archetypes) + dossiers.",
    )
    _add_market_symbols_args(compounder)
    compounder.add_argument("--as-of", default=date.today().isoformat())
    compounder.add_argument("--top-n", type=int, default=20)
    compounder.add_argument(
        "--archetype",
        default=None,
        choices=["profitable_compounder", "hypergrowth_disruptor", "value_turnaround"],
    )
    compounder.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="Pin fundamentals to a content-verified snapshot CSV.",
    )
    compounder.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_pit_universe_args(compounder)
    compounder.add_argument("--output", type=Path)
    compounder.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)
    compounder.add_argument(
        "--sectors-csv",
        type=Path,
        default=None,
        help="CSV (symbol,sic,sector) to enable sector-aware scoring "
        "(financials: FCF metrics excluded).",
    )

    walk_forward = sub.add_parser(
        "walk-forward",
        help="Run factor walk-forward parameter selection and out-of-sample validation.",
    )
    _add_market_symbols_args(walk_forward)
    _add_date_args(walk_forward)
    _add_factor_portfolio_args(walk_forward)
    _add_crash_hedge_search_args(walk_forward)
    walk_forward.add_argument("--train-years", type=int, default=5)
    walk_forward.add_argument(
        "--validation-years",
        type=int,
        default=0,
        help="Use the last N train years as an inner validation window for parameter selection.",
    )
    walk_forward.add_argument("--test-years", type=int, default=3)
    walk_forward.add_argument("--step-years", type=int, default=1)
    walk_forward.add_argument("--momentum-lookbacks", default="126,252")
    walk_forward.add_argument("--top-ns", default="2,3")
    walk_forward.add_argument("--reversal-lookbacks", help="Comma-separated reversal lookbacks.")
    walk_forward.add_argument(
        "--volatility-lookbacks", help="Comma-separated volatility lookbacks."
    )
    walk_forward.add_argument(
        "--rebalance-days-values", help="Comma-separated rebalance intervals."
    )
    walk_forward.add_argument(
        "--defensive-symbols",
        help="Comma-separated defensive assets. Use CASH to hold cash when risk-off.",
    )
    walk_forward.add_argument(
        "--max-risk-weights",
        help="Comma-separated max risky-asset weights for search, e.g. 0.7,1.0.",
    )
    walk_forward.add_argument(
        "--drawdown-guards",
        help="Comma-separated strategy drawdown guard thresholds for search, e.g. 0,0.15.",
    )
    walk_forward.add_argument(
        "--risk-filter-lookbacks",
        help="Comma-separated risk-filter lookbacks for search. 0 disables the filter.",
    )
    walk_forward.add_argument(
        "--weighting-modes",
        help="Comma-separated weighting modes for search: inverse-vol,equal.",
    )
    walk_forward.add_argument(
        "--selection-metric",
        default="annualized-excess",
        choices=sorted(SELECTION_METRICS),
        help="Train-window objective used to choose the next test-window parameter set.",
    )
    walk_forward.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(walk_forward)
    _add_benchmark_args(walk_forward)
    _add_pit_universe_args(walk_forward)
    _add_delisting_return_args(walk_forward)
    _add_preflight_audit_args(walk_forward)
    walk_forward.add_argument("--output", type=Path)
    walk_forward.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    validate_model = sub.add_parser(
        "validate-model",
        help="Run promotion-grade factor validation: walk-forward, fee stress, parameter perturbation, and stress windows.",
    )
    _add_market_symbols_args(validate_model)
    _add_date_args(validate_model)
    _add_factor_portfolio_args(validate_model)
    _add_crash_hedge_search_args(validate_model)
    validate_model.add_argument("--train-years", type=int, default=5)
    validate_model.add_argument("--validation-years", type=int, default=0)
    validate_model.add_argument("--test-years", type=int, default=3)
    validate_model.add_argument("--step-years", type=int, default=1)
    validate_model.add_argument("--momentum-lookbacks", default="126,252")
    validate_model.add_argument("--top-ns", default="2,3")
    validate_model.add_argument("--reversal-lookbacks")
    validate_model.add_argument("--volatility-lookbacks")
    validate_model.add_argument("--rebalance-days-values")
    validate_model.add_argument("--defensive-symbols")
    validate_model.add_argument("--max-risk-weights")
    validate_model.add_argument("--drawdown-guards")
    validate_model.add_argument("--risk-filter-lookbacks")
    validate_model.add_argument("--weighting-modes")
    validate_model.add_argument(
        "--selection-metric",
        default="return-drawdown",
        choices=sorted(SELECTION_METRICS),
    )
    validate_model.add_argument("--fee-stress-bps", default="2,5,10")
    validate_model.add_argument(
        "--stress-windows",
        default="gfc:2008-01-01:2009-12-31,covid:2020-02-15:2020-05-31,rates-2022:2022-01-01:2022-12-31",
    )
    validate_model.add_argument("--min-walk-forward-windows", type=int, default=8)
    validate_model.add_argument("--min-positive-test-rate", type=float, default=0.60)
    validate_model.add_argument("--min-average-test-excess", type=float, default=0.0)
    validate_model.add_argument("--max-worst-test-drawdown", type=float, default=0.30)
    validate_model.add_argument("--min-parameter-positive-rate", type=float, default=0.60)
    validate_model.add_argument("--min-stress-windows", type=int, default=0)
    validate_model.add_argument(
        "--min-stress-return",
        type=float,
        default=0.0,
        help="Minimum total return required in every tested stress/crash window.",
    )
    validate_model.add_argument("--max-stress-drawdown", type=float, default=0.35)
    validate_model.add_argument("--record-gate", action="store_true")
    validate_model.add_argument("--strategy-id")
    validate_model.add_argument("--params-label")
    validate_model.add_argument("--registry", type=Path, default=DEFAULT_RESEARCH_REGISTRY)
    validate_model.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(validate_model)
    _add_benchmark_args(validate_model)
    _add_pit_universe_args(validate_model)
    _add_delisting_return_args(validate_model)
    _add_preflight_audit_args(validate_model)
    validate_model.add_argument("--output", type=Path)
    validate_model.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    universe_audit = sub.add_parser(
        "universe-audit",
        help="Check PIT universe readiness: bars, delistings, and optional fundamentals.",
    )
    universe_audit.add_argument("symbols", nargs="?", default="ALL")
    universe_audit.add_argument(
        "--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"]
    )
    _add_date_args(universe_audit)
    _add_pit_universe_args(universe_audit)
    _add_delisting_return_args(universe_audit)
    universe_audit.add_argument("--require-fundamentals", action="store_true")
    universe_audit.add_argument("--no-require-delistings", action="store_true")
    universe_audit.add_argument("--rebalance-days", type=int, default=21)
    universe_audit.add_argument("--strict", action="store_true")
    universe_audit.add_argument("--output", type=Path)
    universe_audit.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    pair = sub.add_parser(
        "pair",
        help="Analyze a two-asset statistical-arbitrage spread from catalog bars.",
    )
    pair.add_argument("first")
    pair.add_argument("second")
    pair.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])
    _add_date_args(pair)
    pair.add_argument("--lookback", type=int, default=252)
    pair.add_argument("--entry-z", type=float, default=2.0)
    pair.add_argument("--exit-z", type=float, default=0.5)
    pair.add_argument("--min-observations", type=int, default=60)
    pair.add_argument(
        "--validate", action="store_true", help="Run rolling cost-adjusted validation."
    )
    pair.add_argument("--fee-bps", type=float, default=2.0)
    pair.add_argument("--slippage-bps", type=float, default=2.0)
    pair.add_argument("--min-trades", type=int, default=3)
    pair.add_argument("--min-sharpe", type=float, default=0.0)
    pair.add_argument("--max-drawdown", type=float, default=0.2)
    pair.add_argument("--shortability-csv", type=Path)
    pair.add_argument("--require-shortability", action="store_true")
    pair.add_argument("--max-borrow-fee-bps", type=float, default=500.0)
    pair.add_argument("--shortability-max-age-days", type=int, default=2)
    pair.add_argument(
        "--min-shortability-confidence",
        default="medium",
        choices=["low", "medium", "high"],
    )
    pair.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(pair)
    pair.add_argument("--output", type=Path)
    pair.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    vix_calc = sub.add_parser(
        "vix-calc",
        help="Calculate a VIX-like 30-day volatility index from an option-chain CSV.",
    )
    vix_calc.add_argument("--source", choices=["csv", "yahoo"], default="csv")
    vix_calc.add_argument("--file", type=Path)
    vix_calc.add_argument("--underlying", help="Yahoo option-chain underlying, e.g. SPY.")
    vix_calc.add_argument("--expirations", help="Comma-separated YYYY-MM-DD expirations for Yahoo.")
    vix_calc.add_argument("--as-of", default=date.today().isoformat())
    vix_calc.add_argument("--target-days", type=int, default=30)
    vix_calc.add_argument(
        "--risk-free-rate",
        type=float,
        default=None,
        help="Annualized decimal rate, e.g. 0.045 for 4.5%.",
    )
    vix_calc.add_argument(
        "--risk-free-series",
        default="DGS10",
        help="Catalog macro series used when --risk-free-rate is omitted. Use empty string to disable.",
    )
    vix_calc.add_argument("--risk-free-max-age-days", type=int, default=14)
    vix_calc.add_argument("--max-option-quote-age-days", type=int, default=7)
    vix_calc.add_argument("--max-bid-ask-spread-pct", type=float, default=0.5)
    vix_calc.add_argument("--require-last-trade", action="store_true")
    vix_calc.add_argument("--strict-quality", action="store_true")
    vix_calc.add_argument("--store", action="store_true", help="Store result in option_sentiment.")
    vix_calc.add_argument("--market", default="US")
    vix_calc.add_argument("--output", type=Path)
    vix_calc.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    robustness = sub.add_parser(
        "robustness",
        help="Run train/test parameter-grid checks for a momentum rotation portfolio.",
    )
    _add_market_symbols_args(robustness)
    _add_date_args(robustness)
    robustness.add_argument(
        "--split", required=True, help="Train/test split date, e.g. 2016-01-01."
    )
    robustness.add_argument("--lookbacks", default="63,126,252")
    robustness.add_argument("--top-ns", default="1,2,3")
    robustness.add_argument("--rebalance-days", default="21,63")
    robustness.add_argument("--initial-cash", type=float, default=10_000.0)
    robustness.add_argument("--fee-bps", type=float, default=2.0)
    robustness.add_argument("--no-fetch", action="store_true", help="Use stored bars only.")
    _add_provider_arg(robustness)
    _add_benchmark_args(robustness)
    _add_pit_universe_args(robustness)
    _add_delisting_return_args(robustness)
    _add_preflight_audit_args(robustness)
    robustness.add_argument("--output", type=Path)
    robustness.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    macro = sub.add_parser("macro", help="Fetch/store public FRED macro series.")
    macro.add_argument("series", help="FRED series id or comma-separated ids, e.g. DGS10,VIXCLS")
    _add_date_args(macro)
    macro.add_argument("--provider", default="fred", choices=["fred", "manual"])
    macro.add_argument("--date")
    macro.add_argument("--value", type=float)
    macro.add_argument("--country", default="US")
    macro.add_argument("--category", default="macro")
    macro.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    fundamentals = sub.add_parser(
        "fundamentals",
        help="Store PIT fundamentals from yfinance, CSV, or explicit numeric inputs.",
    )
    _add_market_symbol_args(fundamentals)
    fundamentals.add_argument(
        "--provider", default="yfinance", choices=["yfinance", "csv", "manual"]
    )
    fundamentals.add_argument("--file", type=Path, help="CSV fundamentals import.")
    fundamentals.add_argument("--period-end", default=date.today().isoformat())
    fundamentals.add_argument("--revenue", type=float)
    fundamentals.add_argument("--net-income", type=float)
    fundamentals.add_argument("--free-cash-flow", type=float)
    fundamentals.add_argument("--equity", type=float)
    fundamentals.add_argument("--debt", type=float)
    fundamentals.add_argument("--shares-out", type=float)
    fundamentals.add_argument("--eps", type=float)
    fundamentals.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    flows = sub.add_parser("flows", help="Fetch/store KRX investor flow data.")
    _add_market_symbol_args(flows)
    _add_date_args(flows)
    flows.add_argument(
        "--provider",
        default="pykrx",
        choices=["pykrx", "csv", "naver-estimate", "manual"],
        help=(
            "pykrx and csv store reported KRX rows only. naver-estimate stores "
            "close x net-volume estimates in quarantine."
        ),
    )
    flows.add_argument(
        "--allow-estimated",
        action="store_true",
        help="Allow pykrx to fall back to quarantined Naver estimated rows.",
    )
    flows.add_argument("--date")
    flows.add_argument("--investor", default="foreign")
    flows.add_argument("--net-value", type=float)
    flows.add_argument("--buy-value", type=float, default=0.0)
    flows.add_argument("--sell-value", type=float, default=0.0)
    flows.add_argument("--net-volume", type=float)
    flows.add_argument("--value-kind", default="reported_value")
    flows.add_argument("--confidence", default="high", choices=["high", "medium", "low"])
    flows.add_argument("--file", type=Path, help="KRX reported investor-flow CSV export.")
    flows.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    factor = sub.add_parser("factor", help="Rank a universe by AQR value/momentum/quality.")
    _add_market_symbols_args(factor)
    _add_date_args(factor)
    factor.add_argument("--lookback", type=int, default=126)
    factor.add_argument("--output", type=Path)
    factor.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    valuate = sub.add_parser("valuate", help="Compute/store a valuation score and fair value.")
    _add_market_symbol_args(valuate)
    valuate.add_argument("--fair-value", type=float)
    valuate.add_argument("--fair-dcf", type=float)
    valuate.add_argument("--fair-multiple", type=float)
    valuate.add_argument("--fair-rim", type=float)
    valuate.add_argument("--wacc", type=float, default=0.09)
    valuate.add_argument("--growth", type=float, default=0.05)
    valuate.add_argument("--terminal-growth", type=float, default=0.025)
    valuate.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    entry = sub.add_parser("entry", help="Create/store a margin-of-safety ATR entry ladder.")
    _add_market_symbol_args(entry)
    entry.add_argument("--margin-of-safety", type=float, default=0.25)
    entry.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    paper = sub.add_parser("paper", help="Run a local paper order simulation.")
    _add_market_symbol_args(paper)
    paper.add_argument("--side", choices=["buy", "sell"], default="buy")
    paper.add_argument("--qty", type=float, required=True)
    paper.add_argument("--price", type=float, required=True)
    paper.add_argument("--cash", type=float, default=10_000.0)
    paper.add_argument("--strategy", default="manual")

    risk_check = sub.add_parser("risk-check", help="Evaluate kill-switch limits.")
    risk_check.add_argument("--start-equity", type=float, required=True)
    risk_check.add_argument("--current-equity", type=float, required=True)
    risk_check.add_argument("--gross-exposure", type=float, required=True)
    risk_check.add_argument("--max-daily-drawdown", type=float, default=0.02)
    risk_check.add_argument("--max-gross-exposure", type=float, default=1.0)

    sub.add_parser("live-policy", help="Show required live-trading environment gates.")

    paper_exposure = sub.add_parser(
        "paper-exposure",
        help="Portfolio exposure report for a paper-drill book (gross/net/single-name) "
        "against policy limits, marked from the live price catalog.",
    )
    paper_exposure.add_argument(
        "--strategy-id",
        default="aqr_top7_cap20_trail10_pit110",
        help="paper-drill strategy id (state file out/paper-drill-state-<id>.json)",
    )
    paper_exposure.add_argument("--catalog-db", type=Path, default=_default_live_catalog_db())
    paper_exposure.add_argument("--max-mark-age-days", type=int, default=5)
    paper_exposure.add_argument(
        "--max-gross",
        type=float,
        default=1.10,
        help="gross/net limit vs state NAV. Paper NAV is NOT marked-to-market, so gains "
        "inflate gross past 100%% — the 10%% headroom avoids false breaches on an "
        "unlevered book. Tighten for live books (real broker equity IS marked).",
    )
    paper_exposure.add_argument("--max-single-name", type=float, default=0.25)

    live_readiness = sub.add_parser(
        "live-readiness",
        help="Fail-closed readiness check for real-money live submission.",
    )
    live_readiness.add_argument("--registry", type=Path, default=DEFAULT_RESEARCH_REGISTRY)
    live_readiness.add_argument("--halt-state", type=Path, default=DEFAULT_HALT_STATE)
    live_readiness.add_argument("--drill-log", type=Path, default=DEFAULT_DRILL_LOG)
    live_readiness.add_argument("--paper-oos-dir", type=Path, default=DEFAULT_PAPER_OOS_DIR)
    live_readiness.add_argument(
        "--paper-oos-prices", type=Path, default=_default_paper_oos_prices()
    )
    live_readiness.add_argument("--catalog-db", type=Path, default=_default_live_catalog_db())
    live_readiness.add_argument("--as-of", default=date.today().isoformat())
    live_readiness.add_argument(
        "--require-price",
        help="Comma-separated live price requirements. Defaults to symbols passed to live-submit.",
    )
    live_readiness.add_argument("--max-price-age-days", type=int, default=2)
    live_readiness.add_argument(
        "--require-order-submission",
        action="store_true",
        help="Also require LIVE_ORDER_SUBMISSION_ENABLED=true.",
    )
    live_readiness.add_argument(
        "--require-broker-preflight",
        action="store_true",
        help="Read broker account/positions before declaring live readiness.",
    )

    live_halt = sub.add_parser("live-halt", help="View or update the persistent live halt latch.")
    live_halt.add_argument("action", choices=["status", "activate", "clear"])
    live_halt.add_argument("--reason", default="")
    live_halt.add_argument("--halt-state", type=Path, default=DEFAULT_HALT_STATE)

    live_drill = sub.add_parser(
        "live-drill",
        help="Record or inspect required paper/shadow drills for live promotion.",
    )
    live_drill.add_argument("action", choices=["record", "status"])
    live_drill.add_argument("--mode", choices=["paper", "shadow"])
    live_drill.add_argument("--strategy-id")
    live_drill.add_argument("--day", default=date.today().isoformat())
    live_drill.add_argument("--passed", action="store_true")
    live_drill.add_argument("--failed", action="store_true")
    live_drill.add_argument("--submitted-count", type=int, default=0)
    live_drill.add_argument("--blocked-count", type=int, default=0)
    live_drill.add_argument("--notes", default="")
    live_drill.add_argument("--required-paper-days", type=int)
    live_drill.add_argument("--required-shadow-days", type=int)
    live_drill.add_argument("--drill-log", type=Path, default=DEFAULT_DRILL_LOG)

    live_reconcile = sub.add_parser(
        "live-reconcile",
        help="Compare expected positions with broker positions and latch halt on mismatch.",
    )
    live_reconcile.add_argument(
        "--expected",
        default=None,
        help="Comma-separated expected positions, e.g. QQQ:us:2,TLT:us:0. Omit and pass "
        "--from-store to derive the baseline from recorded fills instead of typing it.",
    )
    live_reconcile.add_argument(
        "--from-store",
        action="store_true",
        help="Derive expected positions from the order log's recorded net fills "
        "(auto-reconcile) instead of --expected.",
    )
    live_reconcile.add_argument("--order-log", type=Path, default=DEFAULT_ORDER_LOG)
    live_reconcile.add_argument(
        "--broker",
        choices=["fake", "alpaca-paper", "alpaca-live", "manual-paper", "manual-live"],
        default=None,
    )
    live_reconcile.add_argument(
        "--fake-position",
        help="Fake broker positions for drills, e.g. QQQ:us:1:100,TLT:us:0:0.",
    )
    live_reconcile.add_argument("--cash", type=float, default=100_000.0)
    live_reconcile.add_argument("--equity", type=float, default=100_000.0)
    live_reconcile.add_argument("--buying-power", type=float, default=100_000.0)
    live_reconcile.add_argument("--qty-tolerance", type=float, default=1e-8)
    live_reconcile.add_argument("--halt-state", type=Path, default=DEFAULT_HALT_STATE)
    live_reconcile.add_argument(
        "--no-halt-on-mismatch",
        action="store_true",
        help="Report mismatches without activating the persistent halt latch.",
    )

    live_cancel = sub.add_parser(
        "live-cancel",
        help="Cancel a working broker order by client_order_id and record it in the ledger. "
        "Deliberately NOT gated by live-readiness: canceling is risk-reducing and must stay "
        "available even while halted.",
    )
    live_cancel.add_argument("client_order_id")
    live_cancel.add_argument(
        "--broker",
        choices=["fake", "alpaca-paper", "alpaca-live", "manual-paper", "manual-live"],
        default=None,
    )
    live_cancel.add_argument(
        "--fake-position",
        help="Fake broker positions for drills, e.g. QQQ:us:1:100.",
    )
    live_cancel.add_argument("--cash", type=float, default=100_000.0)
    live_cancel.add_argument("--equity", type=float, default=100_000.0)
    live_cancel.add_argument("--buying-power", type=float, default=100_000.0)
    live_cancel.add_argument("--order-log", type=Path, default=DEFAULT_ORDER_LOG)

    rebalance_plan = sub.add_parser(
        "rebalance-plan",
        help="Generate the delta rebalance order plan (sells first, live-gate pre-validated) "
        "as reviewable JSON + submit commands — the semi-auto operating surface.",
    )
    rebalance_plan.add_argument("--top-n", type=int, default=None)
    rebalance_plan.add_argument("--strategy-id", default=None)
    rebalance_plan.add_argument("--capital", type=float, default=None)
    rebalance_plan.add_argument("--snapshot", type=Path, default=None)
    rebalance_plan.add_argument("--allow-live-fundamentals", action="store_true")
    rebalance_plan.add_argument(
        "--no-record-oos",
        action="store_true",
        help="Skip appending this rebalance to the forward-OOS ledger.",
    )
    rebalance_plan.add_argument(
        "--whole-shares",
        action="store_true",
        help="Disable fractional sizing for brokers that only support whole shares.",
    )
    rebalance_plan.add_argument(
        "--preview-only",
        action="store_true",
        help="Generate report/JSON without changing paper positions or the OOS ledger.",
    )

    live_price_ingest = sub.add_parser(
        "live-price-ingest",
        help="Fetch latest US stock bars into the catalog (Alpaca broker-grade, or keyless "
        "Yahoo EOD fallback for paper marks).",
    )
    live_price_ingest.add_argument("symbols")
    live_price_ingest.add_argument("--feed", default="iex")
    live_price_ingest.add_argument(
        "--source",
        default="alpaca",
        choices=["alpaca", "yahoo", "external"],
        help="alpaca = broker-grade latest bars (needs API keys); yahoo = keyless EOD "
        "daily bars — good enough for paper-loop marks, NOT execution-grade; external = "
        "operator-attested quote from a non-Alpaca broker.",
    )
    live_price_ingest.add_argument(
        "--price",
        type=float,
        help="Required with --source external. Supports one symbol per command.",
    )
    live_price_ingest.add_argument("--price-as-of", default=date.today().isoformat())
    live_price_ingest.add_argument(
        "--ack-external-price",
        action="store_true",
        help="Required with --source external to attest that the price came from the external broker.",
    )
    live_price_ingest.add_argument("--catalog-db", type=Path, default=_default_live_catalog_db())

    live_price_stream = sub.add_parser(
        "live-price-stream",
        help="Stream Alpaca real-time US stock bars into the live catalog.",
    )
    live_price_stream.add_argument("symbols")
    live_price_stream.add_argument("--feed", default="iex")
    live_price_stream.add_argument(
        "--max-bars",
        type=int,
        default=0,
        help="Stop after N streamed bars. 0 streams until interrupted.",
    )
    live_price_stream.add_argument(
        "--timeout-seconds",
        type=float,
        default=0.0,
        help="Abort if the stream does not finish within N seconds. 0 disables the timeout.",
    )
    live_price_stream.add_argument("--catalog-db", type=Path, default=_default_live_catalog_db())

    live_dry_run = sub.add_parser(
        "live-dry-run",
        help="Run one live order intent through data-free pre-trade and idempotency gates.",
    )
    live_dry_run.add_argument("symbol")
    live_dry_run.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])
    live_dry_run.add_argument("--side", required=True, choices=["buy", "sell"])
    live_dry_run.add_argument("--qty", type=float, required=True)
    live_dry_run.add_argument("--price", type=float, required=True)
    live_dry_run.add_argument("--strategy", default="manual-live-dry-run")
    live_dry_run.add_argument("--order-type", default="limit", choices=["market", "limit"])
    live_dry_run.add_argument("--limit-price", type=float)
    live_dry_run.add_argument("--rebalance-key", default="manual")
    live_dry_run.add_argument("--cash", type=float, default=10_000.0)
    live_dry_run.add_argument("--equity", type=float, default=10_000.0)
    live_dry_run.add_argument("--buying-power", type=float, default=10_000.0)
    live_dry_run.add_argument("--max-order-notional", type=float, default=1_000.0)
    live_dry_run.add_argument("--max-daily-new-notional", type=float, default=2_000.0)
    live_dry_run.add_argument("--submit-fake", action="store_true")
    live_dry_run.add_argument(
        "--fake-mode",
        default="fill",
        choices=["fill", "partial", "reject", "timeout"],
        help="Fake broker behavior when --submit-fake is enabled.",
    )
    live_dry_run.add_argument("--order-log", type=Path, default=DEFAULT_ORDER_LOG)
    live_dry_run.add_argument("--halt-state", type=Path, default=DEFAULT_HALT_STATE)

    live_submit = sub.add_parser(
        "live-submit",
        help="Run one approved strategy order through live fail-closed gates, then optionally submit.",
    )
    live_submit.add_argument("symbol")
    live_submit.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])
    live_submit.add_argument("--side", required=True, choices=["buy", "sell"])
    live_submit.add_argument("--qty", type=float, required=True)
    live_submit.add_argument(
        "--price", type=float, required=True, help="Current broker/live mark used for risk checks."
    )
    live_submit.add_argument("--order-type", default="limit", choices=["market", "limit"])
    live_submit.add_argument("--limit-price", type=float)
    live_submit.add_argument("--time-in-force", default="day", choices=["day", "gtc", "ioc", "fok"])
    live_submit.add_argument("--rebalance-key", default=date.today().isoformat())
    live_submit.add_argument(
        "--submit",
        action="store_true",
        help="Submit to the configured broker instead of shadow mode.",
    )
    live_submit.add_argument(
        "--ack-live-order",
        action="store_true",
        help="Required with --submit to acknowledge that this may place a real order.",
    )
    live_submit.add_argument(
        "--broker",
        choices=["fake", "alpaca-paper", "alpaca-live", "manual-paper", "manual-live"],
        default=None,
    )
    live_submit.add_argument(
        "--fake-mode", default="fill", choices=["fill", "partial", "reject", "timeout"]
    )
    live_submit.add_argument(
        "--cash", type=float, default=100_000.0, help="Fake broker cash for drills."
    )
    live_submit.add_argument(
        "--equity", type=float, default=100_000.0, help="Fake broker equity for drills."
    )
    live_submit.add_argument(
        "--buying-power", type=float, default=100_000.0, help="Fake broker buying power for drills."
    )
    live_submit.add_argument("--as-of", default=date.today().isoformat())
    live_submit.add_argument("--max-price-age-days", type=int, default=2)
    live_submit.add_argument(
        "--max-mark-deviation",
        type=float,
        default=_default_live_mark_deviation(),
        help="Maximum allowed difference between --price and latest broker-grade catalog close.",
    )
    live_submit.add_argument("--order-log", type=Path, default=DEFAULT_ORDER_LOG)
    live_submit.add_argument("--halt-state", type=Path, default=DEFAULT_HALT_STATE)
    live_submit.add_argument("--equity-state", type=Path, default=DEFAULT_EQUITY_STATE)
    live_submit.add_argument("--drill-log", type=Path, default=DEFAULT_DRILL_LOG)
    live_submit.add_argument("--paper-oos-dir", type=Path, default=DEFAULT_PAPER_OOS_DIR)
    live_submit.add_argument("--paper-oos-prices", type=Path, default=_default_paper_oos_prices())
    live_submit.add_argument("--registry", type=Path, default=DEFAULT_RESEARCH_REGISTRY)
    live_submit.add_argument("--catalog-db", type=Path, default=_default_live_catalog_db())
    live_submit.add_argument(
        "--sectors-csv",
        type=Path,
        default=None,
        help="symbol->sector map for the pre-trade sector cap (default: newest "
        "data/sectors/*-sectors.csv; a real --submit blocks if none is found).",
    )

    live_ticket = sub.add_parser(
        "live-ticket",
        help="Create an externally executable manual broker order ticket after live gates pass.",
    )
    live_ticket.add_argument("symbol")
    live_ticket.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])
    live_ticket.add_argument("--side", required=True, choices=["buy", "sell"])
    live_ticket.add_argument("--qty", type=float, required=True)
    live_ticket.add_argument("--price", type=float, required=True)
    live_ticket.add_argument("--order-type", default="limit", choices=["market", "limit"])
    live_ticket.add_argument("--limit-price", type=float)
    live_ticket.add_argument(
        "--stop-loss",
        type=float,
        help="Protective stop level recorded on a BUY ticket for external broker entry.",
    )
    live_ticket.add_argument(
        "--target-exit",
        type=float,
        help="Profit target recorded on a BUY ticket for external broker entry.",
    )
    live_ticket.add_argument("--time-in-force", default="day", choices=["day", "gtc", "ioc", "fok"])
    live_ticket.add_argument("--rebalance-key", default=date.today().isoformat())
    live_ticket.add_argument("--broker", choices=["manual-paper", "manual-live"], default=None)
    live_ticket.add_argument(
        "--ack-manual-ticket",
        action="store_true",
        help="Required to create a manual execution ticket.",
    )
    live_ticket.add_argument(
        "--verify-only",
        action="store_true",
        help="Run the exact order-specific readiness and risk gates without creating a ticket.",
    )
    live_ticket.add_argument("--as-of", default=date.today().isoformat())
    live_ticket.add_argument("--max-price-age-days", type=int, default=2)
    live_ticket.add_argument(
        "--max-mark-deviation",
        type=float,
        default=_default_live_mark_deviation(),
        help="Maximum allowed difference between --price and latest external catalog close.",
    )
    live_ticket.add_argument(
        "--sectors-csv",
        type=Path,
        default=None,
        help="Explicit symbol->sector CSV for the sector cap (else auto-discover data/sectors/). "
        "manual-live fails closed if neither classifies the order symbol.",
    )
    live_ticket.add_argument("--ticket-log", type=Path, default=DEFAULT_MANUAL_TICKET_LOG)
    live_ticket.add_argument("--halt-state", type=Path, default=DEFAULT_HALT_STATE)
    live_ticket.add_argument("--equity-state", type=Path, default=DEFAULT_EQUITY_STATE)
    live_ticket.add_argument("--drill-log", type=Path, default=DEFAULT_DRILL_LOG)
    live_ticket.add_argument("--paper-oos-dir", type=Path, default=DEFAULT_PAPER_OOS_DIR)
    live_ticket.add_argument("--paper-oos-prices", type=Path, default=_default_paper_oos_prices())
    live_ticket.add_argument("--registry", type=Path, default=DEFAULT_RESEARCH_REGISTRY)
    live_ticket.add_argument("--catalog-db", type=Path, default=_default_live_catalog_db())

    model_gate = sub.add_parser(
        "model-gate",
        help="Record whether a research result is eligible for live promotion.",
    )
    model_gate.add_argument("--strategy-id", required=True)
    model_gate.add_argument("--params", required=True, help="Stable parameter label.")
    model_gate.add_argument("--windows", type=int, required=True)
    model_gate.add_argument("--positive-test-rate", type=float, required=True)
    model_gate.add_argument("--avg-test-excess", type=float, required=True)
    model_gate.add_argument("--worst-test-mdd", type=float, required=True)
    model_gate.add_argument("--fee-stress-passed", action="store_true")
    model_gate.add_argument("--pit-audit-passed", action="store_true")
    model_gate.add_argument("--full-sample-annualized-return", type=float)
    model_gate.add_argument("--full-sample-mdd", type=float)
    model_gate.add_argument("--stress-windows-tested", type=int, default=0)
    model_gate.add_argument("--worst-stress-return", type=float)
    model_gate.add_argument("--stress-passed", action="store_true")
    model_gate.add_argument(
        "--worst-stress-excess",
        type=float,
        help="Worst single crisis-window return minus the benchmark (relative gate).",
    )
    model_gate.add_argument(
        "--mean-stress-excess",
        type=float,
        help="Mean crisis-window return minus the benchmark across tested windows.",
    )
    model_gate.add_argument("--command", dest="source_command", default="")
    model_gate.add_argument("--source-commit", default="")
    model_gate.add_argument("--notes", default="")
    model_gate.add_argument("--registry", type=Path, default=DEFAULT_RESEARCH_REGISTRY)

    quality = sub.add_parser("quality", help="Run catalog data-quality checks.")
    quality.add_argument("--as-of", default=date.today().isoformat())
    quality.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero for warnings; quarantined estimate info is non-blocking.",
    )
    quality.add_argument("--require-flow", help="Comma-separated symbols requiring reported flow.")
    quality.add_argument(
        "--flow-market",
        default="kospi",
        choices=["kospi", "kosdaq"],
        help="Market for --require-flow symbols.",
    )
    quality.add_argument(
        "--require-price",
        help="Comma-separated live price requirements. Use SYMBOL or SYMBOL:market.",
    )
    quality.add_argument("--max-price-age-days", type=int, default=5)
    quality.add_argument(
        "--live-policy",
        action="store_true",
        help="Warn when required prices come from research-grade sources.",
    )
    quality.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    dashboard = sub.add_parser("dashboard", help="Print or run the local Streamlit dashboard.")
    dashboard.add_argument("--port", type=int, default=8501)
    dashboard.add_argument("--run", action="store_true", help="Start the local Streamlit server.")

    market_map = sub.add_parser(
        "market-map",
        help="마켓 히트맵 HTML 생성 — 거시 레짐 시계열 + 테마 자금흐름 (surgedesk 스타일).",
    )
    market_map.add_argument(
        "--weeks", type=_positive_int, default=28, help="표시할 주 수 (기본 28, 최소 1)."
    )
    market_map.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out" / "market_map.html",
        help="출력 HTML 경로 (기본 out/market_map.html).",
    )
    market_map.add_argument(
        "--offline",
        action="store_true",
        help="네트워크 없이 로컬 카탈로그 데이터만 사용 (매크로/KR/칩은 비게 됨).",
    )
    market_map.add_argument("--dashboard-url", default="http://localhost:8501")
    market_map.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)

    copilot = sub.add_parser("copilot", help="Forward arguments to the integrated copilot CLI.")
    copilot.add_argument("args", nargs=argparse.REMAINDER)
    return parser


def _run_init(catalog_db: Path) -> int:
    MarketDataCatalog(catalog_db).initialize()
    copilot_result = _run_copilot(["init"])
    print(f"Initialized market catalog: {catalog_db}")
    return copilot_result


def _run_ingest(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    for symbol in _parse_symbols(args.symbols):
        bars = _fetch_bars(symbol, args.market, start, end, provider=args.provider)
        stored = catalog.put_bars(bars)
        first = bars[0]
        last = bars[-1]
        print(
            f"Stored {stored} {first.freq} bars for {first.symbol} "
            f"({first.market}, {first.source_symbol}) from {first.ts} to {last.ts}"
        )
    return 0


def _run_bars(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    symbol = _catalog_symbol(args.symbol, args.market)
    bars = catalog.get_bars(
        symbol,
        market=args.market,
        start=_parse_optional_date(args.start),
        end=_parse_optional_date(args.end),
    )
    if not bars:
        print("No bars found. Run `uv run trader ingest SYMBOL` first.")
        return 1
    selected = bars[-args.limit :] if args.limit > 0 else bars
    print("| Date | Symbol | Open | High | Low | Close | Volume |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for bar in selected:
        print(
            f"| {bar.ts} | {bar.symbol} | {bar.open:.2f} | {bar.high:.2f} | "
            f"{bar.low:.2f} | {bar.close:.2f} | {bar.volume:,.0f} |"
        )
    return 0


_TF_HOURS = {
    "1m": 1 / 60,
    "3m": 3 / 60,
    "5m": 5 / 60,
    "15m": 0.25,
    "30m": 0.5,
    "1h": 1.0,
    "2h": 2.0,
    "4h": 4.0,
    "6h": 6.0,
    "8h": 8.0,
    "12h": 12.0,
    "1d": 24.0,
}


def _fetch_chart_bars_live(
    args: argparse.Namespace, lookback: int
) -> tuple[list[PriceBar], list[PriceBar] | None]:
    """Live-fetch the analysis (LTF) bars and, for crypto, the daily HTF bias bars."""
    market = args.market
    tf = args.tf
    end = date.today()

    if market == "crypto":
        span_days = max(2, int((lookback * _TF_HOURS.get(tf, 4.0)) / 24) + 3)
        start = end - timedelta(days=span_days)
        ltf = fetch_ccxt_bars(
            args.symbol, start, end, timeframe=tf, exchange_id=args.exchange, intraday=(tf != "1d")
        )
        htf: list[PriceBar] | None = None
        try:
            htf = fetch_ccxt_bars(
                args.symbol,
                end - timedelta(days=420),
                end,
                timeframe="1d",
                exchange_id=args.exchange,
            )
        except Exception as exc:  # noqa: BLE001 - HTF bias is optional
            print(f"# HTF 일봉 미가용 (편향 생략): {exc}")
        return ltf, htf

    # Equities: daily bars via provider; the daily series is itself the bias timeframe.
    start = end - timedelta(days=int(lookback * 1.7) + 10)
    return _fetch_bars(args.symbol, market, start, end, provider=args.provider), None


def _fetch_crypto_microstructure(
    args: argparse.Namespace, lookback: int
) -> tuple[
    OrderBookSnapshot | None,
    list[OpenInterestRecord] | None,
    list[CryptoFundingRecord] | None,
]:
    """Live-fetch the requested crypto order book + open interest.

    These are always live (the catalog stores no L2 / OI), so they are fetched whenever
    requested regardless of where the OHLCV bars came from. Each degrades gracefully.
    """
    order_book: OrderBookSnapshot | None = None
    oi_records: list[OpenInterestRecord] | None = None
    funding_records: list[CryptoFundingRecord] | None = None

    if args.with_orderbook:
        try:
            order_book = fetch_order_book(args.symbol, exchange_id=args.exchange)
        except Exception as exc:  # noqa: BLE001 - order book is optional
            print(f"# 호가창 미가용: {exc}")
    if args.with_oi:
        end = date.today()
        span_days = max(2, int((lookback * _TF_HOURS.get(args.tf, 4.0)) / 24) + 3)
        # Most venues (Binance) only retain ~30 days of open-interest history; requesting a
        # startTime older than that is rejected (-1130). Clamp to the retention window.
        start = end - timedelta(days=min(span_days, 29))
        try:
            oi_records = fetch_open_interest_history(
                args.symbol, start, end, timeframe=args.tf, exchange_id=args.exchange
            )
            # Funding exists only for perpetuals — fetch with the swap symbol, not spot.
            funding_records = fetch_funding_history(
                to_perp_symbol(args.symbol), start, end, exchange_id=args.exchange
            )
        except Exception as exc:  # noqa: BLE001 - OI/funding are optional
            print(f"# 미체결약정/펀딩 미가용: {exc}")
    return order_book, oi_records, funding_records


def _run_chart_read(args: argparse.Namespace) -> int:
    market = args.market
    # Default timeframe by market: crypto trades intraday, equities are daily.
    args.tf = args.tf or ("4h" if market == "crypto" else "1d")
    tf = args.tf
    lookback = max(int(args.lookback), 1)
    catalog = MarketDataCatalog(args.catalog_db)
    catalog_symbol = (
        normalize_crypto_symbol(args.symbol)
        if market == "crypto"
        else _catalog_symbol(args.symbol, market)
    )

    ltf: list[PriceBar] = []
    htf: list[PriceBar] | None = None

    use_live = args.source == "live"
    if args.source in ("auto", "catalog"):
        # The catalog is keyed by freq, and its ts column is DATE, so intraday bars are
        # never persisted there; an intraday request falls through to a live fetch.
        ltf = catalog.get_bars(catalog_symbol, market=market, freq=tf)
        if not ltf and args.source == "auto":
            use_live = True
    if use_live:
        ltf, htf = _fetch_chart_bars_live(args, lookback)

    if not ltf or len(ltf) < 20:
        if tf != "1d" and args.source == "catalog":
            print(
                f"장중봉({tf})은 카탈로그에 저장되지 않습니다 — `--source live`(또는 auto)로 "
                "실시간 페치하세요."
            )
        else:
            print(
                "차트 읽기에 필요한 봉이 부족합니다 (>=20 필요). "
                "`trader ingest`로 적재하거나 `--source live`로 실시간 페치하세요."
            )
        return 1

    ltf = ltf[-lookback:] if lookback > 0 else ltf

    # Crypto order book / open interest have no catalog copy and are fetched whenever
    # requested, independent of whether the OHLCV bars came from catalog or live.
    order_book: OrderBookSnapshot | None = None
    oi_records: list[OpenInterestRecord] | None = None
    funding_records: list[CryptoFundingRecord] | None = None
    if market == "crypto" and (args.with_orderbook or args.with_oi):
        order_book, oi_records, funding_records = _fetch_crypto_microstructure(args, lookback)

    mean_reversion = {"on": True, "off": False}.get(args.mean_reversion, market == "crypto")
    read = read_chart(
        ltf,
        htf_bars=htf,
        order_book=order_book,
        oi_records=oi_records,
        funding_records=funding_records,
        direction=args.direction,
        mean_reversion=mean_reversion,
    )
    return _emit(format_chart_read(read), args.output)


def _run_status(catalog_db: Path) -> int:
    coverage = MarketDataCatalog(catalog_db).coverage()
    if not coverage:
        print("No catalog data yet. Run `uv run trader ingest MSFT`.")
        return 0
    print("| Symbol | Market | Freq | Start | End | Rows |")
    print("|---|---|---|---|---|---:|")
    for item in coverage:
        print(
            f"| {item.symbol} | {item.market} | {item.freq} | "
            f"{item.start} | {item.end} | {item.rows} |"
        )
    return 0


def _run_screen(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=_parse_symbols(args.symbols),
        market=args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    rows = screen_momentum(bars_by_symbol, lookback=args.lookback)
    if not rows:
        print("No screen rows available. Use a shorter --lookback or fetch more history.")
        return 1
    report = format_screen_report(rows)
    return _emit(report, args.output)


def _run_backtest(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    catalog_symbol = _catalog_symbol(args.symbol, args.market)
    bars = catalog.get_bars(catalog_symbol, market=args.market, start=start, end=end)
    if _bars_need_fetch(bars, args.market, args.provider, start, end) and not args.no_fetch:
        fetched = _fetch_bars(args.symbol, args.market, start, end, provider=args.provider)
        catalog.put_bars(fetched)
        bars = catalog.get_bars(catalog_symbol, market=args.market, start=start, end=end)
    if not bars:
        print("No bars available for backtest.")
        return 1
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=args.benchmark_market or args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )

    result = run_momentum_backtest(
        bars,
        lookback=args.lookback,
        initial_cash=args.initial_cash,
        fee_bps=args.fee_bps,
        max_position=args.max_position,
        benchmark_bars=benchmark_bars,
    )
    report = format_backtest_report(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(report)
    return 0


def _run_portfolio(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)
    pit_members = _filter_pit_members(pit_members, symbols)
    delisting_returns = _load_delisting_returns(catalog, args, symbols, args.market, start, end)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    preflight = _preflight_universe_audit(
        catalog,
        args,
        pit_members=pit_members,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
    )
    if preflight != 0:
        return preflight
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=args.benchmark_market or args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )
    result = run_momentum_rotation_backtest(
        bars_by_symbol,
        lookback=args.lookback,
        top_n=args.top_n,
        initial_cash=args.initial_cash,
        rebalance_days=args.rebalance_days,
        fee_bps=args.fee_bps,
        benchmark_bars=benchmark_bars,
        universe_members=pit_members,
        delisting_returns=delisting_returns,
    )
    report = format_portfolio_report(result)
    return _emit(report, args.output)


def _run_robustness(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    split = _parse_date(args.split)
    if split <= start or split >= end:
        raise ValueError("--split must be between --start and --end")
    if args.benchmark is None:
        raise ValueError("--benchmark is required for robustness checks")
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)
    pit_members = _filter_pit_members(pit_members, symbols)
    delisting_returns = _load_delisting_returns(catalog, args, symbols, args.market, start, end)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    preflight = _preflight_universe_audit(
        catalog,
        args,
        pit_members=pit_members,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
    )
    if preflight != 0:
        return preflight
    benchmark_market = args.benchmark_market or args.market
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=benchmark_market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )
    if benchmark_bars is None:
        raise ValueError("--benchmark is required for robustness checks")
    report = run_momentum_robustness_grid(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        split_date=split,
        lookbacks=tuple(_parse_ints(args.lookbacks)),
        top_ns=tuple(_parse_ints(args.top_ns)),
        rebalance_days_values=tuple(_parse_ints(args.rebalance_days)),
        initial_cash=args.initial_cash,
        fee_bps=args.fee_bps,
        universe_members=pit_members,
        delisting_returns=delisting_returns,
    )
    if not report.rows:
        print("No robustness rows available. Use a longer window or smaller lookbacks.")
        return 1
    return _emit(format_robustness_report(report), args.output)


def _run_factor_portfolio(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    ensemble_momentum_lookbacks = _parse_optional_positive_int_tuple(
        args.ensemble_momentum_lookbacks
    )
    ensemble_risk_filter_lookbacks = _parse_optional_nonnegative_int_tuple(
        args.ensemble_risk_filter_lookbacks
    )
    defensive_basket = _parse_optional_defensive_symbol_tuple(args.defensive_basket)
    crash_hedge_symbols = _parse_optional_symbol_tuple(args.crash_hedge_symbols)
    data_start = _factor_warmup_start(
        start,
        args.momentum_lookback,
        args.reversal_lookback,
        args.volatility_lookback,
        args.risk_filter_lookback,
        *(ensemble_momentum_lookbacks or ()),
        *(ensemble_risk_filter_lookbacks or ()),
        args.defensive_selection_lookback,
        args.crash_hedge_trigger_lookback,
        args.crash_hedge_selection_lookback,
        args.volume_lookback_long if args.volume_weight > 0 else 0,
    )
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)
    pit_members = _filter_pit_members(pit_members, symbols)
    delisting_returns = _load_delisting_returns(
        catalog, args, symbols, args.market, data_start, end
    )
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=symbols,
        market=args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    preflight = _preflight_universe_audit(
        catalog,
        args,
        pit_members=pit_members,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
    )
    if preflight != 0:
        return preflight
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=args.benchmark_market or args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )
    quality_weight = getattr(args, "quality_weight", 0.0)
    value_weight = getattr(args, "value_weight", 0.0)
    factor_weights = FactorWeights(
        momentum=1.0,
        reversal=0.5,
        low_volatility=0.75,
        value=value_weight,
        quality=quality_weight,
    )
    result = run_factor_rotation_backtest(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        fundamentals_by_symbol=_fundamentals_history(catalog, symbols, args.market),
        universe_members=pit_members,
        delisting_returns=delisting_returns,
        momentum_lookback=args.momentum_lookback,
        reversal_lookback=args.reversal_lookback,
        volatility_lookback=args.volatility_lookback,
        risk_filter_lookback=args.risk_filter_lookback,
        top_n=args.top_n,
        initial_cash=args.initial_cash,
        rebalance_days=args.rebalance_days,
        fee_bps=args.fee_bps,
        defensive_symbol=args.defensive_symbol,
        weighting=args.weighting,
        max_risk_weight=args.max_risk_weight,
        drawdown_guard=args.drawdown_guard,
        defensive_only=args.defensive_only,
        ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
        ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
        risk_filter_vote_threshold=args.risk_filter_vote_threshold,
        defensive_symbols=defensive_basket,
        defensive_selection_lookback=args.defensive_selection_lookback,
        volatility_target=args.volatility_target,
        max_leverage=args.max_leverage,
        crash_hedge_symbols=crash_hedge_symbols,
        crash_hedge_weight=args.crash_hedge_weight,
        crash_hedge_trigger_lookback=args.crash_hedge_trigger_lookback,
        crash_hedge_trigger_drawdown=args.crash_hedge_trigger_drawdown,
        crash_hedge_selection_lookback=args.crash_hedge_selection_lookback,
        crash_hedge_hold_days=args.crash_hedge_hold_days,
        volume_lookback_short=args.volume_lookback_short,
        volume_lookback_long=args.volume_lookback_long,
        volume_weight=args.volume_weight,
        factor_weights=factor_weights,
        trade_start=start,
        trade_end=end,
        regime_cash_enable=args.regime_cash_enable,
        regime_cash_corr_symbol=args.regime_cash_corr_symbol,
        regime_cash_corr_window=args.regime_cash_corr_window,
        regime_cash_corr_threshold=args.regime_cash_corr_threshold,
        regime_cash_override_symbol=args.regime_cash_override_symbol,
    )
    if getattr(args, "returns_output", None) is not None:
        _write_returns_csv(args.returns_output, result)
    return _emit(format_factor_portfolio_report(result), args.output)


def _run_compounder_scan(args: argparse.Namespace) -> int:
    as_of = _parse_date(args.as_of)
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)

    # Fundamentals: snapshot (reproducible) or live catalog.
    if args.snapshot is not None:
        from collections import defaultdict

        from data.fundamentals_snapshot import read_fundamentals_snapshot

        idx: dict[str, list] = defaultdict(list)
        for rec in read_fundamentals_snapshot(args.snapshot, verify=True):
            idx[rec.symbol.upper()].append(rec)
        funds_by_symbol = {
            s: sorted(idx.get(s.upper(), []), key=lambda r: r.asof_ts) for s in symbols
        }
    else:
        funds_by_symbol = {
            s: sorted(
                catalog.get_fundamentals(symbol=s, market=args.market, as_of=None, limit=500),
                key=lambda r: r.asof_ts,
            )
            for s in symbols
        }

    universe: dict[str, tuple[Sequence[FundamentalRecord], float]] = {}
    for s in symbols:
        recs = [r for r in funds_by_symbol.get(s, []) if r.asof_ts.date() <= as_of]
        if not recs:
            continue
        bars = catalog.get_bars(_catalog_symbol(s, args.market), market=args.market)
        # Use the latest bar whose timestamp is on or before the as-of date so that
        # valuation ratios (P/E, P/FCF, …) are point-in-time and do not leak future
        # or current prices into historical scans.
        pit_bars = [b for b in bars if b.ts <= as_of]
        if not pit_bars:
            continue
        universe[s] = (recs, float(pit_bars[-1].close))

    sectors: dict[str, str] = {}
    if args.sectors_csv is not None and args.sectors_csv.exists():
        import csv as _csv

        with args.sectors_csv.open(encoding="utf-8", newline="") as fh:
            for row in _csv.DictReader(fh):
                sym = (row.get("symbol") or "").upper()
                if sym:
                    sectors[sym] = row.get("sector") or "unknown"

    ranked = rank_compounders(universe, top_n=args.top_n, sectors=sectors or None)
    if args.archetype:
        ranked = [c for c in ranked if c.best_archetype == args.archetype]

    lines = [f"# Compounder Scan — as-of {as_of} — {len(universe)} names scored", ""]
    for c in ranked:
        lines.append(
            format_dossier_markdown(build_dossier(c, sector=sectors.get(c.symbol, "unknown")))
        )
        lines.append("")
    return _emit("\n".join(lines), args.output)


def _write_returns_csv(path: Path, result: FactorPortfolioResult) -> None:
    """Persist the daily return series for statistical-significance analysis."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,portfolio_return,benchmark_return"]
    lines.extend(
        f"{point.ts.isoformat()},{point.portfolio_return!r},{point.benchmark_return!r}"
        for point in result.equity_curve
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path} ({len(result.equity_curve)} daily returns)")


def _run_walk_forward(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if args.benchmark is None:
        raise ValueError("--benchmark is required for walk-forward checks")
    ensemble_momentum_lookbacks = _parse_optional_positive_int_tuple(
        args.ensemble_momentum_lookbacks
    )
    ensemble_risk_filter_lookbacks = _parse_optional_nonnegative_int_tuple(
        args.ensemble_risk_filter_lookbacks
    )
    defensive_basket = _parse_optional_defensive_symbol_tuple(args.defensive_basket)
    crash_hedge_symbols = _parse_optional_symbol_tuple(args.crash_hedge_symbols)
    crash_hedge_weights = _parse_optional_float_tuple(
        args.crash_hedge_weights,
        min_value=0.0,
        max_value=1.0,
    )
    crash_hedge_trigger_lookbacks = _parse_optional_positive_int_tuple(
        args.crash_hedge_trigger_lookbacks
    )
    crash_hedge_trigger_drawdowns = _parse_optional_float_tuple(
        args.crash_hedge_trigger_drawdowns,
        min_value=0.0,
        max_value=1.0,
        min_exclusive=True,
        max_exclusive=True,
    )
    crash_hedge_selection_lookbacks = _parse_optional_positive_int_tuple(
        args.crash_hedge_selection_lookbacks
    )
    crash_hedge_hold_days_values = _parse_optional_nonnegative_int_tuple(
        args.crash_hedge_hold_days_values
    )
    momentum_lookbacks = tuple(_parse_ints(args.momentum_lookbacks))
    top_ns = tuple(_parse_ints(args.top_ns))
    reversal_lookbacks = (
        tuple(_parse_ints(args.reversal_lookbacks))
        if args.reversal_lookbacks
        else (args.reversal_lookback,)
    )
    volatility_lookbacks = (
        tuple(_parse_ints(args.volatility_lookbacks))
        if args.volatility_lookbacks
        else (args.volatility_lookback,)
    )
    rebalance_days_values = (
        tuple(_parse_ints(args.rebalance_days_values))
        if args.rebalance_days_values
        else (args.rebalance_days,)
    )
    defensive_symbols = (
        tuple(_parse_defensive_symbols(args.defensive_symbols))
        if args.defensive_symbols
        else (args.defensive_symbol,)
    )
    max_risk_weights = (
        tuple(
            _parse_floats(args.max_risk_weights, min_value=0.0, max_value=1.0, min_exclusive=True)
        )
        if args.max_risk_weights
        else (args.max_risk_weight,)
    )
    drawdown_guards = (
        tuple(_parse_floats(args.drawdown_guards, min_value=0.0, max_value=1.0, max_exclusive=True))
        if args.drawdown_guards
        else (args.drawdown_guard,)
    )
    risk_filter_lookbacks = (
        tuple(_parse_nonnegative_ints(args.risk_filter_lookbacks))
        if args.risk_filter_lookbacks
        else (args.risk_filter_lookback,)
    )
    weighting_modes = (
        tuple(_parse_weighting_modes(args.weighting_modes))
        if args.weighting_modes
        else (args.weighting,)
    )
    data_start = _factor_warmup_start(
        start,
        *momentum_lookbacks,
        *reversal_lookbacks,
        *volatility_lookbacks,
        *risk_filter_lookbacks,
        *(ensemble_momentum_lookbacks or ()),
        *(ensemble_risk_filter_lookbacks or ()),
        args.defensive_selection_lookback,
        args.crash_hedge_trigger_lookback,
        args.crash_hedge_selection_lookback,
        *(crash_hedge_trigger_lookbacks or ()),
        *(crash_hedge_selection_lookbacks or ()),
    )
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)
    pit_members = _filter_pit_members(pit_members, symbols)
    delisting_returns = _load_delisting_returns(
        catalog, args, symbols, args.market, data_start, end
    )
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=symbols,
        market=args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    preflight = _preflight_universe_audit(
        catalog,
        args,
        pit_members=pit_members,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
    )
    if preflight != 0:
        return preflight
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=args.benchmark_market or args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )
    if benchmark_bars is None:
        raise ValueError("--benchmark is required for walk-forward checks")
    report = run_factor_walk_forward(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        fundamentals_by_symbol=_fundamentals_history(catalog, symbols, args.market),
        universe_members=pit_members,
        delisting_returns=delisting_returns,
        start=start,
        end=end,
        train_years=args.train_years,
        validation_years=args.validation_years,
        test_years=args.test_years,
        step_years=args.step_years,
        momentum_lookbacks=momentum_lookbacks,
        reversal_lookbacks=reversal_lookbacks,
        volatility_lookbacks=volatility_lookbacks,
        top_ns=top_ns,
        risk_filter_lookback=args.risk_filter_lookback,
        risk_filter_lookbacks=risk_filter_lookbacks,
        weighting_modes=weighting_modes,
        selection_metric=args.selection_metric,
        rebalance_days=args.rebalance_days,
        rebalance_days_values=rebalance_days_values,
        fee_bps=args.fee_bps,
        defensive_symbol=args.defensive_symbol,
        defensive_symbols=defensive_symbols,
        max_risk_weight=args.max_risk_weight,
        max_risk_weights=max_risk_weights,
        drawdown_guard=args.drawdown_guard,
        drawdown_guards=drawdown_guards,
        defensive_only=args.defensive_only,
        ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
        ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
        risk_filter_vote_threshold=args.risk_filter_vote_threshold,
        defensive_basket=defensive_basket,
        defensive_selection_lookback=args.defensive_selection_lookback,
        volatility_target=args.volatility_target,
        max_leverage=args.max_leverage,
        crash_hedge_symbols=crash_hedge_symbols,
        crash_hedge_weight=args.crash_hedge_weight,
        crash_hedge_trigger_lookback=args.crash_hedge_trigger_lookback,
        crash_hedge_trigger_drawdown=args.crash_hedge_trigger_drawdown,
        crash_hedge_selection_lookback=args.crash_hedge_selection_lookback,
        crash_hedge_hold_days=args.crash_hedge_hold_days,
        crash_hedge_weights=crash_hedge_weights,
        crash_hedge_trigger_lookbacks=crash_hedge_trigger_lookbacks,
        crash_hedge_trigger_drawdowns=crash_hedge_trigger_drawdowns,
        crash_hedge_selection_lookbacks=crash_hedge_selection_lookbacks,
        crash_hedge_hold_days_values=crash_hedge_hold_days_values,
        regime_cash_enable=args.regime_cash_enable,
        regime_cash_corr_symbol=args.regime_cash_corr_symbol,
        regime_cash_corr_window=args.regime_cash_corr_window,
        regime_cash_corr_threshold=args.regime_cash_corr_threshold,
        regime_cash_override_symbol=args.regime_cash_override_symbol,
    )
    if not report.rows:
        print("No walk-forward rows available. Use a longer window or smaller lookbacks.")
        return 1
    return _emit(format_walk_forward_report(report), args.output)


def _run_validate_model(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    if args.benchmark is None:
        raise ValueError("--benchmark is required for validate-model checks")
    ensemble_momentum_lookbacks = _parse_optional_positive_int_tuple(
        args.ensemble_momentum_lookbacks
    )
    ensemble_risk_filter_lookbacks = _parse_optional_nonnegative_int_tuple(
        args.ensemble_risk_filter_lookbacks
    )
    defensive_basket = _parse_optional_defensive_symbol_tuple(args.defensive_basket)
    crash_hedge_symbols = _parse_optional_symbol_tuple(args.crash_hedge_symbols)
    crash_hedge_weights = _parse_optional_float_tuple(
        args.crash_hedge_weights,
        min_value=0.0,
        max_value=1.0,
    )
    crash_hedge_trigger_lookbacks = _parse_optional_positive_int_tuple(
        args.crash_hedge_trigger_lookbacks
    )
    crash_hedge_trigger_drawdowns = _parse_optional_float_tuple(
        args.crash_hedge_trigger_drawdowns,
        min_value=0.0,
        max_value=1.0,
        min_exclusive=True,
        max_exclusive=True,
    )
    crash_hedge_selection_lookbacks = _parse_optional_positive_int_tuple(
        args.crash_hedge_selection_lookbacks
    )
    crash_hedge_hold_days_values = _parse_optional_nonnegative_int_tuple(
        args.crash_hedge_hold_days_values
    )
    momentum_lookbacks = tuple(_parse_ints(args.momentum_lookbacks))
    top_ns = tuple(_parse_ints(args.top_ns))
    reversal_lookbacks = (
        tuple(_parse_ints(args.reversal_lookbacks))
        if args.reversal_lookbacks
        else (args.reversal_lookback,)
    )
    volatility_lookbacks = (
        tuple(_parse_ints(args.volatility_lookbacks))
        if args.volatility_lookbacks
        else (args.volatility_lookback,)
    )
    rebalance_days_values = (
        tuple(_parse_ints(args.rebalance_days_values))
        if args.rebalance_days_values
        else (args.rebalance_days,)
    )
    defensive_symbols = (
        tuple(_parse_defensive_symbols(args.defensive_symbols))
        if args.defensive_symbols
        else (args.defensive_symbol,)
    )
    max_risk_weights = (
        tuple(
            _parse_floats(args.max_risk_weights, min_value=0.0, max_value=1.0, min_exclusive=True)
        )
        if args.max_risk_weights
        else (args.max_risk_weight,)
    )
    drawdown_guards = (
        tuple(_parse_floats(args.drawdown_guards, min_value=0.0, max_value=1.0, max_exclusive=True))
        if args.drawdown_guards
        else (args.drawdown_guard,)
    )
    risk_filter_lookbacks = (
        tuple(_parse_nonnegative_ints(args.risk_filter_lookbacks))
        if args.risk_filter_lookbacks
        else (args.risk_filter_lookback,)
    )
    weighting_modes = (
        tuple(_parse_weighting_modes(args.weighting_modes))
        if args.weighting_modes
        else (args.weighting,)
    )
    fee_stress_bps = tuple(_parse_floats(args.fee_stress_bps, min_value=0.0, min_exclusive=True))
    stress_windows = parse_stress_windows(args.stress_windows)
    data_start = _factor_warmup_start(
        _earliest_validation_start(start, end, stress_windows),
        args.momentum_lookback,
        args.reversal_lookback,
        args.volatility_lookback,
        args.risk_filter_lookback,
        *momentum_lookbacks,
        *reversal_lookbacks,
        *volatility_lookbacks,
        *risk_filter_lookbacks,
        *(ensemble_momentum_lookbacks or ()),
        *(ensemble_risk_filter_lookbacks or ()),
        args.defensive_selection_lookback,
        args.crash_hedge_trigger_lookback,
        args.crash_hedge_selection_lookback,
        *(crash_hedge_trigger_lookbacks or ()),
        *(crash_hedge_selection_lookbacks or ()),
    )
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = _symbols_for_request(args.symbols, pit_members)
    pit_members = _filter_pit_members(pit_members, symbols)
    delisting_returns = _load_delisting_returns(
        catalog, args, symbols, args.market, data_start, end
    )
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=symbols,
        market=args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    preflight = _preflight_universe_audit(
        catalog,
        args,
        pit_members=pit_members,
        symbols=symbols,
        market=args.market,
        start=start,
        end=end,
    )
    if preflight != 0:
        return preflight
    benchmark_bars = _load_or_fetch_benchmark(
        catalog=catalog,
        symbol=args.benchmark,
        market=args.benchmark_market or args.market,
        start=data_start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=_benchmark_provider(args.provider, args.market, args.benchmark_market),
    )
    if benchmark_bars is None:
        raise ValueError("--benchmark is required for validate-model checks")
    suite = run_factor_validation_suite(
        bars_by_symbol,
        benchmark_bars=benchmark_bars,
        fundamentals_by_symbol=_fundamentals_history(catalog, symbols, args.market),
        universe_members=pit_members,
        delisting_returns=delisting_returns,
        start=start,
        end=end,
        momentum_lookback=args.momentum_lookback,
        reversal_lookback=args.reversal_lookback,
        volatility_lookback=args.volatility_lookback,
        risk_filter_lookback=args.risk_filter_lookback,
        top_n=args.top_n,
        rebalance_days=args.rebalance_days,
        fee_bps=args.fee_bps,
        defensive_symbol=args.defensive_symbol,
        weighting=args.weighting,
        max_risk_weight=args.max_risk_weight,
        drawdown_guard=args.drawdown_guard,
        defensive_only=args.defensive_only,
        train_years=args.train_years,
        validation_years=args.validation_years,
        test_years=args.test_years,
        step_years=args.step_years,
        momentum_lookbacks=momentum_lookbacks,
        reversal_lookbacks=reversal_lookbacks,
        volatility_lookbacks=volatility_lookbacks,
        top_ns=top_ns,
        risk_filter_lookbacks=risk_filter_lookbacks,
        weighting_modes=weighting_modes,
        rebalance_days_values=rebalance_days_values,
        defensive_symbols=defensive_symbols,
        max_risk_weights=max_risk_weights,
        drawdown_guards=drawdown_guards,
        selection_metric=args.selection_metric,
        fee_stress_bps=fee_stress_bps,
        stress_windows=stress_windows,
        ensemble_momentum_lookbacks=ensemble_momentum_lookbacks,
        ensemble_risk_filter_lookbacks=ensemble_risk_filter_lookbacks,
        risk_filter_vote_threshold=args.risk_filter_vote_threshold,
        defensive_basket=defensive_basket,
        defensive_selection_lookback=args.defensive_selection_lookback,
        volatility_target=args.volatility_target,
        max_leverage=args.max_leverage,
        crash_hedge_symbols=crash_hedge_symbols,
        crash_hedge_weight=args.crash_hedge_weight,
        crash_hedge_trigger_lookback=args.crash_hedge_trigger_lookback,
        crash_hedge_trigger_drawdown=args.crash_hedge_trigger_drawdown,
        crash_hedge_selection_lookback=args.crash_hedge_selection_lookback,
        crash_hedge_hold_days=args.crash_hedge_hold_days,
        crash_hedge_weights=crash_hedge_weights,
        crash_hedge_trigger_lookbacks=crash_hedge_trigger_lookbacks,
        crash_hedge_trigger_drawdowns=crash_hedge_trigger_drawdowns,
        crash_hedge_selection_lookbacks=crash_hedge_selection_lookbacks,
        crash_hedge_hold_days_values=crash_hedge_hold_days_values,
        regime_cash_enable=args.regime_cash_enable,
        regime_cash_corr_symbol=args.regime_cash_corr_symbol,
        regime_cash_corr_window=args.regime_cash_corr_window,
        regime_cash_corr_threshold=args.regime_cash_corr_threshold,
        regime_cash_override_symbol=args.regime_cash_override_symbol,
        thresholds=FactorValidationThresholds(
            min_walk_forward_windows=args.min_walk_forward_windows,
            min_positive_test_rate=args.min_positive_test_rate,
            min_average_test_excess=args.min_average_test_excess,
            max_worst_test_drawdown=args.max_worst_test_drawdown,
            min_parameter_positive_rate=args.min_parameter_positive_rate,
            min_stress_windows=args.min_stress_windows,
            min_stress_return=args.min_stress_return,
            max_stress_drawdown=args.max_stress_drawdown,
        ),
    )
    report = format_factor_validation_suite(suite)
    if args.record_gate:
        if not args.strategy_id:
            raise ValueError("--strategy-id is required with --record-gate")
        evidence = make_evidence(
            strategy_id=args.strategy_id,
            parameter_label=args.params_label or _validation_params_label(args),
            windows=len(suite.walk_forward.rows),
            positive_test_rate=suite.walk_forward.positive_test_rate,
            average_test_annualized_excess=suite.walk_forward.average_test_annualized_excess,
            worst_test_drawdown=suite.worst_test_drawdown,
            fee_stress_passed=suite.fee_stress_passed,
            pit_audit_passed=(
                bool(pit_members)
                and not getattr(args, "skip_universe_audit", False)
                and bool(getattr(args, "pit_universe", None))
            ),
            full_sample_annualized_return=suite.full_sample.annualized_return,
            full_sample_max_drawdown=suite.full_sample.max_drawdown,
            stress_windows_tested=suite.tested_stress_windows,
            worst_stress_return=suite.worst_stress_return,
            stress_passed=suite.stress_passed,
            worst_stress_excess=suite.worst_stress_excess,
            mean_stress_excess=suite.mean_stress_excess,
            notes=(
                f"parameter_positive_rate={suite.parameter_positive_rate:.4f}; "
                f"worst_stress_return={suite.worst_stress_return:.4f}; "
                f"min_stress_return={suite.thresholds.min_stress_return:.4f}; "
                f"stress_windows_tested={suite.tested_stress_windows}; "
                f"stress_passed={suite.stress_passed}"
            ),
        )
        decision = evaluate_promotion(
            evidence,
            gate=PromotionGate(
                min_windows=args.min_walk_forward_windows,
                min_positive_test_rate=args.min_positive_test_rate,
                min_average_test_excess=args.min_average_test_excess,
                max_worst_test_drawdown=args.max_worst_test_drawdown,
                min_stress_windows=args.min_stress_windows,
                min_worst_stress_return=args.min_stress_return
                if args.min_stress_windows or args.min_stress_return
                else None,
                require_stress_pass=bool(args.min_stress_windows or args.min_stress_return),
            ),
        )
        ResearchRegistry(args.registry).append(evidence, decision)
        report = "\n".join(
            [
                report,
                "",
                "## Model Gate Registry",
                "",
                f"Recorded: {'APPROVED' if decision.passed else 'BLOCKED'}",
            ]
        )
    emit_result = _emit(report, args.output)
    if emit_result != 0:
        return emit_result
    return 0 if suite.promotion_passed else 2


def _run_universe_audit(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    pit_members = _load_pit_universe(catalog, args, market=args.market)
    symbols = [] if _parse_symbols(args.symbols) == ["ALL"] else _parse_symbols(args.symbols)
    _load_delisting_returns(
        catalog, args, symbols or _symbols_for_request("ALL", pit_members), args.market, start, end
    )
    if not getattr(args, "pit_universe", None):
        raise ValueError("--pit-universe or --universe-csv is required for universe-audit")
    report = run_universe_audit(
        catalog,
        universe=args.pit_universe,
        market=args.market,
        start=start,
        end=end,
        symbols=symbols or None,
        require_fundamentals=args.require_fundamentals,
        require_delistings=not args.no_require_delistings,
        rebalance_days=args.rebalance_days,
    )
    result = _emit(format_universe_audit_report(report), args.output)
    if result != 0:
        return result
    if args.strict and not report.ready:
        return 2
    return 0


def _run_pair(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    bars_by_symbol = _load_or_fetch_universe(
        catalog=catalog,
        symbols=[args.first, args.second],
        market=args.market,
        start=start,
        end=end,
        fetch_missing=not args.no_fetch,
        provider=args.provider,
    )
    first_symbol = _catalog_symbol(args.first, args.market)
    second_symbol = _catalog_symbol(args.second, args.market)
    first_bars = bars_by_symbol.get(first_symbol)
    second_bars = bars_by_symbol.get(second_symbol)
    if not first_bars or not second_bars:
        print("No enough bars available for pair analysis.")
        return 1

    analysis = analyze_pair(
        first_bars,
        second_bars,
        lookback=args.lookback,
        entry_z=args.entry_z,
        exit_z=args.exit_z,
        min_observations=min(args.min_observations, args.lookback),
    )
    if analysis is None:
        print("No usable pair spread. Check overlap, positive prices, and observation count.")
        return 1
    validation = None
    if args.validate:
        validation = backtest_pair_mean_reversion(
            first_bars,
            second_bars,
            lookback=args.lookback,
            entry_z=args.entry_z,
            exit_z=args.exit_z,
            min_observations=max(args.min_observations, args.lookback + 2),
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            min_trades=args.min_trades,
            min_sharpe=args.min_sharpe,
            max_drawdown_limit=args.max_drawdown,
        )
    short_gate = _pair_shortability_gate(analysis, args, end, args.market)
    report = _format_pair_analysis(analysis, validation, short_gate)
    emit_result = _emit(report, args.output)
    if args.require_shortability and short_gate is not None and not short_gate.passed:
        return 1
    return emit_result


def _run_vix_calc(args: argparse.Namespace) -> int:
    asof_date = _parse_date(args.as_of)
    catalog = MarketDataCatalog(args.catalog_db)
    risk_free_rate, risk_free_source, risk_free_warnings = _resolve_vix_risk_free_rate(
        catalog,
        args,
        asof_date,
    )
    try:
        quotes, quote_source = _load_vix_quotes(args, asof_date)
        result = calculate_vix_like_index(
            quotes,
            asof_date=asof_date,
            target_days=args.target_days,
            risk_free_rate=risk_free_rate,
            max_quote_age_days=args.max_option_quote_age_days,
            require_last_trade=args.require_last_trade or args.source == "yahoo",
            max_bid_ask_spread_pct=args.max_bid_ask_spread_pct,
        )
    except (ValueError, YahooOptionChainError) as exc:
        print(f"VIX calculation failed: {exc}")
        return 1
    warnings = (*result.warnings, *risk_free_warnings)
    stored_market = None
    if args.strict_quality and warnings:
        print(
            _format_vix_calculation(
                result,
                quote_source=quote_source,
                risk_free_source=risk_free_source,
                extra_warnings=risk_free_warnings,
            )
        )
        print("VIX calculation blocked by --strict-quality warnings.")
        return 1
    if args.store:
        catalog.put_option_sentiment(
            [
                OptionSentimentRecord(
                    date=asof_date,
                    market=args.market,
                    vix=result.volatility * 100,
                    source=f"{result.source};quotes={quote_source};rf={risk_free_source}",
                )
            ]
        )
        stored_market = args.market.upper()
    return _emit(
        _format_vix_calculation(
            result,
            quote_source=quote_source,
            risk_free_source=risk_free_source,
            extra_warnings=risk_free_warnings,
            stored_market=stored_market,
        ),
        args.output,
    )


def _run_macro(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    for series_id in _parse_symbols(args.series):
        if args.provider == "manual":
            if args.value is None:
                raise ValueError("--value is required for manual macro observations")
            observation_date = _parse_date(args.date or args.end)
            rows = [
                MacroObservation(
                    series_id=series_id,
                    country=args.country,
                    category=args.category,
                    asof_date=observation_date,
                    release_ts=datetime.combine(observation_date, datetime.min.time()).replace(
                        hour=18
                    ),
                    value=args.value,
                    source="manual",
                )
            ]
        else:
            rows = fetch_fred_series(
                series_id,
                start,
                end,
                country=args.country,
                category=args.category,
            )
            catalog.delete_macro_range(series_id, start, end)
            if series_id.upper() == "VIXCLS":
                catalog.delete_option_sentiment_range("US", start, end)
        stored = catalog.put_macro(rows)
        if series_id.upper() == "VIXCLS":
            catalog.put_option_sentiment(vix_from_macro(rows))
        print(
            f"Stored {stored} macro observations for {series_id.upper()} "
            f"from {rows[0].asof_date} to {rows[-1].asof_date}; "
            f"sources={_source_summary(rows)}"
        )
    return 0


def _run_fundamentals(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    symbol = _catalog_symbol(args.symbol, args.market)
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    if args.provider == "csv":
        if args.file is None:
            raise ValueError("--file is required for --provider csv")
        records = load_fundamentals_csv(args.file)
    elif args.provider == "manual":
        record = FundamentalRecord(
            symbol=symbol,
            market=args.market,
            period_end=_parse_date(args.period_end),
            asof_ts=now,
            revenue=args.revenue,
            net_income=args.net_income,
            free_cash_flow=args.free_cash_flow,
            total_equity=args.equity,
            total_debt=args.debt,
            shares_out=args.shares_out,
            eps=args.eps,
            source="manual",
        )
        records = [record]
    else:
        records = fetch_yfinance_fundamentals(symbol, args.market, asof_ts=now)
    stored = catalog.put_fundamentals(records)
    label = (
        symbol
        if args.provider != "csv"
        else f"{len({record.symbol for record in records})} symbol(s)"
    )
    print(f"Stored {stored} fundamentals record(s) for {label}")
    return 0


def _run_flows(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    if args.provider == "manual":
        if args.net_value is None:
            raise ValueError("--net-value is required for manual flows")
        flow_date = _parse_date(args.date or args.end)
        rows = [
            FlowRecord(
                symbol=_catalog_symbol(args.symbol, args.market),
                market=args.market,
                ts=flow_date,
                investor=args.investor,
                net_value=args.net_value,
                buy_value=args.buy_value,
                sell_value=args.sell_value,
                net_volume=args.net_volume,
                release_ts=datetime.combine(flow_date, datetime.min.time()).replace(hour=18),
                value_kind=args.value_kind,
                confidence=args.confidence,
                source="manual",
            )
        ]
    elif args.provider == "csv":
        if args.file is None:
            raise ValueError("--file is required for --provider csv")
        try:
            rows = parse_krx_flow_csv(args.file, args.symbol, args.market)
        except KrxFlowCsvError as exc:
            print(f"Flow CSV import failed: {exc}")
            return 1
    elif args.provider == "naver-estimate":
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        rows = fetch_naver_investor_flows(
            _catalog_symbol(args.symbol, args.market),
            args.market,
            start,
            end,
        )
    else:
        start = _parse_date(args.start)
        end = _parse_date(args.end)
        try:
            rows = fetch_krx_flows(
                args.symbol,
                args.market,
                start,
                end,
                allow_estimated=args.allow_estimated,
            )
        except KrxFlowError as exc:
            print(f"Flow ingest failed: {exc}")
            return 1
    untrusted = [
        row for row in rows if row.value_kind != "reported_value" or row.confidence != "high"
    ]
    trusted = [row for row in rows if row not in untrusted]
    if trusted:
        catalog.put_flows(trusted)
    if untrusted:
        catalog.put_flow_estimates(untrusted)
    stored = len(rows)
    storage_note = (
        f"{len(trusted)} reported rows, {len(untrusted)} quarantined estimate rows"
        if untrusted
        else f"{len(trusted)} reported rows"
    )
    print(
        f"Stored {stored} KRX flow rows for {_catalog_symbol(args.symbol, args.market)}; "
        f"{storage_note}; sources={_source_summary(rows)}"
    )
    if untrusted:
        print(
            "Warning: estimated flow rows were quarantined and are excluded from default signals."
        )
    return 0


def _run_factor(args: argparse.Namespace) -> int:
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    catalog = MarketDataCatalog(args.catalog_db)
    symbols = [_catalog_symbol(symbol, args.market) for symbol in _parse_symbols(args.symbols)]
    bars_by_symbol = {
        symbol: catalog.get_bars(symbol, market=args.market, start=start, end=end)
        for symbol in symbols
    }
    fundamentals = {
        symbol: fundamental_rows[0]
        for symbol in symbols
        if (
            fundamental_rows := catalog.get_fundamentals(
                symbol,
                market=args.market,
                as_of=datetime.now(tz=UTC).replace(tzinfo=None),
            )
        )
    }
    factor_rows = rank_aqr_factors(bars_by_symbol, fundamentals, lookback=args.lookback)
    if not factor_rows:
        print("No factor rows available. Store fundamentals and enough price history first.")
        return 1
    lines = [
        "# AQR Factor Rank",
        "",
        "| Rank | Symbol | Value | Momentum | Quality | Composite |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(factor_rows, 1):
        lines.append(
            f"| {rank} | {row.symbol} | {row.value:.4f} | {row.momentum * 100:+.2f}% | "
            f"{row.quality:.4f} | {row.composite:.2f} |"
        )
    return _emit("\n".join(lines), args.output)


def _run_valuate(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    symbol = _catalog_symbol(args.symbol, args.market)
    latest_price = _latest_close(catalog, symbol, args.market)
    fair_dcf = args.fair_dcf
    fair_multiple = args.fair_multiple
    fair_rim = args.fair_rim
    if args.fair_value is not None:
        fair = args.fair_value
        dispersion = 0.0
    else:
        if fair_dcf is None:
            fundamentals = catalog.get_fundamentals(symbol, market=args.market)
            if fundamentals:
                item = fundamentals[0]
                if item.free_cash_flow and item.shares_out:
                    fair_dcf = discounted_cash_flow(
                        free_cash_flow=item.free_cash_flow,
                        shares_out=item.shares_out,
                        net_debt=item.total_debt or 0.0,
                        growth=args.growth,
                        wacc=args.wacc,
                        terminal_growth=args.terminal_growth,
                    ).fair_value
        fair, dispersion = composite_fair_value(
            {"dcf": fair_dcf, "multiple": fair_multiple, "rim": fair_rim}
        )
    disc = discount_pct(latest_price, fair)
    record = ValuationRecord(
        symbol=symbol,
        market=args.market,
        asof_date=date.today(),
        current_price=latest_price,
        fair_value=fair,
        fair_dcf=fair_dcf,
        fair_multiple=fair_multiple,
        fair_rim=fair_rim,
        dispersion_pct=dispersion,
        discount_pct=disc,
        rating=rating_from_discount(disc),
        confidence=confidence_from_dispersion(dispersion),
        source="trader:valuation",
    )
    catalog.put_valuations([record])
    print(_format_valuation(record))
    return 0


def _run_entry(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    symbol = _catalog_symbol(args.symbol, args.market)
    valuations = catalog.get_valuations(symbol=symbol, market=args.market, limit=1)
    if not valuations:
        print("No valuation found. Run `uv run trader valuate SYMBOL --fair-value ...` first.")
        return 1
    bars = catalog.get_bars(symbol, market=args.market)
    if len(bars) < 2:
        print("No enough bars for ATR entry plan.")
        return 1
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    closes = [bar.close for bar in bars]
    plan = make_entry_plan(
        symbol=symbol,
        market=args.market,
        current_price=valuations[0].current_price,
        fair_value=valuations[0].fair_value,
        atr_pct=average_true_range_pct(highs, lows, closes),
        asof_ts=datetime.now(tz=UTC).replace(tzinfo=None),
        margin_of_safety=args.margin_of_safety,
    )
    catalog.put_entry_plans([plan])
    print(
        f"Entry plan for {symbol}: target_entry={plan.target_entry:,.2f}, stop={plan.stop_loss:,.2f}, target={plan.target_exit:,.2f}, R/R={plan.risk_reward:.2f}"
    )
    print(plan.ladder_json)
    return 0


def _run_paper(args: argparse.Namespace) -> int:
    broker = PaperBroker(initial_cash=args.cash)
    order = broker.submit_market_order(
        strategy=args.strategy,
        symbol=_catalog_symbol(args.symbol, args.market),
        market=args.market,
        side=args.side,
        qty=args.qty,
        price=args.price,
        ts=datetime.now(tz=UTC).replace(tzinfo=None),
    )
    marks = {order.symbol: order.price}
    print(f"Filled {order.side} {order.qty:g} {order.symbol} @ {order.price:,.2f}")
    print(f"Cash: {broker.cash:,.2f}")
    print(f"Equity: {broker.equity(marks):,.2f}")
    print(f"Gross exposure: {broker.gross_exposure(marks):.2f}")
    return 0


def _run_risk_check(args: argparse.Namespace) -> int:
    result = check_kill_switch(
        start_equity=args.start_equity,
        current_equity=args.current_equity,
        gross_exposure=args.gross_exposure,
        max_daily_drawdown=args.max_daily_drawdown,
        max_gross_exposure=args.max_gross_exposure,
    )
    if result.halted:
        print("HALT")
        for reason in result.reasons:
            print(f"- {reason}")
        return 2
    print("OK")
    return 0


def _run_live_policy() -> int:
    policy = load_live_trading_policy()
    risk_policy = live_risk_policy(policy)
    lines = [
        "# Live Trading Policy",
        "",
        "| Gate | Value |",
        "|---|---:|",
        f"| Ready | {'yes' if policy.ready else 'no'} |",
        f"| Enabled | {'yes' if policy.enabled else 'no'} |",
        f"| Risk Acknowledged | {'yes' if policy.risk_acknowledged else 'no'} |",
        f"| Order Submission Enabled | {'yes' if policy.order_submission_enabled else 'no'} |",
        f"| Strategy | {policy.strategy_id or 'missing'} |",
        f"| Broker | {policy.broker or 'missing'} |",
        f"| Max Capital | {policy.max_capital:,.2f} |",
        f"| Policy Version | {policy.policy_version or 'missing'} |",
        f"| Required Paper Drill Days | {policy.min_paper_days} |",
        f"| Required Shadow Drill Days | {policy.min_shadow_days} |",
        f"| Required Paper OOS Periods | {policy.min_paper_oos_periods} |",
        f"| Min Paper OOS / Backtest | {policy.min_paper_oos_vs_backtest:.2f}x |",
        f"| Allowed Order Types | {', '.join(risk_policy.allowed_order_types)} |",
        f"| Max Order Notional | {risk_policy.max_order_notional:,.2f} |",
        f"| Max Daily New Notional | {risk_policy.max_daily_new_notional:,.2f} |",
        f"| Max Limit Deviation | {risk_policy.max_limit_deviation:.2%} |",
    ]
    print("\n".join(lines))
    try:
        assert_live_trading_enabled(policy)
    except LiveTradingBlockedError as exc:
        print("")
        print(str(exc))
        return 1
    return 0


def _run_live_readiness(args: argparse.Namespace) -> int:
    policy = load_live_trading_policy()
    required_prices = _parse_symbol_market_pairs(args.require_price, default_market="us")
    issues = _live_readiness_issues(
        policy=policy,
        registry=ResearchRegistry(args.registry),
        halt_store=HaltStateStore(args.halt_state),
        drill_log=DrillLog(args.drill_log),
        paper_oos_dir=args.paper_oos_dir,
        paper_oos_prices=args.paper_oos_prices,
        catalog=MarketDataCatalog(args.catalog_db),
        required_prices=required_prices,
        as_of=_parse_date(args.as_of),
        max_price_age_days=args.max_price_age_days,
        require_order_submission=args.require_order_submission,
        require_broker_preflight=args.require_broker_preflight,
    )
    print(
        _format_live_readiness(
            policy,
            issues,
            required_prices=required_prices,
            require_order_submission=args.require_order_submission,
            require_broker_preflight=args.require_broker_preflight,
            paper_oos_prices=args.paper_oos_prices,
        )
    )
    return 0 if not issues else 2


def _run_live_halt(args: argparse.Namespace) -> int:
    store = HaltStateStore(args.halt_state)
    if args.action == "activate":
        reason = args.reason or "manual halt"
        record = store.activate(reason, source="cli")
    elif args.action == "clear":
        reason = args.reason or "manual clear"
        record = store.clear(reason, source="cli")
    else:
        record = store.current()
    print(f"Halt: {'yes' if record.halted else 'no'}")
    print(f"Reason: {record.reason or 'n/a'}")
    print(f"Source: {record.source}")
    print(f"Timestamp: {record.ts.isoformat()}")
    return 2 if record.halted else 0


def _run_live_drill(args: argparse.Namespace) -> int:
    policy = load_live_trading_policy()
    strategy_id = args.strategy_id or policy.strategy_id
    if not strategy_id:
        print("strategy id is required; set --strategy-id or LIVE_STRATEGY_ID")
        return 2
    day = _parse_date(args.day)
    required_paper_days = (
        args.required_paper_days if args.required_paper_days is not None else policy.min_paper_days
    )
    required_shadow_days = (
        args.required_shadow_days
        if args.required_shadow_days is not None
        else policy.min_shadow_days
    )
    log = DrillLog(args.drill_log)
    if args.action == "record":
        if not args.mode:
            print("--mode is required when recording a drill")
            return 2
        if args.passed and args.failed:
            print("use only one of --passed or --failed")
            return 2
        log.append(
            DrillRecord(
                strategy_id=strategy_id,
                mode=args.mode,
                day=day,
                passed=not args.failed if args.passed or args.failed else True,
                submitted_count=args.submitted_count,
                blocked_count=args.blocked_count,
                notes=args.notes,
            )
        )
    summary = log.summary(
        strategy_id,
        as_of=day,
        required_paper_days=required_paper_days,
        required_shadow_days=required_shadow_days,
    )
    print(_format_drill_summary(summary))
    return 0 if summary.passed else 2


def _run_live_reconcile(args: argparse.Namespace) -> int:
    policy = load_live_trading_policy()
    broker_name = args.broker or policy.broker
    try:
        broker = _live_broker_adapter(broker_name, args)
    except ValueError as exc:
        print(str(exc))
        return 2
    if args.from_store and args.expected:
        print("Pass either --expected or --from-store, not both.")
        return 2
    recoveries: list = []
    if args.from_store:
        # Auto-reconcile: baseline = the system's own recorded net fills, not operator memory.
        store = JsonlOrderStore(args.order_log)
        # Self-heal first: bring any in-flight orders (process crashed after submit, or an
        # uncertain submit) back into the ledger via get_order BEFORE deriving the baseline, so a
        # real fill the ledger simply had not recorded does not read as drift (live-readiness P0).
        recoveries = reconcile_in_flight(store, broker)
        expected = expected_positions_from_store(store)
        baseline = f"order-log:{args.order_log}"
    elif args.expected:
        expected = _parse_expected_positions(args.expected)
        baseline = "--expected"
    else:
        print("Provide --expected POSITIONS or --from-store (derive from the order log).")
        return 2
    issues = reconcile_positions(
        expected,
        broker.list_positions(),
        qty_tolerance=args.qty_tolerance,
    )
    lines = [
        "# Live Reconciliation",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Broker | {broker_name} |",
        f"| Baseline | {baseline} |",
        f"| Expected Positions | {len(expected)} |",
        f"| Mismatches | {len(issues)} |",
    ]
    # Uncertain (state unknown) or still-working (open at broker) recoveries leave the FILLED
    # baseline incomplete — a "no mismatch" result built on them is guesswork (live-readiness P1).
    unresolved = [r for r in recoveries if r.outcome in ("uncertain", "still_working")]
    if recoveries:
        lines.append(f"| In-Flight Recovered | {len(recoveries)} |")
        if unresolved:
            lines.append(f"| In-Flight Unresolved | {len(unresolved)} |")
    if issues:
        if not args.no_halt_on_mismatch:
            HaltStateStore(args.halt_state).activate(
                "broker position reconciliation mismatch",
                source="live-reconcile",
            )
            lines.append("| Halt Latched | yes |")
        lines.extend(
            [
                "",
                "## Mismatches",
                "",
                "| Symbol | Market | Expected | Actual | Message |",
                "|---|---|---:|---:|---|",
            ]
        )
        for issue in issues:
            lines.append(
                f"| {issue.symbol} | {issue.market} | {issue.expected_qty:g} | "
                f"{issue.actual_qty:g} | {issue.message} |"
            )
    print("\n".join(lines))
    # Fail on a position mismatch OR an unresolved in-flight order — both mean the reconcile
    # baseline cannot be trusted, so callers/cron must not read exit 0 as "clean" (live-readiness P1).
    return 0 if (not issues and not unresolved) else 2


def _run_live_cancel(args: argparse.Namespace) -> int:
    """Cancel a working order by client_order_id and record the outcome in the ledger.

    Deliberately NOT gated by live-readiness: canceling is risk-REDUCING and must remain
    available even while a halt is latched (a halt is exactly when an operator needs to pull
    stray working orders). Exit codes mirror live-submit: 0 ok/no-op, 1 rejected, 2 not
    found / config error, 3 uncertain (re-run or reconcile once the broker is reachable).
    """
    # Local imports keep this handler self-contained (shared import block is contended).
    from trader.execution.broker import BrokerRejectedError, BrokerTemporaryError
    from trader.execution.order_store import OrderEvent

    policy = load_live_trading_policy()
    broker_name = args.broker or policy.broker
    try:
        broker = _live_broker_adapter(broker_name, args)
    except ValueError as exc:
        print(str(exc))
        return 2
    store = JsonlOrderStore(args.order_log)
    cancel = getattr(broker, "cancel_order", None)
    if cancel is None:
        # e.g. the manual ticket adapter — orders there are placed by hand, so recall them by
        # hand too; refuse loudly instead of crashing on a structurally-typed missing method.
        print(f"broker {broker_name} does not support order cancel; cancel it at the broker.")
        return 2
    try:
        order = cancel(args.client_order_id)
    except BrokerTemporaryError as exc:
        # State unknown — log the attempt (NOT as a resolving broker-order event) and tell the
        # operator how to converge: retry, or reconcile-in-flight once the broker answers.
        store.record_event(
            OrderEvent(
                event_type="broker_cancel_uncertain",
                client_order_id=args.client_order_id,
                ts=datetime.now(UTC),
                status="uncertain",
                message=str(exc),
            )
        )
        print(
            f"cancel state uncertain for {args.client_order_id}: {exc}\n"
            "Re-run live-cancel, or `trader live-reconcile --from-store` once the broker "
            "is reachable."
        )
        return 3
    except BrokerRejectedError as exc:
        print(f"broker rejected cancel for {args.client_order_id}: {exc}")
        return 1
    if order is None:
        print(
            f"order {args.client_order_id} not found at broker {broker_name} — nothing to cancel."
        )
        return 2
    store.record_broker_order("broker_cancel", order)
    lines = [
        "# Live Cancel",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Broker | {broker_name} |",
        f"| Client Order ID | {order.client_order_id} |",
        f"| Symbol | {order.symbol} |",
        f"| Status | {order.status} |",
        f"| Filled Qty | {order.filled_qty:g} |",
    ]
    if order.status.lower() != "canceled":
        lines.append(f"| Note | order already terminal ({order.status}); nothing was recalled |")
    print("\n".join(lines))
    return 0


def _run_rebalance_plan(args: argparse.Namespace) -> int:
    """Thin forwarder to the validated paper_drill generator (single implementation of the
    ranking/sizing/delta-plan pipeline — the CLI must not grow a second one)."""
    import scripts.paper_drill as paper_drill  # lazy: keeps yfinance/pandas off other paths

    argv: list[str] = []
    if args.top_n is not None:
        argv += ["--top-n", str(args.top_n)]
    if args.strategy_id:
        argv += ["--strategy-id", args.strategy_id]
    if args.capital is not None:
        argv += ["--capital", str(args.capital)]
    if args.snapshot is not None:
        argv += ["--snapshot", str(args.snapshot)]
    if args.allow_live_fundamentals:
        argv += ["--allow-live-fundamentals"]
    if args.no_record_oos:
        argv += ["--no-record-oos"]
    if args.whole_shares:
        argv += ["--whole-shares"]
    if args.preview_only:
        argv += ["--preview-only"]
    return int(paper_drill.main(argv) or 0)


def _run_paper_exposure(args: argparse.Namespace) -> int:
    """Exposure report for a paper book: state positions x latest catalog marks vs limits.

    Fail-closed: a position without a fresh mark (catalog miss, or older than
    --max-mark-age-days) aborts with the missing symbols listed — run
    `trader live-price-ingest "<syms>" --source yahoo` to refresh, rather than
    silently valuing a book on stale prices.
    """
    import json as _json

    from scripts.paper_drill import state_path_for  # lazy: keeps yfinance off this path

    state_path = state_path_for(args.strategy_id)
    if not state_path.exists():
        print(f"no paper state for {args.strategy_id!r} ({state_path.name} missing)")
        return 2
    state = _json.loads(state_path.read_text())
    positions_qty: dict[str, float] = {
        sym: float(qty) for sym, qty in (state.get("positions") or {}).items() if float(qty) != 0
    }
    nav = float(state.get("nav") or 0.0)
    if not positions_qty:
        print(
            f"paper book {args.strategy_id} holds no positions (NAV ${nav:,.2f}) — nothing to report"
        )
        return 0

    catalog = MarketDataCatalog(args.catalog_db)
    today = date.today()
    marks: dict[str, tuple[float, date]] = {}
    stale_or_missing: list[str] = []
    for sym in sorted(positions_qty):
        bars = catalog.get_bars(sym, market="us", freq="1d")
        if not bars:
            stale_or_missing.append(sym)
            continue
        last = bars[-1]
        if (today - last.ts).days > args.max_mark_age_days:
            stale_or_missing.append(f"{sym}(stale {last.ts})")
            continue
        marks[sym] = (float(last.close), last.ts)
    if stale_or_missing:
        print(
            f"FAIL-CLOSED: no fresh mark for {', '.join(stale_or_missing)} "
            f"(max age {args.max_mark_age_days}d). Refresh with:\n"
            f'  .venv/bin/trader live-price-ingest "{",".join(sorted(positions_qty))}" --source yahoo'
        )
        return 2

    snapshots = [
        PositionSnapshot(
            symbol=sym,
            market="us",
            qty=qty,
            market_value=qty * marks[sym][0],
            avg_entry_price=0.0,
        )
        for sym, qty in positions_qty.items()
    ]
    equity = nav if nav > 0 else sum(p.market_value for p in snapshots)
    report = build_exposure_report(snapshots, equity)
    check = check_exposure_limits(
        report,
        ExposureLimits(
            max_gross_exposure=args.max_gross,
            # Long-only paper book: net == gross, so the same headroom applies (see --max-gross).
            max_net_exposure=args.max_gross,
            max_single_name=args.max_single_name,
        ),
    )

    lines = [
        f"# Paper Exposure — {args.strategy_id}",
        "",
        f"NAV (state): ${nav:,.2f} | marked book: ${sum(p.market_value for p in snapshots):,.2f}",
        f"Gross {report.gross_exposure:.2%} | Net {report.net_exposure:.2%} | "
        f"Top {report.top_symbol} {report.top_weight:.2%}",
        "",
        "| Symbol | Qty | Mark | Mark Date | Value | Weight |",
        "|---|---:|---:|---|---:|---:|",
    ]
    for snap in sorted(snapshots, key=lambda p: -abs(p.market_value)):
        mark, mark_date = marks[snap.symbol]
        lines.append(
            f"| {snap.symbol} | {snap.qty:g} | ${mark:,.2f} | {mark_date} | "
            f"${snap.market_value:,.2f} | {report.symbol_weights[snap.symbol]:.2%} |"
        )
    lines += ["", f"Limits: {'PASS' if check.passed else 'BREACH'}"]
    for reason in check.reasons:
        lines.append(f"- {reason}")
    print("\n".join(lines))
    return 0 if check.passed else 2


_ALPACA_CREDENTIAL_PLACEHOLDERS = {
    "your_key_here",
    "your_secret_here",
    "your_secret_key_here",
    "replace_me",
    "changeme",
    "change_me",
}


def _looks_like_placeholder_credential(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in _ALPACA_CREDENTIAL_PLACEHOLDERS or (
        normalized.startswith("your_") and normalized.endswith("_here")
    )


def _alpaca_credentials_issue(api_key: str, secret_key: str, *, purpose: str) -> str | None:
    if not api_key or not secret_key:
        return f"ALPACA_API_KEY and ALPACA_SECRET_KEY are required for {purpose}"
    if _looks_like_placeholder_credential(api_key) or _looks_like_placeholder_credential(
        secret_key
    ):
        return (
            f"ALPACA_API_KEY and ALPACA_SECRET_KEY must be real Alpaca credentials for "
            f"{purpose}; replace placeholder values"
        )
    return None


_MANUAL_BROKERS = {"manual-paper", "manual-live"}


def _manual_env_float(name: str, missing: list[str]) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        missing.append(name)
        return 0.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    # argparse/float() accept nan/inf; a non-finite equity/buying-power makes every downstream
    # readiness and risk comparison fail open (NaN compares False) — reject it here (codex P2).
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite (got {raw!r})")
    return value


def _manual_broker_adapter_from_env(broker_name: str) -> ManualBrokerAdapter:
    missing: list[str] = []
    account_id = os.getenv("LIVE_MANUAL_ACCOUNT_ID", "").strip()
    if not account_id:
        missing.append("LIVE_MANUAL_ACCOUNT_ID")
    cash = _manual_env_float("LIVE_MANUAL_CASH", missing)
    equity = _manual_env_float("LIVE_MANUAL_EQUITY", missing)
    buying_power = _manual_env_float("LIVE_MANUAL_BUYING_POWER", missing)
    if missing:
        raise ValueError(
            f"{broker_name} requires operator-attested account env: " + ", ".join(missing)
        )
    positions = _parse_fake_positions(os.getenv("LIVE_MANUAL_POSITIONS", "").strip() or None)
    market_open = os.getenv("LIVE_MANUAL_MARKET_OPEN", "").strip().lower() == "true"
    return ManualBrokerAdapter(
        account=AccountSnapshot(
            account_id=account_id,
            buying_power=buying_power,
            cash=cash,
            equity=equity,
            currency=os.getenv("LIVE_MANUAL_CURRENCY", "USD").strip() or "USD",
            last_equity=(
                float(os.getenv("LIVE_MANUAL_LAST_EQUITY", "").strip())
                if os.getenv("LIVE_MANUAL_LAST_EQUITY", "").strip()
                else None
            ),
        ),
        positions=positions,
        clock=BrokerClock(is_open=market_open, timestamp=datetime.now(UTC)),
    )


def _run_live_price_ingest(args: argparse.Namespace) -> int:
    symbols = _parse_symbols(args.symbols)
    if args.source == "external":
        if len(symbols) != 1:
            print("--source external supports exactly one symbol per command")
            return 2
        if args.price is None or not math.isfinite(args.price) or args.price <= 0:
            print("--price must be a finite positive number with --source external")
            return 2
        if not args.ack_external_price:
            print("--ack-external-price is required with --source external")
            return 2
        symbol = symbols[0]
        ts = _parse_date(args.price_as_of)
        price = float(args.price)
        bars = [
            PriceBar(
                symbol=symbol,
                market="us",
                source_symbol=symbol,
                ts=ts,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=0.0,
                source="external-broker:operator-attested",
            )
        ]
    elif args.source == "yahoo":
        # Keyless EOD fallback: latest daily close per symbol from Yahoo. Good enough for
        # paper-loop marks / equity tracking; NOT execution-grade (use Alpaca for live).
        today = datetime.now(UTC).date()
        bars = []
        for symbol in symbols:
            try:
                history = fetch_yahoo_bars(
                    symbol, "us", today - timedelta(days=10), today + timedelta(days=1)
                )
            except YahooDataError as exc:
                print(f"{symbol}: yahoo fetch failed — {exc}")
                continue
            if history:
                bars.append(history[-1])  # latest completed daily bar
            else:
                print(f"{symbol}: yahoo returned no daily bars")
    else:
        api_key = os.getenv("ALPACA_API_KEY", "").strip()
        secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
        credential_issue = _alpaca_credentials_issue(
            api_key, secret_key, purpose="live-price-ingest"
        )
        if credential_issue:
            print(f"{credential_issue} (or use --source yahoo for the keyless EOD fallback)")
            return 2
        try:
            bars = fetch_alpaca_latest_stock_bars(
                symbols,
                api_key=api_key,
                secret_key=secret_key,
                feed=args.feed,
            )
        except Exception as exc:  # noqa: BLE001 - external SDK errors vary by auth/feed state.
            print(f"alpaca latest-bar fetch failed: {_compact_external_error(exc)}")
            return 2
    stored = MarketDataCatalog(args.catalog_db).put_bars(bars)
    source_detail = (
        args.feed
        if args.source == "alpaca"
        else "EOD daily"
        if args.source == "yahoo"
        else "operator-attested"
    )
    lines = [
        "# Live Price Ingest",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Symbols | {', '.join(symbols)} |",
        f"| Source | {args.source} ({source_detail}) |",
        f"| Stored Bars | {stored} |",
    ]
    if bars:
        lines.extend(
            [
                "",
                "## Latest Bars",
                "",
                "| Symbol | Date | Close | Source |",
                "|---|---:|---:|---|",
            ]
        )
        for bar in bars:
            lines.append(f"| {bar.symbol} | {bar.ts} | {bar.close:,.4f} | {bar.source} |")
    print("\n".join(lines))
    return 0 if stored == len(symbols) else 2


def _compact_external_error(exc: Exception) -> str:
    message = " ".join(str(exc).strip().split())
    if not message:
        return exc.__class__.__name__
    lowered = message.lower()
    if "status=401" in lowered or ("401" in message and "unauthorized" in lowered):
        return "401 Unauthorized"
    if "<html" in lowered or "<body" in lowered or "<h1>" in lowered:
        if "401" in message and "authorization required" in lowered:
            return "401 Authorization Required"
        return "upstream returned an HTML error page"
    return message


def _run_live_price_stream(args: argparse.Namespace) -> int:
    symbols = _parse_symbols(args.symbols)
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
    credential_issue = _alpaca_credentials_issue(api_key, secret_key, purpose="live-price-stream")
    if credential_issue:
        print(credential_issue)
        return 2
    catalog = MarketDataCatalog(args.catalog_db)

    def persist_bar(bar) -> None:
        catalog.put_bars([bar])
        print(
            f"{datetime.now(UTC).isoformat()} stored {bar.symbol} {bar.ts} "
            f"close={bar.close:.4f} source={bar.source}",
            flush=True,
        )

    max_bars = args.max_bars if args.max_bars > 0 else None
    timeout_s = args.timeout_seconds if args.timeout_seconds > 0 else None
    print(
        f"Streaming {', '.join(symbols)} bars from Alpaca feed={args.feed} "
        f"into {args.catalog_db} (max-bars={max_bars or 'until interrupted'}, "
        f"timeout={timeout_s or 'disabled'})",
        flush=True,
    )
    try:
        stream_alpaca_stock_bars(
            symbols,
            api_key=api_key,
            secret_key=secret_key,
            feed=args.feed,
            on_bar=persist_bar,
            max_bars=max_bars,
            timeout_s=timeout_s,
        )
    except AlpacaStreamTimeoutError as exc:
        print(str(exc))
        return 2
    except KeyboardInterrupt:
        print("stream interrupted")
    return 0


# The fund trades US equities, so the daily-loss latch must roll on the US market-session date.
# Deriving the day from host-local time (e.g. KST) would roll mid-session and reset the baseline,
# letting pre-midnight losses escape the 2% latch (Codex P1).
_MARKET_TZ = ZoneInfo("America/New_York")


def _live_equity_refs(
    broker: BrokerAdapter, equity_state: Path, broker_name: str
) -> tuple[float | None, float | None]:
    """Update the persistent equity tracker with the broker's current equity and return the
    (reference, peak) refs that arm the portfolio kill-switch. The day-roll uses the US market
    session date (not host-local). The state file is keyed by normalized broker + account id so a
    fake/paper drill or a different account cannot seed the peak/baseline that gates a real account
    (Codex P2). Returns (None, None) on a non-positive equity so the pre-trade equity check fails
    the order closed (rather than the kill-switch raising on a zero reference)."""
    account = broker.get_account()
    if account.equity <= 0:
        return None, None
    key = f"{broker_name.strip().lower()}-{account.account_id}"
    path = equity_state.with_name(f"{equity_state.stem}-{key}{equity_state.suffix}")
    refs = EquityTrackStore(path).update(
        account.equity,
        today=datetime.now(_MARKET_TZ).date(),
        prior_close=account.last_equity,
    )
    return refs.reference_equity, refs.peak_equity


def _run_live_dry_run(args: argparse.Namespace) -> int:
    symbol = _catalog_symbol(args.symbol, args.market)
    # Mirror live-submit: a limit order with no explicit limit defaults to the mark.
    limit_price = args.limit_price
    if args.order_type == "limit" and limit_price is None:
        limit_price = args.price
    intent = OrderIntent(
        strategy=args.strategy,
        symbol=symbol,
        market=args.market,
        side=args.side,
        qty=args.qty,
        order_type=args.order_type,
        limit_price=limit_price,
        rebalance_key=args.rebalance_key,
        reason="cli live dry run",
        asof_ts=datetime.now(UTC),
    ).normalized()
    account = AccountSnapshot(
        account_id="cli-dry-run",
        buying_power=args.buying_power,
        cash=args.cash,
        equity=args.equity,
    )
    broker = FakeBrokerAdapter(account=account, mode=args.fake_mode)
    # Mirror the live-submit guard: market orders are rejected unless
    # LIVE_ALLOW_MARKET_ORDERS=true, so the dry-run rehearsal cannot pass an order
    # type the real path would block (the rehearsal must not give false confidence).
    allow_market = os.getenv("LIVE_ALLOW_MARKET_ORDERS", "").lower() == "true"
    allowed_order_types = ("market", "limit") if allow_market else ("limit",)
    policy = RiskPolicy(
        policy_id="cli-live-dry-run",
        allowed_markets=(args.market,),
        allowed_order_types=allowed_order_types,
        max_order_notional=args.max_order_notional,
        max_daily_new_notional=args.max_daily_new_notional,
        max_symbol_weight=1.0,
        max_gross_exposure=1.0,
        min_cash_fraction=0.0,
    )
    results = process_order_intents(
        [intent],
        broker=broker,
        store=JsonlOrderStore(args.order_log),
        halt_store=HaltStateStore(args.halt_state),
        policy=policy,
        marks={symbol: args.price},
        dry_run=not args.submit_fake,
        # --submit-fake flips dry_run off, and the runner fail-closes any real submission
        # without an armed kill-switch — arm it with the drill's synthetic account equity
        # (Codex P2: this path used to crash with ValueError instead of printing the gate).
        reference_equity=args.equity,
        peak_equity=args.equity,
    )
    result = results[0]
    lines = [
        "# Live Order Gate",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Client Order ID | {result.client_order_id} |",
        f"| Symbol | {symbol} |",
        f"| Side | {args.side} |",
        f"| Quantity | {args.qty:g} |",
        f"| Mark | {args.price:,.2f} |",
        f"| Action | {result.action} |",
        f"| Status | {result.status} |",
    ]
    if result.reasons:
        lines.extend(["", "## Reasons", ""])
        lines.extend(f"- {reason}" for reason in result.reasons)
    print("\n".join(lines))
    if result.status in {"accepted", "filled", "partially_filled"}:
        return 0
    if result.status == "uncertain":
        return 3
    if result.status == "risk_block":
        return 2
    return 1


def _resolve_live_sectors(
    sectors_csv: Path | None,
    symbol: str,
    *,
    subcommand: str,
    require_gate: bool,
) -> tuple[dict[str, str] | None, list[DataQualityIssue], str]:
    """Load the symbol->sector map for the pre-trade sector cap and, for a REAL execution
    (``require_gate``), fail closed when the map is missing or does not classify the order symbol
    — otherwise the order trades sector-blind (codex P1). Shared by live-submit and live-ticket so
    the manual path cannot silently bypass the sector concentration gate the API path enforces.
    """
    from risk.sectors import load_sector_map  # local: shared import block is contended

    issues: list[DataQualityIssue] = []
    sectors: dict[str, str] | None = None
    label = "missing (sector cap inactive)"
    if sectors_csv is not None:
        if sectors_csv.exists():
            sectors = load_sector_map(sectors_csv)
            label = str(sectors_csv)
        else:
            issues.append(
                DataQualityIssue(
                    "error", subcommand, "sectors", f"--sectors-csv {sectors_csv} does not exist"
                )
            )
    else:
        auto_map = _latest_sector_map()
        if auto_map is not None:
            sectors = load_sector_map(auto_map)
            label = str(auto_map)
        elif require_gate:
            issues.append(
                DataQualityIssue(
                    "error",
                    subcommand,
                    "sectors",
                    "no symbol->sector map found under data/sectors/; run "
                    "`python scripts/fetch_sectors.py --universe-csv "
                    "data/universes/sp100-pit-2008.csv` or pass --sectors-csv "
                    "(a real submission must not trade sector-blind)",
                )
            )
    # A map that does not classify the ORDER symbol makes the pretrade gate skip the sector cap for
    # exactly this order — a real execution must fail closed instead of trading sector-blind.
    if require_gate and sectors is not None and symbol.upper() not in sectors:
        issues.append(
            DataQualityIssue(
                "error",
                subcommand,
                "sectors",
                f"sector map {label} does not classify {symbol} — the sector cap cannot gate this "
                "order; extend the map (fetch_sectors) or pass a --sectors-csv that covers it",
            )
        )
    return sectors, issues, label


def _run_live_submit(args: argparse.Namespace) -> int:
    policy = load_live_trading_policy()
    symbol = _catalog_symbol(args.symbol, args.market)
    required_prices = ((symbol, args.market),)
    catalog = MarketDataCatalog(args.catalog_db)
    issues = _live_readiness_issues(
        policy=policy,
        registry=ResearchRegistry(args.registry),
        halt_store=HaltStateStore(args.halt_state),
        drill_log=DrillLog(args.drill_log),
        paper_oos_dir=args.paper_oos_dir,
        paper_oos_prices=args.paper_oos_prices,
        catalog=catalog,
        required_prices=required_prices,
        as_of=_parse_date(args.as_of),
        max_price_age_days=args.max_price_age_days,
        require_order_submission=args.submit,
        require_broker_preflight=False,
    )
    # A non-finite/non-positive mark makes the deviation check (nan > x == False) and every
    # downstream notional/cash/weight comparison fail open — reject before it reaches risk (codex P2).
    for _flag, _val in (("--price", args.price), ("--limit-price", args.limit_price)):
        if _val is not None and (not math.isfinite(_val) or _val <= 0):
            issues.append(
                DataQualityIssue(
                    "error",
                    "live-submit",
                    "price",
                    f"{_flag} must be a finite positive number (got {_val})",
                )
            )
    catalog_mark = _latest_catalog_mark(catalog, symbol, args.market)
    if catalog_mark is not None and args.max_mark_deviation >= 0:
        deviation = abs(args.price / catalog_mark - 1.0)
        if deviation > args.max_mark_deviation:
            issues.append(
                DataQualityIssue(
                    "error",
                    "live-submit",
                    "mark",
                    f"--price {args.price:.4f} deviates {deviation:.2%} from latest catalog "
                    f"close {catalog_mark:.4f}; max {args.max_mark_deviation:.2%}",
                )
            )
    if args.submit and not args.ack_live_order:
        issues.append(
            DataQualityIssue(
                "error",
                "live-submit",
                "ack",
                "--ack-live-order is required with --submit",
            )
        )
    broker_name = args.broker or policy.broker
    if args.submit and args.broker and policy.broker and args.broker != policy.broker:
        issues.append(
            DataQualityIssue(
                "error",
                "live-submit",
                "broker",
                f"--broker {args.broker} does not match LIVE_BROKER={policy.broker}",
            )
        )
    if args.submit and broker_name.strip().lower() in _MANUAL_BROKERS:
        issues.append(
            DataQualityIssue(
                "error",
                "live-submit",
                "broker",
                "manual brokers do not support API submission; use live-ticket and execute "
                "the ticket in the external broker",
            )
        )
    # Symbol->sector map for the pre-trade sector cap (audit P1 activation). Explicitly named
    # but missing = config error; auto-discovery empty = warning in shadow (cap inactive,
    # surfaced in the output) but a BLOCKING issue for a real --submit — real money must not
    # trade sector-blind silently.
    sectors, sector_issues, sectors_label = _resolve_live_sectors(
        args.sectors_csv, symbol, subcommand="live-submit", require_gate=args.submit
    )
    issues.extend(sector_issues)
    if issues:
        print(
            _format_live_readiness(
                policy,
                issues,
                required_prices=required_prices,
                require_order_submission=args.submit,
                require_broker_preflight=False,
                paper_oos_prices=args.paper_oos_prices,
            )
        )
        return 2
    try:
        broker = _live_broker_adapter(broker_name, args)
    except ValueError as exc:
        print(str(exc))
        return 2

    limit_price = args.limit_price
    if args.order_type == "limit" and limit_price is None:
        limit_price = args.price
    intent = OrderIntent(
        strategy=policy.strategy_id,
        symbol=symbol,
        market=args.market,
        side=args.side,
        qty=args.qty,
        order_type=args.order_type,
        limit_price=limit_price,
        time_in_force=args.time_in_force,
        rebalance_key=args.rebalance_key,
        reason="live-submit" if args.submit else "live-shadow",
        asof_ts=datetime.now(UTC),
    ).normalized()
    reference_equity, peak_equity = _live_equity_refs(broker, args.equity_state, broker_name)
    store = JsonlOrderStore(args.order_log)
    # Self-heal before sending: resolve any prior in-flight order (crash after submit, or an
    # uncertain submit) against the broker so the ledger reflects real fills before this order
    # is sized/checked and so the next reconcile is honest (live-readiness P0).
    recoveries = reconcile_in_flight(store, broker)
    # Fail closed: never place a NEW live order while a prior intent is still working at the
    # broker or its state is unknown — sizing/submitting on top of unresolved exposure risks a
    # double-fill or unhedged position (live-readiness P1). Shadow mode (no real order) is exempt.
    unresolved = [r for r in recoveries if r.outcome in ("uncertain", "still_working")]
    if args.submit and unresolved:
        detail = ", ".join(f"{r.client_order_id}={r.outcome}" for r in unresolved)
        print(
            "# Live Submit Gate\n\n"
            f"BLOCKED: {len(unresolved)} prior in-flight order(s) unresolved ({detail}). "
            "Resolve them first (`trader live-reconcile --from-store`) before submitting."
        )
        return 2
    results = process_order_intents(
        [intent],
        broker=broker,
        store=store,
        halt_store=HaltStateStore(args.halt_state),
        policy=live_risk_policy(policy),
        marks={symbol: args.price},
        dry_run=not args.submit,
        reference_equity=reference_equity,
        peak_equity=peak_equity,
        # Poll an async accepted/filled_qty=0 submit to its real terminal fill instead of
        # leaving a stale non-terminal snapshot in the ledger (live-readiness P0 Gap A).
        fill_poll=FillPoll(),
        # Sector cap only fires with a map; None = inactive (surfaced in the table below).
        sectors=sectors,
    )
    result = results[0]
    lines = [
        "# Live Submit Gate",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Mode | {'submit' if args.submit else 'shadow'} |",
        f"| Broker | {broker_name} |",
        f"| Strategy | {policy.strategy_id} |",
        f"| Client Order ID | {result.client_order_id} |",
        f"| Symbol | {symbol} |",
        f"| Side | {args.side} |",
        f"| Quantity | {args.qty:g} |",
        f"| Mark | {args.price:,.2f} |",
        f"| Catalog Mark | {_number_or_na(catalog_mark)} |",
        f"| Sector Map | {sectors_label} |",
        f"| Action | {result.action} |",
        f"| Status | {result.status} |",
    ]
    if result.reasons:
        lines.extend(["", "## Reasons", ""])
        lines.extend(f"- {reason}" for reason in result.reasons)
    print("\n".join(lines))
    if result.status in {"accepted", "filled", "partially_filled"}:
        return 0
    if result.status == "uncertain":
        return 3
    if result.status == "risk_block":
        return 2
    return 1


def _run_live_ticket(args: argparse.Namespace) -> int:
    policy = load_live_trading_policy()
    broker_name = args.broker or policy.broker
    symbol = _catalog_symbol(args.symbol, args.market)
    required_prices = ((symbol, args.market),)
    catalog = MarketDataCatalog(args.catalog_db)
    issues: list[DataQualityIssue] = []
    if broker_name.strip().lower() not in _MANUAL_BROKERS:
        issues.append(
            DataQualityIssue(
                "error",
                "live-ticket",
                "broker",
                "live-ticket requires LIVE_BROKER=manual-paper or manual-live",
            )
        )
    if args.broker and policy.broker and args.broker != policy.broker:
        issues.append(
            DataQualityIssue(
                "error",
                "live-ticket",
                "broker",
                f"--broker {args.broker} does not match LIVE_BROKER={policy.broker}",
            )
        )
    if not args.verify_only and not args.ack_manual_ticket:
        issues.append(
            DataQualityIssue(
                "error",
                "live-ticket",
                "ack",
                "--ack-manual-ticket is required to create an external execution ticket",
            )
        )
    issues.extend(
        _live_readiness_issues(
            policy=policy,
            registry=ResearchRegistry(args.registry),
            halt_store=HaltStateStore(args.halt_state),
            drill_log=DrillLog(args.drill_log),
            paper_oos_dir=args.paper_oos_dir,
            paper_oos_prices=args.paper_oos_prices,
            catalog=catalog,
            required_prices=required_prices,
            as_of=_parse_date(args.as_of),
            max_price_age_days=args.max_price_age_days,
            require_order_submission=True,
            require_broker_preflight=True,
        )
    )
    # A non-finite/non-positive mark makes the deviation check (nan > x == False) and every
    # downstream notional/cash/weight comparison fail open — reject before it reaches risk (codex P2).
    for _flag, _val in (("--price", args.price), ("--limit-price", args.limit_price)):
        if _val is not None and (not math.isfinite(_val) or _val <= 0):
            issues.append(
                DataQualityIssue(
                    "error",
                    "live-ticket",
                    "price",
                    f"{_flag} must be a finite positive number (got {_val})",
                )
            )
    protection_values = (args.stop_loss, args.target_exit)
    if any(value is not None for value in protection_values):
        if args.side != "buy":
            issues.append(
                DataQualityIssue(
                    "error",
                    "live-ticket",
                    "protection",
                    "--stop-loss/--target-exit are supported only for BUY tickets",
                )
            )
        elif any(value is None for value in protection_values):
            issues.append(
                DataQualityIssue(
                    "error",
                    "live-ticket",
                    "protection",
                    "--stop-loss and --target-exit must be provided together",
                )
            )
        else:
            assert args.stop_loss is not None and args.target_exit is not None
            entry_reference = args.limit_price or args.price
            if not (
                math.isfinite(args.stop_loss)
                and math.isfinite(args.target_exit)
                and 0 < args.stop_loss < entry_reference < args.target_exit
            ):
                issues.append(
                    DataQualityIssue(
                        "error",
                        "live-ticket",
                        "protection",
                        "BUY protection must satisfy 0 < stop-loss < entry price < target-exit",
                    )
                )
    catalog_mark = _latest_catalog_mark(catalog, symbol, args.market)
    if catalog_mark is not None and args.max_mark_deviation >= 0:
        deviation = abs(args.price / catalog_mark - 1.0)
        if deviation > args.max_mark_deviation:
            issues.append(
                DataQualityIssue(
                    "error",
                    "live-ticket",
                    "mark",
                    f"--price {args.price:.4f} deviates {deviation:.2%} from latest catalog "
                    f"close {catalog_mark:.4f}; max {args.max_mark_deviation:.2%}",
                )
            )
    # Mirror live-submit: a manual-live execution must not bypass the sector concentration gate.
    # manual-paper (inspection) is lenient; manual-live fails closed on a missing/incomplete map.
    ticket_sectors, ticket_sector_issues, _ = _resolve_live_sectors(
        args.sectors_csv,
        symbol,
        subcommand="live-ticket",
        require_gate=broker_name.strip().lower() == "manual-live",
    )
    issues.extend(ticket_sector_issues)
    if issues:
        readiness = _format_live_readiness(
            policy,
            issues,
            required_prices=required_prices,
            require_order_submission=True,
            require_broker_preflight=True,
            paper_oos_prices=args.paper_oos_prices,
        )
        if args.verify_only:
            readiness = f"# Manual Order Verification Gate\n\n{readiness}"
        print(readiness)
        return 2
    try:
        broker = _live_broker_adapter(broker_name, args)
    except ValueError as exc:
        print(str(exc))
        return 2

    limit_price = args.limit_price
    if args.order_type == "limit" and limit_price is None:
        limit_price = args.price
    intent = OrderIntent(
        strategy=policy.strategy_id,
        symbol=symbol,
        market=args.market,
        side=args.side,
        qty=args.qty,
        order_type=args.order_type,
        limit_price=limit_price,
        time_in_force=args.time_in_force,
        rebalance_key=args.rebalance_key,
        reason="manual-live-ticket",
        asof_ts=datetime.now(UTC),
    ).normalized()
    reference_equity, peak_equity = _live_equity_refs(broker, args.equity_state, broker_name)
    store = JsonlOrderStore(args.ticket_log)
    result = process_order_intents(
        [intent],
        broker=broker,
        store=store,
        halt_store=HaltStateStore(args.halt_state),
        policy=live_risk_policy(policy),
        marks={symbol: args.price},
        dry_run=True,
        reference_equity=reference_equity,
        peak_equity=peak_equity,
        # Sector cap fires only with a map; manual-live fails closed above when it is missing.
        sectors=ticket_sectors,
    )[0]
    if result.status == "accepted" and not args.verify_only:
        _append_manual_ticket(
            args.ticket_log,
            broker_name,
            policy,
            intent,
            args.price,
            catalog_mark,
            stop_loss=args.stop_loss,
            target_exit=args.target_exit,
        )
    verify_only = bool(args.verify_only)
    lines = [
        "# Manual Order Verification Gate" if verify_only else "# Manual Order Ticket",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Mode | {'verification only' if verify_only else 'ticket creation'} |",
        f"| Ready | {'yes' if result.status == 'accepted' else 'no'} |",
        f"| Broker | {broker_name} |",
        f"| Strategy | {policy.strategy_id} |",
        f"| Client Order ID | {result.client_order_id} |",
        f"| Symbol | {symbol} |",
        f"| Side | {args.side} |",
        f"| Quantity | {args.qty:g} |",
        f"| Order Type | {args.order_type} |",
        f"| Limit Price | {_number_or_na(limit_price)} |",
        f"| Mark | {args.price:,.2f} |",
        f"| Catalog Mark | {_number_or_na(catalog_mark)} |",
        f"| Protective Stop | {_number_or_na(args.stop_loss)} |",
        f"| Profit Target | {_number_or_na(args.target_exit)} |",
        f"| Ticket Log | {args.ticket_log} |",
        f"| Action | {result.action} |",
        f"| Status | {result.status} |",
        (
            "| Execution Required | none; rerun without --verify-only after operator review |"
            if verify_only
            else "| Execution Required | external broker manual entry |"
        ),
    ]
    lines.extend(
        [
            "",
            "## Gate Coverage",
            "",
            "- live policy and order-submission arming",
            "- approved strategy, paper/shadow drills, and paper OOS evidence",
            "- persistent halt latch and broker account/market preflight",
            "- broker-attested price freshness and catalog-mark deviation",
            "- order notional, cash, exposure, concentration, and daily limits",
            "- sector classification and protective-price ordering",
        ]
    )
    if result.reasons:
        lines.extend(["", "## Reasons", ""])
        lines.extend(f"- {reason}" for reason in result.reasons)
    print("\n".join(lines))
    if result.status == "accepted":
        return 0
    if result.status == "uncertain":
        return 3
    return 2


def _append_manual_ticket(
    path: Path,
    broker_name: str,
    policy: LiveTradingPolicy,
    intent: OrderIntent,
    price: float,
    catalog_mark: float | None,
    *,
    stop_loss: float | None = None,
    target_exit: float | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "record_type": "manual_ticket",
        "created_at": datetime.now(UTC).isoformat(),
        "broker": broker_name,
        "strategy": policy.strategy_id,
        "policy_version": policy.policy_version,
        "client_order_id": intent.client_order_id,
        "symbol": intent.symbol,
        "market": intent.market,
        "side": intent.side,
        "qty": intent.qty,
        "order_type": intent.order_type,
        "limit_price": intent.limit_price,
        "time_in_force": intent.time_in_force,
        "rebalance_key": intent.rebalance_key,
        "operator_price": price,
        "catalog_mark": catalog_mark,
        "stop_loss": stop_loss,
        "target_exit": target_exit,
        "protection_status": (
            "external_protective_orders_required"
            if stop_loss is not None and target_exit is not None
            else "not_provided"
        ),
        "status": "ticket_created_external_execution_required",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _run_model_gate(args: argparse.Namespace) -> int:
    evidence = make_evidence(
        strategy_id=args.strategy_id,
        parameter_label=args.params,
        windows=args.windows,
        positive_test_rate=args.positive_test_rate,
        average_test_annualized_excess=args.avg_test_excess,
        worst_test_drawdown=args.worst_test_mdd,
        fee_stress_passed=args.fee_stress_passed,
        pit_audit_passed=args.pit_audit_passed,
        full_sample_annualized_return=args.full_sample_annualized_return,
        full_sample_max_drawdown=args.full_sample_mdd,
        stress_windows_tested=args.stress_windows_tested,
        worst_stress_return=args.worst_stress_return,
        stress_passed=args.stress_passed,
        worst_stress_excess=args.worst_stress_excess,
        mean_stress_excess=args.mean_stress_excess,
        command=args.source_command,
        source_commit=args.source_commit,
        notes=args.notes,
    )
    decision = evaluate_promotion(evidence)
    ResearchRegistry(args.registry).append(evidence, decision)
    lines = [
        "# Model Promotion Gate",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Strategy | {evidence.strategy_id} |",
        f"| Params | {evidence.parameter_label} |",
        f"| Windows | {evidence.windows} |",
        f"| Positive Test Rate | {evidence.positive_test_rate * 100:.1f}% |",
        f"| Average Test Annualized Excess | {evidence.average_test_annualized_excess * 100:+.2f}% |",
        f"| Worst Test MDD | {evidence.worst_test_drawdown * 100:.2f}% |",
        f"| Fee Stress | {'pass' if evidence.fee_stress_passed else 'fail'} |",
        f"| PIT Audit | {'pass' if evidence.pit_audit_passed else 'fail'} |",
        f"| Full-sample Annualized Return | {_pct_or_na(evidence.full_sample_annualized_return)} |",
        f"| Full-sample MDD | {_pct_or_na(evidence.full_sample_max_drawdown)} |",
        f"| Stress Windows Tested | {evidence.stress_windows_tested} |",
        f"| Worst Stress Return | {_pct_or_na(evidence.worst_stress_return)} |",
        f"| Worst Stress Excess vs Bench | {_pct_or_na(evidence.worst_stress_excess)} |",
        f"| Mean Stress Excess vs Bench | {_pct_or_na(evidence.mean_stress_excess)} |",
        f"| Stress Windows | {'pass' if evidence.stress_passed else 'fail'} |",
        f"| Verdict | {'APPROVED' if decision.passed else 'BLOCKED'} |",
    ]
    if decision.reasons:
        lines.extend(["", "## Reasons", ""])
        lines.extend(f"- {reason}" for reason in decision.reasons)
    print("\n".join(lines))
    return 0 if decision.passed else 2


@dataclass(frozen=True)
class LiveReadinessConfidence:
    score: int
    band: str
    deductions: tuple[tuple[str, int, str], ...]


_READINESS_AREA_PENALTIES = {
    "live-policy": 35,
    "broker-preflight": 30,
    "halt": 30,
    "price": 25,
    "model-gate": 20,
    "paper-oos": 20,
    "live-drill": 15,
}


def _live_readiness_issues(
    *,
    policy: LiveTradingPolicy,
    registry: ResearchRegistry,
    halt_store: HaltStateStore,
    drill_log: DrillLog,
    paper_oos_dir: Path,
    paper_oos_prices: Path | None,
    catalog: MarketDataCatalog,
    required_prices: tuple[tuple[str, str], ...],
    as_of: date,
    max_price_age_days: int,
    require_order_submission: bool,
    require_broker_preflight: bool,
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    try:
        if require_order_submission:
            assert_live_order_submission_enabled(policy)
        else:
            assert_live_trading_enabled(policy)
    except LiveTradingBlockedError as exc:
        issues.append(DataQualityIssue("error", "live-policy", "environment", str(exc)))
    if require_broker_preflight:
        issues.extend(_live_broker_preflight_issues(policy))
    if policy.strategy_id:
        for reason in registry.live_approval_issues(policy.strategy_id):
            issues.append(DataQualityIssue("error", "model-gate", policy.strategy_id, reason))
        drill_summary = drill_log.summary(
            policy.strategy_id,
            as_of=as_of,
            required_paper_days=policy.min_paper_days,
            required_shadow_days=policy.min_shadow_days,
        )
        for reason in drill_summary.reasons:
            issues.append(DataQualityIssue("error", "live-drill", policy.strategy_id, reason))
        if policy.min_paper_oos_periods > 0:
            ledger_path = _paper_oos_ledger_path(paper_oos_dir, policy.strategy_id)
            try:
                entries = _paper_oos_entries(ledger_path, policy.strategy_id, as_of=as_of)
                closed_periods = _paper_oos_closed_periods(entries)
            except Exception as exc:
                issues.append(
                    DataQualityIssue(
                        "error",
                        "paper-oos",
                        policy.strategy_id,
                        f"paper OOS ledger check failed: {exc}",
                    )
                )
            else:
                if closed_periods < policy.min_paper_oos_periods:
                    issues.append(
                        DataQualityIssue(
                            "error",
                            "paper-oos",
                            policy.strategy_id,
                            f"paper OOS closed periods {closed_periods} < "
                            f"{policy.min_paper_oos_periods} ({ledger_path})",
                        )
                    )
                elif paper_oos_prices is None:
                    issues.append(
                        DataQualityIssue(
                            "error",
                            "paper-oos",
                            policy.strategy_id,
                            "paper OOS prices CSV is required to score closed periods; "
                            "set --paper-oos-prices or LIVE_PAPER_OOS_PRICES",
                        )
                    )
                else:
                    try:
                        history = load_mark_price_history_csv(paper_oos_prices)
                        marks = mark_prices_at_dates(
                            history,
                            [entry.rebal_date for entry in entries],
                            max_staleness_days=max_price_age_days,
                        )
                        record = score_ledger(
                            entries,
                            marks,
                            backtest_excess_ann=policy.paper_oos_backtest_excess,
                        )
                    except Exception as exc:
                        issues.append(
                            DataQualityIssue(
                                "error",
                                "paper-oos",
                                policy.strategy_id,
                                f"paper OOS scoring failed: {exc}",
                            )
                        )
                    else:
                        if record.n_periods < policy.min_paper_oos_periods:
                            issues.append(
                                DataQualityIssue(
                                    "error",
                                    "paper-oos",
                                    policy.strategy_id,
                                    f"paper OOS scoreable periods {record.n_periods} < "
                                    f"{policy.min_paper_oos_periods} ({paper_oos_prices})",
                                )
                            )
                        if policy.min_paper_oos_vs_backtest > 0.0:
                            if record.vs_backtest is None:
                                # Ratio gate is required but uncomputable. When there
                                # ARE enough scoreable periods, the only cause is a
                                # zero/unset backtest excess — a config error that must
                                # block, never silently skip the gate. (Too-few-periods
                                # is already reported above, so don't pile on there.)
                                if record.n_periods >= policy.min_paper_oos_periods:
                                    issues.append(
                                        DataQualityIssue(
                                            "error",
                                            "paper-oos",
                                            policy.strategy_id,
                                            "paper OOS live/backtest ratio gate is required "
                                            f"({policy.min_paper_oos_vs_backtest:.2f}x) but could "
                                            "not be computed; set LIVE_PAPER_OOS_BACKTEST_EXCESS "
                                            "to the nonzero backtested annual excess",
                                        )
                                    )
                            elif record.vs_backtest < policy.min_paper_oos_vs_backtest:
                                issues.append(
                                    DataQualityIssue(
                                        "error",
                                        "paper-oos",
                                        policy.strategy_id,
                                        f"paper OOS live/backtest ratio {record.vs_backtest:.2f}x < "
                                        f"{policy.min_paper_oos_vs_backtest:.2f}x",
                                    )
                                )
    halt = halt_store.current()
    if halt.halted:
        issues.append(DataQualityIssue("error", "halt", "live-halt", f"halted: {halt.reason}"))
    if required_prices:
        try:
            quality_issues = evaluate_catalog_quality(
                catalog,
                as_of=as_of,
                required_macro=(),
                required_prices=required_prices,
                max_price_age_days=max_price_age_days,
                live_mode=True,
            )
        except Exception as exc:
            issues.append(
                DataQualityIssue(
                    "error", "price", "catalog", f"catalog quality check failed: {exc}"
                )
            )
        else:
            issues.extend(issue for issue in quality_issues if issue.severity != "info")
    return issues


def _live_broker_preflight_issues(policy: LiveTradingPolicy) -> list[DataQualityIssue]:
    broker_name = policy.broker.strip().lower()
    if not broker_name:
        return [
            DataQualityIssue(
                "error",
                "broker-preflight",
                "environment",
                "LIVE_BROKER is required for broker preflight",
            )
        ]
    if broker_name == "fake":
        return []
    if broker_name in _MANUAL_BROKERS:
        try:
            manual_broker = _manual_broker_adapter_from_env(broker_name)
            account = manual_broker.get_account()
            manual_broker.list_positions()
            clock = manual_broker.get_clock()
        except ValueError as exc:
            return [
                DataQualityIssue(
                    "error",
                    "broker-preflight",
                    broker_name,
                    str(exc),
                )
            ]
        return _broker_snapshot_preflight_issues(policy, broker_name, account, clock)
    if broker_name not in {"alpaca-paper", "alpaca-live"}:
        return [
            DataQualityIssue(
                "error",
                "broker-preflight",
                broker_name,
                f"unsupported broker for preflight: {policy.broker}",
            )
        ]
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
    credential_issue = _alpaca_credentials_issue(api_key, secret_key, purpose="broker preflight")
    if credential_issue:
        return [
            DataQualityIssue(
                "error",
                "broker-preflight",
                broker_name,
                credential_issue,
            )
        ]
    try:
        alpaca_broker = AlpacaBrokerAdapter(
            api_key,
            secret_key,
            paper=broker_name == "alpaca-paper",
        )
        account = alpaca_broker.get_account()
        alpaca_broker.list_positions()
        clock = alpaca_broker.get_clock()
    except Exception as exc:  # noqa: BLE001 - SDK/network/auth errors are normalized for operators.
        return [
            DataQualityIssue(
                "error",
                "broker-preflight",
                broker_name,
                f"broker account/positions preflight failed: {_compact_external_error(exc)}",
            )
        ]
    return _broker_snapshot_preflight_issues(policy, broker_name, account, clock)


def _broker_snapshot_preflight_issues(
    policy: LiveTradingPolicy,
    broker_name: str,
    account: AccountSnapshot,
    clock: BrokerClock,
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    if account.account_blocked:
        issues.append(
            DataQualityIssue("error", "broker-preflight", broker_name, "account is blocked")
        )
    if account.trading_blocked:
        issues.append(
            DataQualityIssue("error", "broker-preflight", broker_name, "trading is blocked")
        )
    if account.equity <= 0:
        issues.append(
            DataQualityIssue(
                "error",
                "broker-preflight",
                broker_name,
                f"account equity must be positive; got {account.equity:,.2f}",
            )
        )
    min_buying_power = live_risk_policy(policy).max_order_notional
    if account.buying_power < min_buying_power:
        issues.append(
            DataQualityIssue(
                "error",
                "broker-preflight",
                broker_name,
                f"buying power {account.buying_power:,.2f} < max order notional "
                f"{min_buying_power:,.2f}",
            )
        )
    if account.currency.upper() != "USD":
        issues.append(
            DataQualityIssue(
                "error",
                "broker-preflight",
                broker_name,
                f"account currency must be USD; got {account.currency}",
            )
        )
    if not clock.is_open:
        next_open = clock.next_open.isoformat() if clock.next_open else "unknown"
        issues.append(
            DataQualityIssue(
                "error",
                "broker-preflight",
                broker_name,
                f"market is closed at {clock.timestamp.isoformat()}; next_open={next_open}",
            )
        )
    return issues


def _live_readiness_confidence(issues: list[DataQualityIssue]) -> LiveReadinessConfidence:
    by_area: dict[str, list[DataQualityIssue]] = {}
    for issue in issues:
        if issue.severity == "info":
            continue
        by_area.setdefault(issue.area, []).append(issue)

    deductions: list[tuple[str, int, str]] = []
    for area in sorted(by_area):
        penalty = _READINESS_AREA_PENALTIES.get(area, 10)
        messages = "; ".join(issue.message for issue in by_area[area])
        deductions.append((area, penalty, messages))

    score = max(0, 100 - sum(penalty for _, penalty, _ in deductions))
    if issues:
        if score >= 70:
            band = "blocked-medium"
        elif score >= 40:
            band = "blocked-low"
        else:
            band = "blocked-critical"
    elif score >= 95:
        band = "high"
    elif score >= 80:
        band = "medium"
    else:
        band = "low"
    return LiveReadinessConfidence(score, band, tuple(deductions))


def _paper_oos_ledger_path(paper_oos_dir: Path, strategy_id: str) -> Path:
    return Path(paper_oos_dir) / f"paper-oos-ledger-{strategy_id}.jsonl"


def _paper_oos_entries(
    ledger_path: Path, strategy_id: str, *, as_of: date | None = None
) -> list[PaperOOSEntry]:
    """Ledger entries for one strategy, sorted by rebalance date.

    ``as_of`` enforces point-in-time integrity for the readiness gate: rebalances
    dated after ``as_of`` were not knowable then, so they must not count toward the
    closed-period or ratio gates. ISO date strings compare lexicographically.
    """
    cutoff = as_of.isoformat() if as_of is not None else None
    return sorted(
        (
            entry
            for entry in load_ledger(ledger_path)
            if entry.strategy_id == strategy_id
            and (cutoff is None or entry.rebal_date[:10] <= cutoff)
        ),
        key=lambda entry: entry.rebal_date,
    )


def _paper_oos_closed_periods(entries: list[PaperOOSEntry]) -> int:
    rebal_dates = {entry.rebal_date for entry in entries}
    return max(0, len(rebal_dates) - 1)


def _format_live_readiness(
    policy: LiveTradingPolicy,
    issues: list[DataQualityIssue],
    *,
    required_prices: tuple[tuple[str, str], ...],
    require_order_submission: bool,
    require_broker_preflight: bool,
    paper_oos_prices: Path | None,
) -> str:
    latest_prices = ", ".join(f"{market}:{symbol}" for symbol, market in required_prices) or "none"
    confidence = _live_readiness_confidence(issues)
    lines = [
        "# Live Readiness",
        "",
        "| Gate | Value |",
        "|---|---:|",
        f"| Ready | {'yes' if not issues else 'no'} |",
        f"| Operational Confidence | {confidence.score}% |",
        f"| Confidence Band | {confidence.band} |",
        "| Confidence Scope | live execution readiness, not profit forecast |",
        f"| Trading Env Ready | {'yes' if policy.ready else 'no'} |",
        f"| Order Submission Required | {'yes' if require_order_submission else 'no'} |",
        f"| Order Submission Enabled | {'yes' if policy.order_submission_enabled else 'no'} |",
        f"| Broker Preflight Required | {'yes' if require_broker_preflight else 'no'} |",
        f"| Strategy | {policy.strategy_id or 'missing'} |",
        f"| Broker | {policy.broker or 'missing'} |",
        f"| Max Capital | {policy.max_capital:,.2f} |",
        f"| Policy Version | {policy.policy_version or 'missing'} |",
        f"| Required Paper Drill Days | {policy.min_paper_days} |",
        f"| Required Shadow Drill Days | {policy.min_shadow_days} |",
        f"| Required Paper OOS Periods | {policy.min_paper_oos_periods} |",
        f"| Min Paper OOS / Backtest | {policy.min_paper_oos_vs_backtest:.2f}x |",
        f"| Paper OOS Prices | {str(paper_oos_prices) if paper_oos_prices else 'missing'} |",
        f"| Required Prices | {latest_prices} |",
    ]
    if issues:
        lines.extend(["", "## Blocking Issues", ""])
        lines.extend(f"- [{issue.area}:{issue.item}] {issue.message}" for issue in issues)
    if confidence.deductions:
        lines.extend(
            [
                "",
                "## Confidence Deductions",
                "",
                "| Area | Penalty | Reason |",
                "|---|---:|---|",
            ]
        )
        for area, penalty, reason in confidence.deductions:
            lines.append(f"| {area} | -{penalty}% | {reason} |")
    remediation = _live_readiness_remediation(issues, required_prices)
    if remediation:
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {item}" for item in remediation)
    return "\n".join(lines)


def _live_readiness_remediation(
    issues: list[DataQualityIssue],
    required_prices: tuple[tuple[str, str], ...],
) -> list[str]:
    actions: list[str] = []
    areas = {issue.area for issue in issues}
    messages = " ".join(issue.message for issue in issues).lower()
    if "live-policy" in areas:
        actions.append(
            "Set and review LIVE_TRADING_ENABLED=true, LIVE_TRADING_ACK_RISK=true, "
            "LIVE_ORDER_SUBMISSION_ENABLED=true, LIVE_STRATEGY_ID, LIVE_BROKER, "
            "LIVE_MAX_CAPITAL, and LIVE_POLICY_VERSION only after accepting the risk."
        )
    if "broker-preflight" in areas:
        if "alpaca_api_key" in messages or "401" in messages or "unauthorized" in messages:
            actions.append(
                "Replace or enable ALPACA_API_KEY/ALPACA_SECRET_KEY for the selected "
                "LIVE_BROKER, then rerun readiness with --require-broker-preflight."
            )
        if "operator-attested account env" in messages:
            actions.append(
                "For manual brokers, set LIVE_MANUAL_ACCOUNT_ID, LIVE_MANUAL_CASH, "
                "LIVE_MANUAL_EQUITY, LIVE_MANUAL_BUYING_POWER, LIVE_MANUAL_MARKET_OPEN=true, "
                "and optional LIVE_MANUAL_POSITIONS, then rerun readiness."
            )
        if "market is closed" in messages:
            actions.append("Rerun broker preflight during the regular market session.")
        if "buying power" in messages:
            actions.append(
                "Reduce LIVE_MAX_CAPITAL or fund the broker account so buying power covers "
                "the max order notional."
            )
    if "price" in areas and required_prices:
        symbols = ",".join(symbol for symbol, _market in required_prices)
        actions.append(
            "Load broker-grade prices with "
            f"`uv run trader live-price-stream {symbols} --max-bars {len(required_prices)} "
            "--timeout-seconds 90 --catalog-db data/store/live-prices.duckdb` "
            "or `uv run trader live-price-ingest SYMBOL --source external --price PRICE "
            "--ack-external-price --price-as-of YYYY-MM-DD` for manual broker execution."
        )
    if "paper-oos" in areas:
        actions.append(
            "Continue cadence-controlled paper OOS until the ledger has the required closed "
            "periods, then provide LIVE_PAPER_OOS_PRICES for scoring."
        )
    if "live-drill" in areas:
        actions.append(
            "Record passing paper/shadow drills with `uv run trader live-drill record ...`."
        )
    if "halt" in areas:
        actions.append(
            "Resolve the halt root cause, then clear it with `uv run trader live-halt clear`."
        )
    if "model-gate" in areas:
        actions.append(
            "Record an approved promotion decision with `uv run trader validate-model --record-gate ...`."
        )
    return actions


def _format_drill_summary(summary: DrillSummary) -> str:
    lines = [
        "# Live Drill Status",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Ready | {'yes' if summary.passed else 'no'} |",
        f"| Strategy | {summary.strategy_id} |",
        f"| As Of | {summary.as_of} |",
        f"| Paper Consecutive Days | {summary.paper_consecutive_days} |",
        f"| Paper Required Days | {summary.required_paper_days} |",
        f"| Shadow Consecutive Days | {summary.shadow_consecutive_days} |",
        f"| Shadow Required Days | {summary.required_shadow_days} |",
    ]
    if summary.reasons:
        lines.extend(["", "## Blocking Issues", ""])
        lines.extend(f"- {reason}" for reason in summary.reasons)
    return "\n".join(lines)


def _pct_or_na(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.2f}%"


def _number_or_na(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.4f}"


def _latest_sector_map() -> Path | None:
    """Newest ``data/sectors/*-sectors.csv`` (fetch_sectors output) or None. The sector cap
    only fires with a map, so discovery failure surfaces as a warning/blocker at the caller —
    never a silent skip."""
    sector_dir = ROOT / "data" / "sectors"
    candidates = sorted(
        sector_dir.glob("*-sectors.csv"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return candidates[0] if candidates else None


def _latest_catalog_mark(catalog: MarketDataCatalog, symbol: str, market: str) -> float | None:
    try:
        bars = catalog.get_bars(symbol, market=market)
    except Exception:
        return None
    if not bars:
        return None
    latest = bars[-1]
    return latest.close if latest.close > 0 else None


def _live_broker_adapter(broker_name: str, args: argparse.Namespace) -> BrokerAdapter:
    if not broker_name:
        raise ValueError("LIVE_BROKER or --broker is required")
    normalized = broker_name.strip().lower()
    if normalized == "fake":
        positions = _parse_fake_positions(getattr(args, "fake_position", None))
        return FakeBrokerAdapter(
            account=AccountSnapshot(
                account_id="live-submit-fake",
                buying_power=args.buying_power,
                cash=args.cash,
                equity=args.equity,
            ),
            positions=positions,
            mode=getattr(args, "fake_mode", "fill"),
        )
    if normalized in _MANUAL_BROKERS:
        return _manual_broker_adapter_from_env(normalized)
    if normalized in {"alpaca-paper", "alpaca-live"}:
        api_key = os.getenv("ALPACA_API_KEY", "").strip()
        secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
        credential_issue = _alpaca_credentials_issue(
            api_key, secret_key, purpose="Alpaca live-submit"
        )
        if credential_issue:
            raise ValueError(credential_issue)
        return AlpacaBrokerAdapter(api_key, secret_key, paper=normalized == "alpaca-paper")
    raise ValueError(f"Unsupported LIVE_BROKER: {broker_name}")


def _parse_expected_positions(value: str) -> dict[tuple[str, str], float]:
    expected: dict[tuple[str, str], float] = {}
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) == 2:
            symbol, qty = parts
            market = "us"
        elif len(parts) == 3:
            symbol, market, qty = parts
        else:
            raise ValueError("expected positions must use SYMBOL:QTY or SYMBOL:MARKET:QTY")
        expected[(_catalog_symbol(symbol, market), market.lower())] = float(qty)
    if not expected:
        raise ValueError("at least one expected position is required")
    return expected


def _parse_fake_positions(value: str | None) -> list[PositionSnapshot]:
    if not value:
        return []
    positions: list[PositionSnapshot] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) == 3:
            symbol, qty, market_value = parts
            market = "us"
        elif len(parts) == 4:
            symbol, market, qty, market_value = parts
        else:
            raise ValueError("fake positions must use SYMBOL:QTY:VALUE or SYMBOL:MARKET:QTY:VALUE")
        positions.append(
            PositionSnapshot(
                symbol=_catalog_symbol(symbol, market),
                market=market.lower(),
                qty=float(qty),
                market_value=float(market_value),
            )
        )
    return positions


def _run_quality(args: argparse.Namespace) -> int:
    catalog = MarketDataCatalog(args.catalog_db)
    required_flows: tuple[tuple[str, str], ...] = ()
    if args.require_flow:
        required_flows = tuple(
            (_catalog_symbol(symbol, args.flow_market), args.flow_market)
            for symbol in _parse_symbols(args.require_flow)
        )
    required_prices = _parse_symbol_market_pairs(args.require_price, default_market="us")
    issues = evaluate_catalog_quality(
        catalog,
        as_of=_parse_date(args.as_of),
        required_flows=required_flows,
        required_prices=required_prices,
        max_price_age_days=args.max_price_age_days,
        live_mode=args.live_policy,
    )
    print(format_quality_report(issues))
    if any(issue.severity == "error" for issue in issues):
        return 1
    if args.strict and any(issue.severity == "warn" for issue in issues):
        return 2
    return 0


def _run_dashboard(args: argparse.Namespace) -> int:
    command_args = [
        "uv",
        "run",
        "streamlit",
        "run",
        "dashboard/app.py",
        "--server.port",
        str(args.port),
    ]
    command = " ".join(command_args)
    print(command)
    print(f"Dashboard URL: http://localhost:{args.port}")
    if args.run:
        return subprocess.call(command_args)
    return 0


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 합니다.")
    return number


def _run_market_map(args: argparse.Namespace) -> int:
    from engine.market_map import build_market_map

    html, stats = build_market_map(
        weeks_count=args.weeks,
        catalog_db=args.catalog_db,
        offline=args.offline,
        dashboard_url=args.dashboard_url,
    )
    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(
        f"market-map: {stats['weeks']}주 · 매크로 {stats['macro_rows_with_data']}/{stats['macro_rows']}행 "
        f"· US 테마 {stats['us_themes']} · KR 테마 {stats['kr_themes']} · 칩 {stats['chips']} "
        f"· 카탈로그 심볼 {stats['catalog_symbols']} (최신 {stats['catalog_last_bar'] or '—'})"
    )
    print(f"페이지: {out}")
    return 0


def _load_or_fetch_universe(
    *,
    catalog: MarketDataCatalog,
    symbols: list[str],
    market: str,
    start: date,
    end: date,
    fetch_missing: bool,
    provider: str,
) -> dict[str, list]:
    catalog_symbols = {symbol: _catalog_symbol(symbol, market) for symbol in symbols}
    bars_by_symbol = {
        catalog_symbol: catalog.get_bars(catalog_symbol, market=market, start=start, end=end)
        for catalog_symbol in catalog_symbols.values()
    }
    if fetch_missing:
        for requested_symbol, catalog_symbol in catalog_symbols.items():
            bars = bars_by_symbol[catalog_symbol]
            if not _bars_need_fetch(bars, market, provider, start, end):
                continue
            fetched = _fetch_bars(requested_symbol, market, start, end, provider=provider)
            catalog.put_bars(fetched)
            bars_by_symbol[catalog_symbol] = catalog.get_bars(
                catalog_symbol, market=market, start=start, end=end
            )
    return {symbol: bars for symbol, bars in bars_by_symbol.items() if bars}


def _load_pit_universe(
    catalog: MarketDataCatalog,
    args: argparse.Namespace,
    *,
    market: str,
) -> list:
    if getattr(args, "universe_csv", None):
        records = load_universe_members_csv(args.universe_csv)
        catalog.put_universe_members(records)
        if not getattr(args, "pit_universe", None):
            universes = sorted({record.universe for record in records})
            if len(universes) == 1:
                args.pit_universe = universes[0]
            else:
                raise ValueError("--pit-universe is required when CSV contains multiple universes")
    if not getattr(args, "pit_universe", None):
        return []
    members = catalog.get_universe_members(args.pit_universe, market=market)
    if not members:
        raise ValueError(f"No PIT universe members found for {args.pit_universe} ({market}).")
    return members


def _symbols_for_request(symbols_arg: str, pit_members: list) -> list[str]:
    requested = _parse_symbols(symbols_arg)
    if pit_members and requested == ["ALL"]:
        return sorted({member.symbol for member in pit_members})
    if pit_members:
        allowed = set(requested)
        return sorted({member.symbol for member in pit_members if member.symbol in allowed})
    return requested


def _filter_pit_members(
    pit_members: list[UniverseMember],
    symbols: list[str],
) -> list[UniverseMember]:
    if not pit_members:
        return []
    allowed = {symbol.upper() for symbol in symbols}
    filtered = [member for member in pit_members if member.symbol.upper() in allowed]
    if not filtered:
        raise ValueError("requested symbols are not present in the PIT universe")
    return filtered


def _load_delisting_returns(
    catalog: MarketDataCatalog,
    args: argparse.Namespace,
    symbols: list[str],
    market: str,
    start: date,
    end: date,
) -> list[DelistingReturn]:
    if getattr(args, "delisting_returns_csv", None):
        records = load_delisting_returns_csv(args.delisting_returns_csv)
        catalog.put_delisting_returns(records)
    return catalog.get_delisting_returns(symbols=symbols, market=market, start=start, end=end)


def _preflight_universe_audit(
    catalog: MarketDataCatalog,
    args: argparse.Namespace,
    *,
    pit_members: list[UniverseMember],
    symbols: list[str],
    market: str,
    start: date,
    end: date,
) -> int:
    if getattr(args, "skip_universe_audit", False) or not pit_members:
        return 0
    universe = getattr(args, "pit_universe", None)
    if not universe:
        return 0
    report = run_universe_audit(
        catalog,
        universe=universe,
        market=market,
        start=start,
        end=end,
        symbols=symbols,
        require_fundamentals=getattr(args, "audit_require_fundamentals", False),
        require_delistings=not getattr(args, "audit_no_require_delistings", False),
        rebalance_days=getattr(args, "rebalance_days", 21),
    )
    if report.ready:
        print(
            f"Universe audit: OK ({report.active_symbols} symbols, "
            f"{report.error_count} errors, {report.warn_count} warnings)"
        )
        return 0
    print(format_universe_audit_report(report))
    print("Backtest blocked by universe audit errors.")
    return 2


def _fundamentals_history(
    catalog: MarketDataCatalog,
    symbols: list[str],
    market: str,
) -> dict[str, FundamentalRecord | list[FundamentalRecord]]:
    rows: dict[str, FundamentalRecord | list[FundamentalRecord]] = {}
    for symbol in symbols:
        records = catalog.get_fundamentals(symbol, market=market, limit=0)
        if records:
            rows[symbol.upper()] = records
    return rows


def _load_or_fetch_benchmark(
    *,
    catalog: MarketDataCatalog,
    symbol: str | None,
    market: str,
    start: date,
    end: date,
    fetch_missing: bool,
    provider: str,
) -> list | None:
    if symbol is None:
        return None
    catalog_symbol = _catalog_symbol(symbol, market)
    bars = catalog.get_bars(catalog_symbol, market=market, start=start, end=end)
    if _bars_need_fetch(bars, market, provider, start, end) and fetch_missing:
        fetched = _fetch_bars(symbol, market, start, end, provider=provider)
        catalog.put_bars(fetched)
        bars = catalog.get_bars(catalog_symbol, market=market, start=start, end=end)
    if not bars:
        raise ValueError(f"No benchmark bars available for {catalog_symbol} ({market}).")
    return bars


def _bars_need_fetch(
    bars: list,
    market: str,
    provider: str,
    start: date,
    end: date,
) -> bool:
    if not bars:
        return True
    return bars[0].ts > start or bars[-1].ts < end or _bars_need_refresh(bars, market, provider)


def _bars_need_refresh(bars: list, market: str, provider: str) -> bool:
    if not bars:
        return False
    if market.lower() != "us" or provider.lower() not in {"auto", "yahoo"}:
        return False
    return any(YAHOO_ADJUSTED_SOURCE_MARKER not in getattr(bar, "source", "") for bar in bars)


def _benchmark_provider(provider: str, market: str, benchmark_market: str | None) -> str:
    if provider == "auto" or benchmark_market is None or benchmark_market == market:
        return provider
    return "auto"


def _latest_close(catalog: MarketDataCatalog, symbol: str, market: str) -> float:
    bars = catalog.get_bars(symbol, market=market)
    if not bars:
        raise ValueError(f"{symbol}: no bars found. Ingest price data first.")
    return bars[-1].close


def _format_valuation(record: ValuationRecord) -> str:
    return "\n".join(
        [
            f"# Valuation - {record.symbol}",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Market | {record.market} |",
            f"| Current Price | {record.current_price:,.2f} |",
            f"| Fair Value | {record.fair_value:,.2f} |",
            f"| Discount | {record.discount_pct * 100:+.2f}% |",
            f"| Rating | {record.rating:+d} |",
            f"| Confidence | {record.confidence} |",
        ]
    )


def _format_pair_analysis(
    analysis: PairAnalysis,
    validation: PairBacktestResult | None = None,
    short_gate: ShortabilityCheck | None = None,
) -> str:
    half_life = "n/a" if analysis.half_life_days is None else f"{analysis.half_life_days:.1f} days"
    if analysis.signal is None:
        signal = "No entry signal"
    else:
        signal = f"Long {analysis.signal.long_symbol} / Short {analysis.signal.short_symbol}"
    lines = [
        f"# Pair Analysis - {analysis.first_symbol}/{analysis.second_symbol}",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Observations | {analysis.observations} |",
        f"| Hedge Ratio | {analysis.hedge_ratio:.4f} |",
        f"| Intercept | {analysis.intercept:.4f} |",
        f"| Spread Z-score | {analysis.z_score:+.2f} |",
        f"| Correlation | {analysis.correlation:.3f} |",
        f"| Half-life | {half_life} |",
        f"| State | {analysis.state} |",
        f"| Signal | {signal} |",
    ]
    if validation is not None:
        verdict = "PASS" if validation.passed else "FAIL"
        reasons = "OK" if validation.passed else "; ".join(validation.reasons)
        lines.extend(
            [
                "",
                "## Cost-adjusted Rolling Validation",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| Verdict | {verdict} |",
                f"| Reasons | {reasons} |",
                f"| OOS Observations | {validation.observations} |",
                f"| Trades | {validation.trades} |",
                f"| Gross Return | {validation.gross_return * 100:+.2f}% |",
                f"| Net Return | {validation.net_return * 100:+.2f}% |",
                f"| Annualized Return | {validation.annualized_return * 100:+.2f}% |",
                f"| Sharpe | {validation.sharpe:.2f} |",
                f"| Max Drawdown | {validation.max_drawdown * 100:.2f}% |",
                f"| Hit Rate | {validation.hit_rate * 100:.2f}% |",
                f"| Fee + Slippage | {validation.fee_bps:.2f} + {validation.slippage_bps:.2f} bps |",
            ]
        )
    if short_gate is not None:
        verdict = "PASS" if short_gate.passed else "FAIL"
        reasons = "OK" if short_gate.passed else "; ".join(short_gate.reasons)
        warnings = "OK" if not short_gate.warnings else "; ".join(short_gate.warnings)
        borrow_fee = (
            "n/a" if short_gate.borrow_fee_bps is None else f"{short_gate.borrow_fee_bps:.2f} bps"
        )
        lines.extend(
            [
                "",
                "## Shortability Gate",
                "",
                "| Metric | Value |",
                "|---|---:|",
                f"| Verdict | {verdict} |",
                f"| Short Symbol | {short_gate.symbol} |",
                f"| Market | {short_gate.market} |",
                f"| As Of | {short_gate.asof_date} |",
                f"| Borrow Fee | {borrow_fee} |",
                f"| Source | {short_gate.source} |",
                f"| Confidence | {short_gate.confidence} |",
                f"| Reasons | {reasons} |",
                f"| Warnings | {warnings} |",
            ]
        )
    return "\n".join(lines)


def _format_vix_calculation(
    result: VixCalculationResult,
    *,
    quote_source: str,
    risk_free_source: str,
    extra_warnings: tuple[str, ...] = (),
    stored_market: str | None = None,
) -> str:
    warnings = (*result.warnings, *extra_warnings)
    lines = [
        "# VIX-like Option Volatility",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| As of | {result.asof_date} |",
        f"| Target Days | {result.target_days} |",
        f"| Volatility | {result.volatility * 100:.2f}% |",
        f"| VIX Points | {result.volatility * 100:.2f} |",
        f"| Risk-free Rate | {result.risk_free_rate * 100:.3f}% |",
        f"| Risk-free Source | {risk_free_source} |",
        f"| Quote Source | {quote_source} |",
        f"| Near Expiration | {result.near.expiration} ({result.near.days_to_expiration}d) |",
        f"| Near Forward | {result.near.forward:.2f} |",
        f"| Near K0 | {result.near.reference_strike:.2f} |",
        f"| Next Expiration | {result.next.expiration} ({result.next.days_to_expiration}d) |",
        f"| Next Forward | {result.next.forward:.2f} |",
        f"| Next K0 | {result.next.reference_strike:.2f} |",
        f"| Source | {result.source} |",
    ]
    if stored_market is not None:
        lines.append(f"| Stored | option_sentiment/{stored_market} |")
    if warnings:
        lines.extend(["", "## Quality Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def _pair_shortability_gate(
    analysis: PairAnalysis,
    args: argparse.Namespace,
    asof_date: date,
    market: str,
) -> ShortabilityCheck | None:
    if analysis.signal is None:
        return None
    if not args.shortability_csv and not args.require_shortability:
        return None
    rows = load_short_availability_csv(args.shortability_csv) if args.shortability_csv else []
    return check_shortability(
        analysis.signal.short_symbol,
        market,
        rows,
        asof_date=asof_date,
        max_borrow_fee_bps=args.max_borrow_fee_bps,
        require_row=args.require_shortability,
        max_age_days=args.shortability_max_age_days,
        min_confidence=args.min_shortability_confidence,
    )


def _load_vix_quotes(args: argparse.Namespace, asof_date: date) -> tuple[list, str]:
    if args.source == "csv":
        if args.file is None:
            raise ValueError("--file is required when --source csv")
        return load_option_quotes_csv(args.file), f"csv:{args.file.name}"
    if args.underlying is None:
        raise ValueError("--underlying is required when --source yahoo")
    expirations = (
        tuple(_parse_date(item.strip()) for item in args.expirations.split(","))
        if args.expirations
        else ()
    )
    fetched = fetch_yahoo_option_quotes(
        args.underlying,
        asof_date=asof_date,
        target_days=args.target_days,
        expirations=expirations,
    )
    return fetched.quotes, fetched.source


def _resolve_vix_risk_free_rate(
    catalog: MarketDataCatalog,
    args: argparse.Namespace,
    asof_date: date,
) -> tuple[float, str, tuple[str, ...]]:
    if args.risk_free_rate is not None:
        return args.risk_free_rate, "manual:--risk-free-rate", ()
    series = (args.risk_free_series or "").strip().upper()
    if not series:
        return 0.0, "zero", ("risk-free series disabled; using 0.0",)

    asof_ts = datetime.combine(asof_date, datetime.max.time())
    rows = catalog.get_macro(series, as_of=asof_ts, limit=1)
    if not rows:
        return 0.0, "zero", (f"{series} not found in catalog; using 0.0 risk-free rate",)

    latest = rows[0]
    warnings: list[str] = []
    age_days = (asof_date - latest.asof_date).days
    if age_days > args.risk_free_max_age_days:
        warnings.append(
            f"{series} is {age_days} days old; max age is {args.risk_free_max_age_days}"
        )
    raw_rate = latest.value
    annualized_rate = raw_rate / 100.0 if abs(raw_rate) > 1 else raw_rate
    return annualized_rate, f"catalog:{series}@{latest.asof_date}", tuple(warnings)


def _source_summary(records: list) -> str:
    sources = sorted({getattr(record, "source", "") or "unknown" for record in records})
    return ",".join(sources)


def _emit(text: str, output: Path | None) -> int:
    if output is None:
        print(text)
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


def _fetch_bars(symbol: str, market: str, start: date, end: date, provider: str) -> list:
    market_key = market.lower()
    provider_key = provider.lower()
    if provider_key == "auto":
        if market_key == "crypto":
            provider_key = "ccxt"
        elif market_key in {"kospi", "kosdaq"}:
            provider_key = "pykrx"
        else:
            provider_key = "yahoo"
    if provider_key == "ccxt":
        if market_key != "crypto":
            raise ValueError("ccxt provider is only supported for --market crypto")
        return fetch_ccxt_bars(symbol, start, end)
    if provider_key == "pykrx":
        return fetch_pykrx_bars(symbol, market, start, end)
    if provider_key == "yahoo":
        return fetch_yahoo_bars(symbol, market, start, end)
    raise ValueError(f"unknown provider: {provider}")


def _catalog_symbol(symbol: str, market: str) -> str:
    market_key = market.lower()
    if market_key in {"kospi", "kosdaq"}:
        return normalize_kr_symbol(symbol)
    if market_key == "crypto":
        return normalize_crypto_symbol(symbol)
    return symbol.upper()


def _run_copilot(argv: list[str]) -> int:
    forwarded = _with_workspace_defaults(argv)
    if str(COPILOT_DIR) not in sys.path:
        sys.path.insert(0, str(COPILOT_DIR))

    from trading_copilot.cli import main as copilot_main

    return copilot_main(forwarded)


def _with_workspace_defaults(argv: list[str]) -> list[str]:
    defaults: list[str] = []
    if "--financial-services-dir" not in argv:
        defaults.extend(["--financial-services-dir", str(DEFAULT_FINANCIAL_SERVICES_DIR)])
    if "--db" not in argv:
        defaults.extend(["--db", str(DEFAULT_DB)])
    return [*defaults, *argv]


def _add_market_symbol_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol")
    parser.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])


def _add_market_symbols_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbols", help="Ticker or comma-separated tickers, e.g. MSFT,AAPL,NVDA")
    parser.add_argument("--market", default="us", choices=["us", "kospi", "kosdaq", "crypto"])


def _add_provider_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "yahoo", "ccxt", "pykrx"],
        help="Data provider. auto uses CCXT for crypto, pykrx for Korean equities, and Yahoo otherwise.",
    )


def _add_benchmark_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--benchmark",
        help="Optional benchmark symbol for excess-return comparison, e.g. SPY.",
    )
    parser.add_argument(
        "--benchmark-market",
        choices=["us", "kospi", "kosdaq", "crypto"],
        help="Benchmark market. Defaults to the strategy market.",
    )


def _add_pit_universe_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pit-universe",
        help="Point-in-time universe name stored in the catalog.",
    )
    parser.add_argument(
        "--universe-csv",
        type=Path,
        help="CSV with universe,symbol,market,start_date,end_date,source,confidence columns.",
    )


def _add_delisting_return_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--delisting-returns-csv",
        type=Path,
        help="CSV with symbol,market,ts,return_pct,source,confidence columns.",
    )


def _add_preflight_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skip-universe-audit",
        action="store_true",
        help="Skip the automatic PIT universe audit preflight.",
    )
    parser.add_argument(
        "--audit-require-fundamentals",
        action="store_true",
        help="Require PIT fundamentals during the automatic universe audit.",
    )
    parser.add_argument(
        "--audit-no-require-delistings",
        action="store_true",
        help="Do not require explicit delisting returns for ended PIT members.",
    )


def _add_factor_portfolio_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--momentum-lookback", type=int, default=252)
    parser.add_argument(
        "--ensemble-momentum-lookbacks",
        help="Comma-separated momentum lookbacks to average into one live trading signal, e.g. 63,126,252.",
    )
    parser.add_argument("--reversal-lookback", type=int, default=21)
    parser.add_argument("--volatility-lookback", type=int, default=63)
    parser.add_argument("--risk-filter-lookback", type=int, default=200)
    parser.add_argument(
        "--ensemble-risk-filter-lookbacks",
        help="Comma-separated benchmark moving-average filters to vote risk-on/off. 0 disables one vote.",
    )
    parser.add_argument(
        "--risk-filter-vote-threshold",
        type=float,
        default=0.5,
        help="Minimum share of risk-filter votes that must be risk-on.",
    )
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--rebalance-days", type=int, default=21)
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--fee-bps", type=float, default=2.0)
    parser.add_argument(
        "--defensive-symbol",
        default="TLT",
        help="Symbol to hold when risk filter is off. Set to empty string for cash.",
    )
    parser.add_argument(
        "--defensive-basket",
        help="Comma-separated defensive risk-off basket ranked by momentum, e.g. TLT,SHY,CASH.",
    )
    parser.add_argument(
        "--defensive-selection-lookback",
        type=int,
        default=63,
        help="Lookback used to choose the strongest defensive basket asset.",
    )
    parser.add_argument(
        "--weighting",
        default="inverse-vol",
        choices=["inverse-vol", "equal"],
        help="Position sizing for selected assets.",
    )
    parser.add_argument(
        "--max-risk-weight",
        type=float,
        default=1.0,
        help="Cap each non-defensive asset weight and put overflow into the defensive asset or cash.",
    )
    parser.add_argument(
        "--drawdown-guard",
        type=float,
        default=0.0,
        help="Switch to the defensive asset when strategy drawdown reaches this decimal threshold.",
    )
    parser.add_argument(
        "--defensive-only",
        action="store_true",
        help="Exclude the defensive symbol from normal factor ranking and use it only for risk-off/capped overflow.",
    )
    parser.add_argument(
        "--volatility-target",
        type=float,
        default=0.0,
        help="Annualized portfolio volatility target. 0 disables live volatility scaling.",
    )
    parser.add_argument(
        "--max-leverage",
        type=float,
        default=1.0,
        help="Maximum gross exposure allowed by volatility targeting.",
    )
    parser.add_argument(
        "--crash-hedge-symbols",
        help="Comma-separated hedge sleeve symbols used during benchmark drawdowns, e.g. QID,SDS.",
    )
    parser.add_argument(
        "--crash-hedge-weight",
        type=float,
        default=0.0,
        help="Target portfolio weight assigned to the crash hedge sleeve when triggered.",
    )
    parser.add_argument(
        "--crash-hedge-trigger-lookback",
        type=int,
        default=21,
        help="Trailing benchmark lookback used to measure crash drawdown.",
    )
    parser.add_argument(
        "--crash-hedge-trigger-drawdown",
        type=float,
        default=0.10,
        help="Activate crash hedge when trailing benchmark drawdown reaches this decimal threshold.",
    )
    parser.add_argument(
        "--crash-hedge-selection-lookback",
        type=int,
        default=5,
        help="Momentum lookback used to select the strongest crash hedge symbol.",
    )
    parser.add_argument(
        "--crash-hedge-hold-days",
        type=int,
        default=0,
        help="Hold crash hedge for N trading days after a trigger. 0 follows the signal directly.",
    )
    parser.add_argument(
        "--volume-lookback-short",
        type=int,
        default=21,
        help="Short window (bars) for volume-spike ratio numerator. Default 21 (~1 month).",
    )
    parser.add_argument(
        "--volume-lookback-long",
        type=int,
        default=252,
        help="Long window (bars) for volume-spike ratio denominator. Default 252 (~1 year).",
    )
    parser.add_argument(
        "--volume-weight",
        type=float,
        default=0.0,
        help=(
            "Weight of volume-spike z-score in composite factor score. "
            "0 disables volume signal (backward-compatible default). "
            "score = (1-w)*existing_factors + w*volume_z"
        ),
    )
    parser.add_argument(
        "--quality-weight",
        type=float,
        default=0.0,
        help=(
            "Weight of AQR Quality z-score in composite factor score. "
            "0 disables quality signal (backward-compatible default). "
            "Quality = z(ROE) + z(GrossProfit/Assets) + z(-Leverage). "
            "Requires fundamentals in catalog (trader fundamentals <symbol> --provider yfinance)."
        ),
    )
    parser.add_argument(
        "--value-weight",
        type=float,
        default=0.0,
        help=(
            "Weight of AQR Value z-score in composite factor score. "
            "0 disables value signal (backward-compatible default). "
            "Value = z(E/P) + z(FCF/P). "
            "Requires fundamentals in catalog (trader fundamentals <symbol> --provider yfinance)."
        ),
    )
    parser.add_argument(
        "--regime-cash-enable",
        action="store_true",
        default=False,
        help=(
            "Enable bond-equity correlation regime filter. "
            "When SPY < MA(corr-window) AND rolling corr(SPY, corr-symbol) > threshold, "
            "override defensive symbol with --regime-cash-override-symbol."
        ),
    )
    parser.add_argument(
        "--regime-cash-corr-symbol",
        default="TLT",
        help="Bond proxy symbol whose correlation with the benchmark is monitored. Default TLT.",
    )
    parser.add_argument(
        "--regime-cash-corr-window",
        type=int,
        default=60,
        help="Rolling window (trading days) for SPY–bond correlation and MA. Default 60.",
    )
    parser.add_argument(
        "--regime-cash-corr-threshold",
        type=float,
        default=0.2,
        help="Correlation threshold above which bonds are no longer considered a hedge. Default 0.2.",
    )
    parser.add_argument(
        "--regime-cash-override-symbol",
        default="SHY",
        help=(
            "Symbol to hold in risk-off regime when bond-equity correlation is elevated. "
            "Use CASH or empty string for cash. Default SHY."
        ),
    )


def _add_crash_hedge_search_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--crash-hedge-weights",
        help="Comma-separated crash hedge weights to search in walk-forward selection.",
    )
    parser.add_argument(
        "--crash-hedge-trigger-lookbacks",
        help="Comma-separated benchmark drawdown lookbacks to search for crash hedge triggers.",
    )
    parser.add_argument(
        "--crash-hedge-trigger-drawdowns",
        help="Comma-separated benchmark drawdown thresholds to search, e.g. 0.03,0.05,0.08.",
    )
    parser.add_argument(
        "--crash-hedge-selection-lookbacks",
        help="Comma-separated hedge symbol momentum lookbacks to search.",
    )
    parser.add_argument(
        "--crash-hedge-hold-days-values",
        help="Comma-separated crash hedge hold-day values to search. 0 follows the signal directly.",
    )


def _add_date_args(parser: argparse.ArgumentParser, required: bool = False) -> None:
    default_start = (date.today() - timedelta(days=365 * 3)).isoformat()
    parser.add_argument("--start", default=None if required else default_start, required=required)
    parser.add_argument("--end", default=date.today().isoformat())


def _factor_warmup_start(start: date, *lookbacks: int) -> date:
    max_lookback = max(lookbacks)
    calendar_days = max_lookback * 365 // 252 + 45
    return start - timedelta(days=calendar_days)


def _earliest_validation_start(
    start: date,
    end: date,
    stress_windows: tuple[StressWindow, ...],
) -> date:
    starts = [start]
    for window in stress_windows:
        if window.end >= start and window.start <= end:
            starts.append(window.start)
    return min(starts)


def _validation_params_label(args: argparse.Namespace) -> str:
    momentum_ensemble = args.ensemble_momentum_lookbacks or "off"
    risk_ensemble = args.ensemble_risk_filter_lookbacks or "off"
    defensive_basket = args.defensive_basket or args.defensive_symbol or "cash"
    crash_hedge = args.crash_hedge_symbols or "off"
    return (
        f"M{args.momentum_lookback}/R{args.reversal_lookback}/"
        f"V{args.volatility_lookback}/RF{args.risk_filter_lookback}/"
        f"Top{args.top_n}/{args.weighting}/Reb{args.rebalance_days}/"
        f"MaxRisk{args.max_risk_weight:.2f}/DD{args.drawdown_guard:.2f}/"
        f"DefOnly{int(args.defensive_only)}/EnsM{momentum_ensemble}/"
        f"EnsRF{risk_ensemble}/DefBasket{defensive_basket}/"
        f"VolTgt{args.volatility_target:.2f}/"
        f"Crash{crash_hedge}@{args.crash_hedge_weight:.2f}"
    )


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_optional_date(value: str | None) -> date | None:
    if value is None:
        return None
    return _parse_date(value)


def _parse_symbols(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols:
        raise ValueError("at least one symbol is required")
    return symbols


def _parse_symbol_market_pairs(
    value: str | None,
    *,
    default_market: str,
) -> tuple[tuple[str, str], ...]:
    if not value:
        return ()
    pairs: list[tuple[str, str]] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        if ":" in item:
            symbol, market = [part.strip() for part in item.split(":", 1)]
        else:
            symbol, market = item, default_market
        pairs.append((_catalog_symbol(symbol, market), market.lower()))
    if not pairs:
        raise ValueError("at least one symbol is required")
    return tuple(pairs)


def _parse_ints(value: str) -> list[int]:
    items = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("at least one integer is required")
    if any(item < 1 for item in items):
        raise ValueError("integer values must be >= 1")
    return items


def _parse_nonnegative_ints(value: str) -> list[int]:
    items = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("at least one integer is required")
    if any(item < 0 for item in items):
        raise ValueError("integer values must be >= 0")
    return items


def _parse_floats(
    value: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    min_exclusive: bool = False,
    max_exclusive: bool = False,
) -> list[float]:
    items = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("at least one float is required")
    for item in items:
        if min_value is not None:
            below_min = item <= min_value if min_exclusive else item < min_value
            if below_min:
                raise ValueError(
                    f"float values must be {'>' if min_exclusive else '>='} {min_value}"
                )
        if max_value is not None:
            above_max = item >= max_value if max_exclusive else item > max_value
            if above_max:
                raise ValueError(
                    f"float values must be {'<' if max_exclusive else '<='} {max_value}"
                )
    return items


def _parse_weighting_modes(value: str) -> list[str]:
    modes = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not modes:
        raise ValueError("at least one weighting mode is required")
    invalid = [item for item in modes if item not in {"inverse-vol", "equal"}]
    if invalid:
        raise ValueError(f"unknown weighting mode(s): {', '.join(invalid)}")
    return modes


def _parse_defensive_symbols(value: str) -> list[str | None]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols:
        raise ValueError("at least one defensive symbol is required")
    return [None if symbol in {"CASH", "NONE"} else symbol for symbol in symbols]


def _parse_optional_positive_int_tuple(value: str | None) -> tuple[int, ...] | None:
    return tuple(_parse_ints(value)) if value else None


def _parse_optional_nonnegative_int_tuple(value: str | None) -> tuple[int, ...] | None:
    return tuple(_parse_nonnegative_ints(value)) if value else None


def _parse_optional_float_tuple(
    value: str | None,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    min_exclusive: bool = False,
    max_exclusive: bool = False,
) -> tuple[float, ...] | None:
    if not value:
        return None
    return tuple(
        _parse_floats(
            value,
            min_value=min_value,
            max_value=max_value,
            min_exclusive=min_exclusive,
            max_exclusive=max_exclusive,
        )
    )


def _parse_optional_defensive_symbol_tuple(value: str | None) -> tuple[str | None, ...] | None:
    return tuple(_parse_defensive_symbols(value)) if value else None


def _parse_optional_symbol_tuple(value: str | None) -> tuple[str, ...] | None:
    return tuple(_parse_symbols(value)) if value else None
