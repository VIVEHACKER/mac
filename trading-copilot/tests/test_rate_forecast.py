"""Tests for the policy-rate decision forecaster and its forward-OOS ledger."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from trading_copilot import rate_forecast as rf
from trading_copilot.macro import FredSeries, MacroDataError, MacroObservation


def _series(series_id: str, points: list[tuple[date, float]]) -> FredSeries:
    return FredSeries(
        series_id=series_id,
        name=series_id,
        source=f"test://{series_id}",
        observations=tuple(MacroObservation(series_id, d, v) for d, v in sorted(points)),
    )


def _daily(series_id: str, start: date, values: list[float]) -> FredSeries:
    return _series(series_id, [(start + timedelta(days=i), v) for i, v in enumerate(values)])


def _monthly(series_id: str, start: tuple[int, int], values: list[float]) -> FredSeries:
    points = []
    y, m = start
    for v in values:
        points.append((date(y, m, 1), v))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return _series(series_id, points)


class FakeProvider:
    def __init__(self, series_map: dict[str, FredSeries]):
        self.series_map = series_map
        self.calls: list[str] = []

    def series(self, series_id: str) -> FredSeries:
        self.calls.append(series_id)
        if series_id not in self.series_map:
            raise MacroDataError(f"{series_id}: not available in fake")
        return self.series_map[series_id]


# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #
def test_next_meeting_picks_first_on_or_after_today(tmp_path):
    override = str(tmp_path / "none.json")
    assert rf.next_meeting("us", date(2026, 6, 12), override) == date(2026, 6, 17)
    assert rf.next_meeting("us", date(2026, 6, 17), override) == date(2026, 6, 17)
    assert rf.next_meeting("us", date(2026, 6, 18), override) == date(2026, 7, 29)
    assert rf.next_meeting("kr", date(2026, 6, 12), override) == date(2026, 7, 16)


def test_next_meeting_exhausted_calendar_raises(tmp_path):
    override = str(tmp_path / "none.json")
    with pytest.raises(MacroDataError, match="calendar exhausted"):
        rf.next_meeting("us", date(2030, 1, 1), override)


def test_meetings_override_file_extends_builtin(tmp_path):
    override = tmp_path / "rate_meetings.json"
    override.write_text('{"us": ["2027-01-27"]}', encoding="utf-8")
    assert rf.next_meeting("us", date(2026, 12, 10), str(override)) == date(2027, 1, 27)


# --------------------------------------------------------------------------- #
# Probability mapping
# --------------------------------------------------------------------------- #
def test_decision_probs_sum_to_one_and_monotonic_in_pressure():
    low = rf.decision_probs(-1.0, streak=0)
    mid = rf.decision_probs(0.0, streak=0)
    high = rf.decision_probs(1.0, streak=0)
    for probs in (low, mid, high):
        assert abs(sum(probs.values()) - 1.0) < 0.01
    assert low["cut"] > mid["cut"] > high["cut"]
    assert low["hike"] < mid["hike"] < high["hike"]
    assert mid["hold"] == max(mid.values())  # zero pressure -> hold is modal


def test_decision_probs_hold_streak_raises_hold_probability():
    short = rf.decision_probs(0.5, streak=0)
    long = rf.decision_probs(0.5, streak=6)
    assert long["hold"] > short["hold"]


def test_infer_upper_from_effective_snaps_to_band():
    assert rf.infer_upper_from_effective(3.63) == 3.75
    assert rf.infer_upper_from_effective(5.33) == 5.50
    assert rf.infer_upper_from_effective(0.08) == 0.25


# --------------------------------------------------------------------------- #
# Decision detection + streak
# --------------------------------------------------------------------------- #
def test_detect_decision_daily_hike_and_hold():
    meeting = date(2026, 6, 17)
    hike = _daily("DFEDTARU", meeting - timedelta(days=10), [3.75] * 10 + [4.00] * 10)
    assert rf.detect_decision(hike, meeting) == ("hike", 25.0)
    hold = _daily("DFEDTARU", meeting - timedelta(days=10), [3.75] * 20)
    assert rf.detect_decision(hold, meeting) == ("hold", 0.0)


def test_detect_decision_daily_not_yet_observable():
    meeting = date(2026, 6, 17)
    series = _daily("DFEDTARU", meeting - timedelta(days=10), [3.75] * 10)
    assert rf.detect_decision(series, meeting) is None


def test_detect_decision_monthly_cut():
    # Cut at the 2026-05-28 meeting: April avg 2.75 -> June avg 2.50.
    series = _monthly("KR", (2026, 1), [2.75, 2.75, 2.75, 2.75, 2.6, 2.50])
    assert rf.detect_decision(series, date(2026, 5, 28)) == ("cut", -25.0)


def test_detect_decision_eop_does_not_leak_adjacent_meeting():
    # Codex P1 regression: end-of-period series, BOK 4/10 hold + 5/28 cut.
    # The May print reflects the 5/28 cut; the 4/10 meeting must read April.
    series = _monthly("KR", (2026, 1), [2.75, 2.75, 2.75, 2.75, 2.50, 2.50])
    assert rf.detect_decision(series, date(2026, 4, 10), "eop") == ("hold", 0.0)
    assert rf.detect_decision(series, date(2026, 5, 28), "eop") == ("cut", -25.0)
    # Old "avg" window (m+1 vs m-1) would have mis-attributed the cut to 4/10.
    assert rf.detect_decision(series, date(2026, 4, 10), "avg") == ("cut", -25.0)


def test_detect_decision_eop_observable_one_month_earlier():
    # eop only needs the meeting month's own print.
    series = _monthly("KR", (2026, 1), [2.75, 2.75, 2.75, 2.75, 2.50])
    assert rf.detect_decision(series, date(2026, 5, 28), "eop") == ("cut", -25.0)
    assert rf.detect_decision(series, date(2026, 5, 28), "avg") is None  # needs June


def test_detect_decision_rejects_unknown_monthly_kind():
    series = _monthly("KR", (2026, 1), [2.75, 2.75, 2.75])
    with pytest.raises(ValueError, match="monthly_kind"):
        rf.detect_decision(series, date(2026, 2, 26), "median")


def test_seeded_calendar_lets_streak_see_pre_2026_holds():
    # Codex P2 (inertia): with 2024-2025 meetings seeded, the streak counts real
    # prior holds. KR base rate: 2.75 until the 2025-05-29 cut, 2.50 (hold) after.
    points = []
    y, m = 2024, 1
    while (y, m) <= (2026, 5):
        points.append((date(y, m, 1), 2.75 if (y, m) < (2025, 5) else 2.50))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    series = _series("722Y001/0101000", points)
    past = rf.past_meetings("kr", date(2026, 6, 13))
    # 8 holds after the 2025-05-29 cut: 2025-07/08/10/11 + 2026-01/02/04/05.
    assert rf.hold_streak(series, past, monthly_kind="eop") == 8


def test_hold_streak_counts_back_to_last_change():
    # Monthly policy rate: change between 2025-12 and 2026-01, holds after.
    series = _monthly("KR", (2025, 11), [3.0, 3.0, 2.75, 2.75, 2.75, 2.75, 2.75, 2.75])
    meetings = (date(2026, 1, 15), date(2026, 2, 26), date(2026, 4, 10), date(2026, 5, 28))
    # 5-28 hold, 4-10 hold, 2-26 hold, then 1-15 is the change (cut) -> streak 3.
    assert rf.hold_streak(series, meetings) == 3
    # Drop the June print: 5-28 becomes unobservable and is skipped -> still 3? No —
    # 4-10 hold, 2-26 hold, 1-15 change -> streak 2.
    truncated = _monthly("KR", (2025, 11), [3.0, 3.0, 2.75, 2.75, 2.75, 2.75, 2.75])
    assert rf.hold_streak(truncated, meetings) == 2


# --------------------------------------------------------------------------- #
# Signal collection
# --------------------------------------------------------------------------- #
def _us_provider(**overrides) -> FakeProvider:
    today = date(2026, 6, 12)
    cpi = _monthly("CPIAUCSL", (2024, 1), [100 + i * 0.35 for i in range(29)])  # ~4% YoY
    series_map = {
        "DFEDTARU": _daily("DFEDTARU", today - timedelta(days=200), [3.75] * 200),
        "EFFR": _daily("EFFR", today - timedelta(days=30), [3.63] * 30),
        "DTB3": _daily("DTB3", today - timedelta(days=30), [3.68] * 30),
        "CPIAUCSL": cpi,
        "UNRATE": _monthly("UNRATE", (2025, 6), [4.3] * 12),
        "FEDFUNDS": _monthly("FEDFUNDS", (2025, 6), [3.63] * 12),
    }
    series_map.update(overrides)
    return FakeProvider(series_map)


def test_collect_us_signals_full_stack():
    signals = rf.collect_us_signals(_us_provider(), date(2026, 6, 12))
    assert signals.meeting == date(2026, 6, 17)
    assert signals.current_rate == 3.75
    assert signals.market_spread_bp == pytest.approx(5.0)
    assert signals.missing == ()
    assert abs(sum(signals.probs.values()) - 1.0) < 0.01
    assert signals.modal in ("cut", "hold", "hike")


def test_collect_us_signals_survives_blocked_daily_series():
    provider = _us_provider()
    del provider.series_map["DFEDTARU"], provider.series_map["EFFR"], provider.series_map["DTB3"]
    signals = rf.collect_us_signals(provider, date(2026, 6, 12))
    assert signals.current_rate == 3.75  # inferred from FEDFUNDS 3.63
    assert "market" in signals.missing
    assert any("unavailable" in n for n in signals.notes)


def test_collect_us_signals_all_signals_missing_refuses():
    provider = _us_provider()
    for sid in ("DTB3", "EFFR", "CPIAUCSL"):
        del provider.series_map[sid]
    with pytest.raises(MacroDataError, match="refusing to record"):
        rf.collect_us_signals(provider, date(2026, 6, 12))


def test_collect_kr_signals():
    provider = FakeProvider(
        {
            "722Y001/0101000": _monthly("722Y001/0101000", (2025, 1), [2.75] * 5 + [2.50] * 13),
            "901Y009/0": _monthly("901Y009/0", (2024, 1), [100 + i * 0.25 for i in range(30)]),
        }
    )
    market = FakeProvider(
        {"817Y002/010150000": _daily("817Y002/010150000", date(2026, 6, 1), [2.6] * 10)}
    )
    signals = rf.collect_kr_signals(provider, date(2026, 6, 12), market_provider=market)
    assert signals.meeting == date(2026, 7, 16)
    assert signals.current_rate == 2.50
    assert signals.market_spread_bp == pytest.approx(10.0)
    assert market.calls == ["817Y002/010150000"]  # daily provider serves the market leg


def test_collect_kr_signals_falls_back_to_bare_stat_code():
    provider = FakeProvider(
        {
            "722Y001": _monthly("722Y001", (2025, 1), [2.50] * 18),
            "901Y009/0": _monthly("901Y009/0", (2024, 1), [100 + i * 0.25 for i in range(30)]),
        }
    )
    signals = rf.collect_kr_signals(provider, date(2026, 6, 12))
    assert signals.current_rate == 2.50
    assert "market" in signals.missing


# --------------------------------------------------------------------------- #
# Ledger: record / score / summary
# --------------------------------------------------------------------------- #
def _fake_signals(region="us", meeting=date(2026, 6, 17)) -> rf.RateSignals:
    probs = rf.decision_probs(0.6, streak=3)
    return rf.RateSignals(
        region=region,
        meeting=meeting,
        current_rate=3.75,
        market_spread_bp=5.0,
        market_units=0.1,
        taylor_rule_rate=6.2,
        taylor_units=0.99,
        streak=3,
        pressure=0.6,
        probs=probs,
        modal=max(probs, key=lambda k: probs[k]),
        missing=(),
        notes=(),
        sources=("test://",),
    )


def test_record_rate_forecast_refuses_meeting_day_or_later(tmp_path):
    path = str(tmp_path / "rate_ledger.jsonl")
    with pytest.raises(MacroDataError, match="pollute the forward-OOS ledger"):
        rf.record_rate_forecast(_fake_signals(), recorded_at=date(2026, 6, 17), path=path)
    with pytest.raises(MacroDataError, match="pollute the forward-OOS ledger"):
        rf.record_rate_forecast(_fake_signals(), recorded_at=date(2026, 6, 18), path=path)
    assert rf.read_rate_ledger(path) == []


def test_already_recorded_helper(tmp_path):
    path = str(tmp_path / "rate_ledger.jsonl")
    assert not rf.already_recorded("us", date(2026, 6, 17), path)
    rf.record_rate_forecast(_fake_signals(), recorded_at=date(2026, 6, 12), path=path)
    assert rf.already_recorded("us", date(2026, 6, 17), path)
    assert not rf.already_recorded("kr", date(2026, 6, 17), path)
    assert not rf.already_recorded("us", date(2026, 7, 29), path)


def test_record_rate_forecast_idempotent_and_force(tmp_path):
    path = str(tmp_path / "rate_ledger.jsonl")
    first = rf.record_rate_forecast(_fake_signals(), recorded_at=date(2026, 6, 12), path=path)
    assert first is not None and first["status"] == "pending"
    dup = rf.record_rate_forecast(_fake_signals(), recorded_at=date(2026, 6, 13), path=path)
    assert dup is None
    forced = rf.record_rate_forecast(
        _fake_signals(), recorded_at=date(2026, 6, 14), path=path, force=True
    )
    assert forced is not None
    ledger = rf.read_rate_ledger(path)
    assert len([e for e in ledger if e["kind"] == "rate_forecast"]) == 2
    latest = rf._latest_forecasts(ledger)[("us", "2026-06-17")]
    assert latest["recorded_at"] == "2026-06-14"


def test_score_rate_pending_hold_hit(tmp_path):
    path = str(tmp_path / "rate_ledger.jsonl")
    rf.record_rate_forecast(_fake_signals(), recorded_at=date(2026, 6, 12), path=path)
    meeting = date(2026, 6, 17)
    provider = FakeProvider(
        {"DFEDTARU": _daily("DFEDTARU", meeting - timedelta(days=10), [3.75] * 30)}
    )
    scored = rf.score_rate_pending({"us": provider}, scored_at=date(2026, 6, 22), path=path)
    assert len(scored) == 1
    row = scored[0]
    assert row["actual"] == "hold"
    assert row["modal_hit"] == (row["modal"] == "hold")
    assert 0.0 <= row["brier"] <= 2.0
    # Idempotent: a second run scores nothing new.
    assert rf.score_rate_pending({"us": provider}, scored_at=date(2026, 6, 23), path=path) == []


def test_score_rate_pending_waits_for_data(tmp_path):
    path = str(tmp_path / "rate_ledger.jsonl")
    rf.record_rate_forecast(_fake_signals(), recorded_at=date(2026, 6, 12), path=path)
    meeting = date(2026, 6, 17)
    provider = FakeProvider(
        {"DFEDTARU": _daily("DFEDTARU", meeting - timedelta(days=10), [3.75] * 10)}
    )
    assert rf.score_rate_pending({"us": provider}, scored_at=date(2026, 6, 18), path=path) == []
    summary = rf.rate_ledger_summary(path)
    assert "Pending" in summary and "2026-06-17" in summary


def test_score_rate_pending_brier_for_miss(tmp_path):
    path = str(tmp_path / "rate_ledger.jsonl")
    rf.record_rate_forecast(_fake_signals(), recorded_at=date(2026, 6, 12), path=path)
    meeting = date(2026, 6, 17)
    provider = FakeProvider(
        {"DFEDTARU": _daily("DFEDTARU", meeting - timedelta(days=10), [3.75] * 10 + [3.50] * 20)}
    )
    scored = rf.score_rate_pending({"us": provider}, scored_at=date(2026, 6, 25), path=path)
    assert scored[0]["actual"] == "cut"
    assert scored[0]["actual_change_bp"] == -25.0
    expected_brier = sum(
        (scored[0]["probs"][a] - (1.0 if a == "cut" else 0.0)) ** 2 for a in rf.ACTIONS
    )
    assert scored[0]["brier"] == pytest.approx(expected_brier, abs=1e-3)


def test_rate_ledger_summary_aggregates(tmp_path):
    path = str(tmp_path / "rate_ledger.jsonl")
    rf.record_rate_forecast(_fake_signals(), recorded_at=date(2026, 6, 12), path=path)
    meeting = date(2026, 6, 17)
    provider = FakeProvider(
        {"DFEDTARU": _daily("DFEDTARU", meeting - timedelta(days=10), [3.75] * 30)}
    )
    rf.score_rate_pending({"us": provider}, scored_at=date(2026, 6, 22), path=path)
    summary = rf.rate_ledger_summary(path)
    assert "modal hit rate" in summary
    assert "mean Brier" in summary


# --------------------------------------------------------------------------- #
# Report formatting
# --------------------------------------------------------------------------- #
def test_format_rate_forecast_report_contains_probs_and_basis():
    report = rf.format_rate_forecast_report(_fake_signals())
    assert "P(cut)" in report and "P(hold)" in report and "P(hike)" in report
    assert "2026-06-17" in report
    assert "Basis" in report
    assert "priors" in report  # honesty disclaimer present
