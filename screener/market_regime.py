"""Market-level trend indicators."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

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

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "markets": markets,
    }
