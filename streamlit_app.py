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
from datetime import datetime, timedelta, timezone
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
LOCAL_TARGET_EVENTS = Path(__file__).parent / "data" / "analyst_target_events.jsonl"
LOCAL_TW_TARGET_EVENTS = Path(__file__).parent / "data" / "tw_target_events.jsonl"

TOP_PICK_MIN_SCORE_RATIO = 0.6
SPECIAL_ATTENTION_MIN_SCORE_RATIO = 0.5
SPECIAL_ATTENTION_MIN_SCORE_RATIOS = {
    "bear_downtrend": 0.7,
}
CHART_HEIGHT_DESKTOP = 620
CHART_HEIGHT_MOBILE = 380
VIEWPORT_BREAKPOINT_PX = 900  # ≥ this → desktop


@st.cache_data(ttl=60)
def load_signals() -> dict:
    if LOCAL_FALLBACK.exists():
        try:
            with LOCAL_FALLBACK.open() as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            st.error(f"Failed to parse local signals JSON: {exc}")
            return {"signals": {}, "generated_at": None, "last_run": {}}
    if REPO_RAW_URL:
        try:
            with urlopen(REPO_RAW_URL, timeout=10) as response:
                return json.load(response)
        except (URLError, json.JSONDecodeError) as exc:
            st.error(f"Failed to load signals: {exc}")
    return {"signals": {}, "generated_at": None, "last_run": {}}


@st.cache_data(ttl=300)
def load_target_event_history() -> list[dict]:
    events: list[dict] = []
    for path in (LOCAL_TARGET_EVENTS, LOCAL_TW_TARGET_EVENTS):
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


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

    volume_colors = ["#ef5350" if r["Close"] >= r["Open"] else "#26a69a" for r in rows]
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
        dragmode=False,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.08)")
    st.plotly_chart(
        fig,
        width="stretch",
        config={
            "displayModeBar": False,
            "displaylogo": False,
            "scrollZoom": False,
            "staticPlot": False,
        },
    )


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


def _was_above_all_mas_prev_day(row: dict) -> bool:
    ind = row.get("indicators") or {}
    prev_close = ind.get("prev_close")
    if prev_close is None:
        return False
    for key in ("prev_ma5", "prev_ma10", "prev_ma20", "prev_ma240"):
        val = ind.get(key)
        if val is None or prev_close <= val:
            return False
    return True


def _has_prev_all_ma_data(row: dict) -> bool:
    ind = row.get("indicators") or {}
    return ind.get("prev_close") is not None and all(
        ind.get(key) is not None
        for key in ("prev_ma5", "prev_ma10", "prev_ma20", "prev_ma240")
    )


def _is_newly_above_all_mas(row: dict) -> bool:
    return (
        _is_above_all_mas(row)
        and _has_prev_all_ma_data(row)
        and not _was_above_all_mas_prev_day(row)
    )


def _has_active_target_raise(row: dict) -> bool:
    return any(
        "目標價" in reason.get("rule", "") and reason.get("passed") is True
        for reason in row.get("reasons", [])
    )


def _parse_event_date(event: dict) -> datetime | None:
    raw = event.get("published_at") or event.get("event_date") or event.get("date")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _has_recent_research_report(row: dict, days: int = 7) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    events = (row.get("analyst") or {}).get("target_price_events") or []
    return any(
        event_date is not None and event_date.date() >= cutoff
        for event in events
        for event_date in (_parse_event_date(event),)
    )


def _is_special_attention(row: dict) -> bool:
    min_ratio = _special_attention_min_score_ratio(row)
    if _score_ratio(row) < min_ratio:
        return False
    if row.get("market") == "tw":
        return True
    return _is_newly_above_all_mas(row)


def _special_attention_min_score_ratio(row: dict) -> float:
    strategy = (row.get("score_regime") or {}).get("strategy")
    return SPECIAL_ATTENTION_MIN_SCORE_RATIOS.get(
        strategy, SPECIAL_ATTENTION_MIN_SCORE_RATIO
    )


