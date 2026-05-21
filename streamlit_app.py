"""Streamlit dashboard for daily stock signals."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import streamlit as st
import streamlit.components.v1 as components

REPO_RAW_URL = os.environ.get("SIGNALS_URL", "")
LOCAL_FALLBACK = Path(__file__).parent / "data" / "latest_signals.json"


@st.cache_data(ttl=60)
def load_signals() -> dict:
    if LOCAL_FALLBACK.exists():
        with LOCAL_FALLBACK.open() as f:
            return json.load(f)
    if REPO_RAW_URL:
        try:
            with urlopen(REPO_RAW_URL, timeout=10) as response:
                return json.load(response)
        except URLError as exc:
            st.error(f"Failed to load signals: {exc}")
    return {"signals": {}, "generated_at": None, "last_run": {}}


def tradingview_widget(symbol: str, height: int = 600) -> str:
    container_id = "tv_" + symbol.replace(":", "_").replace(".", "_")
    return f"""
    <div id="{container_id}" style="height:{height}px"></div>
    <script src="https://s3.tradingview.com/tv.js"></script>
    <script>
      new TradingView.widget({{
        "container_id": "{container_id}",
        "autosize": true,
        "symbol": "{symbol}",
        "interval": "D",
        "timezone": "Asia/Taipei",
        "theme": "dark",
        "style": "1",
        "locale": "zh_TW",
        "studies": ["MASimple@tv-basicstudies", "Volume@tv-basicstudies"],
        "withdateranges": true,
        "allow_symbol_change": false
      }});
    </script>
    """


def render() -> None:
    st.set_page_config(page_title="Stock Screener", layout="wide")

    data = load_signals()
    signals = data.get("signals", {})

    if not signals:
        st.warning(
            "No signals yet. Run `uv run python -m screener.run --market us` locally, "
            "or wait for the GitHub Actions schedule."
        )
        return

    rows_ok = sorted(
        [s for s in signals.values() if s.get("status") == "ok"],
        key=lambda r: (r.get("score", 0), r.get("max_score", 0)),
        reverse=True,
    )
    rows_failed = [s for s in signals.values() if s.get("status") != "ok"]

    generated = data.get("generated_at") or "n/a"
    last_run = data.get("last_run") or {}
    st.caption(
        f"Last update: {generated} (UTC). "
        f"TW run: {last_run.get('tw', 'n/a')}. US run: {last_run.get('us', 'n/a')}. "
        f"{len(rows_ok)} ok · {len(rows_failed)} failed."
    )

    if not rows_ok:
        st.warning("All signals failed to fetch.")
        if rows_failed:
            with st.expander(f"Fetch failures ({len(rows_failed)})"):
                st.json(rows_failed)
        return

    col_left, col_mid, col_right = st.columns([2, 3, 5])

    with col_left:
        st.subheader("Today's list")
        labels = [
            f"{r['symbol']}  {r['score']}/{r['max_score']}  ({r['market'].upper()})"
            for r in rows_ok
        ]
        idx = st.radio(
            "ticker",
            options=list(range(len(rows_ok))),
            format_func=lambda i: labels[i],
            label_visibility="collapsed",
        )
        selected = rows_ok[idx]

    with col_mid:
        st.subheader(f"{selected['name']} ({selected['symbol']})")
        st.metric("Score", f"{selected['score']} / {selected['max_score']}")
        st.write("**Reasons**")
        for reason in selected.get("reasons", []):
            marker = "[x]" if reason["passed"] else "[ ]"
            st.write(f"`{marker}` {reason['rule']} — {reason['detail']}")
        with st.expander("Raw indicators"):
            st.json(selected.get("indicators", {}))
        if selected.get("analyst"):
            with st.expander("Analyst"):
                st.json(selected["analyst"])

    with col_right:
        tv_symbol = selected.get("tradingview_symbol")
        if tv_symbol:
            components.html(tradingview_widget(tv_symbol), height=620)
        else:
            st.info("No TradingView symbol configured for this ticker.")

    if rows_failed:
        with st.expander(f"Fetch failures ({len(rows_failed)})"):
            st.json(rows_failed)


render()
