"""재무관리 모델 — 통합 트레이딩 대시보드 (Streamlit, 로컬 전용).

직접 만든 기능 전부를 한 화면에서: 종목선정(AQR/모멘텀) · 차트리딩(SMC/ICT) ·
추천기(evaluate_ticker) · 검증결과(chartbloom/백테스트) · 예측(금리/CPI) ·
페이퍼원장(forward-OOS) · RAG(경제분석 챗봇).

실행:  cd "…/trader" && .venv/bin/streamlit run dashboard/app.py
"""

from __future__ import annotations

import contextlib
import html
import io
import json
import math
import os
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from data.catalog import DEFAULT_CATALOG_PATH, MarketDataCatalog
from data.ingest.yahoo import YahooQuote
from data.models import PriceBar

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / DEFAULT_CATALOG_PATH
OUT_DIR = ROOT / "out"
MERR = Path("/Users/jjuni/재무관리 모델/merr_corpus")
RAG_URL = "http://localhost:8800"
LIVE_CATALOG_PATH = ROOT / "data" / "store" / "live-prices.duckdb"
MANUAL_TICKET_LOG = ROOT / "data" / "store" / "manual-order-tickets.jsonl"
LIVE_HALT_STATE = ROOT / "data" / "store" / "live-halt.json"
LIVE_EQUITY_STATE = ROOT / "data" / "store" / "live-equity.json"
CHART_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
CHART_LOOKBACK_DAYS = {"1m": 7, "5m": 30, "15m": 59, "1h": 59, "4h": 59, "1d": 420}
CRYPTO_CHART_LOOKBACK_DAYS = {"1m": 1, "5m": 2, "15m": 4, "1h": 14, "4h": 60, "1d": 420}
LIVE_QUOTE_TTL_SECONDS = 12
LIVE_ORDER_GATE_TTL_SECONDS = 300

# ─────────────────────────────────────────────────────────────────────────────
# UI / 테마
# ─────────────────────────────────────────────────────────────────────────────
_CSS = """
<style>
  @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.min.css");

  :root {
    --bg: #f4f6f1;
    --bg-rail: #e8ede5;
    --surface: #ffffff;
    --surface-muted: #eef2eb;
    --ink: #141815;
    --ink-soft: #343b37;
    --muted: #69736d;
    --line: #d6ddd3;
    --line-strong: #aeb8af;
    --accent: #2c6d5c;
    --accent-deep: #174f42;
    --accent-soft: #dfece6;
    --warning: #8a6726;
    --danger: #a34842;
    --success: #276b57;
    --shadow: 0 18px 45px rgba(38, 54, 45, 0.08);
    --mono: "SFMono-Regular", "JetBrains Mono", "Menlo", monospace;
  }

  html, body, .stApp {
    font-family: "Pretendard", -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
    letter-spacing: 0;
  }

  [data-testid="stIconMaterial"] {
    font-family: "Material Symbols Rounded" !important;
    font-weight: 400;
    font-style: normal;
    line-height: 1;
    letter-spacing: normal;
    text-transform: none;
    white-space: nowrap;
    overflow-wrap: normal;
    direction: ltr;
    font-feature-settings: "liga";
    -webkit-font-smoothing: antialiased;
  }

  html, body, .stApp {
    background:
      linear-gradient(90deg, rgba(20, 24, 21, 0.035) 1px, transparent 1px),
      linear-gradient(180deg, rgba(20, 24, 21, 0.03) 1px, transparent 1px),
      var(--bg);
    background-size: 28px 28px;
    color: var(--ink);
  }

  .stApp::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    background-image: radial-gradient(rgba(20, 24, 21, 0.09) 0.6px, transparent 0.6px);
    background-size: 5px 5px;
    opacity: 0.16;
    z-index: 0;
  }

  [data-testid="stToolbar"], footer, header[data-testid="stHeader"] {
    display: none;
  }

  .block-container {
    position: relative;
    z-index: 1;
    padding: 2.1rem 3rem 4rem;
    max-width: 1460px;
  }

  h1, h2, h3, h4 {
    color: var(--ink);
    letter-spacing: 0;
    text-wrap: balance;
    word-break: keep-all;
  }

  h2, h3 {
    margin-top: 0.45rem;
  }

  p, li, label, span, div {
    word-break: keep-all;
  }

  code, pre, [data-testid="stMetricValue"], .stDataFrame {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
  }

  div[data-testid="stCode"] pre,
  div[data-testid="stCode"] pre > div,
  div[data-testid="stCode"] code {
    white-space: pre-wrap !important;
    overflow-wrap: anywhere;
    word-break: break-word;
  }

  .app-hero {
    display: grid;
    grid-template-columns: minmax(280px, 0.9fr) minmax(420px, 1.35fr);
    gap: 2rem;
    align-items: end;
    border-top: 2px solid var(--ink);
    border-bottom: 1px solid var(--line-strong);
    padding: 1.65rem 0 1.35rem;
    margin-bottom: 1.2rem;
  }

  .hero-kicker, .section-kicker {
    color: var(--accent-deep);
    font-size: 0.73rem;
    font-weight: 800;
    text-transform: uppercase;
  }

  .hero-title {
    margin: 0.4rem 0 0.35rem;
    color: var(--ink);
    font-size: clamp(2rem, 4.4vw, 4.3rem);
    line-height: 1.02;
    font-weight: 860;
  }

  .hero-copy {
    color: var(--muted);
    max-width: 62ch;
    font-size: 0.98rem;
    line-height: 1.6;
    word-break: keep-all !important;
    overflow-wrap: normal !important;
    line-break: strict;
  }

  .hero-metrics {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    border: 1px solid var(--line);
    background: rgba(255, 255, 255, 0.72);
    box-shadow: var(--shadow);
  }

  .hero-metric {
    min-height: 104px;
    padding: 1rem 1rem 0.9rem;
    border-right: 1px solid var(--line);
  }

  .hero-metric:last-child {
    border-right: 0;
  }

  .hero-metric-label {
    color: var(--muted);
    font-size: 0.77rem;
    font-weight: 700;
  }

  .hero-metric-value {
    margin-top: 0.6rem;
    color: var(--ink);
    font-family: var(--mono);
    font-size: clamp(1.35rem, 2.5vw, 2rem);
    font-weight: 760;
    line-height: 1.08;
  }

  .hero-metric-value.compact {
    font-size: clamp(1.15rem, 1.7vw, 1.55rem);
    line-height: 1.22;
    white-space: normal;
  }

  .hero-metric-foot {
    margin-top: 0.55rem;
    color: var(--muted);
    font-size: 0.72rem;
  }

  .section-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(220px, max-content);
    align-items: end;
    gap: 1rem;
    padding-top: 1.15rem;
    margin: 0.35rem 0 1rem;
    border-top: 1px solid var(--line);
  }

  .section-title {
    margin: 0.18rem 0 0;
    color: var(--ink);
    font-size: clamp(1.35rem, 2vw, 2rem);
    font-weight: 820;
    line-height: 1.18;
  }

  .section-note, .evidence-note {
    color: var(--muted);
    font-size: 0.92rem;
    line-height: 1.55;
    margin: 0.35rem 0 0;
    word-break: keep-all !important;
    overflow-wrap: normal !important;
    line-break: strict;
  }

  .section-side {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 0.76rem;
    text-align: right;
    max-width: 100%;
    white-space: normal;
    overflow-wrap: anywhere;
  }

  .stock-brief {
    display: grid;
    grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr);
    gap: 1rem;
    margin: 0.9rem 0 1rem;
  }

  .candidate-list {
    border-top: 2px solid var(--ink);
    border-bottom: 1px solid var(--line-strong);
    margin: 1rem 0 1.1rem;
    background: rgba(255, 255, 255, 0.58);
  }

  .candidate-row {
    display: grid;
    grid-template-columns: 0.55fr 1.15fr 1.6fr 0.88fr;
    gap: 1rem;
    padding: 0.95rem 0.85rem;
    border-bottom: 1px solid var(--line);
    align-items: start;
  }

  .candidate-row:last-child {
    border-bottom: 0;
  }

  .candidate-rank {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 0.78rem;
  }

  .candidate-symbol {
    margin-top: 0.25rem;
    color: var(--ink);
    font-family: var(--mono);
    font-size: 1.25rem;
    font-weight: 820;
  }

  .candidate-name {
    color: var(--ink);
    font-weight: 780;
    line-height: 1.25;
  }

  .candidate-sector {
    margin-top: 0.22rem;
    color: var(--muted);
    font-size: 0.78rem;
  }

  .candidate-copy {
    color: var(--ink-soft);
    font-size: 0.88rem;
    line-height: 1.5;
    word-break: keep-all;
  }

  .candidate-levels {
    color: var(--ink);
    font-family: var(--mono);
    font-size: 0.82rem;
    line-height: 1.55;
    white-space: nowrap;
  }

  .brief-panel {
    background: rgba(255, 255, 255, 0.82);
    border: 1px solid var(--line);
    border-top: 2px solid var(--ink);
    padding: 1rem 1.05rem;
    min-width: 0;
  }

  .brief-label {
    color: var(--accent-deep);
    font-size: 0.72rem;
    font-weight: 820;
    text-transform: uppercase;
  }

  .brief-title {
    margin-top: 0.35rem;
    color: var(--ink);
    font-size: 1.35rem;
    font-weight: 840;
    line-height: 1.2;
  }

  .brief-meta {
    margin-top: 0.28rem;
    color: var(--muted);
    font-family: var(--mono);
    font-size: 0.78rem;
  }

  .brief-body {
    margin-top: 0.78rem;
    color: var(--ink-soft);
    font-size: 0.94rem;
    line-height: 1.62;
    word-break: keep-all;
    overflow-wrap: normal;
  }

  .brief-list {
    margin: 0.7rem 0 0;
    padding: 0;
    list-style: none;
  }

  .brief-list li {
    position: relative;
    padding-left: 0.9rem;
    margin: 0.42rem 0;
    color: var(--ink-soft);
    font-size: 0.9rem;
    line-height: 1.5;
  }

  .brief-list li::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0.62em;
    width: 0.34rem;
    height: 0.34rem;
    background: var(--accent);
  }

  .badge {
    display: inline-flex;
    align-items: center;
    min-height: 1.55rem;
    padding: 0.18rem 0.58rem;
    border-radius: 4px;
    font-family: var(--mono);
    font-weight: 760;
    font-size: 0.78rem;
    letter-spacing: 0;
  }

  .badge-buy {
    background: #dceee7;
    color: var(--success);
    border: 1px solid rgba(39, 107, 87, 0.24);
  }

  .badge-hold {
    background: #f2ead8;
    color: var(--warning);
    border: 1px solid rgba(138, 103, 38, 0.24);
  }

  .badge-avoid {
    background: #f4dedc;
    color: var(--danger);
    border: 1px solid rgba(163, 72, 66, 0.22);
  }

  div[data-testid="stMetric"] {
    background: rgba(255, 255, 255, 0.78);
    border: 1px solid var(--line);
    border-left: 3px solid var(--accent);
    padding: 0.9rem 1rem;
    min-height: 96px;
    box-shadow: 0 10px 24px rgba(38, 54, 45, 0.055);
  }

  [data-testid="stMetricLabel"] {
    color: var(--muted);
    font-weight: 720;
  }

  [data-testid="stMetricValue"] {
    color: var(--ink);
    font-weight: 760;
  }

  [data-testid="stMetricDelta"] {
    color: var(--accent-deep);
  }

  div[data-baseweb="tab-list"] {
    gap: 0.35rem;
    border-bottom: 1px solid var(--line-strong);
    padding-bottom: 0.52rem;
    margin-bottom: 1.35rem;
    overflow-x: auto;
    flex-wrap: nowrap;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }

  div[data-baseweb="tab-list"]::-webkit-scrollbar {
    display: none;
  }

  button[data-baseweb="tab"] {
    flex: 0 0 auto;
    min-height: 2.65rem;
    border: 1px solid transparent;
    border-radius: 4px;
    color: var(--muted);
    font-weight: 760;
    white-space: nowrap;
    transition: background 180ms ease, color 180ms ease, border-color 180ms ease, transform 120ms ease;
  }

  button[data-baseweb="tab"]:hover {
    background: rgba(44, 109, 92, 0.08);
    color: var(--ink);
  }

  button[data-baseweb="tab"]:active {
    transform: translateY(1px);
  }

  button[data-baseweb="tab"][aria-selected="true"] {
    background: var(--ink);
    color: #f7faf6;
    border-color: var(--ink);
  }

  div[data-baseweb="tab-highlight"] {
    display: none;
  }

  .stButton > button {
    border-radius: 4px;
    border: 1px solid var(--ink);
    background: var(--ink);
    color: #f7faf6;
    font-weight: 760;
    transition: transform 120ms ease, box-shadow 180ms ease, background 180ms ease;
    box-shadow: 0 8px 18px rgba(20, 24, 21, 0.12);
  }

  .stButton > button:hover {
    background: var(--accent-deep);
    border-color: var(--accent-deep);
    color: #ffffff;
  }

  .stButton > button * {
    color: inherit !important;
  }

  .stButton > button:active {
    transform: translateY(1px);
  }

  .stButton > button:focus-visible,
  button[data-baseweb="tab"]:focus-visible {
    outline: 3px solid rgba(44, 109, 92, 0.28);
    outline-offset: 2px;
  }

  [data-baseweb="input"] > div,
  [data-baseweb="select"] > div,
  [data-baseweb="textarea"] {
    background: rgba(255, 255, 255, 0.84);
    border-color: var(--line-strong);
    border-radius: 4px;
  }

  [data-baseweb="input"]:focus-within > div,
  [data-baseweb="select"]:focus-within > div,
  [data-baseweb="textarea"]:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(44, 109, 92, 0.12);
  }

  [data-testid="stDataFrame"],
  .stDataFrame {
    border: 1px solid var(--line);
    box-shadow: 0 12px 28px rgba(38, 54, 45, 0.055);
  }

  div[data-testid="stExpander"] {
    border: 1px solid var(--line);
    background: rgba(255, 255, 255, 0.7);
    border-radius: 4px;
    box-shadow: none;
  }

  div[data-testid="stAlert"] {
    border-radius: 4px;
    border: 1px solid var(--line);
  }

  hr {
    border-color: var(--line);
    margin: 1.4rem 0;
  }

  @media (max-width: 900px) {
    .block-container {
      padding: 1.35rem 1rem 3rem;
    }
    .app-hero {
      grid-template-columns: 1fr;
      gap: 1rem;
    }
    .hero-metrics {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .hero-metric:nth-child(2) {
      border-right: 0;
    }
    .hero-metric:nth-child(-n + 2) {
      border-bottom: 1px solid var(--line);
    }
    .section-head {
      grid-template-columns: 1fr;
    }
    .section-side {
      text-align: left;
    }
    .stock-brief {
      grid-template-columns: 1fr;
    }
    .candidate-row {
      grid-template-columns: 1fr;
      gap: 0.55rem;
      padding: 0.9rem 0.75rem;
    }
    .candidate-levels {
      white-space: normal;
    }
    div[data-baseweb="tab-list"] {
      flex-wrap: wrap;
      overflow-x: visible;
      row-gap: 0.42rem;
    }
    button[data-baseweb="tab"] {
      min-height: 2.45rem;
      padding-inline: 0.7rem;
      font-size: 0.86rem;
    }
  }

  @media (min-width: 901px) and (max-width: 1180px) {
    .app-hero {
      grid-template-columns: 1fr;
      align-items: start;
    }
    .hero-metrics {
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }
    .section-head {
      grid-template-columns: 1fr;
      align-items: start;
    }
    .section-side {
      text-align: left;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      scroll-behavior: auto !important;
      transition-duration: 0.01ms !important;
    }
  }
</style>
"""


