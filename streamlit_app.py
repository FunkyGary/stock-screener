"""Streamlit dashboard — desktop (3-column) + mobile (stacked) layouts.

Layout is auto-selected from the browser viewport width:
- viewport ≥ 900 px → desktop (3-column with long radio + tall TV chart)
- viewport < 900 px → mobile (stacked with selectbox + short chart)

A `?layout=desktop|mobile` URL query parameter forces a specific layout
and bypasses auto-detection (useful for bookmarking).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
from plotly.subplots import make_subplots
from streamlit_javascript import st_javascript

REPO_RAW_URL = os.environ.get("SIGNALS_URL", "")
LOCAL_FALLBACK = Path(__file__).parent / "data" / "latest_signals.json"

TOP_PICK_MIN_SCORE_RATIO = 0.6
CHART_HEIGHT_DESKTOP = 620
CHART_HEIGHT_MOBILE = 380
VIEWPORT_BREAKPOINT_PX = 900  # ≥ this → desktop


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


def tradingview_widget(symbol: str, height: int) -> str:
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


@st.cache_data(ttl=3600, show_spinner=False)
def load_chart_ohlcv(symbol: str) -> dict:
    df = yf.download(
        symbol,
        period="6mo",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return {"error": f"No chart data for {symbol}", "rows": []}
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(set(df.columns)):
        return {"error": f"Missing chart columns for {symbol}", "rows": []}

    chart_df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    for window in (5, 10, 20, 240):
        chart_df[f"MA{window}"] = chart_df["Close"].rolling(window).mean()
    chart_df = chart_df.tail(130).reset_index()
    chart_df = chart_df.rename(columns={chart_df.columns[0]: "Date"})
    chart_df["Date"] = chart_df["Date"].astype(str)
    return {"error": None, "rows": chart_df.to_dict("records")}


def local_price_chart(symbol: str, name: str, height: int) -> None:
    payload = load_chart_ohlcv(symbol)
    if payload.get("error"):
        st.info(payload["error"])
        return

    rows = payload["rows"]
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.74, 0.26],
    )
    dates = [r["Date"] for r in rows]

    fig.add_trace(
        go.Candlestick(
            x=dates,
            open=[r["Open"] for r in rows],
            high=[r["High"] for r in rows],
            low=[r["Low"] for r in rows],
            close=[r["Close"] for r in rows],
            name=symbol,
            increasing_line_color="#ef5350",
            decreasing_line_color="#26a69a",
            increasing_fillcolor="#ef5350",
            decreasing_fillcolor="#26a69a",
        ),
        row=1,
        col=1,
    )

    ma_colors = {
        "MA5": "#f9c74f",
        "MA10": "#4cc9f0",
        "MA20": "#b5179e",
        "MA240": "#adb5bd",
    }
    for ma, color in ma_colors.items():
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=[r.get(ma) for r in rows],
                mode="lines",
                line={"width": 1.4, "color": color},
                name=ma,
                connectgaps=False,
            ),
            row=1,
            col=1,
        )

    volume_colors = [
        "#ef5350" if r["Close"] >= r["Open"] else "#26a69a" for r in rows
    ]
    fig.add_trace(
        go.Bar(
            x=dates,
            y=[r["Volume"] for r in rows],
            marker_color=volume_colors,
            name="Volume",
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        height=height,
        template="plotly_dark",
        title={"text": f"{name} ({symbol})", "font": {"size": 16}},
        margin={"l": 10, "r": 10, "t": 42, "b": 10},
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.01,
            "xanchor": "right",
            "x": 1,
        },
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.08)")
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})


def render_chart(selected: dict, height: int) -> None:
    if selected.get("market") == "tw":
        local_price_chart(selected["symbol"], selected["name"], height)
        return

    tv_symbol = selected.get("tradingview_symbol")
    if tv_symbol:
        components.html(tradingview_widget(tv_symbol, height), height=height + 20)
    else:
        st.info("No TradingView symbol configured for this ticker.")


def _is_above_all_mas(row: dict) -> bool:
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


# ---------------- shared detail panel ----------------


def _detail_panel(selected: dict, *, mobile: bool) -> None:
    above = _is_above_all_mas(selected)
    tag = "▲ 站上全均線" if above else "▼ 未站上"

    if mobile:
        col_a, col_b = st.columns([3, 2])
        with col_a:
            st.markdown(f"### {selected['symbol']}")
            st.caption(f"{selected['name']} · {tag}")
        with col_b:
            st.metric("Score", f"{selected['score']} / {selected['max_score']}")
    else:
        st.subheader(f"{selected['name']} ({selected['symbol']})")
        st.caption(tag)
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


# ---------------- mobile layout ----------------


def _market_view_mobile(rows: list[dict], market_key: str) -> None:
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

    # ⭐ Top picks
    top = [r for r in above if _score_ratio(r) >= TOP_PICK_MIN_SCORE_RATIO]
    if top:
        with st.expander(
            f"⭐ 今日精選 — 站上全均線且分數 ≥ {int(TOP_PICK_MIN_SCORE_RATIO * 100)}% ({len(top)})",
            expanded=True,
        ):
            for r in top[:20]:
                st.markdown(
                    f"**{r['symbol']}**　{r['score']}/{r['max_score']}　·　{r['name']}"
                )

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

    label_to_row: dict[str, dict] = {}
    for r in candidates:
        sym_tag = "▲" if _is_above_all_mas(r) else "▼"
        label = (
            f"{sym_tag} {r['symbol']}  {r['score']}/{r['max_score']}  ({r['name']})"
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
    _detail_panel(selected, mobile=True)

    st.divider()
    render_chart(selected, CHART_HEIGHT_MOBILE)


# ---------------- desktop layout (original 3-column) ----------------


def _market_view_desktop(rows: list[dict], market_key: str) -> None:
    above = sorted(
        [r for r in rows if _is_above_all_mas(r)],
        key=lambda r: (r.get("score", 0), r.get("max_score", 0)),
        reverse=True,
    )
    below = sorted(
        [r for r in rows if not _is_above_all_mas(r)],
        key=lambda r: (r.get("score", 0), r.get("max_score", 0)),
        reverse=True,
    )

    HEADER_ABOVE = f"__HDR_ABOVE_{market_key}__"
    HEADER_BELOW = f"__HDR_BELOW_{market_key}__"

    options: list[str] = []
    label_map: dict[str, str] = {}
    row_map: dict[str, dict] = {}

    if above:
        options.append(HEADER_ABOVE)
        label_map[HEADER_ABOVE] = (
            f"━━━ ▲ 站上全均線 5/10/20/年 ({len(above)}) ━━━"
        )
        for r in above:
            options.append(r["symbol"])
            label_map[r["symbol"]] = (
                f"▲ {r['symbol']}  {r['score']}/{r['max_score']}"
            )
            row_map[r["symbol"]] = r

    if below:
        options.append(HEADER_BELOW)
        label_map[HEADER_BELOW] = f"━━━ ▼ 未站上全均線 ({len(below)}) ━━━"
        for r in below:
            options.append(r["symbol"])
            label_map[r["symbol"]] = (
                f"▼ {r['symbol']}  {r['score']}/{r['max_score']}"
            )
            row_map[r["symbol"]] = r

    if not options:
        st.info("（此分區無資料）")
        return

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

    if choice.startswith("__HDR_"):
        choice = next(o for o in options if not o.startswith("__HDR_"))

    selected = row_map[choice]

    with col_mid:
        _detail_panel(selected, mobile=False)

    with col_right:
        render_chart(selected, CHART_HEIGHT_DESKTOP)


# ---------------- entry point ----------------


def render() -> None:
    st.set_page_config(
        page_title="Stock Screener",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Auto-detect layout from viewport width via a tiny JS bridge.
    # On first render, st_javascript returns None; we default to mobile to
    # avoid showing a cramped 3-column layout on a phone. Once the JS
    # resolves (~one rerun later) the real width is used.
    # URL param ?layout=desktop|mobile bypasses detection (bookmarkable).
    forced = st.query_params.get("layout")
    if forced in ("mobile", "desktop"):
        current_layout = forced
    else:
        viewport_width = st_javascript("window.innerWidth", key="vw")
        if viewport_width is None or viewport_width == 0:
            current_layout = "mobile"  # pre-detection fallback
        else:
            current_layout = (
                "desktop" if viewport_width >= VIEWPORT_BREAKPOINT_PX else "mobile"
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
    short_gen = (
        generated[:16].replace("T", " ") if isinstance(generated, str) else "n/a"
    )

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

    market_view = (
        _market_view_desktop if current_layout == "desktop" else _market_view_mobile
    )

    tab_tw, tab_us = st.tabs(
        [f"台股 ({len(tw_rows)})", f"美股 ({len(us_rows)})"]
    )
    with tab_tw:
        market_view(tw_rows, market_key="tw")
    with tab_us:
        market_view(us_rows, market_key="us")

    if rows_failed:
        with st.expander(f"⚠️ Fetch failures ({len(rows_failed)})"):
            st.json(rows_failed)


render()
