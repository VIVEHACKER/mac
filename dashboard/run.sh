#!/usr/bin/env bash
# 재무관리 모델 — 통합 대시보드 런처.
# "버튼이 안 눌린다"의 거의 모든 원인은 (1) 서버 미실행 (2) 잘못된 cwd 로 import 실패
# (3) 중복 기동/포트 점유.  이 스크립트가 셋 다 방지한다.
#
# 사용:
#   ./dashboard/run.sh             # 포트 8501, 브라우저 자동 오픈
#   PORT=8600 ./dashboard/run.sh   # 포트 변경
set -euo pipefail

# dashboard/ 의 부모(=trader 루트)에서 실행해야 `from data.catalog ...` import 가 된다.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8501}"
STREAMLIT="$ROOT/.venv/bin/streamlit"

if [[ ! -x "$STREAMLIT" ]]; then
  echo "✗ .venv 의 streamlit 을 찾을 수 없습니다: $STREAMLIT" >&2
  echo "  먼저 의존성을 설치하세요:  uv sync   (또는 bash setup/install.sh)" >&2
  exit 1
fi

# 같은 포트가 점유돼 있으면: 점유 프로세스의 명령줄이 바로 이 대시보드
# (dashboard/app.py)인지 확인한 뒤에만 "새로고침"을 안내한다. 다른 앱(다른
# Streamlit 포함)이 점유한 경우엔 기동 실패를 숨기지 않고 에러로 종료한다(codex P2).
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  is_ours=""
  for pid in $(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null); do
    # ps -ww: 폭 기반 잘림 없이 전체 명령줄을 본다(경로가 길어도 매칭).
    if ps -ww -p "$pid" -o command= 2>/dev/null | grep -q "dashboard/app.py"; then
      is_ours=1
      break
    fi
  done
  if [[ -n "$is_ours" ]]; then
    echo "ℹ 대시보드가 이미 포트 $PORT 에서 실행 중입니다 → http://localhost:$PORT 를 새로고침하세요."
    exit 0
  fi
  echo "✗ 포트 $PORT 을 다른 프로세스가 점유하고 있어 기동할 수 없습니다." >&2
  echo "  점유 프로세스 확인:  lsof -nP -iTCP:$PORT -sTCP:LISTEN" >&2
  echo "  다른 포트로 띄우기:  PORT=8600 ./dashboard/run.sh" >&2
  exit 1
fi

echo "▶ 대시보드 기동 → http://localhost:$PORT   (종료: Ctrl-C)"
# config.toml 의 headless=true 를 CLI 로 덮어써 브라우저를 자동으로 연다.
exec "$STREAMLIT" run dashboard/app.py \
  --server.port "$PORT" \
  --server.headless false \
  --server.runOnSave false
