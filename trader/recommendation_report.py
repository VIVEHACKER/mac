from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from valuation.recommendation import AQREvaluation


@dataclass(frozen=True)
class TradeRecommendation:
    symbol: str
    rank: int | None
    universe_size: int
    action: str
    decision: str
    actionable: bool
    blockers: tuple[str, ...]
    confidence_score: float
    confidence_band: str
    composite: float | None
    momentum: float | None
    value: float | None
    quality: float | None
    current_price: float | None
    execution_limit: float | None
    advisory_entry: float | None
    advisory_stop: float | None
    stop_loss: float | None
    stop_basis: str
    target_exit: float | None
    reward_risk: float | None
    target_weight: float
    target_qty: float
    target_notional: float
    allocation_drift: float
    risk_to_stop: float | None
    upside_to_target: float | None
    order_side: str
    order_qty: float
    pretrade_status: str
    client_order_id: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_trade_recommendations(
    evaluations: list[AQREvaluation],
    *,
    target_weights: dict[str, float],
    target_book: dict[str, float],
    intents: list[dict[str, Any]],
    pretrade: list[dict[str, Any]],
    target_capital: float,
    nav: float,
    max_risk_pct_of_nav: float = 0.02,
    min_reward_risk: float = 1.5,
) -> list[TradeRecommendation]:
    """Join the validated signal with the exact target book and delta order plan.

    ``current_price`` / ``execution_limit`` reproduce the validated monthly rebalance.
    ``advisory_entry`` / stop / target are an ATR risk frame and do not silently replace
    the strategy's execution price. This distinction keeps the report honest: waiting for
    an unvalidated pullback would change the strategy that earned the backtest evidence.
    """

    intent_by_symbol = {str(row.get("symbol", "")).upper(): row for row in intents}
    check_by_id = {str(row.get("client_order_id", "")): row for row in pretrade}
    evaluation_by_symbol = {result.ticker.upper(): result for result in evaluations}
    rows: list[TradeRecommendation] = []

    def rank_key(item: tuple[str, float]) -> int:
        result = evaluation_by_symbol.get(item[0])
        return result.rank if result is not None and result.rank is not None else 10**9

    for symbol, target_weight in sorted(
        ((key.upper(), float(value)) for key, value in target_weights.items()),
        key=rank_key,
    ):
        result = evaluation_by_symbol.get(symbol)
        if result is None:
            continue
        plan = result.entry_plan
        current_price = result.current_price
        target_qty = float(target_book.get(symbol, 0.0))
        target_notional = target_qty * current_price if current_price is not None else 0.0
        realized_weight = target_notional / target_capital if target_capital > 0 else 0.0
        intent = intent_by_symbol.get(symbol, {})
        client_order_id = str(intent.get("client_order_id", ""))
        check = check_by_id.get(client_order_id, {}) if client_order_id else {}
        order_side = str(intent.get("side", "hold"))
        order_qty = float(intent.get("qty", 0.0) or 0.0)
        execution_limit = _positive_float(intent.get("limit_price")) or current_price

        blockers: list[str] = []
        if result.action != "BUY":
            blockers.append(f"recommendation action is {result.action}, not BUY")
        if plan is None:
            blockers.append("entry/stop/target plan is unavailable")
        if target_qty <= 0:
            blockers.append("target quantity is zero")
        if execution_limit is None or execution_limit <= 0:
            blockers.append("execution limit is unavailable")
        pretrade_status = str(check.get("status", "no_order" if not intent else "unknown"))
        if intent and pretrade_status != "accepted":
            blockers.append(f"pretrade status is {pretrade_status}")

        if order_side == "buy":
            decision = "BUY / ADD"
        elif order_side == "sell":
            decision = "REDUCE"
        elif target_qty > 0:
            decision = "HOLD TARGET"
        else:
            decision = "NO POSITION"

        advisory_stop = plan.stop_loss if plan is not None else None
        target_exit = plan.target_exit if plan is not None else None
        reference_entry = execution_limit or current_price
        stop_loss = advisory_stop
        stop_reasons: list[str] = []
        if (
            advisory_stop is not None
            and reference_entry is not None
            and target_qty > 0
            and nav > 0
            and max_risk_pct_of_nav > 0
        ):
            risk_capped_stop = reference_entry - nav * max_risk_pct_of_nav / target_qty
            if risk_capped_stop > advisory_stop:
                stop_loss = risk_capped_stop
                stop_reasons.append(f"{max_risk_pct_of_nav:.1%} NAV risk cap")
        if (
            advisory_stop is not None
            and reference_entry is not None
            and target_exit is not None
            and target_exit > reference_entry
            and min_reward_risk > 0
        ):
            reward_risk_stop = reference_entry - (
                target_exit - reference_entry
            ) / min_reward_risk
            if stop_loss is None or reward_risk_stop > stop_loss:
                stop_loss = reward_risk_stop
            if reward_risk_stop > advisory_stop:
                stop_reasons.append(f"{min_reward_risk:.2f}R minimum")
        stop_basis = " + ".join(stop_reasons) if stop_reasons else "ATR advisory"
        if stop_loss is not None and reference_entry is not None and stop_loss >= reference_entry:
            blockers.append("risk-capped stop is not below the execution price")
        risk_to_stop = (
            target_qty * max(reference_entry - stop_loss, 0.0)
            if reference_entry is not None and stop_loss is not None
            else None
        )
        reward_risk = None
        if (
            reference_entry is not None
            and stop_loss is not None
            and target_exit is not None
            and reference_entry > stop_loss
        ):
            reward_risk = max(target_exit - reference_entry, 0.0) / (
                reference_entry - stop_loss
            )
        upside_to_target = (
            target_qty * max(target_exit - reference_entry, 0.0)
            if reference_entry is not None and target_exit is not None
            else None
        )

        rows.append(
            TradeRecommendation(
                symbol=symbol,
                rank=result.rank,
                universe_size=result.universe_size,
                action=result.action,
                decision=decision,
                actionable=not blockers,
                blockers=tuple(blockers),
                confidence_score=float(result.confidence.score),
                confidence_band=result.confidence.band,
                composite=result.composite,
                momentum=result.momentum,
                value=result.value,
                quality=result.quality,
                current_price=current_price,
                execution_limit=execution_limit,
                advisory_entry=plan.target_entry if plan is not None else None,
                advisory_stop=advisory_stop,
                stop_loss=stop_loss,
                stop_basis=stop_basis,
                target_exit=target_exit,
                reward_risk=reward_risk,
                target_weight=target_weight,
                target_qty=target_qty,
                target_notional=target_notional,
                allocation_drift=realized_weight - target_weight,
                risk_to_stop=risk_to_stop,
                upside_to_target=upside_to_target,
                order_side=order_side,
                order_qty=order_qty,
                pretrade_status=pretrade_status,
                client_order_id=client_order_id,
                reasons=tuple(result.reasons),
            )
        )
    return rows


