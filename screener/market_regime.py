"""Market-level trend indicators."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from . import fetch, indicators


@dataclass(frozen=True)
class MarketIndex:
    symbol: str
    name: str


INDEXES = {
    "tw": [
        MarketIndex("^TWII", "加權指數"),
        MarketIndex("^TWOII", "櫃買指數"),
    ],
    "us": [
        MarketIndex("^GSPC", "S&P 500"),
        MarketIndex("^IXIC", "NASDAQ"),
        MarketIndex("^SOX", "費半"),
    ],
}

TW_STRATEGY_BENCHMARK = MarketIndex("0050.TW", "元大台灣50")
US_STRATEGY_BENCHMARK = MarketIndex("SPY", "SPDR S&P 500 ETF")
STRATEGY_LABELS = {
    "bear_crash": "空頭 / 急跌",
    "range": "區間震盪",
    "bull": "多頭",
}


def _as_of(df) -> str | None:
    if df.empty:
        return None
    last_index = df.index[-1]
    if hasattr(last_index, "date"):
        return last_index.date().isoformat()
    return str(last_index)[:10]


def _above_all_mas(snapshot: indicators.IndicatorSnapshot) -> bool:
    return all(
        value is not None and snapshot.close > value
        for value in (snapshot.ma5, snapshot.ma10, snapshot.ma20, snapshot.ma240)
    )


def _safe_last(series: pd.Series) -> float | None:
    if series.empty:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _classify_strategy_from_ohlcv(
    df: pd.DataFrame, benchmark: MarketIndex, market: str
) -> dict[str, Any]:
    close = df["Close"].astype(float)
    current = _safe_last(close)
    ma10 = _safe_last(close.rolling(10).mean())
    ma20 = _safe_last(close.rolling(20).mean())
    ma60 = _safe_last(close.rolling(60).mean())
    ma240 = _safe_last(close.rolling(240).mean())
    close_60d_ago = close.iloc[-61] if len(close) >= 61 else None
    high_120d = _safe_last(close.rolling(120).max())
    return_60d = (
        float(current) / float(close_60d_ago) - 1.0
        if current is not None and close_60d_ago is not None and close_60d_ago > 0
        else None
    )
    drawdown_120d = (
        float(current) / high_120d - 1.0
        if current is not None and high_120d is not None and high_120d > 0
        else None
    )

    if drawdown_120d is not None and drawdown_120d <= -0.12:
        strategy = "bear_crash"
        reason = f"{benchmark.symbol} 距 120 日高點回撤達 12% 以上，保留現金、降低追價。"
    elif (
        current is not None
        and ma20 is not None
        and ma60 is not None
        and ma240 is not None
        and return_60d is not None
        and current > ma20
        and current > ma60
        and ma60 > ma240
        and return_60d > 0.03
    ):
        strategy = "bull"
        reason = (
            f"{benchmark.symbol} 位於 MA20/MA60 上方，"
            "MA60 高於年線，且 60 日報酬大於 3%。"
        )
    else:
        strategy = "range"
        reason = "未達急跌或強多頭條件，維持高持股的區間震盪權重。"

    return {
        "strategy": strategy,
        "label": STRATEGY_LABELS[strategy],
        "reason": reason,
        "benchmark": benchmark.symbol,
        "benchmark_name": benchmark.name,
        "market": market,
        "as_of": _as_of(df),
        "close": current,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "ma240": ma240,
        "return_60d": return_60d,
        "drawdown_120d": drawdown_120d,
        "thresholds": {
            "bear_drawdown_120d": -0.12,
            "bull_return_60d": 0.03,
        },
    }


def classify_tw_strategy_from_ohlcv(df: pd.DataFrame) -> dict[str, Any]:
    return _classify_strategy_from_ohlcv(df, TW_STRATEGY_BENCHMARK, "tw")


def classify_us_strategy_from_ohlcv(df: pd.DataFrame) -> dict[str, Any]:
    return _classify_strategy_from_ohlcv(df, US_STRATEGY_BENCHMARK, "us")


def build_tw_strategy_snapshot() -> dict[str, Any]:
    ohlcv = fetch.fetch_ohlcv(TW_STRATEGY_BENCHMARK.symbol)
    return classify_tw_strategy_from_ohlcv(ohlcv.df)


def build_us_strategy_snapshot() -> dict[str, Any]:
    ohlcv = fetch.fetch_ohlcv(US_STRATEGY_BENCHMARK.symbol)
    return classify_us_strategy_from_ohlcv(ohlcv.df)


def build_index_snapshot(index: MarketIndex) -> dict[str, Any]:
    ohlcv = fetch.fetch_ohlcv(index.symbol)
    snapshot = indicators.compute(ohlcv.df)
    return {
        "symbol": index.symbol,
        "name": index.name,
        "as_of": _as_of(ohlcv.df),
        "close": snapshot.close,
        "today_return": snapshot.today_return,
        "ma5": snapshot.ma5,
        "ma10": snapshot.ma10,
        "ma20": snapshot.ma20,
        "ma240": snapshot.ma240,
        "above_all_mas": _above_all_mas(snapshot),
        "indicators": asdict(snapshot),
    }


def build_market_regime() -> dict[str, Any]:
    markets: dict[str, Any] = {}

    for market, configs in INDEXES.items():
        index_rows: list[dict] = []
        for config in configs:
            try:
                row = build_index_snapshot(config)
                row["status"] = "ok"
            except Exception as exc:
                row = {
                    "symbol": config.symbol,
                    "name": config.name,
                    "status": "fetch_failed",
                    "error": str(exc),
                }
            index_rows.append(row)

        markets[market] = {"indexes": index_rows}

    try:
        markets["tw"]["strategy"] = build_tw_strategy_snapshot()
    except Exception as exc:
        markets["tw"]["strategy"] = {
            "strategy": "range",
            "label": STRATEGY_LABELS["range"],
            "status": "fetch_failed",
            "error": str(exc),
        }

    try:
        markets["us"]["strategy"] = build_us_strategy_snapshot()
    except Exception as exc:
        markets["us"]["strategy"] = {
            "strategy": "range",
            "label": STRATEGY_LABELS["range"],
            "status": "fetch_failed",
            "error": str(exc),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "markets": markets,
    }
