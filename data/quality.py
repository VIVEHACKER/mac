from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from data.catalog import MarketDataCatalog

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


@dataclass(frozen=True)
class DataQualityIssue:
    severity: str
    area: str
    item: str
    message: str


def evaluate_catalog_quality(
    catalog: MarketDataCatalog,
    *,
    as_of: date,
    required_macro: tuple[str, ...] = ("DGS10", "VIXCLS"),
    required_flows: tuple[tuple[str, str], ...] = (),
    required_prices: tuple[tuple[str, str], ...] = (),
    max_macro_age_days: int = 14,
    max_flow_age_days: int = 7,
    max_price_age_days: int = 5,
    live_mode: bool = False,
) -> list[DataQualityIssue]:
    catalog.initialize()
    issues: list[DataQualityIssue] = []
    issues.extend(
        _price_issues(
            catalog,
            as_of=as_of,
            required_prices=required_prices,
            max_age_days=max_price_age_days,
            live_mode=live_mode,
        )
    )
    issues.extend(
        _macro_issues(
            catalog,
            as_of=as_of,
            required_macro=required_macro,
            max_age_days=max_macro_age_days,
        )
    )
    issues.extend(
        _flow_issues(
            catalog,
            as_of=as_of,
            max_age_days=max_flow_age_days,
            required_flows=required_flows,
        )
    )
    return sorted(
        issues,
        key=lambda item: (SEVERITY_ORDER.get(item.severity, 99), item.area, item.item),
    )


def _price_issues(
    catalog: MarketDataCatalog,
    *,
    as_of: date,
    required_prices: tuple[tuple[str, str], ...],
    max_age_days: int,
    live_mode: bool,
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    for symbol, market in required_prices:
        rows = catalog.get_bars(symbol, market=market)
        item = f"{market.lower()}:{symbol.upper()}"
        if not rows:
            issues.append(DataQualityIssue("error", "price", item, "required price series missing"))
            continue
        latest = rows[-1]
        age_days = (as_of - latest.ts).days
        if age_days > max_age_days:
            issues.append(
                DataQualityIssue(
                    "error",
                    "price",
                    item,
                    f"latest bar is stale: {latest.ts} ({age_days} days old)",
                )
            )
        if live_mode:
            if not latest.source:
                issues.append(
                    DataQualityIssue("warn", "price", item, "live policy price source is missing")
                )
            elif _research_price_source(latest.source):
                issues.append(
                    DataQualityIssue(
                        "warn",
                        "price",
                        item,
                        f"live policy is using research-grade price source: {latest.source}",
                    )
                )
    return issues


def _research_price_source(source: str) -> bool:
    lowered = source.lower()
    if any(marker in lowered for marker in ("yahoo", "manual", "fixture")):
        return True
    tokens = {token for token in lowered.replace("-", ":").replace("_", ":").split(":") if token}
    return "test" in tokens


def _macro_issues(
    catalog: MarketDataCatalog,
    *,
    as_of: date,
    required_macro: tuple[str, ...],
    max_age_days: int,
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    with catalog._connect() as con:
        rows = con.execute(
            """
            SELECT series_id, max(asof_date)
            FROM macro_series
            GROUP BY series_id
            """
        ).fetchall()
        latest_by_series = {row[0]: row[1] for row in rows}
        source_rows = con.execute(
            """
            SELECT series_id, asof_date, source
            FROM macro_series
            QUALIFY row_number() OVER (PARTITION BY series_id ORDER BY asof_date DESC) = 1
            """
        ).fetchall()
    latest_source = {row[0]: row[2] for row in source_rows}
    for series_id in required_macro:
        latest = latest_by_series.get(series_id)
        if latest is None:
            issues.append(DataQualityIssue("error", "macro", series_id, "required series missing"))
            continue
        age_days = (as_of - latest).days
        if age_days > max_age_days:
            issues.append(
                DataQualityIssue(
                    "error",
                    "macro",
                    series_id,
                    f"latest observation is stale: {latest} ({age_days} days old)",
                )
            )
        source = latest_source.get(series_id, "")
        if source.startswith("yahoo-fallback:"):
            issues.append(
                DataQualityIssue(
                    "warn",
                    "macro",
                    series_id,
                    f"using Yahoo fallback instead of official FRED source: {source}",
                )
            )
        elif source == "manual":
            issues.append(
                DataQualityIssue("warn", "macro", series_id, "latest observation is manual input")
            )
    return issues


def _flow_issues(
    catalog: MarketDataCatalog,
    *,
    as_of: date,
    max_age_days: int,
    required_flows: tuple[tuple[str, str], ...],
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    with catalog._connect() as con:
        rows = con.execute(
            """
            SELECT symbol, market, max(ts)
            FROM flows
            GROUP BY symbol, market
            """
        ).fetchall()
        latest_rows = con.execute(
            """
            SELECT symbol, market, ts, investor, value_kind, confidence, source
            FROM flows
            QUALIFY row_number() OVER (
                PARTITION BY symbol, market, investor ORDER BY ts DESC
            ) = 1
            """
        ).fetchall()
        estimate_rows = con.execute(
            """
            SELECT symbol, market, ts, investor, value_kind, confidence, source
            FROM flow_estimates
            QUALIFY row_number() OVER (
                PARTITION BY symbol, market, investor ORDER BY ts DESC
            ) = 1
            """
        ).fetchall()
    latest_by_flow = {(symbol, market): latest for symbol, market, latest in rows}
    for symbol, market in required_flows:
        if (symbol.upper(), market.lower()) not in latest_by_flow:
            issues.append(
                DataQualityIssue(
                    "error",
                    "flow",
                    f"{market.lower()}:{symbol.upper()}",
                    "required reported flow missing",
                )
            )
    for symbol, market, latest in rows:
        age_days = (as_of - latest).days
        if age_days > max_age_days:
            issues.append(
                DataQualityIssue(
                    "error",
                    "flow",
                    f"{market}:{symbol}",
                    f"latest flow is stale: {latest} ({age_days} days old)",
                )
            )
    for symbol, market, ts, investor, value_kind, confidence, source in latest_rows:
        item = f"{market}:{symbol}:{investor}"
        if value_kind != "reported_value":
            issues.append(
                DataQualityIssue(
                    "warn",
                    "flow",
                    item,
                    f"{ts} uses {value_kind}; source={source}",
                )
            )
        if confidence != "high":
            issues.append(
                DataQualityIssue(
                    "warn",
                    "flow",
                    item,
                    f"confidence is {confidence}; source={source}",
                )
            )
    for symbol, market, ts, investor, value_kind, confidence, source in estimate_rows:
        issues.append(
            DataQualityIssue(
                "info",
                "flow_estimate",
                f"{market}:{symbol}:{investor}",
                f"{ts} quarantined {value_kind} ({confidence}); source={source}",
            )
        )
    return issues


def format_quality_report(issues: list[DataQualityIssue]) -> str:
    if not issues:
        return "Data quality: OK"
    lines = [
        "# Data Quality",
        "",
        "| Severity | Area | Item | Message |",
        "|---|---|---|---|",
    ]
    for issue in issues:
        lines.append(f"| {issue.severity} | {issue.area} | {issue.item} | {issue.message} |")
    return "\n".join(lines)
