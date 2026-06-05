from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from data.catalog import DEFAULT_CATALOG_PATH, MarketDataCatalog
from engine.portfolio import screen_momentum

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / DEFAULT_CATALOG_PATH


def _render_chart_read_tab(catalog: MarketDataCatalog) -> None:
    """Render the Chart Read tab contents."""
    st.subheader("Chart Read")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        symbol = st.text_input("Symbol", "BTC/USDT", key="cr_symbol")
    with col2:
        market = st.selectbox("Market", ["crypto", "us", "kospi", "kosdaq"], key="cr_market")
    with col3:
        timeframe = st.selectbox("Timeframe", ["1d", "4h", "1h", "15m"], key="cr_tf")
    with col4:
        direction = st.selectbox("Direction", ["long", "short"], key="cr_dir")

    run = st.button("Run Chart Read", type="primary", key="cr_run")

    if not run:
        st.info("Set parameters above and click Run Chart Read.")
        return

    # ------------------------------------------------------------------
    # 1. Load bars
    # ------------------------------------------------------------------
    sym_upper = symbol.strip().upper()
    bars = catalog.get_bars(sym_upper, market=market)

    if not bars and market == "crypto":
        try:
            from data.ingest.ccxt_crypto import fetch_ccxt_bars  # type: ignore[import]

            end = datetime.utcnow()
            start = end - timedelta(days=400)
            bars = fetch_ccxt_bars(
                sym_upper,
                start,
                end,
                timeframe=timeframe,
                intraday=(timeframe != "1d"),
            )
        except Exception as exc:
            st.warning(f"CCXT fetch failed: {exc}")

    if not bars:
        st.error("No bars found. Check symbol / market or ingest data first.")
        return

    bars = bars[-300:]  # use the last 300 bars

    # ------------------------------------------------------------------
    # 2. Run detectors
    # ------------------------------------------------------------------
    import plotly.graph_objects as go

    from engine.chart.fvg import run_fvg
    from engine.chart.order_block import detect_order_blocks
    from engine.chart.read import format_chart_read, read_chart
    from engine.chart.structure import detect_swing_structure
    from engine.chart.volume_profile import build_volume_profile

    # Run chart read (fuses all detectors)
    with st.spinner("Running chart read…"):
        try:
            chart_read = read_chart(bars, direction=direction)
        except Exception as exc:
            st.error(f"Chart read failed: {exc}")
            return

    # ------------------------------------------------------------------
    # 3. Decision banner
    # ------------------------------------------------------------------
    decision_val = chart_read.decision.value
    confluence = chart_read.confluence

    long_enter = direction == "long" and decision_val in ("ENTER_NOW", "SCALE_IN")
    short_enter = direction == "short" and decision_val in ("ENTER_NOW", "SCALE_IN")

    banner_msg = f"**{decision_val}** — Confluence: {confluence:.1f} / 100"
    if long_enter or short_enter:
        st.success(banner_msg)
    elif decision_val == "WAIT_FOR_PULLBACK":
        st.warning(banner_msg)
    else:
        st.error(banner_msg)

    # ------------------------------------------------------------------
    # 4. Build candlestick chart
    # ------------------------------------------------------------------
    ts_list = [b.ts for b in bars]
    opens = [b.open for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=ts_list,
                open=opens,
                high=highs,
                low=lows,
                close=closes,
                name=sym_upper,
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
            )
        ]
    )

    fig.update_layout(
        title=f"{sym_upper} — {timeframe} Chart Read",
        xaxis_title="Time",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        height=600,
    )

    # ------------------------------------------------------------------
    # 4a. FVG overlays
    # ------------------------------------------------------------------
    try:
        fvg_result = run_fvg(bars)
        for zone in fvg_result.active_fvgs:
            if zone.mitigated:
                continue
            color = (
                "rgba(38,166,154,0.20)" if zone.direction == "bullish" else "rgba(239,83,80,0.20)"
            )
            line_color = (
                "rgba(38,166,154,0.60)" if zone.direction == "bullish" else "rgba(239,83,80,0.60)"
            )
            # Extend zone to the end of the bar series
            x_end = ts_list[-1]
            fig.add_shape(
                type="rect",
                x0=zone.ts,
                x1=x_end,
                y0=zone.zone_low,
                y1=zone.zone_high,
                fillcolor=color,
                line={"color": line_color, "width": 1},
                layer="below",
            )
    except Exception as exc:
        st.caption(f"FVG overlay skipped: {exc}")

    # ------------------------------------------------------------------
    # 4b. Order Block overlays
    # ------------------------------------------------------------------
    try:
        obs = detect_order_blocks(bars)
        for ob in obs:
            if ob.mitigated:
                continue
            color = (
                "rgba(30,136,229,0.18)" if ob.direction == "bullish" else "rgba(171,71,188,0.18)"
            )
            line_color = (
                "rgba(30,136,229,0.55)" if ob.direction == "bullish" else "rgba(171,71,188,0.55)"
            )
            x_end = ts_list[-1]
            fig.add_shape(
                type="rect",
                x0=ob.ts,
                x1=x_end,
                y0=ob.zone_low,
                y1=ob.zone_high,
                fillcolor=color,
                line={"color": line_color, "width": 1, "dash": "dot"},
                layer="below",
            )
    except Exception as exc:
        st.caption(f"Order Block overlay skipped: {exc}")

    # ------------------------------------------------------------------
    # 4c. Volume Profile (POC / VAH / VAL)
    # ------------------------------------------------------------------
    try:
        vp = build_volume_profile(bars)
        if not vp.degenerate:
            fig.add_hline(
                y=vp.poc_price,
                line={"color": "rgba(255,235,59,0.90)", "width": 2, "dash": "solid"},
                annotation_text="POC",
                annotation_position="right",
            )
            fig.add_hline(
                y=vp.vah,
                line={"color": "rgba(255,235,59,0.50)", "width": 1, "dash": "dash"},
                annotation_text="VAH",
                annotation_position="right",
            )
            fig.add_hline(
                y=vp.val,
                line={"color": "rgba(255,235,59,0.50)", "width": 1, "dash": "dash"},
                annotation_text="VAL",
                annotation_position="right",
            )
    except Exception as exc:
        st.caption(f"Volume Profile overlay skipped: {exc}")

    # ------------------------------------------------------------------
    # 4d. Swing Pivot markers
    # ------------------------------------------------------------------
    try:
        ms = detect_swing_structure(bars)
        pivot_ts_h, pivot_price_h = [], []
        pivot_ts_l, pivot_price_l = [], []
        for p in ms.swing_pivots:
            if p.pivot_type == "high":
                pivot_ts_h.append(p.ts)
                pivot_price_h.append(p.price)
            else:
                pivot_ts_l.append(p.ts)
                pivot_price_l.append(p.price)

        if pivot_ts_h:
            fig.add_trace(
                go.Scatter(
                    x=pivot_ts_h,
                    y=pivot_price_h,
                    mode="markers",
                    marker={"symbol": "triangle-down", "color": "#ef5350", "size": 10},
                    name="Swing High",
                )
            )
        if pivot_ts_l:
            fig.add_trace(
                go.Scatter(
                    x=pivot_ts_l,
                    y=pivot_price_l,
                    mode="markers",
                    marker={"symbol": "triangle-up", "color": "#26a69a", "size": 10},
                    name="Swing Low",
                )
            )
    except Exception as exc:
        st.caption(f"Swing pivot overlay skipped: {exc}")

    st.plotly_chart(fig, use_container_width=True)

    # ------------------------------------------------------------------
    # 5. Full text report + contributions table
    # ------------------------------------------------------------------
    with st.expander("Full Chart Read Report", expanded=False):
        st.text(format_chart_read(chart_read))

    if chart_read.contributions:
        contrib_rows = [
            {
                "name": c.name,
                "layer": c.layer,
                "weight": round(c.weight, 3),
                "direction": c.direction,
                "note": c.note,
            }
            for c in chart_read.contributions
        ]
        st.dataframe(
            pd.DataFrame(contrib_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No signal contributions available.")


def main() -> None:
    st.set_page_config(page_title="Trader Control", layout="wide")
    st.title("Trader Control")
    catalog = MarketDataCatalog(CATALOG_PATH)

    coverage = catalog.coverage()
    overview, screen, valuation, chart_read_tab = st.tabs(
        ["Catalog", "Momentum", "Valuation", "Chart Read"]
    )

    with overview:
        st.dataframe(
            pd.DataFrame([item.__dict__ for item in coverage]),
            use_container_width=True,
            hide_index=True,
        )

    with screen:
        symbols = st.text_input("Universe", "MSFT,AAPL,NVDA,AMZN,META,GOOGL,AVGO")
        market = st.selectbox("Market", ["us", "kospi", "kosdaq", "crypto"])
        lookback = st.number_input("Lookback", min_value=5, max_value=504, value=126)
        if st.button("Run Screen", type="primary"):
            requested = [item.strip().upper() for item in symbols.split(",") if item.strip()]
            bars = {symbol: catalog.get_bars(symbol, market=market) for symbol in requested}
            rows = screen_momentum(bars, lookback=int(lookback))
            st.dataframe(pd.DataFrame([row.__dict__ for row in rows]), use_container_width=True)

    with valuation:
        valuations = catalog.get_valuations(limit=100)
        if valuations:
            st.dataframe(
                pd.DataFrame([item.__dict__ for item in valuations]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No valuation scores stored yet.")

    with chart_read_tab:
        _render_chart_read_tab(catalog)


if __name__ == "__main__":
    main()