def _sell_pressure_passed(row: dict, rule_part: str | None = None) -> bool:
    for reason in row.get("reasons", []):
        rule = reason.get("rule", "")
        if not rule.startswith("賣壓扣分"):
            continue
        if reason.get("passed") is not True:
            continue
        if rule_part is None or rule_part in rule:
            return True
    return False


def _market_below_ma10(row: dict) -> bool:
    regime = row.get("score_regime") or {}
    close = regime.get("close")
    ma10 = regime.get("ma10")
    if close is not None and ma10 is not None:
        return close < ma10
    return _sell_pressure_passed(row, "大盤跌破 10 日線")


def _downside_attention_reason(row: dict) -> str | None:
    ind = row.get("indicators") or {}
    close = ind.get("close")
    strategy = (row.get("score_regime") or {}).get("strategy") or "range"
    if close is None:
        return None

    if strategy == "bear_crash":
        prev_5d_low = ind.get("prev_5d_low")
        if prev_5d_low is not None and close < prev_5d_low:
            return "空頭：跌破近 5 日低點"
        return None

    if strategy == "bear_downtrend":
        if _score_ratio(row) < 0.20 and _sell_pressure_passed(row):
            return "震盪走低：賣壓扣分後 < 20%"
        return None

    if strategy == "bull":
        ma5 = ind.get("ma5")
        if ma5 is not None and close < ma5 and _score_ratio(row) < 0.20:
            return "多頭：跌破 MA5 且分數 < 20%"
        return None

    if _score_ratio(row) < 0.20 and _sell_pressure_passed(row):
        if _sell_pressure_passed(row, "跌破大量長紅 K 低點"):
            return "震盪：跌破大量長紅 K 低點且分數 < 20%"
        if _market_below_ma10(row):
            return "震盪：賣壓扣分後 < 20%，且大盤跌破 MA10"
        return "震盪：賣壓扣分後 < 20%"
    return None


def _is_downside_attention(row: dict) -> bool:
    return _downside_attention_reason(row) is not None


def _trend_tag(row: dict) -> str:
    downside_reason = _downside_attention_reason(row)
    if downside_reason:
        return f"下跌特別注意 · {downside_reason}"
    newly_above = _is_newly_above_all_mas(row)
    target_raise = _has_active_target_raise(row)
    if newly_above and target_raise:
        return "今日站上全均線 · 🎯 目標價上調"
    if newly_above:
        return "今日站上全均線"
    if target_raise:
        return "🎯 目標價上調"
    if _has_recent_research_report(row):
        return "研究報告 7日內"
    if _is_above_all_mas(row):
        return "▲ 全均線之上"
    return "▼ 其他"


def _score_ratio(row: dict) -> float:
    mx = row.get("max_score", 0) or 0
    return (row.get("score", 0) / mx) if mx else 0.0


def _score_value(value: float | int | None) -> str:
    return f"{float(value or 0):g}"


def _score_label(row: dict) -> str:
    return f"{_score_value(row.get('score'))}/{_score_value(row.get('max_score'))}"


def _today_return_label(row: dict) -> str:
    value = (row.get("indicators") or {}).get("today_return")
    return f"{value * 100:+.2f}%" if value is not None else "n/a"


def _name_with_return(row: dict) -> str:
    return f"{row['name']} {_today_return_label(row)}"


def _list_primary_label(row: dict, market_key: str) -> str:
    return row["name"] if market_key == "tw" else row["symbol"]


def _list_row_label(row: dict, market_key: str, prefix: str = "") -> str:
    primary = _list_primary_label(row, market_key)
    if market_key == "tw":
        return f"{prefix}{primary}  {_score_label(row)}  {_today_return_label(row)}"
    return f"{prefix}{primary}  {_score_label(row)}  ({_name_with_return(row)})"


def _top_pick_label(row: dict, market_key: str) -> str:
    primary = _list_primary_label(row, market_key)
    detail = _today_return_label(row) if market_key == "tw" else _name_with_return(row)
    return f"**{primary}**　{_score_label(row)}　·　{detail}"


def _fmt_pct(value: float | int | None) -> str:
    return f"{float(value) * 100:+.2f}%" if value is not None else "n/a"


def _fmt_price(value: float | int | None) -> str:
    return f"{float(value):.2f}" if value is not None else "n/a"


