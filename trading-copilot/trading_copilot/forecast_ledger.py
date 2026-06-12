"""Forward-OOS ledger for CPI/PPI forecasts.

Records each pre-release forecast as a pending entry, then scores it against the
official print once the data is released — turning the forecaster into a live,
auditable track record (the same discipline as the AQR forward-OOS paper ledger).

Storage is an append-only JSONL file (default ``out/forecast_ledger.jsonl``):
each line is either a ``"forecast"`` snapshot or a ``"score"`` once the actual
arrives. Scoring re-fetches the series through the provider; if the target month
is now present, it computes the realised MoM / YoY and the forecast error.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date

from .macro import MacroDataProvider
from .macro_forecast import SeriesForecast, month_add, mom_series, to_monthly

DEFAULT_LEDGER = "out/forecast_ledger.jsonl"


def _target_str(target: tuple[int, int]) -> str:
    return f"{target[0]}-{target[1]:02d}"


def record_forecasts(
    forecasts: tuple[SeriesForecast, ...],
    *,
    region: str,
    recorded_at: date,
    path: str = DEFAULT_LEDGER,
) -> list[dict]:
    """Append a pending forecast snapshot per series (skipping duplicates of an
    already-recorded (region, series, target) so reruns are idempotent).
    Returns the written rows."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    existing = {
        (e["region"], e["series_id"], e["target"])
        for e in read_ledger(path)
        if e.get("kind") == "forecast"
    }
    rows: list[dict] = []
    for f in forecasts:
        if (region, f.series_id, _target_str(f.target)) in existing:
            continue
        if f.ensemble_mom != f.ensemble_mom:  # NaN forecast — nothing to record
            continue
        rows.append(
            {
                "kind": "forecast",
                "recorded_at": recorded_at.isoformat(),
                "region": region,
                "series_id": f.series_id,
                "label": f.label,
                "target": _target_str(f.target),
                "last_observed": f.last_observed.isoformat(),
                "forecast_mom": round(f.ensemble_mom, 4),
                "forecast_yoy": None if f.yoy is None else round(f.yoy, 4),
                "pi80": [round(f.pi80[0], 4), round(f.pi80[1], 4)],
                "oos_rmse": round(f.oos_rmse, 4),
                "skill_pct": None if f.skill_pct != f.skill_pct else round(f.skill_pct, 2),
                "source": f.source,
                "status": "pending",
            }
        )
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def read_ledger(path: str = DEFAULT_LEDGER) -> list[dict]:
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _already_scored(ledger: list[dict], region: str, series_id: str, target: str) -> bool:
    return any(
        e.get("kind") == "score"
        and e.get("region") == region
        and e.get("series_id") == series_id
        and e.get("target") == target
        for e in ledger
    )


def score_pending(
    providers: dict[str, MacroDataProvider],
    *,
    scored_at: date,
    path: str = DEFAULT_LEDGER,
) -> list[dict]:
    """For every pending forecast whose target month has since been released,
    compute the realised MoM / error and append a score row. Returns new scores."""
    ledger = read_ledger(path)
    new_scores: list[dict] = []
    scored_in_run: set[tuple[str, str, str]] = set()
    for entry in ledger:
        if entry.get("kind") != "forecast":
            continue
        region, series_id, target = entry["region"], entry["series_id"], entry["target"]
        if (region, series_id, target) in scored_in_run or _already_scored(
            ledger, region, series_id, target
        ):
            continue
        provider = providers.get(region)
        if provider is None:
            continue
        try:
            monthly = to_monthly(provider.series(series_id).observations)
        except Exception:  # noqa: BLE001 - data not yet released / fetch error
            continue
        ty, tm = (int(p) for p in target.split("-"))
        tkey = (ty, tm)
        if tkey not in monthly:
            continue  # not released yet
        prev = month_add(tkey, -1)
        if prev not in monthly:
            continue
        actual_mom = (monthly[tkey] / monthly[prev] - 1.0) * 100.0
        py = month_add(tkey, -12)
        actual_yoy = (monthly[tkey] / monthly[py] - 1.0) * 100.0 if py in monthly else None
        err = actual_mom - entry["forecast_mom"]
        lo, hi = entry["pi80"]
        score = {
            "kind": "score",
            "scored_at": scored_at.isoformat(),
            "region": region,
            "series_id": series_id,
            "label": entry["label"],
            "target": target,
            "forecast_mom": entry["forecast_mom"],
            "actual_mom": round(actual_mom, 4),
            "error_mom": round(err, 4),
            "abs_error_mom": round(abs(err), 4),
            "in_pi80": bool(lo <= actual_mom <= hi),
            "forecast_yoy": entry.get("forecast_yoy"),
            "actual_yoy": None if actual_yoy is None else round(actual_yoy, 4),
        }
        new_scores.append(score)
        scored_in_run.add((region, series_id, target))
    if new_scores:
        with open(path, "a", encoding="utf-8") as fh:
            for row in new_scores:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return new_scores


def ledger_summary(path: str = DEFAULT_LEDGER) -> str:
    ledger = read_ledger(path)
    forecasts = [e for e in ledger if e.get("kind") == "forecast"]
    scores = [e for e in ledger if e.get("kind") == "score"]
    lines = [
        "# CPI/PPI Forecast Forward-OOS Ledger",
        "",
        f"- forecasts recorded: {len(forecasts)}",
        f"- scored (released): {len(scores)}",
    ]
    if scores:
        mae = sum(s["abs_error_mom"] for s in scores) / len(scores)
        hit = sum(1 for s in scores if s["in_pi80"]) / len(scores) * 100.0
        lines.append(f"- mean abs MoM error: {mae:.3f} pp")
        lines.append(f"- 80% interval coverage: {hit:.0f}%")
        lines.append("")
        lines.append("| Target | Series | Forecast MoM | Actual MoM | Error | in80 |")
        lines.append("|---|---|---:|---:|---:|:--:|")
        for s in sorted(scores, key=lambda x: (x["target"], x["series_id"])):
            lines.append(
                f"| {s['target']} | {s['label']} | {s['forecast_mom']:+.2f}% | "
                f"{s['actual_mom']:+.2f}% | {s['error_mom']:+.2f}pp | "
                f"{'Y' if s['in_pi80'] else 'N'} |"
            )
    pending = [
        f
        for f in forecasts
        if not _already_scored(ledger, f["region"], f["series_id"], f["target"])
    ]
    if pending:
        lines.append("")
        lines.append("## Pending (awaiting release)")
        for f in pending:
            yoy = f["forecast_yoy"]
            yoy_str = "n/a" if yoy is None else f"{yoy:+.2f}%"
            lines.append(
                f"- {f['target']} {f['label']} ({f['region']}): "
                f"MoM {f['forecast_mom']:+.2f}%, YoY {yoy_str}"
            )
    return "\n".join(lines) + "\n"


__all__ = [
    "record_forecasts",
    "read_ledger",
    "score_pending",
    "ledger_summary",
    "asdict",
]
