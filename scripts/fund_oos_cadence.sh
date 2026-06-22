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

# 스냅샷 디렉토리: 로컬(gitignore) + sibling ../trader. 각 파일을 독립적으로 해석한다 —
# 부분 스냅샷(로컬에 일부 패밀리만)이어도 누락분은 sibling 폴백에서 채운다.
LOCAL="$ROOT/data/snapshots"
SIB="$ROOT/../trader/data/snapshots"
# 최신 날짜(ISO 사전순) 파일을 로컬→sibling 순으로 고른다.
pick()  { local f; f="$(ls -1 "$LOCAL"/$1 2>/dev/null | sort | tail -1)"; [ -n "$f" ] || f="$(ls -1 "$SIB"/$1 2>/dev/null | sort | tail -1)"; echo "$f"; }
# gp(megacap) 는 gp2(횡단) 와 접미사가 겹치므로 -gp2.csv 제외.
pickm() { local f; f="$(ls -1 "$LOCAL"/$1 2>/dev/null | grep -v -- '-gp2.csv' | sort | tail -1)"; [ -n "$f" ] || f="$(ls -1 "$SIB"/$1 2>/dev/null | grep -v -- '-gp2.csv' | sort | tail -1)"; echo "$f"; }

SNAPSHOT="$(pick 'fundamentals-*-gp2.csv')"
PRICES="$(pick 'prices-2*.csv')"
PHIST="$(pick 'prices-ideal-*.csv')"
MOMSNAP="$(pickm 'fundamentals-*-gp.csv')"

if [ -z "$SNAPSHOT" ] || [ -z "$PRICES" ]; then
  echo "[fund-oos-cadence] 스냅샷을 못 찾음 ($LOCAL, $SIB) — snapshot_*.py 로 생성 후 재시도" >&2
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