def _target_event_line(event: dict) -> str:
    date = event.get("date") or event.get("event_date") or "n/a"
    firm = event.get("firm") or event.get("source") or "Unknown"
    previous = event.get("previous_target")
    target = event.get("target_price")
    target_text = _fmt_price(target)
    if previous is not None:
        target_text = f"{_fmt_price(previous)} → {target_text}"
    upside = _fmt_pct(event.get("upside_pct"))
    raise_pct = _fmt_pct(event.get("raise_pct"))
    return f"{date}　{firm}　目標價 {target_text}　距現價 {upside}　上調 {raise_pct}"


def _market_regime_item(row: dict) -> str:
    mark = "V" if row.get("status") == "ok" and row.get("above_all_mas") else "X"
    return f"`{mark}` {row.get('name')}"


def _strategy_weight_summary(strategy: str | None, market: str | None = None) -> str:
    if market == "us":
        summaries = {
            "bear_crash": "美股空頭/急跌權重：新高 2.25、站上全均線 1.5、相對強度 1、MACD 0.75",
            "bear_downtrend": "美股空頭/震盪走低權重：新高 2.25、站上全均線 1.5、相對強度 1、MACD 0.75",
            "range": "美股區間權重：站上全均線 3、放量 2.25、短線趨勢 0.75、相對強度 1",
            "bull": "美股多頭權重：站上全均線 4.5、相對強度 3、新高 0.75、放量 0.75",
        }
    else:
        summaries = {
            "bear_crash": "台股空頭/急跌權重：新高 2.25、目標價 1、板塊 0.75、投信 1、外資 0.5",
            "bear_downtrend": "台股空頭/震盪走低權重：進場門檻 70%、新高 2.25、目標價 1、板塊 0.75、投信 1、外資 0.5",
            "range": "台股區間權重：站上全均線 4.5、短線趨勢 2.25、新高 0.75、相對強度 1",
            "bull": "台股多頭權重：站上全均線 4.5、放量 2.25、相對強度 3、新高 0.75",
        }
    return summaries.get(strategy or "", "權重：使用預設設定")


def _strategy_line(strategy: dict) -> str:
    label = strategy.get("label") or strategy.get("strategy") or "區間震盪"
    close = _fmt_price(strategy.get("close"))
    drawdown = _fmt_pct(strategy.get("drawdown_120d"))
    ret_60d = _fmt_pct(strategy.get("return_60d"))
    as_of = strategy.get("as_of") or "n/a"
    benchmark = strategy.get("benchmark") or strategy.get("benchmark_name") or "benchmark"
    return (
        f"策略：**{label}**　"
        f"{benchmark} {close}　120日回撤 {drawdown}　60日報酬 {ret_60d}　"
        f"as of {as_of}"
    )


def render_market_regime(market_regime: dict | None, market_key: str) -> None:
    market = ((market_regime or {}).get("markets") or {}).get(market_key)
    if not market:
        st.info("大盤指標尚未產生，下一次資料更新後會顯示。")
        return

    st.markdown("#### 大盤指標")
    strategy = market.get("strategy")
    if strategy:
        st.markdown(_strategy_line(strategy))
        st.caption(
            f"{strategy.get('reason', '')}　"
            f"{_strategy_weight_summary(strategy.get('strategy'), market_key)}"
        )
    indexes = market.get("indexes", [])
    st.markdown("　　".join(_market_regime_item(row) for row in indexes))


def render_sector_strength(rows: list[dict]) -> None:
    by_group: dict[str, dict] = {}
    for row in rows:
        sector = row.get("sector") or {}
        group = sector.get("group_name")
        if not group or (sector.get("strong_days") or 0) <= 0:
            continue
        current = by_group.get(group)
        if current is None or sector.get("strong_days", 0) > current.get(
            "strong_days", 0
        ):
            by_group[group] = sector

    if not by_group:
        st.caption("強勢板塊：無")
        return

    sectors = sorted(
        by_group.values(),
        key=lambda s: (
            s.get("strong_days", 0),
            s.get("return_5d", 0),
            s.get("breadth_above_ma5", 0),
        ),
        reverse=True,
    )
    labels = [
        (
            f"{s['group_name']} {s['strong_days']}日 "
            f"5d {s.get('return_5d', 0) * 100:+.1f}% "
            f"MA5 {s.get('breadth_above_ma5', 0) * 100:.0f}%"
        )
        for s in sectors[:6]
    ]
    st.caption("強勢板塊：" + "　".join(labels))


