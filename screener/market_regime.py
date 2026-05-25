"""Market-level regime indicators and exposure guidance."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from . import fetch, indicators, io


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

BULLISH_SENTIMENT = {
    "tw": {"retail_short"},
    "us": {"extreme_fear"},
}

BEARISH_SENTIMENT = {
    "tw": {"retail_long"},
    "us": {"extreme_greed"},
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


def _below_all_mas(snapshot: indicators.IndicatorSnapshot) -> bool:
    return all(
        value is not None and snapshot.close < value
        for value in (snapshot.ma5, snapshot.ma10, snapshot.ma20, snapshot.ma240)
    )


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
        "below_all_mas": _below_all_mas(snapshot),
        "indicators": asdict(snapshot),
    }


def _sentiment_signal(market: str, sentiment: dict[str, Any]) -> str | None:
    if market == "tw":
        bias = sentiment.get("retail_mtx_bias")
        if bias == "short":
            return "retail_short"
        if bias == "long":
            return "retail_long"
    if market == "us":
        fear_greed = sentiment.get("fear_greed")
        if fear_greed in {"extreme_fear", "extreme_greed"}:
            return fear_greed
    return None


def recommend_exposure(
    market: str, indexes: list[dict], sentiment: dict[str, Any]
) -> dict:
    available = [idx for idx in indexes if idx.get("status") == "ok"]
    if not available:
        return {
            "exposure_pct": None,
            "reason": "指數資料不足",
            "trend_state": "unknown",
            "sentiment_signal": None,
        }
    if len(available) < len(indexes):
        return {
            "exposure_pct": None,
            "reason": "部分指數資料抓取失敗",
            "trend_state": "unknown",
            "sentiment_signal": _sentiment_signal(market, sentiment),
        }

    all_above = all(idx.get("above_all_mas") for idx in available)
    all_below = all(idx.get("below_all_mas") for idx in available)
    trend_state = "all_above" if all_above else "all_below" if all_below else "mixed"
    signal = _sentiment_signal(market, sentiment)

    if all_above and signal in BULLISH_SENTIMENT[market]:
        return {
            "exposure_pct": 100,
            "reason": "大盤站上全部均線且情緒為反向偏多",
            "trend_state": trend_state,
            "sentiment_signal": signal,
        }

    if all_below and signal in BEARISH_SENTIMENT[market]:
        return {
            "exposure_pct": 80,
            "reason": "大盤跌破全部均線且情緒為反向偏空",
            "trend_state": trend_state,
            "sentiment_signal": signal,
        }

    return {
        "exposure_pct": None,
        "reason": "未命中已定義的部位規則",
        "trend_state": trend_state,
        "sentiment_signal": signal,
    }


def build_market_regime() -> dict[str, Any]:
    overrides = io.load_market_sentiment_overrides()
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

        sentiment = overrides.get(market, {}) or {}
        markets[market] = {
            "indexes": index_rows,
            "sentiment": sentiment,
            "exposure": recommend_exposure(market, index_rows, sentiment),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "markets": markets,
    }
