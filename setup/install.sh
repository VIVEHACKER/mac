#!/usr/bin/env bash
# Mac Mini M4 Pro 초기 셋업 스크립트
# Usage: bash setup/install.sh

set -euo pipefail

echo "==> 1. Homebrew 확인"
if ! command -v brew &> /dev/null; then
  echo "Homebrew 설치 중..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

echo "==> 2. 핵심 도구 설치"
brew install uv duckdb redis postgresql@16 git

echo "==> 3. Python 환경 (uv 사용 — pip보다 10~100배 빠름)"
if [ ! -f pyproject.toml ]; then
  uv init --python 3.12
fi
uv venv

echo "==> 4. 핵심 패키지 (모두 arm64 네이티브 wheel 확인됨)"
uv add \
  nautilus_trader \
  polars \
  duckdb \
  alpaca-py \
  ccxt \
  pykrx \
  streamlit \
  plotly \
  xgboost \
  scikit-learn \
  pyarrow \
  python-dotenv

echo "==> 5. PyTorch (M4 MPS 활성화)"
uv add torch torchvision

echo "==> 6. 개발 도구"
uv add --dev pytest pytest-asyncio ruff mypy

echo "==> 7. 동작 확인"
uv run python -c "
import nautilus_trader
import polars as pl
import torch
print(f'Nautilus: {nautilus_trader.__version__}')
print(f'Polars: {pl.__version__}')
print(f'PyTorch: {torch.__version__}')
print(f'MPS available: {torch.backends.mps.is_available()}')
"

echo "==> 8. 디렉토리 구조 생성"
mkdir -p \
  data/ingest data/store/eod data/store/intraday \
  strategies signals engine pod risk dashboard \
  infra tests/test_strategies tests/test_data tests/test_engine

echo "==> 9. .env 템플릿 생성"
if [ ! -f .env ]; then
  cp .env.example .env 2>/dev/null || cat > .env <<'EOF'
# === Alpaca (US stocks) ===
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# === Binance (Crypto) ===
BINANCE_API_KEY=
BINANCE_SECRET=
BINANCE_TESTNET=true

# === KIS API (한국, 라이브용 — 선택) ===
KIS_APP_KEY=
KIS_APP_SECRET=
KIS_ACCOUNT_NO=
EOF
  echo ".env 생성됨. API 키 채우세요."
fi

echo ""
echo "==> 셋업 완료"
echo ""
echo "다음 단계:"
echo "  1. .env에 API 키 입력 (https://app.alpaca.markets, Binance API)"
echo "  2. Stage 1 시작: 'uv run python -m data.ingest.alpaca_us'"
echo "  3. Claude Code 켜서 CLAUDE.md 자동 로드 → 작업 이어가기"
