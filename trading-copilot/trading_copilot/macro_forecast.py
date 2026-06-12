"""Next-release CPI / PPI forecaster built on the macro data layer.

Provider-agnostic: it consumes any object implementing ``MacroDataProvider``
(``FredCsvProvider`` for the US, ``EcosProvider`` for Korea), so the same
ensemble runs across markets. Methodology mirrors the Cleveland Fed inflation
nowcast philosophy — an ensemble of persistence / seasonal models plus an
optional energy bridge (oil / gasoline), weighted by an HONEST expanding-window
out-of-sample error (no look-forward weight selection).

Pure stdlib (no numpy) to honour the package's zero-dependency contract; the
linear algebra needed (tiny OLS systems) is solved with Gaussian elimination.

Point forecasts and the claimed skill come from real computation on real data.
The energy bridge uses the target month's (already-released) energy reading, so
the headline forecast is a *nowcast* rather than a pure forecast — this is
disclosed in ``SeriesForecast.notes``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from statistics import fmean, stdev

from .macro import FredSeries, MacroDataProvider, MacroObservation

MonthKey = tuple[int, int]
NAN = float("nan")


# --------------------------------------------------------------------------- #
# Monthly helpers
# --------------------------------------------------------------------------- #
def to_monthly(observations: tuple[MacroObservation, ...]) -> dict[MonthKey, float]:
    """Collapse observations to (year, month) -> value. Monthly series are 1/mo;
    higher-frequency series (daily oil, weekly gas) are averaged within the month."""
    buckets: dict[MonthKey, list[float]] = {}
    for o in observations:
        buckets.setdefault((o.observed_at.year, o.observed_at.month), []).append(o.value)
    return {k: fmean(v) for k, v in buckets.items()}


def month_add(key: MonthKey, delta: int) -> MonthKey:
    idx = key[0] * 12 + (key[1] - 1) + delta
    return idx // 12, idx % 12 + 1


def sorted_keys(monthly: dict[MonthKey, float]) -> list[MonthKey]:
    return sorted(monthly.keys())


def mom_series(monthly: dict[MonthKey, float]) -> tuple[list[MonthKey], list[float]]:
    """Month-over-month % change (x100), keeping only contiguous month pairs.
    keys[i] is the month whose MoM change is values[i]."""
    keys = sorted_keys(monthly)
    out_keys: list[MonthKey] = []
    out_vals: list[float] = []
    for i in range(1, len(keys)):
        prev, cur = keys[i - 1], keys[i]
        if month_add(prev, 1) != cur:
            continue
        out_keys.append(cur)
        out_vals.append((monthly[cur] / monthly[prev] - 1.0) * 100.0)
    return out_keys, out_vals


# --------------------------------------------------------------------------- #
# Tiny linear algebra (pure stdlib) — small, well-conditioned OLS systems only.
# --------------------------------------------------------------------------- #
def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve a @ x = b via Gaussian elimination with partial pivoting."""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ArithmeticError("singular OLS system")
        m[col], m[pivot] = m[pivot], m[col]
        inv = 1.0 / m[col][col]
        for r in range(col + 1, n):
            f = m[r][col] * inv
            if f == 0.0:
                continue
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def _ols(rows: list[list[float]], ys: list[float]) -> list[float]:
    """Least squares via normal equations (XᵀX β = Xᵀy) with a tiny ridge for
    numerical safety. Systems here are 2-4 unknowns on 18+ observations."""
    k = len(rows[0])
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for row, y in zip(rows, ys, strict=False):
        for i in range(k):
            xty[i] += row[i] * y
            for j in range(i, k):
                xtx[i][j] += row[i] * row[j]
    for i in range(k):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
    ridge = 1e-10 * (sum(xtx[i][i] for i in range(k)) / k or 1.0)
    for i in range(k):
        xtx[i][i] += ridge
    return _solve(xtx, xty)


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile, matching numpy's default method."""
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q / 100.0
    lo = math.floor(pos)
    frac = pos - lo
    if lo + 1 >= len(s):
        return s[-1]
    return s[lo] + frac * (s[lo + 1] - s[lo])


