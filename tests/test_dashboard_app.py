from datetime import UTC, date, datetime, timedelta

import dashboard.app as dashboard_app
from dashboard.app import (
    _CSS,
    _entry_zone_text,
    _feed_summary,
    _live_candidate_rows,
    _live_ticket_args,
    _order_gate_fingerprint,
    _order_gate_passed,
    _order_gate_receipt_is_current,
    _order_gate_rows,
    _overlay_live_quotes,
    _quote_state,
)
from data.ingest.yahoo import YahooQuote
from data.models import PriceBar


def test_entry_zone_text_compacts_large_prices() -> None:
    assert _entry_zone_text((79_447.0, 79_808.72)) == "79,447–79,809"


def test_entry_zone_text_preserves_small_price_precision() -> None:
    assert _entry_zone_text((0.12345, 0.23456)) == "0.1235–0.2346"
    assert _entry_zone_text((83.75, 105.88)) == "83.75–105.88"


def test_entry_zone_text_handles_missing_zone() -> None:
    assert _entry_zone_text(None) == "—"


def test_code_blocks_wrap_long_local_commands() -> None:
    assert 'div[data-testid="stCode"] code' in _CSS
    assert "overflow-wrap: anywhere" in _CSS


def test_streamlit_material_icons_keep_their_ligature_font() -> None:
    assert 'html, body, [class*="css"], [class*="st-"]' not in _CSS
    assert '[data-testid="stIconMaterial"]' in _CSS
    assert 'font-family: "Material Symbols Rounded" !important' in _CSS
    assert 'font-feature-settings: "liga"' in _CSS


def _ticket_args(*, quote: float, verify_only: bool) -> list[str]:
    return _live_ticket_args(
        symbol="QQQ",
        side="buy",
        qty=1.0,
        quote=quote,
        limit_price=100.0,
        order_key="2026-07-13",
        as_of="2026-07-13",
        stop_loss=90.0,
        target_exit=115.0,
        verify_only=verify_only,
    )


def test_order_gate_fingerprint_locks_the_exact_order_and_account() -> None:
    env = {"LIVE_BROKER": "manual-paper", "LIVE_MANUAL_EQUITY": "100000"}
    verification = _ticket_args(quote=100.0, verify_only=True)
    ticket = _ticket_args(quote=100.0, verify_only=False)

    assert _order_gate_fingerprint(verification, env) == _order_gate_fingerprint(ticket, env)
    assert _order_gate_fingerprint(ticket, env) != _order_gate_fingerprint(
        _ticket_args(quote=101.0, verify_only=False), env
    )
    assert _order_gate_fingerprint(ticket, env) != _order_gate_fingerprint(
        ticket, {**env, "LIVE_MANUAL_EQUITY": "90000"}
    )


def test_order_gate_pass_requires_current_result_and_live_prerequisites() -> None:
    assert _order_gate_passed(prerequisites=True, result=(0, "ok"), is_current=True)
    assert not _order_gate_passed(prerequisites=False, result=(0, "ok"), is_current=True)
    assert not _order_gate_passed(prerequisites=True, result=(0, "ok"), is_current=False)
    assert not _order_gate_passed(prerequisites=True, result=(2, "blocked"), is_current=True)


def test_order_gate_receipt_expires_and_rejects_changed_input() -> None:
    now = datetime(2026, 7, 13, 0, 5, tzinfo=UTC)
    receipt = {
        "fingerprint": "same-order",
        "checked_at_epoch": now.timestamp() - 299,
    }

    assert _order_gate_receipt_is_current(receipt, "same-order", now=now)
    assert not _order_gate_receipt_is_current(receipt, "changed-order", now=now)
    assert not _order_gate_receipt_is_current(
        {**receipt, "checked_at_epoch": now.timestamp() - 301},
        "same-order",
        now=now,
    )


def test_order_gate_rows_never_show_stale_or_failed_checks_as_passed() -> None:
    passed = _order_gate_rows(
        recommendation_status="PASS",
        quote_attested=True,
        result=(0, "ok"),
        is_current=True,
    )
    stale = _order_gate_rows(
        recommendation_status="PASS",
        quote_attested=True,
        result=(0, "ok"),
        is_current=False,
    )
    blocked = _order_gate_rows(
        recommendation_status="PASS",
        quote_attested=True,
        result=(2, "[broker-preflight:manual-live] market is closed"),
        is_current=True,
    )

    assert all(row["상태"] == "PASS" for row in passed)
    assert {row["상태"] for row in stale[2:]} == {"재검증"}
    assert next(row for row in blocked if row["검증 항목"] == "브로커·시장")["상태"] == "BLOCK"
    assert all(row["상태"] != "PASS" for row in blocked[2:])


def _daily_bar(ts: date, close: float = 100.0) -> PriceBar:
    return PriceBar(
        symbol="AAPL",
        market="us",
        source_symbol="AAPL",
        ts=ts,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1_000,
        freq="1d",
        currency="USD",
        source="catalog",
    )


def _quote(ts: datetime, price: float = 105.0) -> YahooQuote:
    return YahooQuote(
        symbol="AAPL",
        source_symbol="AAPL",
        price=price,
        day_open=101.0,
        timestamp=ts,
        currency="USD",
    )


def test_overlay_live_quotes_appends_a_new_daily_mark() -> None:
    quote_time = datetime(2026, 7, 10, 19, 59, tzinfo=UTC)
    updated = _overlay_live_quotes(
        {"AAPL": [_daily_bar(date(2026, 7, 9))]},
        {"AAPL": _quote(quote_time)},
        "us",
    )

    assert len(updated["AAPL"]) == 2
    assert updated["AAPL"][-1].ts == date(2026, 7, 10)
    assert updated["AAPL"][-1].open == 101.0
    assert updated["AAPL"][-1].close == 105.0
    assert updated["AAPL"][-1].source == "yfinance:1m"


