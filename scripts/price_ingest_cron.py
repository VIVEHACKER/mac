"""Cron runner: keep the live price catalog fresh for the marks the gates actually need.

Audit P1 closure (operational leg): the live catalog was 100% manually-ingested yahoo EOD and
went weeks stale — the freshness gate then hard-blocks live-submit, so the system was safe but
inoperable. This runner ingests the union of every paper-drill book's positions plus the
benchmark on a schedule, via the SAME ``trader live-price-ingest`` CLI the operator uses.

Key contract (semi-auto model): with no ALPACA keys it falls back to the keyless yahoo EOD
source; the moment ``ALPACA_API_KEY``/``ALPACA_SECRET_KEY`` land in the environment (.env) the
SAME cron switches to broker-grade IEX bars — the keys are the single activation switch, no
code or crontab change. Install with ``scripts/install_price_ingest_cron.sh`` (idempotent).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "out"
BENCHMARK = "SPY"


def collect_symbols(*, out_dir: Path | None = None, benchmark: str = BENCHMARK) -> list[str]:
    """Union of positions across every paper-drill state book, plus the benchmark (sorted).

    These are exactly the marks live-submit's freshness gate and the OOS ledger scoring need;
    an unreadable/malformed state file is skipped (the freshness gate still catches the gap)."""
    directory = Path(out_dir) if out_dir is not None else OUT_DIR
    symbols: set[str] = {benchmark.upper()}
    for state_path in sorted(directory.glob("paper-drill-state-*.json")):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for symbol in state.get("positions") or {}:
            if symbol:
                symbols.add(str(symbol).upper())
    return sorted(symbols)


def decide_source() -> str:
    """``alpaca`` (broker-grade IEX) when both keys are set, else the keyless yahoo fallback."""
    api_key = os.getenv("ALPACA_API_KEY", "").strip()
    secret_key = os.getenv("ALPACA_SECRET_KEY", "").strip()
    return "alpaca" if api_key and secret_key else "yahoo"


def run(
    *,
    out_dir: Path | None = None,
    benchmark: str = BENCHMARK,
    cli_main: Callable[[list[str]], int] | None = None,
) -> int:
    if cli_main is None:  # lazy: keep the trader CLI import off the test path
        from trader.cli import main as cli_main  # type: ignore[no-redef]
    symbols = collect_symbols(out_dir=out_dir, benchmark=benchmark)
    source = decide_source()
    if source == "yahoo":
        print(
            "price-ingest: no ALPACA_API_KEY/SECRET — using keyless yahoo EOD fallback. "
            "Set the keys in .env to switch this same cron to broker-grade IEX bars."
        )
    argv = ["live-price-ingest", ",".join(symbols), "--source", source]
    return int(cli_main(argv) or 0)


if __name__ == "__main__":
    raise SystemExit(run())