def _rmse(errors: list[float]) -> float:
    return math.sqrt(fmean(e * e for e in errors)) if errors else NAN


# --------------------------------------------------------------------------- #
# Forecast models. Each maps MoM history (up to but excluding the target month)
# to a point forecast for the target month's MoM. Bridge models additionally
# take contemporaneous energy (legitimate: the target month is already complete).
# --------------------------------------------------------------------------- #
def m_random_walk(mom: list[float]) -> float:
    return mom[-1]


def m_mean6(mom: list[float]) -> float:
    return fmean(mom[-6:])


def m_ewma(mom: list[float], alpha: float = 0.4) -> float:
    n = len(mom)
    weights = [(1 - alpha) ** (n - 1 - i) for i in range(n)]
    total = sum(weights)
    return sum(w * v for w, v in zip(weights, mom, strict=False)) / total


def m_seasonal(keys: list[MonthKey], mom: list[float], target: MonthKey) -> float | None:
    prior_year = month_add(target, -12)
    if prior_year in keys:
        return mom[keys.index(prior_year)]
    return None


def m_ar(mom: list[float], p: int = 3) -> float | None:
    if len(mom) < p + 8:
        return None
    rows, ys = [], []
    for t in range(p, len(mom)):
        rows.append([1.0, *mom[t - p : t][::-1]])
        ys.append(mom[t])
    try:
        beta = _ols(rows, ys)
    except ArithmeticError:
        return None
    feat = [1.0, *mom[-p:][::-1]]
    return sum(b * f for b, f in zip(beta, feat, strict=False))


def m_bridge(
    keys: list[MonthKey],
    mom: list[float],
    energy_mom: dict[MonthKey, float],
    core_mom: dict[MonthKey, float] | None,
    target: MonthKey,
) -> float | None:
    """OLS: headline_MoM ~ const + energy_MoM (+ core_MoM), fit on training
    months only, predicted with the target month's (released) energy reading."""
    rows, ys = [], []
    for k, v in zip(keys, mom, strict=False):
        if k not in energy_mom:
            continue
        feat = [1.0, energy_mom[k]]
        if core_mom is not None:
            if k not in core_mom:
                continue
            feat.append(core_mom[k])
        rows.append(feat)
        ys.append(v)
    if len(rows) < 18 or target not in energy_mom:
        return None
    try:
        beta = _ols(rows, ys)
    except ArithmeticError:
        return None
    feat = [1.0, energy_mom[target]]
    if core_mom is not None:
        if target not in core_mom:
            return None
        feat.append(core_mom[target])
    return sum(b * f for b, f in zip(beta, feat, strict=False))


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelScore:
    name: str
    oos_rmse: float
    mae: float
    hit_dir: float
    n: int
    forecast: float | None


@dataclass(frozen=True)
class SeriesForecast:
    label: str
    series_id: str
    target: MonthKey
    last_observed: date
    last_index: float
    last_mom: float
    last_yoy: float | None
    last_yoy_nsa: float | None
    ensemble_mom: float
    yoy: float | None
    proj_index: float
    pi80: tuple[float, float]
    pi95: tuple[float, float]
    oos_rmse: float
    rw_rmse: float
    skill_pct: float
    n_test: int
    models: tuple[ModelScore, ...]
    weights: tuple[tuple[str, float], ...]
    source: str
    notes: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# Honest expanding-window ensemble backtest
