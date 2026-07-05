"""Concrete go-live status: `python -m scripts.live_setup_check`.

Turns docs/LIVE_OPERATIONS.md's checklist into a live readout — where the operator actually is
and the single exact command to run next. The two blockers to real trading are, by design, not
code: (1) the Alpaca API keys (operator) and (2) the forward-OOS time gate (calendar). This
prints both, plus (if the keys look real) a connectivity probe, so "다 됐냐"는 매번 실행으로 답한다.

Read-only. Safe to run anytime, key or no key.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.price_ingest_cron import _looks_like_real_key  # noqa: E402  (single key-shape check)

# The alpaca-live floor enforced by engine.live.load_live_trading_policy (audit hard floor).
REQUIRED_OOS_PERIODS = 6
LIVE_STRATEGY_ID = "aqr_top7_cap20_trail10_pit110"


def key_state(api_key: str, secret_key: str) -> str:
    """``missing`` (unset/blank) | ``placeholder`` (template / too short) | ``present`` (real shape)."""
    if not api_key.strip() or not secret_key.strip():
        return "missing"
    if _looks_like_real_key(api_key) and _looks_like_real_key(secret_key):
        return "present"
    return "placeholder"


def ledger_progress(ledger_path: Path) -> tuple[int, int]:
    """(entries, closed_periods) for a forward-OOS ledger. Each consecutive pair of entries is
    one realised holding period, so closed = max(0, entries - 1) (matches engine.paper_oos)."""
    path = Path(ledger_path)
    if not path.exists():
        return (0, 0)
    entries = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return (entries, max(0, entries - 1))


def next_actions(*, key: str, closed: int) -> list[str]:
    """The exact next step(s) for the current state — one blocker at a time, in order."""
    if key in ("missing", "placeholder"):
        return [
            "STEP 1 — Alpaca 페이퍼 키 발급 (무료, ~10분):",
            "  1) https://alpaca.markets 가입 → 로그인",
            "  2) 대시보드에서 'Paper Trading' 계정 선택(입금 불필요)",
            "  3) 'API Keys' → Generate → Key ID(약 20자)와 Secret(약 40자, 1회만 표시) 복사",
            "  4) .env 편집: ALPACA_API_KEY=<Key ID>  /  ALPACA_SECRET_KEY=<Secret>",
            "     (LIVE_BROKER=alpaca-paper 는 이미 설정됨 → 자동으로 페이퍼 엔드포인트)",
            "  5) 검증: .venv/bin/trader live-price-ingest SPY --source alpaca",
            "     → 표가 출력되면 키 정상(이 순간부터 가격 cron도 IEX로 자동 전환).",
        ]
    if closed < REQUIRED_OOS_PERIODS:
        return [
            f"STEP 2 — 실증 시간게이트 대기 중 ({closed}/{REQUIRED_OOS_PERIODS} 닫힌 기간).",
            "  이 게이트는 코드로 못 줄인다(라이브 전환 하드 플로어). cadence cron이 매",
            "  21영업일 자동으로 리밸런스를 기록하며 닫힌 기간을 쌓는다.",
            "  그동안 페이퍼 루프를 돌려 운영을 검증:",
            "    .venv/bin/trader rebalance-plan --top-n 7   # 델타 주문안(사전검증 포함)",
            "    → out/rebalance-plan-*.json 검토 → 출력된 live-submit 명령 실행(승인)",
            "    .venv/bin/trader live-reconcile --from-store  # 제출 후 정산",
            "  6개 닫힌 기간 도달 예상: T0 06-05 기준 ~2026-12월 초.",
        ]
    return [
        f"STEP 3 — 시간게이트 충족 ({closed}/{REQUIRED_OOS_PERIODS}). 라이브 전환 검토 가능:",
        "  1) Alpaca LIVE 키 발급 → .env 교체 + LIVE_BROKER=alpaca-live",
        "  2) .venv/bin/trader live-readiness --require-order-submission → Ready | yes 확인",
        "  3) 첫 달 LIVE_MAX_CAPITAL=10000(소액) 유지 → 성과 보고 후 증액 재검토",
    ]


def _probe_alpaca(api_key: str, secret_key: str) -> str:
    """Confirm the keys actually authenticate (not just look real). Best-effort; never raises."""
    try:
        from trader.execution.adapters.alpaca import AlpacaBrokerAdapter

        account = AlpacaBrokerAdapter(api_key, secret_key, paper=True, timeout_s=15).get_account()
        return f"reachable (account {account.account_id}, equity ${account.equity:,.2f})"
    except Exception as exc:  # noqa: BLE001 — a probe failure is informational, not fatal
        return f"UNREACHABLE / auth failed ({type(exc).__name__}: {exc})"


def build_report(*, out_dir: Path | None = None, probe: bool = True) -> str:
    out = Path(out_dir) if out_dir is not None else ROOT / "out"
    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    state = key_state(api_key, secret_key)
    ledger = out / f"paper-oos-ledger-{LIVE_STRATEGY_ID}.jsonl"
    entries, closed = ledger_progress(ledger)

    lines = [
        "# Live Setup Check",
        "",
        "| Gate | State |",
        "|---|---|",
        f"| Alpaca keys | {state} |",
        f"| Forward-OOS (alpaca-live 게이트) | {closed}/{REQUIRED_OOS_PERIODS} 닫힌 기간 "
        f"({entries} 리밸 기록) |",
        f"| LIVE_BROKER | {os.getenv('LIVE_BROKER', '(unset)')} |",
        f"| LIVE_MAX_CAPITAL | {os.getenv('LIVE_MAX_CAPITAL', '(unset)')} |",
    ]
    if state == "present" and probe:
        lines.append(f"| Alpaca 연결 | {_probe_alpaca(api_key, secret_key)} |")
    lines += ["", "## 다음 할 일", ""]
    lines += next_actions(key=state, closed=closed)
    return "\n".join(lines)


def run(*, out_dir: Path | None = None, probe: bool = True) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:  # pragma: no cover
        pass
    print(build_report(out_dir=out_dir, probe=probe))
    return 0


if __name__ == "__main__":
    raise SystemExit(run(probe="--no-probe" not in sys.argv))
