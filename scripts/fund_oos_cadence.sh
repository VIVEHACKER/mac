#!/usr/bin/env bash
# 펀드북 forward-OOS cadence 드릴 (cron 용): marks 재생성 → record-if-due(21영업일) → score.
#
# ⚠️ 핀 스냅샷 한계: 스냅샷이 갱신되지 않으면 record 는 동일 rebal_date 라 cadence-gate 가
#    skip 하고, score 도 forward 마크가 없어 0 이다. 새 진입/실현 성과가 쌓이려면 먼저
#    `scripts/snapshot_prices.py` / `scripts/snapshot_fundamentals.py` 로 데이터를 갱신해야 한다.
#    (아래 REFRESH=1 로 켤 수 있게 자리만 둔다 — 데이터 소스/키 구성된 환경에서만.)
#
# 등록 예 (평일 13:15):
#   15 13 * * 1-5  cd "/Users/jjuni/재무관리 모델/trader-fund" && bash scripts/fund_oos_cadence.sh >> out/fund-oos-cadence.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"

# 스냅샷 디렉토리: 로컬(gitignore) → sibling ../trader 폴백
SNAP="$ROOT/data/snapshots"
if [ -z "$(ls "$SNAP"/prices-ideal-*.csv 2>/dev/null)" ]; then
  SNAP="$ROOT/../trader/data/snapshots"
fi

# 최신 날짜의 핀 스냅샷 선택 (ISO 날짜명 = 사전순 최신)
SNAPSHOT="$(ls -1 "$SNAP"/fundamentals-*-gp2.csv 2>/dev/null | sort | tail -1)"
PRICES="$(ls -1 "$SNAP"/prices-2*.csv 2>/dev/null | sort | tail -1)"
PHIST="$(ls -1 "$SNAP"/prices-ideal-*.csv 2>/dev/null | sort | tail -1)"
MOMSNAP="$(ls -1 "$SNAP"/fundamentals-*-gp.csv 2>/dev/null | grep -v -- '-gp2.csv' | sort | tail -1)"

if [ -z "$SNAPSHOT" ] || [ -z "$PRICES" ]; then
  echo "[fund-oos-cadence] 스냅샷을 못 찾음 ($SNAP) — snapshot_*.py 로 생성 후 재시도" >&2
  exit 1
fi

# (선택) 데이터 갱신: 소스/키 구성된 환경에서만. 기본 OFF.
if [ "${REFRESH:-0}" = "1" ]; then
  "$PY" scripts/snapshot_prices.py || echo "[fund-oos-cadence] snapshot_prices 실패(무시)" >&2
  "$PY" scripts/snapshot_fundamentals.py || echo "[fund-oos-cadence] snapshot_fundamentals 실패(무시)" >&2
fi

echo "[fund-oos-cadence] $(date '+%F %T') marks 재생성"
"$PY" scripts/fund_marks.py --since 2026-01-01 --out out/fund-marks.csv

echo "[fund-oos-cadence] record-if-due (cadence 21 business days)"
"$PY" scripts/fund_book_oos.py \
  --marks out/fund-marks.csv \
  --snapshot "$SNAPSHOT" --prices "$PRICES" \
  --price-history "$PHIST" --momentum-snapshot "$MOMSNAP" \
  --benchmark SPY --max-staleness-days 7 --cadence-days 21

echo "[fund-oos-cadence] score (realised vs SPY, per-sleeve)"
"$PY" scripts/fund_book_oos.py --score --by-sleeve \
  --marks out/fund-marks.csv --max-staleness-days 7