def _badge(action: str) -> str:
    a = (action or "").upper()
    cls = (
        "badge-buy"
        if a in ("BUY", "ENTER_NOW", "SCALE_IN")
        else ("badge-avoid" if a in ("AVOID", "SELL") else "badge-hold")
    )
    return f'<span class="badge {cls}">{a or "—"}</span>'


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _fmt_num(value: Any, digits: int = 2) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "—"
    return f"{numeric:,.{digits}f}"


def _fmt_pct(value: Any, digits: int = 1, *, already_pct: bool = False, signed: bool = True) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "—"
    pct = numeric if already_pct else numeric * 100
    sign = "+" if signed else ""
    return f"{pct:{sign}.{digits}f}%"


def _short_text(text: str, limit: int = 120) -> str:
    clean = " ".join(str(text).split())
    return clean if len(clean) <= limit else f"{clean[: limit - 1]}…"


def _compact_reasons(reasons: tuple[str, ...] | list[str], limit: int = 2) -> str:
    clean = [_short_text(reason, 110) for reason in reasons if str(reason).strip()]
    return " · ".join(clean[:limit]) if clean else "—"


def _confidence_score(confidence: Any) -> float | None:
    return _safe_float(getattr(confidence, "score", None))


def _factor_rows(values: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "항목": "AQR 합성",
            "값": _fmt_num(values.get("composite"), 3),
            "해석": "횡단면 최종 점수",
        },
        {"항목": "모멘텀", "값": _fmt_pct(values.get("momentum")), "해석": "최근 추세 강도"},
        {"항목": "가치", "값": _fmt_num(values.get("value"), 3), "해석": "밸류에이션 팩터"},
        {"항목": "퀄리티", "값": _fmt_num(values.get("quality"), 3), "해석": "수익성/재무 품질"},
    ]


def _signal_direction_label(direction: int) -> str:
    if direction > 0:
        return "우호"
    if direction < 0:
        return "반대"
    return "중립"


def _entry_zone_text(entry_zone: tuple[float, float] | None) -> str:
    if not entry_zone:
        return "—"
    lo, hi = entry_zone
    largest = max(abs(lo), abs(hi))
    digits = 0 if largest >= 1_000 else (2 if largest >= 1 else 4)
    return f"{_fmt_num(lo, digits)}–{_fmt_num(hi, digits)}"


def _entry_plan_rows(entry_plan: Any) -> list[dict[str, str]]:
    if entry_plan is None:
        return []
    return [
        {"항목": "현재가", "값": _fmt_num(getattr(entry_plan, "current_price", None))},
        {"항목": "평균 진입", "값": _fmt_num(getattr(entry_plan, "target_entry", None))},
        {"항목": "손절", "값": _fmt_num(getattr(entry_plan, "stop_loss", None))},
        {"항목": "목표", "값": _fmt_num(getattr(entry_plan, "target_exit", None))},
        {"항목": "손익비", "값": _fmt_num(getattr(entry_plan, "risk_reward", None), 2)},
        {"항목": "예상 보유일", "값": f"{getattr(entry_plan, 'expected_holding_days', '—')}일"},
    ]


_STOCK_PROFILES: dict[str, dict[str, str | tuple[str, ...]]] = {
    "CL": {
        "name": "Colgate-Palmolive",
        "sector": "필수소비재",
        "business": "치약, 구강관리, 개인·가정용품, 반려동물 영양 브랜드를 전세계에 판매하는 방어적 소비재 기업입니다.",
        "why": "경기 민감도가 낮은 반복 수요 사업이고, 현재 검증 AQR 랭킹에서 최상단에 있어 모델은 방어적 현금흐름과 상대 강도를 동시에 평가합니다.",
        "risks": (
            "원재료 가격과 환율이 마진을 압박할 수 있습니다.",
            "신흥국 소비 둔화가 매출 성장률을 낮출 수 있습니다.",
            "방어주 특성상 급등장에서 상대 수익률이 약해질 수 있습니다.",
        ),
    },
    "MU": {
        "name": "Micron Technology",
        "sector": "반도체 메모리",
        "business": "DRAM, NAND, 고대역폭 메모리(HBM)를 만드는 메모리 반도체 기업입니다.",
        "why": "메모리 업황 회복과 AI 서버 메모리 수요를 가격/모멘텀이 반영하는 구간에서 AQR 상위권에 올라온 종목입니다.",
        "risks": (
            "메모리 가격은 공급 증설과 재고 사이클에 크게 흔들립니다.",
            "대규모 설비투자가 현금흐름 변동성을 키울 수 있습니다.",
            "중국/수출 규제와 고객 투자 지연이 리스크입니다.",
        ),
    },
    "TGT": {
        "name": "Target",
        "sector": "소매",
        "business": "미국 전역의 대형 매장과 온라인 채널로 생활용품, 의류, 식료품을 판매하는 리테일러입니다.",
        "why": "소비재 리테일 중 가격 회복과 밸류에이션/모멘텀 조합이 개선되어 검증 전략의 보유권 안에 들어왔습니다.",
        "risks": (
            "미국 소비 둔화와 재고 부담이 마진을 누를 수 있습니다.",
            "Walmart, Amazon, Costco와의 가격 경쟁이 강합니다.",
            "임금·물류비 상승이 영업 레버리지를 약화시킬 수 있습니다.",
        ),
    },
    "HD": {
        "name": "Home Depot",
        "sector": "주택·리테일",
        "business": "건축자재, 주택 보수, DIY·프로 고객용 공구와 설비를 판매하는 미국 최대권 홈임프루브먼트 기업입니다.",
        "why": "주택 보수 수요가 구조적으로 유지되는 가운데, 모델은 현재 가격 추세와 품질/가치 조합을 상위 후보로 평가합니다.",
        "risks": (
            "금리와 주택 거래량 둔화가 매출을 압박할 수 있습니다.",
            "전문 시공 고객 수요가 경기 변동에 민감합니다.",
            "목재·운송비 같은 비용 변동이 마진에 영향을 줍니다.",
        ),
    },
    "DD": {
        "name": "DuPont",
        "sector": "특수소재",
        "business": "전자재료, 산업용 소재, 보호소재 등 고부가 화학·소재 제품을 공급하는 기업입니다.",
        "why": "산업재/소재군 안에서 가격 회복과 AQR 점수가 동시에 개선되어, 모델은 경기 회복 노출이 있는 상위 후보로 분류합니다.",
        "risks": (
            "산업 생산 둔화와 고객 재고 조정에 민감합니다.",
            "원재료 가격과 구조조정 비용이 실적 변동을 키울 수 있습니다.",
            "사업 포트폴리오 변화가 비교 가능성을 낮출 수 있습니다.",
        ),
    },
    "INTC": {
        "name": "Intel",
        "sector": "반도체",
        "business": "PC·서버 CPU와 데이터센터 반도체, 파운드리 사업을 운영하는 종합 반도체 기업입니다.",
        "why": "턴어라운드 성격이 강하지만, 현재 모델은 가격 모멘텀과 횡단면 점수가 검증 전략 보유 기준을 넘었다고 봅니다.",
        "risks": (
            "공정 전환과 파운드리 실행 실패가 가장 큰 리스크입니다.",
            "AMD, NVIDIA, TSMC와의 경쟁 압력이 큽니다.",
            "대규모 투자로 잉여현금흐름 회복이 지연될 수 있습니다.",
        ),
    },
    "LRCX": {
        "name": "Lam Research",
        "sector": "반도체 장비",
        "business": "식각·증착 등 웨이퍼 제조 공정 장비를 공급하는 반도체 장비 기업입니다.",
        "why": "메모리/첨단 공정 투자 회복에 레버리지가 있고, AQR 상위권 진입으로 모델상 매수 후보가 됐습니다.",
        "risks": (
            "반도체 설비투자 사이클이 꺾이면 매출이 빠르게 둔화됩니다.",
            "중국 수출 규제와 고객 투자 지연에 민감합니다.",
            "장비주는 기대가 앞서면 밸류에이션 압축이 빠르게 나타납니다.",
        ),
    },
    "AMD": {
        "name": "Advanced Micro Devices",
        "sector": "반도체",
        "business": "데이터센터·PC용 CPU, AI 가속기, GPU와 임베디드 반도체를 설계하는 팹리스 기업입니다.",
        "why": "데이터센터와 AI 가속기 매출 기대가 가격 상대강도에 반영되며, 현재 가치·모멘텀·퀄리티 합성 랭크가 검증 전략의 보유권에 진입했습니다.",
        "risks": (
            "AI 가속기 시장에서 NVIDIA와의 성능·생태계 경쟁이 강합니다.",
            "TSMC 생산 의존과 첨단 패키징 공급 제약이 있습니다.",
            "높아진 기대 대비 매출 전환이 늦으면 변동성이 커질 수 있습니다.",
        ),
    },
    "QCOM": {
        "name": "Qualcomm",
        "sector": "통신 반도체",
        "business": "스마트폰 애플리케이션 프로세서와 모뎀, RF 칩, 자동차·IoT 반도체 및 무선통신 특허를 보유한 기업입니다.",
        "why": "스마트폰 외 자동차·엣지 AI로 수익원이 확장되는 가운데, 현 시점의 AQR 합성 점수가 검증 전략 Top-N에 포함됐습니다.",
        "risks": (
            "스마트폰 출하량과 중국 안드로이드 수요에 민감합니다.",
            "주요 고객의 자체 모뎀 전환이 장기 매출을 줄일 수 있습니다.",
            "특허 라이선스 규제와 경쟁사 가격 압력이 리스크입니다.",
        ),
    },
    "AAPL": {
        "name": "Apple",
        "sector": "소비자 기술",
        "business": "iPhone, Mac, iPad, 웨어러블과 서비스 생태계를 운영하는 글로벌 소비자 기술 기업입니다.",
        "why": "브랜드·서비스 수익 기반이 강하지만, 이 화면에서는 검증 AQR 랭크와 가격 모멘텀을 우선합니다.",
        "risks": (
            "iPhone 교체 수요 둔화",
            "중국 매출과 공급망 리스크",
            "규제와 앱스토어 수수료 압박",
        ),
    },
    "MSFT": {
        "name": "Microsoft",
        "sector": "소프트웨어·클라우드",
        "business": "Azure, Office, Windows, 보안, AI 인프라를 운영하는 글로벌 소프트웨어 플랫폼 기업입니다.",
        "why": "클라우드와 기업 소프트웨어 기반의 질이 높지만, 매수 여부는 이 화면의 AQR 랭크와 진입 가격으로 판단합니다.",
        "risks": ("클라우드 성장률 둔화", "AI 인프라 투자 부담", "규제와 대형 고객 IT 지출 둔화"),
    },
    "NVDA": {
        "name": "NVIDIA",
        "sector": "AI 반도체",
        "business": "AI 가속기, GPU, 네트워킹, 소프트웨어 생태계를 공급하는 반도체 플랫폼 기업입니다.",
        "why": "AI 투자 사이클의 대표 수혜주지만, 이 대시보드는 내러티브보다 검증 랭크와 리스크 가격을 우선합니다.",
        "risks": ("AI 설비투자 둔화", "고객 집중", "수출 규제와 고밸류에이션 압축"),
    },
}


def _stock_profile(symbol: str) -> dict[str, str | tuple[str, ...]]:
    normalized = symbol.upper()
    return _STOCK_PROFILES.get(
        normalized,
        {
            "name": normalized,
            "sector": "미분류",
            "business": "이 종목의 사업 설명은 아직 로컬 프로필에 등록되지 않았습니다. 모델 점수와 가격 데이터 기준으로만 판단합니다.",
            "why": "검증 유니버스 내 상대 랭크, 모멘텀, 가치, 퀄리티 점수와 진입/손절 레벨을 기준으로 매수 후보 여부를 판단합니다.",
            "risks": (
                "사업 설명이 미등록되어 정성 리스크 검토가 부족합니다.",
                "추천기 탭에서 개별 종목 평가를 다시 확인해야 합니다.",
            ),
        },
    )