def _special_symbol_prefix(row: dict) -> str:
    return "🎯 " if _has_active_target_raise(row) else ""


def _target_event_sort_key(event: dict) -> str:
    return (
        event.get("published_at") or event.get("event_date") or event.get("date") or ""
    )


def _recent_target_history(symbol: str, days: int = 180) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    rows: list[dict] = []
    for event in load_target_event_history():
        if event.get("symbol") != symbol:
            continue
        event_date = event.get("event_date") or event.get("date")
        if isinstance(event_date, str):
            try:
                if datetime.fromisoformat(event_date).date() < cutoff:
                    continue
            except ValueError:
                pass
        rows.append(event)
    return sorted(rows, key=_target_event_sort_key, reverse=True)


# ---------------- shared detail panel ----------------


def _detail_panel(selected: dict, *, mobile: bool) -> None:
    tag = _trend_tag(selected)

    if mobile:
        col_a, col_b = st.columns([3, 2])
        with col_a:
            st.markdown(f"### {selected['symbol']}")
            st.caption(f"{_name_with_return(selected)} · {tag}")
        with col_b:
            st.metric("Score", _score_label(selected))
    else:
        st.subheader(f"{_name_with_return(selected)} ({selected['symbol']})")
        st.caption(tag)
        st.metric("Score", _score_label(selected))

    score_regime = selected.get("score_regime") or {}
    if score_regime:
        market = selected.get("market")
        st.caption(
            f"計分策略：{score_regime.get('label') or score_regime.get('strategy')} · "
            f"{_strategy_weight_summary(score_regime.get('strategy'), market)}"
        )

    st.markdown("**訊號**")
    for reason in selected.get("reasons", []):
        marker = "✅" if reason["passed"] else "⬜"
        weight = reason.get("weight")
        earned = reason.get("score") if reason.get("passed") else None
        if isinstance(earned, (int, float)) and earned < 0 and weight == 0:
            suffix = f" · 扣{abs(earned) * 100:g}%"
        elif isinstance(earned, (int, float)) and isinstance(weight, (int, float)):
            suffix = (
                f" · {earned:g}/{weight:g}分"
                if earned != weight
                else f" · {weight:g}分"
            )
        else:
            suffix = f" · {weight:g}分" if isinstance(weight, (int, float)) else ""
        st.markdown(f"{marker} **{reason['rule']}**{suffix}")
        st.caption(f"　{reason['detail']}")

    if selected.get("analyst"):
        with st.expander("📈 Analyst"):
            events = (selected.get("analyst") or {}).get("target_price_events") or []
            if events:
                st.markdown("**近期目標價上調**")
                for event in events:
                    url = event.get("url")
                    line = _target_event_line(event)
                    if url:
                        st.markdown(f"- [{line}]({url})")
                    else:
                        st.markdown(f"- {line}")
            history = _recent_target_history(selected["symbol"])
            if history:
                st.markdown("**近 180 天目標價歷史**")
                for event in history[:30]:
                    url = event.get("url")
                    line = _target_event_line(event)
                    if url:
                        st.markdown(f"- [{line}]({url})")
                    else:
                        st.markdown(f"- {line}")


# ---------------- mobile layout ----------------