# --------------------------------------------------------------------------- #
def _evaluate(
    keys: list[MonthKey],
    mom: list[float],
    target: MonthKey,
    energy_mom: dict[MonthKey, float] | None,
    core_mom: dict[MonthKey, float] | None,
    n_test: int,
    min_train: int,
):
    def build(train_keys, train_mom, tgt):
        out = {
            "random_walk": m_random_walk(train_mom),
            "mean6": m_mean6(train_mom),
            "ewma": m_ewma(train_mom),
            "ar3": m_ar(train_mom),
        }
        seas = m_seasonal(train_keys, train_mom, tgt)
        if seas is not None:
            out["seasonal"] = seas
        if energy_mom is not None:
            br = m_bridge(train_keys, train_mom, energy_mom, None, tgt)
            if br is not None:
                out["energy_bridge"] = br
            if core_mom is not None:
                brc = m_bridge(train_keys, train_mom, energy_mom, core_mom, tgt)
                if brc is not None:
                    out["energy_core_bridge"] = brc
        return {k: v for k, v in out.items() if v is not None}

    start = max(min_train, len(mom) - n_test)
    errors: dict[str, list[float]] = {}
    dir_hits: dict[str, list[int]] = {}
    folds: list[tuple[float, dict[str, float]]] = []
    for t in range(start, len(mom)):
        tgt = keys[t]
        preds = build(keys[:t], mom[:t], tgt)
        actual, prior = mom[t], mom[t - 1]
        folds.append((actual, preds))
        for name, p in preds.items():
            errors.setdefault(name, []).append(p - actual)
            dir_hits.setdefault(name, []).append(int((p - prior) * (actual - prior) >= 0))

    live = build(keys, mom, target)
    models: list[ModelScore] = []
    for name in sorted(errors):
        e = errors[name]
        models.append(
            ModelScore(
                name=name,
                oos_rmse=_rmse(e),
                mae=fmean(abs(v) for v in e),
                hit_dir=fmean(dir_hits[name]),
                n=len(e),
                forecast=live.get(name),
            )
        )

    # Require enough backtest folds to trust a model's weight — but adapt the
    # floor to short histories (otherwise every model is dropped and the
    # ensemble degenerates to NaN, which the short-history path must support).
    max_n = max((m.n for m in models if m.forecast is not None), default=0)
    min_n = min(12, max_n) if max_n else 0
    usable_names = [m.name for m in models if m.forecast is not None and m.n >= min_n]
    # Expanding-window ensemble: each fold weighted by inverse-MSE of PAST folds.
    past_sq: dict[str, list[float]] = {n: [] for n in usable_names}
    ens_resid: list[float] = []
    for actual, preds in folds:
        avail = [n for n in usable_names if n in preds]
        if not avail:
            continue
        wt = {n: (1.0 / max(fmean(past_sq[n]), 1e-6) if past_sq[n] else 1.0) for n in avail}
        wsum = sum(wt.values())
        blend = sum(wt[n] / wsum * preds[n] for n in avail)
        ens_resid.append(blend - actual)
        for n in avail:
            past_sq[n].append((preds[n] - actual) ** 2)

    live_usable = [m for m in models if m.name in usable_names]
    raw_w = [1.0 / max(m.oos_rmse, 1e-4) ** 2 for m in live_usable]
    wsum = sum(raw_w)
    norm_w = [w / wsum for w in raw_w] if wsum else raw_w
    ens_fc = (
        sum(w * m.forecast for w, m in zip(norm_w, live_usable, strict=False))
        if live_usable
        else NAN
    )
    weights = tuple((m.name, w) for m, w in zip(live_usable, norm_w, strict=False))
    return models, ens_resid, ens_fc, weights


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def forecast_series(
    provider: MacroDataProvider,
    series_id: str,
    *,
    label: str | None = None,
    nsa_provider: MacroDataProvider | None = None,
    nsa_series_id: str | None = None,
    energy_provider: MacroDataProvider | None = None,
    energy_series_id: str | None = None,
    core_provider: MacroDataProvider | None = None,
    core_series_id: str | None = None,
    n_test: int = 48,
    min_train: int = 36,
) -> SeriesForecast:
    """Forecast the next month's MoM (SA where applicable) and reconstruct YoY
    for a single price index, with an honest out-of-sample skill estimate."""
    series: FredSeries = provider.series(series_id)
    monthly = to_monthly(series.observations)
    keys, mom = mom_series(monthly)
    if len(mom) < min_train + 1:
        # Adapt to short histories (e.g. ECOS sample key) so we still produce a result.
        min_train = max(12, len(mom) // 2)
        n_test = min(n_test, max(6, len(mom) - min_train))
    last_key = sorted_keys(monthly)[-1]
    target = month_add(last_key, 1)

    energy_mom = None
    notes: list[str] = []
    if energy_provider is not None and energy_series_id is not None:
        try:
            e_monthly = to_monthly(energy_provider.series(energy_series_id).observations)
            ek, ev = mom_series(e_monthly)
            energy_mom = dict(zip(ek, ev, strict=False))
            notes.append(
                f"energy bridge uses {energy_series_id} (target-month reading is already "
                "released -> headline is a nowcast, not a pure forecast)"
            )
        except Exception as exc:  # noqa: BLE001 - energy is optional
            notes.append(f"energy bridge skipped ({energy_series_id}: {exc})")

    core_mom = None
    if core_provider is not None and core_series_id is not None:
        try:
            ck, cv = mom_series(to_monthly(core_provider.series(core_series_id).observations))
            core_mom = dict(zip(ck, cv, strict=False))
        except Exception:  # noqa: BLE001 - core bridge is optional
            pass

    models, ens_resid, ens_fc, weights = _evaluate(
        keys, mom, target, energy_mom, core_mom, n_test, min_train
    )

    rw = next((m.oos_rmse for m in models if m.name == "random_walk"), NAN)
    ens_rmse = _rmse(ens_resid)
    skill = (1 - ens_rmse / rw) * 100.0 if rw and not math.isnan(rw) else NAN

    proj = monthly[last_key] * (1.0 + ens_fc / 100.0)
    py = month_add(target, -12)
    yoy = (proj / monthly[py] - 1.0) * 100.0 if py in monthly else None

    if len(ens_resid) >= 12:
        # ens_resid = prediction - actual, so actual = forecast - resid.
        # The interval must therefore SUBTRACT the residual quantiles (flipping
        # them): with a biased (e.g. under-forecasting) residual distribution,
        # adding them would shift the band the wrong way.
        sigma = stdev(ens_resid)
        pi80 = (ens_fc - _percentile(ens_resid, 90), ens_fc - _percentile(ens_resid, 10))
        pi95 = (ens_fc - 1.96 * sigma, ens_fc + 1.96 * sigma)
    else:
        pi80 = pi95 = (NAN, NAN)

    lpy = month_add(last_key, -12)
    last_yoy = (monthly[last_key] / monthly[lpy] - 1.0) * 100.0 if lpy in monthly else None
    last_yoy_nsa = None
    if nsa_provider is not None and nsa_series_id is not None:
        try:
            nsa_monthly = to_monthly(nsa_provider.series(nsa_series_id).observations)
            if last_key in nsa_monthly and lpy in nsa_monthly:
                last_yoy_nsa = (nsa_monthly[last_key] / nsa_monthly[lpy] - 1.0) * 100.0
        except Exception:  # noqa: BLE001
            pass

    return SeriesForecast(
        label=label or series_id,
        series_id=series_id,
        target=target,
        last_observed=date(last_key[0], last_key[1], 1),
        last_index=monthly[last_key],
        last_mom=mom[-1],
        last_yoy=last_yoy,
        last_yoy_nsa=last_yoy_nsa,
        ensemble_mom=ens_fc,
        yoy=yoy,
        proj_index=proj,
        pi80=pi80,
        pi95=pi95,
        oos_rmse=ens_rmse,
        rw_rmse=rw,
        skill_pct=skill,
        n_test=len(ens_resid),
        models=tuple(models),
        weights=weights,
        source=series.source,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
# Region presets + dashboard
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ForecastSpec:
    label: str
    series_id: str
    nsa_series_id: str | None = None
    energy_series_id: str | None = None
    core_series_id: str | None = None


US_SPECS = (
    ForecastSpec("Headline CPI", "CPIAUCSL", "CPIAUCNS", "DCOILWTICO", "CPILFESL"),
    ForecastSpec("Core CPI", "CPILFESL", "CPILFENS", None, None),
    ForecastSpec("PPI Final Demand", "PPIFIS", "WPSFD49207", "DCOILWTICO", None),
)

# Korea (Bank of Korea ECOS). Series are NSA; YoY is the headline Koreans track.
KR_SPECS = (
    ForecastSpec("소비자물가지수 (CPI)", "901Y009/0", None, None, None),
    ForecastSpec("생산자물가지수 (PPI)", "404Y014/*AA", None, None, None),
)


def forecast_dashboard(
    provider: MacroDataProvider,
    specs: tuple[ForecastSpec, ...],
    *,
    energy_provider: MacroDataProvider | None = None,
) -> tuple[SeriesForecast, ...]:
    """Run the forecaster across a region's price indices. ``energy_provider``
    (e.g. a FRED provider for oil) supplies the optional energy bridge even when
    the main provider is a different source (ECOS)."""
    out: list[SeriesForecast] = []
    for spec in specs:
        out.append(
            forecast_series(
                provider,
                spec.series_id,
                label=spec.label,
                nsa_provider=provider if spec.nsa_series_id else None,
                nsa_series_id=spec.nsa_series_id,
                energy_provider=energy_provider if spec.energy_series_id else None,
                energy_series_id=spec.energy_series_id,
                core_provider=provider if spec.core_series_id else None,
                core_series_id=spec.core_series_id,
            )
        )
    return tuple(out)


def _fmt(x: float | None) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.2f}%"


def format_forecast_report(forecasts: tuple[SeriesForecast, ...], title: str) -> str:
    lines = [
        f"# {title}",
        "",
        "Not investment advice. Statistical nowcast for research; verify against official releases.",
        "",
        "| Indicator | Target | Forecast MoM | 80% band | Implied YoY | OOS skill vs RW | n |",
        "|---|---|---:|---|---:|---:|---:|",
    ]
    for f in forecasts:
        ty, tm = f.target
        band = f"[{_fmt(f.pi80[0])}, {_fmt(f.pi80[1])}]"
        skill = "n/a" if math.isnan(f.skill_pct) else f"{f.skill_pct:+.1f}%"
        lines.append(
            f"| {f.label} ({f.series_id}) | {ty}-{tm:02d} | {_fmt(f.ensemble_mom)} | "
            f"{band} | {_fmt(f.yoy)} | {skill} | {f.n_test} |"
        )
    lines.append("")
    for f in forecasts:
        lines.append(f"## {f.label} ({f.series_id})")
        lines.append(
            f"- latest actual: {f.last_observed.isoformat()} index {f.last_index:.3f}, "
            f"MoM {_fmt(f.last_mom)}, YoY {_fmt(f.last_yoy)}"
            + (f" (official NSA {_fmt(f.last_yoy_nsa)})" if f.last_yoy_nsa is not None else "")
        )
        lines.append(
            f"- ensemble: MoM {_fmt(f.ensemble_mom)} | 95% {_fmt(f.pi95[0])}..{_fmt(f.pi95[1])} | "
            f"implied YoY {_fmt(f.yoy)}"
        )
        ranked = sorted(f.models, key=lambda m: m.oos_rmse)
        lines.append(
            "- models (OOS RMSE): "
            + ", ".join(
                f"{m.name} {m.oos_rmse:.3f}{'' if m.forecast is None else f'->{m.forecast:+.2f}'}"
                for m in ranked
            )
        )
        lines.append("- weights: " + ", ".join(f"{n}={w:.2f}" for n, w in f.weights))
        for note in f.notes:
            lines.append(f"- note: {note}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
