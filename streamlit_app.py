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
        "studies": ["MASimple@tv-basicstudies", "Volume@tv-basicstudies", "MACD@tv-basicstudies"],
        "withdateranges": true,
        "allow_symbol_change": false
      }});
    </script>
    """


def _is_above_ma5(row: dict) -> bool:
    ind = row.get("indicators") or {}
    close = ind.get("close")
    ma5 = ind.get("ma5")
    return close is not None and ma5 is not None and close > ma5


def _detail_panel(selected: dict) -> None:
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


def _market_tab(rows: list[dict], market_key: str) -> None:
    above = sorted(
        [r for r in rows if _is_above_ma5(r)],
        key=lambda r: (r.get("score", 0), r.get("max_score", 0)),
        reverse=True,
    )
    below = sorted(
        [r for r in rows if not _is_above_ma5(r)],
        key=lambda r: (r.get("score", 0), r.get("max_score", 0)),
        reverse=True,
    )

    # Single radio with two visually-separated groups. Symbol-as-value so the
    # selection survives reruns. The "header" entries are disabled placeholders.
    HEADER_ABOVE = f"__HDR_ABOVE_{market_key}__"
    HEADER_BELOW = f"__HDR_BELOW_{market_key}__"

    options: list[str] = []
    label_map: dict[str, str] = {}
    row_map: dict[str, dict] = {}

    if above:
        options.append(HEADER_ABOVE)
        label_map[HEADER_ABOVE] = f"━━━ ▲ 5日線以上 ({len(above)}) ━━━"
        for r in above:
            options.append(r["symbol"])
            label_map[r["symbol"]] = f"▲ {r['symbol']}  {r['score']}/{r['max_score']}"
            row_map[r["symbol"]] = r

    if below:
        options.append(HEADER_BELOW)
        label_map[HEADER_BELOW] = f"━━━ ▼ 5日線以下 ({len(below)}) ━━━"
        for r in below:
            options.append(r["symbol"])
            label_map[r["symbol"]] = f"▼ {r['symbol']}  {r['score']}/{r['max_score']}"
            row_map[r["symbol"]] = r

    if not options:
        st.info("（此分區無資料）")
        return

    # Default to the first real entry, not a header.
    default_idx = next(
        (i for i, o in enumerate(options) if not o.startswith("__HDR_")), 0
    )

    col_left, col_mid, col_right = st.columns([2, 3, 5])

    with col_left:
        choice = st.radio(
            "ticker",
            options=options,
            index=default_idx,
            format_func=lambda o: label_map[o],
            label_visibility="collapsed",
            key=f"radio_{market_key}",
        )

    # If user lands on a header label, fall back to first real entry.
    if choice.startswith("__HDR_"):
        choice = next(o for o in options if not o.startswith("__HDR_"))

    selected = row_map[choice]

    with col_mid:
        _detail_panel(selected)

    with col_right:
        tv_symbol = selected.get("tradingview_symbol")
        if tv_symbol:
            components.html(tradingview_widget(tv_symbol), height=620)
        else:
            st.info("No TradingView symbol configured for this ticker.")


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

    rows_ok = [s for s in signals.values() if s.get("status") == "ok"]
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

    tw_rows = [r for r in rows_ok if r.get("market") == "tw"]
    us_rows = [r for r in rows_ok if r.get("market") == "us"]

    tab_tw, tab_us = st.tabs([f"台股 ({len(tw_rows)})", f"美股 ({len(us_rows)})"])
    with tab_tw:
        _market_tab(tw_rows, market_key="tw")
    with tab_us:
        _market_tab(us_rows, market_key="us")

    if rows_failed:
        with st.expander(f"Fetch failures ({len(rows_failed)})"):
            st.json(rows_failed)


render()