def test_overlay_live_quotes_updates_same_day_without_duplicate() -> None:
    quote_time = datetime(2026, 7, 10, 19, 59, tzinfo=UTC)
    updated = _overlay_live_quotes(
        {"AAPL": [_daily_bar(date(2026, 7, 10))]},
        {"AAPL": _quote(quote_time, 102.0)},
        "us",
    )

    assert len(updated["AAPL"]) == 1
    assert updated["AAPL"][-1].close == 102.0
    assert updated["AAPL"][-1].high == 102.0


def test_quote_and_feed_freshness_are_explicit() -> None:
    now = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)
    assert _quote_state(now - timedelta(seconds=90), now=now) == "LIVE"
    assert _quote_state(now - timedelta(minutes=10), now=now) == "시장 마감/지연"

    intraday = PriceBar(
        symbol="AAPL",
        market="us",
        source_symbol="AAPL",
        ts=datetime(2026, 7, 10, 19, 59),
        open=100,
        high=101,
        low=99,
        close=100.5,
        volume=10,
        freq="1m",
        currency="USD",
        source="https://query1.finance.yahoo.com/chart/AAPL",
    )
    assert _feed_summary([intraday], "1m", now=now) == (
        "Yahoo",
        "07-10 19:59 UTC",
        "LIVE",
    )
    crypto = PriceBar(
        symbol="BTC/USDT",
        market="crypto",
        source_symbol="BTC/USDT",
        ts=datetime(2026, 7, 10, 19, 59),
        open=60_000,
        high=60_100,
        low=59_900,
        close=60_050,
        volume=10,
        freq="1m",
        currency="USDT",
        source="ccxt:BTC/USDT",
    )
    assert _feed_summary([crypto], "1m", now=now)[0] == "Binance"


def test_live_candidate_rows_keep_validated_and_watch_candidates_distinct() -> None:
    now = datetime.now(tz=UTC)
    rows = [
        {
            "순위": 1,
            "종목": "AAPL",
            "기업": "Apple",
            "_pick": True,
            "액션": "BUY",
            "신뢰도": "high",
            "진입": 100.0,
            "손절": 90.0,
            "목표": 120.0,
        },
        {
            "순위": 8,
            "종목": "MSFT",
            "기업": "Microsoft",
            "_pick": False,
            "액션": "AVOID",
            "신뢰도": "low",
            "진입": 300.0,
            "손절": 270.0,
            "목표": 360.0,
            "현재가": 305.0,
        },
    ]
    display = _live_candidate_rows(rows, {"AAPL": _quote(now)})

    assert display[0]["구분"] == "검증 매수 후보"
    assert display[0]["시세 상태"] == "LIVE"
    assert display[1]["구분"] == "관찰 후보"
    assert display[1]["시세 상태"] == "수집 실패"


def test_single_name_live_load_only_requests_the_target_quote(monkeypatch) -> None:
    requested: list[tuple[str, ...]] = []

    class Catalog:
        def get_bars(self, symbol: str, *, market: str) -> list[PriceBar]:
            bar = _daily_bar(date(2026, 7, 9))
            return [
                PriceBar(
                    symbol=symbol,
                    market=market,
                    source_symbol=symbol,
                    ts=bar.ts,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    freq=bar.freq,
                    currency=bar.currency,
                    source=bar.source,
                )
            ]

    def fake_quotes(symbols: tuple[str, ...], market: str) -> dict[str, YahooQuote]:
        requested.append(symbols)
        return {"AAPL": _quote(datetime(2026, 7, 10, 19, 59, tzinfo=UTC))}

    monkeypatch.setattr(dashboard_app, "_cached_yahoo_quotes", fake_quotes)
    loaded = dashboard_app._load_universe(
        Catalog(),
        ["AAPL", "MSFT"],
        "us",
        live=True,
        live_symbols=("AAPL",),
    )

    assert requested == [("AAPL",)]
    assert loaded["AAPL"][-1].close == 105.0
    assert loaded["MSFT"][-1].source == "catalog"


# ─────────────────────────────────────────────────────────────────────────────
# 딥링크 (?tab=recommender&ticker=NVDA&market=us)
# ─────────────────────────────────────────────────────────────────────────────
def test_deeplink_updates_maps_alias_and_prefills() -> None:
    updates, tab, marker = dashboard_app._deeplink_updates(
        {"tab": "recommender", "ticker": "brk-b", "market": "us"}, None
    )
    assert updates == {"rec_tkr": "BRK-B", "rec_mkt": "us"}
    assert tab == "추천기"
    assert marker


def test_deeplink_updates_consumed_marker_applies_once() -> None:
    params = {"tab": "recommender", "ticker": "NVDA"}
    updates, tab, marker = dashboard_app._deeplink_updates(params, None)
    assert updates and tab == "추천기"
    # 같은 파라미터가 rerun 으로 다시 들어오면 무시 (사용자 입력 보호)
    updates2, tab2, marker2 = dashboard_app._deeplink_updates(params, marker)
    assert updates2 == {} and tab2 is None and marker2 == marker


def test_deeplink_updates_rejects_junk() -> None:
    updates, tab, _ = dashboard_app._deeplink_updates(
        {"tab": "nope", "ticker": "A" * 20, "market": "mars"}, None
    )
    assert updates == {} and tab is None


def test_deeplink_updates_accepts_korean_tab_label() -> None:
    _, tab, _ = dashboard_app._deeplink_updates({"tab": "추천기"}, None)
    assert tab == "추천기"