def recommendation_markdown(
    rows: list[TradeRecommendation],
    *,
    nav: float,
    target_capital: float,
    fractional: bool,
) -> list[str]:
    """Render the recommendation/risk section embedded in the operating report."""

    deployed = sum(row.target_notional for row in rows)
    aggregate_risk = sum(row.risk_to_stop or 0.0 for row in rows)
    actionables = sum(row.actionable for row in rows)
    lines = [
        "## 매매 추천 및 가격 계획",
        "",
        f"실행 가능 추천: {actionables}/{len(rows)} | 목표 투자 ${target_capital:,.2f} | "
        f"산출 투자 ${deployed:,.2f} | 잔여 현금 ${max(target_capital - deployed, 0.0):,.2f}",
        f"참고 손절가까지의 합산 위험: ${aggregate_risk:,.2f} "
        f"({aggregate_risk / nav:.2%} of NAV)" if nav > 0 else "참고 손절 위험: 계산 불가",
        f"수량 방식: {'분할주 6자리' if fractional else '정수주'}",
        "",
        "| 순위 | 종목 | 판단 | 신뢰도 | 목표비중 | 수량 | 실행 지정가 | 참고 평균진입 | 손절 | 목표 | R/R | 사전검증 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.rank or '-'} | {row.symbol} | {row.decision} | "
            f"{row.confidence_score:.0f} ({row.confidence_band}) | {row.target_weight:.1%} | "
            f"{row.target_qty:g} | {_money(row.execution_limit)} | {_money(row.advisory_entry)} | "
            f"{_money(row.stop_loss)} | {_money(row.target_exit)} | "
            f"{_number(row.reward_risk, 2)} | "
            f"{'PASS' if row.actionable else 'BLOCK'} |"
        )
    lines += [
        "",
        "> 실행 지정가는 검증된 월간 리밸런싱을 재현하는 가격입니다. 참고 평균진입, "
        "advisory_stop, 목표는 ATR 기반 위험 프레임이며 초과수익이 별도 검증된 신호가 아닙니다.",
        "> 주문 티켓 손절가는 ATR 무효화 가격, 종목당 2% NAV 손실 상한, 최소 1.5R 조건 중 "
        "가장 가까운 가격을 사용합니다. JSON의 advisory_stop에 원래 ATR 가격을 보존합니다.",
        "> 현재 주문 경로는 손절·목표 주문을 자동 제출하지 않습니다. 체결 후 보호 주문을 별도로 "
        "등록하거나, 하드 비중 상한을 실제 손실 한도로 사용해야 합니다.",
    ]
    blocked = [row for row in rows if row.blockers]
    if blocked:
        lines += ["", "### 차단 사유", ""]
        for row in blocked:
            lines.append(f"- {row.symbol}: {'; '.join(row.blockers)}")
    lines += ["", "### 종목별 근거", ""]
    for row in rows:
        reason = " · ".join(row.reasons[:3]) if row.reasons else "근거 없음"
        lines.append(
            f"- **{row.symbol} #{row.rank or '-'}** — 합성 {_number(row.composite, 2)}, "
            f"모멘텀 {_percent(row.momentum)}, 퀄리티 {_number(row.quality, 4)}. {reason}"
        )
    return lines


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "-"


def _number(value: float | None, digits: int) -> str:
    return f"{value:.{digits}f}" if value is not None else "-"


def _percent(value: float | None) -> str:
    return f"{value:+.1%}" if value is not None else "-"
