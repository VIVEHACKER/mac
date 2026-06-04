from __future__ import annotations

from datetime import date

from risk.equity_track import EquityTrackStore


def test_first_update_sets_reference_and_peak_to_current(tmp_path) -> None:
    store = EquityTrackStore(tmp_path / "equity.json")
    refs = store.update(10_000.0, today=date(2026, 6, 4))
    assert refs.reference_equity == 10_000.0
    assert refs.peak_equity == 10_000.0


def test_peak_rises_with_equity_and_holds_on_drawdown(tmp_path) -> None:
    store = EquityTrackStore(tmp_path / "equity.json")
    store.update(10_000.0, today=date(2026, 6, 4))
    up = store.update(12_000.0, today=date(2026, 6, 4))
    assert up.peak_equity == 12_000.0
    # equity falls intraday: peak persists, same-day reference unchanged
    down = store.update(9_000.0, today=date(2026, 6, 4))
    assert down.peak_equity == 12_000.0
    assert down.reference_equity == 10_000.0  # start-of-day, not the new low


def test_new_day_resets_reference_but_keeps_peak(tmp_path) -> None:
    store = EquityTrackStore(tmp_path / "equity.json")
    store.update(10_000.0, today=date(2026, 6, 4))
    store.update(12_000.0, today=date(2026, 6, 4))
    next_day = store.update(11_000.0, today=date(2026, 6, 5))
    assert next_day.reference_equity == 12_000.0  # prior session close, not the new intraday tick
    assert next_day.peak_equity == 12_000.0  # all-time peak persists across days


def test_new_day_baseline_is_prior_close_so_overnight_loss_is_caught(tmp_path) -> None:
    """An overnight / at-open gap-down must be measured from the prior session close so the daily
    latch can fire on the first cycle of the day, not be reset below the breach (Codex P1)."""
    store = EquityTrackStore(tmp_path / "equity.json")
    store.update(10_000.0, today=date(2026, 6, 4))  # prior session closes at 10_000
    refs = store.update(9_500.0, today=date(2026, 6, 5))  # opens down 5%
    assert refs.reference_equity == 10_000.0  # NOT 9_500 -> (10_000-9_500)/10_000 = 5% > 2% latch


def test_persists_across_instances(tmp_path) -> None:
    path = tmp_path / "equity.json"
    EquityTrackStore(path).update(12_000.0, today=date(2026, 6, 4))
    reopened = EquityTrackStore(path).update(9_000.0, today=date(2026, 6, 4))
    assert reopened.peak_equity == 12_000.0
    assert reopened.reference_equity == 12_000.0


def test_rejects_non_positive_equity(tmp_path) -> None:
    store = EquityTrackStore(tmp_path / "equity.json")
    try:
        store.update(0.0, today=date(2026, 6, 4))
    except ValueError as exc:
        assert "positive" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for non-positive equity")


def test_broker_prior_close_is_the_authoritative_daily_baseline(tmp_path) -> None:
    """On first deploy / new account (no local state), the broker's prior-session close — not the
    bootstrapped current equity — must be the daily baseline, so a gap-down open is caught (P1)."""
    store = EquityTrackStore(tmp_path / "equity.json")
    # no prior local state; broker says yesterday closed at 10_000; account opened down 5%
    refs = store.update(9_500.0, today=date(2026, 6, 5), prior_close=10_000.0)
    assert refs.reference_equity == 10_000.0  # NOT 9_500 -> daily latch sees the 5% gap
    assert refs.peak_equity == 10_000.0  # peak floored by prior close, arming the sleeve latch


def test_prior_close_overrides_local_history_same_session(tmp_path) -> None:
    """Broker prior-close is stable intraday and overrides local last-observed each cycle."""
    store = EquityTrackStore(tmp_path / "equity.json")
    store.update(10_500.0, today=date(2026, 6, 5), prior_close=10_000.0)
    refs = store.update(9_800.0, today=date(2026, 6, 5), prior_close=10_000.0)
    assert refs.reference_equity == 10_000.0
    assert refs.peak_equity == 10_500.0  # high-water from the intraday observation


def test_out_of_band_higher_peak_is_not_regressed(tmp_path) -> None:
    """A lower-equity write must not clobber a higher peak recorded out-of-band by another writer:
    _write re-reads the on-disk peak under an exclusive lock and keeps the max (Codex P2)."""
    import json as _json

    path = tmp_path / "equity.json"
    store = EquityTrackStore(path)
    store.update(10_000.0, today=date(2026, 6, 4))
    data = _json.loads(path.read_text(encoding="utf-8"))
    data["peak"] = 15_000.0  # another process recorded a new all-time high
    path.write_text(_json.dumps(data), encoding="utf-8")
    refs = store.update(9_000.0, today=date(2026, 6, 4))  # our lower-equity cycle
    assert refs.peak_equity == 15_000.0


def test_returns_merged_peak_when_bumped_during_locked_write(tmp_path) -> None:
    """If another writer raises the peak between our read and our locked write, update must RETURN
    the merged (higher) peak so this cycle's kill-switch uses it, not the stale pre-merge value
    (Codex P1). Simulated by bumping the peak on the in-_write merge re-read."""
    path = tmp_path / "equity.json"

    class _Racing(EquityTrackStore):
        reads = 0

        def _read(self) -> dict:
            data = super()._read()
            type(self).reads += 1
            if type(self).reads == 2:  # the merge re-read inside _write
                data = {**data, "peak": 16_000.0}
            return data

    refs = _Racing(path).update(8_000.0, today=date(2026, 6, 4))
    assert refs.peak_equity == 16_000.0  # merged, not the stale 8_000-based peak
