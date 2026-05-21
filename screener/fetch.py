"""Data fetchers for yfinance (price OHLCV) and Finnhub (analyst data)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import finnhub
import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


@dataclass
class OHLCV:
    symbol: str
    df: pd.DataFrame


@dataclass
class AnalystSnapshot:
    target_mean: Optional[float]
    rating: Optional[str]
    rating_score: Optional[float]


class FetchError(RuntimeError):
    pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_ohlcv(symbol: str, period: str = "2mo") -> OHLCV:
    df = yf.download(
        symbol,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise FetchError(f"no OHLCV data for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(set(df.columns)):
        raise FetchError(f"missing OHLCV columns for {symbol}: {list(df.columns)}")
    return OHLCV(symbol=symbol, df=df)


def _finnhub_client() -> finnhub.Client:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        raise FetchError("FINNHUB_API_KEY not set")
    return finnhub.Client(api_key=key)


def _rating_label(score: float) -> str:
    if score < 1.5:
        return "Strong Buy"
    if score < 2.5:
        return "Buy"
    if score < 3.5:
        return "Hold"
    if score < 4.5:
        return "Sell"
    return "Strong Sell"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_analyst(symbol: str) -> AnalystSnapshot:
    client = _finnhub_client()
    pt = client.price_target(symbol) or {}
    target_mean = pt.get("targetMean")

    recs = client.recommendation_trends(symbol) or []
    rating: Optional[str] = None
    rating_score: Optional[float] = None
    if recs:
        latest = recs[0]
        buckets = {
            "strongBuy": (latest.get("strongBuy") or 0, 1.0),
            "buy": (latest.get("buy") or 0, 2.0),
            "hold": (latest.get("hold") or 0, 3.0),
            "sell": (latest.get("sell") or 0, 4.0),
            "strongSell": (latest.get("strongSell") or 0, 5.0),
        }
        total = sum(count for count, _ in buckets.values())
        if total > 0:
            rating_score = (
                sum(count * weight for count, weight in buckets.values()) / total
            )
            rating = _rating_label(rating_score)

    return AnalystSnapshot(
        target_mean=float(target_mean) if target_mean else None,
        rating=rating,
        rating_score=rating_score,
    )
