from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from data.catalog import DEFAULT_CATALOG_PATH, MarketDataCatalog
from engine.portfolio import screen_momentum

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / DEFAULT_CATALOG_PATH


def main() -> None:
    st.set_page_config(page_title="Trader Control", layout="wide")
    st.title("Trader Control")
    catalog = MarketDataCatalog(CATALOG_PATH)

    coverage = catalog.coverage()
    overview, screen, valuation = st.tabs(["Catalog", "Momentum", "Valuation"])

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
            bars = {
                symbol: catalog.get_bars(symbol, market=market)
                for symbol in requested
            }
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


if __name__ == "__main__":
    main()
