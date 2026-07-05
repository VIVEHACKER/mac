"""`python -m scripts.live_setup_check` — the concrete "where am I / what's my exact next
command" readout for going live. Turns the LIVE_OPERATIONS checklist into a live status:
key state, forward-OOS progress (X/6 closed periods), and the single next action.
"""

from __future__ import annotations

import json

from scripts.live_setup_check import (
    REQUIRED_OOS_PERIODS,
    key_state,
    ledger_progress,
    next_actions,
)

# ---------------------------------------------------------------- key_state


def test_key_state_missing_when_unset_or_blank() -> None:
    assert key_state("", "") == "missing"
    assert key_state("   ", "\t") == "missing"


def test_key_state_placeholder_for_template_or_short() -> None:
    assert key_state("your_key_here", "your_secret_here") == "placeholder"
    assert key_state("PKSHORT", "s") == "placeholder"  # too short to be a real Alpaca key


def test_key_state_present_for_real_shaped_keys() -> None:
    assert key_state("PK" + "A" * 18, "S" * 40) == "present"


# ---------------------------------------------------------------- ledger_progress


def test_ledger_progress_counts_closed_periods_as_pairs(tmp_path) -> None:
    ledger = tmp_path / "paper-oos-ledger-x.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps({"rebal_date": d, "strategy_id": "x"})
            for d in ("2026-06-05", "2026-07-06", "2026-08-04")
        )
        + "\n",
        encoding="utf-8",
    )
    entries, closed = ledger_progress(ledger)
    assert entries == 3
    assert closed == 2  # 3 entries -> 2 realised holding periods


def test_ledger_progress_single_entry_is_zero_closed(tmp_path) -> None:
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps({"rebal_date": "2026-06-05"}) + "\n", encoding="utf-8")
    assert ledger_progress(ledger) == (1, 0)


def test_ledger_progress_missing_file_is_zero(tmp_path) -> None:
    assert ledger_progress(tmp_path / "nope.jsonl") == (0, 0)


# ---------------------------------------------------------------- next_actions


def test_next_actions_missing_keys_points_to_key_issuance() -> None:
    actions = next_actions(key="missing", closed=0)
    joined = " ".join(actions).lower()
    assert "alpaca.markets" in joined and ".env" in joined


def test_next_actions_present_keys_but_time_gate_open() -> None:
    actions = next_actions(key="present", closed=2)
    joined = " ".join(actions).lower()
    # Keys done -> the remaining blocker is the time gate; guidance is "keep the paper loop
    # running", NOT "go live".
    assert "rebalance-plan" in joined
    assert f"{2}/{REQUIRED_OOS_PERIODS}" in " ".join(actions)
    assert "alpaca-live" not in joined


def test_next_actions_time_gate_met_offers_live_review() -> None:
    actions = next_actions(key="present", closed=REQUIRED_OOS_PERIODS)
    joined = " ".join(actions).lower()
    assert "alpaca-live" in joined and "live-readiness" in joined
