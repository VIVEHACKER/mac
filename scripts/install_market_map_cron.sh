#!/bin/bash
# Idempotent installer for the market-map lane (P0b: 카탈로그 신선도 + 페이지 자동 재생성).
#
# Schedule (KST):
#   * Sat 08:10          — catalog_refresh --mode full  (금요일 US 세션 종료 후 주 1회 풀 재수집
#                          — 조정종가 이음새 리셋. 1000+ 심볼이라 ~2-3시간 소요)
#   * Tue-Fri 08:40      — catalog_refresh --mode incremental --scope map (평일 저비용 탑업)
#   * Tue-Fri 08:55      — trader market-map 재생성 (탑업 직후, US 세션 마감 반영)
#   * Sat 11:30          — trader market-map 재생성 (풀 리프레시 종료 후 — 08시대에 돌리면
#                          풀 리프레시와 DuckDB 쓰기 락이 경합해 US 테마가 빈 칸이 된다)
#
# fail-open: 리프레시가 부분 실패해도 market-map 은 가용 데이터로 렌더되고 페이지의
# 신선도 배지가 stale 을 드러낸다. 잡을 분리한 이유 — 한쪽 실패가 다른 쪽을 못 막게.
set -euo pipefail

TRADER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MARKER_REFRESH="catalog_refresh"
MARKER_MAP="market-map"
LOG_REFRESH="out/catalog-refresh-cron.log"
LOG_MAP="out/market-map-cron.log"

mkdir -p "${TRADER_DIR}/out"

LINE1="10 8 * * 6 cd \"${TRADER_DIR}\" && .venv/bin/python -m scripts.${MARKER_REFRESH} --mode full >> ${LOG_REFRESH} 2>&1"
LINE2="40 8 * * 2-5 cd \"${TRADER_DIR}\" && .venv/bin/python -m scripts.${MARKER_REFRESH} --mode incremental --scope map >> ${LOG_REFRESH} 2>&1"
LINE3="55 8 * * 2-5 cd \"${TRADER_DIR}\" && .venv/bin/trader ${MARKER_MAP} >> ${LOG_MAP} 2>&1"
LINE4="30 11 * * 6 cd \"${TRADER_DIR}\" && .venv/bin/trader ${MARKER_MAP} >> ${LOG_MAP} 2>&1"

# 멱등 + 스케줄 갱신: 기존 market-map lane 항목을 걷어내고 현재 스케줄로 다시 깐다.
current="$(crontab -l 2>/dev/null | grep -vE "scripts.${MARKER_REFRESH}|bin/trader ${MARKER_MAP}" || true)"
printf '%s\n%s\n%s\n%s\n%s\n' "$current" "$LINE1" "$LINE2" "$LINE3" "$LINE4" | crontab -
echo "installed market-map cron (4 entries, KST):"
crontab -l | grep -E "scripts.${MARKER_REFRESH}|bin/trader ${MARKER_MAP}"