def _model_buy_case(row: dict[str, Any], meta: dict[str, Any] | None = None) -> str:
    ticker = str(row.get("종목", "")).upper()
    action = str(row.get("액션", "")).upper()
    rank = row.get("순위")
    universe = (meta or {}).get("universe_size") or "?"
    top_n = (meta or {}).get("top_n") or "?"
    percentile = row.get("백분위")
    entry_label = str(row.get("_entry_label") or "평균 진입")
    entry = _fmt_num(row.get("진입"))
    stop = _fmt_num(row.get("손절"))
    target = _fmt_num(row.get("목표"))
    if action not in {"BUY", "HOLD", "AVOID", "SELL"}:
        basis = row.get("_sort_basis") or row.get("정렬 기준") or "현재 정렬 기준"
        return (
            f"{ticker}는 커스텀 스크리너에서 {basis} 기준 #{rank}입니다. "
            "실제 매수 판단은 검증 선정 또는 추천기 탭에서 액션, 신뢰도, 진입/손절 레벨을 다시 확인해야 합니다."
        )
    if action == "BUY":
        return (
            f"{ticker}는 검증 유니버스 {universe}개 중 AQR {rank}위, 백분위 {percentile}로 "
            f"전략 top-{top_n} 보유권 안에 있습니다. {entry_label} {entry}, 손절 {stop}, 목표 {target}로 "
            "손실 한도를 먼저 정한 뒤 매수 후보로 봅니다."
        )
    if action == "HOLD":
        return (
            f"{ticker}는 현재 매수 우선순위는 아니지만 검증 유니버스 내 상대 점수가 남아 있어 "
            "보유 또는 관찰 대상으로 분류됩니다."
        )
    return (
        f"{ticker}는 현재 모델 기준 매수 조건을 충족하지 않습니다. 랭크, 신뢰도, 진입 가격이 "
        "개선될 때까지 관찰 대상으로 둡니다."
    )


