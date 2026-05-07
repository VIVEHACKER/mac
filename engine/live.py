from __future__ import annotations

import os


class LiveTradingBlockedError(RuntimeError):
    pass


def assert_live_trading_enabled() -> None:
    enabled = os.getenv("LIVE_TRADING_ENABLED", "").lower() == "true"
    acknowledged = os.getenv("LIVE_TRADING_ACK_RISK", "").lower() == "true"
    if not enabled or not acknowledged:
        raise LiveTradingBlockedError(
            "Live trading is disabled. Set LIVE_TRADING_ENABLED=true and "
            "LIVE_TRADING_ACK_RISK=true only after paper verification and key hardening."
        )