def _market_view_mobile(rows: list[dict], market_key: str) -> None:
    special = sorted(
        [r for r in rows if _is_special_attention(r)],
        key=lambda r: (_score_ratio(r), r.get("score", 0)),
        reverse=True,
    )
    downside = sorted(
        [r for r in rows if _is_downside_attention(r)],
        key=lambda r: r.get("indicators", {}).get("today_return") or 0,
    )
    research = sorted(
        [
            r
            for r in rows
            if _has_recent_research_report(r)
            and not _is_special_attention(r)
            and not _is_downside_attention(r)
        ],
        key=lambda r: (_score_ratio(r), r.get("score", 0)),
        reverse=True,
    )
    above = sorted(
        [
            r
            for r in rows
            if _is_above_all_mas(r)
            and not _is_special_attention(r)
            and not _is_downside_attention(r)
            and not _has_recent_research_report(r)
        ],
        key=lambda r: (_score_ratio(r), r.get("score", 0)),
        reverse=True,
    )
    below = sorted(
        [
            r
            for r in rows
            if not _is_above_all_mas(r)
            and not _is_special_attention(r)
            and not _is_downside_attention(r)
            and not _has_recent_research_report(r)
        ],
        key=lambda r: (_score_ratio(r), r.get("score", 0)),
        reverse=True,
    )

    # ⭐ Top picks
    top = [
        r
        for r in rows
        if (_is_special_attention(r) or _is_above_all_mas(r))
        and _score_ratio(r) >= TOP_PICK_MIN_SCORE_RATIO
    ]
    top = sorted(top, key=lambda r: (_score_ratio(r), r.get("score", 0)), reverse=True)
    if top:
        with st.expander(
            f"⭐ 今日精選 — 分數 ≥ {int(TOP_PICK_MIN_SCORE_RATIO * 100)}% ({len(top)})",
            expanded=True,
        ):
            for r in top[:20]:
                st.markdown(_top_pick_label(r, market_key))

    options = []
    options.append(f"特別注意 ({len(special)})")
    options.append(f"研究報告 7日內 ({len(research)})")
    options.append(f"下跌特別注意 ({len(downside)})")
    options.append(f"全均線之上 ({len(above)})")
    options.append(f"其他 ({len(below)})")
    options.append("全部")

    if not (special or research or downside or above or below):
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
    if filter_choice.startswith("特別注意"):
        candidates = special
    elif filter_choice.startswith("研究報告"):
        candidates = research
    elif filter_choice.startswith("下跌特別注意"):
        candidates = downside
    elif filter_choice.startswith("全均線之上"):
        candidates = above
    elif filter_choice.startswith("其他"):
        candidates = below
    else:
        candidates = special + research + downside + above + below

    if not candidates:
        st.info("此分組無資料")
        return

    label_to_row: dict[str, dict] = {}
    for r in candidates:
        if _is_special_attention(r):
            sym_tag = _special_symbol_prefix(r)
        else:
            sym_tag = ""
        label = _list_row_label(r, market_key, prefix=sym_tag)
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
    special = sorted(
        [r for r in rows if _is_special_attention(r)],
        key=lambda r: (r.get("score", 0), r.get("max_score", 0)),
        reverse=True,
    )
    downside = sorted(
        [r for r in rows if _is_downside_attention(r)],
        key=lambda r: r.get("indicators", {}).get("today_return") or 0,
    )
    research = sorted(
        [
            r
            for r in rows
            if _has_recent_research_report(r)
            and not _is_special_attention(r)
            and not _is_downside_attention(r)
        ],
        key=lambda r: (r.get("score", 0), r.get("max_score", 0)),
        reverse=True,
    )
    above = sorted(
        [
            r
            for r in rows
            if _is_above_all_mas(r)
            and not _is_special_attention(r)
            and not _is_downside_attention(r)
            and not _has_recent_research_report(r)
        ],
        key=lambda r: (r.get("score", 0), r.get("max_score", 0)),
        reverse=True,
    )
    below = sorted(
        [
            r
            for r in rows
            if not _is_above_all_mas(r)
            and not _is_special_attention(r)
            and not _is_downside_attention(r)
            and not _has_recent_research_report(r)
        ],
        key=lambda r: (r.get("score", 0), r.get("max_score", 0)),
        reverse=True,
    )

    row_map: dict[str, dict] = {}
    sections = [
        (
            "special",
            f"特別注意：分數達當前策略門檻 ({len(special)})",
            special,
        ),
        ("research", f"研究報告 7日內 ({len(research)})", research),
        (
            "downside",
            f"下跌特別注意：依當前多空震盪賣出訊號 ({len(downside)})",
            downside,
        ),
        ("above", f"全均線之上 5/10/20/年 ({len(above)})", above),
        ("below", f"其他 ({len(below)})", below),
    ]
    for _, _, section_rows in sections:
        for r in section_rows:
            row_map[r["symbol"]] = r

    if not row_map:
        st.info("（此分區無資料）")
        return

    col_left, col_mid, col_right = st.columns([2, 3, 5])
    selected_key = f"selected_{market_key}"
    if st.session_state.get(selected_key) not in row_map:
        st.session_state[selected_key] = next(iter(row_map))

    def _select_symbol(symbol: str) -> None:
        st.session_state[selected_key] = symbol

    with col_left:
        for section_id, title, section_rows in sections:
            with st.expander(title, expanded=True):
                st.markdown(
                    '<div class="desktop-stock-list">', unsafe_allow_html=True
                )
                if not section_rows:
                    st.caption("無")
                else:
                    current_symbol = st.session_state[selected_key]
                    for r in section_rows:
                        symbol = r["symbol"]
                        selected = symbol == current_symbol
                        st.button(
                            _list_row_label(r, market_key),
                            key=f"pick_{market_key}_{section_id}_{symbol}",
                            type="primary" if selected else "secondary",
                            use_container_width=False,
                            on_click=_select_symbol,
                            args=(symbol,),
                        )
                st.markdown("</div>", unsafe_allow_html=True)

    selected = row_map[st.session_state[selected_key]]

    with col_mid:
        _detail_panel(selected, mobile=False)

    with col_right:
        render_chart(selected, CHART_HEIGHT_DESKTOP)


