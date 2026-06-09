"""Fail-closed reproducibility gate for validation / backtest runs.

The deploy-candidate validation scripts (``aqr_ideal_walkforward``,
``compounder_oos_validation`` …) accept content-pinned snapshots (``--prices`` /
``--snapshot`` / ``--price-snapshot``) but SILENTLY fall back to a live yfinance
download / the live catalog when those are omitted. An unpinned run is not
byte-reproducible: yfinance revises adjusted closes, so the forward-return ICs
drift run-to-run (observed +0.031..+0.070 for the *same* fundamentals snapshot —
larger than the gross signal itself). A verdict written from drifting inputs is
not a verdict.

Setting ``TRADER_REQUIRE_PINNED`` turns any such fallback into a hard error, so an
"official" run — the deploy-candidate cadence, anything that updates a
research-registry record or a published verdict — cannot quietly emit a
non-reproducible number. Exploratory runs leave the variable unset and keep the
old, convenient live-download behaviour.

This is a *gate*, not a data source: it only decides whether an unpinned source is
permitted. Pinning itself lives in ``data.price_snapshot`` /
``data.fundamentals_snapshot``.
"""

from __future__ import annotations

import os

ENV_VAR = "TRADER_REQUIRE_PINNED"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def require_pinned() -> bool:
    """True when ``TRADER_REQUIRE_PINNED`` requests fail-closed pinned-input enforcement.

    Accepts ``1/true/yes/on`` (case- and whitespace-insensitive) as on; everything
    else — including unset, ``0``, ``false`` — is off.
    """
    return os.environ.get(ENV_VAR, "").strip().lower() in _TRUTHY


def assert_pinned(source_pinned: bool, what: str) -> None:
    """Raise ``SystemExit`` if strict mode is on and ``what`` uses a live/unpinned source.

    No-op when the source is already pinned, or when strict mode is off (an
    exploratory run). ``what`` is a short human label for the offending input, e.g.
    ``"prices (--prices omitted → live yfinance)"``.
    """
    if source_pinned or not require_pinned():
        return
    raise SystemExit(
        f"{ENV_VAR} is set but {what} would use a LIVE, unpinned source "
        f"(non-reproducible — yfinance revises closes, so the verdict would drift "
        f"run-to-run). Pass the content-pinned snapshot, or unset {ENV_VAR} for an "
        f"exploratory run."
    )
