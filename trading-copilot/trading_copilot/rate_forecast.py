"""Policy-rate decision forecaster (FOMC / Bank of Korea) with a forward-OOS ledger.

Before each scheduled meeting it records a {cut, hold, hike} probability forecast;
after the decision it scores the forecast against the realised move (modal hit +
multiclass Brier) — the same pre-register-then-score discipline as the CPI/PPI
``forecast_ledger``. This is a *direction* forecaster: 50bp+ moves count as the
same class as 25bp moves, and unscheduled emergency meetings are out of scope.

Three independent signals feed a transparent softmax over {cut, hold, hike}:

- ``market``  — short-rate spread vs the policy rate (US: DTB3 − EFFR; KR: CD91
  − base rate), read as expected 25bp moves priced over ~2 meetings.
- ``taylor``  — Taylor(1993)-style gap between the implied rule rate (using the
  latest released CPI YoY and unemployment) and the current policy rate, damped
  because central banks move toward the rule gradually, not in one meeting.
- ``inertia`` — consecutive-hold streak detected from the policy-rate series
  itself, expressed as a hold bonus in the softmax (most meetings are holds).

The softmax constants are documented priors, NOT fitted parameters — the scored
ledger exists precisely to test them out of sample before anyone trusts them.

Pure stdlib; providers are the existing ``FredCsvProvider`` / ``EcosProvider``.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import date, timedelta

from .macro import FredSeries, MacroDataError, MacroDataProvider
from .macro_forecast import MonthKey, month_add, to_monthly

DEFAULT_RATE_LEDGER = "out/rate_ledger.jsonl"
MEETINGS_OVERRIDE_PATH = "out/rate_meetings.json"

# Decision-announcement dates (FOMC: second day of the two-day meeting).
# Verified 2026-06-13 against federalreserve.gov/monetarypolicy/fomccalendars.htm
# and bok.or.kr (통화정책방향 결정회의; 3/6/9/12월은 금융안정회의라 금리 결정 없음).
# 2024-2025 history is seeded so hold_streak() counts real prior holds against the
# policy-rate series (the streak feeds the inertia bonus; a 2026-only table would
# truncate long hold runs, e.g. BOK's 8-meeting hold since the 2025-05 cut).
# Extend future years via MEETINGS_OVERRIDE_PATH: {"us": ["YYYY-MM-DD", ...], ...}
BUILTIN_MEETINGS: dict[str, tuple[date, ...]] = {
    "us": (
        date(2024, 1, 31),
        date(2024, 3, 20),
        date(2024, 5, 1),
        date(2024, 6, 12),
        date(2024, 7, 31),
        date(2024, 9, 18),
        date(2024, 11, 7),
        date(2024, 12, 18),
        date(2025, 1, 29),
        date(2025, 3, 19),
        date(2025, 5, 7),
        date(2025, 6, 18),
        date(2025, 7, 30),
        date(2025, 9, 17),
        date(2025, 10, 29),
        date(2025, 12, 10),
        date(2026, 1, 28),
        date(2026, 3, 18),
        date(2026, 4, 29),
        date(2026, 6, 17),
        date(2026, 7, 29),
        date(2026, 9, 16),
        date(2026, 10, 28),
        date(2026, 12, 9),
    ),
    "kr": (
        date(2024, 1, 11),
        date(2024, 2, 22),
        date(2024, 4, 12),
        date(2024, 5, 23),
        date(2024, 7, 11),
        date(2024, 8, 22),
        date(2024, 10, 11),
        date(2024, 11, 28),
        date(2025, 1, 16),
        date(2025, 2, 25),
        date(2025, 4, 17),
        date(2025, 5, 29),
        date(2025, 7, 10),
        date(2025, 8, 28),
        date(2025, 10, 23),
        date(2025, 11, 27),
        date(2026, 1, 15),
        date(2026, 2, 26),
        date(2026, 4, 10),
        date(2026, 5, 28),
        date(2026, 7, 16),
        date(2026, 8, 27),
        date(2026, 10, 22),
        date(2026, 11, 26),
    ),
}

# Series ids per region. KR ECOS item codes have fallbacks because the sample
# catalogue occasionally re-keys items; first one that returns rows wins.
US_TARGET_UPPER = "DFEDTARU"
US_EFFECTIVE_DAILY = "EFFR"
US_EFFECTIVE_MONTHLY = "FEDFUNDS"
US_TBILL_3M = "DTB3"
US_CPI = "CPIAUCSL"
US_UNRATE = "UNRATE"
KR_POLICY_CANDIDATES = ("722Y001/0101000", "722Y001")
# KORIBOR 3M (daily, ECOS 817Y002/010150000): a 3-month term rate spans the next
# ~2 meetings — the cleanest keyless market-expectation proxy ECOS offers.
KR_MARKET_CANDIDATES = ("817Y002/010150000",)
KR_CPI = "901Y009/0"

# Softmax priors (documented, unfitted — see module docstring).
PRESSURE_SCALE = 0.8  # score per expected 25bp move
HOLD_BASE = 1.0  # base hold bonus (holds dominate meeting outcomes)
HOLD_STREAK_BONUS = 0.15  # extra hold bonus per consecutive hold (capped)
HOLD_STREAK_CAP = 6
TAYLOR_GRADUALISM = 0.33  # fraction of the (clamped) Taylor gap acted on per meeting
TAYLOR_CLAMP_UNITS = 3.0  # clamp Taylor gap to +/- 3 moves of 25bp
MARKET_CLAMP_UNITS = 2.0
MARKET_MEETINGS_PRICED = 2.0  # a 3-month rate spans roughly two meetings
SIGNAL_WEIGHTS = {"market": 0.5, "taylor": 0.3}  # inertia enters as the hold bonus
NEUTRAL_REAL_RATE = {"us": 1.0, "kr": 0.5}
INFLATION_TARGET = 2.0
US_NAIRU = 4.2
HOLD_THRESHOLD_BP = 12.5  # |change| below this scores as a hold

ACTIONS = ("cut", "hold", "hike")


# --------------------------------------------------------------------------- #
# Meeting calendar
# --------------------------------------------------------------------------- #
def load_meetings(region: str, override_path: str = MEETINGS_OVERRIDE_PATH) -> tuple[date, ...]:
    """Built-in calendar, unioned with the optional user-maintained override file."""
    region = region.lower()
    if region not in BUILTIN_MEETINGS:
        raise ValueError(f"unknown region {region!r} (use 'us' or 'kr')")
    meetings = set(BUILTIN_MEETINGS[region])
    if os.path.exists(override_path):
        with open(override_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        for raw in payload.get(region, []):
            meetings.add(date.fromisoformat(raw))
    return tuple(sorted(meetings))


def next_meeting(region: str, today: date, override_path: str = MEETINGS_OVERRIDE_PATH) -> date:
    """Earliest scheduled decision date on/after ``today`` (decision-day inclusive)."""
    for meeting in load_meetings(region, override_path):
        if meeting >= today:
            return meeting
    raise MacroDataError(
        f"{region}: rate-meeting calendar exhausted (last known "
        f"{load_meetings(region, override_path)[-1].isoformat()}) — add future dates to "
        f"{override_path}"
    )


def past_meetings(
    region: str, today: date, override_path: str = MEETINGS_OVERRIDE_PATH
) -> tuple[date, ...]:
    return tuple(m for m in load_meetings(region, override_path) if m < today)


# --------------------------------------------------------------------------- #
# Series helpers
# --------------------------------------------------------------------------- #
def _last_value(series: FredSeries) -> tuple[date, float]:
    obs = series.observations[-1]
    return obs.observed_at, obs.value


def _series_any(provider: MacroDataProvider, candidates: tuple[str, ...]) -> FredSeries:
    errors: list[str] = []
    for sid in candidates:
        try:
            return provider.series(sid)
        except Exception as exc:  # noqa: BLE001 - try the next candidate id
            errors.append(f"{sid}: {exc}")
    raise MacroDataError("; ".join(errors))


def _is_daily(series: FredSeries) -> bool:
    """More than 3 observations inside the latest observed month => daily-ish."""
    if len(series.observations) < 4:
        return False
    last = series.observations[-1].observed_at
    n = sum(
        1
        for o in series.observations
        if o.observed_at.year == last.year and o.observed_at.month == last.month
    )
    if n > 3:
        return True
    # The latest month may be young (e.g. the 2nd); check the prior month too.
    prev = (last.replace(day=1) - timedelta(days=1)).replace(day=1)
    n_prev = sum(
        1
        for o in series.observations
        if o.observed_at.year == prev.year and o.observed_at.month == prev.month
    )
    return n_prev > 3


def infer_upper_from_effective(effective: float) -> float:
    """Snap a fed-funds effective print to the 25bp target-band upper bound."""
    return round((effective + 0.125) * 4) / 4


def latest_yoy(monthly: dict[MonthKey, float]) -> float | None:
    keys = sorted(monthly)
    if not keys:
        return None
    last = keys[-1]
    prior = month_add(last, -12)
    if prior not in monthly:
        return None
    return (monthly[last] / monthly[prior] - 1.0) * 100.0


def detect_decision(
    series: FredSeries, meeting: date, monthly_kind: str = "avg"
) -> tuple[str, float] | None:
    """Classify the move at ``meeting`` from the policy-rate series, or ``None``
    if the post-meeting reading is not observable yet.

    Daily series: last print on/before meeting-1d vs first print on/after
    meeting+3d. Monthly series read according to ``monthly_kind``:

    - ``"eop"`` (end-of-period, e.g. ECOS base rate): the meeting month's own
      print is already post-decision, so compare month m vs m-1. Never reads
      month m+1 — an adjacent meeting there (BOK 4/10 + 5/28) cannot leak in.
    - ``"avg"`` (month average, e.g. FEDFUNDS): month m is a mixed reading, so
      compare m+1 vs m-1. An adjacent meeting late in m+1 contributes only its
      last few days to that month's average (<5bp even for a 50bp move on the
      FOMC/BOK calendars, where consecutive meetings are 4+ weeks apart) and
      stays under HOLD_THRESHOLD_BP.
    """
    if _is_daily(series):
        before = [o for o in series.observations if o.observed_at <= meeting - timedelta(days=1)]
        after = [o for o in series.observations if o.observed_at >= meeting + timedelta(days=3)]
        if not before or not after:
            return None
        diff = after[0].value - before[-1].value
    else:
        if monthly_kind not in ("avg", "eop"):
            raise ValueError(f"monthly_kind must be 'avg' or 'eop', got {monthly_kind!r}")
        monthly = to_monthly(series.observations)
        mkey: MonthKey = (meeting.year, meeting.month)
        prev = month_add(mkey, -1)
        nxt = mkey if monthly_kind == "eop" else month_add(mkey, 1)
        if prev not in monthly or nxt not in monthly:
            return None
        diff = monthly[nxt] - monthly[prev]
    bp = diff * 100.0
    if abs(bp) < HOLD_THRESHOLD_BP:
        return "hold", 0.0
    return ("hike" if bp > 0 else "cut"), round(bp, 1)


def hold_streak(
    series: FredSeries, meetings: tuple[date, ...], monthly_kind: str = "avg"
) -> int | None:
    """Consecutive holds counting back from the most recent scoreable meeting.
    Meetings whose outcome is not yet observable in the series are skipped."""
    streak, seen = 0, False
    for meeting in reversed(meetings):
        decision = detect_decision(series, meeting, monthly_kind)
        if decision is None:
            continue
        seen = True
        if decision[0] == "hold":
            streak += 1
        else:
            break
    return streak if seen else None


# --------------------------------------------------------------------------- #
# Signals -> probabilities
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RateSignals:
    region: str
    meeting: date
    current_rate: float  # US: target-band upper; KR: base rate (%)
    market_spread_bp: float | None
    market_units: float | None
    taylor_rule_rate: float | None
    taylor_units: float | None
    streak: int | None
    pressure: float
    probs: dict[str, float]
    modal: str
    missing: tuple[str, ...]
    notes: tuple[str, ...]
    sources: tuple[str, ...]


def _clamp(value: float, bound: float) -> float:
    return max(-bound, min(bound, value))


def decision_probs(pressure: float, streak: int | None) -> dict[str, float]:
    """Softmax over {cut, hold, hike}: pressure tilts the hike/cut scores, the
    hold streak raises the hold score. Monotonic and fully auditable."""
    s_cut = -PRESSURE_SCALE * pressure
    s_hike = PRESSURE_SCALE * pressure
    s_hold = HOLD_BASE + HOLD_STREAK_BONUS * min(streak or 0, HOLD_STREAK_CAP)
    exps = {"cut": math.exp(s_cut), "hold": math.exp(s_hold), "hike": math.exp(s_hike)}
    total = sum(exps.values())
    return {k: round(v / total, 4) for k, v in exps.items()}


def _blend_pressure(
    market_units: float | None, taylor_units: float | None
) -> tuple[float, tuple[str, ...]]:
    parts: dict[str, float] = {}
    if market_units is not None:
        parts["market"] = market_units
    if taylor_units is not None:
        parts["taylor"] = taylor_units
    if not parts:
        raise MacroDataError(
            "all rate signals unavailable (market + taylor) — refusing to record a forecast"
        )
    wsum = sum(SIGNAL_WEIGHTS[name] for name in parts)
    pressure = sum(SIGNAL_WEIGHTS[name] * units for name, units in parts.items()) / wsum
    missing = tuple(name for name in SIGNAL_WEIGHTS if name not in parts)
    return pressure, missing


def _taylor_units(
    cpi_yoy: float | None, unemployment: float | None, *, region: str, policy_mid: float
) -> tuple[float | None, float | None]:
    if cpi_yoy is None:
        return None, None
    rule = NEUTRAL_REAL_RATE[region] + cpi_yoy + 0.5 * (cpi_yoy - INFLATION_TARGET)
    if unemployment is not None:
        rule += 0.5 * (US_NAIRU - unemployment)
    gap_units = _clamp((rule - policy_mid) / 0.25, TAYLOR_CLAMP_UNITS)
    return rule, gap_units * TAYLOR_GRADUALISM


def collect_us_signals(provider: MacroDataProvider, today: date) -> RateSignals:
    meeting = next_meeting("us", today)
    notes: list[str] = []
    sources: list[str] = []

    # Current target upper — DFEDTARU, else inferred from the effective rate.
    policy_series: FredSeries | None = None
    try:
        policy_series = provider.series(US_TARGET_UPPER)
        _, upper = _last_value(policy_series)
        sources.append(policy_series.source)
    except Exception as exc:  # noqa: BLE001 - WAF-blocked FRED daily endpoints
        notes.append(f"{US_TARGET_UPPER} unavailable ({exc}); upper inferred from effective rate")
        eff_series = provider.series(US_EFFECTIVE_MONTHLY)
        _, eff = _last_value(eff_series)
        upper = infer_upper_from_effective(eff)
        sources.append(eff_series.source)

    # Market signal: 3m bill vs effective funds rate.
    market_spread_bp = market_units = None
    try:
        _, tbill = _last_value(provider.series(US_TBILL_3M))
        try:
            _, eff = _last_value(provider.series(US_EFFECTIVE_DAILY))
        except Exception:  # noqa: BLE001 - daily EFFR often WAF-blocked
            _, eff = _last_value(provider.series(US_EFFECTIVE_MONTHLY))
        market_spread_bp = (tbill - eff) * 100.0
        market_units = _clamp(market_spread_bp / 25.0 / MARKET_MEETINGS_PRICED, MARKET_CLAMP_UNITS)
    except Exception as exc:  # noqa: BLE001 - record with the remaining signals
        notes.append(f"market signal unavailable ({exc})")

    # Taylor signal: latest released CPI YoY + unemployment gap.
    cpi_yoy = unemployment = None
    try:
        cpi_yoy = latest_yoy(to_monthly(provider.series(US_CPI).observations))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"CPI unavailable for Taylor signal ({exc})")
    try:
        _, unemployment = _last_value(provider.series(US_UNRATE))
    except Exception as exc:  # noqa: BLE001 - inflation-only Taylor still works
        notes.append(f"UNRATE unavailable ({exc}); Taylor uses inflation gap only")
    rule, taylor_units = _taylor_units(cpi_yoy, unemployment, region="us", policy_mid=upper - 0.125)

    streak = None
    if policy_series is not None:
        streak = hold_streak(policy_series, past_meetings("us", today))
    if streak is None:
        try:
            streak = hold_streak(provider.series(US_EFFECTIVE_MONTHLY), past_meetings("us", today))
            notes.append("hold streak inferred from monthly effective rate")
        except Exception:  # noqa: BLE001 - streak stays unknown
            pass

    pressure, missing = _blend_pressure(market_units, taylor_units)
    probs = decision_probs(pressure, streak)
    return RateSignals(
        region="us",
        meeting=meeting,
        current_rate=upper,
        market_spread_bp=market_spread_bp,
        market_units=market_units,
        taylor_rule_rate=rule,
        taylor_units=taylor_units,
        streak=streak,
        pressure=pressure,
        probs=probs,
        modal=max(probs, key=lambda k: probs[k]),
        missing=missing,
        notes=tuple(notes),
        sources=tuple(sources),
    )


def collect_kr_signals(
    provider: MacroDataProvider,
    today: date,
    market_provider: MacroDataProvider | None = None,
) -> RateSignals:
    """``market_provider`` should serve DAILY series (KORIBOR); the main
    ``provider`` serves the monthly policy-rate and CPI series."""
    meeting = next_meeting("kr", today)
    notes: list[str] = []

    policy_series = _series_any(provider, KR_POLICY_CANDIDATES)
    _, rate = _last_value(policy_series)
    sources = [policy_series.source]

    market_spread_bp = market_units = None
    try:
        _, koribor = _last_value(_series_any(market_provider or provider, KR_MARKET_CANDIDATES))
        market_spread_bp = (koribor - rate) * 100.0
        market_units = _clamp(market_spread_bp / 25.0 / MARKET_MEETINGS_PRICED, MARKET_CLAMP_UNITS)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"market signal unavailable ({exc})")

    cpi_yoy = None
    try:
        cpi_yoy = latest_yoy(to_monthly(provider.series(KR_CPI).observations))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"CPI unavailable for Taylor signal ({exc})")
    rule, taylor_units = _taylor_units(cpi_yoy, None, region="kr", policy_mid=rate)
    if cpi_yoy is not None:
        notes.append("KR Taylor uses inflation gap only (no unemployment term)")

    streak = hold_streak(policy_series, past_meetings("kr", today), monthly_kind="eop")

    pressure, missing = _blend_pressure(market_units, taylor_units)
    probs = decision_probs(pressure, streak)
    return RateSignals(
        region="kr",
        meeting=meeting,
        current_rate=rate,
        market_spread_bp=market_spread_bp,
        market_units=market_units,
        taylor_rule_rate=rule,
        taylor_units=taylor_units,
        streak=streak,
        pressure=pressure,
        probs=probs,
        modal=max(probs, key=lambda k: probs[k]),
        missing=missing,
        notes=tuple(notes),
        sources=tuple(sources),
    )


def collect_signals(
    region: str, providers: dict[str, MacroDataProvider], today: date
) -> RateSignals:
    region = region.lower()
    if region == "us":
        return collect_us_signals(providers["us"], today)
    if region in ("kr", "korea"):
        return collect_kr_signals(
            providers["kr"], today, market_provider=providers.get("kr_market")
        )
    raise ValueError(f"unknown region {region!r} (use 'us' or 'kr')")


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #
def read_rate_ledger(path: str = DEFAULT_RATE_LEDGER) -> list[dict]:
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _latest_forecasts(ledger: list[dict]) -> dict[tuple[str, str], dict]:
    """Last rate_forecast row per (region, meeting) — later rows supersede."""
    out: dict[tuple[str, str], dict] = {}
    for entry in ledger:
        if entry.get("kind") == "rate_forecast":
            out[(entry["region"], entry["meeting"])] = entry
    return out


def _scored_keys(ledger: list[dict]) -> set[tuple[str, str]]:
    return {(e["region"], e["meeting"]) for e in ledger if e.get("kind") == "rate_score"}


def already_recorded(region: str, meeting: date, path: str = DEFAULT_RATE_LEDGER) -> bool:
    """True when (region, meeting) already has a pending forecast or a score —
    lets callers skip signal collection entirely on idempotent reruns."""
    ledger = read_rate_ledger(path)
    key = (region.lower(), meeting.isoformat())
    return key in _latest_forecasts(ledger) or key in _scored_keys(ledger)


def record_rate_forecast(
    signals: RateSignals,
    *,
    recorded_at: date,
    path: str = DEFAULT_RATE_LEDGER,
    force: bool = False,
) -> dict | None:
    """Append a pending decision forecast; idempotent per (region, meeting).
    ``force=True`` appends a superseding row (readers take the latest)."""
    if signals.meeting <= recorded_at:
        # Date-granularity ledger cannot prove a meeting-day entry predates the
        # announcement — forward-OOS integrity requires strictly-prior recording.
        raise MacroDataError(
            f"{signals.region}: recording on/after meeting day "
            f"({signals.meeting.isoformat()} <= {recorded_at.isoformat()}) would "
            "pollute the forward-OOS ledger"
        )
    ledger = read_rate_ledger(path)
    key = (signals.region, signals.meeting.isoformat())
    if key in _scored_keys(ledger):
        return None
    if not force and key in _latest_forecasts(ledger):
        return None
    row = {
        "kind": "rate_forecast",
        "recorded_at": recorded_at.isoformat(),
        "region": signals.region,
        "meeting": signals.meeting.isoformat(),
        "current_rate": round(signals.current_rate, 4),
        "probs": signals.probs,
        "modal": signals.modal,
        "basis": {
            "market_spread_bp": None
            if signals.market_spread_bp is None
            else round(signals.market_spread_bp, 1),
            "market_units": None
            if signals.market_units is None
            else round(signals.market_units, 3),
            "taylor_rule_rate": None
            if signals.taylor_rule_rate is None
            else round(signals.taylor_rule_rate, 2),
            "taylor_units": None
            if signals.taylor_units is None
            else round(signals.taylor_units, 3),
            "hold_streak": signals.streak,
            "pressure": round(signals.pressure, 3),
            "missing": list(signals.missing),
        },
        "notes": list(signals.notes),
        "superseded": force,
        "status": "pending",
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _policy_series(region: str, provider: MacroDataProvider) -> FredSeries:
    if region == "us":
        try:
            return provider.series(US_TARGET_UPPER)
        except Exception:  # noqa: BLE001 - fall back to the effective rate
            return provider.series(US_EFFECTIVE_MONTHLY)
    return _series_any(provider, KR_POLICY_CANDIDATES)


def score_rate_pending(
    providers: dict[str, MacroDataProvider],
    *,
    scored_at: date,
    path: str = DEFAULT_RATE_LEDGER,
) -> list[dict]:
    """Score every pending forecast whose meeting has passed and whose outcome
    is observable in the policy-rate series. Multiclass Brier in [0, 2]."""
    ledger = read_rate_ledger(path)
    scored = _scored_keys(ledger)
    new_rows: list[dict] = []
    series_cache: dict[str, FredSeries] = {}
    for key, entry in sorted(_latest_forecasts(ledger).items()):
        if key in scored:
            continue
        region, meeting_str = key
        meeting = date.fromisoformat(meeting_str)
        if meeting >= scored_at:
            continue
        provider = providers.get(region)
        if provider is None:
            continue
        if region not in series_cache:
            try:
                series_cache[region] = _policy_series(region, provider)
            except Exception:  # noqa: BLE001 - retry on the next run
                continue
        # US monthly fallback (FEDFUNDS) is a month-average; KR ECOS base rate
        # is an end-of-period print. Daily series ignore the kind.
        decision = detect_decision(
            series_cache[region], meeting, "avg" if region == "us" else "eop"
        )
        if decision is None:
            continue  # post-meeting print not out yet — stays pending
        actual, change_bp = decision
        probs = entry["probs"]
        brier = sum((probs.get(a, 0.0) - (1.0 if a == actual else 0.0)) ** 2 for a in ACTIONS)
        new_rows.append(
            {
                "kind": "rate_score",
                "scored_at": scored_at.isoformat(),
                "region": region,
                "meeting": meeting_str,
                "actual": actual,
                "actual_change_bp": change_bp,
                "modal": entry["modal"],
                "modal_hit": entry["modal"] == actual,
                "prob_actual": probs.get(actual),
                "brier": round(brier, 4),
                "probs": probs,
            }
        )
    if new_rows:
        with open(path, "a", encoding="utf-8") as fh:
            for row in new_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return new_rows


def rate_ledger_summary(path: str = DEFAULT_RATE_LEDGER) -> str:
    ledger = read_rate_ledger(path)
    forecasts = _latest_forecasts(ledger)
    scores = [e for e in ledger if e.get("kind") == "rate_score"]
    lines = [
        "# Policy Rate Decision Forward-OOS Ledger",
        "",
        f"- decisions forecast: {len(forecasts)}",
        f"- scored (announced): {len(scores)}",
    ]
    if scores:
        hit = sum(1 for s in scores if s["modal_hit"]) / len(scores) * 100.0
        brier = sum(s["brier"] for s in scores) / len(scores)
        lines.append(f"- modal hit rate: {hit:.0f}%")
        lines.append(f"- mean Brier (0 best, 2 worst): {brier:.3f}")
        lines.append("")
        lines.append("| Meeting | Region | Modal | Actual | Hit | P(actual) | Brier |")
        lines.append("|---|---|---|---|:--:|---:|---:|")
        for s in sorted(scores, key=lambda x: (x["meeting"], x["region"])):
            lines.append(
                f"| {s['meeting']} | {s['region']} | {s['modal']} | {s['actual']} "
                f"({s['actual_change_bp']:+.0f}bp) | {'Y' if s['modal_hit'] else 'N'} | "
                f"{s['prob_actual']:.2f} | {s['brier']:.3f} |"
            )
    scored = _scored_keys(ledger)
    pending = [f for k, f in sorted(forecasts.items()) if k not in scored]
    if pending:
        lines.append("")
        lines.append("## Pending (awaiting decision)")
        for f in pending:
            probs = f["probs"]
            lines.append(
                f"- {f['meeting']} {f['region']}: cut {probs['cut']:.2f} / "
                f"hold {probs['hold']:.2f} / hike {probs['hike']:.2f} "
                f"(modal {f['modal']}, recorded {f['recorded_at']})"
            )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _fmt_opt(value: float | None, fmt: str, na: str = "n/a") -> str:
    return na if value is None else format(value, fmt)


def format_rate_forecast_report(signals: RateSignals) -> str:
    region_label = "FOMC" if signals.region == "us" else "한국은행 금통위"
    rate_label = "target upper" if signals.region == "us" else "base rate"
    lines = [
        f"# {region_label} Rate Decision Forecast — meeting {signals.meeting.isoformat()}",
        "",
        "Not investment advice. Direction-only forecast; softmax constants are priors,",
        "not fitted parameters — judge them by the scored ledger, not by confidence.",
        "",
        f"- current {rate_label}: {signals.current_rate:.2f}%",
        f"- P(cut) {signals.probs['cut']:.2f} | P(hold) {signals.probs['hold']:.2f} | "
        f"P(hike) {signals.probs['hike']:.2f}  -> modal **{signals.modal}**",
        "",
        "## Basis",
        f"- market: spread {_fmt_opt(signals.market_spread_bp, '+.1f')}bp "
        f"-> {_fmt_opt(signals.market_units, '+.3f')} moves/meeting "
        f"(weight {SIGNAL_WEIGHTS['market']})",
        f"- taylor: rule rate {_fmt_opt(signals.taylor_rule_rate, '.2f')}% "
        f"-> {_fmt_opt(signals.taylor_units, '+.3f')} moves/meeting "
        f"(weight {SIGNAL_WEIGHTS['taylor']}, gradualism {TAYLOR_GRADUALISM})",
        f"- inertia: hold streak {signals.streak if signals.streak is not None else 'n/a'} "
        f"-> hold bonus {HOLD_BASE + HOLD_STREAK_BONUS * min(signals.streak or 0, HOLD_STREAK_CAP):.2f}",
        f"- blended pressure: {signals.pressure:+.3f} (in 25bp moves)",
    ]
    if signals.missing:
        lines.append(f"- missing signals: {', '.join(signals.missing)}")
    for note in signals.notes:
        lines.append(f"- note: {note}")
    return "\n".join(lines) + "\n"


__all__ = [
    "RateSignals",
    "collect_signals",
    "collect_us_signals",
    "collect_kr_signals",
    "decision_probs",
    "detect_decision",
    "hold_streak",
    "infer_upper_from_effective",
    "load_meetings",
    "next_meeting",
    "record_rate_forecast",
    "score_rate_pending",
    "read_rate_ledger",
    "rate_ledger_summary",
    "format_rate_forecast_report",
    "DEFAULT_RATE_LEDGER",
]