def render() -> None:
    st.set_page_config(
        page_title="Stock Screener",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(
        """
        <style>
        div.stButton > button {
            min-height: 1.7rem;
            padding: 0.12rem 0.45rem;
            line-height: 1.15;
        }
        details[data-testid="stExpander"] {
            margin-bottom: 0.35rem;
        }
        details[data-testid="stExpander"] summary {
            min-height: 2rem;
            padding: 0.18rem 0.55rem;
        }
        details[data-testid="stExpander"] summary p {
            line-height: 1.2;
        }
        details[data-testid="stExpander"] div[data-testid="stExpanderDetails"] {
            padding: 0.35rem 0.45rem 0.45rem;
        }
        .desktop-stock-list {
            padding: 0;
        }
        .desktop-stock-list div.stButton {
            margin-bottom: 0.04rem;
        }
        .desktop-stock-list div.stButton > button {
            min-height: 1.65rem;
            padding: 0.1rem 0.4rem;
        }
        .desktop-stock-list div.stButton > button[kind="secondary"] {
            border-color: transparent;
            background: transparent;
            box-shadow: none;
        }
        .desktop-stock-list div.stButton > button[kind="secondary"]:hover {
            border-color: transparent;
            background: rgba(49, 51, 63, 0.06);
        }
        @media (min-width: 900px) {
            /* Split-pane: fix the 3-column block to viewport height so all columns scroll independently */
            div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)) {
                height: calc(100vh - 8rem);
                overflow: hidden;
                align-items: stretch !important;
            }
            div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)) > div[data-testid="column"] {
                overflow-y: auto;
                overflow-x: hidden;
                min-height: 0;
                scrollbar-gutter: stable;
            }
            div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)) > div[data-testid="column"]::-webkit-scrollbar {
                width: 0.45rem;
            }
            div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)) > div[data-testid="column"]::-webkit-scrollbar-thumb {
                background: rgba(120, 126, 140, 0.35);
                border-radius: 999px;
            }
            div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)) > div[data-testid="column"]::-webkit-scrollbar-thumb:hover {
                background: rgba(120, 126, 140, 0.55);
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
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

    tab_tw, tab_us = st.tabs([f"台股 ({len(tw_rows)})", f"美股 ({len(us_rows)})"])
    with tab_tw:
        render_market_regime(data.get("market_regime"), "tw")
        render_sector_strength(tw_rows)
        market_view(tw_rows, market_key="tw")
    with tab_us:
        render_market_regime(data.get("market_regime"), "us")
        render_sector_strength(us_rows)
        market_view(us_rows, market_key="us")

    if rows_failed:
        with st.expander(f"⚠️ Fetch failures ({len(rows_failed)})"):
            st.json(rows_failed)


render()