def _render_stock_brief(
    symbol: str, row: dict[str, Any], meta: dict[str, Any] | None = None
) -> None:
    profile = _stock_profile(symbol)
    risks = profile.get("risks", ())
    risk_items = [risks] if isinstance(risks, str) else list(risks)
    risk_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in risk_items[:4])
    entry_label = str(row.get("_entry_label") or "평균 진입")
    advisory_entry = _safe_float(row.get("참고진입"))
    conditions = [
        f"{entry_label}: {_fmt_num(row.get('진입'))}",
    ]
    if advisory_entry is not None:
        conditions.append(f"참고 평균진입: {_fmt_num(advisory_entry)}")
    conditions += [
        f"손절: {_fmt_num(row.get('손절'))}",
        f"목표: {_fmt_num(row.get('목표'))}",
        f"현재가: {_fmt_num(row.get('현재가'))}",
    ]
    condition_html = "".join(f"<li>{html.escape(item)}</li>" for item in conditions)
    st.markdown(
        f"""
        <section class="stock-brief">
          <article class="brief-panel">
            <div class="brief-label">무슨 종목인가</div>
            <div class="brief-title">{html.escape(symbol.upper())} · {html.escape(str(profile["name"]))}</div>
            <div class="brief-meta">{html.escape(str(profile["sector"]))}</div>
            <div class="brief-body">{html.escape(str(profile["business"]))}</div>
          </article>
          <article class="brief-panel">
            <div class="brief-label">왜 매수 후보인가</div>
            <div class="brief-body">{html.escape(str(profile["why"]))}</div>
            <ul class="brief-list">
              <li>{html.escape(_model_buy_case(row, meta))}</li>
              <li>모델 판단은 회사 설명이 아니라 검증된 AQR 랭크, 신뢰도, 진입/손절 레벨을 우선합니다.</li>
            </ul>
          </article>
          <article class="brief-panel">
            <div class="brief-label">매수 조건</div>
            <ul class="brief-list">{condition_html}</ul>
          </article>
          <article class="brief-panel">
            <div class="brief-label">확인할 리스크</div>
            <ul class="brief-list">{risk_html}</ul>
          </article>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_candidate_list(picks: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    rows: list[str] = []
    for row in picks:
        symbol = str(row["종목"]).upper()
        profile = row.get("_profile") or _stock_profile(symbol)
        rows.append(
            '<article class="candidate-row">'
            "<div>"
            f'<div class="candidate-rank">#{html.escape(str(row["순위"]))} / '
            f"{html.escape(str(meta['universe_size']))}</div>"
            f'<div class="candidate-symbol">{html.escape(symbol)}</div>'
            f'<div class="candidate-sector">{html.escape(str(row["액션"]))} · '
            f"{html.escape(str(row['신뢰도']))}</div>"
            "</div>"
            "<div>"
            f'<div class="candidate-name">{html.escape(str(profile["name"]))}</div>'
            f'<div class="candidate-sector">{html.escape(str(profile["sector"]))}</div>'
            f'<div class="candidate-copy">{html.escape(str(profile["business"]))}</div>'
            "</div>"
            f'<div class="candidate-copy">{html.escape(_model_buy_case(row, meta))}</div>'
            '<div class="candidate-levels">'
            f"현재 {_fmt_num(row.get('현재가'))}<br>"
            f"진입 {_fmt_num(row.get('진입'))}<br>"
            f"손절 {_fmt_num(row.get('손절'))}<br>"
            f"목표 {_fmt_num(row.get('목표'))}"
            "</div>"
            "</article>"
        )
    st.markdown(
        f'<section class="candidate-list">{"".join(rows)}</section>',
        unsafe_allow_html=True,
    )


def _render_section_header(
    title: str,
    note: str = "",
    *,
    kicker: str = "Workspace",
    side: str = "",
) -> None:
    side_html = f'<div class="section-side">{html.escape(side)}</div>' if side else ""
    st.markdown(
        f"""
        <section class="section-head">
          <div>
            <div class="section-kicker">{html.escape(kicker)}</div>
            <h2 class="section-title">{html.escape(title)}</h2>
            {f'<p class="section-note">{html.escape(note)}</p>' if note else ""}
          </div>
          {side_html}
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_app_header(cov: list[Any]) -> None:
    total_bars = sum(c.rows for c in cov)
    markets = " / ".join(sorted({c.market for c in cov})) if cov else "—"
    refreshed = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(
        f"""
        <section class="app-hero">
          <div>
            <div class="hero-kicker">Local trading operations</div>
            <h1 class="hero-title">재무관리 모델</h1>
            <p class="hero-copy">
              종목 선정, 차트 판정, 추천 근거, 검증 결과를 확인합니다.
              페이퍼 원장과 실거래 티켓도 같은 화면에서 점검합니다.
            </p>
          </div>
          <div class="hero-metrics" aria-label="대시보드 상태">
            <div class="hero-metric">
              <div class="hero-metric-label">Catalog</div>
              <div class="hero-metric-value">{len(cov):,}</div>
              <div class="hero-metric-foot">tracked symbols</div>
            </div>
            <div class="hero-metric">
              <div class="hero-metric-label">Bars</div>
              <div class="hero-metric-value">{total_bars:,}</div>
              <div class="hero-metric-foot">stored observations</div>
            </div>
            <div class="hero-metric">
              <div class="hero-metric-label">Markets</div>
              <div class="hero-metric-value compact">{html.escape(markets)}</div>
              <div class="hero-metric-foot">available universes</div>
            </div>
            <div class="hero-metric">
              <div class="hero-metric-label">Runtime</div>
              <div class="hero-metric-value">Local</div>
              <div class="hero-metric-foot">{refreshed}</div>
            </div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로더
# ─────────────────────────────────────────────────────────────────────────────
def _live_fetch(symbol: str, market: str, tf: str = "1d", days: int | None = None):
    """Fetch fresh OHLCV, using no-key providers available to the local dashboard."""

    if tf not in CHART_TIMEFRAMES:
        raise ValueError(f"unsupported chart timeframe: {tf}")
    if days is None:
        days = CRYPTO_CHART_LOOKBACK_DAYS[tf] if market == "crypto" else CHART_LOOKBACK_DAYS[tf]
    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=days)
    if market == "crypto":
        from data.ingest.ccxt_crypto import fetch_ccxt_bars

        return fetch_ccxt_bars(
            symbol, start, end, timeframe=tf, exchange_id="binance", intraday=(tf != "1d")
        )
    if market == "us" or (market in ("kospi", "kosdaq") and tf != "1d"):
        from data.ingest.yahoo import aggregate_intraday_bars, fetch_yahoo_bars

        yahoo_interval = "1h" if tf == "4h" else tf
        bars = fetch_yahoo_bars(
            symbol,
            market=market,
            start=start,
            end=end,
            interval=yahoo_interval,
        )
        if tf == "4h":
            return aggregate_intraday_bars(bars, bars_per_bucket=4, frequency="4h")
        return bars
    if market in ("kospi", "kosdaq"):
        from data.ingest.pykrx_kr import fetch_pykrx_bars

        return fetch_pykrx_bars(symbol, market=market, start=start, end=end)
    return []


@st.cache_data(ttl=LIVE_QUOTE_TTL_SECONDS, show_spinner=False)
def _cached_yahoo_quotes(symbols: tuple[str, ...], market: str) -> dict[str, YahooQuote]:
    from data.ingest.yahoo import fetch_yahoo_quotes

    return fetch_yahoo_quotes(symbols, market)


def _bar_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _overlay_live_quotes(
    bars_by_symbol: dict[str, list[PriceBar]],
    quotes: dict[str, YahooQuote],
    market: str,
) -> dict[str, list[PriceBar]]:
    """Overlay the latest quote as a daily mark without rewriting historical bars."""

    updated = {symbol: list(bars) for symbol, bars in bars_by_symbol.items()}
    for symbol, quote in quotes.items():
        bars = updated.get(symbol)
        if not bars:
            continue
        quote_date = quote.timestamp.date()
        last = bars[-1]
        last_date = _bar_date(last.ts)
        if quote_date < last_date:
            continue
        if quote_date == last_date:
            bars[-1] = PriceBar(
                symbol=last.symbol,
                market=last.market,
                source_symbol=quote.source_symbol,
                ts=last.ts,
                open=last.open,
                high=max(last.high, quote.price),
                low=min(last.low, quote.price),
                close=quote.price,
                volume=last.volume,
                freq=last.freq,
                currency=quote.currency or last.currency,
                source=quote.source,
            )
            continue
        open_value = quote.day_open if quote.day_open is not None else quote.price
        bars.append(
            PriceBar(
                symbol=symbol,
                market=market,
                source_symbol=quote.source_symbol,
                ts=quote_date,
                open=open_value,
                high=max(open_value, quote.price),
                low=min(open_value, quote.price),
                close=quote.price,
                volume=0.0,
                freq="1d",
                currency=quote.currency,
                source=quote.source,
            )
        )
    return updated


def _load_universe(
    catalog,
    symbols,
    market,
    live=False,
    tf="1d",
    live_symbols: tuple[str, ...] | None = None,
):
    normalized = list(
        dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())
    )
    bars_by_symbol: dict[str, list[PriceBar]] = {}

    if live and tf != "1d":
        for symbol in normalized:
            try:
                bars = _live_fetch(symbol, market, tf)
            except Exception:
                bars = []
            if bars:
                bars_by_symbol[symbol] = bars
        return bars_by_symbol

    for symbol in normalized:
        bars = catalog.get_bars(symbol, market=market)
        if bars:
            bars_by_symbol[symbol] = bars

    if live and market in {"us", "kospi", "kosdaq"}:
        requested_live = set(live_symbols or ())
        quote_symbols = (
            normalized
            if live_symbols is None
            else [symbol for symbol in normalized if symbol in requested_live]
        )
        try:
            quotes = _cached_yahoo_quotes(tuple(quote_symbols), market) if quote_symbols else {}
        except Exception:
            quotes = {}
        bars_by_symbol = _overlay_live_quotes(bars_by_symbol, quotes, market)

    if live:
        for symbol in normalized:
            if symbol in bars_by_symbol:
                continue
            try:
                bars = _live_fetch(symbol, market, tf)
            except Exception:
                bars = []
            if bars:
                bars_by_symbol[symbol] = bars
    return bars_by_symbol


def _load_fundamentals(catalog, symbols, market):
    out = {}
    asof = datetime.now(tz=UTC).replace(tzinfo=None)
    for s in symbols:
        try:
            rows = catalog.get_fundamentals(s, market=market, as_of=asof)
            if rows:
                out[s] = rows[0]
        except Exception:
            pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 탭 1 — 종목선정 (AQR 팩터 + 모멘텀)
# ─────────────────────────────────────────────────────────────────────────────
def _ideal_universe() -> list[str]:
    """검증 정본(IDEAL 메가캡) 유니버스 — evaluate_ticker·PBO 검증과 동일한 풀.

    중복 정의를 피하려고 단일 출처(scripts.aqr_ideal_walkforward.MEGACAPS)에서 가져온다.
    import 실패 시에만 대표 메가캡 8종으로 폴백한다(완전 실패 방지).
    """
    try:
        from scripts.aqr_ideal_walkforward import MEGACAPS

        return list(MEGACAPS)
    except Exception:
        return ["MSFT", "AAPL", "NVDA", "AMZN", "META", "GOOGL", "AVGO", "TSLA"]


def _apply_quick_symbol(picker_key: str, symbol_key: str, market_key: str) -> None:
    symbol = str(st.session_state.get(picker_key) or "").strip().upper()
    if symbol:
        st.session_state[symbol_key] = symbol
        st.session_state[market_key] = "us"


@st.cache_data(show_spinner=False)
def _validated_scan_rows() -> tuple[dict, list[dict]]:
    """검증 정본 스캔 — `python -m scripts.scan_universe`와 동일한 핀드 스냅샷 경로.

    유니버스 전체(106)를 AQR 횡단면으로 1패스 랭크하고, 각 종목의 액션/신뢰도/진입
    플랜을 붙인다. ``in_top_n``(rank ≤ 전략 top_n)이 전략이 실제 매수하는 선정 종목.
    반환은 모두 plain dict — st.cache_data 피클 안전.
    """
    from scripts.evaluate_ticker import (
        DEFAULT_FUNDAMENTALS,
        DEFAULT_PRICES,
        load_universe,
    )
    from valuation.recommendation import load_validated_strategy, scan_universe

    strat = load_validated_strategy()
    bars, funds, asof = load_universe(DEFAULT_PRICES, DEFAULT_FUNDAMENTALS, None)
    results = scan_universe(
        bars_by_symbol=bars,
        fundamentals_by_symbol=funds,
        strategy=strat,
        asof_ts=asof,
    )
    meta = {
        "asof": asof.date().isoformat(),
        "strategy": strat.strategy_id,
        "top_n": int(strat.top_n),
        "universe_size": int(results[0].universe_size) if results else 0,
    }

    def _row(r) -> dict:
        ep = r.entry_plan
        confidence_score = _confidence_score(r.confidence)
        confidence_band = getattr(r.confidence, "band", "—")
        profile = _stock_profile(r.ticker)
        if r.rank is not None:
            selection_reason = (
                f"AQR {r.rank}/{r.universe_size}위 · 백분위 {r.percentile:.0f} · "
                f"{'전략 보유권' if r.in_top_n else f'top-{strat.top_n} 밖'}"
            )
        else:
            selection_reason = "검증 유니버스 밖 · forward edge 미확인"
        risk_parts = []
        if not r.valuation_credible:
            risk_parts.append("DCF 참고용")
        if not r.in_validated_universe:
            risk_parts.append("검증 밖")
        if str(confidence_band).lower() not in {"high", "높음"}:
            risk_parts.append(f"신뢰도 {confidence_band}")
        row = {
            "순위": r.rank,
            "종목": r.ticker,
            "기업": str(profile["name"]),
            "무슨 종목": _short_text(str(profile["business"]), 58),
            "액션": r.action,
            "신뢰도": confidence_band,
            "백분위": round(r.percentile),
            "선정 근거": selection_reason,
            "리스크/제약": " · ".join(risk_parts) if risk_parts else "주요 제약 없음",
            "현재가": round(r.current_price, 2) if r.current_price is not None else None,
            "진입": round(ep.target_entry, 2) if ep else None,
            "손절": round(ep.stop_loss, 2) if ep else None,
            "목표": round(ep.target_exit, 2) if ep else None,
            "합성": round(r.composite, 3) if r.composite is not None else None,
            "모멘텀%": round(r.momentum * 100, 2) if r.momentum is not None else None,
            "가치": round(r.value, 3) if r.value is not None else None,
            "퀄리티": round(r.quality, 3) if r.quality is not None else None,
            "_pick": bool(r.in_top_n),
            "_confidence_score": confidence_score,
            "_reasons": tuple(r.reasons),
            "_composite": r.composite,
            "_momentum": r.momentum,
            "_value": r.value,
            "_quality": r.quality,
            "_profile": profile,
        }
        row["왜 매수"] = _short_text(
            _model_buy_case(row, {"universe_size": r.universe_size, "top_n": strat.top_n}),
            92,
        )
        return row

    return meta, [_row(r) for r in results]


def _quote_state(timestamp: datetime, *, now: datetime | None = None) -> str:
    current = now or datetime.now(tz=UTC)
    moment = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
    age_seconds = max((current - moment.astimezone(UTC)).total_seconds(), 0.0)
    return "LIVE" if age_seconds <= 180 else "시장 마감/지연"


def _live_candidate_rows(
    rows: list[dict[str, Any]], quotes: dict[str, YahooQuote]
) -> list[dict[str, Any]]:
    display: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("종목", ""))
        quote = quotes.get(symbol)
        quote_price = quote.price if quote is not None else _safe_float(row.get("현재가"))
        day_change = None
        if quote is not None and quote.day_open is not None and quote.day_open != 0:
            day_change = quote.price / quote.day_open - 1
        entry = _safe_float(row.get("진입"))
        entry_distance = None
        if quote_price is not None and entry is not None and entry != 0:
            entry_distance = quote_price / entry - 1
        display.append(
            {
                "순위": row.get("순위"),
                "종목": symbol,
                "기업": row.get("기업"),
                "구분": "검증 매수 후보" if row.get("_pick") else "관찰 후보",
                "모델 판단": row.get("액션"),
                "신뢰도": row.get("신뢰도"),
                "실시간가": round(quote_price, 4) if quote_price is not None else None,
                "시가 대비%": round(day_change * 100, 2) if day_change is not None else None,
                "진입가 대비%": round(entry_distance * 100, 2)
                if entry_distance is not None
                else None,
                "진입": row.get("진입"),
                "손절": row.get("손절"),
                "목표": row.get("목표"),
                "시세 상태": _quote_state(quote.timestamp) if quote is not None else "수집 실패",
                "시세 시각": quote.timestamp.strftime("%Y-%m-%d %H:%M UTC")
                if quote is not None
                else "—",
            }
        )
    return display


def _render_live_candidate_table(meta: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    symbols = tuple(str(row["종목"]) for row in rows)
    try:
        quotes = _cached_yahoo_quotes(symbols, "us")
        failure = None
    except Exception as exc:
        quotes = {}
        failure = type(exc).__name__
    display = _live_candidate_rows(rows, quotes)
    live_count = sum(1 for quote in quotes.values() if _quote_state(quote.timestamp) == "LIVE")
    status = st.columns(4)
    status[0].metric("후보 수", f"{len(rows)}개")
    status[1].metric("시세 수집", f"{len(quotes)}/{len(rows)}")
    status[2].metric("실시간", f"{live_count}개")
    status[3].metric("모델 기준일", str(meta.get("asof") or "—"))
    if failure:
        st.warning(f"실시간 시세 배치 수집 실패({failure}). 저장 가격을 표시합니다.")
    st.dataframe(pd.DataFrame(display), width="stretch", hide_index=True)
    st.caption(
        "순위·액션은 핀된 검증 모델, 실시간가는 Yahoo 1분 지표 시세입니다. "
        f"실제 매수 보유권은 Top-{meta.get('top_n', '—')}만 해당하며 나머지는 관찰 후보입니다."
    )


@st.fragment(run_every="15s")
def _render_live_candidate_fragment(meta: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    _render_live_candidate_table(meta, rows)


def _render_validated_scan() -> None:
    """전략이 실제 매수하는 검증 선정(상위 N)을 항상 먼저 보여준다 — '8개' 착시 해소."""
    head = st.columns([5, 1])
    head[0].markdown("#### 검증 선정 — 전략이 실제 매수하는 상위 N")
    if head[1].button("새로고침", key="vscan_refresh", help="스냅샷 갱신 후 재스캔"):
        _validated_scan_rows.clear()
    try:
        meta, rows = _validated_scan_rows()
    except Exception as e:  # 스냅샷 검증 실패 등 — 탭 전체를 죽이지 않음
        st.warning(f"검증 스캔 불가 (핀드 스냅샷 확인 필요): {e}")
        return
    if not rows:
        st.info("스캔 결과가 비어 있습니다.")
        return
    picks = [r for r in rows if r["_pick"]]
    st.caption(
        f"asof {meta['asof']} · 전략 {meta['strategy']} · 유니버스 {meta['universe_size']}개 "
        f"→ 선정 {len(picks)}개 (top_n={meta['top_n']}) · 핀드 스냅샷 기반(재현 가능)"
    )

    scores = [r["_confidence_score"] for r in picks if r.get("_confidence_score") is not None]
    buy_count = sum(1 for r in picks if str(r["액션"]).upper() == "BUY")
    hold_count = sum(1 for r in picks if str(r["액션"]).upper() == "HOLD")
    top_pick = picks[0] if picks else rows[0]
    m = st.columns(4)
    m[0].metric("선정 종목", f"{len(picks)}개")
    m[1].metric("BUY / HOLD", f"{buy_count} / {hold_count}")
    m[2].metric("평균 신뢰도", f"{sum(scores) / len(scores):.0f}%" if scores else "—")
    m[3].metric("최상위", f"{top_pick['종목']} #{top_pick['순위']}")

    st.markdown("#### 실시간 추천·관찰 후보")
    watch_controls = st.columns([1, 1, 3])
    candidate_count = watch_controls[0].select_slider(
        "후보 종목 수",
        options=[7, 15, 25, 40],
        value=25,
        key="vscan_live_count",
    )
    auto_refresh = watch_controls[1].toggle(
        "15초 자동 갱신",
        value=True,
        key="vscan_live_auto",
        help="Yahoo 1분 지표 시세를 15초마다 다시 받습니다.",
    )
    candidate_rows = rows[:candidate_count]
    if auto_refresh:
        _render_live_candidate_fragment(meta, candidate_rows)
    else:
        _render_live_candidate_table(meta, candidate_rows)

    display_cols = [
        "순위",
        "종목",
        "기업",
        "무슨 종목",
        "액션",
        "신뢰도",
        "백분위",
        "왜 매수",
        "리스크/제약",
        "현재가",
        "진입",
        "손절",
        "목표",
    ]
    pick_df = pd.DataFrame([{k: r.get(k) for k in display_cols} for r in picks])
    _render_candidate_list(picks, meta)
    with st.expander("선정 후보 표로 보기"):
        st.dataframe(pick_df, width="stretch", hide_index=True)

    if picks:
        detail_labels = [f"#{r['순위']} {r['종목']} · {r['액션']}" for r in picks]
        detail_label = st.selectbox("선정 종목 상세 근거", detail_labels, key="vscan_detail")
        detail = picks[detail_labels.index(detail_label)]
        st.markdown('<div class="section-kicker">Selection Evidence</div>', unsafe_allow_html=True)
        st.markdown(f"##### {detail['종목']} 선정 근거")
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("AQR 랭크", f"{detail['순위']}/{meta['universe_size']}")
        d2.metric("백분위", f"{detail['백분위']:.0f}")
        d3.metric("액션", str(detail["액션"]))
        d4.metric("신뢰도", str(detail["신뢰도"]))
        st.markdown(f'<p class="evidence-note">{detail["선정 근거"]}</p>', unsafe_allow_html=True)
        _render_stock_brief(str(detail["종목"]), detail, meta)
        c1, c2 = st.columns([1.2, 1])
        with c1:
            st.dataframe(
                pd.DataFrame(
                    _factor_rows(
                        {
                            "composite": detail.get("_composite"),
                            "momentum": detail.get("_momentum"),
                            "value": detail.get("_value"),
                            "quality": detail.get("_quality"),
                        }
                    )
                ),
                width="stretch",
                hide_index=True,
            )
        with c2:
            st.dataframe(
                pd.DataFrame(
                    [
                        {"항목": "현재가", "값": _fmt_num(detail.get("현재가"))},
                        {"항목": "평균 진입", "값": _fmt_num(detail.get("진입"))},
                        {"항목": "손절", "값": _fmt_num(detail.get("손절"))},
                        {"항목": "목표", "값": _fmt_num(detail.get("목표"))},
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
        if detail.get("_reasons"):
            st.markdown("**핵심 근거**")
            for reason in detail["_reasons"][:5]:
                st.markdown(f"- {reason}")

    with st.expander(f"전체 랭킹 {meta['universe_size']}개 보기"):
        usize = max(meta["top_n"], len(rows))
        n = st.slider("표시 개수", meta["top_n"], usize, min(20, usize), key="vscan_top")
        full = []
        for r in rows[:n]:
            row = {k: v for k, v in r.items() if not k.startswith("_")}
            row["선정"] = "선정" if r["_pick"] else ""
            full.append(row)
        st.dataframe(pd.DataFrame(full), width="stretch", hide_index=True)
    st.caption(
        "선정 = 전략의 top-N 보유(실제 매수 대상). 라이브 카탈로그가 아니라 검증 스냅샷을 "
        "쓰므로 `python -m scripts.scan_universe`와 결과가 일치합니다."
    )


def _render_screener(catalog) -> None:
    _render_section_header(
        "종목 선정",
        "검증 전략의 실제 매수 후보와 커스텀 유니버스 랭킹을 같은 기준으로 확인합니다.",
        kicker="Selection",
        side="AQR / Momentum / Entry levels",
    )
    _render_validated_scan()
    st.divider()
    st.markdown("##### 커스텀 유니버스 랭킹 (탐색용 · 라이브/멀티마켓)")
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    with c1:
        uni = st.text_input(
            "유니버스 (쉼표 구분 · US는 빈칸이면 검증 유니버스 전체)",
            "",
            key="scr_uni",
            help=(
                "비워두면 검증된 IDEAL 메가캡 유니버스 전체를 스크리닝합니다 "
                "(evaluate_ticker·PBO 검증과 동일한 풀). 특정 종목만 보려면 쉼표로 입력."
            ),
        )
    with c2:
        market = st.selectbox("시장", ["us", "kospi", "kosdaq", "crypto"], key="scr_mkt")
    with c3:
        lookback = st.number_input("Lookback", 20, 504, 126, key="scr_lb")
    with c4:
        live = st.checkbox(
            "라이브 페치",
            value=True,
            key="scr_live",
            help="저장 이력에 Yahoo 1분 최신가를 합성하고, 데이터가 없으면 외부에서 수집합니다.",
        )
    if not st.button("스크리닝 실행", type="primary", key="scr_run"):
        st.info(
            "유니버스를 입력하고 실행하세요. AQR 합성점수(가치+모멘텀+퀄리티)와 모멘텀 수익률로 랭크합니다."
        )
        return

    syms = [s.strip().upper() for s in uni.split(",") if s.strip()]
    if not syms:
        # 빈칸 → US는 검증 정본 유니버스 전체, 그 외 시장은 명시 입력 요구
        syms = _ideal_universe() if market == "us" else []
        if not syms:
            st.info(f"'{market}' 시장은 기본 유니버스가 없습니다. 종목을 쉼표로 입력하세요.")
            return
        st.caption(f"검증 유니버스 전체 스크리닝: {len(syms)}개 종목")
    with st.spinner("바 데이터 로드 중…"):
        bars = _load_universe(catalog, syms, market, live=live)
    if not bars:
        st.error("데이터를 못 불러왔습니다. 심볼/시장 확인 또는 '라이브 페치' 체크 후 재시도.")
        return
    live_note = " · 1분 최신가 합성" if live and market in {"us", "kospi", "kosdaq"} else ""
    st.caption(f"로드됨: {len(bars)}/{len(syms)} 종목{live_note}")

    rows = []
    # 모멘텀 랭크 (bars만 필요 — 항상 동작)
    try:
        from engine.portfolio import screen_momentum

        mom = {r.symbol: r for r in screen_momentum(bars, lookback=int(lookback))}
    except Exception as e:
        mom = {}
        st.caption(f"모멘텀 계산 스킵: {e}")
    # AQR 합성 (fundamentals 있으면 가치/퀄리티 포함)
    funds = _load_fundamentals(catalog, list(bars), market)
    aqr = {}
    try:
        from strategies.factor_aqr import rank_aqr_factors

        for r in rank_aqr_factors(bars, funds, lookback=int(lookback)):
            aqr[r.symbol] = r
    except Exception as e:
        st.caption(f"AQR 합성 스킵(펀더멘털 부족 가능): {e}")

    for s in bars:
        m = mom.get(s)
        a = aqr.get(s)
        profile = _stock_profile(s)
        if a:
            reason = (
                f"AQR 합성 {a.composite:.3f} · 모멘텀 "
                f"{_fmt_pct(m.lookback_return if m else None)} · 가치 {_fmt_num(a.value, 3)} "
                f"· 퀄리티 {_fmt_num(a.quality, 3)}"
            )
        else:
            reason = f"펀더멘털 부족 · 모멘텀 {_fmt_pct(m.lookback_return if m else None)} 기준"
        rows.append(
            {
                "종목": s,
                "기업": str(profile["name"]),
                "무슨 종목": _short_text(str(profile["business"]), 58),
                "현재가": round(m.close, 2) if m else None,
                "모멘텀%": round(m.lookback_return * 100, 2) if m else None,
                "AQR합성": round(a.composite, 3) if a else None,
                "가치": round(a.value, 3) if a else None,
                "퀄리티": round(a.quality, 3) if a else None,
                "선정 근거": reason,
            }
        )
    df = pd.DataFrame(rows)
    sort_col = "AQR합성" if df["AQR합성"].notna().any() else "모멘텀%"
    df = df.sort_values(sort_col, ascending=False, na_position="last").reset_index(drop=True)
    df.insert(0, "순위", df.index + 1)
    st.dataframe(df, width="stretch", hide_index=True)
    st.caption(
        f"정렬 기준: {sort_col} (펀더멘털 없으면 모멘텀만 — KOSPI/US 펀더멘털은 `trader fundamentals`로 수집)"
    )

    pick = st.selectbox("상세/차트로 볼 종목", [""] + list(df["종목"]), key="scr_pick")
    if pick:
        row = df.loc[df["종목"] == pick].iloc[0].to_dict()
        row["_sort_basis"] = sort_col
        st.markdown(f"##### {pick} 랭킹 근거")
        cols = st.columns(5)
        momentum_pct = _safe_float(row.get("모멘텀%"))
        cols[0].metric("순위", f"#{int(row['순위'])}")
        cols[1].metric("현재가", _fmt_num(row.get("현재가")))
        cols[2].metric("모멘텀", f"{momentum_pct:+.1f}%" if momentum_pct is not None else "—")
        cols[3].metric("AQR 합성", _fmt_num(row.get("AQR합성"), 3))
        cols[4].metric("정렬 기준", sort_col)
        st.markdown(
            f'<p class="evidence-note">{row.get("선정 근거", "—")}</p>', unsafe_allow_html=True
        )
        _render_stock_brief(pick, row, None)
        st.dataframe(
            pd.DataFrame(
                [
                    {"항목": "가치", "값": _fmt_num(row.get("가치"), 3)},
                    {"항목": "퀄리티", "값": _fmt_num(row.get("퀄리티"), 3)},
                    {
                        "항목": "데이터 상태",
                        "값": "AQR 사용" if pd.notna(row.get("AQR합성")) else "모멘텀만 사용",
                    },
                ]
            ),
            width="stretch",
            hide_index=True,
        )

    if pick and st.button(f"{pick} 차트리딩으로", key="scr_to_chart"):
        st.session_state["cr_symbol_in"] = pick
        st.session_state["cr_market_in"] = market
        st.success(f"{pick} 설정 완료. 상단 '차트 리딩' 탭에서 실행하세요.")


# ─────────────────────────────────────────────────────────────────────────────
# 탭 2 — 차트리딩
# ─────────────────────────────────────────────────────────────────────────────
def _feed_summary(
    bars: list[PriceBar], timeframe: str, *, now: datetime | None = None
) -> tuple[str, str, str]:
    latest = bars[-1]
    source = latest.source.lower()
    if "yahoo" in source:
        provider = "Yahoo"
    elif "ccxt" in source:
        provider = "Binance"
    elif "pykrx" in source:
        provider = "KRX / pykrx"
    else:
        provider = latest.source or "catalog"

    if isinstance(latest.ts, datetime):
        moment = latest.ts if latest.ts.tzinfo is not None else latest.ts.replace(tzinfo=UTC)
        moment = moment.astimezone(UTC)
        latest_label = moment.strftime("%m-%d %H:%M UTC")
        current = now or datetime.now(tz=UTC)
        age = max((current - moment).total_seconds(), 0.0)
        limits = {"1m": 180, "5m": 900, "15m": 2_700, "1h": 10_800, "4h": 32_400}
        state = "LIVE" if age <= limits.get(timeframe, 180) else "시장 마감/지연"
    else:
        latest_label = latest.ts.isoformat()
        state = "최근 종가"
    return provider, latest_label, state


def _render_chart_analysis(
    catalog: MarketDataCatalog,
    symbol: str,
    market: str,
    timeframe: str,
    direction: str,
) -> None:
    sym_upper = symbol.strip().upper()
    if not sym_upper or len(sym_upper) > 24:
        st.error("심볼은 1~24자로 입력하세요.")
        return
    live_error: str | None = None
    try:
        bars = _live_fetch(sym_upper, market, timeframe)
    except Exception as exc:
        live_error = type(exc).__name__
        bars = catalog.get_bars(sym_upper, market=market) if timeframe == "1d" else []
    if not bars:
        suffix = f" ({live_error})" if live_error else ""
        st.error(f"최신 바 데이터를 불러오지 못했습니다{suffix}.")
        return
    bars = bars[-300:]
    provider, latest_label, feed_state = _feed_summary(bars, timeframe)
    feed = st.columns(4)
    feed[0].metric("데이터", f"{provider} · {timeframe}")
    feed[1].metric("마지막 바", latest_label)
    feed[2].metric("시세 상태", feed_state)
    feed[3].metric("조회 시각", datetime.now(tz=UTC).strftime("%H:%M:%S UTC"))
    if live_error:
        st.caption(f"외부 수집 실패({live_error})로 저장된 일봉을 표시합니다.")
    elif market != "crypto":
        st.caption(
            "Yahoo 일중 시세는 로컬 분석용 지표값입니다. 주문 직전에는 브로커 호가로 다시 확인합니다."
        )

    import plotly.graph_objects as go

    from engine.chart.fvg import run_fvg
    from engine.chart.order_block import detect_order_blocks
    from engine.chart.read import format_chart_read, read_chart
    from engine.chart.volume_profile import build_volume_profile

    with st.spinner("차트리딩 실행 중…"):
        try:
            chart_read = read_chart(bars, direction=direction)
        except Exception as exc:
            st.error(f"차트리딩 실패: {exc}")
            return

    decision_val = chart_read.decision.value
    confluence = chart_read.confluence
    m1, m2, m3 = st.columns(3)
    m1.markdown(f"### {_badge(decision_val)}", unsafe_allow_html=True)
    m2.metric("컨플루언스", f"{confluence:.1f} / 100")
    m3.metric("추세 bias", getattr(chart_read.trend_bias, "value", str(chart_read.trend_bias)))

    features = chart_read.features or {}
    range_pos = _safe_float(features.get("range_pos"))
    range_label = f"{range_pos * 100:.0f}%" if range_pos is not None else "—"
    active_votes = [c for c in chart_read.contributions if c.weight > 0]
    top_votes = sorted(active_votes, key=lambda c: abs(c.weight), reverse=True)[:8]

    st.markdown("#### 판정 근거")
    if chart_read.vetoed:
        st.error(f"하드 VETO: {features.get('veto_reason') or '진입 금지 조건 발생'}")
    elif decision_val == "AVOID":
        st.warning("컨플루언스가 진입 기준에 미달했습니다. 진입보다 대기/회피가 기본값입니다.")
    elif decision_val == "WAIT_FOR_PULLBACK":
        st.info("방향성은 일부 있으나 즉시 진입보다 되돌림 대기가 우선입니다.")
    else:
        st.success("컨플루언스 기준상 진입 후보입니다. 아래 레벨과 반대 신호를 확인하세요.")

    lv = st.columns(5)
    lv[0].metric("현재가", _fmt_num(bars[-1].close))
    lv[1].metric("진입 구간", _entry_zone_text(chart_read.entry_zone))
    lv[2].metric("무효화", _fmt_num(chart_read.invalidation))
    lv[3].metric("활성 신호", str(features.get("n_active_votes", len(active_votes))))
    lv[4].metric("레인지 위치", range_label)

    if top_votes:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "레이어": c.layer,
                        "신호": c.name,
                        "판정": _signal_direction_label(c.direction),
                        "가중치": round(c.weight, 3),
                        "기여": round(c.weight * c.direction, 3),
                        "메모": c.note,
                    }
                    for c in top_votes
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    if chart_read.reasons:
        st.markdown("**요약 근거**")
        for reason in chart_read.reasons[:5]:
            st.markdown(f"- {reason}")

    ts = [b.ts for b in bars]
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=ts,
                open=[b.open for b in bars],
                high=[b.high for b in bars],
                low=[b.low for b in bars],
                close=[b.close for b in bars],
                name=sym_upper,
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
            )
        ]
    )
    fig.update_layout(
        title=f"{sym_upper} — {timeframe}",
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        height=560,
        margin={"t": 40, "b": 20},
    )
    try:
        for z in run_fvg(bars).active_fvgs:
            if z.mitigated:
                continue
            col = "rgba(38,166,154,0.18)" if z.direction == "bullish" else "rgba(239,83,80,0.18)"
            fig.add_shape(
                type="rect",
                x0=z.ts,
                x1=ts[-1],
                y0=z.zone_low,
                y1=z.zone_high,
                fillcolor=col,
                line={"width": 0},
                layer="below",
            )
    except Exception:
        pass
    try:
        for ob in detect_order_blocks(bars):
            if ob.mitigated:
                continue
            col = "rgba(30,136,229,0.16)" if ob.direction == "bullish" else "rgba(171,71,188,0.16)"
            fig.add_shape(
                type="rect",
                x0=ob.ts,
                x1=ts[-1],
                y0=ob.zone_low,
                y1=ob.zone_high,
                fillcolor=col,
                line={"width": 1, "dash": "dot", "color": "rgba(120,144,200,.5)"},
                layer="below",
            )
    except Exception:
        pass
    try:
        vp = build_volume_profile(bars)
        if not vp.degenerate:
            for y, lbl, w in ((vp.poc_price, "POC", 2), (vp.vah, "VAH", 1), (vp.val, "VAL", 1)):
                fig.add_hline(
                    y=y,
                    line={
                        "color": "rgba(255,235,59,0.6)",
                        "width": w,
                        "dash": "dash" if lbl != "POC" else "solid",
                    },
                    annotation_text=lbl,
                    annotation_position="right",
                )
    except Exception:
        pass
    st.plotly_chart(fig, width="stretch")

    if chart_read.contributions:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "신호": c.name,
                        "레이어": c.layer,
                        "가중치": round(c.weight, 3),
                        "방향": c.direction,
                        "메모": c.note,
                    }
                    for c in chart_read.contributions
                ]
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption("전체 신호: +1 우호, 0 중립, -1 반대. 컨플루언스 점수는 가중 방향 투표입니다.")
    with st.expander("전체 리포트"):
        st.text(format_chart_read(chart_read))


@st.fragment(run_every="15s")
def _render_live_chart_fragment(
    catalog: MarketDataCatalog,
    symbol: str,
    market: str,
    timeframe: str,
    direction: str,
) -> None:
    _render_chart_analysis(catalog, symbol, market, timeframe, direction)


def _render_chart_read_tab(catalog: MarketDataCatalog) -> None:
    _render_section_header(
        "차트 리딩",
        "최신 OHLCV와 SMC/ICT 신호를 컨플루언스, 진입 구간, 무효화 레벨로 분해합니다.",
        kicker="Chart evidence",
        side="1m–1d · 15s refresh",
    )
    st.caption(
        "차트리딩은 백테스트상 38-42% 적중으로 엣지가 검증되지 않았습니다. "
        "종목 선정은 검증 추천을 우선하고 차트는 진입 타이밍에만 사용합니다."
    )
    st.session_state.setdefault("cr_symbol_in", "BTC/USDT")
    st.selectbox(
        "검증 유니버스 빠른 선택",
        [""] + _ideal_universe(),
        key="cr_quick_pick",
        format_func=lambda value: (
            "직접 입력" if not value else f"{value} · {_stock_profile(value)['name']}"
        ),
        on_change=_apply_quick_symbol,
        args=("cr_quick_pick", "cr_symbol_in", "cr_market_in"),
        help="106개 검증 종목을 검색해 바로 차트에 설정합니다.",
    )
    controls = st.columns(4)
    with controls[0]:
        symbol = st.text_input("심볼", key="cr_symbol_in")
    with controls[1]:
        market = st.selectbox("시장", ["crypto", "us", "kospi", "kosdaq"], key="cr_market_in")
    with controls[2]:
        timeframe = st.selectbox("타임프레임", CHART_TIMEFRAMES, index=2, key="cr_tf")
    with controls[3]:
        direction = st.selectbox("방향", ["long", "short"], key="cr_dir")

    actions = st.columns([1, 1, 4])
    run_chart = actions[0].button("최신 차트 실행", type="primary", key="cr_run")
    auto_refresh = actions[1].toggle(
        "15초 자동 갱신",
        value=True,
        key="cr_auto",
        help="외부 데이터 소스에서 최신 봉을 15초마다 다시 받습니다.",
    )
    current_request = (symbol.strip().upper(), market, timeframe, direction)
    if run_chart:
        st.session_state["cr_active_request"] = current_request
    active_request = st.session_state.get("cr_active_request")
    if active_request is None:
        st.info("심볼과 주기를 선택한 뒤 '최신 차트 실행'을 누르세요.")
        return
    if tuple(active_request) != current_request:
        st.info("파라미터가 바뀌었습니다. 최신 차트를 다시 실행하세요.")
        return
    if auto_refresh:
        _render_live_chart_fragment(catalog, *current_request)
    else:
        _render_chart_analysis(catalog, *current_request)


# ─────────────────────────────────────────────────────────────────────────────
# 탭 3 — 추천기 (evaluate_ticker)
# ─────────────────────────────────────────────────────────────────────────────
def _render_recommender(catalog) -> None:
    _render_section_header(
        "추천기",
        "AQR 검증 신호, DCF 참고값, 신뢰도, ATR 진입 사다리를 한 종목 기준으로 재계산합니다.",
        kicker="Single-name review",
        side="106 validated + custom symbols",
    )
    st.session_state.setdefault("rec_tkr", "NVDA")
    st.selectbox(
        "검증 유니버스 빠른 선택",
        [""] + _ideal_universe(),
        key="rec_quick_pick",
        format_func=lambda value: (
            "직접 입력" if not value else f"{value} · {_stock_profile(value)['name']}"
        ),
        on_change=_apply_quick_symbol,
        args=("rec_quick_pick", "rec_tkr", "rec_mkt"),
        help="106개 검증 종목 중 하나를 검색합니다. 검증 밖 종목은 아래 입력칸에 직접 입력할 수 있습니다.",
    )
    c1, c2, c3 = st.columns([2, 1, 3])
    with c1:
        ticker = st.text_input("종목", key="rec_tkr")
    with c2:
        market = st.selectbox("시장", ["us", "kospi", "kosdaq", "crypto"], key="rec_mkt")
    with c3:
        uni = st.text_input(
            "유니버스 컨텍스트 (횡단면 점수용 · 빈칸이면 검증 유니버스 전체)",
            "",
            key="rec_uni",
            help=(
                "비워두면 검증 정본 유니버스(evaluate_ticker와 동일한 풀)를 횡단면 "
                "컨텍스트로 사용합니다. 카탈로그에 펀더멘털이 없으면 모멘텀만으로 점수. "
                "좁히려면 쉼표로 입력."
            ),
        )
    if not st.button("평가 실행", type="primary", key="rec_run"):
        st.info(
            "종목 평가는 유니버스 대비 횡단면 랭크 + DCF 적정가 + ATR 진입 사다리를 산출합니다."
        )
        return
    try:
        from valuation.recommendation import evaluate_ticker, load_validated_strategy

        strategy = load_validated_strategy()
    except Exception as e:
        st.error(f"검증 전략 로드 실패: {e}")
        return
    eval_symbol = ticker.strip().upper()
    if not eval_symbol or len(eval_symbol) > 24:
        st.error("종목 심볼은 1~24자로 입력하세요.")
        return
    syms = [s.strip().upper() for s in uni.split(",") if s.strip()]
    if not syms and market == "us":
        # 빈칸 → 검증 정본 유니버스 전체. 랭크·신뢰도가 evaluate_ticker 파이프라인과 일치.
        syms = _ideal_universe()
    if eval_symbol not in syms:
        syms.append(eval_symbol)
    with st.spinner("유니버스 로드 + 평가 중…"):
        bars = _load_universe(
            catalog,
            syms,
            market,
            live=True,
            live_symbols=(eval_symbol,),
        )
        funds = _load_fundamentals(catalog, list(bars), market)
        live_quote = None
        if market in {"us", "kospi", "kosdaq"}:
            try:
                live_quote = _cached_yahoo_quotes((eval_symbol,), market).get(eval_symbol)
            except Exception:
                live_quote = None
        try:
            ev = evaluate_ticker(
                ticker=eval_symbol,
                bars_by_symbol=bars,
                fundamentals_by_symbol=funds,
                strategy=strategy,
                asof_ts=datetime.now(tz=UTC).replace(tzinfo=None),
                bars=bars.get(eval_symbol, []),
                with_chart=True,
            )
        except Exception as e:
            st.error(f"평가 실패: {e}")
            return
    st.markdown(f"## {ev.ticker} {_badge(ev.action)}", unsafe_allow_html=True)
    m = st.columns(4)
    # ev.confidence 는 ConfidenceBreakdown(score/band) 객체 — 객체 repr 출력 방지.
    _conf = ev.confidence
    _conf_str = f"{_conf.score:.0f}% · {_conf.band}" if hasattr(_conf, "score") else f"{_conf}%"
    m[0].metric("신뢰도", _conf_str)
    m[1].metric("랭크", f"{ev.rank}/{ev.universe_size}" if ev.rank else "—")
    m[2].metric("적정가", f"{ev.fair_value:,.2f}" if ev.fair_value else "—")
    cur = ev.current_price or 0
    disc = ((ev.fair_value - cur) / ev.fair_value * 100) if (ev.fair_value and cur) else None
    m[3].metric("현재가", f"{cur:,.2f}", f"{disc:+.1f}% 할인" if disc is not None else None)
    if live_quote is not None:
        st.caption(
            f"시세 {live_quote.price:,.4f} · {live_quote.timestamp.strftime('%Y-%m-%d %H:%M UTC')} "
            f"· {_quote_state(live_quote.timestamp)} · 평가 대상 Yahoo 1분가 / 비교군 저장 이력"
        )
    f = st.columns(4)
    f[0].metric("합성", f"{ev.composite:.3f}" if ev.composite is not None else "—")
    f[1].metric("모멘텀", f"{ev.momentum * 100:+.1f}%" if ev.momentum is not None else "—")
    f[2].metric("가치", f"{ev.value:.3f}" if ev.value is not None else "—")
    f[3].metric("퀄리티", f"{ev.quality:.3f}" if ev.quality is not None else "—")

    ev_row = {
        "순위": ev.rank,
        "종목": ev.ticker,
        "액션": ev.action,
        "백분위": round(ev.percentile),
        "현재가": ev.current_price,
        "진입": getattr(ev.entry_plan, "target_entry", None),
        "손절": getattr(ev.entry_plan, "stop_loss", None),
        "목표": getattr(ev.entry_plan, "target_exit", None),
    }
    _render_stock_brief(
        ev.ticker,
        ev_row,
        {"universe_size": ev.universe_size, "top_n": strategy.top_n},
    )

    st.markdown("#### 판단 근거")
    if ev.in_top_n:
        st.success("검증 전략의 top-N 보유권 안에 들어온 종목입니다.")
    elif ev.in_validated_universe:
        st.info("검증 유니버스 안에 있지만 현재 전략 보유권 밖입니다.")
    else:
        st.warning("검증 유니버스 밖 종목입니다. 신뢰도 상한이 적용됩니다.")

    c1, c2 = st.columns([1.1, 1])
    with c1:
        st.dataframe(
            pd.DataFrame(
                _factor_rows(
                    {
                        "composite": ev.composite,
                        "momentum": ev.momentum,
                        "value": ev.value,
                        "quality": ev.quality,
                    }
                )
            ),
            width="stretch",
            hide_index=True,
        )
    with c2:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "항목": "검증 유니버스",
                        "값": "포함" if ev.in_validated_universe else "미포함",
                    },
                    {"항목": "전략 보유권", "값": "포함" if ev.in_top_n else "미포함"},
                    {
                        "항목": "밸류에이션",
                        "값": "참고 가능" if ev.valuation_credible else "참고용",
                    },
                    {"항목": "평가 기준일", "값": ev.as_of.strftime("%Y-%m-%d")},
                ]
            ),
            width="stretch",
            hide_index=True,
        )

    if ev.reasons:
        st.markdown("**추천 근거**")
        for reason in ev.reasons[:6]:
            st.markdown(f"- {reason}")
    if ev.chart_summary:
        cs = ev.chart_summary
        st.markdown("#### 차트 타이밍 (참고)")
        cm = st.columns(4)
        cm[0].metric("차트 판정", cs.decision)
        cm[1].metric("컨플루언스", f"{cs.confluence:.0f}/100")
        cm[2].metric("추세", cs.trend_bias)
        cm[3].metric("방향", cs.direction)
        st.caption("차트 타이밍은 추천 액션/신뢰도/랭크에 반영하지 않는 참고 정보입니다.")

    if ev.entry_plan:
        ep = ev.entry_plan
        st.markdown("#### 진입 플랜 (ATR 사다리)")
        st.dataframe(pd.DataFrame(_entry_plan_rows(ep)), width="stretch", hide_index=True)
        try:
            ladder = json.loads(ep.ladder_json)
        except (TypeError, ValueError):
            ladder = []
        if ladder:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "단계": i + 1,
                            "가격": _fmt_num(step.get("price")),
                            "비중": _fmt_pct(step.get("weight"), signed=False),
                            "근거": step.get("reason", ""),
                        }
                        for i, step in enumerate(ladder)
                    ]
                ),
                width="stretch",
                hide_index=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 탭 4 — 검증결과
# ─────────────────────────────────────────────────────────────────────────────
def _render_validation() -> None:
    _render_section_header(
        "검증 결과",
        "chartbloom 실증 문서와 백테스트 산출물을 확인합니다.",
        kicker="Validation",
        side="Reports / CSV outputs",
    )
    sub1, sub2 = st.tabs(["chartbloom A-1/B 검증", "백테스트 산출물 (out/*.csv)"])
    with sub1:
        md = MERR / "CHARTBLOOM_VALIDATION_RESULTS.md"
        if md.exists():
            st.markdown(md.read_text(encoding="utf-8"))
        else:
            st.info(f"검증 문서 없음: {md}")
    with sub2:
        csvs = sorted(OUT_DIR.glob("*.csv")) if OUT_DIR.exists() else []
        if not csvs:
            st.info("out/ 에 백테스트 CSV 없음. `trader walk-forward --csv-output …` 등으로 생성.")
            return
        pick = st.selectbox("백테스트 파일", [c.name for c in csvs], key="val_csv")
        try:
            df = pd.read_csv(OUT_DIR / pick)
            st.caption(f"{pick} — {len(df)} 행 × {len(df.columns)} 열")
            num = df.select_dtypes("number")
            eq = [
                c
                for c in num.columns
                if any(k in c.lower() for k in ("equity", "cum", "nav", "return"))
            ]
            if eq:
                st.line_chart(num[eq])
            st.dataframe(df.tail(200), width="stretch", hide_index=True)
        except Exception as e:
            st.error(f"로드 실패: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 탭 5 — 예측 (금리/CPI)
# ─────────────────────────────────────────────────────────────────────────────
def _latest_rate_forecast(region: str) -> dict | None:
    """포워드-OOS 원장에 사전 기록된 최신 금리 예측을 읽어 표시용 dict 로 변환.

    라이브 재계산(collect_signals)은 FRED/ECOS 키·네트워크가 필요하므로, 대시보드는
    cron(`rate-record`)이 회의 전에 기록해 둔 원장 값을 보여준다(오프라인·재현 가능).
    """
    led = ROOT / "trading-copilot" / "out" / "rate_ledger.jsonl"
    if not led.exists():
        led = OUT_DIR / "rate_ledger.jsonl"
    if not led.exists():
        return None
    try:
        rows = [json.loads(x) for x in led.read_text().splitlines() if x.strip()]
    except Exception:
        return None
    # '다음 회의'로 보일 자격이 없는 행을 제외한다:
    #   (1) 이미 채점된 (region, meeting) — rate_forecast 행은 채점 후에도 남음(_scored_keys 미러링)
    #   (2) 회의일이 오늘 이전 — 채점 지연 중인 과거 회의(ISO 날짜는 사전식=시간순 비교)
    today_iso = datetime.now(tz=UTC).date().isoformat()
    scored = {(r.get("region"), r.get("meeting")) for r in rows if r.get("kind") == "rate_score"}
    for row in reversed(rows):  # 최신 기록 우선
        if row.get("kind") != "rate_forecast" or row.get("region") != region:
            continue
        meeting = row.get("meeting", "")
        if (region, meeting) in scored or meeting < today_iso:
            continue
        p = row.get("probs", {}) or {}
        return {
            "date": row.get("meeting", "—"),
            "modal_decision": row.get("modal", "—"),
            "cut_prob": p.get("cut", 0),
            "hold_prob": p.get("hold", 0),
            "hike_prob": p.get("hike", 0),
            "recorded_at": row.get("recorded_at", "—"),
            "status": row.get("status", "—"),
        }
    return None


def _render_forecast() -> None:
    _render_section_header(
        "예측",
        "기준금리 결정 확률과 CPI 관련 원장을 확인합니다.",
        kicker="Forecast",
        side="Rate ledger / CPI",
    )
    region = st.radio("지역", ["us", "kr"], horizontal=True, key="fc_region")
    if st.button("금리 결정 예측 실행", type="primary", key="fc_rate"):
        r = _latest_rate_forecast(region)
        if not r:
            st.warning(
                f"'{region.upper()}' 기록된 금리 예측이 없습니다 — "
                "cron(`rate-record`)이 적재 중이거나 trading-copilot 에서 생성하세요."
            )
        else:
            import plotly.graph_objects as go

            probs = {
                "인하": r.get("cut_prob", 0),
                "동결": r.get("hold_prob", 0),
                "인상": r.get("hike_prob", 0),
            }
            c1, c2 = st.columns([1, 2])
            c1.metric("다음 회의", str(r.get("date", "—")))
            c1.metric("최빈 결정", str(r.get("modal_decision", "—")))
            fig = go.Figure(
                [
                    go.Bar(
                        x=list(probs),
                        y=[v * 100 for v in probs.values()],
                        marker_color=["#2c6d5c", "#8a6726", "#a34842"],
                    )
                ]
            )
            fig.update_layout(
                template="plotly_dark",
                height=320,
                yaxis_title="확률 %",
                title=f"{region.upper()} 기준금리 결정 확률",
            )
            c2.plotly_chart(fig, width="stretch")
            st.caption(
                f"기록일 {r.get('recorded_at', '—')} · 상태 {r.get('status', '—')} "
                "— 포워드-OOS 원장에 회의 전 사전 기록된 예측(재현 가능)."
            )
    st.divider()
    # 원장 트랙레코드
    led = ROOT / "trading-copilot" / "out" / "rate_ledger.jsonl"
    if not led.exists():
        led = OUT_DIR / "rate_ledger.jsonl"
    if led.exists():
        try:
            rows = [json.loads(x) for x in led.read_text().splitlines() if x.strip()]
            st.caption(f"금리 예측 원장: {len(rows)} 기록")
            st.dataframe(pd.DataFrame(rows).tail(50), width="stretch", hide_index=True)
        except Exception as e:
            st.caption(f"원장 로드 스킵: {e}")
    else:
        st.info("금리 원장 없음 — cron(`rate-record`)이 적재 중.")


# ─────────────────────────────────────────────────────────────────────────────
# 탭 6 — 페이퍼 원장 (forward-OOS)
# ─────────────────────────────────────────────────────────────────────────────
def _render_ledgers() -> None:
    _render_section_header(
        "페이퍼 원장",
        "forward-OOS 신호와 채점 전후 기록을 누적 원장 기준으로 확인합니다.",
        kicker="Forward OOS",
        side="Paper trail",
    )
    leds = []
    if OUT_DIR.exists():
        leds = sorted(OUT_DIR.glob("*ledger*.jsonl")) + sorted(OUT_DIR.glob("*oos*.jsonl"))
    leds = sorted(set(leds))
    if not leds:
        st.info(
            "out/ 에 원장 없음. cron(chart/chartbloom/paper)이 적재 중. 아직 fresh 신호 대기일 수 있음."
        )
        return
    pick = st.selectbox("원장 파일", [p.name for p in leds], key="led_pick")
    path = OUT_DIR / pick
    try:
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    except Exception as e:
        st.error(f"로드 실패: {e}")
        return
    st.metric("기록 수", len(rows))
    if "chartbloom" in pick and rows:
        wf = sum(1 for r in rows if r.get("has_fvg"))
        c = st.columns(2)
        c[0].metric("CHoCH+FVG", wf)
        c[1].metric("CHoCH-noFVG", len(rows) - wf)
        st.caption(
            "성숙분 채점: `python -m scripts.chartbloom_paper_score --tf 4h` (수 주 누적 후 spread 판정)"
        )
    if rows:
        st.dataframe(pd.DataFrame(rows).tail(100), width="stretch", hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# 탭 7 — RAG 챗봇
# ─────────────────────────────────────────────────────────────────────────────
def _render_rag() -> None:
    _render_section_header(
        "RAG",
        "경제 분석 코퍼스와 로컬 챗봇 상태를 확인합니다.",
        kicker="Research assistant",
        side="localhost:8800",
    )
    import socket

    up = False
    try:
        with socket.create_connection(("127.0.0.1", 8800), timeout=1):
            up = True
    except OSError:
        up = False
    if up:
        import streamlit.components.v1 as components

        st.success(f"RAG 서버 가동 중: {RAG_URL}")
        components.iframe(RAG_URL, height=720, scrolling=True)
    else:
        st.warning("RAG 서버 미가동. 아래 명령으로 띄운 뒤 새로고침하세요:")
        st.code('cd "/Users/jjuni/재무관리 모델/merr_corpus/rag" && ./run.sh', language="bash")
        st.markdown(f"또는 별도 브라우저 탭에서 [{RAG_URL}]({RAG_URL}) 열기.")


# ─────────────────────────────────────────────────────────────────────────────
# 탭 8 — 실거래 콘솔 (로컬 웹 → CLI 게이트)
# ─────────────────────────────────────────────────────────────────────────────
def _run_trader_command(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Run trader.cli in-process with a temporary env overlay and captured output."""
    from trader import cli as trader_cli

    old_env: dict[str, str | None] = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = trader_cli.main(argv)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 1
    except Exception as exc:  # noqa: BLE001 - render command failures inside the local console.
        code = 1
        stderr.write(f"{exc.__class__.__name__}: {exc}\n")
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    output = stdout.getvalue()
    err = stderr.getvalue()
    if err:
        output = f"{output}\n[stderr]\n{err}".strip()
    return code, output.strip()


def _env_default(name: str, fallback: str) -> str:
    value = os.getenv(name, "").strip()
    return value if value else fallback


def _render_command_result(title: str, result: tuple[int, str] | None) -> None:
    if result is None:
        return
    code, output = result
    if code == 0:
        st.success(f"{title}: PASS")
    else:
        st.error(f"{title}: exit {code}")
    st.code(output or "(no output)", language="markdown")


def _live_console_env(
    *,
    broker: str,
    strategy_id: str,
    max_capital: float,
    policy_version: str,
    account_id: str,
    cash: float,
    equity: float,
    buying_power: float,
    market_open: bool,
    positions: str,
) -> dict[str, str]:
    return {
        "LIVE_TRADING_ENABLED": "true",
        "LIVE_TRADING_ACK_RISK": "true",
        "LIVE_ORDER_SUBMISSION_ENABLED": "true",
        "LIVE_STRATEGY_ID": strategy_id.strip(),
        "LIVE_BROKER": broker,
        "LIVE_MAX_CAPITAL": f"{max_capital:.8f}",
        "LIVE_POLICY_VERSION": policy_version.strip(),
        "LIVE_MANUAL_ACCOUNT_ID": account_id.strip(),
        "LIVE_MANUAL_CASH": f"{cash:.8f}",
        "LIVE_MANUAL_EQUITY": f"{equity:.8f}",
        "LIVE_MANUAL_BUYING_POWER": f"{buying_power:.8f}",
        "LIVE_MANUAL_MARKET_OPEN": "true" if market_open else "false",
        "LIVE_MANUAL_POSITIONS": positions.strip(),
    }


def _live_ticket_args(
    *,
    symbol: str,
    side: str,
    qty: float,
    quote: float,
    limit_price: float,
    order_key: str,
    as_of: str,
    stop_loss: float | None,
    target_exit: float | None,
    verify_only: bool,
) -> list[str]:
    args = [
        "live-ticket",
        symbol,
        "--side",
        side,
        "--qty",
        f"{qty:.8f}",
        "--price",
        f"{quote:.8f}",
        "--order-type",
        "limit",
        "--limit-price",
        f"{limit_price:.8f}",
        "--rebalance-key",
        order_key,
        "--as-of",
        as_of,
        "--catalog-db",
        str(LIVE_CATALOG_PATH),
        "--ticket-log",
        str(MANUAL_TICKET_LOG),
        "--halt-state",
        str(LIVE_HALT_STATE),
        "--equity-state",
        str(LIVE_EQUITY_STATE),
    ]
    args.append("--verify-only" if verify_only else "--ack-manual-ticket")
    if (
        side == "buy"
        and stop_loss is not None
        and target_exit is not None
        and 0 < stop_loss < limit_price < target_exit
    ):
        args += [
            "--stop-loss",
            f"{stop_loss:.8f}",
            "--target-exit",
            f"{target_exit:.8f}",
        ]
    return args


def _order_gate_fingerprint(argv: list[str], env: dict[str, str]) -> str:
    mode_flags = {"--verify-only", "--ack-manual-ticket"}
    payload = {
        "argv": [value for value in argv if value not in mode_flags],
        "env": {key: env[key] for key in sorted(env)},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _order_gate_passed(
    *, prerequisites: bool, result: tuple[int, str] | None, is_current: bool
) -> bool:
    return bool(prerequisites and is_current and result is not None and result[0] == 0)


def _order_gate_receipt_is_current(
    receipt: object,
    fingerprint: str,
    *,
    now: datetime | None = None,
) -> bool:
    if not isinstance(receipt, dict) or receipt.get("fingerprint") != fingerprint:
        return False
    checked_at = receipt.get("checked_at_epoch")
    if not isinstance(checked_at, int | float):
        return False
    current = now or datetime.now(UTC)
    age_seconds = current.timestamp() - float(checked_at)
    return 0 <= age_seconds <= LIVE_ORDER_GATE_TTL_SECONDS


def _order_gate_rows(
    *,
    recommendation_status: str,
    quote_attested: bool,
    result: tuple[int, str] | None,
    is_current: bool,
) -> list[dict[str, str]]:
    local_rows = [
        {
            "검증 항목": "추천 적합성",
            "상태": "PASS" if recommendation_status != "BLOCK" else "BLOCK",
            "판정 기준": "검증 전략 보유권과 주문안 actionable 상태",
        },
        {
            "검증 항목": "브로커 가격 대조",
            "상태": "PASS" if quote_attested else "BLOCK",
            "판정 기준": "운영자가 외부 브로커 현재가를 직접 확인",
        },
    ]
    remote_rows = [
        ("환경·정책", ("[live-policy:", "order submission")),
        ("모델·OOS 증거", ("[model-gate:", "[live-drill:", "[paper-oos:")),
        ("브로커·시장", ("[broker-preflight:", "market is closed", "account is blocked")),
        ("중지·킬스위치", ("[halt:", "halted:", "kill-switch")),
        ("가격·카탈로그", ("[price:", "[mark:", "deviates", "fresh mark")),
        (
            "주문·노출 한도",
            ("risk_block", "notional", "buying power", "cash", "exposure", "weight", "daily"),
        ),
        ("섹터·보호가격", ("[sectors:", "[protection:", "stop-loss", "target-exit")),
    ]
    if result is None:
        remote_status = "대기"
        output = ""
    elif not is_current:
        remote_status = "재검증"
        output = ""
    elif result[0] == 0:
        remote_status = "PASS"
        output = result[1].lower()
    else:
        remote_status = "검토"
        output = result[1].lower()
    rows = list(local_rows)
    for label, blockers in remote_rows:
        status = remote_status
        if (
            result is not None
            and is_current
            and result[0] != 0
            and any(token in output for token in blockers)
        ):
            status = "BLOCK"
        rows.append(
            {
                "검증 항목": label,
                "상태": status,
                "판정 기준": "CLI fail-closed 주문 게이트",
            }
        )
    return rows


def _latest_rebalance_plan(strategy_id: str) -> tuple[Path, dict[str, Any]] | None:
    candidates = sorted(
        OUT_DIR.glob(f"rebalance-plan-{strategy_id}-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            if path.stat().st_size > 5_000_000:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("strategy_id") == strategy_id:
            return path, payload
    return None


def _plan_recommendation(plan: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in plan.get("recommendations", []):
        if isinstance(row, dict) and str(row.get("symbol", "")).upper() == symbol.upper():
            return row
    return {}


def _plan_report_path(strategy_id: str) -> Path:
    return OUT_DIR / f"paper-drill-orders-{strategy_id}.md"


def _render_plan_overview(path: Path, plan: dict[str, Any]) -> None:
    recommendations = [row for row in plan.get("recommendations", []) if isinstance(row, dict)]
    intents = [row for row in plan.get("intents", []) if isinstance(row, dict)]
    risk_to_stop = sum(float(row.get("risk_to_stop") or 0.0) for row in recommendations)
    nav = float(plan.get("nav") or 0.0)
    metrics = st.columns(4)
    metrics[0].metric("데이터 기준일", str(plan.get("data_as_of") or "—"))
    metrics[1].metric("추천 / 주문", f"{len(recommendations)} / {len(intents)}")
    metrics[2].metric("손절 위험", _fmt_pct(risk_to_stop / nav, signed=False) if nav > 0 else "—")
    metrics[3].metric("전체 게이트", "PASS" if plan.get("all_pass") else "BLOCK")
    st.caption(
        f"{path.name} · 가격 {plan.get('price_source', '—')} · 펀더멘털 "
        f"{plan.get('fundamentals_source', '—')}"
    )
    if recommendations:
        display = []
        for row in recommendations:
            display.append(
                {
                    "순위": row.get("rank"),
                    "종목": row.get("symbol"),
                    "판단": row.get("decision"),
                    "신뢰도": f"{float(row.get('confidence_score') or 0):.0f} · {row.get('confidence_band', '—')}",
                    "목표비중": _fmt_pct(row.get("target_weight"), signed=False),
                    "목표수량": _fmt_num(row.get("target_qty"), 6),
                    "실행가": _fmt_num(row.get("execution_limit")),
                    "참고진입": _fmt_num(row.get("advisory_entry")),
                    "손절": _fmt_num(row.get("stop_loss")),
                    "손절 기준": row.get("stop_basis"),
                    "목표": _fmt_num(row.get("target_exit")),
                    "상태": "PASS" if row.get("actionable") else "BLOCK",
                }
            )
        st.dataframe(pd.DataFrame(display), width="stretch", hide_index=True)

    report_path = _plan_report_path(str(plan.get("strategy_id", "")))
    actions = st.columns([1, 1, 4])
    actions[0].download_button(
        "JSON 리포트",
        data=json.dumps(plan, ensure_ascii=False, indent=2),
        file_name=path.name,
        mime="application/json",
        width="stretch",
    )
    if report_path.exists():
        actions[1].download_button(
            "매매 리포트",
            data=report_path.read_text(encoding="utf-8"),
            file_name=report_path.name,
            mime="text/markdown",
            width="stretch",
        )


def _render_live_console() -> None:
    _render_section_header(
        "실거래 콘솔",
        "검증 전략의 최신 추천 리포트를 만들고, 선택한 델타 주문을 보호가격 포함 티켓으로 전환합니다.",
        kicker="Live operations",
        side="Recommendation → risk gate → ticket",
    )

    setup_left, setup_mid, setup_right = st.columns([1, 1, 2])
    with setup_left:
        top_n = st.selectbox("검증 전략", [7, 5], format_func=lambda value: f"AQR Top-{value}")
    with setup_mid:
        whole_shares = st.toggle("정수주만", value=False, help="분할주 미지원 브로커에서만 켭니다.")
    strategy_id = f"aqr_top{top_n}_cap20_trail10_pit110"
    with setup_right:
        plan_args = [
            "rebalance-plan",
            "--top-n",
            str(top_n),
            "--strategy-id",
            strategy_id,
            "--no-record-oos",
            "--preview-only",
        ]
        if whole_shares:
            plan_args.append("--whole-shares")
        if st.button("최신 추천·주문안 생성", type="primary", width="stretch"):
            with st.spinner("최신 가격, PIT 펀더멘털, 리스크 게이트를 계산하는 중입니다."):
                st.session_state["lc_plan_result"] = _run_trader_command(plan_args, {})

    plan_ref = _latest_rebalance_plan(strategy_id)
    plan_path: Path | None = None
    plan: dict[str, Any] = {}
    if plan_ref is not None:
        plan_path, plan = plan_ref
        _render_plan_overview(plan_path, plan)
    else:
        st.warning(
            "선택한 전략의 운용안이 없습니다. 위 버튼으로 최신 추천·주문안을 먼저 생성하세요."
        )

    if st.session_state.get("lc_plan_result") is not None:
        with st.expander("운용안 생성 로그"):
            _render_command_result("추천·주문안", st.session_state.get("lc_plan_result"))

    manual_override = st.toggle("직접 주문 입력", value=False, key="lc_manual_override")
    intents = [row for row in plan.get("intents", []) if isinstance(row, dict)]
    selected_intent: dict[str, Any] = {}
    selected_recommendation: dict[str, Any] = {}
    if intents and not manual_override:
        labels = [
            f"{str(row.get('side', '')).upper()} {row.get('symbol')} · "
            f"{_fmt_num(row.get('qty'), 6)}주 @ {_fmt_num(row.get('limit_price'))}"
            for row in intents
        ]
        selected_label = st.selectbox("실행할 추천 주문", labels, key="lc_plan_order")
        selected_intent = intents[labels.index(selected_label)]
        selected_recommendation = _plan_recommendation(plan, str(selected_intent.get("symbol", "")))

    if selected_intent:
        symbol = str(selected_intent.get("symbol", "")).upper()
        side = str(selected_intent.get("side", "buy"))
        qty = float(selected_intent.get("qty") or 0.0)
        limit_price = float(selected_intent.get("limit_price") or 0.0)
        stop_loss = _safe_float(selected_recommendation.get("stop_loss"))
        target_exit = _safe_float(selected_recommendation.get("target_exit"))
        order_key = str(plan.get("rebalance_key") or datetime.now(tz=UTC).date().isoformat())
        order_status = (
            "PASS"
            if (side == "sell" or selected_recommendation.get("actionable", False))
            else "BLOCK"
        )
        order_metrics = st.columns(6)
        order_metrics[0].metric("종목", symbol)
        order_metrics[1].metric("방향", side.upper())
        order_metrics[2].metric("수량", _fmt_num(qty, 4))
        order_metrics[3].metric("실행가", _fmt_num(limit_price))
        order_metrics[4].metric(
            "손절 / 목표", f"{_fmt_num(stop_loss, 0)} / {_fmt_num(target_exit, 0)}"
        )
        order_metrics[5].metric("추천 게이트", order_status)
        if selected_recommendation:
            brief_row = {
                "순위": selected_recommendation.get("rank"),
                "종목": symbol,
                "액션": selected_recommendation.get("action"),
                "백분위": round(
                    100
                    - 100
                    * (float(selected_recommendation.get("rank") or 1) - 1)
                    / max(float(selected_recommendation.get("universe_size") or 1) - 1, 1),
                    1,
                ),
                "현재가": selected_recommendation.get("current_price"),
                "진입": selected_recommendation.get("execution_limit"),
                "참고진입": selected_recommendation.get("advisory_entry"),
                "손절": selected_recommendation.get("stop_loss"),
                "목표": selected_recommendation.get("target_exit"),
                "_entry_label": "실행 지정가",
            }
            _render_stock_brief(
                symbol,
                brief_row,
                {
                    "universe_size": selected_recommendation.get("universe_size"),
                    "top_n": top_n,
                },
            )
    else:
        input_cols = st.columns(4)
        symbol = input_cols[0].text_input("심볼", "QQQ", key="lc_symbol").strip().upper()
        side = input_cols[1].selectbox("방향", ["buy", "sell"], key="lc_side")
        qty = float(input_cols[2].number_input("수량", min_value=0.0, value=1.0, step=0.001))
        limit_price = float(
            input_cols[3].number_input("지정가", min_value=0.0, value=100.0, step=0.01)
        )
        stop_loss = None
        target_exit = None
        order_key = datetime.now(tz=UTC).date().isoformat()
        order_status = "MANUAL"

    st.markdown("#### 계좌 및 브로커 확인")
    left, mid, right = st.columns(3)
    with left:
        broker = st.selectbox("브로커", ["manual-paper", "manual-live"], key="lc_broker")
        policy_version = st.text_input(
            "정책 버전", _env_default("LIVE_POLICY_VERSION", "manual-web-v2"), key="lc_policy"
        )
        max_capital = st.number_input(
            "최대 운용자본",
            min_value=0.0,
            value=float(plan.get("nav") or _env_default("LIVE_MAX_CAPITAL", "10000")),
        )
    with mid:
        account_id = st.text_input(
            "계좌 ID", _env_default("LIVE_MANUAL_ACCOUNT_ID", "manual-local"), key="lc_account"
        )
        cash = st.number_input(
            "현금", min_value=0.0, value=float(_env_default("LIVE_MANUAL_CASH", "100000"))
        )
        equity = st.number_input(
            "평가자산", min_value=0.0, value=float(_env_default("LIVE_MANUAL_EQUITY", "100000"))
        )
        buying_power = st.number_input(
            "매수가능금액",
            min_value=0.0,
            value=float(_env_default("LIVE_MANUAL_BUYING_POWER", "100000")),
        )
    with right:
        as_of = st.date_input("가격 기준일", datetime.now(tz=UTC).date(), key="lc_asof").isoformat()
        quote_key = str(selected_intent.get("client_order_id") or f"manual-{symbol}")
        quote = st.number_input(
            "외부 브로커 현재가",
            min_value=0.0,
            value=limit_price if limit_price > 0 else 100.0,
            step=0.01,
            key=f"lc_quote_{quote_key}",
            help="실거래에서는 브로커 화면의 현재가와 대조합니다. 계획가는 자동으로 채워집니다.",
        )
        quote_attested = st.checkbox(
            "브로커 화면의 가격과 대조했습니다",
            value=False,
            key=f"lc_quote_ack_{quote_key}",
        )
        market_open = st.toggle("시장 열림", value=True, key="lc_open")
        positions = st.text_input(
            "현재 포지션", _env_default("LIVE_MANUAL_POSITIONS", ""), key="lc_pos"
        )

    env = _live_console_env(
        broker=broker,
        strategy_id=strategy_id,
        max_capital=max_capital,
        policy_version=policy_version,
        account_id=account_id,
        cash=cash,
        equity=equity,
        buying_power=buying_power,
        market_open=market_open,
        positions=positions,
    )

    verify_args = _live_ticket_args(
        symbol=symbol,
        side=side,
        qty=qty,
        quote=quote,
        limit_price=limit_price,
        order_key=order_key,
        as_of=as_of,
        stop_loss=stop_loss,
        target_exit=target_exit,
        verify_only=True,
    )
    ticket_args = _live_ticket_args(
        symbol=symbol,
        side=side,
        qty=qty,
        quote=quote,
        limit_price=limit_price,
        order_key=order_key,
        as_of=as_of,
        stop_loss=stop_loss,
        target_exit=target_exit,
        verify_only=False,
    )
    gate_fingerprint = _order_gate_fingerprint(ticket_args, env)
    gate_result = st.session_state.get("lc_gate_result")
    gate_receipt = st.session_state.get("lc_gate_receipt")
    gate_is_current = _order_gate_receipt_is_current(gate_receipt, gate_fingerprint)
    gate_prerequisites = bool(
        quote_attested
        and math.isfinite(quote)
        and quote > 0
        and math.isfinite(qty)
        and qty > 0
        and math.isfinite(limit_price)
        and limit_price > 0
        and order_status != "BLOCK"
    )
    gate_passed = _order_gate_passed(
        prerequisites=gate_prerequisites,
        result=gate_result if isinstance(gate_result, tuple) else None,
        is_current=gate_is_current,
    )

    st.divider()
    st.markdown("#### 주문 검증 게이트")
    st.caption(
        "현재 주문·계좌·가격을 하나의 검증 단위로 잠급니다. 통과 후 값이 하나라도 바뀌면 "
        "티켓 발행 권한이 자동으로 폐기되며, 통과는 5분 동안만 유효합니다."
    )
    gate_metrics = st.columns(4)
    if gate_passed:
        gate_label = "PASS"
    elif gate_result is not None and gate_is_current:
        gate_label = "BLOCK"
    elif gate_result is not None:
        gate_label = "재검증"
    else:
        gate_label = "대기"
    gate_metrics[0].metric("전체 게이트", gate_label)
    gate_metrics[1].metric("추천 주문", order_status)
    gate_metrics[2].metric("가격 대조", "PASS" if quote_attested else "BLOCK")
    gate_metrics[3].metric("주문 지문", gate_fingerprint[:8].upper())
    st.dataframe(
        pd.DataFrame(
            _order_gate_rows(
                recommendation_status=order_status,
                quote_attested=quote_attested,
                result=gate_result if isinstance(gate_result, tuple) else None,
                is_current=gate_is_current,
            )
        ),
        width="stretch",
        hide_index=True,
    )
    if gate_result is not None and not gate_is_current:
        st.warning(
            "검증 이후 주문·계좌 값이 변경되었거나 5분이 지났습니다. 전체 게이트를 다시 실행하세요."
        )
    elif gate_result is not None and gate_is_current and not gate_passed:
        st.error("현재 주문은 검증 게이트를 통과하지 못했습니다. 차단 근거를 확인하세요.")
    elif gate_passed and isinstance(gate_receipt, dict):
        st.success(f"검증 통과 · {gate_receipt.get('checked_at', '방금')} · 현재 입력에만 유효")

    controls = st.columns([1, 1, 2])
    with controls[0]:
        if st.button(
            "전체 검증 게이트 실행",
            type="primary",
            width="stretch",
            disabled=not gate_prerequisites,
        ):
            price_result = _run_trader_command(
                [
                    "live-price-ingest",
                    symbol,
                    "--source",
                    "external",
                    "--price",
                    f"{quote:.8f}",
                    "--price-as-of",
                    as_of,
                    "--ack-external-price",
                    "--catalog-db",
                    str(LIVE_CATALOG_PATH),
                ],
                env,
            )
            st.session_state["lc_price_result"] = price_result
            if price_result[0] == 0:
                gate_result = _run_trader_command(verify_args, env)
            else:
                gate_result = (
                    price_result[0],
                    "# Manual Order Verification Gate\n\n"
                    "BLOCKED: [price:external] broker-attested price registration failed.\n\n"
                    + price_result[1],
                )
            st.session_state["lc_gate_result"] = gate_result
            st.session_state["lc_gate_receipt"] = {
                "fingerprint": gate_fingerprint,
                "code": gate_result[0],
                "checked_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "checked_at_epoch": datetime.now(UTC).timestamp(),
            }
            st.rerun()
    with controls[1]:
        if st.button(
            "검증 통과 티켓 발행",
            width="stretch",
            disabled=not gate_passed,
        ):
            st.session_state["lc_ticket_result"] = _run_trader_command(ticket_args, env)
            st.session_state.pop("lc_gate_receipt", None)
            st.rerun()
    with controls[2]:
        st.caption(
            "게이트는 티켓을 생성하지 않습니다. 티켓 발행 시 동일 검사를 한 번 더 실행해 "
            "검증과 실행 사이의 상태 변경도 차단합니다."
        )

    for title, key in (
        ("브로커 가격 등록", "lc_price_result"),
        ("주문 검증 게이트", "lc_gate_result"),
        ("주문 티켓", "lc_ticket_result"),
    ):
        if st.session_state.get(key) is not None:
            with st.expander(f"{title} 결과", expanded=key == "lc_ticket_result"):
                _render_command_result(title, st.session_state.get(key))

    if MANUAL_TICKET_LOG.exists():
        try:
            rows = [
                json.loads(line)
                for line in MANUAL_TICKET_LOG.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except Exception:
            rows = []
        tickets = [row for row in rows if row.get("record_type") == "manual_ticket"]
        if tickets:
            st.markdown("#### 최근 티켓")
            display_tickets = [
                {
                    "생성": row.get("created_at"),
                    "종목": row.get("symbol"),
                    "방향": row.get("side"),
                    "수량": row.get("qty"),
                    "지정가": row.get("limit_price"),
                    "손절": row.get("stop_loss"),
                    "목표": row.get("target_exit"),
                    "상태": row.get("status"),
                }
                for row in reversed(tickets[-10:])
            ]
            st.dataframe(pd.DataFrame(display_tickets), width="stretch", hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(page_title="재무관리 모델", layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)
    catalog = MarketDataCatalog(CATALOG_PATH)

    cov = catalog.coverage()
    _render_app_header(cov)

    tabs = st.tabs(
        [
            "종목 선정",
            "차트 리딩",
            "추천기",
            "검증 결과",
            "예측",
            "페이퍼 원장",
            "실거래",
            "RAG",
            "카탈로그",
        ]
    )
    with tabs[0]:
        _render_screener(catalog)
    with tabs[1]:
        _render_chart_read_tab(catalog)
    with tabs[2]:
        _render_recommender(catalog)
    with tabs[3]:
        _render_validation()
    with tabs[4]:
        _render_forecast()
    with tabs[5]:
        _render_ledgers()
    with tabs[6]:
        _render_live_console()
    with tabs[7]:
        _render_rag()
    with tabs[8]:
        _render_section_header(
            "카탈로그 커버리지",
            "저장된 심볼, 시장, 바 수, 데이터 커버리지를 점검합니다.",
            kicker="Data catalog",
            side=str(CATALOG_PATH),
        )
        if cov:
            st.dataframe(pd.DataFrame([c.__dict__ for c in cov]), width="stretch", hide_index=True)
        else:
            st.info("저장된 데이터 없음. `trader ingest --symbols … --market …` 로 수집.")
        st.caption("수집 예: `trader ingest --symbols AAPL,MSFT --market us`")


if __name__ == "__main__":
    main()
