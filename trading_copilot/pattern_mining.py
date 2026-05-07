from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO


DEFAULT_ASSETS = ("SPY", "QQQ", "TLT", "GLD", "USO")
DEFAULT_HORIZONS = (21, 63, 126, 252)
ASSET_SETS: dict[str, tuple[str, ...]] = {
    "core": DEFAULT_ASSETS,
    "macro": ("SPY", "QQQ", "TLT", "GLD", "UUP"),
    "commodities": ("GLD", "SLV", "USO", "DBA"),
}


@dataclass(frozen=True)
class PatternResult:
    condition: str
    asset: str
    horizon_days: int
    samples: int
    wins: int
    win_rate: float
    wilson_lower_95: float
    average_return: float
    max_drawdown: float


@dataclass(frozen=True)
class PatternReport:
    results: tuple[PatternResult, ...]
    errors: tuple[str, ...]


def expand_asset_set(asset_set: str) -> tuple[str, ...]:
    return ASSET_SETS.get(asset_set, DEFAULT_ASSETS)


def mine_default_patterns(
    *,
    macro_provider,
    history_provider,
    assets: tuple[str, ...] = DEFAULT_ASSETS,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    min_samples: int = 5,
) -> PatternReport:
    return PatternReport(
        results=(),
        errors=("pattern mining is unavailable in this lightweight build",),
    )


def format_pattern_report(result: PatternReport, limit: int = 25) -> str:
    lines = [
        "# Historical Pattern Mining",
        "",
        "Not investment advice. Historical samples do not guarantee future returns.",
        "",
        "## Results",
    ]
    if result.results:
        lines.extend(
            [
                "| Condition | Asset | Horizon | Samples | Win Rate | Wilson 95% Low | Avg Return | Max Drawdown |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for item in result.results[:limit]:
            lines.append(
                f"| {item.condition} | {item.asset} | {item.horizon_days} | {item.samples} | "
                f"{item.win_rate:.2%} | {item.wilson_lower_95:.2%} | "
                f"{item.average_return:.2%} | {item.max_drawdown:.2%} |"
            )
    else:
        lines.append("- No pattern results available.")
    lines.extend(
        [
            "",
            "## Multiple-testing Warning",
            "- Treat any perfect or high win-rate sample as hypothesis generation, not a forecast.",
        ]
    )
    if result.errors:
        lines.extend(["", "## Data Gaps"])
        lines.extend(f"- {error}" for error in result.errors)
    return "\n".join(lines)


def pattern_results_to_csv(result: PatternReport) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "condition",
            "asset",
            "horizon_days",
            "outcome",
            "samples",
            "wins",
            "win_rate",
            "wilson_lower_95",
            "average_return",
            "max_drawdown",
        ]
    )
    for item in result.results:
        writer.writerow(
            [
                item.condition,
                item.asset,
                item.horizon_days,
                "forward_return",
                item.samples,
                item.wins,
                f"{item.win_rate:.6f}",
                f"{item.wilson_lower_95:.6f}",
                f"{item.average_return:.6f}",
                f"{item.max_drawdown:.6f}",
            ]
        )
    return output.getvalue()
