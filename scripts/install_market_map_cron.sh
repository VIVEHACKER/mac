#!/bin/bash
# Idempotent installer for the market-map lane (P0b: 카탈로그 신선도 + 페이지 자동 재생성).
#
# Schedule (KST):
#   * Sat 08:10          — full US 리프레시 → KR 유니버스 재검증/적재 → map 재생성을 한 줄에
#                          '`;`' 로 직렬 체인. 같은 DuckDB 에 쓰는 잡들의 병렬/경합을 원천 차단하고,
#                          map 은 두 리프레시가 끝난 뒤에만 돈다(빈 섹션 발행 방지). `;` 를 쓰는
#                          이유 — US 리프레시가 실패 임계(exit 2)로 죽어도 KR(pykrx, yahoo 무관)
#                          은 계속 돌아야 하므로 성공 게이팅(`&&`)은 금물.
#   * Tue-Fri 08:40      — catalog_refresh --mode incremental --scope map (평일 저비용 US 탑업)
#   * Tue-Fri 08:55      — trader market-map 재생성 (탑업 직후, US 세션 마감 반영)
#
# fail-open: 리프레시가 부분 실패해도 market-map 은 가용 데이터로 렌더되고 페이지의
# 신선도 배지가 stale 을 드러낸다. KR 카탈로그는 주 1회(토) 갱신(평일 incremental 은 US 만).
set -euo pipefail

TRADER_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MARKER_REFRESH="catalog_refresh"
MARKER_KR="kr_universe_ingest"
MARKER_MAP="market-map"
LOG_REFRESH="out/catalog-refresh-cron.log"
LOG_KR="out/kr-universe-cron.log"
LOG_MAP="out/market-map-cron.log"

mkdir -p "${TRADER_DIR}/out"

# 토요일 한 줄에 full US → KR → map 을 '`;`' 로 직렬 체인 (병렬 쓰기 경합 차단 + map 은 맨 뒤).
# '`;`' 는 앞 잡의 실패와 무관하게 다음 잡을 실행 — US(yahoo) 장애가 KR(pykrx)/map 을 막지 않게.
LINE1="10 8 * * 6 cd \"${TRADER_DIR}\" && { .venv/bin/python -m scripts.${MARKER_REFRESH} --mode full >> ${LOG_REFRESH} 2>&1 ; .venv/bin/python -m scripts.${MARKER_KR} >> ${LOG_KR} 2>&1 ; .venv/bin/trader ${MARKER_MAP} >> ${LOG_MAP} 2>&1 ; }"
LINE2="40 8 * * 2-5 cd \"${TRADER_DIR}\" && .venv/bin/python -m scripts.${MARKER_REFRESH} --mode incremental --scope map >> ${LOG_REFRESH} 2>&1"
LINE3="55 8 * * 2-5 cd \"${TRADER_DIR}\" && .venv/bin/trader ${MARKER_MAP} >> ${LOG_MAP} 2>&1"

# 멱등 + 스케줄 갱신: 기존 market-map lane 항목을 걷어내고 현재 스케줄로 다시 깐다.
current="$(crontab -l 2>/dev/null | grep -vE "scripts.${MARKER_REFRESH}|scripts.${MARKER_KR}|bin/trader ${MARKER_MAP}" || true)"
printf '%s\n%s\n%s\n%s\n' "$current" "$LINE1" "$LINE2" "$LINE3" | crontab -
echo "installed market-map cron (3 entries, KST — 토요일 full;KR;map 직렬 체인):"
crontab -l | grep -E "scripts.${MARKER_REFRESH}|scripts.${MARKER_KR}|bin/trader ${MARKER_MAP}"
