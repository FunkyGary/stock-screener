"""Streamlit dashboard for daily stock signals — mobile-first layout."""

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

TOP_PICK_MIN_SCORE_RATIO = 0.6  # show in 今日精選 if score/max ≥ 60%
CHART_HEIGHT = 380  # smaller than the desktop default; fits a phone viewport


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


def tradingview_widget(symbol: str, height: int = CHART_HEIGHT) -> str:
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
        "allow_symbol_change": false,
        "hide_side_toolbar": true,
        "hide_top_toolbar": false
      }});
    </script>
    """


def _is_above_all_mas(row: dict) -> bool:
    """True iff close > MA5, MA10, MA20, AND MA240."""
    ind = row.get("indicators") or {}
    close = ind.get("close")
    if close is None:
        return False
    for key in ("ma5", "ma10", "ma20", "ma240"):
        val = ind.get(key)
        if val is None or close <= val:
            return False
    return True


def _score_ratio(row: dict) -> float:
    mx = row.get("max_score", 0) or 0
    return (row.get("score", 0) / mx) if mx else 0.0


def _detail_panel(selected: dict) -> None:
    above = _is_above_all_mas(selected)
    tag = "▲ 站上全均線" if above else "▼ 未站上"

    # Compact header row: ticker on left, score on right
    col_a, col_b = st.columns([3, 2])
    with col_a:
        st.markdown(f"### {selected['symbol']}")
        st.caption(f"{selected['name']} · {tag}")
    with col_b:
        st.metric("Score", f"{selected['score']} / {selected['max_score']}")

    st.markdown("**訊號**")
    for reason in selected.get("reasons", []):
        marker = "✅" if reason["passed"] else "⬜"
        st.markdown(f"{marker} **{reason['rule']}**")
        st.caption(f"　{reason['detail']}")

    with st.expander("📊 Raw indicators"):
        st.json(selected.get("indicators", {}))
    if selected.get("analyst"):
        with st.expander("📈 Analyst"):
            st.json(selected["analyst"])
    if selected.get("chip"):
        with st.expander("🏦 Chip 籌碼"):
            st.json(selected["chip"])


def _market_view(rows: list[dict], market_key: str) -> None:
    above = sorted(
        [r for r in rows if _is_above_all_mas(r)],
        key=lambda r: (_score_ratio(r), r.get("score", 0)),
        reverse=True,
    )
    below = sorted(
        [r for r in rows if not _is_above_all_mas(r)],
        key=lambda r: (_score_ratio(r), r.get("score", 0)),
        reverse=True,
    )

    # ⭐ Top picks: above-all-MAs AND score ratio ≥ threshold
    top = [r for r in above if _score_ratio(r) >= TOP_PICK_MIN_SCORE_RATIO]
    if top:
        with st.expander(
            f"⭐ 今日精選 — 站上全均線且分數 ≥ {int(TOP_PICK_MIN_SCORE_RATIO * 100)}% ({len(top)})",
            expanded=True,
        ):
            for r in top[:20]:  # cap at 20 to keep list scannable
                st.markdown(
                    f"**{r['symbol']}**　{r['score']}/{r['max_score']}　·　{r['name']}"
                )

    # Bucket filter
    options = []
    if above:
        options.append(f"▲ 站上 ({len(above)})")
    if below:
        options.append(f"▼ 未站上 ({len(below)})")
    options.append("全部")

    if not (above or below):
        st.info("（無資料）")
        return

    filter_choice = st.radio(
        "分組",
        options=options,
        index=0,
        horizontal=True,
        key=f"filter_{market_key}",
        label_visibility="collapsed",
    )

    if filter_choice.startswith("▲"):
        candidates = above
    elif filter_choice.startswith("▼"):
        candidates = below
    else:
        candidates = above + below

    if not candidates:
        st.info("此分組無資料")
        return

    # Searchable selectbox — type to filter, scroll on mobile is fine.
    label_to_row: dict[str, dict] = {}
    for r in candidates:
        tag = "▲" if _is_above_all_mas(r) else "▼"
        label = (
            f"{tag} {r['symbol']}  {r['score']}/{r['max_score']}  ({r['name']})"
        )
        label_to_row[label] = r

    selected_label = st.selectbox(
        "選擇個股",
        options=list(label_to_row.keys()),
        index=0,
        key=f"sel_{market_key}",
    )
    selected = label_to_row[selected_label]

    st.divider()
    _detail_panel(selected)

    st.divider()
    tv_symbol = selected.get("tradingview_symbol")
    if tv_symbol:
        components.html(tradingview_widget(tv_symbol), height=CHART_HEIGHT + 20)
    else:
        st.info("No TradingView symbol configured for this ticker.")


def render() -> None:
    st.set_page_config(
        page_title="Stock Screener",
        layout="centered",  # narrower content area, looks better on mobile + desktop
        initial_sidebar_state="collapsed",
    )

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
    # Short generated stamp: trim to "YYYY-MM-DD HH:MM"
    short_gen = generated[:16].replace("T", " ") if isinstance(generated, str) else "n/a"

    tw_rows = [r for r in rows_ok if r.get("market") == "tw"]
    us_rows = [r for r in rows_ok if r.get("market") == "us"]

    st.caption(
        f"Last update: {short_gen} UTC · TW {len(tw_rows)} · US {len(us_rows)}"
        + (f" · {len(rows_failed)} failed" if rows_failed else "")
    )

    if not rows_ok:
        st.warning("All signals failed to fetch.")
        if rows_failed:
            with st.expander(f"Fetch failures ({len(rows_failed)})"):
                st.json(rows_failed)
        return

    tab_tw, tab_us = st.tabs([f"台股 ({len(tw_rows)})", f"美股 ({len(us_rows)})"])
    with tab_tw:
        _market_view(tw_rows, market_key="tw")
    with tab_us:
        _market_view(us_rows, market_key="us")

    if rows_failed:
        with st.expander(f"⚠️ Fetch failures ({len(rows_failed)})"):
            st.json(rows_failed)


render()
