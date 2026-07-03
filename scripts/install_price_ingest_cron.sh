#!/bin/bash
# Idempotent installer for the price-ingest cron (audit P1 operational leg).
#
# Schedule: hourly at :40 during US regular hours expressed in KST —
#   22:40, 23:40 on Mon-Fri (ET morning session) and 00:40..05:40 on Tue-Sat (ET afternoon),
# which keeps daily marks comfortably inside live-submit's 2-day freshness gate while staying
# a no-op burden when the market is closed (the ingest is cheap and idempotent).
#
# Keyless-safe: the runner falls back to yahoo EOD without ALPACA keys and switches itself to
# broker-grade IEX the moment the keys land in .env — installing this cron never requires keys.
set -euo pipefail

TRADER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MARKER="price_ingest_cron"
LOG="out/price-ingest-cron.log"

# cron's shell opens the log path before Python starts — on a fresh checkout (or a cleaned
# out/) a missing directory would fail the job before it ever ingests (codex P2).
mkdir -p "${TRADER_DIR}/out"

LINE1="40 22,23 * * 1-5 cd \"${TRADER_DIR}\" && .venv/bin/python -m scripts.${MARKER} >> ${LOG} 2>&1"
LINE2="40 0,1,2,3,4,5 * * 2-6 cd \"${TRADER_DIR}\" && .venv/bin/python -m scripts.${MARKER} >> ${LOG} 2>&1"

current="$(crontab -l 2>/dev/null || true)"
if printf '%s\n' "$current" | grep -q "$MARKER"; then
    echo "price-ingest cron already installed:"
    printf '%s\n' "$current" | grep "$MARKER"
    exit 0
fi

printf '%s\n%s\n%s\n' "$current" "$LINE1" "$LINE2" | crontab -
echo "installed price-ingest cron (2 entries, KST for US session):"
crontab -l | grep "$MARKER"
